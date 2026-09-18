import AppKit
import SwiftUI

struct RGBMapView: View {
    @Bindable var store: RFMappingStore

    var body: some View {
        GeometryReader { proxy in
            let size = proxy.size
            let rgb = store.cachedRGBPlot()
            if store.spatialPlotFormat == .polar {
                let layout = makePolarLayout(size: size, store: store, plot: rgb.reference)
                ZStack(alignment: .topLeading) {
                    Canvas { context, _ in
                        var context = context
                        drawPolarRGB(
                            context: &context,
                            store: store,
                            rgb: rgb,
                            layout: layout
                        )
                    }
                    PolarInteractionLayer(store: store, layout: layout, size: size)
                }
                .accessibilityRepresentation { accessibilityRepresentation(rgb: rgb) }
            } else {
                let layout = makeHeatmapLayout(
                    size: size,
                    plot: rgb.reference,
                    margins: EdgeInsets(top: 56, leading: 78, bottom: 68, trailing: 188)
                )
                ZStack(alignment: .topLeading) {
                    Canvas { context, _ in
                        var context = context
                        drawRGB(
                            context: &context,
                            store: store,
                            rgb: rgb,
                            layout: layout,
                            drawInteraction: false
                        )
                    }
                    RectangularPlotInteractionLayer(store: store, layout: layout, size: size)
                }
                .accessibilityRepresentation { accessibilityRepresentation(rgb: rgb) }
            }
        }
        .background(Color.white)
    }

    private func accessibilityRepresentation(rgb: RGBPlot) -> some View {
        SpatialPlotAccessibilityRepresentation(
            store: store,
            title: "RGB composite; red response, green delay, blue entropy",
            matrix: rgb.total,
            xGroups: rgb.reference.xGroups,
            yGroups: rgb.reference.yGroups,
            valueDescription: { displayY, displayX, value in
                let delay = rgb.delay.indices.contains(displayY)
                    && rgb.delay[displayY].indices.contains(displayX)
                    ? rgb.delay[displayY][displayX]
                    : nil
                let entropy = rgb.entropy.indices.contains(displayY)
                    && rgb.entropy[displayY].indices.contains(displayX)
                    ? rgb.entropy[displayY][displayX]
                    : nil
                let delayText = delay.map { String(format: "%.1f milliseconds", $0) } ?? "none"
                let entropyText = entropy.map { String(format: "%.3f", $0) } ?? "none"
                return "response \(store.valueMode.format(value)) \(store.valueMode.unit), "
                    + "delay \(delayText), entropy \(entropyText)"
            }
        )
    }
}

struct RGBPlot {
    let total: OptionalMatrix
    let delay: OptionalMatrix
    let entropy: OptionalMatrix
    let reference: HeatmapPlot
    let maxTotal: Double
    let minDelay: Double
    let delaySpan: Double
}

private func drawRGB(
    context: inout GraphicsContext,
    store: RFMappingStore,
    rgb: RGBPlot,
    layout: HeatmapLayout,
    drawInteraction: Bool = true
) {
    drawTitle(
        context: &context,
        title: "RGB composite",
        subtitle: "R \(store.valueMode.rawValue); G count-peak delay; B count entropy"
    )

    for displayY in rgb.total.indices {
        for groupIndex in rgb.total[displayY].indices {
            let fill = rgbCellColor(rgb: rgb, displayY: displayY, displayX: groupIndex)
            let rect = CGRect(
                x: layout.x0 + CGFloat(groupIndex) * layout.cellWidth,
                y: layout.y0 + CGFloat(displayY) * layout.cellHeight,
                width: layout.cellWidth,
                height: layout.cellHeight
            )
            context.fill(Path(rect), with: .color(fill))
            if rgb.total[displayY][groupIndex] == nil {
                drawMissingCellMarker(context: &context, center: CGPoint(x: rect.midX, y: rect.midY))
            }
        }
    }

    if drawInteraction {
        drawSelectionAndHover(context: &context, store: store, layout: layout)
    }
    drawAxes(context: &context, store: store, layout: layout)

    let legendX = min(layout.x0 + layout.gridWidth + 34, 10_000)
    let items: [(String, Color)] = [
        ("R \(store.valueMode.unit)", .red),
        ("G delay", .green),
        ("B count entropy", .blue)
    ]
    for (index, item) in items.enumerated() {
        let y = layout.y0 + CGFloat(index * 26)
        context.fill(Path(CGRect(x: legendX, y: y, width: 16, height: 16)), with: .color(item.1))
        context.draw(
            Text(item.0).font(.system(size: 11)).foregroundStyle(.secondary),
            at: CGPoint(x: legendX + 24, y: y + 8),
            anchor: .leading
        )
    }
}

