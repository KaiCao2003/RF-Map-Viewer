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
                .frame(width: outerSize.width, height: layout.contentHeight)
                .accessibilityRepresentation {
                    TimelineAccessibilityRepresentation(store: store, layout: layout)
                }
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
    let cell: CGFloat
    let gridWidth: CGFloat
    let gridHeight: CGFloat
    let xGroups: [AxisGroup]
    let yGroups: [AxisGroup]

    func cellRef(at point: CGPoint) -> CellRef? {
        guard x0 <= point.x, point.x < x0 + gridWidth,
              y0 <= point.y, point.y < y0 + gridHeight else {
            return nil
        }
        let groupIndex = Int((point.x - x0) / cell)
        let displayY = Int((point.y - y0) / cell)
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
}

private func makeTimelineLayout(store: RFMappingStore, width: CGFloat, height: CGFloat) -> TimelineLayout {
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

    let chartRect = CGRect(x: 64, y: 78, width: max(320, width - 140), height: 62)
    let miniTop = chartRect.maxY + 54
    let miniSpec = timelineMiniSpec(
        width: width,
        height: height,
        visibleCount: visibleBins.count,
        xCount: xGroups.count,
        yCount: yGroups.count
    )
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
                cell: miniSpec.cell,
                gridWidth: miniSpec.gridWidth,
                gridHeight: miniSpec.gridHeight,
                xGroups: xGroups,
                yGroups: yGroups
            )
        )
    }

    let rows = Int(ceil(Double(max(1, visibleBins.count)) / Double(max(1, miniSpec.cols))))
    let contentHeight = max(
        height,
        miniTop
            + CGFloat(max(0, rows - 1)) * miniSpec.rowStep
            + miniSpec.gridHeight
            + miniSpec.labelGap
            + miniSpec.labelHeight
            + 12
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
        labelHeight: miniSpec.labelHeight
    )
}

private struct TimelineMiniSpec {
    let left: CGFloat
    let cols: Int
    let gapX: CGFloat
    let slotWidth: CGFloat
    let cell: CGFloat
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
    yCount: Int
) -> TimelineMiniSpec {
    let count = max(1, visibleCount)
    let xCount = max(1, xCount)
    let yCount = max(1, yCount)
    let gapX = max(1.0, min(3.0, width * 0.002))
    let labelGap: CGFloat = 4
    let labelHeight: CGFloat = 12
    let rowGap = max(10.0, min(16.0, height * 0.014))
    let left: CGFloat = 44
    let rightPadding: CGFloat = 44
    let availableWidth = max(120, width - left - rightPadding)
    let baseGridHeight = min(78.0, max(44.0, height * 0.12))
    let densityScale = min(1.0, max(0.35, sqrt(50.0 / Double(count))))
    let targetGridHeight = max(18.0, baseGridHeight * CGFloat(densityScale))
    let targetCell = targetGridHeight / CGFloat(yCount)
    let targetGridWidth = targetCell * CGFloat(xCount)
    let maxColumns = max(1, Int((availableWidth + gapX) / max(1.0, targetGridWidth + gapX)))
    let cols = min(count, maxColumns)
    let slotWidth = max(1.0, (availableWidth - CGFloat(cols - 1) * gapX) / CGFloat(cols))
    let cell = max(2.0, min(targetCell, slotWidth / CGFloat(xCount)))
    let gridWidth = cell * CGFloat(xCount)
    let gridHeight = cell * CGFloat(yCount)
    return TimelineMiniSpec(
        left: left,
        cols: cols,
        gapX: gapX,
        slotWidth: slotWidth,
        cell: cell,
        gridWidth: gridWidth,
        gridHeight: gridHeight,
        labelGap: labelGap,
        labelHeight: labelHeight,
        rowStep: gridHeight + labelGap + labelHeight + rowGap
    )
}

private func drawTimeline(
    context: inout GraphicsContext,
    size: CGSize,
    store: RFMappingStore,
    layout: TimelineLayout
) {
    let selectedBounds = store.selectedTimeBoundsMS()
    let negativeWarning = store.timeAxisStartMS() < 0
        ? " Negative bins may include previous-stimulus responses."
        : ""
    drawTitle(
        context: &context,
        title: "Timeline and \(layout.displayBins) bin maps",
        subtitle: "RF time range \(formatMS(selectedBounds.0)) to \(formatMS(selectedBounds.1)) ms; time res \(formatMS(store.timeResolutionMS)) ms; \(store.valueMode.rawValue).\(negativeWarning)"
    )

    drawTimelineChart(context: &context, store: store, layout: layout)
    drawTimelineMiniMaps(context: &context, store: store, layout: layout)
}

