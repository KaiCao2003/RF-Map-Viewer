import AppKit
import Foundation
import XCTest
@testable import RFMappingSwiftUI

@MainActor
final class StartupBehaviorTests: XCTestCase {
    func testFileReferenceURLCompanionDiscoveryStopsAtFilesystemRoot() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("#Recording/260918_10/data/rfmapping/ProbeA/map.rfmap")
        try FileManager.default.createDirectory(
            at: file.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        try Data().write(to: file)
        // Finder's file-reference URLs retain Foundation's bridged NSURL
        // parent behavior after standardization, unlike native Swift URLs.
        let reference = try XCTUnwrap((file as NSURL).fileReferenceURL())
        XCTAssertNil(try WaveformArtifactStore.discover(forRFURL: reference))
        XCTAssertNil(ProbeGeometryDiscovery.discover(forRFURL: reference))
    }

    func testURLLaunchQueuedBeforeInitialSceneLoadsDocumentWithoutPicker() async throws {
        let router = WindowRouter()
        let delegate = AppDelegate(windowRouter: router)
        let url = URL(fileURLWithPath: "/tmp/direct-open.rfmap")
        let replaced = expectation(description: "Initial scene consumes Launch Services URL")
        var fallbackCount = 0

        delegate.application(NSApplication.shared, open: [url])
        router.install(
            { _ in XCTFail("The first URL belongs in the initial window") },
            coldLaunchReplacement: { actual in
                XCTAssertEqual(actual, url)
                replaced.fulfill()
                return true
            },
            coldLaunchFallback: { fallbackCount += 1 }
        )

        await fulfillment(of: [replaced], timeout: 1)
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertEqual(fallbackCount, 0, "An explicit file launch must cancel the fallback picker")
    }

    func testLaterExternalOpensUseSeparatePreparedDocumentWindows() async throws {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".json")
        defer { try? FileManager.default.removeItem(at: url) }
        let payload = currentRFSchemaPayload([
            "unitsSpikeCounts": [[[[1.0]]]],
            "unitsSpikeCountsSize": [1, 1, 1, 1],
            "unitPool": [22], "xPositions": [0.0], "yPositions": [0.0],
            "timeBinEdges": [0.0, 0.1],
        ], occupancyTimeSec: 0.1, occupancyTimeSecSize: [1, 1])
        try JSONSerialization.data(withJSONObject: payload).write(to: url)
        let router = WindowRouter()
        var replacementCount = 0
        var requests: [DocumentWindowRequest] = []
        router.install(
            { requests.append($0) },
            coldLaunchReplacement: { _ in
                replacementCount += 1
                return true
            }
        )
        let completed = expectation(description: "All external documents opened")
        router.openExternal([url, url, url]) { succeeded in
            XCTAssertTrue(succeeded)
            completed.fulfill()
        }
        await fulfillment(of: [completed], timeout: 2)

