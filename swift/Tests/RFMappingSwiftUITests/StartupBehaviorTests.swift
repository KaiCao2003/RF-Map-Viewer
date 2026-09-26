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

        delegate.application(NSApplication.shared, open: [url])
        router.install(
            { _ in XCTFail("The first URL belongs in the initial window") },
            coldLaunchReplacement: { actual in
                XCTAssertEqual(actual, url)
                replaced.fulfill()
                return true
            }
        )

        await fulfillment(of: [replaced], timeout: 1)
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

    func testNativeRecentOpenUsesTheInitialWindowRoute() async {
        let router = WindowRouter()
        let delegate = AppDelegate(windowRouter: router)
        let url = URL(fileURLWithPath: "/tmp/native-recent.rfmap")
        let opened = expectation(description: "Native recent document is routed")
        router.install(
            { _ in XCTFail("The initial window should receive the recent document") },
            coldLaunchReplacement: { selected in
                XCTAssertEqual(selected, url)
                opened.fulfill()
                return true
            }
        )

        XCTAssertTrue(delegate.application(NSApplication.shared, openFile: url.path))
        await fulfillment(of: [opened], timeout: 1)
    }

    func testInitialWindowClaimIsConsumedByFirstOpenOnly() async throws {
        let state = ColdLaunchInitialWindowState()
        var loadedURL: URL?
        state.install(
            replacement: { url in
                loadedURL = url
                return true
            }
        )

        XCTAssertTrue(state.canClaimInitialWindow)

        let selectedURL = URL(fileURLWithPath: "/tmp/selected.rfmap")
        let replacement = try XCTUnwrap(state.takeReplacement())
        let didLoad = await replacement(selectedURL)
        XCTAssertTrue(didLoad)
        XCTAssertEqual(loadedURL, selectedURL)
        XCTAssertFalse(state.canClaimInitialWindow)
        XCTAssertNil(state.takeReplacement())
    }

    func testClosingInitialWindowAbandonsItsClaim() {
        let state = ColdLaunchInitialWindowState()
        state.install(replacement: { _ in true })
        XCTAssertTrue(state.canClaimInitialWindow)

        state.abandon()

        XCTAssertFalse(state.canClaimInitialWindow)
        XCTAssertNil(state.takeReplacement())
    }

    func testDocumentlessColdLaunchShowsWelcomeWithoutPickerOrDiscoveredData() async throws {
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

        presentColdLaunchWelcome(in: store)
        let router = WindowRouter()
        router.install({ _ in XCTFail("No document was requested") }, coldLaunchReplacement: { _ in true })
        try await Task.sleep(for: .milliseconds(250))

        XCTAssertFalse(store.isAwaitingStartupDocument)
        XCTAssertFalse(store.isImporting)
        XCTAssertNil(store.data)
    }

    func testLateExternalOpenDismissesExplicitPickerAndLoadsInitialStore() async throws {
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

        presentColdLaunchWelcome(in: store)
        store.isImporting = true
        XCTAssertTrue(store.isImporting)

        let state = ColdLaunchInitialWindowState()
        state.install(
            replacement: { externalURL in
                await loadColdLaunchReplacement(externalURL, in: store)
            }
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

    func testUnavailableDocumentLeavesWelcomeReadyForAnotherOpen() async throws {
        let suiteName = "StartupBehaviorTests.\(UUID().uuidString)"
        let preferences = UserDefaults(suiteName: suiteName)!
        defer { preferences.removePersistentDomain(forName: suiteName) }
        let store = RFMappingStore(
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false,
            preferences: preferences
        )
        presentColdLaunchWelcome(in: store)
        let missing = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".rfmap")

        let succeeded = await loadColdLaunchReplacement(missing, in: store)

        XCTAssertFalse(succeeded)
        XCTAssertFalse(store.hasData)
        XCTAssertFalse(store.isLoadingData)
        XCTAssertFalse(store.isAwaitingStartupDocument)
        XCTAssertFalse(store.isImporting)
        XCTAssertNotNil(store.errorMessage)
        store.errorMessage = nil
        store.isImporting = true
        XCTAssertTrue(store.isImporting)
    }
}
