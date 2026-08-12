import Foundation
import XCTest
@testable import RFMappingSwiftUI

final class RFMapTests: XCTestCase {
    private let sourceURL = URL(fileURLWithPath: "/tmp/rf-map-fixture.json")

    private func makeMap(
        unitIndex: Int = 0,
        unitID: Int = 11,
        counts: [[[Double]]] = [
            [[1, 2, 4], [3, 5, 2]],
        ],
        edges: [Double] = [-0.1, 0.0, 0.1, 0.2],
        metadata: [String: RFMapJSONValue] = [
            "session": .string("example"),
            "VSTimeWindow": .array([.number(-0.1), .number(0.2)]),
            "timeWindowMs": .array([.number(-100), .number(200)]),
            "timeBinWidthMs": .number(100),
        ]
    ) throws -> RFMap {
        try RFMap(
            unitIndex: unitIndex,
            unitID: unitID,
            spikeCounts: counts,
            xPositions: [10, 20],
            yPositions: [30],
            timeBinEdgesSeconds: edges,
            presentationCounts: [[4, 4]],
            metadata: metadata,
            sourceURL: sourceURL
        )
    }

    func testRFMapListUsesExplicitOriginalIndexAndUnitIDLookups() throws {
        let first = try makeMap(unitIndex: 0, unitID: 1)
        let second = try makeMap(unitIndex: 1, unitID: 0)
        let maps = try RFMapList([first, second])

        XCTAssertEqual(maps[0].unitID, 1, "Integer subscripting remains positional")
        XCTAssertEqual(try maps.byOriginalIndex(0).unitID, 1)
        XCTAssertEqual(try maps.byUnitID(0).unitIndex, 1)
        XCTAssertThrowsError(try maps.byOriginalIndex(9))
        XCTAssertThrowsError(try maps.byUnitID(9))
    }

    func testSumBetweenSecondsIsHalfOpenAndKeepsSingletonTimeAxis() throws {
        let result = try makeMap().sumBetweenSeconds(0.0, 0.2)

        XCTAssertEqual(result.spikeCounts, [[[6], [7]]])
        XCTAssertEqual(result.timeBinEdgesSeconds, [0.0, 0.2])
        XCTAssertEqual(result.nTimeBins, 1)
        XCTAssertEqual(result.unitIndex, 0)
        XCTAssertEqual(result.unitID, 11)
        XCTAssertEqual(result.presentationCounts, [[4, 4]])
        XCTAssertEqual(result.metadata["session"], RFMapJSONValue.string("example"))
        XCTAssertEqual(
            result.metadata["VSTimeWindow"],
            RFMapJSONValue.array([.number(0.0), .number(0.2)])
        )
        XCTAssertEqual(
            result.metadata["timeWindowMs"],
            RFMapJSONValue.array([.number(0.0), .number(200.0)])
        )
        XCTAssertEqual(result.metadata["timeBinWidthMs"], RFMapJSONValue.number(200.0))
    }

    func testEqualEndpointsReturnOneZeroBin() throws {
        let result = try makeMap().sumBetweenSeconds(0.2, 0.2)

        XCTAssertEqual(result.spikeCounts, [[[0], [0]]])
        XCTAssertEqual(result.timeBinEdgesSeconds, [0.2, 0.2])
        XCTAssertEqual(result.timeBinWidthsSeconds, [0.0])
    }

    func testEdgeToleranceIsAbsoluteAndDoesNotClampOrReorder() throws {
        let map = try makeMap()
        let tolerated = try map.sumBetweenSeconds(0.0, 0.20000000000000004)
        XCTAssertEqual(tolerated.timeBinEdgesSeconds, [0.0, 0.2])

        XCTAssertThrowsError(try map.sumBetweenSeconds(0.2, 0.0)) { error in
            guard let rfMapError = error as? RFMapError,
                  case .invalidTimeRange(let message) = rfMapError else {
                return XCTFail("Expected invalidTimeRange, received \(error)")
            }
            XCTAssertTrue(message.contains("laterSeconds must be >= earlierSeconds"))
            XCTAssertTrue(message.contains("Available time bin edges"))
        }
        XCTAssertThrowsError(try map.sumBetweenSeconds(0.0, 0.15)) { error in
            XCTAssertTrue(error.localizedDescription.contains("Available time bin edges"))
        }
    }

    func testEdgeWithinToleranceOfTwoEdgesIsRejected() throws {
        let map = try RFMap(
            unitIndex: 0,
            unitID: 1,
            spikeCounts: [[[1, 2]]],
            xPositions: [0],
            yPositions: [0],
            timeBinEdgesSeconds: [0, 1.5e-12, 1],
            presentationCounts: nil,
            sourceURL: sourceURL
        )

        XCTAssertThrowsError(try map.sumBetweenSeconds(0.75e-12, 1)) { error in
            XCTAssertTrue(error.localizedDescription.contains("multiple timeBinEdges"))
        }
    }

    func testDetectBumpsUsesGlobalBaselineMeanStrictComparisonAndFullMask() throws {
        let result = try makeMap().detectBumps(
            thresholdRatio: 1.5,
            baselineStartSeconds: -0.1,
            baselineEndSeconds: 0.0
        )

        XCTAssertEqual(result.baselineMean, 2.0)
        XCTAssertEqual(result.threshold, 3.0)
        XCTAssertEqual(result.mask, [[[0, 0, 1], [0, 1, 0]]])
        XCTAssertNil(result.warning, "A value equal to threshold is not a bump")

        let warningResult = try makeMap().detectBumps(
            thresholdRatio: 1.2,
            baselineStartSeconds: -0.1,
            baselineEndSeconds: 0.0
        )
        XCTAssertEqual(warningResult.mask.count, 1)
        XCTAssertEqual(warningResult.mask[0].count, 2)
        XCTAssertEqual(warningResult.mask[0][0].count, 3)
        XCTAssertNotNil(warningResult.warning)
    }

