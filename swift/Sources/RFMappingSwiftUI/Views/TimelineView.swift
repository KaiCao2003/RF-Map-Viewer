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
                    TimelineBaseLayer(layout: layout)
                        .equatable()
                    TimelineSelectionLayer(layout: layout)
                    TimelineInteractionLayer(
                        store: store,
                        layout: layout,
                        outerSize: outerSize
                    )
                }
                .frame(width: outerSize.width, height: layout.contentHeight)
                .accessibilityRepresentation {
                    TimelineAccessibilityRepresentation(store: store, layout: layout)
                }
            }
            .background(Color(nsColor: .textBackgroundColor))
        }
    }
}

/// Deterministic, interaction-free timeline used by live figure preview and
/// final export. Unlike the viewer's scrollable timeline, this lays out every
/// current time-resolution frame inside the allotted figure slot.
struct TimelineExportView: View {
    let store: RFMappingStore

    var body: some View {
        GeometryReader { proxy in
            let layout = makeTimelineExportLayout(
                store: store,
                width: proxy.size.width,
                height: proxy.size.height
            )
            Canvas { context, size in
                var context = context
                drawTimelineBase(context: &context, size: size, layout: layout)
                drawTimelineSelection(context: &context, size: size, layout: layout)
            }
            .background(Color(nsColor: .textBackgroundColor))
        }
    }
}

private enum TimelineHit {
    case bin(Int)
    case cell(Int, CellRef)
}

struct TimelineMiniLayout {
    let bin: Int
    let x0: CGFloat
    let y0: CGFloat
    let cellWidth: CGFloat
    let cellHeight: CGFloat
    let gridWidth: CGFloat
    let gridHeight: CGFloat
    let xGroups: [AxisGroup]
    let yGroups: [AxisGroup]
    let spatialFormat: SpatialPlotFormat
    let totalDegrees: Double
    let polarRingRows: [Int]

    var polarLayout: PolarLayout? {
        guard spatialFormat == .polar, !xGroups.isEmpty, !yGroups.isEmpty else { return nil }
        let ringSpan = polarRingSpan(rowCount: yGroups.count)
        let radiusUnits = CGFloat(innerBlankRows + polarPadRows)
            + CGFloat(yGroups.count) * ringSpan
        let scale = min(gridWidth, gridHeight) / max(2.0, 2.0 * radiusUnits)
        return PolarLayout(
            center: CGPoint(x: x0 + gridWidth / 2.0, y: y0 + gridHeight / 2.0),
            scale: scale,
            totalDegrees: totalDegrees,
            xGroups: xGroups,
            yGroups: yGroups,
            ringRows: polarRingRows,
            ringSpan: ringSpan
        )
    }

    func cellRef(at point: CGPoint) -> CellRef? {
        if let polarLayout {
            return polarCell(at: point, layout: polarLayout)?.cell
        }
        guard x0 <= point.x, point.x < x0 + gridWidth,
              y0 <= point.y, point.y < y0 + gridHeight else {
            return nil
        }
        let groupIndex = Int((point.x - x0) / cellWidth)
        let displayY = Int((point.y - y0) / cellHeight)
        guard xGroups.indices.contains(groupIndex), yGroups.indices.contains(displayY) else {
            return nil
        }
        let xGroup = xGroups[groupIndex]
        let yGroup = yGroups[displayY]
        return CellRef(
            yStart: yGroup.start,
            yEnd: yGroup.end,
            xStart: xGroup.start,
            xEnd: xGroup.end
        )
    }
}

struct TimelineLayout {
    let chartRect: CGRect
    let displayBins: Int
    let timeGroups: [AxisGroup]
    let miniLayouts: [TimelineMiniLayout]
    let miniMatrices: [OptionalMatrix]
    let contentHeight: CGFloat
    let maxTotal: Double
    let selectedMax: Double
    let timeTotals: [Double]
    let selectedValues: [Double]?
    let cellHigh: Double
    let labelGap: CGFloat
    let labelHeight: CGFloat
    let miniColumns: Int
    let miniRowStep: CGFloat
    fileprivate let renderState: TimelineRenderState
    fileprivate let renderKey: TimelineBaseRenderKey
}

private struct TimelineRenderState {
    let valueMode: ResponseValueMode
    let palette: RFPalette
    let spatialFormat: SpatialPlotFormat
    let selectedBoundsStartMS: Double
    let selectedBoundsEndMS: Double
    let axisStartMS: Double
    let axisEndMS: Double
    let baseBinMS: Double
    let timeResolutionMS: Double
    let hasTimeSelection: Bool
    let selectedDisplayRange: AxisGroup?
    let timeGroupLabels: [String]
    let timeGroupEndBoundsMS: [Double]
}

/// A compact provenance key for everything rasterized by the heavy timeline
/// layer. Hover position, hover cell, and scroll offset are intentionally not
/// inputs: those are rendered by lightweight layers above it.
private struct TimelineBaseRenderKey: Equatable {
    let dataID: ObjectIdentifier?
    let unitIndex: Int
    let valueMode: ResponseValueMode
    let timeGroupSize: Int
    let timeResolutionMS: Double
    let xBins: Int
    let yBins: Int
    let flipY: Bool
    let smoothRadius: Int
    let palette: RFPalette
    let spatialFormat: SpatialPlotFormat
    let polarRadiusMode: PolarRadiusMode
    let width: CGFloat
    let height: CGFloat
    let contentHeight: CGFloat
}

