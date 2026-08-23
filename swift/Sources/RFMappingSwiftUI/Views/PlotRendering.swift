import SwiftUI

let singletonYReferenceColumns = 30
let singletonYReferenceRows = 7

struct SpatialGridDimensions: Equatable {
    let cellWidth: CGFloat
    let cellHeight: CGFloat
    let gridWidth: CGFloat
    let gridHeight: CGFloat
}

/// Fits a spatial grid while preserving the legacy 30-column by 7-row visual
/// proportion for data that scientifically contains only one y row.
func spatialGridDimensions(
    availableWidth: CGFloat,
    availableHeight: CGFloat,
    columns: Int,
    rows: Int,
    minimumCellWidth: CGFloat = 0
) -> SpatialGridDimensions {
    let columns = max(1, columns)
    let rows = max(1, rows)
    let width = max(0, availableWidth)
    let height = max(0, availableHeight)
    if rows == 1 {
        let aspect = CGFloat(singletonYReferenceColumns) / CGFloat(singletonYReferenceRows)
        var gridWidth = min(width, height * aspect)
        let cellWidth = max(minimumCellWidth, gridWidth / CGFloat(columns))
        gridWidth = cellWidth * CGFloat(columns)
        let gridHeight = gridWidth / aspect
        return SpatialGridDimensions(
            cellWidth: cellWidth,
            cellHeight: gridHeight,
            gridWidth: gridWidth,
            gridHeight: gridHeight
        )
    }

    let cell = max(
        minimumCellWidth,
        min(width / CGFloat(columns), height / CGFloat(rows))
    )
    return SpatialGridDimensions(
        cellWidth: cell,
        cellHeight: cell,
        gridWidth: cell * CGFloat(columns),
        gridHeight: cell * CGFloat(rows)
    )
}

/// Returns the visual radial width assigned to one scientific y row.
func polarRingSpan(rowCount: Int) -> CGFloat {
    CGFloat(rowCount == 1 ? singletonYReferenceRows : 1)
}

struct HeatmapPlot {
    let matrix: OptionalMatrix
    let xGroups: [AxisGroup]
    let yGroups: [AxisGroup]
    let low: Double
    let high: Double
}

struct HeatmapLayout {
    let x0: CGFloat
    let y0: CGFloat
    let cellWidth: CGFloat
    let cellHeight: CGFloat
    let gridWidth: CGFloat
    let gridHeight: CGFloat
    let xGroups: [AxisGroup]
    let yGroups: [AxisGroup]

    func cellRef(at point: CGPoint) -> CellRef? {
        guard x0 <= point.x, point.x < x0 + gridWidth, y0 <= point.y, point.y < y0 + gridHeight else {
            return nil
        }
        let groupIndex = Int((point.x - x0) / cellWidth)
        let displayY = Int((point.y - y0) / cellHeight)
        guard xGroups.indices.contains(groupIndex), yGroups.indices.contains(displayY) else {
            return nil
        }
        let yGroup = yGroups[displayY]
        let xGroup = xGroups[groupIndex]
        return CellRef(yStart: yGroup.start, yEnd: yGroup.end, xStart: xGroup.start, xEnd: xGroup.end)
    }

    func rect(for cellRef: CellRef) -> CGRect? {
        guard let groupIndex = xGroups.firstIndex(where: { $0.start <= cellRef.xStart && cellRef.xStart <= $0.end }),
              let displayY = yGroups.firstIndex(where: { $0.start <= cellRef.yStart && cellRef.yStart <= $0.end }) else {
            return nil
        }
        return CGRect(
            x: x0 + CGFloat(groupIndex) * cellWidth,
            y: y0 + CGFloat(displayY) * cellHeight,
            width: cellWidth,
            height: cellHeight
        )
    }
}

func makeHeatmapPlot(
    store: RFMappingStore,
    matrix: OptionalMatrix,
    fixedRange: (Double, Double)? = nil,
    smooth: Bool = true
) -> HeatmapPlot {
    let prepared = store.preparePlotMatrix(matrix, smooth: smooth)
    let range = fixedRange ?? finiteMinMax(prepared.0)
    return HeatmapPlot(matrix: prepared.0, xGroups: prepared.1, yGroups: prepared.2, low: range.0, high: range.1)
}

func makeHeatmapLayout(size: CGSize, plot: HeatmapPlot, margins: EdgeInsets = EdgeInsets(top: 56, leading: 78, bottom: 68, trailing: 104)) -> HeatmapLayout {
    let plotWidth = max(10, size.width - margins.leading - margins.trailing)
    let plotHeight = max(10, size.height - margins.top - margins.bottom)
    let rows = max(1, plot.yGroups.count)
    let cols = max(1, plot.xGroups.count)
    let dimensions = spatialGridDimensions(
        availableWidth: plotWidth,
        availableHeight: plotHeight,
        columns: cols,
        rows: rows,
        minimumCellWidth: 4
    )
    let x0 = margins.leading + (plotWidth - dimensions.gridWidth) / 2.0
    let y0 = margins.top + (plotHeight - dimensions.gridHeight) / 2.0
    return HeatmapLayout(
        x0: x0,
        y0: y0,
        cellWidth: dimensions.cellWidth,
        cellHeight: dimensions.cellHeight,
        gridWidth: dimensions.gridWidth,
        gridHeight: dimensions.gridHeight,
        xGroups: plot.xGroups,
        yGroups: plot.yGroups
    )
}

