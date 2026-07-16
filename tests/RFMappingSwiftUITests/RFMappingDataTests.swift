import Foundation
import XCTest
@testable import RFMappingSwiftUI

final class RFMappingDataTests: XCTestCase {
    func testExactEdgesDriveCountPerPresentationAndFiringRate() throws {
        let subject = try load(basePayload())

        XCTAssertEqual(
            try XCTUnwrap(subject.responseValue(
                unitIndex: 0,
                yIndex: 0,
                xIndex: 0,
                start: 0,
                end: 0,
                valueMode: .spikeCount
            )),
            10.0,
            accuracy: 1e-12
        )
        XCTAssertEqual(
            try XCTUnwrap(subject.responseValue(
                unitIndex: 0,
                yIndex: 0,
                xIndex: 0,
                start: 0,
                end: 1,
                valueMode: .spikesPerPresentation
            )),
            3.0,
            accuracy: 1e-12
        )
        XCTAssertEqual(
            try XCTUnwrap(subject.responseValue(
                unitIndex: 0,
                yIndex: 0,
                xIndex: 0,
                start: 0,
                end: 0,
                valueMode: .meanFiringRate
            )),
            10.0,
            accuracy: 1e-12
        )
        XCTAssertEqual(
            try XCTUnwrap(subject.responseValue(
                unitIndex: 0,
                yIndex: 0,
                xIndex: 0,
                start: 1,
                end: 1,
                valueMode: .meanFiringRate
            )),
            40.0,
            accuracy: 1e-12
        )
        XCTAssertEqual(
            try XCTUnwrap(subject.responseValue(
                unitIndex: 0,
                yIndex: 0,
                xIndex: 0,
                start: 0,
                end: 1,
                valueMode: .meanFiringRate
            )),
            20.0,
            accuracy: 1e-12
        )
        XCTAssertEqual(subject.timeSpanSeconds(start: 0, end: 1), 0.15, accuracy: 1e-12)

        let countMatrix = try subject.responseMatrix(
            unitIndex: 0,
            start: 0,
            end: 1,
            valueMode: .spikeCount
        )
        XCTAssertEqual(try XCTUnwrap(countMatrix[0][0]), 30.0, accuracy: 1e-12)
        XCTAssertEqual(try XCTUnwrap(countMatrix[0][1]), 15.0, accuracy: 1e-12)

        let perPresentationMatrix = try subject.responseMatrix(
            unitIndex: 0,
            start: 0,
            end: 1,
            valueMode: .spikesPerPresentation
        )
        XCTAssertEqual(try XCTUnwrap(perPresentationMatrix[0][0]), 3.0, accuracy: 1e-12)
        XCTAssertEqual(try XCTUnwrap(perPresentationMatrix[0][1]), 3.0, accuracy: 1e-12)

        let rateMatrix = try subject.responseMatrix(
            unitIndex: 0,
            start: 0,
            end: 1,
            valueMode: .meanFiringRate
        )
        XCTAssertEqual(try XCTUnwrap(rateMatrix[0][0]), 20.0, accuracy: 1e-12)
        XCTAssertEqual(try XCTUnwrap(rateMatrix[0][1]), 20.0, accuracy: 1e-12)
    }

    func testReversedRangesAreNormalizedWithoutDroppingBins() throws {
        let subject = try load(basePayload())

        let forward = try XCTUnwrap(subject.responseValue(
            unitIndex: 0,
            yIndex: 0,
            xIndex: 0,
            start: 0,
            end: 2,
            valueMode: .meanFiringRate
        ))
        let reversed = try XCTUnwrap(subject.responseValue(
            unitIndex: 0,
            yIndex: 0,
            xIndex: 0,
            start: 2,
            end: 0,
            valueMode: .meanFiringRate
        ))

        XCTAssertEqual(reversed, forward, accuracy: 1e-12)
        XCTAssertEqual(subject.timeSpanSeconds(start: 2, end: 0), 0.3, accuracy: 1e-12)
        XCTAssertEqual(subject.countMatrix(unitIndex: 0, start: 2, end: 0), [[60.0, 30.0]])
    }