private struct TimelineBaseLayer: View, Equatable {
    let layout: TimelineLayout

    nonisolated static func == (lhs: TimelineBaseLayer, rhs: TimelineBaseLayer) -> Bool {
        MainActor.assumeIsolated {
            lhs.layout.renderKey == rhs.layout.renderKey
        }
    }

    var body: some View {
        Canvas(rendersAsynchronously: true) { context, size in
            var context = context
            drawTimelineBase(context: &context, size: size, layout: layout)
        }
    }
}

private struct TimelineSelectionLayer: View {
    let layout: TimelineLayout

    var body: some View {
        Canvas { context, size in
            var context = context
            drawTimelineSelection(context: &context, size: size, layout: layout)
        }
        .allowsHitTesting(false)
    }
}

private struct TimelineHoverLayer: View {
    let layout: TimelineLayout
    let displayBin: Int
    let cell: CellRef

    var body: some View {
        Canvas { context, _ in
            var context = context
            drawTimelineHover(
                context: &context,
                layout: layout,
                displayBin: displayBin,
                cell: cell
            )
        }
    }
}

/// Owns the transient observable state so hover movement and scroll-offset
/// tracking do not invalidate `TimelineView` and rebuild its immutable layout.
private struct TimelineInteractionLayer: View {
    @Bindable var store: RFMappingStore
    let layout: TimelineLayout
    let outerSize: CGSize

    var body: some View {
        ZStack(alignment: .topLeading) {
            if let hoverCell = store.hoverCell,
               let hoverDisplayBin = store.hoverDisplayBin {
                TimelineHoverLayer(
                    layout: layout,
                    displayBin: hoverDisplayBin,
                    cell: hoverCell
                )
                .allowsHitTesting(false)
            }
            PointerCaptureView(
                onMove: { point in
                    if let hit = timelineHit(at: point, layout: layout) {
                        switch hit {
                        case .cell(let bin, let cell):
                            store.setHover(
                                cell,
                                location: point,
                                extra: "timeline bin \(store.timeGroupLabel(bin))",
                                displayBin: bin
                            )
                        case .bin(let bin):
                            store.setTimelineBinHover(bin)
                        }
                    } else {
                        store.clearHover()
                    }
                },
                onClick: { point, modifiers in
                    guard let hit = timelineHit(at: point, layout: layout) else { return }
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
                },
                onLeave: {
                    store.clearHover()
                }
            )
            if let cell = store.hoverCell, let location = store.hoverLocation {
                PlotTooltip(
                    text: store.tooltipText(cell, displayBin: store.hoverDisplayBin),
                    location: location,
                    canvasSize: CGSize(width: outerSize.width, height: layout.contentHeight),
                    visibleRect: CGRect(
                        x: 0,
                        y: CGFloat(store.timelineScrollFraction)
                            * max(0, layout.contentHeight - outerSize.height),
                        width: outerSize.width,
                        height: outerSize.height
                    )
                )
            }
            TimelineScrollOffsetTracker(fraction: $store.timelineScrollFraction)
                .frame(width: 1, height: 1)
        }
        .frame(
            width: outerSize.width,
            height: layout.contentHeight,
            alignment: .topLeading
        )
    }
}

