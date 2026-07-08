import AppKit
import SwiftUI

struct TimelineView: View {
    @Bindable var store: RFMappingStore

    var body: some View {
        GeometryReader { proxy in
            let outerSize = proxy.size
            let layout = makeTimelineLayout(store: store, width: outerSize.width, height: outerSize.height)

            ScrollView(.vertical) {
                ZStack(alignment: .topLeading) {
                    Canvas { context, size in
                        var context = context
                        drawTimeline(context: &context, size: size, store: store, layout: layout)
                    }
                    PointerCaptureView(
                        onMove: { point in
                            if let hit = timelineHit(at: point, store: store, layout: layout) {
                                switch hit {
                                case .cell(let bin, let cell):
                                    store.setHover(cell, location: point, extra: "timeline bin \(store.timeGroupLabel(bin))")
                                case .bin(let bin):
                                    store.hoverCell = nil
                                    store.hoverLocation = nil
                                    store.hoverExtra = "bin \(store.timeGroupLabel(bin))"
                                }
                            } else {
                                store.clearHover()
                            }
                        },
                        onClick: { point, modifiers in
                            if let hit = timelineHit(at: point, store: store, layout: layout) {
                                let extending = modifiers.contains(.shift)
                                    || modifiers.contains(.control)
                                    || modifiers.contains(.option)
                                    || modifiers.contains(.command)
                                switch hit {
                                case .cell(let bin, let cell):
                                    store.selectCell(cell)
                                    store.selectTimelineBin(bin, extending: extending)
                                case .bin(let bin):
                                    store.selectTimelineBin(bin, extending: extending)
                                }
                            }
                        },
                        onLeave: {
                            store.clearHover()
                        }
                    )
                    if let cell = store.hoverCell, let location = store.hoverLocation {
                        PlotTooltip(text: store.tooltipText(cell), location: location, canvasSize: CGSize(width: outerSize.width, height: layout.contentHeight))
                    }
                }
                .frame(width: outerSize.width, height: layout.contentHeight)
            }
            .background(Color(nsColor: .textBackgroundColor))
        }
    }
}

private enum TimelineHit {
    case bin(Int)
    case cell(Int, CellRef)
}

private struct TimelineMiniLayout {
    let bin: Int
    let x0: CGFloat
    let y0: CGFloat
    let cell: CGFloat
    let gridWidth: CGFloat
    let gridHeight: CGFloat
    let xGroups: [AxisGroup]
    let yGroups: [AxisGroup]

    func cellRef(at point: CGPoint) -> CellRef? {
        guard x0 <= point.x, point.x < x0 + gridWidth, y0 <= point.y, point.y < y0 + gridHeight else {
            return nil
        }
        let groupIndex = Int((point.x - x0) / cell)
        let displayY = Int((point.y - y0) / cell)
        guard xGroups.indices.contains(groupIndex), yGroups.indices.contains(displayY) else {
            return nil
        }
        let xGroup = xGroups[groupIndex]
        let yGroup = yGroups[displayY]
        return CellRef(yStart: yGroup.start, yEnd: yGroup.end, xStart: xGroup.start, xEnd: xGroup.end)
    }
}

private struct TimelineLayout {
    let chartRect: CGRect
    let displayBins: Int
    let visibleBins: [Int]
    let miniLayouts: [TimelineMiniLayout]
    let contentHeight: CGFloat
    let maxTotal: Double
    let selectedMax: Double
    let timeTotals: [Double]
    let selectedHist: [Double]?
    let cellHigh: Double
}

private func makeTimelineLayout(store: RFMappingStore, width: CGFloat, height: CGFloat) -> TimelineLayout {
    let timeGroups = store.timeGroups()
    let displayBins = max(1, timeGroups.count)
    let visibleBins = store.visibleTimelineBins(displayBins: displayBins)
    let metrics = store.data?.metrics(for: store.unitIndex)
    let timeTotals = timeGroups.map { group -> Double in
        metrics?.binTotals[group.start...group.end].reduce(0.0, +) ?? 0.0
    }
    let chartRect = CGRect(x: 64, y: 78, width: max(320, width - 140), height: 62)
    let xGroups = store.xGroups()
    let yGroups = store.displayYGroups()
    let miniTop = chartRect.maxY + 30
    let miniSpec = timelineMiniSpec(width: width, height: height, visibleCount: visibleBins.count, xCount: xGroups.count, yCount: yGroups.count)
    var miniLayouts: [TimelineMiniLayout] = []
    for (visibleIndex, bin) in visibleBins.enumerated() {
        let row = visibleIndex / miniSpec.cols
        let col = visibleIndex % miniSpec.cols
        let slotX = miniSpec.left + CGFloat(col) * (miniSpec.slotWidth + miniSpec.gapX)
        let x0 = slotX + max(0.0, (miniSpec.slotWidth - miniSpec.gridWidth) / 2.0)
        let y0 = miniTop + CGFloat(row) * miniSpec.rowStep
        miniLayouts.append(TimelineMiniLayout(bin: bin, x0: x0, y0: y0, cell: miniSpec.cell, gridWidth: miniSpec.gridWidth, gridHeight: miniSpec.gridHeight, xGroups: xGroups, yGroups: yGroups))
    }
    let rows = Int(ceil(Double(max(1, visibleBins.count)) / Double(max(1, miniSpec.cols))))
    let contentHeight = max(height, miniTop + CGFloat(max(0, rows - 1)) * miniSpec.rowStep + miniSpec.gridHeight + 28)
    let selectedHist = store.selectedCell.map { store.timeGroupedHist(store.groupHist($0)) }
    return TimelineLayout(
        chartRect: chartRect,
        displayBins: displayBins,
        visibleBins: visibleBins,
        miniLayouts: miniLayouts,
        contentHeight: contentHeight,
        maxTotal: max(timeTotals.max() ?? 0.0, 1.0),
        selectedMax: max(selectedHist?.max() ?? 0.0, 1.0),
        timeTotals: timeTotals,
        selectedHist: selectedHist,
        cellHigh: store.maxTimeGroupCellCount(timeGroups: timeGroups)
    )
}

