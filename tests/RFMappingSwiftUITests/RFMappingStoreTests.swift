import Foundation
import XCTest
@testable import RFMappingSwiftUI

final class RFMappingStoreTests: XCTestCase {
    func testCompensatedSumMatchesRequiredPythonRuntime() {
        XCTAssertEqual(compensatedSum([1e16, 1, 1]), 1.0000000000000002e16)
    }

    func testSpatialGroupingAndWeightedSmoothingPreserveMissingSamples() {
        XCTAssertEqual(
            axisGroupsForTarget(sourceCount: 5, targetCount: 2),
            [AxisGroup(start: 0, end: 1), AxisGroup(start: 2, end: 4)]
        )

        let reduced = reduceMatrixXY(
            optionalMatrix([[1, 3], [5, 7]]),
            yGroups: [AxisGroup(start: 0, end: 1)],
            xGroups: [AxisGroup(start: 0, end: 1)]
        )
        XCTAssertEqual(reduced[0][0], 4)

        let smoothed = smoothMatrix([[1, nil], [nil, nil]], radius: 1)
        XCTAssertEqual(smoothed[0][0], 1)
        XCTAssertNil(smoothed[0][1])
        XCTAssertNil(smoothed[1][0])
        XCTAssertNil(smoothed[1][1])
    }

    func testExactRangeSnappingAndTimeGroupingMatchPython() throws {
        let fixture = try makeFixture()
        defer { try? FileManager.default.removeItem(at: fixture.deletingLastPathComponent()) }
        let store = RFMappingStore(initialURL: fixture)

        XCTAssertEqual(store.baseBinMS(), 50, accuracy: 1e-9)
        store.timeResolutionMS = 100
        store.rangeStartMS = -90
        store.rangeEndMS = 55
        store.normalizeControls()

        XCTAssertEqual(store.timeGroups(), [
            AxisGroup(start: 0, end: 0),
            AxisGroup(start: 1, end: 1),
            AxisGroup(start: 2, end: 2)
        ])
        XCTAssertEqual(store.timeGroupLabel(0), "-100–0 ms")
        XCTAssertEqual(store.timeGroupLabel(1), "0–50 ms")
        XCTAssertEqual(store.timeGroupLabel(2), "50–200 ms")
        XCTAssertEqual(store.sourceBinsForSelectedRange(), AxisGroup(start: 0, end: 1))
        XCTAssertEqual(store.selectedTimeBoundsMS().0, -100, accuracy: 1e-9)
        XCTAssertEqual(store.selectedTimeBoundsMS().1, 50, accuracy: 1e-9)

        store.selectTimelineBin(2, extending: false)
        XCTAssertEqual(store.sourceBinsForSelectedRange(), AxisGroup(start: 2, end: 2))
        XCTAssertEqual(store.visibleTimelineBins(displayBins: 3), [0, 1, 2])
        XCTAssertEqual(store.timelineSnapshot().matrices.count, 3)
    }

    func testPhysicalTimeGroupingPreservesUniformGroupingAndUsesMeasuredEdges() {
        XCTAssertEqual(
            physicalTimeGroups(
                edgesMS: [0, 10, 20, 30, 40, 50],
                targetDurationMS: 20
            ),
            [
                AxisGroup(start: 0, end: 1),
                AxisGroup(start: 2, end: 3),
                AxisGroup(start: 4, end: 4)
            ]
        )
        XCTAssertEqual(
            physicalTimeGroups(
                edgesMS: [-100, 0, 50, 200],
                targetDurationMS: 100
            ),
            [
                AxisGroup(start: 0, end: 0),
                AxisGroup(start: 1, end: 1),
                AxisGroup(start: 2, end: 2)
            ]
        )
    }

    func testTimeResolutionStepsByOneSourceBinWidth() throws {
        let fixture = try makeFixture()
        defer { try? FileManager.default.removeItem(at: fixture.deletingLastPathComponent()) }
        let store = RFMappingStore(initialURL: fixture)

        XCTAssertEqual(store.baseBinMS(), 50, accuracy: 1e-9)
        store.timeResolutionMS = 50
        store.stepTimeResolution(1)
        XCTAssertEqual(store.timeResolutionMS, 100, accuracy: 1e-9)
        store.stepTimeResolution(-1)
        XCTAssertEqual(store.timeResolutionMS, 50, accuracy: 1e-9)
    }