private func makeTimelineLayout(
    store: RFMappingStore,
    width: CGFloat,
    height: CGFloat,
    fittingAllFrames: Bool = false
) -> TimelineLayout {
    let snapshot = store.timelineSnapshot()
    let timeGroups = snapshot.timeGroups
    let displayBins = max(1, timeGroups.count)
    let visibleBins = Array(0..<displayBins)
    let rawTotals = snapshot.totals
    let timeTotals = (0..<displayBins).map { index in
        rawTotals.indices.contains(index) ? rawTotals[index] : 0.0
    }
    let selectedValues = store.selectedCell.map { cell in
        let values = store.groupResponseValues(cell)
        return (0..<displayBins).map { index in
            guard values.indices.contains(index), let value = values[index], value.isFinite else { return 0.0 }
            return value
        }
    }

    let xGroups = store.xGroups()
    let yGroups = store.displayYGroups()
    let miniMatrices = snapshot.matrices
    let selectedBounds = store.selectedTimeBoundsMS()
    let axisRange = store.timeAxisRangeMS()
    let hasTimeSelection = store.hasTimeSelection
    let selectedDisplayRange = hasTimeSelection ? store.displayRangeIndices() : nil
    let timeGroupLabels = visibleBins.map(store.timeGroupLabel)
    let timeGroupEndBoundsMS = visibleBins.map { store.timeGroupBoundsMS($0).1 }

    let chartRect: CGRect
    let miniTop: CGFloat
    if fittingAllFrames {
        let horizontalMargin = max(22, min(64, width * 0.10))
        let chartY = max(48, min(78, height * 0.28))
        let chartHeight = max(14, min(62, height * 0.18))
        chartRect = CGRect(
            x: horizontalMargin,
            y: chartY,
            width: max(10, width - horizontalMargin * 2),
            height: chartHeight
        )
        miniTop = chartRect.maxY + max(8, min(30, height * 0.08))
    } else {
        chartRect = CGRect(x: 64, y: 78, width: max(320, width - 140), height: 62)
        miniTop = chartRect.maxY + 54
    }
    let miniSpec = timelineMiniSpec(
        width: width,
        height: height,
        visibleCount: visibleBins.count,
        xCount: xGroups.count,
        yCount: yGroups.count,
        spatialFormat: store.spatialPlotFormat,
        miniTop: miniTop,
        fittingAllFrames: fittingAllFrames
    )
    let totalDegrees = store.data?.inferTotalDeg() ?? 360.0
    let polarRingRows = store.polarRadiusMode == .matlabRowOneInner
        ? Array(yGroups.indices).sorted { yGroups[$0].start < yGroups[$1].start }
        : Array(yGroups.indices.reversed())
    var miniLayouts: [TimelineMiniLayout] = []
    for (visibleIndex, bin) in visibleBins.enumerated() {
        let row = visibleIndex / miniSpec.cols
        let col = visibleIndex % miniSpec.cols
        let slotX = miniSpec.left + CGFloat(col) * (miniSpec.slotWidth + miniSpec.gapX)
        let x0 = slotX + max(0.0, (miniSpec.slotWidth - miniSpec.gridWidth) / 2.0)
        let y0 = miniTop + CGFloat(row) * miniSpec.rowStep
        miniLayouts.append(
            TimelineMiniLayout(
                bin: bin,
                x0: x0,
                y0: y0,
                cellWidth: miniSpec.cellWidth,
                cellHeight: miniSpec.cellHeight,
                gridWidth: miniSpec.gridWidth,
                gridHeight: miniSpec.gridHeight,
                xGroups: xGroups,
                yGroups: yGroups,
                spatialFormat: store.spatialPlotFormat,
                totalDegrees: totalDegrees,
                polarRingRows: polarRingRows
            )
        )
    }

    let rows = Int(ceil(Double(max(1, visibleBins.count)) / Double(max(1, miniSpec.cols))))
    let requiredContentHeight = miniTop
        + CGFloat(max(0, rows - 1)) * miniSpec.rowStep
        + miniSpec.gridHeight
        + miniSpec.labelGap
        + miniSpec.labelHeight
        + (fittingAllFrames ? 2 : 12)
    let contentHeight = fittingAllFrames ? height : max(height, requiredContentHeight)
    let renderState = TimelineRenderState(
        valueMode: store.valueMode,
        palette: store.palette,
        spatialFormat: store.spatialPlotFormat,
        selectedBoundsStartMS: selectedBounds.0,
        selectedBoundsEndMS: selectedBounds.1,
        axisStartMS: axisRange.0,
        axisEndMS: axisRange.1,
        baseBinMS: store.baseBinMS(),
        timeResolutionMS: store.timeResolutionMS,
        hasTimeSelection: hasTimeSelection,
        selectedDisplayRange: selectedDisplayRange,
        timeGroupLabels: timeGroupLabels,
        timeGroupEndBoundsMS: timeGroupEndBoundsMS
    )
    let renderKey = TimelineBaseRenderKey(
        dataID: store.data.map(ObjectIdentifier.init),
        unitIndex: store.unitIndex,
        valueMode: store.valueMode,
        timeGroupSize: store.timeGroupSize(),
        timeResolutionMS: store.timeResolutionMS,
        xBins: store.xBins,
        yBins: store.yBins,
        flipY: store.flipY,
        smoothRadius: store.smoothRadius,
        palette: store.palette,
        spatialFormat: store.spatialPlotFormat,
        polarRadiusMode: store.polarRadiusMode,
        width: width,
        height: height,
        contentHeight: contentHeight
    )
    return TimelineLayout(
        chartRect: chartRect,
        displayBins: displayBins,
        timeGroups: timeGroups,
        miniLayouts: miniLayouts,
        miniMatrices: miniMatrices,
        contentHeight: contentHeight,
        maxTotal: max(timeTotals.max() ?? 0.0, 1.0),
        selectedMax: max(selectedValues?.max() ?? 0.0, 1.0),
        timeTotals: timeTotals,
        selectedValues: selectedValues,
        cellHigh: snapshot.sharedHigh,
        labelGap: miniSpec.labelGap,
        labelHeight: miniSpec.labelHeight,
        miniColumns: miniSpec.cols,
        miniRowStep: miniSpec.rowStep,
        renderState: renderState,
        renderKey: renderKey
    )
}

/// Internal for `@testable` verification that every frame remains inside the
/// finite export page rather than depending on an NSScrollView viewport.
func makeTimelineExportLayout(
    store: RFMappingStore,
    width: CGFloat,
    height: CGFloat
) -> TimelineLayout {
    makeTimelineLayout(
        store: store,
        width: width,
        height: height,
        fittingAllFrames: true
    )
}

