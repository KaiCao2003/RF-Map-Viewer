import Foundation
import XCTest
@testable import RFMappingSwiftUI

extension IndexedRFArchiveTests {
    func testResultStatisticsUseSavedHalfOpenWindowAndPopulationSD() throws {
        try withStatisticsSource { source in
            let result = try statisticsResult(source)
            let statistics = SavedRFResultStatistics.calculate(result: result, source: source, unitID: 42)
            XCTAssertNil(statistics.error)
            XCTAssertEqual(try XCTUnwrap(statistics.meanHz), 25, accuracy: 1e-12)
            XCTAssertEqual(try XCTUnwrap(statistics.sdHz), sqrt(1750.0 / 6), accuracy: 1e-12)
            XCTAssertEqual(try XCTUnwrap(statistics.thresholdHz), 25 + 1.8 * sqrt(1750.0 / 6), accuracy: 1e-12)
            XCTAssertEqual(statistics.binCount, 6)
            XCTAssertEqual(statistics.origin, "computed")
            XCTAssertTrue(statistics.detailsText.contains("Original detector mean/SD and temporal aggregation were not saved"))
            XCTAssertTrue(statistics.scope.contains("native RF"))
        }
    }

    func testResultStatisticsCountMissingBinsAsZero() throws {
        try withStatisticsSource(missing: [5]) { source in
            let statistics = SavedRFResultStatistics.calculate(result: try statisticsResult(source), source: source, unitID: 42)
            XCTAssertNil(statistics.error)
            XCTAssertEqual(try XCTUnwrap(statistics.meanHz), 100.0 / 6, accuracy: 1e-12)
            XCTAssertEqual(statistics.binCount, 6)
        }
    }

    func testBottomBaselineUsesLastStoredRowsWithDescendingCoordinates() throws {
        try withStatisticsSource(rates: [[90, 90, 90], [10, 20, 30], [40, 50, 60]], yPositions: [300, 200, 100]) { source in
            let result = try statisticsResult(source, reference: "bottom_row")
            let statistics = SavedRFResultStatistics.calculate(result: result, source: source, unitID: 42)
            XCTAssertNil(statistics.error)
            XCTAssertEqual(try XCTUnwrap(statistics.meanHz), 35, accuracy: 1e-12)
            XCTAssertEqual(statistics.scope, "last two stored rows (all columns)")
            XCTAssertEqual(statistics.binCount, 6)
        }
    }

    func testIndependentOneDimensionalStatisticsPoolTheSavedSpatialSum() throws {
        try withStatisticsSource { source in
            for axis in ["x", "y"] {
                let result = try statisticsResult(source, dimension: "1d", axis: axis)
                let statistics = SavedRFResultStatistics.calculate(result: result, source: source, unitID: 42)
                XCTAssertNil(statistics.error)
                XCTAssertEqual(try XCTUnwrap(statistics.meanHz), axis == "x" ? 50 : 75, accuracy: 1e-12)
                XCTAssertEqual(try XCTUnwrap(statistics.sdHz), axis == "x" ? sqrt(800.0 / 3) : 45, accuracy: 1e-12)
                XCTAssertEqual(statistics.scope, "global 1D sum onto \(axis.uppercased())")
            }
        }
    }

    func testProjectedOneDimensionalStatisticsKeepTheNativeBaseline() throws {
        try withStatisticsSource { source in
            for axis in ["x", "y"] {
                let result = try statisticsResult(source, dimension: "1d", axis: axis, projected: true)
                let statistics = SavedRFResultStatistics.calculate(result: result, source: source, unitID: 42)
                XCTAssertNil(statistics.error)
                XCTAssertEqual(try XCTUnwrap(statistics.meanHz), 25, accuracy: 1e-12)
                XCTAssertEqual(statistics.binCount, 6)
                XCTAssertTrue(statistics.scope.contains("projected from 2D"))
            }
        }
    }

