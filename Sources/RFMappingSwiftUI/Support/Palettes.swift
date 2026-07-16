import SwiftUI

func paletteColor(_ value: Double?, low: Double, high: Double, palette: RFPalette) -> Color {
    guard let value, value.isFinite else {
        return Color(red: 0.902, green: 0.910, blue: 0.922)
    }
    let t = clamp((value - low) / (high - low == 0 ? 1.0 : high - low))
    switch palette {
    case .gray:
        let shade = (18.0 + t * 232.0) / 255.0
        return Color(red: shade, green: shade, blue: shade)
    case .inferno:
        return gradientColor(
            t,
            stops: [
                (0.0, RGB(22, 11, 57)),
                (0.25, RGB(90, 18, 110)),
                (0.50, RGB(190, 54, 85)),
                (0.75, RGB(249, 140, 10)),
                (1.0, RGB(252, 255, 164))
            ]
        )
    case .viridis:
        return gradientColor(
            t,
            stops: [
                (0.0, RGB(68, 1, 84)),
                (0.25, RGB(59, 82, 139)),
                (0.50, RGB(33, 145, 140)),
                (0.75, RGB(94, 201, 98)),
                (1.0, RGB(253, 231, 37))
            ]
        )
    }
}

func delayColor(_ value: Double?, low: Double = 0.0, high: Double = 100.0) -> Color {
    guard let value else {
        return Color(red: 0.925, green: 0.937, blue: 0.949)
    }
    let t = clamp((value - low) / (high - low == 0 ? 1.0 : high - low))
    return gradientColor(
        t,
        stops: [
            (0.0, RGB(47, 88, 167)),
            (0.35, RGB(44, 171, 184)),
            (0.68, RGB(246, 204, 89)),
            (1.0, RGB(203, 71, 45))
        ]
    )
}

func rgbColor(red: Double, green: Double, blue: Double) -> Color {
    Color(red: clamp(red), green: clamp(green), blue: clamp(blue))
}

private struct RGB {
    let r: Double
    let g: Double
    let b: Double

    init(_ r: Int, _ g: Int, _ b: Int) {
        self.r = Double(r)
        self.g = Double(g)
        self.b = Double(b)
    }
}

private func gradientColor(_ t: Double, stops: [(Double, RGB)]) -> Color {
    let t = clamp(t)
    for index in 0..<(stops.count - 1) {
        let left = stops[index]
        let right = stops[index + 1]
        guard left.0 <= t && t <= right.0 else { continue }
        let local = (t - left.0) / (right.0 - left.0 == 0 ? 1.0 : right.0 - left.0)
        return Color(
            red: (left.1.r + (right.1.r - left.1.r) * local) / 255.0,
            green: (left.1.g + (right.1.g - left.1.g) * local) / 255.0,
            blue: (left.1.b + (right.1.b - left.1.b) * local) / 255.0
        )
    }
    let last = stops[stops.count - 1].1
    return Color(red: last.r / 255.0, green: last.g / 255.0, blue: last.b / 255.0)
}