private struct TimelineMiniSpec {
    let left: CGFloat
    let cols: Int
    let gapX: CGFloat
    let gapY: CGFloat
    let slotWidth: CGFloat
    let cell: CGFloat
    let gridWidth: CGFloat
    let gridHeight: CGFloat
    let rowStep: CGFloat
}

private func timelineMiniSpec(width: CGFloat, height: CGFloat, visibleCount: Int, xCount: Int, yCount: Int) -> TimelineMiniSpec {
    let count = max(1, visibleCount)
    let xCount = max(1, xCount)
    let yCount = max(1, yCount)
    let gapX = max(1.0, min(3.0, width * 0.002))
    let gapY = max(2.0, min(4.0, height * 0.004))
    let left: CGFloat = 44
    let rightPad: CGFloat = 44
    let availableWidth = max(120, width - left - rightPad)
    let targetGridHeight = min(78, max(44, height * 0.12))
    let targetCell = targetGridHeight / CGFloat(yCount)
    let targetGridWidth = targetCell * CGFloat(xCount)
    let maxCols = max(1, Int((availableWidth + gapX) / max(1, targetGridWidth + gapX)))
    let cols = min(count, maxCols)
    let slotWidth = max(1, (availableWidth - CGFloat(cols - 1) * gapX) / CGFloat(cols))
    let cell = max(2, min(targetCell, slotWidth / CGFloat(xCount)))
    let gridWidth = cell * CGFloat(xCount)
    let gridHeight = cell * CGFloat(yCount)
    return TimelineMiniSpec(
        left: left,
        cols: cols,
        gapX: gapX,
        gapY: gapY,
        slotWidth: slotWidth,
        cell: cell,
        gridWidth: gridWidth,
        gridHeight: gridHeight,
        rowStep: gridHeight + 13 + gapY
    )
}

private func drawTimeline(context: inout GraphicsContext, size: CGSize, store: RFMappingStore, layout: TimelineLayout) {
    let visibleNote = layout.visibleBins.count == layout.displayBins
        ? "\(layout.displayBins) bin maps"
        : "\(layout.visibleBins.count) selected bin maps"
    drawTitle(
        context: &context,
        title: "Timeline and \(visibleNote)",
        subtitle: "Selected bin: \(store.timeGroupLabel(store.binIndex)); time res \(formatMS(store.timeResolutionMS)) ms"
    )

    drawTimelineChart(context: &context, store: store, layout: layout)
    drawTimelineMiniMaps(context: &context, store: store, layout: layout)
}

