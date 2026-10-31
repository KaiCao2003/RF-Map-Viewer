import AppKit
import SwiftUI
import XCTest
@testable import RFMappingSwiftUI

@MainActor
final class ViewerParityTests: XCTestCase {
    private func source(presentations: Bool = true) throws -> RFMappingData {
        var payload = currentRFSchemaPayload([
            "unitsSpikeCounts": [[[[4.0, 12.0], [8.0, 4.0]]]],
            "unitsSpikeCountsSize": [1, 1, 2, 2], "unitPool": [17],
            "xPositions": [-10.0, 10.0], "yPositions": [0.0],
            "timeBinEdges": [0.0, 0.1, 0.3],
        ], occupancyTimeSec: [[90.0, 1.0]], occupancyTimeSecSize: [1, 2])
        if presentations { payload["stimulusPresentationCounts"] = [[2, 4]] }
        return try RFMappingData(data: JSONSerialization.data(withJSONObject: payload),
                                 url: URL(fileURLWithPath: "/tmp/viewer-parity.rfmap"))
    }

    private func viewer(_ source: RFMappingData, preferences: UserDefaults = UserDefaults(suiteName: UUID().uuidString)!) -> RFMappingStore {
        RFMappingStore(initialData: source, loadDefault: false, discoverJSONChoices: false,
                       discoverCompanions: false, unitQualityFilterEnabled: false, preferences: preferences)
    }

    func testRatesUsePresentationExposureAcrossUnequalWindowsAndSpatialPooling() throws {
        let store = viewer(try source())
        store.timeResolutionMS = 100
        store.normalizeControls()
        XCTAssertEqual(store.valueMode, .meanFiringRate)
        let frames = store.timelineSnapshot().matrices
        XCTAssertEqual(try XCTUnwrap(frames[0][0][0]), 20, accuracy: 1e-12)
        XCTAssertEqual(try XCTUnwrap(frames[1][0][0]), 30, accuracy: 1e-12)
        XCTAssertEqual(store.allPositionsTimelineValues()[0], 20, accuracy: 1e-12)
        XCTAssertEqual(store.allPositionsTimelineValues()[1], 16 / 1.2, accuracy: 1e-12)
        store.xBins = 1
        let pooled = store.timelineSnapshot().matrices
        XCTAssertEqual(try XCTUnwrap(pooled[0][0][0]), 20, accuracy: 1e-12)
        XCTAssertEqual(try XCTUnwrap(pooled[1][0][0]), 16 / 1.2, accuracy: 1e-12)
        let cell = CellRef(yStart: 0, yEnd: 0, xStart: 0, xEnd: 1)
        XCTAssertEqual(try XCTUnwrap(store.groupResponseValue(cell, sourceStart: 1, sourceEnd: 1)), 16 / 1.2, accuracy: 1e-12)
    }

    func testZeroPresentationCellsStayMissingThroughRateSmoothing() throws {
        let payload = currentRFSchemaPayload([
            "unitsSpikeCounts": [[[[4.0], [0.0], [16.0]]]],
            "unitsSpikeCountsSize": [1, 1, 3, 1], "unitPool": [17],
            "xPositions": [-10.0, 0.0, 10.0], "yPositions": [0.0],
            "timeBinEdges": [0.0, 0.01], "stimulusPresentationCounts": [[100, 0, 100]],
        ], occupancyTimeSec: [[1.0, 1.0, 1.0]], occupancyTimeSecSize: [1, 3])
        let data = try RFMappingData(data: JSONSerialization.data(withJSONObject: payload),
                                    url: URL(fileURLWithPath: "/tmp/zero-presentations.rfmap"))
        let store = viewer(data)
        for radius in 1...3 {
            store.smoothRadius = radius
            XCTAssertEqual(store.currentHeatmapPlot().matrix, [[4, nil, 16]])
            XCTAssertEqual(store.timelineSnapshot().matrices, [[[4, nil, 16]]])
        }
        store.setValueMode(.spikeCount)
        XCTAssertNotNil(store.currentHeatmapPlot().matrix[0][1])
    }