    func testSingletonOneDimensionalSourceNeedsNoSecondSpatialCollapse() throws {
        try withStatisticsSource(rates: [[0, 10, 20]]) { source in
            let aggregations: [Any] = [NSNull(), ["native coverage"]]
            for aggregation in aggregations {
                let result = try statisticsResult(source, dimension: "1d", manifestOverrides: [
                    "spatial_aggregation": aggregation, "y_positions": source.yPositions,
                ])
                let statistics = SavedRFResultStatistics.calculate(result: result, source: source, unitID: 42)
                XCTAssertNil(statistics.error)
                XCTAssertEqual(try XCTUnwrap(statistics.meanHz), 10, accuracy: 1e-12)
                XCTAssertEqual(statistics.binCount, 3)
            }
        }
    }

    func testIndependentOneDimensionalStatisticsRejectUnsupportedSelectionProvenance() throws {
        try withStatisticsSource { source in
            var aggregation: [String: Any] = [
                "operation": "sum", "retained_axis": "x", "rf_only": false, "missing_as_zero": true,
                "selected_indices": [0, 1], "x_positions": source.xPositions, "y_positions": source.yPositions,
            ]
            for indices in [[0, 0], [-1], [source.nY], []] {
                aggregation["selected_indices"] = indices
                let result = try statisticsResult(source, dimension: "1d", manifestOverrides: [
                    "spatial_aggregation": [aggregation],
                ])
                XCTAssertNotNil(SavedRFResultStatistics.calculate(result: result, source: source, unitID: 42).error)
            }
        }
    }

    func testSavedBaselineValuesMatchIDsWithoutReadingRFSource() throws {
        try withStatisticsSource { source in
            let result = try statisticsResult(source, manifestOverrides: ["baseline": recordedBaseline()])
            let statistics = SavedRFResultStatistics.calculate(result: result, source: nil, unitID: 42)
            XCTAssertNil(statistics.error)
            XCTAssertEqual(statistics.meanHz, 123)
            XCTAssertEqual(statistics.sdHz, 4)
            XCTAssertEqual(try XCTUnwrap(statistics.thresholdHz), 130.2, accuracy: 1e-12)
            XCTAssertEqual(statistics.origin, "saved")
            XCTAssertEqual(statistics.processingLabel, "Smoothing 1")
            XCTAssertFalse(statistics.basisKey.isEmpty)
            XCTAssertTrue(statistics.detailsText.contains("not recomputed"))
        }
    }

    func testInvalidSavedStatisticsDoNotFallBackToCurrentSource() throws {
        try withStatisticsSource { source in
            let changes: [[String: Any]] = [
                ["unit_ids": [42, 42]], ["mean_hz": [1]], ["sd_hz": [2, -1]],
                ["unit_ids": [17, 99]], ["mean_hz": [true, false]], ["sd_hz": NSNull()],
            ]
            for change in changes {
                var baseline = recordedBaseline()
                baseline.merge(change) { _, replacement in replacement }
                let result = try statisticsResult(source, manifestOverrides: ["baseline": baseline])
                let statistics = SavedRFResultStatistics.calculate(result: result, source: source, unitID: 42)
                XCTAssertNotNil(statistics.error)
                XCTAssertNil(statistics.meanHz)
                XCTAssertEqual(statistics.origin, "saved")
            }
        }
    }