private struct TimelineMiniSpec {
    let left: CGFloat
    let cols: Int
    let gapX: CGFloat
    let slotWidth: CGFloat
    let cellWidth: CGFloat
    let cellHeight: CGFloat
    let gridWidth: CGFloat
    let gridHeight: CGFloat
    let labelGap: CGFloat
    let labelHeight: CGFloat
    let rowStep: CGFloat
}

private func timelineMiniSpec(
    width: CGFloat,
    height: CGFloat,
    visibleCount: Int,
    xCount: Int,
    yCount: Int,
    spatialFormat: SpatialPlotFormat,
    miniTop: CGFloat,
    fittingAllFrames: Bool
) -> TimelineMiniSpec {
    let count = max(1, visibleCount)
    let xCount = max(1, xCount)
    let yCount = max(1, yCount)
    let gapX = max(1.0, min(3.0, width * 0.002))
    let labelGap: CGFloat = fittingAllFrames ? 1 : 4
    let labelHeight: CGFloat = fittingAllFrames ? 8 : 12
    let rowGap = fittingAllFrames
        ? max(1.0, min(4.0, height * 0.006))
        : max(10.0, min(16.0, height * 0.014))
    let left: CGFloat = fittingAllFrames ? max(8, min(44, width * 0.12)) : 44
    let rightPadding: CGFloat = left
    let availableWidth = fittingAllFrames
        ? max(4, width - left - rightPadding)
        : max(120, width - left - rightPadding)
    if fittingAllFrames {
        let availableHeight = max(4, height - miniTop - 2)
        var bestColumns = 1
        var bestRows = count
        var bestCellWidth: CGFloat = 0
        var bestCellHeight: CGFloat = 0
        var bestSlotWidth: CGFloat = availableWidth
        var bestGridWidth: CGFloat = 1
        var bestGridHeight: CGFloat = 1

        for candidateColumns in 1...count {
            let candidateRows = Int(ceil(Double(count) / Double(candidateColumns)))
            let slotWidth = max(
                1,
                (availableWidth - CGFloat(candidateColumns - 1) * gapX)
                    / CGFloat(candidateColumns)
            )
            let rowHeight = max(
                1,
                (availableHeight - CGFloat(candidateRows - 1) * rowGap)
                    / CGFloat(candidateRows)
            )
            let usableHeight = max(0.25, rowHeight - labelGap - labelHeight)
            let cellWidth: CGFloat
            let cellHeight: CGFloat
            let gridWidth: CGFloat
            let gridHeight: CGFloat
            if spatialFormat == .polar {
                let diameter = max(0.25, min(slotWidth, usableHeight))
                cellWidth = diameter / CGFloat(max(xCount, yCount))
                cellHeight = cellWidth
                gridWidth = diameter
                gridHeight = diameter
            } else if yCount == 1 {
                let dimensions = spatialGridDimensions(
                    availableWidth: slotWidth,
                    availableHeight: usableHeight,
                    columns: xCount,
                    rows: yCount,
                    minimumCellWidth: 0.001
                )
                cellWidth = dimensions.cellWidth
                cellHeight = dimensions.cellHeight
                gridWidth = dimensions.gridWidth
                gridHeight = dimensions.gridHeight
            } else {
                cellWidth = max(
                    0.001,
                    min(slotWidth / CGFloat(xCount), usableHeight / CGFloat(yCount))
                )
                cellHeight = cellWidth
                gridWidth = cellWidth * CGFloat(xCount)
                gridHeight = cellWidth * CGFloat(yCount)
            }
            if cellWidth > bestCellWidth {
                bestColumns = candidateColumns
                bestRows = candidateRows
                bestCellWidth = cellWidth
                bestCellHeight = cellHeight
                bestSlotWidth = slotWidth
                bestGridWidth = gridWidth
                bestGridHeight = gridHeight
            }
        }
        let rowStep = max(
            bestGridHeight + labelGap + labelHeight,
            (availableHeight - CGFloat(bestRows - 1) * rowGap) / CGFloat(bestRows)
                + rowGap
        )
        return TimelineMiniSpec(
            left: left,
            cols: bestColumns,
            gapX: gapX,
            slotWidth: bestSlotWidth,
            cellWidth: bestCellWidth,
            cellHeight: bestCellHeight,
            gridWidth: bestGridWidth,
            gridHeight: bestGridHeight,
            labelGap: labelGap,
            labelHeight: labelHeight,
            rowStep: rowStep
        )
    }
    let baseGridHeight = min(78.0, max(44.0, height * 0.12))
    let densityScale = min(1.0, max(0.35, sqrt(50.0 / Double(count))))
    let targetGridHeight = max(18.0, baseGridHeight * CGFloat(densityScale))
    let targetCell = targetGridHeight / CGFloat(yCount)
    let targetGridWidth: CGFloat
    if spatialFormat == .polar {
        targetGridWidth = targetGridHeight
    } else if yCount == 1 {
        let aspect = CGFloat(singletonYReferenceColumns) / CGFloat(singletonYReferenceRows)
        targetGridWidth = max(
            targetGridHeight * aspect,
            2.0 * CGFloat(xCount)
        )
    } else {
        targetGridWidth = targetCell * CGFloat(xCount)
    }
    let maxColumns = max(1, Int((availableWidth + gapX) / max(1.0, targetGridWidth + gapX)))
    let cols = min(count, maxColumns)
    let slotWidth = max(1.0, (availableWidth - CGFloat(cols - 1) * gapX) / CGFloat(cols))
    let cellWidth: CGFloat
    let cellHeight: CGFloat
    let gridWidth: CGFloat
    let gridHeight: CGFloat
    if spatialFormat == .polar {
        let diameter = max(18.0, min(targetGridHeight, slotWidth))
        cellWidth = diameter / CGFloat(max(xCount, yCount))
        cellHeight = cellWidth
        gridWidth = diameter
        gridHeight = diameter
    } else if yCount == 1 {
        let aspect = CGFloat(singletonYReferenceColumns) / CGFloat(singletonYReferenceRows)
        var fittedGridWidth = min(targetGridWidth, slotWidth)
        cellWidth = max(2.0, fittedGridWidth / CGFloat(xCount))
        fittedGridWidth = cellWidth * CGFloat(xCount)
        gridWidth = fittedGridWidth
        gridHeight = fittedGridWidth / aspect
        cellHeight = gridHeight
    } else {
        cellWidth = max(2.0, min(targetCell, slotWidth / CGFloat(xCount)))
        cellHeight = cellWidth
        gridWidth = cellWidth * CGFloat(xCount)
        gridHeight = cellWidth * CGFloat(yCount)
    }
    return TimelineMiniSpec(
        left: left,
        cols: cols,
        gapX: gapX,
        slotWidth: slotWidth,
        cellWidth: cellWidth,
        cellHeight: cellHeight,
        gridWidth: gridWidth,
        gridHeight: gridHeight,
        labelGap: labelGap,
        labelHeight: labelHeight,
        rowStep: gridHeight + labelGap + labelHeight + rowGap
    )
}

