import Foundation
import XCTest
@testable import RFMappingSwiftUI

final class PresentationExposureTests: XCTestCase {
    private func makeData(presentations: Any? = [[2, 10]]) throws -> RFMappingData {
        var payload = currentRFSchemaPayload([
            "unitsSpikeCounts": [[[[4, 8], [20, 40]]]], "unitsSpikeCountsSize": [1, 1, 2, 2],
            "unitPool": [17], "xPositions": [-1, 1], "yPositions": [0], "timeBinEdges": [0, 0.1, 0.3],
        ], occupancyTimeSec: [[7, 1]], occupancyTimeSecSize: [1, 2])
        if let presentations { payload["stimulusPresentationCounts"] = presentations }
        return try RFMappingData(data: JSONSerialization.data(withJSONObject: payload), url: URL(fileURLWithPath: "/tmp/exposure.rfmap"))
    }

    func testHzUsesPresentationCountAndEachActualResponseWindow() throws {
        let data = try makeData()
        XCTAssertEqual(try data.responseValue(unitIndex: 0, yIndex: 0, xIndex: 0, start: 0, end: 0,
                                              valueMode: .meanFiringRate)!, 20, accuracy: 1e-12)
        XCTAssertEqual(try data.responseValue(unitIndex: 0, yIndex: 0, xIndex: 0, start: 1, end: 1,
                                              valueMode: .meanFiringRate)!, 20, accuracy: 1e-12)
        let rates = try data.responseMatrix(unitIndex: 0, start: 0, end: 1, valueMode: .meanFiringRate)
        XCTAssertEqual(rates[0][0]!, 20, accuracy: 1e-12)
        XCTAssertEqual(rates[0][1]!, 20, accuracy: 1e-12)
        let observation = data.spatialObservations(unitIndex: 0, yGroup: AxisGroup(start: 0, end: 0),
            xGroup: AxisGroup(start: 0, end: 1), start: 0, end: 1)
        XCTAssertEqual(observation.stimulusPresentationCount, 12)
        XCTAssertEqual(observation.responseExposureSeconds!, 3.6, accuracy: 1e-12)
        XCTAssertEqual(observation.count, 72)
        XCTAssertEqual(observation.occupancyTimeSeconds, 8)
        let frames = data.spatialObservationFrames(unitIndex: 0,
            timeGroups: [AxisGroup(start: 0, end: 0), AxisGroup(start: 1, end: 1)],
            yGroups: [AxisGroup(start: 0, end: 0)], xGroups: [AxisGroup(start: 0, end: 1)])
        XCTAssertEqual(frames[0][0][0].responseExposureSeconds!, 1.2, accuracy: 1e-12)
        XCTAssertEqual(frames[1][0][0].responseExposureSeconds!, 2.4, accuracy: 1e-12)
        XCTAssertEqual(data.metrics(for: 0).bestX, 0)
    }

    func testFilesWithoutPresentationsRemainCountOnly() throws {
        let data = try makeData(presentations: nil)
        XCTAssertFalse(data.supportsMeanFiringRate)
        XCTAssertThrowsError(try data.responseMatrix(unitIndex: 0, start: 0, end: 1, valueMode: .meanFiringRate))
        XCTAssertEqual(try data.responseMatrix(unitIndex: 0, start: 0, end: 1, valueMode: .spikeCount), [[12, 60]])
        XCTAssertEqual(data.metrics(for: 0).bestX, 1)
        XCTAssertNil(data.metrics(for: 0).maxFiringRate)
    }

    func testPresentationCountsRequireNativeShapeAndNonnegativeIntegers() throws {
        let invalidValues: [Any] = [[[1]], [[-1, 2]], [[0.5, 2]], NSNull()]
        for invalid in invalidValues {
            XCTAssertThrowsError(try makeData(presentations: invalid))
        }
        let zero = try makeData(presentations: [[0, 10]])
        XCTAssertNil(try zero.responseValue(unitIndex: 0, yIndex: 0, xIndex: 0, start: 0, end: 1, valueMode: .meanFiringRate))
    }
}