    func testReloadPreservesSmoothingLikePython() throws {
        let fixture = try makeFixture()
        defer { try? FileManager.default.removeItem(at: fixture.deletingLastPathComponent()) }
        let store = RFMappingStore(initialURL: fixture)
        store.smoothRadius = 3

        store.loadJSON(fixture)

        XCTAssertEqual(store.smoothRadius, 3)
    }

    @MainActor
    func testManualTuningDetachIsRememberedPerRFUntilManualAttach() async throws {
        let fixture = try makeFixture()
        let otherFixture = try makeFixture()
        defer {
            try? FileManager.default.removeItem(at: fixture.deletingLastPathComponent())
            try? FileManager.default.removeItem(at: otherFixture.deletingLastPathComponent())
        }
        let store = RFMappingStore(initialURL: fixture)

        store.clearTuningCurve()
        XCTAssertTrue(store.isTuningAutoloadSuppressedForCurrentData)
        XCTAssertTrue(store.loadJSON(fixture))
        XCTAssertTrue(store.isTuningAutoloadSuppressedForCurrentData)

        let tuningURL = fixture.deletingLastPathComponent()
            .appendingPathComponent("tuning_curves.json")
        let rates = (0..<hdRawBinCount).map(Double.init)
        try JSONSerialization.data(withJSONObject: ["42": rates]).write(to: tuningURL)

        let loaded = await store.loadTuningCurveAsync(tuningURL)
        XCTAssertTrue(loaded)
        XCTAssertFalse(store.isTuningAutoloadSuppressedForCurrentData)

        store.clearTuningCurve()
        XCTAssertTrue(store.isTuningAutoloadSuppressedForCurrentData)
        XCTAssertTrue(store.loadJSON(otherFixture))
        XCTAssertFalse(store.isTuningAutoloadSuppressedForCurrentData)
        XCTAssertTrue(store.loadJSON(fixture))
        XCTAssertTrue(store.isTuningAutoloadSuppressedForCurrentData)
    }

    func testRequestBoundValueNeverExposesAStaleUnitResult() {
        let result = RequestBoundValue(request: 7, value: "cluster 7 curve")

        XCTAssertEqual(result.value(for: 7), "cluster 7 curve")
        XCTAssertNil(result.value(for: 8))
    }

    func testValueModesAndViewSpecificFullWindowSemantics() throws {
        let fixture = try makeFixture()
        defer { try? FileManager.default.removeItem(at: fixture.deletingLastPathComponent()) }
        let store = RFMappingStore(initialURL: fixture)

        XCTAssertEqual(store.sourceBinsForPlotRange(), AxisGroup(start: 1, end: 1))
        XCTAssertEqual(store.currentMatrix()[0][0], 20)
        XCTAssertEqual(store.currentMatrix()[0][1], 10)
        XCTAssertNil(store.currentMatrix()[1][0])
        store.setValueMode(.meanFiringRate)
        XCTAssertEqual(try XCTUnwrap(store.currentMatrix()[0][0]), 40, accuracy: 1e-9)
        XCTAssertEqual(try XCTUnwrap(store.currentMatrix()[0][1]), 40, accuracy: 1e-9)
        XCTAssertNil(store.currentMatrix()[1][0])
        XCTAssertEqual(try XCTUnwrap(store.currentMatrix()[1][1]), 40, accuracy: 1e-9)

        store.plotRangeStartMS = -100
        store.plotRangeEndMS = 200
        store.normalizePlotTimeRange()
        XCTAssertEqual(try XCTUnwrap(store.currentMatrix()[0][0]), 20, accuracy: 1e-9)

        let fullWindow = try XCTUnwrap(store.data).responseMatrix(
            unitIndex: 0,
            start: 0,
            end: 2,
            valueMode: store.valueMode
        )
        XCTAssertEqual(try XCTUnwrap(fullWindow[0][0]), 20, accuracy: 1e-9)
    }

    func testZeroExposureCountRemainsMissingAcrossRFAndRGBPlotsAndTooltip() throws {
        let fixture = try makeFixture()
        defer { try? FileManager.default.removeItem(at: fixture.deletingLastPathComponent()) }
        let store = RFMappingStore(initialURL: fixture)
        store.flipY = false
        store.xBins = 2
        store.yBins = 2
        store.smoothRadius = 0
        store.setValueMode(.spikeCount)

        XCTAssertNil(store.currentHeatmapPlot().matrix[1][0])
        XCTAssertNil(store.cachedRGBPlot().total[1][0])
        XCTAssertTrue(
            store.tooltipText(CellRef(yStart: 1, yEnd: 1, xStart: 0, xEnd: 0))
                .contains("n/a")
        )
    }

