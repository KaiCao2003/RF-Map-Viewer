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

    func testIrregularTimeGroupingUsesNearestPhysicalEdgesAndRefreshesCaches() throws {
        let viewer = store(try data([[5, 8, 3]], edges: [0, 0.01, 0.03, 0.04]))
        viewer.timeResolutionMS = 20
        viewer.normalizeControls()
        let nativeGroups = (0..<3).map { AxisGroup(start: $0, end: $0) }
        XCTAssertEqual(viewer.timeGroups(), nativeGroups, "An exact tie chooses the earlier edge")
        XCTAssertEqual(viewer.timelineSnapshot().matrices, [[[5]], [[8]], [[3]]])
        XCTAssertEqual(viewer.delayHeatmapPlot(floor: 0).matrix, [[5]])
        XCTAssertEqual(viewer.cachedRGBPlot().delay, [[5]])
        XCTAssertEqual(viewer.delayMatrixForTimeGroups(), [[5]])

        viewer.timeResolutionMS = 30
        viewer.normalizeControls()
        XCTAssertEqual(viewer.timeGroups(), [AxisGroup(start: 0, end: 1), AxisGroup(start: 2, end: 2)])
        XCTAssertEqual(viewer.timelineSnapshot().matrices, [[[13]], [[3]]])
        XCTAssertEqual(viewer.delayHeatmapPlot(floor: 0).matrix, [[15]])
        XCTAssertEqual(viewer.cachedRGBPlot().delay, [[15]])

        viewer.timeResolutionMS = 40
        viewer.normalizeControls()
        XCTAssertEqual(viewer.timeResolutionMS, 40)
        XCTAssertEqual(viewer.timeGroupSize(), 4, "Duration steps are not capped by the native bin count")
        XCTAssertEqual(viewer.timeGroups(), [AxisGroup(start: 0, end: 2)])
        XCTAssertEqual(viewer.timelineSnapshot().matrices, [[[16]]])
        XCTAssertEqual(viewer.delayHeatmapPlot(floor: 0).matrix, [[20]])
        XCTAssertEqual(viewer.cachedRGBPlot().delay, [[20]])

        viewer.timeResolutionMS = 20
        viewer.normalizeControls()
        XCTAssertEqual(viewer.timeGroups(), nativeGroups)
        XCTAssertEqual(viewer.timelineSnapshot().matrices, [[[5]], [[8]], [[3]]])
        XCTAssertEqual(viewer.delayHeatmapPlot(floor: 0).matrix, [[5]])
    }

    func testRepeatedResponseSmoothingDoesNotCrossMissingOccupancy() throws {
        let viewer = store(try data([[4], [0], [16]], edges: [0, 0.01], occupancy: [1, 0, 1]))
        for mode in ResponseValueMode.allCases {
            viewer.valueMode = mode
            for radius in 1...3 {
                viewer.smoothRadius = radius
                XCTAssertEqual(viewer.currentHeatmapPlot().matrix, [[4, nil, 16]])
                XCTAssertEqual(viewer.timelineSnapshot().matrices, [[[4, nil, 16]]])
            }
        }
    }

    func testPaletteChangesUseExactCachedBoundsAndColorZeroBaseline() throws {
        let viewer = store(try data([[5], [10]], edges: [0, 0.01]))
        XCTAssertEqual(viewer.currentHeatmapPlot().low, 5)
        XCTAssertEqual(viewer.currentHeatmapPlot().high, 10)
        for palette in [RFPalette.viridis, .inferno, .gray] {
            viewer.palette = palette
            XCTAssertEqual(viewer.currentHeatmapPlot().low, palette == .gray ? 5 : 0)
            XCTAssertEqual(viewer.currentHeatmapPlot().high, 10)
            XCTAssertEqual(viewer.currentHeatmapPlot().matrix, [[5, 10]])
        }
        let constant = store(try data([[5]], edges: [0, 0.01]))
        XCTAssertEqual(constant.currentHeatmapPlot().high, 6)
        constant.palette = .viridis
        XCTAssertEqual(constant.currentHeatmapPlot().low, 0)
        XCTAssertEqual(constant.currentHeatmapPlot().high, 5)
        XCTAssertEqual(constant.cachedRGBPlot().maxTotal, 5)
        constant.palette = .gray
        XCTAssertEqual(constant.currentHeatmapPlot().high, 6)
    }

    func testColorScaleRemainsNondegenerateForZeroAndMissingResponses() throws {
        let silent = store(try data([[0]], edges: [0, 0.01]))
        let missing = store(try data([[9, 0]], edges: [0, 0.08, 0.16]))
        missing.setRFSubtractEnabled(true)
        for palette in [RFPalette.viridis, .inferno] {
            silent.palette = palette
            XCTAssertEqual(silent.currentHeatmapPlot().matrix, [[0]])
            XCTAssertEqual(silent.currentHeatmapPlot().low, 0)
            XCTAssertEqual(silent.currentHeatmapPlot().high, 1)
            missing.palette = palette
            XCTAssertEqual(missing.currentHeatmapPlot().matrix, [[nil]])
            XCTAssertEqual(missing.currentHeatmapPlot().low, 0)
            XCTAssertEqual(missing.currentHeatmapPlot().high, 1)
        }
    }

    func testTemporalMetricReuseKeepsFloorValueModeAndDisplayChangesIndependent() throws {
        let viewer = store(try data([[9, 0], [0, 9]], edges: [0, 0.01, 0.02]))
        XCTAssertEqual(viewer.delayHeatmapPlot(floor: 9).matrix, [[nil, nil]])
        XCTAssertEqual(viewer.cachedRGBPlot().delay, [[5, 15]])
        viewer.valueMode = .spikeCount
        XCTAssertEqual(viewer.delayHeatmapPlot(floor: 8).matrix, [[5, 15]])
        viewer.xBins = 1
        XCTAssertEqual(viewer.delayHeatmapPlot(floor: 8).matrix, [[5]])
        XCTAssertEqual(viewer.cachedRGBPlot().entropy, [[1]])
        viewer.xBins = 2
        viewer.smoothRadius = 1
        XCTAssertEqual(viewer.delayHeatmapPlot(floor: 8).matrix, [[5, 15]])
        XCTAssertGreaterThan(try XCTUnwrap(viewer.cachedRGBPlot().entropy[0][0]), 0)
    }

    func testEscapeClosesZoomThenClearsProbeFilterThenTimelineWithoutChangingRFRange() throws {
        let viewer = store(try data([[1, 2, 3]], edges: [0, 0.01, 0.02, 0.03]))
        viewer.plotRangeStartMS = 10
        viewer.plotRangeEndMS = 20
        viewer.normalizePlotTimeRange()
        viewer.selectTimelineBin(1, extending: false)
        viewer.setProbeFilteredUnitIDs([17])
        viewer.isWaveformZoomed = true

        viewer.handleEscape()
        XCTAssertFalse(viewer.isWaveformZoomed)
        XCTAssertEqual(viewer.probeFilteredUnitIDs, Set([17]))
        XCTAssertEqual(viewer.rangeStartMS, 10)
        XCTAssertEqual(viewer.rangeEndMS, 20)
        viewer.handleEscape()
        XCTAssertNil(viewer.probeFilteredUnitIDs)
        XCTAssertEqual(viewer.rangeStartMS, 10)
        XCTAssertEqual(viewer.rangeEndMS, 20)
        viewer.handleEscape()
        XCTAssertEqual(viewer.rangeStartMS, 0)
        XCTAssertEqual(viewer.rangeEndMS, 30)
        XCTAssertEqual(viewer.plotRangeStartMS, 10)
        XCTAssertEqual(viewer.plotRangeEndMS, 20)
    }

    func testInspectorUsesCountRatePeakAndNativeEntropyInBothValueModes() throws {
        let viewer = store(try data([[5, 5, 8]], edges: [0, 0.01, 0.02, 0.03], occupancy: [2]))
        let cell = CellRef(yStart: 0, yEnd: 0, xStart: 0, xEnd: 0)
        for mode in ResponseValueMode.allCases {
            viewer.setValueMode(mode)
            viewer.timeResolutionMS = 10
            viewer.normalizeControls()
            XCTAssertTrue(viewer.cellMetricsText(cell).contains("count entropy 0.976"))

            viewer.timeResolutionMS = 20
            viewer.normalizeControls()
            // The last interval has fewer counts (8 versus 10), but the
            // higher rate (800 versus 500 counts/s) because it is half as long.
            let inspector = viewer.cellMetricsText(cell)
            XCTAssertTrue(inspector.contains("peak bin 2 ("), inspector)
            XCTAssertTrue(inspector.contains("delay 25.0 ms, count entropy 0.976"), inspector)
            let peakValue = mode == .spikeCount ? 8.0 : 4.0
            XCTAssertTrue(inspector.contains("peak \(mode.format(peakValue)) \(mode.unit)\n"), inspector)
            XCTAssertTrue(viewer.tooltipText(cell).contains("delay 25.0 ms"))
            XCTAssertEqual(viewer.delayHeatmapPlot(floor: 0).matrix, [[25]])
            XCTAssertEqual(try XCTUnwrap(viewer.cachedRGBPlot().entropy[0][0]),
                           0.9758159039662212, accuracy: 1e-12)
        }
    }

    func testInspectorNormalizesIrregularNativeBinsAndKeepsSilentCellsMissing() throws {
        let viewer = store(try data([[5, 8], [0, 0]], edges: [0, 0.01, 0.03]))
        let active = CellRef(yStart: 0, yEnd: 0, xStart: 0, xEnd: 0)
        let silent = CellRef(yStart: 0, yEnd: 0, xStart: 1, xEnd: 1)
        for mode in ResponseValueMode.allCases {
            viewer.setValueMode(mode)
            let inspector = viewer.cellMetricsText(active)
            XCTAssertTrue(inspector.contains("peak bin 1 ("), inspector)
            XCTAssertTrue(inspector.contains("delay 5.0 ms"), inspector)
            XCTAssertTrue(viewer.tooltipText(active).contains("delay 5.0 ms"))
            XCTAssertEqual(viewer.delayHeatmapPlot(floor: 0).matrix, [[5, nil]])
            let silentInspector = viewer.cellMetricsText(silent)
            XCTAssertTrue(silentInspector.contains("peak bin n/a"), silentInspector)
            XCTAssertTrue(silentInspector.contains("delay n/a, count entropy 0.000"), silentInspector)
        }
    }

    func testMissingExposureStaysMissingAndDoesNotDiluteSmoothedTemporalCounts() throws {
        let viewer = store(try data([[0, 0], [9, 0], [0, 9]], edges: [0, 0.01, 0.02], occupancy: [0, 1, 1]))
        viewer.smoothRadius = 1
        // An unsampled neighbor is not a zero-response observation.
        XCTAssertNil(viewer.delayHeatmapPlot(floor: 9).matrix[0][1])
        XCTAssertEqual(viewer.delayHeatmapPlot(floor: 8).matrix[0][1], 5)
        XCTAssertNil(viewer.delayHeatmapPlot(floor: 0).matrix[0][0])
        XCTAssertNil(viewer.cachedRGBPlot().total[0][0])
        XCTAssertNil(viewer.cachedRGBPlot().entropy[0][0])
    }

    func testTemporalSmoothingKeepsMissingBarrierAndSampledZeroResponsesDistinct() throws {
        let viewer = store(try data([[4, 0], [0, 0], [0, 16]], edges: [0, 0.01, 0.02], occupancy: [1, 0, 1]))
        for radius in 1...3 {
            viewer.smoothRadius = radius
            XCTAssertEqual(viewer.delayHeatmapPlot(floor: 0).matrix, [[5, nil, 15]])
            XCTAssertEqual(viewer.cachedRGBPlot().entropy, [[0, nil, 0]])
        }
        let silent = store(try data([[0, 0], [0, 0]], edges: [0, 0.01, 0.02], occupancy: [0, 1]))
        silent.smoothRadius = 2
        XCTAssertEqual(silent.delayHeatmapPlot(floor: 0).matrix, [[nil, nil]])
        XCTAssertEqual(silent.cachedRGBPlot().total, [[nil, 0]])
        XCTAssertEqual(silent.cachedRGBPlot().entropy, [[nil, 0]])
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

    func testFailedReplacementKeepsCachingCurrentIndexedDocument() async throws {
        let fixture = try XCTUnwrap(Bundle.module.url(
            forResource: "indexed-v2-numpy", withExtension: "base64", subdirectory: "Fixtures"
        ))
        let encoded = try String(contentsOf: fixture, encoding: .utf8)
        let bytes = try XCTUnwrap(Data(base64Encoded: encoded, options: .ignoreUnknownCharacters))
        let source = try RFMappingData(data: bytes, url: URL(fileURLWithPath: "/tmp/retained-indexed.rfmap"))
        let viewer = store(source)
        defer { viewer.cancelPendingLoads() }
        XCTAssertEqual(viewer.cachedUnitCount, 1)
        XCTAssertTrue(viewer.isCachingUnits)

        let missing = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".rfmap")
        let replaced = await viewer.loadJSONAsync(missing)
        XCTAssertFalse(replaced)
        XCTAssertTrue(viewer.data === source)
        XCTAssertNotNil(viewer.errorMessage)
        let deadline = Date().addingTimeInterval(5)
        while !viewer.isUnitCacheComplete && viewer.unitCacheError == nil && Date() < deadline {
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTAssertNil(viewer.unitCacheError)
        XCTAssertTrue(viewer.isUnitCacheComplete)
        XCTAssertEqual(viewer.cachedUnitCount, 3)
        let request = try XCTUnwrap(FigureExportWindowRegistry.shared.prepare(from: viewer))
        FigureExportWindowRegistry.shared.release(request)
    }

}
