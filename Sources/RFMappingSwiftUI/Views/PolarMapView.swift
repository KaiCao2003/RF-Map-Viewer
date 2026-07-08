import AppKit
import SwiftUI

struct PolarMapView: View {
    @Bindable var store: RFMappingStore

    var body: some View {
        GeometryReader { proxy in
            let size = proxy.size
            let plot = makeHeatmapPlot(store: store, matrix: optionalMatrix(store.currentMatrix()))
            let layout = makePolarLayout(size: size, store: store, plot: plot)

            ZStack(alignment: .topLeading) {
                Canvas { context, _ in
                    var context = context
                    drawPolar(context: &context, store: store, plot: plot, layout: layout)
                }
                PointerCaptureView(
                    onMove: { point in
                        if let hit = polarCell(at: point, layout: layout) {
                            store.setHover(hit.cell, location: point, extra: "polar ring \(hit.ring + 1)")
                        } else {
                            store.clearHover()
                        }
                    },
                    onClick: { point, _ in
                        if let hit = polarCell(at: point, layout: layout) {
                            store.selectCell(hit.cell)
                        }
                    },
                    onLeave: {
                        store.clearHover()
                    }
                )
                if let cell = store.hoverCell, let location = store.hoverLocation {
                    PlotTooltip(text: store.tooltipText(cell), location: location, canvasSize: size)
                }
            }
        }
        .background(Color(nsColor: .textBackgroundColor))
    }
}

private struct PolarLayout {
    let center: CGPoint
    let scale: CGFloat
    let totalDegrees: Double
    let xGroups: [AxisGroup]
    let yGroups: [AxisGroup]
    let ringRows: [Int]
}

private func makePolarLayout(size: CGSize, store: RFMappingStore, plot: HeatmapPlot) -> PolarLayout {
    let totalDegrees = store.data?.inferTotalDeg() ?? 360.0
    let rowCount = max(1, plot.yGroups.count)
    let radiusUnits = Double(innerBlankRows + rowCount + polarPadRows)
    let scale = max(4.0, min((size.width - 180.0) / CGFloat(2.0 * radiusUnits), (size.height - 130.0) / CGFloat(2.0 * radiusUnits)))
    let center = CGPoint(x: size.width / 2.0, y: size.height / 2.0 + 22.0)
    let ringRows: [Int]
    if store.polarRadiusMode == .matlabRowOneInner {
        ringRows = Array(0..<rowCount).sorted { plot.yGroups[$0].start < plot.yGroups[$1].start }
    } else {
        ringRows = Array((0..<rowCount).reversed())
    }
    return PolarLayout(center: center, scale: scale, totalDegrees: totalDegrees, xGroups: plot.xGroups, yGroups: plot.yGroups, ringRows: ringRows)
}

private func drawPolar(context: inout GraphicsContext, store: RFMappingStore, plot: HeatmapPlot, layout: PolarLayout) {
    drawTitle(
        context: &context,
        title: "Polar RF map - \(store.currentMatrixLabel())",
        subtitle: "total_deg inferred: \(String(format: "%.0f", layout.totalDegrees)); radius: \(store.polarRadiusMode.rawValue)"
    )

    let innerRadius = CGFloat(innerBlankRows) * layout.scale
    context.fill(
        Path(ellipseIn: CGRect(x: layout.center.x - innerRadius, y: layout.center.y - innerRadius, width: innerRadius * 2, height: innerRadius * 2)),
        with: .color(Color(nsColor: .controlBackgroundColor))
    )
    context.stroke(
        Path(ellipseIn: CGRect(x: layout.center.x - innerRadius, y: layout.center.y - innerRadius, width: innerRadius * 2, height: innerRadius * 2)),
        with: .color(.secondary),
        lineWidth: 0.5
    )

    let thetaEdges = (0...layout.xGroups.count).map {
        Double.pi / 180.0 * (90.0 + layout.totalDegrees / 2.0 - layout.totalDegrees * Double($0) / Double(layout.xGroups.count))
    }

    for (ringIndex, displayRow) in layout.ringRows.enumerated() {
        let rInner = Double(innerBlankRows + ringIndex)
        let rOuter = Double(innerBlankRows + ringIndex + 1)
        for col in layout.xGroups.indices {
            let value = plot.matrix[displayRow][col]
            let fill = paletteColor(value, low: plot.low, high: plot.high, palette: store.palette)
            let path = polarCellPath(
                center: layout.center,
                scale: layout.scale,
                rInner: rInner,
                rOuter: rOuter,
                thetaStart: thetaEdges[col],
                thetaEnd: thetaEdges[col + 1]
            )
            context.fill(path, with: .color(fill))
        }
    }

    if let selected = store.selectedCell, let selectedPath = polarPath(for: selected, layout: layout) {
        context.stroke(selectedPath, with: .color(.primary), lineWidth: 2)
    }
    if let hover = store.hoverCell, let hoverPath = polarPath(for: hover, layout: layout) {
        context.stroke(hoverPath, with: .color(.orange), lineWidth: 3)
    }

    let outer = CGFloat(innerBlankRows + layout.yGroups.count) * layout.scale
    context.stroke(
        Path(ellipseIn: CGRect(x: layout.center.x - outer, y: layout.center.y - outer, width: outer * 2, height: outer * 2)),
        with: .color(.secondary),
        lineWidth: 1
    )
    context.draw(
        Text("x columns span visual angle").font(.system(size: 11)).foregroundStyle(.secondary),
        at: CGPoint(x: layout.center.x, y: layout.center.y - outer - 18),
        anchor: .center
    )
    context.draw(
        Text("RF values share the 2D map color scale").font(.system(size: 11)).foregroundStyle(.secondary),
        at: CGPoint(x: layout.center.x, y: layout.center.y + outer + 22),
        anchor: .center
    )
    drawColorbar(
        context: &context,
        x: layout.center.x + outer + 34,
        y: layout.center.y - min(220, outer * 2) / 2,
        height: min(220, outer * 2),
        low: plot.low,
        high: plot.high,
        palette: store.palette,
        suffix: ""
    )
}

