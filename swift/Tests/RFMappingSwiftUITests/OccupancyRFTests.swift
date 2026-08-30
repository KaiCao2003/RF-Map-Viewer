import Foundation
import XCTest
@testable import RFMappingSwiftUI

@MainActor
final class OccupancyRFTests: XCTestCase {
    private let sourceURL = URL(fileURLWithPath: "/tmp/current-occupancy.rfmap")

    private func makeData(
        counts: Any = [[[[10.0], [100.0], [10.0]]]],
        occupancy: Any = [1.0, 100.0, 1.0],
        occupancySize: [Int] = [1, 3]
    ) throws -> RFMappingData {
        let payload = currentRFSchemaPayload([
            "unitsSpikeCounts": counts,
            "unitsSpikeCountsSize": [1, 1, 3, 1],
            "unitPool": 17,
            "xPositions": [-1.0, 0.0, 1.0],
            "yPositions": 0.0,
            "timeBinEdges": [0.0, 1.0],
        ], occupancyTimeSec: occupancy, occupancyTimeSecSize: occupancySize)
        return try RFMappingData(
            data: JSONSerialization.data(withJSONObject: payload),
            url: sourceURL
        )
    }

    func testRateIsCountDividedByOccupancySeconds() throws {
        let data = try makeData()
        let rates = try data.responseMatrix(
            unitIndex: 0,
            start: 0,
            end: 0,
            valueMode: .meanFiringRate
        )
        let counts = try data.responseMatrix(
            unitIndex: 0,
            start: 0,
            end: 0,
            valueMode: .spikeCount
        )

        XCTAssertEqual(rates[0][0], 10.0)
        XCTAssertEqual(rates[0][1], 1.0)
        XCTAssertEqual(rates[0][2], 10.0)
        XCTAssertEqual(counts[0][1], 100.0)
    }

    func testZeroOccupancyIsMissingAndCannotContainCounts() throws {
        let zeroPayload = currentRFSchemaPayload([
            "unitsSpikeCounts": [[[[0.0], [5.0]]]],
            "unitsSpikeCountsSize": [1, 1, 2, 1],
            "unitPool": 17,
            "xPositions": [-1.0, 1.0],
            "yPositions": 0.0,
            "timeBinEdges": [0.0, 0.1],
        ], occupancyTimeSec: [0.0, 0.5], occupancyTimeSecSize: [1, 2])
        let data = try RFMappingData(
            data: JSONSerialization.data(withJSONObject: zeroPayload),
            url: sourceURL
        )

        XCTAssertNil(try data.responseValue(
            unitIndex: 0,
            yIndex: 0,
            xIndex: 0,
            start: 0,
            end: 0,
            valueMode: .spikeCount
        ))
        XCTAssertNil(try data.responseValue(
            unitIndex: 0,
            yIndex: 0,
            xIndex: 0,
            start: 0,
            end: 0,
            valueMode: .meanFiringRate
        ))

        var invalid = zeroPayload
        invalid["unitsSpikeCounts"] = [[[[1.0], [5.0]]]]
        XCTAssertThrowsError(try RFMappingData(
            data: JSONSerialization.data(withJSONObject: invalid),
            url: sourceURL
        )) { error in
            XCTAssertTrue(error.localizedDescription.contains("zero where spikeCounts is nonzero"))
        }
    }

    func testCurrentSchemaRejectsFractionalCountsAndFixedMetadataChanges() throws {
        var fractional = currentRFSchemaPayload([
            "unitsSpikeCounts": [[[[0.5]]]],
            "unitsSpikeCountsSize": [1, 1, 1, 1],
            "unitPool": 17,
            "xPositions": 0.0,
            "yPositions": 0.0,
            "timeBinEdges": [0.0, 0.1],
        ], occupancyTimeSec: 0.1, occupancyTimeSecSize: [1, 1])
        XCTAssertThrowsError(try RFMappingData(
            data: JSONSerialization.data(withJSONObject: fractional),
            url: sourceURL
        )) { error in
            XCTAssertTrue(error.localizedDescription.contains("integers"))
        }

        fractional["unitsSpikeCounts"] = [[[[0.0]]]]
        let invalidMetadata: [(String, String)] = [
            ("responseUnits", "mean_spikes"),
            ("responseNormalization", "legacy"),
            ("spikeCountDefinition", "legacy"),
            ("occupancyTimeDefinition", "legacy"),
        ]
        for (key, value) in invalidMetadata {
            var candidate = fractional
            candidate[key] = value
            XCTAssertThrowsError(try RFMappingData(
                data: JSONSerialization.data(withJSONObject: candidate),
                url: sourceURL
            )) { error in
                XCTAssertTrue(error.localizedDescription.contains(key))
            }
        }
    }

