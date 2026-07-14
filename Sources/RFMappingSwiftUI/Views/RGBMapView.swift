import AppKit
import SwiftUI

struct RGBMapView: View {
    @Bindable var store: RFMappingStore

    var body: some View {
        GeometryReader { proxy in
            let size = proxy.size
            let rgb = makeRGBPlot(store: store)
            let layout = makeHeatmapLayout(
                size: size,
                plot: rgb.reference,
                margins: EdgeInsets(top: 56, leading: 78, bottom: 68, trailing: 188)
            )

            ZStack(alignment: .topLeading) {
                Canvas { context, _ in
                    var context = context
                    drawRGB(context: &context, store: store, rgb: rgb, layout: layout)
                }
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
                    onLeave: {
                        store.clearHover()
                    }
                )
                if let cell = store.hoverCell, let location = store.hoverLocation {
                    PlotTooltip(text: store.tooltipText(cell), location: location, canvasSize: size)
                }
            }
            .accessibilityRepresentation {
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
        .background(Color(nsColor: .textBackgroundColor))
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

func makeRGBPlot(store: RFMappingStore) -> RGBPlot {
    guard let data = store.data else {
        let empty = HeatmapPlot(matrix: [], xGroups: [], yGroups: [], low: 0, high: 1)
        return RGBPlot(total: [], delay: [], entropy: [], reference: empty, maxTotal: 1, minDelay: 0, delaySpan: 1)
    }
    let metrics = data.metrics(for: store.unitIndex)
    let fullWindowResponse = (try? data.responseMatrix(
        unitIndex: store.unitIndex,
        start: 0,
        end: data.nBins - 1,
        valueMode: store.valueMode
    )) ?? []
    let totalPrepared = store.preparePlotMatrix(fullWindowResponse)
    let delayPrepared = store.preparePlotMatrix(store.delayMatrixForTimeGroups(floor: 0.0))
    let entropyPrepared = store.preparePlotMatrix(optionalMatrix(metrics.entropy))
    let responseRange = finiteMinMax(totalPrepared.0)
    let maxResponse = max(responseRange.1, 1.0)
    let reference = HeatmapPlot(
        matrix: totalPrepared.0,
        xGroups: totalPrepared.1,
        yGroups: totalPrepared.2,
        low: 0,
        high: maxResponse
    )
    let range = store.timeAxisRangeMS()
    return RGBPlot(
        total: totalPrepared.0,
        delay: delayPrepared.0,
        entropy: entropyPrepared.0,
        reference: reference,
        maxTotal: maxResponse,
        minDelay: range.0,
        delaySpan: max(range.1 - range.0, 1.0)
    )
}

private func drawRGB(context: inout GraphicsContext, store: RFMappingStore, rgb: RGBPlot, layout: HeatmapLayout) {
    drawTitle(
        context: &context,
        title: "RGB composite",
        subtitle: "R \(store.valueMode.rawValue); G count-peak delay; B count entropy"
    )

    for displayY in rgb.total.indices {
        for groupIndex in rgb.total[displayY].indices {
            let totalValue = rgb.total[displayY][groupIndex] ?? 0.0
            let fill: Color
            if totalValue <= 0 {
                fill = Color(red: 0.929, green: 0.941, blue: 0.953)
            } else {
                let delay = rgb.delay[displayY][groupIndex]
                let entropy = rgb.entropy[displayY][groupIndex] ?? 0.0
                fill = rgbColor(
                    red: clamp(totalValue / rgb.maxTotal),
                    green: delay.map { clamp(($0 - rgb.minDelay) / rgb.delaySpan) } ?? 0.0,
                    blue: clamp(entropy)
                )
            }
            let rect = CGRect(
                x: layout.x0 + CGFloat(groupIndex) * layout.cell,
                y: layout.y0 + CGFloat(displayY) * layout.cell,
                width: layout.cell,
                height: layout.cell
            )
            context.fill(Path(rect), with: .color(fill))
        }
    }

    drawSelectionAndHover(context: &context, store: store, layout: layout)
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