    func testPrefixRangesPreserveSmallCountsAfterLargeEarlierBins() throws {
        let payload: [String: Any] = [
            "unitsSpikeCounts": [[[[1e16, 1.0, 1.0, 0.0], [1e32, 1e16, 1e16, 3.0]]]],
            "unitsSpikeCountsSize": [1, 1, 2, 4],
            "unitPool": [42],
            "xPositions": [0.0, 1.0],
            "yPositions": [0.0],
            "timeBinEdges": [0.0, 0.1, 0.2, 0.3, 0.4]
        ]
        let subject = try load(payload)

        XCTAssertEqual(
            subject.rangeCount(unitIndex: 0, yIndex: 0, xIndex: 0, start: 1, end: 2),
            2.0
        )
        XCTAssertEqual(
            subject.rangeCount(unitIndex: 0, yIndex: 0, xIndex: 1, start: 3, end: 3),
            3.0
        )
        XCTAssertEqual(subject.countMatrix(unitIndex: 0, start: 3, end: 3), [[0.0, 3.0]])
        let matrix = try subject.responseMatrix(
            unitIndex: 0,
            start: 3,
            end: 3,
            valueMode: .spikeCount
        )
        XCTAssertEqual(matrix, [[0.0, 3.0]])
    }

    func testLegacyPayloadIsCountOnlyAndGatesNormalizedModes() throws {
        let subject = try load(basePayload(withPresentations: false))

        XCTAssertFalse(subject.hasPresentationCounts)
        XCTAssertTrue(subject.supports(.spikeCount))
        XCTAssertFalse(subject.supports(.spikesPerPresentation))
        XCTAssertFalse(subject.supports(.meanFiringRate))
        XCTAssertEqual(
            try XCTUnwrap(subject.responseValue(
                unitIndex: 0,
                yIndex: 0,
                xIndex: 0,
                start: 0,
                end: 0,
                valueMode: .spikeCount
            )),
            10.0
        )

        XCTAssertThrowsError(
            try subject.responseMatrix(
                unitIndex: 0,
                start: 0,
                end: 0,
                valueMode: .meanFiringRate
            )
        ) { error in
            guard
                let mappingError = error as? RFMappingError,
                case .presentationCountsRequired(let mode) = mappingError
            else {
                return XCTFail("Expected presentationCountsRequired, got \(error)")
            }
            XCTAssertEqual(mode, .meanFiringRate)
        }
    }

    func testExplicitNullPresentationMetadataIsCountOnly() throws {
        var payload = basePayload(withPresentations: false)
        payload["stimulusPresentationCounts"] = NSNull()
        let subject = try load(payload)

        XCTAssertFalse(subject.hasPresentationCounts)
        XCTAssertTrue(subject.supports(.spikeCount))
        XCTAssertFalse(subject.supports(.meanFiringRate))
    }

    func testZeroPresentationsWithZeroCountsProducesNoData() throws {
        var payload = basePayload()
        payload["unitsSpikeCounts"] = [[[[10.0, 20.0, 30.0], [0.0, 0.0, 0.0]]]]
        payload["stimulusPresentationCounts"] = [[10.0, 0.0]]
        let subject = try load(payload)

        XCTAssertNil(try subject.responseValue(
            unitIndex: 0,
            yIndex: 0,
            xIndex: 1,
            start: 0,
            end: 2,
            valueMode: .spikesPerPresentation
        ))
        XCTAssertNil(try subject.responseValue(
            unitIndex: 0,
            yIndex: 0,
            xIndex: 1,
            start: 0,
            end: 2,
            valueMode: .meanFiringRate
        ))
        let matrix = try subject.responseMatrix(
            unitIndex: 0,
            start: 0,
            end: 2,
            valueMode: .meanFiringRate
        )
        XCTAssertNil(matrix[0][1])
    }