    func testDetectBumpsRejectsInvalidRatioAndEmptyBaseline() throws {
        let map = try makeMap()
        XCTAssertThrowsError(try map.detectBumps(thresholdRatio: 1.0))
        XCTAssertThrowsError(
            try map.detectBumps(
                thresholdRatio: 1.2,
                baselineStartSeconds: 0,
                baselineEndSeconds: 0
            )
        )
    }

    func testDetectSpatialBumpsUsesTwoDimensionalMaximumPerTimeBin() throws {
        let response: [[Double]] = [
            [1, 2, 1],
            [2, 9, 8],
            [1, 8, 7],
        ]
        let counts = response.map { row in row.map { [0, $0] } }
        let map = try RFMap(
            unitIndex: 0,
            unitID: 501,
            spikeCounts: counts,
            xPositions: [-10, 0, 10],
            yPositions: [-10, 0, 10],
            timeBinEdgesSeconds: [-0.1, 0.0, 0.1],
            presentationCounts: Array(repeating: Array(repeating: 1, count: 3), count: 3),
            sourceURL: sourceURL
        )

        let detection = try map.detectSpatialBumps(spatialSize: 3)
        var expected = Array(
            repeating: Array(repeating: [UInt8(0), UInt8(0)], count: 3),
            count: 3
        )
        expected[1][1][1] = 1
        XCTAssertEqual(detection.mask, expected)
        XCTAssertEqual(detection.baselineMean, 0)
        XCTAssertEqual(detection.threshold, 0)
        XCTAssertNil(detection.warning)

        let anisotropic = try map.detectSpatialBumps(spatialSize: (y: 1, x: 3))
        XCTAssertEqual(anisotropic.mask[0][1][1], 1)
        XCTAssertEqual(anisotropic.mask[1][1][1], 1)
        XCTAssertEqual(anisotropic.mask[2][1][1], 1)
        XCTAssertEqual(anisotropic.mask[1][2][1], 0)
    }

    func testDetectSpatialBumpsRetainsPlateausAndBaselineWarning() throws {
        let map = try RFMap(
            unitIndex: 0,
            unitID: 77,
            spikeCounts: [[[5, 9], [5, 9]]],
            xPositions: [0, 1],
            yPositions: [0],
            timeBinEdgesSeconds: [-0.1, 0.0, 0.1],
            presentationCounts: [[1, 1]],
            sourceURL: sourceURL
        )

        let detection = try map.detectSpatialBumps(
            thresholdRatio: 1.2,
            spatialSize: 3
        )
        XCTAssertEqual(detection.mask, [[[0, 1], [0, 1]]])
        XCTAssertEqual(detection.baselineMean, 5)
        XCTAssertEqual(detection.threshold, 6)
        XCTAssertNil(detection.warning)

        let warningMap = try RFMap(
            unitIndex: 0,
            unitID: 78,
            spikeCounts: [[[1, 3], [3, 3]]],
            xPositions: [0, 1],
            yPositions: [0],
            timeBinEdgesSeconds: [-0.1, 0.0, 0.1],
            presentationCounts: [[1, 1]],
            sourceURL: sourceURL
        )
        let warning = try warningMap.detectSpatialBumps(spatialSize: 3)
        XCTAssertNotNil(warning.warning)
        XCTAssertEqual(warning.mask.count, warningMap.nY)
        XCTAssertEqual(warning.mask[0].count, warningMap.nX)
        XCTAssertEqual(warning.mask[0][0].count, warningMap.nTimeBins)
    }

    func testDetectSpatialBumpsRejectsNonPositiveOrEvenWindowDimensions() throws {
        let map = try makeMap()
        XCTAssertThrowsError(try map.detectSpatialBumps(spatialSize: 0))
        XCTAssertThrowsError(try map.detectSpatialBumps(spatialSize: 2))
        XCTAssertThrowsError(try map.detectSpatialBumps(spatialSize: -1))
        XCTAssertThrowsError(try map.detectSpatialBumps(spatialSize: (y: 3, x: 2)))
    }

    func testRFMappingDataExposesPerUnitMapsAndPreservesMetadata() throws {
        let object: [String: Any] = [
            "unitsSpikeCounts": [
                [[[1.0, 2.0]]],
                [[[3.0, 4.0]]],
            ],
            "unitsSpikeCountsSize": [2, 1, 1, 2],
            "unitPool": [9, 4],
            "xPositions": [10.0],
            "yPositions": [20.0],
            "timeBinEdges": [0.0, 0.1, 0.2],
            "stimulusPresentationCounts": [[2]],
            "sessionName": "preserved",
            "nested": ["enabled": true],
        ]
        let json = try JSONSerialization.data(withJSONObject: object)
        let data = try RFMappingData(data: json, url: sourceURL)

        XCTAssertEqual(data.rfMaps.count, 2)
        XCTAssertEqual(try data.rfMap(byOriginalIndex: 1).unitID, 4)
        XCTAssertEqual(try data.rfMap(byUnitID: 9).unitIndex, 0)
        XCTAssertEqual(data.unitIndex(forUnitID: 4), 1)
        XCTAssertEqual(data.metadata["sessionName"], RFMapJSONValue.string("preserved"))
        XCTAssertEqual(
            data.metadata["nested"],
            RFMapJSONValue.object(["enabled": .bool(true)])
        )
        XCTAssertNil(data.metadata["unitsSpikeCounts"])
        XCTAssertEqual(data.sourceSHA256.count, 64)
        XCTAssertEqual(data.sourceByteCount, json.count)
    }
}