    func testDisplayGroupingPoolsUnequalExposureAndSmoothsNumeratorDenominator() throws {
        let fixture = try makeSpatialEstimandFixture()
        defer { try? FileManager.default.removeItem(at: fixture.deletingLastPathComponent()) }
        let store = RFMappingStore(initialURL: fixture)
        store.flipY = false
        store.yBins = 1
        store.xBins = 1
        store.smoothRadius = 0
        store.setValueMode(.spikesPerPresentation)

        let pooled = store.preparedResponseMatrix(sourceStart: 0, sourceEnd: 1, smooth: false)
        XCTAssertEqual(try XCTUnwrap(pooled.0[0][0]), 109.0 / 101.0, accuracy: 1e-12)

        store.xBins = 2
        store.smoothRadius = 1
        store.setValueMode(.meanFiringRate)
        let smoothed = store.preparedResponseMatrix(sourceStart: 0, sourceEnd: 0, smooth: true).0
        XCTAssertEqual(
            try XCTUnwrap(smoothed[0][0]),
            ((4.0 * 100.0 + 2.0 * 0.0) / (4.0 * 100.0 + 2.0 * 1.0)) / 0.1,
            accuracy: 1e-12
        )
        XCTAssertEqual(
            try XCTUnwrap(smoothed[0][1]),
            ((4.0 * 0.0 + 2.0 * 100.0) / (4.0 * 1.0 + 2.0 * 100.0)) / 0.1,
            accuracy: 1e-12
        )
        XCTAssertNotEqual(try XCTUnwrap(smoothed[0][0]), (4.0 * 10.0 + 2.0 * 0.0) / 6.0)
    }

    func testGroupedDelayAndRGBEntropyAreRecomputedFromPooledHistogram() throws {
        let fixture = try makeSpatialEstimandFixture()
        defer { try? FileManager.default.removeItem(at: fixture.deletingLastPathComponent()) }
        let store = RFMappingStore(initialURL: fixture)
        store.flipY = false
        store.yBins = 1
        store.xBins = 1
        store.smoothRadius = 0
        store.timeResolutionMS = 100
        store.normalizeControls()

        let delay = store.delayHeatmapPlot(floor: 0)
        XCTAssertEqual(try XCTUnwrap(delay.matrix[0][0]), 50.0, accuracy: 1e-12)

        let probabilities = [100.0 / 109.0, 9.0 / 109.0]
        let expectedEntropy = -probabilities.reduce(0.0) {
            $0 + $1 * log($1)
        } / log(2.0)
        let rgb = store.cachedRGBPlot()
        XCTAssertEqual(try XCTUnwrap(rgb.entropy[0][0]), expectedEntropy, accuracy: 1e-12)
        XCTAssertGreaterThan(try XCTUnwrap(rgb.entropy[0][0]), 0.0)

        store.xBins = 2
        store.smoothRadius = 1
        let smoothedDelay = store.delayHeatmapPlot(floor: 0).matrix
        XCTAssertEqual(try XCTUnwrap(smoothedDelay[0][0]), 50.0, accuracy: 1e-12)
        XCTAssertEqual(try XCTUnwrap(smoothedDelay[0][1]), 50.0, accuracy: 1e-12)
        let smoothedRGB = store.cachedRGBPlot()
        XCTAssertGreaterThan(try XCTUnwrap(smoothedRGB.entropy[0][0]), 0.0)
        XCTAssertGreaterThan(try XCTUnwrap(smoothedRGB.entropy[0][1]), 0.0)
    }