    func testZeroPresentationsWithNonzeroCountsIsRejected() throws {
        var payload = basePayload()
        payload["stimulusPresentationCounts"] = [[0.0, 5.0]]

        assertInvalid(payload, contains: "zero where spike counts are nonzero")
    }

    func testMATLABSingletonPresentationShapesAreRestored() throws {
        var flatRow = basePayload()
        flatRow["stimulusPresentationCounts"] = [10.0, 5.0]
        XCTAssertEqual(try load(flatRow).presentationCounts, [[10.0, 5.0]])

        let flatColumn: [String: Any] = [
            "unitsSpikeCounts": [[[[1.0, 2.0]], [[3.0, 4.0]]]],
            "unitsSpikeCountsSize": [1, 2, 1, 2],
            "unitPool": [7],
            "xPositions": [0.0],
            "yPositions": [-1.0, 1.0],
            "timeBinEdges": [0.0, 0.1, 0.2],
            "stimulusPresentationCounts": [2.0, 4.0]
        ]
        XCTAssertEqual(try load(flatColumn).presentationCounts, [[2.0], [4.0]])

        let scalar: [String: Any] = [
            "unitsSpikeCounts": [[[[1.0, 2.0]]]],
            "unitsSpikeCountsSize": [1, 1, 1, 2],
            "unitPool": [11],
            "xPositions": [0.0],
            "yPositions": [0.0],
            "timeBinEdges": [0.0, 0.1, 0.2],
            "stimulusPresentationCounts": 3.0
        ]
        XCTAssertEqual(try load(scalar).presentationCounts, [[3.0]])
    }

    func testInvalidDimensionsAreRejected() throws {
        var wrongSizeArity = basePayload()
        wrongSizeArity["unitsSpikeCountsSize"] = [1, 1, 2]
        assertInvalid(wrongSizeArity, contains: "4 values")

        var nonpositiveSize = basePayload()
        nonpositiveSize["unitsSpikeCountsSize"] = [1, 1, 2, 0]
        assertInvalid(nonpositiveSize, contains: "positive")

        var wrongUnitCount = basePayload()
        wrongUnitCount["unitsSpikeCountsSize"] = [2, 1, 2, 3]
        assertInvalid(wrongUnitCount, contains: "first dimension")

        var wrongUnitPool = basePayload()
        wrongUnitPool["unitPool"] = [42, 43]
        assertInvalid(wrongUnitPool, contains: "unitPool length")

        var wrongXPositions = basePayload()
        wrongXPositions["xPositions"] = [-1.0]
        assertInvalid(wrongXPositions, contains: "xPositions")

        var wrongYPositions = basePayload()
        wrongYPositions["yPositions"] = [0.0, 1.0]
        assertInvalid(wrongYPositions, contains: "yPositions")

        var wrongEdgeCount = basePayload()
        wrongEdgeCount["timeBinEdges"] = [-0.1, 0.0, 0.05]
        assertInvalid(wrongEdgeCount, contains: "nBins + 1")

        var wrongBinCount = basePayload()
        wrongBinCount["unitsSpikeCounts"] = [[[[10.0, 20.0], [5.0, 10.0, 15.0]]]]
        assertInvalid(wrongBinCount, contains: "wrong bin dimension")

        var invalidPresentationShape = basePayload()
        invalidPresentationShape["stimulusPresentationCounts"] = [[10.0]]
        assertInvalid(invalidPresentationShape, contains: "x dimension")

        let invalidFlatPresentation: [String: Any] = [
            "unitsSpikeCounts": [[
                [[1.0], [2.0]],
                [[3.0], [4.0]]
            ]],
            "unitsSpikeCountsSize": [1, 2, 2, 1],
            "unitPool": [1],
            "xPositions": [-1.0, 1.0],
            "yPositions": [-1.0, 1.0],
            "timeBinEdges": [0.0, 0.1],
            "stimulusPresentationCounts": [1.0, 1.0]
        ]
        assertInvalid(invalidFlatPresentation, contains: "singleton dimensions")
    }

