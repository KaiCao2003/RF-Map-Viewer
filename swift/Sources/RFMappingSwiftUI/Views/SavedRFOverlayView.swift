import SwiftUI

func savedRFColor(_ hex: String) -> Color {
    let value = UInt64(hex.dropFirst(), radix: 16) ?? 0
    return Color(red: Double((value >> 16) & 255) / 255,
                 green: Double((value >> 8) & 255) / 255,
                 blue: Double(value & 255) / 255)
}

private func overlayColor(_ cell: RFOverlayCell, settings: ViewerDisplayDefaults) -> Color {
    savedRFColor(cell.membership == 3 ? settings.overlayIntersectionColor
                 : cell.membership == 2 ? settings.overlay1DColor : settings.overlay2DColor)
}

/// Keep each detection on its native bin when the response display pools bins.
func savedRFRect(_ cell: RFOverlayCell, layout: HeatmapLayout, flipY: Bool) -> CGRect? {
    guard let column = layout.xGroups.firstIndex(where: { $0.start <= cell.x && cell.x <= $0.end }),
          let row = layout.yGroups.firstIndex(where: { $0.start <= cell.y && cell.y <= $0.end }) else { return nil }
    let x = layout.xGroups[column], y = layout.yGroups[row]
    let width = layout.cellWidth / CGFloat(x.end - x.start + 1)
    let height = layout.cellHeight / CGFloat(y.end - y.start + 1)
    return CGRect(x: layout.x0 + CGFloat(column) * layout.cellWidth + CGFloat(cell.x - x.start) * width,
                  y: layout.y0 + CGFloat(row) * layout.cellHeight + CGFloat(flipY ? y.end - cell.y : cell.y - y.start) * height,
                  width: width, height: height)
}

func drawSavedRFRectangles(context: inout GraphicsContext, store: RFMappingStore, layout: HeatmapLayout, resultOnly: Bool) {
    for cell in store.savedOverlayCells {
        guard let rect = savedRFRect(cell, layout: layout, flipY: store.flipY) else { continue }
        let color = overlayColor(cell, settings: store.displayDefaults)
        if resultOnly { context.fill(Path(rect), with: .color(color)) }
        context.stroke(Path(rect), with: .color(color), lineWidth: store.displayDefaults.overlayWidth)
        if resultOnly && cell.center2D {
            drawSavedCenter(context: &context, at: CGPoint(x: rect.midX, y: rect.midY), triangle: false)
        }
        if resultOnly && cell.center1D {
            let point = cell.oneDAxis == "y"
                ? CGPoint(x: layout.x0 - 7, y: rect.midY)
                : CGPoint(x: rect.midX, y: layout.y0 + layout.gridHeight + 7)
            drawSavedCenter(context: &context, at: point, triangle: true)
        }
    }
}

func drawSavedRFPolar(context: inout GraphicsContext, store: RFMappingStore, layout: PolarLayout, resultOnly: Bool) {
    for cell in store.savedOverlayCells {
        guard let column = layout.xGroups.firstIndex(where: { $0.start <= cell.x && cell.x <= $0.end }),
              let row = layout.yGroups.firstIndex(where: { $0.start <= cell.y && cell.y <= $0.end }),
              let ring = layout.ringRows.firstIndex(of: row) else { continue }
        let x = layout.xGroups[column], y = layout.yGroups[row]
        let xFraction = Double(cell.x - x.start) / Double(x.end - x.start + 1)
        let xWidth = 1 / Double(x.end - x.start + 1)
        let reverse = store.polarRadiusMode == .displayBottomInner ? !store.flipY : false
        let yFraction = Double(reverse ? y.end - cell.y : cell.y - y.start) / Double(y.end - y.start + 1)
        let yWidth = 1 / Double(y.end - y.start + 1)
        let degrees = layout.totalDegrees / Double(layout.xGroups.count)
        let theta1 = (90 + layout.totalDegrees / 2 - degrees * (Double(column) + xFraction)) * .pi / 180
        let theta2 = theta1 - degrees * xWidth * .pi / 180
        let inner = Double(innerBlankRows) + (Double(ring) + yFraction) * Double(layout.ringSpan)
        let outer = inner + yWidth * Double(layout.ringSpan)
        let path = polarCellPath(center: layout.center, scale: layout.scale, rInner: inner, rOuter: outer,
                                 thetaStart: theta1, thetaEnd: theta2,
                                 arcSegments: polarArcSampleCount(thetaStart: theta1, thetaEnd: theta2))
        let color = overlayColor(cell, settings: store.displayDefaults)
        if resultOnly { context.fill(path, with: .color(color)) }
        context.stroke(path, with: .color(color), lineWidth: store.displayDefaults.overlayWidth)
        if resultOnly && (cell.center2D || cell.center1D) {
            let angle = (theta1 + theta2) / 2
            let radius = cell.center1D && !cell.center2D
                ? Double(innerBlankRows) - 0.35 : (inner + outer) / 2
            drawSavedCenter(context: &context, at: CGPoint(
                x: layout.center.x + CGFloat(radius * cos(angle)) * layout.scale,
                y: layout.center.y - CGFloat(radius * sin(angle)) * layout.scale
            ), triangle: cell.center1D && !cell.center2D)
        }
    }
}

private func drawSavedCenter(context: inout GraphicsContext, at point: CGPoint, triangle: Bool) {
    var path = Path()
    if triangle {
        path.move(to: CGPoint(x: point.x, y: point.y - 4))
        path.addLine(to: CGPoint(x: point.x - 4, y: point.y + 4))
        path.addLine(to: CGPoint(x: point.x + 4, y: point.y + 4))
        path.closeSubpath()
    } else {
        path.move(to: CGPoint(x: point.x - 4, y: point.y - 4))
        path.addLine(to: CGPoint(x: point.x + 4, y: point.y + 4))
        path.move(to: CGPoint(x: point.x - 4, y: point.y + 4))
        path.addLine(to: CGPoint(x: point.x + 4, y: point.y - 4))
    }
    context.stroke(path, with: .color(.black), lineWidth: 2)
}