    func testUnsupportedBaselineRecipesAreReportedUnavailable() throws {
        try withStatisticsSource { source in
            let changes: [[String: Any]] = [
                ["detector_algorithm": "unknown"], ["response_normalization": "occupancy_time"],
                ["response_units": "spike_count"], ["spatial_aggregation": [["operation": "sum"]]],
            ]
            for change in changes {
                let result = try statisticsResult(source, manifestOverrides: change)
                XCTAssertNotNil(SavedRFResultStatistics.calculate(result: result, source: source, unitID: 42).error)
            }
            for parameter in ["is_shuffle", "exclude_zero_bins"] {
                let result = try statisticsResult(source, parameterOverrides: [parameter: true])
                XCTAssertNotNil(SavedRFResultStatistics.calculate(result: result, source: source, unitID: 42).error)
            }
            let result = try statisticsResult(source)
            XCTAssertNotNil(SavedRFResultStatistics.calculate(result: result, source: nil, unitID: 42).error)
            XCTAssertNotNil(SavedRFResultStatistics.calculate(result: result, source: source, unitID: 99).error)
        }
    }

    func testSavedStatisticsSharingRequiresMatchingRecipeAndValues() throws {
        try withStatisticsSource { source in
            let baseline = recordedBaseline()
            let excitatory = try statisticsResult(source, manifestOverrides: ["baseline": baseline])
            let inhibitory = try statisticsResult(source, polarity: "inhibitory", manifestOverrides: ["baseline": baseline])
            let a = SavedRFResultStatistics.calculate(result: excitatory, source: nil, unitID: 42)
            let b = SavedRFResultStatistics.calculate(result: inhibitory, source: nil, unitID: 42)
            XCTAssertTrue(a.sharesBaseline(with: b))
            XCTAssertEqual(try XCTUnwrap(b.thresholdHz), 115.8, accuracy: 1e-12)
            var different = baseline
            different["response_stage"] = "before_smoothing"
            let changedRecipe = try statisticsResult(source, manifestOverrides: ["baseline": different])
            XCTAssertFalse(a.sharesBaseline(with: SavedRFResultStatistics.calculate(result: changedRecipe, source: nil, unitID: 42)))
            different = baseline
            different["mean_hz"] = [9, 124]
            let changedValues = try statisticsResult(source, manifestOverrides: ["baseline": different])
            XCTAssertFalse(a.sharesBaseline(with: SavedRFResultStatistics.calculate(result: changedValues, source: nil, unitID: 42)))
            let incomplete = try statisticsResult(source, manifestOverrides: [
                "baseline": ["unit_ids": [42], "mean_hz": [123], "sd_hz": [4]],
            ])
            let incompleteStatistics = SavedRFResultStatistics.calculate(result: incomplete, source: nil, unitID: 42)
            XCTAssertNil(incompleteStatistics.error)
            XCTAssertTrue(incompleteStatistics.basisKey.isEmpty)
            XCTAssertFalse(incompleteStatistics.sharesBaseline(with: incompleteStatistics))
        }
    }

    func testConstantBaselineHasZeroPopulationSD() throws {
        try withStatisticsSource(rates: [[10, 10, 10], [10, 10, 10]]) { source in
            let statistics = SavedRFResultStatistics.calculate(result: try statisticsResult(source), source: source, unitID: 42)
            XCTAssertNil(statistics.error)
            XCTAssertEqual(try XCTUnwrap(statistics.meanHz), 10, accuracy: 1e-12)
            XCTAssertEqual(try XCTUnwrap(statistics.sdHz), 0, accuracy: 1e-12)
            XCTAssertEqual(try XCTUnwrap(statistics.thresholdHz), 10, accuracy: 1e-12)
        }
    }

    private func recordedBaseline() -> [String: Any] {
        ["unit_ids": [99, 42], "mean_hz": [9, 123], "sd_hz": [2, 4],
         "mean_scope": "bottom_two_rows", "sd_scope": "bottom_two_rows", "sd_ddof": 0,
         "response_stage": "after_gui_smoothing1", "bin_count": 6]
    }