func polarCellPath(center: CGPoint, scale: CGFloat, rInner: Double, rOuter: Double, thetaStart: Double, thetaEnd: Double) -> Path {
    let nArc = 16
    var points: [CGPoint] = []
    for index in 0..<nArc {
        let t = thetaStart + (thetaEnd - thetaStart) * Double(index) / Double(nArc - 1)
        points.append(CGPoint(x: center.x + CGFloat(rOuter * cos(t)) * scale, y: center.y - CGFloat(rOuter * sin(t)) * scale))
    }
    for index in stride(from: nArc - 1, through: 0, by: -1) {
        let t = thetaStart + (thetaEnd - thetaStart) * Double(index) / Double(nArc - 1)
        points.append(CGPoint(x: center.x + CGFloat(rInner * cos(t)) * scale, y: center.y - CGFloat(rInner * sin(t)) * scale))
    }
    var path = Path()
    guard let first = points.first else { return path }
    path.move(to: first)
    for point in points.dropFirst() {
        path.addLine(to: point)
    }
    path.closeSubpath()
    return path
}

private func polarPath(for cell: CellRef, layout: PolarLayout) -> Path? {
    guard let displayRow = layout.yGroups.firstIndex(where: { $0.start <= cell.yStart && cell.yStart <= $0.end }),
          let ringIndex = layout.ringRows.firstIndex(of: displayRow),
          let col = layout.xGroups.firstIndex(where: { $0.start <= cell.xStart && cell.xStart <= $0.end }) else {
        return nil
    }
    let thetaStart = Double.pi / 180.0 * (90.0 + layout.totalDegrees / 2.0 - layout.totalDegrees * Double(col) / Double(layout.xGroups.count))
    let thetaEnd = Double.pi / 180.0 * (90.0 + layout.totalDegrees / 2.0 - layout.totalDegrees * Double(col + 1) / Double(layout.xGroups.count))
    return polarCellPath(
        center: layout.center,
        scale: layout.scale,
        rInner: Double(innerBlankRows + ringIndex),
        rOuter: Double(innerBlankRows + ringIndex + 1),
        thetaStart: thetaStart,
        thetaEnd: thetaEnd
    )
}

private func polarCell(at point: CGPoint, layout: PolarLayout) -> (ring: Int, cell: CellRef)? {
    let dx = Double((point.x - layout.center.x) / layout.scale)
    let dy = Double((layout.center.y - point.y) / layout.scale)
    let radius = hypot(dx, dy)
    guard radius >= Double(innerBlankRows), radius < Double(innerBlankRows + layout.yGroups.count) else {
        return nil
    }
    let ring = Int(floor(radius - Double(innerBlankRows)))
    guard layout.ringRows.indices.contains(ring) else { return nil }
    let displayRow = layout.ringRows[ring]
    var thetaDegrees = atan2(dy, dx) * 180.0 / Double.pi
    let start = 90.0 + layout.totalDegrees / 2.0
    let col: Int
    if layout.totalDegrees >= 359.999 {
        let rel = (start - thetaDegrees).truncatingRemainder(dividingBy: 360.0)
        col = Int(rel / (layout.totalDegrees / Double(layout.xGroups.count)))
    } else {
        let end = 90.0 - layout.totalDegrees / 2.0
        while thetaDegrees > start { thetaDegrees -= 360.0 }
        while thetaDegrees < end { thetaDegrees += 360.0 }
        guard end <= thetaDegrees && thetaDegrees <= start else { return nil }
        col = Int((start - thetaDegrees) / (layout.totalDegrees / Double(layout.xGroups.count)))
    }
    let safeCol = max(0, min(layout.xGroups.count - 1, col))
    let yGroup = layout.yGroups[displayRow]
    let xGroup = layout.xGroups[safeCol]
    return (ring, CellRef(yStart: yGroup.start, yEnd: yGroup.end, xStart: xGroup.start, xEnd: xGroup.end))
}
