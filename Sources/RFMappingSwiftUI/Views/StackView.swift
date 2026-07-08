import AppKit
import SwiftUI

struct StackView: View {
    @Bindable var store: RFMappingStore

    var body: some View {
        GeometryReader { proxy in
            Canvas { context, size in
                var context = context
                drawStack(context: &context, size: size, store: store)
            }
        }
        .background(Color(nsColor: .textBackgroundColor))
    }
}

private func drawStack(context: inout GraphicsContext, size: CGSize, store: RFMappingStore) {
    drawTitle(
        context: &context,
        title: "Vertical stack",
        subtitle: "RF, delay, polar, and RGB shown together with the same orientation/resolution"
    )

    let sectionTop: CGFloat = 68
    let sectionGap: CGFloat = 14
    let sectionHeight = max(120, (size.height - sectionTop - 3 * sectionGap - 20) / 4.0)
    let x: CGFloat = 20
    let width = size.width - 40

    drawStackHeatmap(
        context: &context,
        title: "RF - \(store.currentMatrixLabel())",
        matrix: optionalMatrix(store.currentMatrix()),
        x: x,
        y: sectionTop,
        width: width,
        height: sectionHeight,
        palette: store.palette,
        fixedRange: nil,
        store: store
    )

    drawStackHeatmap(
        context: &context,
        title: "Delay",
        matrix: store.delayMatrixForTimeGroups(floor: store.responseFloor),
        x: x,
        y: sectionTop + sectionHeight + sectionGap,
        width: width,
        height: sectionHeight,
        palette: nil,
        fixedRange: store.timeAxisRangeMS(),
        store: store
    )

    drawStackPolar(
        context: &context,
        title: "Polar RF",
        matrix: optionalMatrix(store.currentMatrix()),
        x: x,
        y: sectionTop + 2 * (sectionHeight + sectionGap),
        width: width,
        height: sectionHeight,
        store: store
    )

    drawStackRGB(
        context: &context,
        title: "RGB composite",
        x: x,
        y: sectionTop + 3 * (sectionHeight + sectionGap),
        width: width,
        height: sectionHeight,
        store: store
    )
}

private func drawStackHeatmap(
    context: inout GraphicsContext,
    title: String,
    matrix: OptionalMatrix,
    x: CGFloat,
    y: CGFloat,
    width: CGFloat,
    height: CGFloat,
    palette: RFPalette?,
    fixedRange: (Double, Double)?,
    store: RFMappingStore
) {
    let plot = makeHeatmapPlot(store: store, matrix: matrix, fixedRange: fixedRange)
    let labelWidth: CGFloat = 110
    let plotWidth = width - labelWidth - 40
    let cell = max(2, min(plotWidth / CGFloat(max(1, plot.xGroups.count)), (height - 32) / CGFloat(max(1, plot.yGroups.count))))
    let x0 = x + labelWidth
    let y0 = y + 26

    context.draw(
        Text(title).font(.system(size: 11, weight: .semibold)).foregroundStyle(.primary),
        at: CGPoint(x: x, y: y),
        anchor: .topLeading
    )
    for displayY in plot.matrix.indices {
        for groupIndex in plot.matrix[displayY].indices {
            let value = plot.matrix[displayY][groupIndex]
            let fill = palette.map { paletteColor(value, low: plot.low, high: plot.high, palette: $0) }
                ?? delayColor(value, low: plot.low, high: plot.high)
            context.fill(
                Path(CGRect(x: x0 + CGFloat(groupIndex) * cell, y: y0 + CGFloat(displayY) * cell, width: cell, height: cell)),
                with: .color(fill)
            )
        }
    }
    let gridWidth = cell * CGFloat(plot.xGroups.count)
    let gridHeight = cell * CGFloat(plot.yGroups.count)
    context.stroke(Path(CGRect(x: x0, y: y0, width: gridWidth, height: gridHeight)), with: .color(.secondary), lineWidth: 1)
    context.draw(Text(String(format: "%.1f", plot.high)).font(.system(size: 8)).foregroundStyle(.secondary), at: CGPoint(x: x0 + gridWidth + 10, y: y0), anchor: .topLeading)
    context.draw(Text(String(format: "%.1f", plot.low)).font(.system(size: 8)).foregroundStyle(.secondary), at: CGPoint(x: x0 + gridWidth + 10, y: y0 + gridHeight - 10), anchor: .topLeading)
}

