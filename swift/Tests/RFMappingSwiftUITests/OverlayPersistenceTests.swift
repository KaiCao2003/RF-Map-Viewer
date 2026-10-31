import Foundation
import XCTest
@testable import RFMappingSwiftUI

@MainActor
final class OverlayPersistenceTests: XCTestCase {
    private func store(preferences: UserDefaults) throws -> RFMappingStore {
        let payload = currentRFSchemaPayload([
            "unitsSpikeCounts": [[[[2, 3]]]], "unitsSpikeCountsSize": [1, 1, 1, 2],
            "unitPool": [17], "xPositions": [0], "yPositions": [0], "timeBinEdges": [0, 0.1, 0.2],
        ], occupancyTimeSec: [[1]], occupancyTimeSecSize: [1, 1])
        let source = try RFMappingData(data: JSONSerialization.data(withJSONObject: payload),
                                      url: URL(fileURLWithPath: "/tmp/overlay-persistence.rfmap"))
        return RFMappingStore(initialData: source, loadDefault: false,
                              discoverJSONChoices: false, discoverCompanions: false,
                              unitQualityFilterEnabled: false, preferences: preferences)
    }

    func testMasterVisibilityRetainsLayersAcrossRestartWithoutSavingOtherEdits() throws {
        let suite = UUID().uuidString
        let preferences = UserDefaults(suiteName: suite)!
        defer { preferences.removePersistentDomain(forName: suite) }
        let viewer = try store(preferences: preferences)
        viewer.palette = .inferno
        viewer.setOverlayLayer(.excitatory2D, enabled: true)
        viewer.setOverlayLayer(.inhibitory1D, enabled: true)
        viewer.toggleOverlayLayer(nil)
        XCTAssertFalse(viewer.overlayEnabled)
        XCTAssertEqual(viewer.overlayLayers, [.excitatory2D, .inhibitory1D])
        XCTAssertTrue(viewer.activeResultLayers.isEmpty)
        let reopened = try store(preferences: preferences)
        XCTAssertFalse(reopened.overlayEnabled)
        XCTAssertEqual(reopened.overlayLayers, viewer.overlayLayers)
        XCTAssertEqual(reopened.palette, .gray)
        reopened.toggleOverlayLayer(nil)
        XCTAssertEqual(Set(reopened.activeResultLayers), viewer.overlayLayers)
        reopened.selectedTab = .results
        reopened.setOverlayEnabled(false)
        XCTAssertFalse(reopened.activeResultLayers.isEmpty, "RF Results remains visible when the RF overlay is hidden")
    }

    func testOverlayPreferenceDoesNotResetOtherOpenWindows() throws {
        let preferences = UserDefaults(suiteName: UUID().uuidString)!
        let first = try store(preferences: preferences)
        let second = try store(preferences: preferences)
        second.palette = .viridis
        second.selectedTab = .timeline
        first.setOverlayLayer(.inhibitory2D, enabled: true)
        first.setOverlayEnabled(false)
        XCTAssertEqual(second.overlayLayers, [.inhibitory2D])
        XCTAssertFalse(second.overlayEnabled)
        XCTAssertEqual(second.palette, .viridis)
        XCTAssertEqual(second.selectedTab, .timeline)
    }

    func testPairedStateIncludesMasterVisibilityIndependentlyOfLayerSelection() throws {
        let first = try store(preferences: UserDefaults(suiteName: UUID().uuidString)!)
        let second = try store(preferences: UserDefaults(suiteName: UUID().uuidString)!)
        first.setOverlayLayer(.excitatory1D, enabled: true)
        second.applyViewerSyncState(first.viewerSyncState, fields: .overlay)
        let before = second.viewerSyncState
        first.setOverlayEnabled(false)
        let changed = first.viewerSyncState.changedFields(comparedTo: before)
        XCTAssertTrue(changed.contains(.overlay))
        second.applyViewerSyncState(first.viewerSyncState, fields: .overlay)
        XCTAssertFalse(second.overlayEnabled)
        XCTAssertEqual(second.overlayLayers, [.excitatory1D])
    }

    func testOldSettingsDecodeWithVisibleMasterAndDefaultLayout() throws {
        let old = Data(#"{"overlayLayers":["excitatory_2d"]}"#.utf8)
        let decoded = try JSONDecoder().decode(ViewerDisplayDefaults.self, from: old)
        XCTAssertTrue(decoded.overlayEnabled)
        XCTAssertEqual(decoded.rfResultLayoutPattern, SavedRFResult.defaultLayoutPattern)
    }

    func testCustomResultLayoutAndMasterVisibilityRoundTrip() throws {
        let preferences = UserDefaults(suiteName: UUID().uuidString)!
        let viewer = try store(preferences: preferences)
        viewer.displayDefaults.rfResultLayoutPattern = "custom/recording"
        viewer.setOverlayLayer(.inhibitory1D, enabled: true)
        viewer.setOverlayEnabled(false)
        viewer.saveDisplayDefaults()
        let reopened = try store(preferences: preferences)
        XCTAssertEqual(reopened.displayDefaults.rfResultLayoutPattern, "custom/recording")
        XCTAssertFalse(reopened.overlayEnabled)
        XCTAssertEqual(reopened.overlayLayers, [.inhibitory1D])
    }
}