private func drawTimelineChart(
    context: inout GraphicsContext,
    store: RFMappingStore,
    layout: TimelineLayout
) {
    let rect = layout.chartRect
    let axisRange = store.timeAxisRangeMS()
    let axisSpan = max(axisRange.1 - axisRange.0, store.baseBinMS())

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

    drawTimelineLegend(context: &context, store: store, layout: layout)
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
            highLabel: store.valueMode.format(layout.selectedMax),
            color: .red,
            leading: true
        )
    }
    drawAxisScale(
        context: &context,
        x: rect.maxX + 20,
        rect: rect,
        highLabel: store.valueMode.format(layout.maxTotal),
        color: .blue,
        leading: false
    )

    if store.hasTimeSelection {
        let bounds = store.selectedTimeBoundsMS()
        let startX = rect.minX + rect.width * CGFloat((bounds.0 - axisRange.0) / axisSpan)
        let endX = rect.minX + rect.width * CGFloat((bounds.1 - axisRange.0) / axisSpan)
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
        let timeMS = boundary == 0 ? axisRange.0 : store.timeGroupBoundsMS(boundary - 1).1
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
    store: RFMappingStore,
    layout: TimelineLayout
) {
    let y = layout.chartRect.minY - 11
    var blueLine = Path()
    blueLine.move(to: CGPoint(x: layout.chartRect.minX, y: y))
    blueLine.addLine(to: CGPoint(x: layout.chartRect.minX + 16, y: y))
    context.stroke(blueLine, with: .color(.blue), lineWidth: 2)
    let totalLabel = store.valueMode == .spikeCount ? "All positions (sum)" : "All positions (weighted mean)"
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

private func drawTimelineMiniMaps(
    context: inout GraphicsContext,
    store: RFMappingStore,
    layout: TimelineLayout
) {
    for mini in layout.miniLayouts {
        guard layout.miniMatrices.indices.contains(mini.bin) else { continue }
        let matrix = layout.miniMatrices[mini.bin]
        for displayY in matrix.indices {
            for groupIndex in matrix[displayY].indices {
                let rect = CGRect(
                    x: mini.x0 + CGFloat(groupIndex) * mini.cell,
                    y: mini.y0 + CGFloat(displayY) * mini.cell,
                    width: mini.cell,
                    height: mini.cell
                )
                context.fill(
                    Path(rect),
                    with: .color(
                        paletteColor(
                            matrix[displayY][groupIndex],
                            low: 0,
                            high: layout.cellHigh,
                            palette: store.palette
                        )
                    )
                )
            }
        }

        let group = layout.timeGroups[mini.bin]
        let inSelectedRange = store.hasTimeSelection && store.selectedRangeOverlaps(group)
        let outline = inSelectedRange ? Color.green : Color.secondary.opacity(0.45)
        let lineWidth: CGFloat = inSelectedRange ? 2 : 1
        context.stroke(
            Path(CGRect(x: mini.x0, y: mini.y0, width: mini.gridWidth, height: mini.gridHeight)),
            with: .color(outline),
            lineWidth: lineWidth
        )
        if store.hoverDisplayBin == mini.bin,
           let hover = store.hoverCell,
           let xIndex = mini.xGroups.firstIndex(where: { $0.start == hover.xStart && $0.end == hover.xEnd }),
           let yIndex = mini.yGroups.firstIndex(where: { $0.start == hover.yStart && $0.end == hover.yEnd }) {
            let hoverRect = CGRect(
                x: mini.x0 + CGFloat(xIndex) * mini.cell,
                y: mini.y0 + CGFloat(yIndex) * mini.cell,
                width: mini.cell,
                height: mini.cell
            ).insetBy(dx: 1, dy: 1)
            context.stroke(Path(hoverRect), with: .color(.orange), lineWidth: 3)
        }
        context.draw(
            Text(store.timeGroupLabel(mini.bin))
                .font(.system(size: 8, weight: inSelectedRange ? .semibold : .regular))
                .foregroundStyle(inSelectedRange ? Color.green : Color.secondary),
            at: CGPoint(x: mini.x0, y: mini.y0 + mini.gridHeight + layout.labelGap),
            anchor: .topLeading
        )
    }
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
                          let documentView = self.scrollView?.documentView else { return }
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
            clipView.scroll(to: CGPoint(x: clipView.bounds.origin.x, y: requested))
            scrollView.reflectScrolledClipView(clipView)
        }

        func detach() {
            if let observer { NotificationCenter.default.removeObserver(observer) }
            observer = nil
            clipView = nil
            scrollView = nil
        }

        deinit {
            if let observer { NotificationCenter.default.removeObserver(observer) }
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

    for mini in layout.miniLayouts {
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