private func drawTimelineBase(
    context: inout GraphicsContext,
    size: CGSize,
    layout: TimelineLayout
) {
    drawTimelineMiniMapCells(context: &context, layout: layout)
}

private func drawTimelineSelection(
    context: inout GraphicsContext,
    size: CGSize,
    layout: TimelineLayout
) {
    let renderState = layout.renderState
    let negativeWarning = renderState.axisStartMS < 0
        ? " Negative bins may include previous-stimulus responses."
        : ""
    drawTitle(
        context: &context,
        title: "Timeline and \(layout.displayBins) bin maps",
        subtitle: "Full time axis; Timeline highlight \(formatMS(renderState.selectedBoundsStartMS)) to \(formatMS(renderState.selectedBoundsEndMS)) ms; time res \(formatMS(renderState.timeResolutionMS)) ms; \(renderState.spatialFormat.rawValue) maps.\(negativeWarning)"
    )

    drawTimelineChart(context: &context, layout: layout)
    drawTimelineMiniMapFrames(context: &context, layout: layout)
}

private func drawTimelineChart(
    context: inout GraphicsContext,
    layout: TimelineLayout
) {
    let renderState = layout.renderState
    let rect = layout.chartRect
    let axisRange = (renderState.axisStartMS, renderState.axisEndMS)
    let axisSpan = max(axisRange.1 - axisRange.0, renderState.baseBinMS)

    if axisRange.0 < 0 {
        let negativeEnd = min(0.0, axisRange.1)
        let fraction = clamp((negativeEnd - axisRange.0) / axisSpan)
        let shadeRect = CGRect(x: rect.minX, y: rect.minY, width: rect.width * CGFloat(fraction), height: rect.height)
        context.fill(Path(shadeRect), with: .color(Color(nsColor: .windowBackgroundColor).opacity(0.75)))
    }
    context.stroke(Path(rect), with: .color(.secondary.opacity(0.45)), lineWidth: 1)

    if axisRange.0 <= 0, 0 <= axisRange.1 {
        let zeroX = rect.minX + rect.width * CGFloat((0.0 - axisRange.0) / axisSpan)
        var zeroPath = Path()
        zeroPath.move(to: CGPoint(x: zeroX, y: rect.minY))
        zeroPath.addLine(to: CGPoint(x: zeroX, y: rect.maxY))
        context.stroke(
            zeroPath,
            with: .color(.purple),
            style: StrokeStyle(lineWidth: 1, dash: [4, 3])
        )
        context.draw(
            Text("VS 0 ms").font(.system(size: 8, weight: .semibold)).foregroundStyle(.purple),
            at: CGPoint(x: zeroX + 4, y: rect.minY + 5),
            anchor: .topLeading
        )
    }

    drawTimelineLegend(context: &context, layout: layout)
    drawTimelinePath(
        context: &context,
        values: layout.timeTotals,
        high: layout.maxTotal,
        rect: rect,
        color: .blue,
        lineWidth: 2
    )
    if let selectedValues = layout.selectedValues {
        drawTimelinePath(
            context: &context,
            values: selectedValues,
            high: layout.selectedMax,
            rect: rect,
            color: .red,
            lineWidth: 1.8,
            dash: [5, 2]
        )
        drawAxisScale(
            context: &context,
            x: rect.minX - 20,
            rect: rect,
            highLabel: renderState.valueMode.format(layout.selectedMax),
            color: .red,
            leading: true
        )
    }
    drawAxisScale(
        context: &context,
        x: rect.maxX + 20,
        rect: rect,
        highLabel: renderState.valueMode.format(layout.maxTotal),
        color: .blue,
        leading: false
    )

    if renderState.hasTimeSelection {
        let startX = rect.minX
            + rect.width * CGFloat((renderState.selectedBoundsStartMS - axisRange.0) / axisSpan)
        let endX = rect.minX
            + rect.width * CGFloat((renderState.selectedBoundsEndMS - axisRange.0) / axisSpan)
        let rangeRect = CGRect(
            x: min(startX, endX),
            y: rect.minY,
            width: abs(endX - startX),
            height: rect.height
        )
        context.stroke(Path(rangeRect), with: .color(.green), lineWidth: 1)
    }

    let maximumTickIntervals = 5
    let tickStep = max(1, Int(ceil(Double(layout.displayBins) / Double(maximumTickIntervals))))
    var boundaries = Array(stride(from: 0, through: layout.displayBins, by: tickStep))
    if boundaries.last != layout.displayBins {
        boundaries.append(layout.displayBins)
    }
    for boundary in boundaries {
        let x = rect.minX + rect.width * CGFloat(boundary) / CGFloat(layout.displayBins)
        let timeMS = boundary == 0
            ? axisRange.0
            : renderState.timeGroupEndBoundsMS[min(boundary - 1, renderState.timeGroupEndBoundsMS.count - 1)]
        var tick = Path()
        tick.move(to: CGPoint(x: x, y: rect.maxY))
        tick.addLine(to: CGPoint(x: x, y: rect.maxY + 4))
        context.stroke(tick, with: .color(.secondary), lineWidth: 1)
        let anchor: UnitPoint = boundary == 0 ? .leading : (boundary == layout.displayBins ? .trailing : .center)
        context.draw(
            Text(formatMS(timeMS)).font(.system(size: 8)).foregroundStyle(.secondary),
            at: CGPoint(x: x, y: rect.maxY + 17),
            anchor: anchor
        )
    }
    context.draw(
        Text("Time from VS onset (ms)").font(.system(size: 9)).foregroundStyle(.secondary),
        at: CGPoint(x: rect.midX, y: rect.maxY + 36),
        anchor: .center
    )
}