    func testDisplayedCSVHasExactFortyColumnSchemaAndProvenance() throws {
        let fixture = try makeFixture()
        defer { try? FileManager.default.removeItem(at: fixture.deletingLastPathComponent()) }
        let store = RFMappingStore(initialURL: fixture)
        store.xBins = 1
        store.yBins = 1
        store.smoothRadius = 0
        store.flipY = true
        store.plotRangeStartMS = -100
        store.plotRangeEndMS = 200
        store.normalizeControls()

        let csv = store.exportCSV()
        XCTAssertTrue(csv.hasSuffix("\r\n"))
        XCTAssertFalse(csv.replacingOccurrences(of: "\r\n", with: "").contains("\n"))
        let lines = csv.components(separatedBy: "\r\n").filter { !$0.isEmpty }
        XCTAssertEqual(lines.count, 2)
        let header = lines[0].split(separator: ",", omittingEmptySubsequences: false).map(String.init)
        let row = lines[1].split(separator: ",", omittingEmptySubsequences: false).map(String.init)
        XCTAssertEqual(header.count, 40)
        XCTAssertEqual(row.count, 40)

        let record = Dictionary(uniqueKeysWithValues: zip(header, row))
        XCTAssertEqual(record["value"], "34.0")
        XCTAssertEqual(record["value_mode"], "Spike count")
        XCTAssertEqual(record["value_unit"], "spikes")
        XCTAssertEqual(record["presentation_count_min"], "0.0")
        XCTAssertEqual(record["presentation_count_max"], "10.0")
        XCTAssertEqual(record["export_space"], "displayed")
        XCTAssertEqual(record["display_x_bins"], "1")
        XCTAssertEqual(record["display_y_bins"], "1")
        XCTAssertEqual(record["flip_y"], "True")
        XCTAssertEqual(record["source_json"], fixture.path)
    }

    func testDefaultRFPlotRangeIsZeroToTwentyAndClampsToAvailableAxis() throws {
        let normal = try makeRangeFixture(edges: [-0.01, 0, 0.01, 0.02, 0.03])
        let short = try makeRangeFixture(edges: [-0.01, 0, 0.005, 0.012])
        let positiveOnly = try makeRangeFixture(edges: [0.05, 0.06, 0.07])
        let negativeOnly = try makeRangeFixture(edges: [-0.04, -0.03, -0.02])
        defer {
            for url in [normal, short, positiveOnly, negativeOnly] {
                try? FileManager.default.removeItem(at: url.deletingLastPathComponent())
            }
        }

        let normalStore = RFMappingStore(initialURL: normal)
        XCTAssertEqual(normalStore.plotRangeStartMS, 0, accuracy: 1e-9)
        XCTAssertEqual(normalStore.plotRangeEndMS, 20, accuracy: 1e-9)
        XCTAssertEqual(normalStore.sourceBinsForPlotRange(), AxisGroup(start: 1, end: 2))

        let shortStore = RFMappingStore(initialURL: short)
        XCTAssertEqual(shortStore.plotRangeStartMS, 0, accuracy: 1e-9)
        XCTAssertEqual(shortStore.plotRangeEndMS, 12, accuracy: 1e-9)

        let positiveStore = RFMappingStore(initialURL: positiveOnly)
        XCTAssertEqual(positiveStore.plotRangeStartMS, 50, accuracy: 1e-9)
        XCTAssertEqual(positiveStore.plotRangeEndMS, 60, accuracy: 1e-9)
        XCTAssertEqual(positiveStore.sourceBinsForPlotRange(), AxisGroup(start: 0, end: 0))

        let negativeStore = RFMappingStore(initialURL: negativeOnly)
        XCTAssertEqual(negativeStore.plotRangeStartMS, -30, accuracy: 1e-9)
        XCTAssertEqual(negativeStore.plotRangeEndMS, -20, accuracy: 1e-9)
        XCTAssertEqual(negativeStore.sourceBinsForPlotRange(), AxisGroup(start: 1, end: 1))
    }

    func testTimelineSelectionDoesNotChangeRFPlotRangeOrMatrix() throws {
        let fixture = try makeFixture()
        defer { try? FileManager.default.removeItem(at: fixture.deletingLastPathComponent()) }
        let store = RFMappingStore(initialURL: fixture)
        let originalPlotRange = store.sourceBinsForPlotRange()
        let originalMatrix = store.currentMatrix()

        store.timeResolutionMS = 100
        store.normalizeControls()
        store.selectTimelineBin(0, extending: false)

        XCTAssertEqual(store.sourceBinsForSelectedRange(), AxisGroup(start: 0, end: 0))
        XCTAssertEqual(store.sourceBinsForPlotRange(), originalPlotRange)
        XCTAssertEqual(store.currentMatrix(), originalMatrix)

        store.selectTimelineBin(1, extending: false)
        XCTAssertEqual(store.sourceBinsForSelectedRange(), AxisGroup(start: 1, end: 1))
        XCTAssertEqual(store.sourceBinsForPlotRange(), originalPlotRange)
        XCTAssertEqual(store.currentMatrix(), originalMatrix)

        store.selectTimelineBin(2, extending: false)
        XCTAssertEqual(store.sourceBinsForSelectedRange(), AxisGroup(start: 2, end: 2))
        XCTAssertEqual(store.sourceBinsForPlotRange(), originalPlotRange)
        XCTAssertEqual(store.currentMatrix(), originalMatrix)
    }