private func drawTimelineChart(context: inout GraphicsContext, store: RFMappingStore, layout: TimelineLayout) {
    let rect = layout.chartRect
    context.stroke(Path(rect), with: .color(.secondary.opacity(0.45)), lineWidth: 1)

    var totalPath = Path()
    for (index, value) in layout.timeTotals.enumerated() {
        let x = rect.minX + rect.width * (CGFloat(index) + 0.5) / CGFloat(layout.displayBins)
        let y = rect.maxY - rect.height * CGFloat(value / layout.maxTotal)
        if index == 0 {
            totalPath.move(to: CGPoint(x: x, y: y))
        } else {
            totalPath.addLine(to: CGPoint(x: x, y: y))
        }
    }
    context.stroke(totalPath, with: .color(.blue), lineWidth: 2)

    if let selectedHist = layout.selectedHist {
        var selectedPath = Path()
        for (index, value) in selectedHist.enumerated() {
            let x = rect.minX + rect.width * (CGFloat(index) + 0.5) / CGFloat(layout.displayBins)
            let y = rect.maxY - rect.height * CGFloat(value / layout.selectedMax)
            if index == 0 {
                selectedPath.move(to: CGPoint(x: x, y: y))
            } else {
                selectedPath.addLine(to: CGPoint(x: x, y: y))
            }
        }
        context.stroke(selectedPath, with: .color(.red), lineWidth: 1.8)
        drawAxisScale(context: &context, x: rect.minX - 20, rect: rect, high: layout.selectedMax, color: .red, leading: true)
    }

    drawAxisScale(context: &context, x: rect.maxX + 20, rect: rect, high: layout.maxTotal, color: .blue, leading: false)

    let binWidth = rect.width / CGFloat(layout.displayBins)
    if store.mode == .bin || store.mode == .rangeSum {
        let selectedX = rect.minX + CGFloat(store.binIndex) * binWidth
        context.stroke(Path(CGRect(x: selectedX, y: rect.minY, width: binWidth, height: rect.height)), with: .color(.orange), lineWidth: 2)
        if store.mode == .rangeSum {
            let start = min(store.rangeStart, store.rangeEnd)
            let end = max(store.rangeStart, store.rangeEnd)
            let rangeRect = CGRect(x: rect.minX + CGFloat(start) * binWidth, y: rect.minY, width: CGFloat(end - start + 1) * binWidth, height: rect.height)
            context.stroke(Path(rangeRect), with: .color(.green), lineWidth: 1)
        }
    }

    let axisRange = store.timeAxisRangeMS()
    context.draw(Text("\(formatMS(axisRange.0)) ms").font(.system(size: 11)).foregroundStyle(.secondary), at: CGPoint(x: rect.minX, y: rect.maxY + 18), anchor: .leading)
    if layout.displayBins > 1 {
        context.draw(Text(store.timeGroupEndLabel(0)).font(.system(size: 11)).foregroundStyle(.secondary), at: CGPoint(x: rect.minX + binWidth, y: rect.maxY + 18), anchor: .center)
    }
    context.draw(Text("\(formatMS(axisRange.1)) ms").font(.system(size: 11)).foregroundStyle(.secondary), at: CGPoint(x: rect.maxX, y: rect.maxY + 18), anchor: .trailing)
}

private func drawAxisScale(context: inout GraphicsContext, x: CGFloat, rect: CGRect, high: Double, color: Color, leading: Bool) {
    var axis = Path()
    axis.move(to: CGPoint(x: x, y: rect.minY))
    axis.addLine(to: CGPoint(x: x, y: rect.maxY))
    context.stroke(axis, with: .color(color), lineWidth: 1)
    let anchor: UnitPoint = leading ? .trailing : .leading
    let textX = leading ? x - 7 : x + 7
    context.draw(Text(String(format: "%.0f", high)).font(.system(size: 8)).foregroundStyle(color), at: CGPoint(x: textX, y: rect.minY), anchor: anchor)
    context.draw(Text("0").font(.system(size: 8)).foregroundStyle(color), at: CGPoint(x: textX, y: rect.maxY), anchor: anchor)
}

private func drawTimelineMiniMaps(context: inout GraphicsContext, store: RFMappingStore, layout: TimelineLayout) {
    guard let data = store.data else { return }
    let timeGroups = store.timeGroups()
    for mini in layout.miniLayouts {
        let source = timeGroups[mini.bin]
        let matrix = data.aggregateMatrix(unitIndex: store.unitIndex, mode: .rangeSum, binIndex: 0, rangeStart: source.start, rangeEnd: source.end)
        let prepared = store.preparePlotMatrix(optionalMatrix(matrix), smooth: true)

        for displayY in prepared.0.indices {
            for groupIndex in prepared.0[displayY].indices {
                let value = prepared.0[displayY][groupIndex]
                let rect = CGRect(
                    x: mini.x0 + CGFloat(groupIndex) * mini.cell,
                    y: mini.y0 + CGFloat(displayY) * mini.cell,
                    width: mini.cell,
                    height: mini.cell
                )
                context.fill(Path(rect), with: .color(paletteColor(value, low: 0, high: layout.cellHigh, palette: store.palette)))
            }
        }

        let outline = mini.bin == store.binIndex ? Color.orange : Color.secondary.opacity(0.45)
        let lineWidth: CGFloat = mini.bin == store.binIndex ? 2 : 1
        context.stroke(Path(CGRect(x: mini.x0, y: mini.y0, width: mini.gridWidth, height: mini.gridHeight)), with: .color(outline), lineWidth: lineWidth)
        context.draw(Text(store.timeGroupEndLabel(mini.bin)).font(.system(size: 8)).foregroundStyle(.secondary), at: CGPoint(x: mini.x0, y: mini.y0 + mini.gridHeight + 11), anchor: .leading)
    }
}

private func timelineHit(at point: CGPoint, store: RFMappingStore, layout: TimelineLayout) -> TimelineHit? {
    let chart = layout.chartRect
    if chart.contains(point) {
        let binWidth = chart.width / CGFloat(layout.displayBins)
        let bin = max(0, min(layout.displayBins - 1, Int((point.x - chart.minX) / binWidth)))
        return .bin(bin)
    }

    for mini in layout.miniLayouts {
        if let cell = mini.cellRef(at: point) {
            return .cell(mini.bin, cell)
        }
        let labelRect = CGRect(x: mini.x0, y: mini.y0, width: mini.gridWidth, height: mini.gridHeight + 13)
        if labelRect.contains(point) {
            return .bin(mini.bin)
        }
    }

    return nil
}
