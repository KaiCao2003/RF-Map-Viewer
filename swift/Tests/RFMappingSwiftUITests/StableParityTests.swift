import Foundation
import XCTest
@testable import RFMappingSwiftUI

@MainActor
final class StableParityTests: XCTestCase {
    private func data(_ cells: [[Double]], edges: [Double], occupancy: [Double]? = nil) throws -> RFMappingData {
        let payload = currentRFSchemaPayload([
            "unitsSpikeCounts": [[cells]],
            "unitsSpikeCountsSize": [1, 1, cells.count, edges.count - 1],
            "unitPool": [17],
            "xPositions": cells.indices.map(Double.init),
            "yPositions": [0.0],
            "timeBinEdges": edges,
        ], occupancyTimeSec: [occupancy ?? Array(repeating: 1.0, count: cells.count)],
           occupancyTimeSecSize: [1, cells.count])
        return try RFMappingData(data: JSONSerialization.data(withJSONObject: payload),
                                 url: URL(fileURLWithPath: "/tmp/stable-parity.rfmap"))
    }

    private func store(_ data: RFMappingData, preferences: UserDefaults? = nil) -> RFMappingStore {
        RFMappingStore(initialData: data, loadDefault: false,
                       discoverJSONChoices: false, discoverCompanions: false,
                       unitQualityFilterEnabled: false,
                       preferences: preferences ?? UserDefaults(suiteName: UUID().uuidString)!)
    }

    func testDifferencePoolsBothWindowsBeforeClippingAndKeepsTimelineIndependent() throws {
        let source = try data([[6, 2, 0], [2, 10, 0]], edges: [0, 0.08, 0.16, 0.24], occupancy: [1, 3])
        let viewer = store(source)
        let originalTimeline = viewer.timelineSnapshot().matrices
        let originalDelay = viewer.delayHeatmapPlot(floor: 0).matrix
        viewer.setRFSubtractEnabled(true)
        XCTAssertNil(viewer.currentHeatmapPlot().matrix[0][0])
        XCTAssertEqual(try XCTUnwrap(viewer.currentHeatmapPlot().matrix[0][1]), 8.0 / 3.0, accuracy: 1e-12)
        viewer.xBins = 1
        XCTAssertEqual(viewer.currentHeatmapPlot().matrix, [[1]])
        viewer.xBins = 2
        XCTAssertEqual(viewer.timelineSnapshot().matrices, originalTimeline)
        XCTAssertEqual(viewer.delayHeatmapPlot(floor: 0).matrix, originalDelay)
        XCTAssertTrue(viewer.exportCSV().contains("rf_subtract_enabled,rf_subtract_start_ms,rf_subtract_end_ms"))
        XCTAssertTrue(viewer.exportCSV().contains("True,0.0,80.0"))
        viewer.subtractRangeStartMS = 80
        viewer.subtractRangeEndMS = 160
        viewer.normalizePlotTimeRange()
        XCTAssertEqual(viewer.currentHeatmapPlot().matrix, [[0, 0]])
    }

    func testModesRestoreIndependentWindowsAndSavedTimingDefaults() throws {
        let source = try data([[2, 5, 8]], edges: [0, 0.08, 0.16, 0.24])
        let preferences = UserDefaults(suiteName: UUID().uuidString)!
        let viewer = store(source, preferences: preferences)
        viewer.plotRangeStartMS = 0
        viewer.plotRangeEndMS = 80
        viewer.normalizePlotTimeRange()
        viewer.setRFSubtractEnabled(true)
        XCTAssertEqual(viewer.plotRangeStartMS, 80)
        XCTAssertEqual(viewer.plotRangeEndMS, 160)
        viewer.plotRangeStartMS = 160
        viewer.plotRangeEndMS = 240
        viewer.normalizePlotTimeRange()
        viewer.setRFSubtractEnabled(false)
        XCTAssertEqual(viewer.plotRangeStartMS, 0)
        XCTAssertEqual(viewer.plotRangeEndMS, 80)
        viewer.setRFSubtractEnabled(true)
        XCTAssertEqual(viewer.plotRangeStartMS, 160)
        XCTAssertEqual(viewer.plotRangeEndMS, 240)
        viewer.saveTimingDefaults()
        let reopened = store(source, preferences: preferences)
        XCTAssertTrue(reopened.rfSubtractEnabled)
        XCTAssertEqual(reopened.plotRangeStartMS, 160)
        reopened.plotRangeStartMS = 80
        reopened.normalizePlotTimeRange()
        reopened.resetPlotRangeToDefault()
        XCTAssertEqual(reopened.plotRangeStartMS, 160)
        reopened.setRFSubtractEnabled(false)
        XCTAssertEqual(reopened.plotRangeEndMS, 80)
    }