    func testLegacyDocumentsRemainCountOnlyAndChooseStrongestCountCell() throws {
        let store = viewer(try source(presentations: false))
        XCTAssertEqual(store.valueMode, .spikeCount)
        store.setValueMode(.meanFiringRate)
        XCTAssertEqual(store.valueMode, .spikeCount)
        XCTAssertNotNil(store.selectedCell)
        XCTAssertEqual(store.selectedCell?.xStart, 0)
        XCTAssertTrue(store.dataSummary.contains("Hz is unavailable"))
    }

    func testDefaultsRestoreVisibleTabsAndDisplayControls() throws {
        let preferences = UserDefaults(suiteName: UUID().uuidString)!
        let data = try source()
        let first = viewer(data, preferences: preferences)
        let alreadyOpen = viewer(data, preferences: preferences)
        first.setTabVisible(.rf, visible: false)
        first.setTabVisible(.delayRGB, visible: false)
        first.displayDefaults.initialTab = .results
        first.displayDefaults.tuningBins = 30
        first.displayDefaults.tuningStyle = .polar
        first.displayDefaults.tuningSharedScale = true
        first.overlayLayers = [.inhibitory1D, .inhibitory2D]
        first.displayDefaults.showProbeUnitInfo = false
        first.xBins = 1
        first.palette = .viridis
        first.saveDisplayDefaults()
        XCTAssertEqual(alreadyOpen.visibleTabs, [.timeline, .results])
        XCTAssertEqual(alreadyOpen.palette, .viridis)
        let second = viewer(data, preferences: preferences)
        XCTAssertEqual(second.visibleTabs, [.timeline, .results])
        XCTAssertEqual(second.selectedTab, .results)
        XCTAssertEqual(second.xBins, 1)
        XCTAssertEqual(second.palette, .viridis)
        XCTAssertEqual(second.overlayLayers, [.inhibitory1D, .inhibitory2D])
        XCTAssertFalse(second.displayDefaults.showProbeUnitInfo)
        XCTAssertFalse(alreadyOpen.displayDefaults.showProbeUnitInfo)
        XCTAssertEqual(second.displayDefaults.tuningBins, 30)
        XCTAssertTrue(second.displayDefaults.tuningSharedScale)
        second.selectTab(0)
        XCTAssertEqual(second.selectedTab, .timeline)
        second.setTabVisible(.timeline, visible: false)
        second.setTabVisible(.results, visible: false)
        XCTAssertEqual(second.visibleTabs, [.results])
    }

    func testCompanionFoldOverridesAreNotChangedByOtherDisplaySettings() throws {
        let store = viewer(try source())
        XCTAssertFalse(store.isProbeExpanded)
        XCTAssertFalse(store.isWaveformExpanded)
        XCTAssertFalse(store.isTuningExpanded)
        store.probeExpanded = true
        store.tuningExpanded = false
        store.displayDefaults.tuningStyle = .polar
        XCTAssertTrue(store.isProbeExpanded)
        XCTAssertFalse(store.isTuningExpanded)
    }

    func testNativeOverlayBinsRemainSeparateWithinAGroupedDisplayCell() throws {
        let group = AxisGroup(start: 0, end: 1)
        let layout = HeatmapLayout(x0: 10, y0: 20, cellWidth: 80, cellHeight: 60,
                                  gridWidth: 80, gridHeight: 60, xGroups: [group], yGroups: [group])
        let first = RFOverlayCell(polarity: .excitatory, y: 0, x: 0, membership: 1, center2D: false, center1D: false, oneDAxis: nil)
        let second = RFOverlayCell(polarity: .excitatory, y: 1, x: 1, membership: 2, center2D: false, center1D: false, oneDAxis: "x")
        let a = try XCTUnwrap(savedRFRect(first, layout: layout, flipY: false))
        let b = try XCTUnwrap(savedRFRect(second, layout: layout, flipY: false))
        XCTAssertEqual(a, CGRect(x: 10, y: 20, width: 40, height: 30))
        XCTAssertEqual(b, CGRect(x: 50, y: 50, width: 40, height: 30))
        XCTAssertLessThanOrEqual(a.maxX, b.minX)
        XCTAssertLessThanOrEqual(a.maxY, b.minY)
        XCTAssertEqual(savedRFRect(first, layout: layout, flipY: true)?.minY, 50)
    }