private func drawTimelineLegend(
    context: inout GraphicsContext,
    layout: TimelineLayout
) {
    let y = layout.chartRect.minY - 11
    var blueLine = Path()
    blueLine.move(to: CGPoint(x: layout.chartRect.minX, y: y))
    blueLine.addLine(to: CGPoint(x: layout.chartRect.minX + 16, y: y))
    context.stroke(blueLine, with: .color(.blue), lineWidth: 2)
    let totalLabel = layout.renderState.valueMode == .spikeCount
        ? "All positions (sum)"
        : "All positions (pooled count / occupancy)"
    context.draw(
        Text(totalLabel).font(.system(size: 8)).foregroundStyle(.blue),
        at: CGPoint(x: layout.chartRect.minX + 21, y: y),
        anchor: .leading
    )

    if layout.selectedValues != nil {
        let selectedX = layout.chartRect.minX + 196
        var redLine = Path()
        redLine.move(to: CGPoint(x: selectedX, y: y))
        redLine.addLine(to: CGPoint(x: selectedX + 16, y: y))
        context.stroke(redLine, with: .color(.red), style: StrokeStyle(lineWidth: 2, dash: [5, 2]))
        context.draw(
            Text("Selected cell").font(.system(size: 8)).foregroundStyle(.red),
            at: CGPoint(x: selectedX + 21, y: y),
            anchor: .leading
        )
    }
}

private func drawTimelinePath(
    context: inout GraphicsContext,
    values: [Double],
    high: Double,
    rect: CGRect,
    color: Color,
    lineWidth: CGFloat,
    dash: [CGFloat] = []
) {
    guard !values.isEmpty else { return }
    let points = values.enumerated().map { index, value in
        CGPoint(
            x: rect.minX + rect.width * (CGFloat(index) + 0.5) / CGFloat(values.count),
            y: rect.maxY - rect.height * CGFloat(max(0.0, value) / max(high, 1e-12))
        )
    }
    var path = Path()
    path.move(to: points[0])
    if points.count == 2 {
        path.addLine(to: points[1])
    } else if points.count > 2 {
        for index in 0..<(points.count - 1) {
            let p0 = points[max(0, index - 1)]
            let p1 = points[index]
            let p2 = points[index + 1]
            let p3 = points[min(points.count - 1, index + 2)]
            let control1 = CGPoint(
                x: p1.x + (p2.x - p0.x) / 6,
                y: p1.y + (p2.y - p0.y) / 6
            )
            let control2 = CGPoint(
                x: p2.x - (p3.x - p1.x) / 6,
                y: p2.y - (p3.y - p1.y) / 6
            )
            path.addCurve(to: p2, control1: control1, control2: control2)
        }
    }
    context.stroke(path, with: .color(color), style: StrokeStyle(lineWidth: lineWidth, dash: dash))
}