func drawHeatmap(
    context: inout GraphicsContext,
    store: RFMappingStore,
    plot: HeatmapPlot,
    layout: HeatmapLayout,
    title: String,
    subtitle: String,
    palette: RFPalette?,
    valueSuffix: String = "",
    drawLegend: Bool = true,
    drawInteraction: Bool = true
) {
    drawTitle(context: &context, title: title, subtitle: subtitle)

    for displayY in plot.matrix.indices {
        for groupIndex in plot.matrix[displayY].indices {
            let rect = CGRect(
                x: layout.x0 + CGFloat(groupIndex) * layout.cellWidth,
                y: layout.y0 + CGFloat(displayY) * layout.cellHeight,
                width: layout.cellWidth,
                height: layout.cellHeight
            )
            let value = plot.matrix[displayY][groupIndex]
            let fill = palette.map { paletteColor(value, low: plot.low, high: plot.high, palette: $0) }
                ?? delayColor(value, low: plot.low, high: plot.high)
            context.fill(Path(rect), with: .color(fill))
        }
    }

    if drawInteraction {
        drawSelectionAndHover(context: &context, store: store, layout: layout)
    }
    drawAxes(context: &context, store: store, layout: layout)
    if drawLegend {
        drawColorbar(
            context: &context,
            x: layout.x0 + layout.gridWidth + 36,
            y: layout.y0,
            height: min(220, layout.gridHeight),
            low: plot.low,
            high: plot.high,
            palette: palette,
            suffix: valueSuffix
        )
    }
}

struct RectangularPlotInteractionLayer: View {
    @Bindable var store: RFMappingStore
    let layout: HeatmapLayout
    let size: CGSize

    var body: some View {
        ZStack(alignment: .topLeading) {
            Canvas { context, _ in
                var context = context
                drawSelectionAndHover(context: &context, store: store, layout: layout)
            }
            .allowsHitTesting(false)

            PointerCaptureView(
                onMove: { point in
                    if let cell = layout.cellRef(at: point) {
                        store.setHover(cell, location: point)
                    } else {
                        store.clearHover()
                    }
                },
                onClick: { point, _ in
                    if let cell = layout.cellRef(at: point) {
                        store.selectCell(cell)
                    }
                },
                onLeave: store.clearHover
            )

            if let cell = store.hoverCell, let location = store.hoverLocation {
                PlotTooltip(text: store.tooltipText(cell), location: location, canvasSize: size)
            }
        }
    }
}

func drawTitle(context: inout GraphicsContext, title: String, subtitle: String) {
    context.draw(
        Text(title).font(.system(size: 15, weight: .semibold)).foregroundStyle(.primary),
        at: CGPoint(x: 20, y: 22),
        anchor: .leading
    )
    context.draw(
        Text(subtitle).font(.system(size: 11)).foregroundStyle(.secondary),
        at: CGPoint(x: 20, y: 44),
        anchor: .leading
    )
}

func drawAxes(context: inout GraphicsContext, store: RFMappingStore, layout: HeatmapLayout) {
    guard let data = store.data else { return }
    let border = Path(CGRect(x: layout.x0, y: layout.y0, width: layout.gridWidth, height: layout.gridHeight))
    context.stroke(border, with: .color(.secondary), lineWidth: 1)

    let tickStep = max(1, layout.xGroups.count / 6)
    for groupIndex in stride(from: 0, to: layout.xGroups.count, by: tickStep) {
        let group = layout.xGroups[groupIndex]
        let x = layout.x0 + (CGFloat(groupIndex) + 0.5) * layout.cellWidth
        var tick = Path()
        tick.move(to: CGPoint(x: x, y: layout.y0 + layout.gridHeight))
        tick.addLine(to: CGPoint(x: x, y: layout.y0 + layout.gridHeight + 5))
        context.stroke(tick, with: .color(.secondary), lineWidth: 1)
        let pos = (data.xPositions[group.start] + data.xPositions[group.end]) / 2.0
        context.draw(
            Text(formatPos(pos)).font(.system(size: 9)).foregroundStyle(.secondary),
            at: CGPoint(x: x, y: layout.y0 + layout.gridHeight + 18),
            anchor: .center
        )
    }

    if let last = layout.xGroups.indices.last, last % tickStep != 0 {
        let group = layout.xGroups[last]
        let x = layout.x0 + (CGFloat(last) + 0.5) * layout.cellWidth
        let pos = (data.xPositions[group.start] + data.xPositions[group.end]) / 2.0
        context.draw(
            Text(formatPos(pos)).font(.system(size: 9)).foregroundStyle(.secondary),
            at: CGPoint(x: x, y: layout.y0 + layout.gridHeight + 18),
            anchor: .center
        )
    }

    for (displayY, group) in layout.yGroups.enumerated() {
        let y = layout.y0 + (CGFloat(displayY) + 0.5) * layout.cellHeight
        var tick = Path()
        tick.move(to: CGPoint(x: layout.x0 - 5, y: y))
        tick.addLine(to: CGPoint(x: layout.x0, y: y))
        context.stroke(tick, with: .color(.secondary), lineWidth: 1)
        let pos = (data.yPositions[group.start] + data.yPositions[group.end]) / 2.0
        let label = group.start == group.end
            ? "\(group.start + 1) / \(formatPos(pos))"
            : "\(group.start + 1)-\(group.end + 1) / \(formatPos(pos))"
        context.draw(
            Text(label).font(.system(size: 9)).foregroundStyle(.secondary),
            at: CGPoint(x: layout.x0 - 10, y: y),
            anchor: .trailing
        )
    }

    context.drawLayer { layer in
        layer.translateBy(x: layout.x0 - 58, y: layout.y0 + layout.gridHeight / 2)
        layer.rotate(by: .degrees(-90))
        layer.draw(
            Text("yIdx / y").font(.system(size: 10)).foregroundStyle(.secondary),
            at: .zero,
            anchor: .center
        )
    }

    context.draw(
        Text("x position").font(.system(size: 11)).foregroundStyle(.secondary),
        at: CGPoint(x: layout.x0 + layout.gridWidth / 2.0, y: layout.y0 + layout.gridHeight + 44),
        anchor: .center
    )
}