private func drawStackPolar(
    context: inout GraphicsContext,
    title: String,
    matrix: OptionalMatrix,
    x: CGFloat,
    y: CGFloat,
    width: CGFloat,
    height: CGFloat,
    store: RFMappingStore
) {
    let plot = makeHeatmapPlot(store: store, matrix: matrix)
    let totalDegrees = store.data?.inferTotalDeg() ?? 360.0
    let rowCount = max(1, plot.yGroups.count)
    let radiusUnits = Double(innerBlankRows + rowCount + polarPadRows)
    let scale = max(3.0, min((width - 130.0) / CGFloat(2.0 * radiusUnits), (height - 30.0) / CGFloat(2.0 * radiusUnits)))
    let center = CGPoint(x: x + width * 0.5, y: y + height * 0.54)
    let ringRows = store.polarRadiusMode == .matlabRowOneInner
        ? Array(0..<rowCount).sorted { plot.yGroups[$0].start < plot.yGroups[$1].start }
        : Array((0..<rowCount).reversed())
    let thetaEdges = (0...plot.xGroups.count).map {
        Double.pi / 180.0 * (90.0 + totalDegrees / 2.0 - totalDegrees * Double($0) / Double(plot.xGroups.count))
    }

    context.draw(Text(title).font(.system(size: 11, weight: .semibold)).foregroundStyle(.primary), at: CGPoint(x: x, y: y), anchor: .topLeading)
    for (ringIndex, displayRow) in ringRows.enumerated() {
        for col in plot.xGroups.indices {
            let path = polarCellPath(
                center: center,
                scale: scale,
                rInner: Double(innerBlankRows + ringIndex),
                rOuter: Double(innerBlankRows + ringIndex + 1),
                thetaStart: thetaEdges[col],
                thetaEnd: thetaEdges[col + 1]
            )
            context.fill(path, with: .color(paletteColor(plot.matrix[displayRow][col], low: plot.low, high: plot.high, palette: store.palette)))
        }
    }
    let outer = CGFloat(innerBlankRows + rowCount) * scale
    context.stroke(Path(ellipseIn: CGRect(x: center.x - outer, y: center.y - outer, width: outer * 2, height: outer * 2)), with: .color(.secondary), lineWidth: 1)
}

private func drawStackRGB(
    context: inout GraphicsContext,
    title: String,
    x: CGFloat,
    y: CGFloat,
    width: CGFloat,
    height: CGFloat,
    store: RFMappingStore
) {
    let rgb = makeRGBPlot(store: store)
    let labelWidth: CGFloat = 110
    let plotWidth = width - labelWidth - 40
    let cell = max(2, min(plotWidth / CGFloat(max(1, rgb.reference.xGroups.count)), (height - 32) / CGFloat(max(1, rgb.reference.yGroups.count))))
    let x0 = x + labelWidth
    let y0 = y + 26

    context.draw(Text(title).font(.system(size: 11, weight: .semibold)).foregroundStyle(.primary), at: CGPoint(x: x, y: y), anchor: .topLeading)
    for displayY in rgb.total.indices {
        for groupIndex in rgb.total[displayY].indices {
            let totalValue = rgb.total[displayY][groupIndex] ?? 0.0
            let fill: Color
            if totalValue <= 0 {
                fill = Color(red: 0.929, green: 0.941, blue: 0.953)
            } else {
                let delay = rgb.delay[displayY][groupIndex]
                fill = rgbColor(
                    red: clamp(totalValue / rgb.maxTotal),
                    green: delay.map { clamp(($0 - rgb.minDelay) / rgb.delaySpan) } ?? 0.0,
                    blue: clamp(rgb.entropy[displayY][groupIndex] ?? 0.0)
                )
            }
            context.fill(Path(CGRect(x: x0 + CGFloat(groupIndex) * cell, y: y0 + CGFloat(displayY) * cell, width: cell, height: cell)), with: .color(fill))
        }
    }
    context.stroke(
        Path(CGRect(x: x0, y: y0, width: cell * CGFloat(rgb.reference.xGroups.count), height: cell * CGFloat(rgb.reference.yGroups.count))),
        with: .color(.secondary),
        lineWidth: 1
    )
}