    func testRFResultsShareRFGeometryAndPairedDisplayState() throws {
        let first = viewer(try source()), second = viewer(try source())
        first.xBins = 1
        first.displayDefaults.tuningStyle = .line
        first.displayDefaults.tuningBins = 30
        first.overlayLayers = [.excitatory1D, .inhibitory2D]
        let flags = first.viewerSyncState.changedFields(comparedTo: second.viewerSyncState)
        XCTAssertTrue(flags.contains(.companionDisplay))
        XCTAssertTrue(flags.contains(.overlay))
        second.applyViewerSyncState(first.viewerSyncState, fields: flags)
        XCTAssertEqual(second.overlayLayers, [.excitatory1D, .inhibitory2D])
        XCTAssertEqual(second.displayDefaults.tuningBins, 30)
        let size = CGSize(width: 720, height: 600)
        let rf = makeHeatmapLayout(size: size, plot: first.currentHeatmapPlot())
        let results = makeHeatmapLayout(size: size, plot: first.resultsHeatmapPlot())
        XCTAssertEqual(rf.gridWidth, results.gridWidth)
        XCTAssertEqual(rf.gridHeight, results.gridHeight)
        XCTAssertEqual(rf.x0, results.x0)
        XCTAssertEqual(rf.y0, results.y0)
    }

    func testOverlayShortcutsToggleIndependentlyAndPreserveHiddenLayers() throws {
        let store = viewer(try source())
        store.overlayLayers = [.inhibitory1D]
        store.toggleOverlayLayer(.excitatory1D)
        store.toggleOverlayLayer(.excitatory2D)
        XCTAssertEqual(store.overlayLayers, [.excitatory1D, .excitatory2D, .inhibitory1D])
        store.toggleOverlayLayer(.excitatory1D)
        XCTAssertEqual(store.overlayLayers, [.excitatory2D, .inhibitory1D])
        store.toggleOverlayLayer(nil)
        XCTAssertFalse(store.overlayEnabled)
        XCTAssertEqual(store.overlayLayers, [.excitatory2D, .inhibitory1D])
        store.toggleOverlayLayer(nil)
        XCTAssertTrue(store.overlayEnabled)
        XCTAssertEqual(store.overlayLayers, [.excitatory2D, .inhibitory1D])
    }

    func testNewSettingsPreserveEarlierOverlayPreferences() throws {
        let legacy = Data(#"{"overlayMode":"Both","overlayPolarity":"inhibitory","overlayWidth":3,"showProbe":false}"#.utf8)
        let defaults = try JSONDecoder().decode(ViewerDisplayDefaults.self, from: legacy)
        XCTAssertEqual(defaults.overlayLayers, [.inhibitory1D, .inhibitory2D])
        XCTAssertEqual(defaults.overlayWidth, 3)
        XCTAssertFalse(defaults.showProbe)
        XCTAssertTrue(defaults.showProbeUnitInfo)
        XCTAssertEqual(try JSONDecoder().decode(ViewerDisplayDefaults.self,
            from: JSONEncoder().encode(defaults)), defaults)
    }

    func testOverlayColorsAndWidthsDistinguishPolarities() {
        var defaults = ViewerDisplayDefaults()
        defaults.overlayWidth = 3.5
        XCTAssertEqual(defaults.overlayColor(membership: 3, polarity: .excitatory), "#ff9500")
        XCTAssertEqual(defaults.overlayColor(membership: 3, polarity: .inhibitory), "#5856d6")
        let inhibitory = savedRFStroke(polarity: .inhibitory, settings: defaults, resultOnly: false)
        XCTAssertEqual(inhibitory.lineWidth, 3.5)
        XCTAssertEqual(inhibitory.dash, [5, 3])
        XCTAssertTrue(savedRFStroke(polarity: .excitatory, settings: defaults, resultOnly: false).dash.isEmpty)
        XCTAssertTrue(savedRFStroke(polarity: .inhibitory, settings: defaults, resultOnly: true).dash.isEmpty)
    }

    func testClosingAllWindowsDoesNotTerminateApplication() {
        XCTAssertFalse(AppDelegate(windowRouter: WindowRouter()).applicationShouldTerminateAfterLastWindowClosed(.shared))
    }
}