private func drawPolarRGB(
    context: inout GraphicsContext,
    store: RFMappingStore,
    rgb: RGBPlot,
    layout: PolarLayout
) {
    drawTitle(
        context: &context,
        title: "Polar RGB composite",
        subtitle: "R \(store.valueMode.rawValue); G count-peak delay; B count entropy"
    )

    let innerRadius = CGFloat(innerBlankRows) * layout.scale
    let innerCircle = Path(ellipseIn: CGRect(
        x: layout.center.x - innerRadius,
        y: layout.center.y - innerRadius,
        width: innerRadius * 2,
        height: innerRadius * 2
    ))
    context.fill(innerCircle, with: .color(Color.white))
    context.stroke(innerCircle, with: .color(.secondary), lineWidth: 0.5)

    let thetaEdges = (0...layout.xGroups.count).map {
        Double.pi / 180.0
            * (90.0 + layout.totalDegrees / 2.0
                - layout.totalDegrees * Double($0) / Double(layout.xGroups.count))
    }
    for (ringIndex, displayRow) in layout.ringRows.enumerated() {
        let rInner = CGFloat(innerBlankRows) + CGFloat(ringIndex) * layout.ringSpan
        let rOuter = rInner + layout.ringSpan
        for col in layout.xGroups.indices {
            let path = polarCellPath(
                center: layout.center,
                scale: layout.scale,
                rInner: Double(rInner),
                rOuter: Double(rOuter),
                thetaStart: thetaEdges[col],
                thetaEnd: thetaEdges[col + 1]
            )
            context.fill(path, with: .color(rgbCellColor(rgb: rgb, displayY: displayRow, displayX: col)))
            if rgb.total[displayRow][col] == nil {
                let angle = (thetaEdges[col] + thetaEdges[col + 1]) / 2
                let radius = (rInner + rOuter) / 2 * layout.scale
                drawMissingCellMarker(context: &context, center: CGPoint(
                    x: layout.center.x + radius * CGFloat(cos(angle)),
                    y: layout.center.y - radius * CGFloat(sin(angle))
                ))
            }
        }
    }

    let outer = (
        CGFloat(innerBlankRows) + CGFloat(layout.yGroups.count) * layout.ringSpan
    ) * layout.scale
    context.stroke(
        Path(ellipseIn: CGRect(
            x: layout.center.x - outer,
            y: layout.center.y - outer,
            width: outer * 2,
            height: outer * 2
        )),
        with: .color(.secondary),
        lineWidth: 1
    )
    let legendX = layout.center.x + outer + 28
    let legendY = layout.center.y - 34
    for (index, item) in [
        ("R \(store.valueMode.unit)", Color.red),
        ("G delay", Color.green),
        ("B count entropy", Color.blue)
    ].enumerated() {
        let y = legendY + CGFloat(index * 26)
        context.fill(Path(CGRect(x: legendX, y: y, width: 16, height: 16)), with: .color(item.1))
        context.draw(
            Text(item.0).font(.system(size: 11)).foregroundStyle(.secondary),
            at: CGPoint(x: legendX + 24, y: y + 8),
            anchor: .leading
        )
    }
}

private func rgbCellColor(rgb: RGBPlot, displayY: Int, displayX: Int) -> Color {
    guard let totalValue = rgb.total[displayY][displayX], totalValue.isFinite else {
        return Color(red: 0.929, green: 0.941, blue: 0.953)
    }
    guard totalValue > 0 else { return .black }
    let delay = rgb.delay[displayY][displayX]
    let entropy = rgb.entropy[displayY][displayX] ?? 0.0
    return rgbColor(
        red: clamp(totalValue / rgb.maxTotal),
        green: delay.map { clamp(($0 - rgb.minDelay) / rgb.delaySpan) } ?? 0.0,
        blue: clamp(entropy)
    )
}
