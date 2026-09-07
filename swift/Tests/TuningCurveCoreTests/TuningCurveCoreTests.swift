import Foundation
import XCTest
@testable import TuningCurveCore

final class TuningCurveCoreTests: XCTestCase {
    private func makeData(
        unitIDs: [Int] = [11],
        countsByUnit: [[Double]]? = nil,
        occupancy: [Double]? = nil
    ) throws -> HDTuningData {
        let occupancy = occupancy ?? Array(repeating: 1.0, count: HDTuningData.rawBinCount)
        let counts = countsByUnit ?? unitIDs.map { _ in
            Array(repeating: 2.0, count: HDTuningData.rawBinCount)
        }
        let rates: [[Any]] = counts.map { row in
            row.enumerated().map { index, count -> Any in
                if occupancy[index] > 0 {
                    return count / occupancy[index]
                }
                return NSNull()
            }
        }
        let payload: [String: Any] = [
            "metadata": ["source": "unit-test"],
            "angle_bin_edges_deg": (0...HDTuningData.rawBinCount).map { Double($0) * 2 },
            "occupancy_time_s": occupancy,
            "unit_id": unitIDs,
            "spike_counts": counts,
            "firing_rate_hz": rates,
            "unit_data": ["hd_class": unitIDs.map { _ in 1 }],
        ]
        return try HDTuningData(
            data: JSONSerialization.data(withJSONObject: payload),
            sourceURL: URL(fileURLWithPath: "/tmp/test.tc")
        )
    }

    func testThirtyBinReductionPoolsCountsAndOccupancy() throws {
        var counts = Array(repeating: 0.0, count: HDTuningData.rawBinCount)
        for index in 0..<6 { counts[index] = 12 }
        let data = try makeData(countsByUnit: [counts])

        let curve = try data.processedCurve(
            unitID: 11,
            displayBins: 30,
            smoothing: false
        )

        XCTAssertEqual(curve.anglesDegrees.count, 30)
        XCTAssertEqual(curve.anglesDegrees.first, 6)
        XCTAssertEqual(curve.ratesHz.first, 12)
        XCTAssertTrue(curve.ratesHz.dropFirst().allSatisfy { $0 == 0 })
    }

    func testCircularSmoothingKeepsAConstantRateConstant() throws {
        let data = try makeData()

        let curve = try data.processedCurve(
            unitID: 11,
            displayBins: 45,
            smoothing: true,
            sigma: 2.25
        )

        XCTAssertEqual(curve.ratesHz.count, 45)
        for rate in curve.ratesHz {
            XCTAssertEqual(rate, 2, accuracy: 1e-12)
        }
    }

    func testPositiveCalibrationRotatesClockwiseAndSortsWrappedAngles() {
        let curve = ProcessedHDCurve(
            anglesDegrees: [6, 18, 354],
            ratesHz: [1, 2, 3]
        )

        let calibrated = curve.applyingAngleOffset(80)

        XCTAssertEqual(calibrated.anglesDegrees, [74, 86, 98])
        XCTAssertEqual(calibrated.ratesHz, [3, 1, 2])
    }

    func testNegativeCalibrationWrapsIntoZeroToThreeSixty() {
        let curve = ProcessedHDCurve(
            anglesDegrees: [10, 120],
            ratesHz: [4, 8]
        )

        let calibrated = curve.applyingAngleOffset(-30)

        XCTAssertEqual(calibrated.anglesDegrees, [90, 340])
        XCTAssertEqual(calibrated.ratesHz, [8, 4])
    }

    func testDisplayBinChoicesAlwaysDivideRawBins() {
        XCTAssertTrue(HDTuningData.validDisplayBinCounts.contains(30))
        XCTAssertTrue(HDTuningData.validDisplayBinCounts.contains(180))
        XCTAssertFalse(HDTuningData.validDisplayBinCounts.contains(31))
        XCTAssertEqual(HDTuningData.normalizedDisplayBinCount(31), 30)
    }
}