    private func withStatisticsSource(
        rates: [[Int]] = [[0, 10, 20], [30, 40, 50]], yPositions: [Int]? = nil,
        missing: Set<Int> = [], body: (RFMappingData) throws -> Void
    ) throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let nY = rates.count, nX = rates[0].count
        let presentations = (0..<nY).map { y in
            (0..<nX).map { x in missing.contains(y * nX + x) ? 0 : y * nX + x + 1 }
        }
        let counts = (0..<nY).map { y in
            (0..<nX).map { x -> [Int] in
                let total = rates[y][x] * presentations[y][x] / 5
                return [missing.contains(y * nX + x) ? 0 : 100, total / 2, total - total / 2]
            }
        }
        let occupancy = presentations.map { row in row.map { $0 == 0 ? 0 : 1 } }
        let payload = currentRFSchemaPayload([
            "unitsSpikeCounts": [counts], "unitsSpikeCountsSize": [1, nY, nX, 3], "unitPool": [42],
            "xPositions": Array(0..<nX), "yPositions": yPositions ?? (0..<nY).map { 100 + 100 * $0 },
            "timeBinEdges": [-0.1, 0, 0.05, 0.2], "stimulusPresentationCounts": presentations,
        ], occupancyTimeSec: occupancy, occupancyTimeSecSize: [nY, nX])
        let source = try RFMappingData(data: JSONSerialization.data(withJSONObject: payload),
                                      url: directory.appendingPathComponent("statistics.rfmap"))
        try body(source)
    }

    private func statisticsResult(
        _ source: RFMappingData, reference: String = "global", dimension: String = "2d", axis: String = "x",
        projected: Bool = false, polarity: String = "excitatory",
        manifestOverrides: [String: Any] = [:], parameterOverrides: [String: Any] = [:]
    ) throws -> SavedRFResult {
        let shape = dimension == "2d" ? [1, source.nY, source.nX]
            : axis == "x" ? [1, 1, source.nX] : [1, source.nY, 1]
        var parameters: [String: Any] = [
            "reference": reference, "is_shuffle": false, "exclude_zero_bins": false,
            "cluster_forming_z": 1.8, "alternative": polarity == "inhibitory" ? "less" : "greater",
        ]
        parameters.merge(parameterOverrides) { _, replacement in replacement }
        var manifest: [String: Any] = [
            "detector_algorithm": reference == "bottom_row" ? "bottom-two-rows-mean-sd-v1" : "pooled-spatial-z-v3-zero-filled",
            "response_units": "Hz", "response_normalization": "presentation_count_time",
            "x_positions": source.xPositions, "y_positions": source.yPositions, "parameters": parameters,
        ]
        if reference == "bottom_row" {
            manifest["baseline"] = [
                "row_indices": [source.nY - 2, source.nY - 1], "y_positions": Array(source.yPositions.suffix(2)),
                "bin_count": 2 * source.nX, "mean_scope": "bottom_two_rows", "sd_scope": "bottom_two_rows", "sd_ddof": 0,
            ]
        }
        if dimension == "1d" {
            manifest[axis == "x" ? "y_positions" : "x_positions"] = projected
                ? Array((axis == "x" ? source.yPositions : source.xPositions).prefix(1)) : [0.0]
            if projected {
                manifest["projection_source_cache_key"] = "original-2d-key"
            } else {
                manifest["spatial_aggregation"] = [[
                    "operation": "sum", "retained_axis": axis, "rf_only": false, "missing_as_zero": true,
                    "selected_indices": Array(0..<(axis == "x" ? source.nY : source.nX)),
                    "x_positions": source.xPositions, "y_positions": source.yPositions,
                ]]
            }
        }
        manifest.merge(manifestOverrides) { _, replacement in replacement }
        let count = shape[1] * shape[2]
        let mask = [UInt8](repeating: 0, count: count)
        let path = try SavedRFResult.resultURL(source: source.url, dimension: dimension, polarity: polarity)
        try savedArchive(shape: shape, mask: mask, center: mask, axis: dimension == "1d" ? axis : nil,
                         polarity: polarity, manifestOverrides: manifest).write(to: path)
        return try SavedRFResult.load(source: source, dimension: dimension, polarity: polarity)
    }
}