private func drawAxisScale(
    context: inout GraphicsContext,
    x: CGFloat,
    rect: CGRect,
    highLabel: String,
    color: Color,
    leading: Bool
) {
    var axis = Path()
    axis.move(to: CGPoint(x: x, y: rect.minY))
    axis.addLine(to: CGPoint(x: x, y: rect.maxY))
    context.stroke(axis, with: .color(color), lineWidth: 1)
    let anchor: UnitPoint = leading ? .trailing : .leading
    let textX = leading ? x - 7 : x + 7
    context.draw(
        Text(highLabel).font(.system(size: 8)).foregroundStyle(color),
        at: CGPoint(x: textX, y: rect.minY),
        anchor: anchor
    )
    context.draw(
        Text("0").font(.system(size: 8)).foregroundStyle(color),
        at: CGPoint(x: textX, y: rect.maxY),
        anchor: anchor
    )
}

private func drawTimelineMiniMapCells(
    context: inout GraphicsContext,
    layout: TimelineLayout
) {
    let renderState = layout.renderState
    for mini in layout.miniLayouts {
        guard layout.miniMatrices.indices.contains(mini.bin) else { continue }
        let matrix = layout.miniMatrices[mini.bin]
        if let polar = mini.polarLayout {
            let thetaEdges = (0...polar.xGroups.count).map {
                Double.pi / 180.0
                    * (90.0 + polar.totalDegrees / 2.0
                        - polar.totalDegrees * Double($0) / Double(polar.xGroups.count))
            }
            for (ringIndex, displayY) in polar.ringRows.enumerated() {
                let rInner = CGFloat(innerBlankRows)
                    + CGFloat(ringIndex) * polar.ringSpan
                let rOuter = rInner + polar.ringSpan
                for groupIndex in polar.xGroups.indices {
                    let path = polarCellPath(
                        center: polar.center,
                        scale: polar.scale,
                        rInner: Double(rInner),
                        rOuter: Double(rOuter),
                        thetaStart: thetaEdges[groupIndex],
                        thetaEnd: thetaEdges[groupIndex + 1],
                        arcSegments: polarArcSampleCount(
                            thetaStart: thetaEdges[groupIndex],
                            thetaEnd: thetaEdges[groupIndex + 1]
                        )
                    )
                    context.fill(
                        path,
                        with: .color(
                            paletteColor(
                                matrix[displayY][groupIndex],
                                low: 0,
                                high: layout.cellHigh,
                                palette: renderState.palette
                            )
                        )
                    )
                }
            }
        } else {
            for displayY in matrix.indices {
                for groupIndex in matrix[displayY].indices {
                    let rect = CGRect(
                        x: mini.x0 + CGFloat(groupIndex) * mini.cellWidth,
                        y: mini.y0 + CGFloat(displayY) * mini.cellHeight,
                        width: mini.cellWidth,
                        height: mini.cellHeight
                    )
                    context.fill(
                        Path(rect),
                        with: .color(
                            paletteColor(
                                matrix[displayY][groupIndex],
                                low: 0,
                                high: layout.cellHigh,
                                palette: renderState.palette
                            )
                        )
                    )
                }
            }
        }
    }
}

private func drawTimelineMiniMapFrames(
    context: inout GraphicsContext,
    layout: TimelineLayout
) {
    let renderState = layout.renderState
    for mini in layout.miniLayouts {
        let inSelectedRange = renderState.selectedDisplayRange.map {
            $0.start <= mini.bin && mini.bin <= $0.end
        } ?? false
        let outline = inSelectedRange ? Color.green : Color.secondary.opacity(0.45)
        let lineWidth: CGFloat = inSelectedRange ? 2 : 1
        let framePath: Path
        if let polar = mini.polarLayout {
            let outer = (
                CGFloat(innerBlankRows)
                    + CGFloat(polar.yGroups.count) * polar.ringSpan
            ) * polar.scale
            framePath = Path(ellipseIn: CGRect(
                x: polar.center.x - outer,
                y: polar.center.y - outer,
                width: outer * 2,
                height: outer * 2
            ))
        } else {
            framePath = Path(CGRect(x: mini.x0, y: mini.y0, width: mini.gridWidth, height: mini.gridHeight))
        }
        context.stroke(framePath, with: .color(outline), lineWidth: lineWidth)
        context.draw(
            Text(renderState.timeGroupLabels[mini.bin])
                .font(.system(size: 8, weight: inSelectedRange ? .semibold : .regular))
                .foregroundStyle(inSelectedRange ? Color.green : Color.secondary),
            at: CGPoint(x: mini.x0, y: mini.y0 + mini.gridHeight + layout.labelGap),
            anchor: .topLeading
        )
    }
}

private func drawTimelineHover(
    context: inout GraphicsContext,
    layout: TimelineLayout,
    displayBin: Int,
    cell: CellRef
) {
    guard layout.miniLayouts.indices.contains(displayBin) else { return }
    let mini = layout.miniLayouts[displayBin]
    guard mini.bin == displayBin else { return }
    if let polar = mini.polarLayout, let path = polarPath(for: cell, layout: polar) {
        context.stroke(path, with: .color(.orange), lineWidth: 3)
        return
    }
    guard let xIndex = mini.xGroups.firstIndex(where: {
              $0.start == cell.xStart && $0.end == cell.xEnd
          }),
          let yIndex = mini.yGroups.firstIndex(where: {
              $0.start == cell.yStart && $0.end == cell.yEnd
          }) else {
        return
    }
    let hoverRect = CGRect(
        x: mini.x0 + CGFloat(xIndex) * mini.cellWidth,
        y: mini.y0 + CGFloat(yIndex) * mini.cellHeight,
        width: mini.cellWidth,
        height: mini.cellHeight
    ).insetBy(dx: 1, dy: 1)
    context.stroke(Path(hoverRect), with: .color(.orange), lineWidth: 3)
}