        XCTAssertEqual(replacementCount, 1)
        XCTAssertEqual(requests.count, 2)
        XCTAssertEqual(Set(requests.map(\.id)).count, 2)
        for request in requests {
            XCTAssertEqual(request.path, url.standardizedFileURL.path)
            let prepared = try XCTUnwrap(router.takePreparedDocument(for: request.id))
            XCTAssertEqual(prepared.unitPool, [22])
            XCTAssertNil(router.takePreparedDocument(for: request.id))
        }
    }

    func testFallbackPickerPresentsOnceWithoutConsumingInitialWindowClaim() async throws {
        let state = ColdLaunchInitialWindowState()
        var presentationCount = 0
        var loadedURL: URL?
        state.install(
            replacement: { url in
                loadedURL = url
                return true
            },
            fallback: {
                presentationCount += 1
            }
        )

        XCTAssertTrue(state.shouldScheduleFallback)
        let fallback = try XCTUnwrap(state.takeFallbackPresentation())
        fallback()

        XCTAssertEqual(presentationCount, 1)
        XCTAssertTrue(state.didPresentFallback)
        XCTAssertTrue(state.canClaimInitialWindow)
        XCTAssertFalse(state.shouldScheduleFallback)
        XCTAssertNil(state.takeFallbackPresentation())

        let selectedURL = URL(fileURLWithPath: "/tmp/selected.rfmap")
        let replacement = try XCTUnwrap(state.takeReplacement())
        let didLoad = await replacement(selectedURL)
        XCTAssertTrue(didLoad)
        XCTAssertEqual(loadedURL, selectedURL)
        XCTAssertFalse(state.canClaimInitialWindow)
    }

    func testCancelAbandonsClaimAndCannotPresentFallbackAgain() throws {
        let state = ColdLaunchInitialWindowState()
        var presentationCount = 0
        state.install(
            replacement: { _ in true },
            fallback: { presentationCount += 1 }
        )
        let fallback = try XCTUnwrap(state.takeFallbackPresentation())
        fallback()
        XCTAssertEqual(presentationCount, 1)
        XCTAssertTrue(state.canClaimInitialWindow)

        state.abandon()

        XCTAssertTrue(state.didPresentFallback)
        XCTAssertFalse(state.canClaimInitialWindow)
        XCTAssertFalse(state.shouldScheduleFallback)
        XCTAssertNil(state.takeReplacement())
        XCTAssertNil(state.takeFallbackPresentation())
        XCTAssertEqual(presentationCount, 1)
    }

    func testDocumentlessColdLaunchPresentsPickerWithoutLoadingDiscoveredData() {
        let suiteName = "StartupBehaviorTests.\(UUID().uuidString)"
        let preferences = UserDefaults(suiteName: suiteName)!
        defer { preferences.removePersistentDomain(forName: suiteName) }
        let store = RFMappingStore(
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false,
            preferences: preferences
        )

        XCTAssertTrue(store.isAwaitingStartupDocument)
        XCTAssertFalse(store.isImporting)
        XCTAssertNil(store.data)

        presentColdLaunchDocumentPicker(in: store)

        XCTAssertFalse(store.isAwaitingStartupDocument)
        XCTAssertTrue(store.isImporting)
        XCTAssertNil(store.data)
    }

    func testLateExternalOpenDismissesFallbackPickerAndLoadsInitialStore() async throws {
        let suiteName = "StartupBehaviorTests.\(UUID().uuidString)"
        let preferences = UserDefaults(suiteName: suiteName)!
        defer { preferences.removePersistentDomain(forName: suiteName) }
        let store = RFMappingStore(
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false,
            preferences: preferences
        )
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("late-open.rfmap")
        let payload = currentRFSchemaPayload([
            "unitsSpikeCounts": [[[[1.0]]]],
            "unitsSpikeCountsSize": [1, 1, 1, 1],
            "unitPool": [22],
            "xPositions": [0.0],
            "yPositions": [0.0],
            "timeBinEdges": [0.0, 0.1],
        ], occupancyTimeSec: 0.1, occupancyTimeSecSize: [1, 1])
        try JSONSerialization.data(withJSONObject: payload).write(to: url, options: .atomic)

        presentColdLaunchDocumentPicker(in: store)
        XCTAssertTrue(store.isImporting)

        let state = ColdLaunchInitialWindowState()
        state.install(
            replacement: { externalURL in
                await loadColdLaunchReplacement(externalURL, in: store)
            },
            fallback: nil
        )
        let replacement = try XCTUnwrap(state.takeReplacement())
        let didReplace = await replacement(url)
        XCTAssertTrue(didReplace)

        XCTAssertFalse(store.isImporting)
        XCTAssertFalse(store.isAwaitingStartupDocument)
        XCTAssertEqual(store.data?.url.standardizedFileURL, url.standardizedFileURL)
        XCTAssertEqual(store.data?.unitPool, [22])
        XCTAssertNil(state.takeReplacement(), "The initial-window replacement must remain one-shot")
    }
}
