import CoreGraphics
import XCTest
@testable import RFMappingSwiftUI

final class TimelineGeometryTests: XCTestCase {
    func testNonuniformBinCentersUsePhysicalTimeGeometry() {
        let rect = CGRect(x: 10, y: 20, width: 300, height: 60)
        let points = timelineChartPoints(
            values: [1, 2, 3],
            centerTimesMS: [-75, 0, 125],
            axisRangeMS: (-100, 200),
            high: 3,
            rect: rect
        )

        XCTAssertEqual(points.map(\.x), [35, 110, 235])
        XCTAssertNotEqual(points.map(\.x), [60, 160, 260])
    }

    func testTimelinePointsStayInsideMeasuredNonnegativeRange() {
        let rect = CGRect(x: 0, y: 10, width: 100, height: 40)
        let points = timelineChartPoints(
            values: [-2, 5, 12, .nan],
            centerTimesMS: [0, 1, 2, 3],
            axisRangeMS: (0, 3),
            high: 10,
            rect: rect
        )

        XCTAssertEqual(points.map(\.y), [50, 30, 10, 50])
        XCTAssertTrue(points.allSatisfy { rect.minY <= $0.y && $0.y <= rect.maxY })
    }

    func testOverlaidResponseTracesShareOneYScale() {
        let rect = CGRect(x: 0, y: 10, width: 100, height: 40)
        let high = timelineResponseHigh(
            allPositionValues: [10, 5],
            selectedPositionValues: [2, 2]
        )
        let allPositionPoint = timelineChartPoints(
            values: [2],
            centerTimesMS: [0.5],
            axisRangeMS: (0, 1),
            high: high,
            rect: rect
        )
        let selectedPoint = timelineChartPoints(
            values: [2],
            centerTimesMS: [0.5],
            axisRangeMS: (0, 1),
            high: high,
            rect: rect
        )

        XCTAssertEqual(high, 10)
        XCTAssertEqual(allPositionPoint, selectedPoint)
        XCTAssertEqual(selectedPoint.first?.y, 42)
        XCTAssertGreaterThan(selectedPoint[0].y, rect.minY)
        XCTAssertEqual(
            timelineResponseHigh(allPositionValues: [3], selectedPositionValues: [12]),
            12
        )
    }

    func testTimelineHitTestingUsesPhysicalIntervalEnds() {
        let ends = [-50.0, 50.0, 200.0]

        XCTAssertEqual(timelineBinIndex(timeMS: -75, endBoundsMS: ends), 0)
        XCTAssertEqual(timelineBinIndex(timeMS: -50, endBoundsMS: ends), 1)
        XCTAssertEqual(timelineBinIndex(timeMS: 125, endBoundsMS: ends), 2)
        XCTAssertEqual(timelineBinIndex(timeMS: 200, endBoundsMS: ends), 2)
    }
}