    func testInvalidEdgesAndNegativeValuesAreRejected() throws {
        var repeatedEdges = basePayload()
        repeatedEdges["timeBinEdges"] = [-0.1, 0.0, 0.0, 0.2]
        assertInvalid(repeatedEdges, contains: "strictly increasing")

        var decreasingEdges = basePayload()
        decreasingEdges["timeBinEdges"] = [-0.1, 0.05, 0.0, 0.2]
        assertInvalid(decreasingEdges, contains: "strictly increasing")

        var negativeCount = basePayload()
        negativeCount["unitsSpikeCounts"] = [[[[10.0, -1.0, 30.0], [5.0, 10.0, 15.0]]]]
        assertInvalid(negativeCount, contains: "finite and non-negative")

        var negativePresentations = basePayload()
        negativePresentations["stimulusPresentationCounts"] = [[-1.0, 5.0]]
        assertInvalid(negativePresentations, contains: "non-negative integers")

        var fractionalPresentations = basePayload()
        fractionalPresentations["stimulusPresentationCounts"] = [[10.5, 5.0]]
        assertInvalid(fractionalPresentations, contains: "non-negative integers")
    }

    func testOutOfRangeJSONNumberIsRejectedAsNonfinite() throws {
        let json = """
        {
          "unitsSpikeCounts": [[[[1e999]]]],
          "unitsSpikeCountsSize": [1, 1, 1, 1],
          "unitPool": [1],
          "xPositions": [0],
          "yPositions": [0],
          "timeBinEdges": [0, 0.1]
        }
        """

        XCTAssertThrowsError(try load(rawJSON: json))
    }

    func testPeakTieUsesEarliestBinAndItsCenter() throws {
        let payload: [String: Any] = [
            "unitsSpikeCounts": [[[[3.0, 7.0, 7.0]]]],
            "unitsSpikeCountsSize": [1, 1, 1, 3],
            "unitPool": [9],
            "xPositions": [0.0],
            "yPositions": [0.0],
            "timeBinEdges": [0.0, 0.1, 0.2, 0.4]
        ]
        let metrics = try load(payload).metrics(for: 0)

        XCTAssertEqual(metrics.peak[0][0], 7.0)
        XCTAssertEqual(metrics.peakBin[0][0], 1)
        XCTAssertEqual(try XCTUnwrap(metrics.delayMS[0][0]), 150.0, accuracy: 1e-12)
    }

    private func basePayload(withPresentations: Bool = true) -> [String: Any] {
        var payload: [String: Any] = [
            "unitsSpikeCounts": [[[[10.0, 20.0, 30.0], [5.0, 10.0, 15.0]]]],
            "unitsSpikeCountsSize": [1, 1, 2, 3],
            "unitPool": [42],
            "xPositions": [-1.0, 1.0],
            "yPositions": [0.0],
            "timeBinEdges": [-0.1, 0.0, 0.05, 0.2]
        ]
        if withPresentations {
            payload["stimulusPresentationCounts"] = [[10.0, 5.0]]
        }
        return payload
    }

    private func load(_ payload: [String: Any]) throws -> RFMappingData {
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
        return try load(data: data)
    }

    private func load(rawJSON: String) throws -> RFMappingData {
        try load(data: Data(rawJSON.utf8))
    }

    private func load(data: Data) throws -> RFMappingData {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("RFMappingDataTests-\(UUID().uuidString)")
            .appendingPathExtension("json")
        try data.write(to: url, options: .atomic)
        defer { try? FileManager.default.removeItem(at: url) }
        return try RFMappingData(url: url)
    }

    private func assertInvalid(
        _ payload: [String: Any],
        contains expectedText: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertThrowsError(try load(payload), file: file, line: line) { error in
            guard
                let mappingError = error as? RFMappingError,
                case .invalidData(let message) = mappingError
            else {
                return XCTFail("Expected invalidData, got \(error)", file: file, line: line)
            }
            XCTAssertTrue(
                message.contains(expectedText),
                "Expected error containing '\(expectedText)', got '\(message)'",
                file: file,
                line: line
            )
        }
    }
}