/// Bridges the enclosing NSScrollView's content offset into the per-window
/// store so a Timeline survives tab changes and its tooltip stays inside the
/// visible viewport.
private struct TimelineScrollOffsetTracker: NSViewRepresentable {
    @Binding var fraction: Double

    func makeCoordinator() -> Coordinator {
        Coordinator(fraction: $fraction)
    }

    func makeNSView(context: Context) -> TrackerView {
        let view = TrackerView()
        view.coordinator = context.coordinator
        return view
    }

    func updateNSView(_ nsView: TrackerView, context: Context) {
        context.coordinator.fraction = $fraction
        context.coordinator.attach(to: nsView)
        context.coordinator.restoreIfNeeded()
    }

    static func dismantleNSView(_ nsView: TrackerView, coordinator: Coordinator) {
        coordinator.detach()
    }

    final class TrackerView: NSView {
        weak var coordinator: Coordinator?

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                self.coordinator?.attach(to: self)
                self.coordinator?.restoreIfNeeded()
            }
        }

        override func hitTest(_ point: NSPoint) -> NSView? { nil }
    }

    @MainActor
    final class Coordinator {
        var fraction: Binding<Double>
        private weak var clipView: NSClipView?
        private weak var scrollView: NSScrollView?
        private var observer: NSObjectProtocol?
        private var isRestoring = false

        init(fraction: Binding<Double>) {
            self.fraction = fraction
        }

        func attach(to view: NSView) {
            guard let scrollView = view.enclosingScrollView,
                  scrollView.contentView !== clipView else { return }
            detach()
            self.scrollView = scrollView
            let clip = scrollView.contentView
            clip.postsBoundsChangedNotifications = true
            clipView = clip
            observer = NotificationCenter.default.addObserver(
                forName: NSView.boundsDidChangeNotification,
                object: clip,
                queue: .main
            ) { [weak self] _ in
                MainActor.assumeIsolated {
                    guard let self, let clipView = self.clipView,
                          let documentView = self.scrollView?.documentView,
                          !self.isRestoring else { return }
                    let maximum = max(0, documentView.bounds.height - clipView.bounds.height)
                    if maximum > 0 {
                        self.fraction.wrappedValue = max(
                            0,
                            min(1, Double(clipView.bounds.origin.y / maximum))
                        )
                    }
                }
            }
        }

        func restoreIfNeeded() {
            guard let clipView, let scrollView, let documentView = scrollView.documentView else { return }
            let maximum = max(0, documentView.bounds.height - clipView.bounds.height)
            let requested = maximum * CGFloat(max(0, min(1, fraction.wrappedValue)))
            guard abs(clipView.bounds.origin.y - requested) > 0.5 else { return }
            isRestoring = true
            clipView.scroll(to: CGPoint(x: clipView.bounds.origin.x, y: requested))
            scrollView.reflectScrolledClipView(clipView)
            DispatchQueue.main.async { [weak self] in
                self?.isRestoring = false
            }
        }

        func detach() {
            if let observer { NotificationCenter.default.removeObserver(observer) }
            observer = nil
            clipView = nil
            scrollView = nil
            isRestoring = false
        }

        deinit {
            MainActor.assumeIsolated {
                if let observer { NotificationCenter.default.removeObserver(observer) }
            }
        }
    }
}

private func timelineHit(at point: CGPoint, layout: TimelineLayout) -> TimelineHit? {
    let chart = layout.chartRect
    if chart.contains(point) {
        let binWidth = chart.width / CGFloat(layout.displayBins)
        let bin = max(0, min(layout.displayBins - 1, Int((point.x - chart.minX) / binWidth)))
        return .bin(bin)
    }

    guard let firstMini = layout.miniLayouts.first,
          point.y >= firstMini.y0,
          layout.miniColumns > 0,
          layout.miniRowStep > 0 else {
        return nil
    }
    let row = Int((point.y - firstMini.y0) / layout.miniRowStep)
    let rowStart = row * layout.miniColumns
    guard row >= 0, rowStart < layout.miniLayouts.count else { return nil }
    let rowEnd = min(rowStart + layout.miniColumns, layout.miniLayouts.count)
    for mini in layout.miniLayouts[rowStart..<rowEnd] {
        if let cell = mini.cellRef(at: point) {
            return .cell(mini.bin, cell)
        }
        let labelRect = CGRect(
            x: mini.x0,
            y: mini.y0,
            width: mini.gridWidth,
            height: mini.gridHeight + layout.labelGap + layout.labelHeight
        )
        if labelRect.contains(point) {
            return .bin(mini.bin)
        }
    }
    return nil
}