    func testSpatialTooltipReportsIndependentRFRangeAndDisplayedValue() throws {
        let fixture = try makeFixture()
        defer { try? FileManager.default.removeItem(at: fixture.deletingLastPathComponent()) }
        let store = RFMappingStore(initialURL: fixture)
        let cell = CellRef(yStart: 0, yEnd: 0, xStart: 0, xEnd: 0)

        let tooltip = store.tooltipText(cell)

        XCTAssertTrue(tooltip.contains("bin 1: 10 spikes"))
        XCTAssertTrue(tooltip.contains("RF sum range 0–50 ms: 20 spikes"))
    }

    func testRFResponseScaleIsZeroToPeakWhileDelayKeepsTimeRange() throws {
        let fixture = try makeFixture()
        defer { try? FileManager.default.removeItem(at: fixture.deletingLastPathComponent()) }
        let store = RFMappingStore(initialURL: fixture)
        store.smoothRadius = 0

        let response = store.currentHeatmapPlot()
        XCTAssertEqual(response.low, 0.0, accuracy: 1e-12)
        XCTAssertEqual(response.high, 20.0, accuracy: 1e-12)

        let delay = store.delayHeatmapPlot(floor: 0)
        let timeRange = store.timeAxisRangeMS()
        XCTAssertEqual(delay.low, timeRange.0, accuracy: 1e-12)
        XCTAssertEqual(delay.high, timeRange.1, accuracy: 1e-12)
        XCTAssertLessThan(delay.low, 0)
    }

    func testFullCirclePolarSectorUsesEnoughArcSamplesToRemainVisible() {
        let start = 3.0 * Double.pi / 2.0
        let end = -Double.pi / 2.0

        XCTAssertEqual(polarArcSampleCount(thetaStart: start, thetaEnd: end), 17)
        XCTAssertEqual(
            polarArcSampleCount(thetaStart: start, thetaEnd: start - Double.pi / 12.0),
            3
        )
    }

    func testPlotNavigationHasExactlyThreeCombinedTabs() {
        XCTAssertEqual(PlotTab.allCases, [.rf, .delayRGB, .timeline])
        let store = RFMappingStore(loadDefault: false)
        store.selectTab(2)
        XCTAssertEqual(store.selectedTab, .timeline)
        store.selectTab(3)
        XCTAssertEqual(store.selectedTab, .timeline)
    }

    private func makeFixture() throws -> URL {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("RFMappingStoreTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let url = directory.appendingPathComponent("fixture.json")
        let payload: [String: Any] = [
            "unitsSpikeCounts": [[
                [[10, 20, 30], [5, 10, 15]],
                [[0, 0, 0], [2, 4, 6]]
            ]],
            "unitsSpikeCountsSize": [1, 2, 2, 3],
            "unitPool": [42],
            "xPositions": [-1, 1],
            "yPositions": [-1, 1],
            "timeBinEdges": [-0.1, 0, 0.05, 0.2],
            "stimulusPresentationCounts": [[10, 5], [0, 2]]
        ]
        try JSONSerialization.data(withJSONObject: payload).write(to: url)
        return url
    }

    private func makeSpatialEstimandFixture() throws -> URL {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("RFMappingSpatialEstimandTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let url = directory.appendingPathComponent("fixture.json")
        let payload: [String: Any] = [
            "unitsSpikeCounts": [[[[100, 0], [0, 9]]]],
            "unitsSpikeCountsSize": [1, 1, 2, 2],
            "unitPool": [42],
            "xPositions": [-1, 1],
            "yPositions": [0],
            "timeBinEdges": [0, 0.1, 0.2],
            "stimulusPresentationCounts": [[100, 1]]
        ]
        try JSONSerialization.data(withJSONObject: payload).write(to: url)
        return url
    }

    private func makeRangeFixture(edges: [Double]) throws -> URL {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("RFMappingRangeTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let url = directory.appendingPathComponent("range-fixture.json")
        let bins = Array(1...max(1, edges.count - 1))
        let payload: [String: Any] = [
            "unitsSpikeCounts": [[[bins]]],
            "unitsSpikeCountsSize": [1, 1, 1, bins.count],
            "unitPool": [1],
            "xPositions": [0],
            "yPositions": [0],
            "timeBinEdges": edges
        ]
        try JSONSerialization.data(withJSONObject: payload).write(to: url)
        return url
    }
}