    func testPairStateIncludesDifferenceAndFilterOnlyUsesA() throws {
        let source = try data([[0, 5, 0]], edges: [0, 0.08, 0.16, 0.24])
        let first = store(source)
        let second = store(source)
        first.setRFSubtractEnabled(true)
        first.setRFUnitQualityFilterEnabled(true)
        first.subtractRangeEndMS = 240
        first.normalizePlotTimeRange()
        XCTAssertEqual(first.qualityFilteredUnitIDs, [17])
        let incoming = first.viewerSyncState
        let fields = incoming.changedFields(comparedTo: second.viewerSyncState)
        XCTAssertTrue(fields.contains(.plotRange))
        second.applyViewerSyncState(incoming, fields: fields)
        XCTAssertTrue(second.rfSubtractEnabled)
        XCTAssertEqual(second.subtractRangeStartMS, 0)
        XCTAssertEqual(second.subtractRangeEndMS, 240)
        XCTAssertEqual(second.currentHeatmapPlot().matrix, [[0]])
    }

    func testTemporalPeakFollowsSmoothedCountsAndFirstEqualPeak() throws {
        let viewer = store(try data([[20, 0], [0, 20]], edges: [0, 0.01, 0.02]))
        viewer.xBins = 1
        XCTAssertEqual(viewer.delayHeatmapPlot(floor: 0).matrix, [[5]])
        XCTAssertEqual(viewer.cachedRGBPlot().delay, [[5]])
        XCTAssertEqual(viewer.cachedRGBPlot().entropy, [[1]])
        viewer.xBins = 2
        viewer.smoothRadius = 1
        XCTAssertEqual(viewer.delayHeatmapPlot(floor: 0).matrix, [[5, 15]])
    }

    func testTemporalPeakUsesDurationForShortFinalGroupAndIrregularBins() throws {
        let grouped = store(try data([[5, 5, 8]], edges: [0, 0.01, 0.02, 0.03]))
        grouped.timeResolutionMS = 20
        grouped.normalizeControls()
        XCTAssertEqual(grouped.delayHeatmapPlot(floor: 0).matrix, [[25]])
        XCTAssertEqual(grouped.cachedRGBPlot().delay, [[25]])
        let irregular = store(try data([[5, 8]], edges: [0, 0.01, 0.03]))
        XCTAssertEqual(irregular.delayHeatmapPlot(floor: 0).matrix, [[5]])
    }

    func testMissingExposureContributesZeroToSmoothedTemporalCounts() throws {
        let viewer = store(try data([[0, 0], [9, 0], [0, 9]], edges: [0, 0.01, 0.02], occupancy: [0, 1, 1]))
        viewer.smoothRadius = 1
        // At the center, weights 1:2:1 yield total 6.75, not 9 from dropping the missing neighbor.
        XCTAssertNil(viewer.delayHeatmapPlot(floor: 7).matrix[0][1])
        XCTAssertEqual(viewer.delayHeatmapPlot(floor: 6).matrix[0][1], 5)
        XCTAssertNil(viewer.cachedRGBPlot().total[0][0])
    }

