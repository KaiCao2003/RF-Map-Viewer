import CoreGraphics
import SwiftUI
import XCTest
@testable import RFMappingSwiftUI

@MainActor
final class PlotLayoutTests: XCTestCase {
    private let referenceAspect = CGFloat(singletonYReferenceColumns)
        / CGFloat(singletonYReferenceRows)

    private func makePlot(columns: Int, rows: Int) -> HeatmapPlot {
        HeatmapPlot(
            matrix: Array(
                repeating: Array(repeating: Double?.some(1), count: columns),
                count: rows
            ),
            xGroups: (0..<columns).map { AxisGroup(start: $0, end: $0) },
            yGroups: (0..<rows).map { AxisGroup(start: $0, end: $0) },
            low: 0,
            high: 1
        )
    }

    private func makeData(columns: Int, rows: Int, bins: Int = 3) throws -> RFMappingData {
        let unit = (0..<rows).map { yIndex in
            (0..<columns).map { xIndex in
                (0..<bins).map { binIndex in
                    Double(1 + yIndex + xIndex + binIndex)
                }
            }
        }
        let payload = currentRFSchemaPayload([
            "unitsSpikeCounts": [unit],
            "unitsSpikeCountsSize": [1, rows, columns, bins],
            "unitPool": [17],
            "xPositions": (0..<columns).map(Double.init),
            "yPositions": (0..<rows).map(Double.init),
            "timeBinEdges": (0...bins).map { Double($0) / 10.0 },
        ], occupancyTimeSec: Array(
            repeating: Array(repeating: 0.1, count: columns),
            count: rows
        ), occupancyTimeSecSize: [rows, columns])
        return try RFMappingData(
            data: JSONSerialization.data(withJSONObject: payload),
            url: URL(fileURLWithPath: "/tmp/singleton-y-layout.rfmap")
        )
    }

    func testSingletonYGridUsesThirtyBySevenTotalAspectAndRectangularInteraction() throws {
        let plot = makePlot(columns: 30, rows: 1)
        let layout = makeHeatmapLayout(
            size: CGSize(width: 600, height: 300),
            plot: plot,
            margins: EdgeInsets()
        )

        XCTAssertEqual(layout.gridWidth / layout.gridHeight, referenceAspect, accuracy: 1e-9)
        XCTAssertEqual(layout.cellHeight, layout.gridHeight, accuracy: 1e-9)
        XCTAssertGreaterThan(layout.cellHeight, layout.cellWidth)

        let expectedCell = CellRef(yStart: 0, yEnd: 0, xStart: 12, xEnd: 12)
        XCTAssertEqual(
            layout.cellRef(at: CGPoint(
                x: layout.x0 + 12.5 * layout.cellWidth,
                y: layout.y0 + 0.9 * layout.cellHeight
            )),
            expectedCell
        )
        let selectionRect = try XCTUnwrap(layout.rect(for: expectedCell))
        XCTAssertEqual(selectionRect.width, layout.cellWidth, accuracy: 1e-9)
        XCTAssertEqual(selectionRect.height, layout.cellHeight, accuracy: 1e-9)
        XCTAssertNil(layout.cellRef(at: CGPoint(
            x: layout.x0 + 12.5 * layout.cellWidth,
            y: layout.y0 + layout.gridHeight
        )))
    }

    func testMultirowGridRetainsSquareCells() {
        let dimensions = spatialGridDimensions(
            availableWidth: 200,
            availableHeight: 100,
            columns: 5,
            rows: 3,
            minimumCellWidth: 4
        )

        XCTAssertEqual(dimensions.cellWidth, dimensions.cellHeight, accuracy: 1e-9)
        XCTAssertEqual(dimensions.gridWidth, dimensions.cellWidth * 5, accuracy: 1e-9)
        XCTAssertEqual(dimensions.gridHeight, dimensions.cellHeight * 3, accuracy: 1e-9)
    }

    func testSingletonPolarRingSpansSevenVisualRowsForHitTestingAndSelection() throws {
        let data = try makeData(columns: 30, rows: 1)
        let store = RFMappingStore(
            initialData: data,
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false
        )
        let plot = makePlot(columns: 30, rows: 1)
        let layout = makePolarLayout(
            size: CGSize(width: 620, height: 420),
            store: store,
            plot: plot
        )

        XCTAssertEqual(layout.ringSpan, 7, accuracy: 1e-9)
        let pointInsideOuterPart = CGPoint(
            x: layout.center.x,
            y: layout.center.y - CGFloat(innerBlankRows + 6) * layout.scale
        )
        let hit = try XCTUnwrap(polarCell(at: pointInsideOuterPart, layout: layout))
        XCTAssertEqual(hit.ring, 0)
        XCTAssertEqual(hit.cell.yStart, 0)
        XCTAssertNotNil(polarPath(
            for: CellRef(yStart: 0, yEnd: 0, xStart: 0, xEnd: 0),
            layout: layout
        ))
        XCTAssertNil(polarCell(
            at: CGPoint(
                x: layout.center.x,
                y: layout.center.y - CGFloat(innerBlankRows + 8) * layout.scale
            ),
            layout: layout
        ))
    }

    func testMultirowPolarLayoutRetainsOneUnitRings() throws {
        let data = try makeData(columns: 5, rows: 3)
        let store = RFMappingStore(
            initialData: data,
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false
        )
        let layout = makePolarLayout(
            size: CGSize(width: 620, height: 420),
            store: store,
            plot: makePlot(columns: 5, rows: 3)
        )

        XCTAssertEqual(layout.ringSpan, 1, accuracy: 1e-9)
    }

    func testTimelineAndTimelineExportUseSingletonGeometryForBothFormats() throws {
        let data = try makeData(columns: 30, rows: 1, bins: 4)
        let store = RFMappingStore(
            initialData: data,
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false
        )
        store.spatialPlotFormat = .rectangular

        let rectangular = makeTimelineExportLayout(
            store: store,
            width: 800,
            height: 500
        )
        let rectangularMini = try XCTUnwrap(rectangular.miniLayouts.first)
        XCTAssertEqual(
            rectangularMini.gridWidth / rectangularMini.gridHeight,
            referenceAspect,
            accuracy: 1e-9
        )
        XCTAssertNotNil(rectangularMini.cellRef(at: CGPoint(
            x: rectangularMini.x0 + 0.5 * rectangularMini.cellWidth,
            y: rectangularMini.y0 + 0.9 * rectangularMini.cellHeight
        )))

        store.spatialPlotFormat = .polar
        let polar = makeTimelineExportLayout(
            store: store,
            width: 800,
            height: 500
        )
        let polarMini = try XCTUnwrap(polar.miniLayouts.first)
        let polarLayout = try XCTUnwrap(polarMini.polarLayout)
        XCTAssertEqual(polarLayout.ringSpan, 7, accuracy: 1e-9)
        XCTAssertNotNil(polarMini.cellRef(at: CGPoint(
            x: polarLayout.center.x,
            y: polarLayout.center.y - CGFloat(innerBlankRows + 6) * polarLayout.scale
        )))
    }
}