func drawColorbar(
    context: inout GraphicsContext,
    x: CGFloat,
    y: CGFloat,
    height: CGFloat,
    low: Double,
    high: Double,
    palette: RFPalette?,
    suffix: String
) {
    let steps = 90
    let width: CGFloat = 16
    for index in 0..<steps {
        let t = Double(index) / Double(steps)
        let value = high - (high - low) * t
        let fill = palette.map { paletteColor(value, low: low, high: high, palette: $0) }
            ?? delayColor(value, low: low, high: high)
        let y1 = y + height * CGFloat(index) / CGFloat(steps)
        let y2 = y + height * CGFloat(index + 1) / CGFloat(steps)
        context.fill(Path(CGRect(x: x, y: y1, width: width, height: y2 - y1)), with: .color(fill))
    }
    context.stroke(Path(CGRect(x: x, y: y, width: width, height: height)), with: .color(.secondary), lineWidth: 1)
    context.draw(
        Text(String(format: "%.1f%@", high, suffix)).font(.system(size: 9)).foregroundStyle(.secondary),
        at: CGPoint(x: x + width + 8, y: y),
        anchor: .leading
    )
    context.draw(
        Text(String(format: "%.1f%@", low, suffix)).font(.system(size: 9)).foregroundStyle(.secondary),
        at: CGPoint(x: x + width + 8, y: y + height),
        anchor: .leading
    )
}

func drawSelectionAndHover(context: inout GraphicsContext, store: RFMappingStore, layout: HeatmapLayout) {
    if let selected = store.selectedCell, let rect = layout.rect(for: selected) {
        context.stroke(Path(rect.insetBy(dx: 1, dy: 1)), with: .color(.primary), lineWidth: 2)
        context.stroke(Path(rect.insetBy(dx: 3, dy: 3)), with: .color(.white), lineWidth: 1)
    }
    if let hover = store.hoverCell, let rect = layout.rect(for: hover) {
        context.stroke(Path(rect.insetBy(dx: 1, dy: 1)), with: .color(.orange), lineWidth: 3)
    }
}

struct PlotTooltip: View {
    let text: String
    let location: CGPoint
    let canvasSize: CGSize
    let visibleRect: CGRect?

    init(text: String, location: CGPoint, canvasSize: CGSize, visibleRect: CGRect? = nil) {
        self.text = text
        self.location = location
        self.canvasSize = canvasSize
        self.visibleRect = visibleRect
    }

    var body: some View {
        Text(text)
            .font(.system(size: 11, design: .monospaced))
            .foregroundStyle(.white)
            .padding(8)
            .frame(width: 210, alignment: .leading)
            .background(Color(nsColor: .textColor).opacity(0.92), in: RoundedRectangle(cornerRadius: 6))
            .position(position)
            .allowsHitTesting(false)
    }

    private var position: CGPoint {
        let width: CGFloat = 210
        let lineCount = max(1, text.split(separator: "\n", omittingEmptySubsequences: false).count)
        let height = max(92, CGFloat(lineCount) * 14 + 16)
        let bounds = visibleRect ?? CGRect(origin: .zero, size: canvasSize)
        var x = location.x + width / 2.0 + 16
        var y = location.y + height / 2.0 + 16
        if x + width / 2.0 > bounds.maxX - 8 {
            x = location.x - width / 2.0 - 16
        }
        if y + height / 2.0 > bounds.maxY - 8 {
            y = location.y - height / 2.0 - 16
        }
        x = max(bounds.minX + width / 2.0 + 8, min(bounds.maxX - width / 2.0 - 8, x))
        y = max(bounds.minY + height / 2.0 + 8, min(bounds.maxY - height / 2.0 - 8, y))
        return CGPoint(x: x, y: y)
    }
}