    func testOccupancyDeclaredShapeMustMatchRFDimensions() throws {
        XCTAssertThrowsError(try makeData(occupancySize: [3, 1])) { error in
            XCTAssertTrue(error.localizedDescription.contains("occupancyTimeSecSize"))
        }
    }

    func testAllZeroOccupancyIsRejected() throws {
        XCTAssertThrowsError(try makeData(
            counts: [[[[0.0], [0.0], [0.0]]]],
            occupancy: [0.0, 0.0, 0.0]
        )) { error in
            XCTAssertTrue(error.localizedDescription.contains("at least one positive"))
        }
    }

    func testEveryCurrentSchemaFieldIsRequired() throws {
        let valid = currentRFSchemaPayload([
            "unitsSpikeCounts": [[[[0.0]]]],
            "unitsSpikeCountsSize": [1, 1, 1, 1],
            "unitPool": 17,
            "xPositions": 0.0,
            "yPositions": 0.0,
            "timeBinEdges": [0.0, 0.1],
        ], occupancyTimeSec: 0.1, occupancyTimeSecSize: [1, 1])
        let required = [
            "unitsSpikeCounts", "unitsSpikeCountsSize", "unitPool", "xPositions",
            "yPositions", "timeBinEdges", "occupancyTimeSec", "occupancyTimeSecSize",
            "responseUnits", "responseNormalization", "spikeCountDefinition",
            "occupancyTimeDefinition",
        ]

        for key in required {
            var candidate = valid
            candidate.removeValue(forKey: key)
            XCTAssertThrowsError(try RFMappingData(
                data: JSONSerialization.data(withJSONObject: candidate),
                url: sourceURL
            )) { error in
                XCTAssertTrue(error.localizedDescription.contains(key))
            }
        }
    }

    func testSpatialPoolingSmoothingTimelineSelectionAndExportUseOccupancy() throws {
        let data = try makeData()
        let store = RFMappingStore(
            initialData: data,
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false
        )

        XCTAssertEqual(store.valueMode, .meanFiringRate)
        XCTAssertEqual(store.selectedCell?.xStart, 0)
        XCTAssertTrue(store.unitStatsText.contains("Strongest rate cell: yIdx 1, xIdx 1"))

        store.valueMode = .spikeCount
        store.selectUnitID(17)
        XCTAssertEqual(
            store.selectedCell?.xStart,
            0,
            "Initial selection must use full-window rate even while count is displayed."
        )
        store.valueMode = .meanFiringRate

        store.xBins = 1
        store.normalizeControls()
        let pooled = store.currentHeatmapPlot().matrix[0][0]
        XCTAssertEqual(pooled ?? .nan, 120.0 / 102.0, accuracy: 1e-12)
        XCTAssertEqual(
            store.timelineSnapshot().matrices[0][0][0] ?? .nan,
            120.0 / 102.0,
            accuracy: 1e-12
        )
        XCTAssertEqual(
            store.allPositionsTimelineValues()[0],
            120.0 / 102.0,
            accuracy: 1e-12
        )
        let exported = store.exportCSV()
        XCTAssertTrue(exported.contains("occupancy_time_sec_min"))
        XCTAssertTrue(exported.contains("occupancy_time_sec_max"))

        store.xBins = 3
        store.smoothRadius = 1
        store.normalizeControls()
        let smoothedCenter = store.currentHeatmapPlot().matrix[0][1]
        XCTAssertEqual(smoothedCenter ?? .nan, 55.0 / 50.5, accuracy: 1e-12)
    }
}
