import XCTest
@testable import RFMappingSwiftUI

final class PlotRenderingTests: XCTestCase {
    func testNonnegativeResponseRangeUsesZeroBaselineAndCurrentPeak() {
        let range = nonnegativeResponseRange([
            [nil, 2.5, 0.0],
            [7.25, .nan, -0.5]
        ])

        XCTAssertEqual(range.low, 0.0, accuracy: 1e-12)
        XCTAssertEqual(range.high, 7.25, accuracy: 1e-12)
    }

    func testEmptyAndZeroResponseMapsKeepAnExactZeroRange() {
        let missing = nonnegativeResponseRange([[nil, .nan]])
        let zeros = nonnegativeResponseRange([[0.0, 0.0]])

        XCTAssertEqual(missing.low, 0.0, accuracy: 1e-12)
        XCTAssertEqual(missing.high, 0.0, accuracy: 1e-12)
        XCTAssertEqual(zeros.low, 0.0, accuracy: 1e-12)
        XCTAssertEqual(zeros.high, 0.0, accuracy: 1e-12)
    }

    func testMissingEncodingIsDistinctFromZeroAndPeak() {
        XCTAssertEqual(spatialSampleEncoding(nil, low: 0, high: 8), .missing)
        XCTAssertEqual(spatialSampleEncoding(.nan, low: 0, high: 8), .missing)
        XCTAssertEqual(spatialSampleEncoding(0, low: 0, high: 8), .normalized(0))
        XCTAssertEqual(spatialSampleEncoding(8, low: 0, high: 8), .normalized(1))
    }

    func testAccessibleResponseLabelsDoNotDescribeMissingAsZero() {
        let missing = responseValueAccessibilityDescription(nil, mode: .meanFiringRate)
        let zero = responseValueAccessibilityDescription(0, mode: .meanFiringRate)

        XCTAssertEqual(missing, "Missing value; no stimulus presentations")
        XCTAssertEqual(zero, "0 Hz")
        XCTAssertNotEqual(missing, zero)
    }

    func testSpikeCountMissingLabelUsesExposureProvenanceWhenAvailable() {
        XCTAssertEqual(
            responseValueAccessibilityDescription(
                nil,
                mode: .spikeCount,
                hasPresentationMetadata: true
            ),
            "Missing value; no stimulus presentations"
        )
        XCTAssertEqual(
            responseMissingLegendLabel(
                .spikeCount,
                hasPresentationMetadata: true
            ),
            "No presentations"
        )
    }
}
