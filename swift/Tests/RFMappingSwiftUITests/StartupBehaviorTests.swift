import Foundation
import XCTest
@testable import RFMappingSwiftUI

@MainActor
final class StartupBehaviorTests: XCTestCase {
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