    func testResolutionChangesOneSourceBinAtATime() throws {
        let viewer = store(try data([[1, 2, 3]], edges: [0, 0.08, 0.16, 0.24]))
        viewer.stepTimeResolution(1)
        XCTAssertEqual(viewer.timeResolutionMS, 160)
        viewer.stepTimeResolution(-1)
        XCTAssertEqual(viewer.timeResolutionMS, 80)
    }
    func testIndexedStoreCachesRequestedUnitAndClosingCancelsPendingReads() async throws {
        let fixture = try XCTUnwrap(Bundle.module.url(
            forResource: "indexed-v2-numpy", withExtension: "base64", subdirectory: "Fixtures"
        ))
        let encoded = try String(contentsOf: fixture, encoding: .utf8)
        let bytes = try XCTUnwrap(Data(base64Encoded: encoded, options: .ignoreUnknownCharacters))
        let source = try RFMappingData(data: bytes, url: URL(fileURLWithPath: "/tmp/indexed-store.rfmap"))
        let viewer = store(source)
        XCTAssertEqual(viewer.cachedUnitCount, 1)
        XCTAssertFalse(viewer.isUnitCacheComplete)
        XCTAssertNil(FigureExportWindowRegistry.shared.prepare(from: viewer))
        viewer.selectUnitID(902)
        XCTAssertTrue(viewer.isSelectedUnitLoading)
        let deadline = Date().addingTimeInterval(5)
        while !viewer.isUnitCacheComplete && viewer.unitCacheError == nil && Date() < deadline {
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTAssertNil(viewer.unitCacheError)
        XCTAssertTrue(viewer.isUnitCacheComplete)
        XCTAssertEqual(viewer.cachedUnitCount, 3)
        XCTAssertEqual(viewer.selectedUnitID, 902)
        XCTAssertEqual(viewer.unitIndex, 2)
        XCTAssertTrue(viewer.hasSelectedUnit)
        if let request = FigureExportWindowRegistry.shared.prepare(from: viewer) {
            FigureExportWindowRegistry.shared.release(request)
        } else {
            XCTFail("Composer should become available after the cache completes")
        }
        viewer.cancelPendingLoads()

        let closingSource = try RFMappingData(data: bytes, url: URL(fileURLWithPath: "/tmp/closing-indexed.rfmap"))
        let closing = store(closingSource)
        closing.cancelPendingLoads()
        await Task.yield()
        XCTAssertEqual(closing.cachedUnitCount, 1)
        XCTAssertFalse(closing.isCachingUnits)
        // Restart before the cancelled task's defer runs; it must not clear the new worker.
        closing.retryUnitCaching()
        closing.cancelPendingLoads()
        closing.retryUnitCaching()
        let retryDeadline = Date().addingTimeInterval(5)
        while !closing.isUnitCacheComplete && closing.unitCacheError == nil && Date() < retryDeadline {
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTAssertTrue(closing.isUnitCacheComplete)
        XCTAssertFalse(closing.isCachingUnits)
    }

    func testFilterShortcutPreferenceUpdatesOtherWindowsWithoutResettingControls() throws {
        let source = try data([[0, 5, 1]], edges: [0, 0.08, 0.16, 0.24])
        let preferences = UserDefaults(suiteName: UUID().uuidString)!
        let first = store(source, preferences: preferences)
        let second = store(source, preferences: preferences)
        second.setRFSubtractEnabled(true)
        second.palette = .gray
        let before = second.viewerSyncState
        first.setRFUnitQualityFilterEnabled(true)
        XCTAssertTrue(second.rfFilterUnitsWithZeroBins)
        XCTAssertEqual(second.viewerSyncState, before)
        first.setRFUnitQualityFilterEnabled(false)
        XCTAssertFalse(second.rfFilterUnitsWithZeroBins)
        XCTAssertEqual(second.viewerSyncState, before)
    }

}
