import Foundation
import SwiftUI
import TuningCurveCore

struct StyledComparisonCurve: Identifiable {
    let slot: ComparisonSlot
    let sourceName: String
    let curve: ProcessedHDCurve
    let color: Color
    let angleOffset: Double

    var id: ComparisonSlot { slot }
}

private struct TuningSample {
    let angle: Double
    let rate: Double
}

struct TuningLineChart: View {
    let curves: [StyledComparisonCurve]
    let rateMaximum: Double

    var body: some View {
        Canvas { context, size in
            drawLineChart(
                context: &context,
                size: size,
                curves: curves,
                rateMaximum: rateMaximum
            )
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Angular tuning comparison")
        .accessibilityValue(accessibilitySummary(curves))
    }
}

struct TuningPolarChart: View {
    let curves: [StyledComparisonCurve]
    let rateMaximum: Double

    var body: some View {
        Canvas { context, size in
            drawPolarChart(
                context: &context,
                size: size,
                curves: curves,
                rateMaximum: rateMaximum
            )
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Polar tuning comparison")
        .accessibilityValue(accessibilitySummary(curves))
    }
}

private func drawLineChart(
    context: inout GraphicsContext,
    size: CGSize,
    curves: [StyledComparisonCurve],
    rateMaximum: Double
) {
    let plotRect = CGRect(
        x: 58,
        y: 18,
        width: max(40, size.width - 78),
        height: max(40, size.height - 65)
    )
    let high = max(rateMaximum, Double.leastNonzeroMagnitude)
    let gridColor = Color.secondary.opacity(0.2)
    let axisColor = Color.secondary.opacity(0.55)

    for fraction in [0.0, 0.25, 0.5, 0.75, 1.0] {
        let y = plotRect.maxY - plotRect.height * CGFloat(fraction)
        var path = Path()
        path.move(to: CGPoint(x: plotRect.minX, y: y))
        path.addLine(to: CGPoint(x: plotRect.maxX, y: y))
        context.stroke(path, with: .color(gridColor), lineWidth: 1)
        context.draw(
            Text(formatRate(high * fraction))
                .font(.system(size: 10, design: .rounded))
                .foregroundStyle(.secondary),
            at: CGPoint(x: plotRect.minX - 8, y: y),
            anchor: .trailing
        )
    }

    for degrees in stride(from: 0, through: 360, by: 45) {
        let x = plotRect.minX + plotRect.width * CGFloat(Double(degrees) / 360)
        var path = Path()
        path.move(to: CGPoint(x: x, y: plotRect.minY))
        path.addLine(to: CGPoint(x: x, y: plotRect.maxY))
        context.stroke(path, with: .color(gridColor), lineWidth: 1)
        context.draw(
            Text("\(degrees)°")
                .font(.system(size: 10, design: .rounded))
                .foregroundStyle(.secondary),
            at: CGPoint(x: x, y: plotRect.maxY + 8),
            anchor: .top
        )
    }

    var axes = Path()
    axes.move(to: CGPoint(x: plotRect.minX, y: plotRect.minY))
    axes.addLine(to: CGPoint(x: plotRect.minX, y: plotRect.maxY))
    axes.addLine(to: CGPoint(x: plotRect.maxX, y: plotRect.maxY))
    context.stroke(axes, with: .color(axisColor), lineWidth: 1)

    context.draw(
        Text("Hz")
            .font(.system(size: 10, weight: .medium, design: .rounded))
            .foregroundStyle(.secondary),
        at: CGPoint(x: 8, y: plotRect.minY - 3),
        anchor: .leading
    )

    for styled in curves {
        let sourceSamples = samples(from: styled.curve)
        let plottedSamples = samplesIncludingCircularSeam(sourceSamples)
        guard !plottedSamples.isEmpty else { continue }

        var path = Path()
        for (index, sample) in plottedSamples.enumerated() {
            let point = linePoint(sample, plotRect: plotRect, high: high)
            if index == 0 {
                path.move(to: point)
            } else {
                path.addLine(to: point)
            }
        }
        context.stroke(
            path,
            with: .color(styled.color),
            style: StrokeStyle(lineWidth: 2.6, lineCap: .round, lineJoin: .round)
        )

        if sourceSamples.count <= 60 {
            for sample in sourceSamples {
                let point = linePoint(sample, plotRect: plotRect, high: high)
                context.fill(
                    Path(ellipseIn: CGRect(x: point.x - 2.1, y: point.y - 2.1, width: 4.2, height: 4.2)),
                    with: .color(styled.color)
                )
            }
        }
    }
}

private func drawPolarChart(
    context: inout GraphicsContext,
    size: CGSize,
    curves: [StyledComparisonCurve],
    rateMaximum: Double
) {
    let high = max(rateMaximum, Double.leastNonzeroMagnitude)
    let radius = max(30, min(size.width, size.height) * 0.38)
    let center = CGPoint(x: size.width / 2, y: size.height / 2 + 8)
    let gridColor = Color.secondary.opacity(0.22)
    let axisColor = Color.secondary.opacity(0.48)

    for fraction in [0.25, 0.5, 0.75, 1.0] {
        let ringRadius = radius * CGFloat(fraction)
        let rect = CGRect(
            x: center.x - ringRadius,
            y: center.y - ringRadius,
            width: ringRadius * 2,
            height: ringRadius * 2
        )
        context.stroke(Path(ellipseIn: rect), with: .color(gridColor), lineWidth: 1)
        context.draw(
            Text(formatRate(high * fraction))
                .font(.system(size: 9, design: .rounded))
                .foregroundStyle(.secondary),
            at: CGPoint(x: center.x + 5, y: center.y - ringRadius),
            anchor: .leading
        )
    }

    for degrees in stride(from: 0, to: 360, by: 45) {
        let endpoint = polarPoint(
            angleDegrees: Double(degrees),
            radius: radius,
            center: center
        )
        var spoke = Path()
        spoke.move(to: center)
        spoke.addLine(to: endpoint)
        context.stroke(spoke, with: .color(degrees.isMultiple(of: 90) ? axisColor : gridColor), lineWidth: 1)
    }

    for (degrees, anchor) in [
        (0.0, UnitPoint.bottom),
        (90.0, UnitPoint.leading),
        (180.0, UnitPoint.top),
        (270.0, UnitPoint.trailing),
    ] {
        let point = polarPoint(
            angleDegrees: degrees,
            radius: radius + 10,
            center: center
        )
        context.draw(
            Text("\(Int(degrees))°")
                .font(.system(size: 10, weight: .medium, design: .rounded))
                .foregroundStyle(.secondary),
            at: point,
            anchor: anchor
        )
    }

    context.draw(
        Text("Hz")
            .font(.system(size: 9, weight: .medium, design: .rounded))
            .foregroundStyle(.secondary),
        at: CGPoint(x: center.x + radius, y: center.y - radius - 20),
        anchor: .trailing
    )

    for styled in curves {
        let sourceSamples = samples(from: styled.curve)
        guard let first = sourceSamples.first else { continue }
        var path = Path()
        for (index, sample) in (sourceSamples + [first]).enumerated() {
            let point = polarPoint(
                angleDegrees: sample.angle,
                radius: radius * CGFloat(sample.rate / high),
                center: center
            )
            if index == 0 {
                path.move(to: point)
            } else {
                path.addLine(to: point)
            }
        }
        context.stroke(
            path,
            with: .color(styled.color),
            style: StrokeStyle(lineWidth: 2.6, lineCap: .round, lineJoin: .round)
        )

        if sourceSamples.count <= 60 {
            for sample in sourceSamples {
                let point = polarPoint(
                    angleDegrees: sample.angle,
                    radius: radius * CGFloat(sample.rate / high),
                    center: center
                )
                context.fill(
                    Path(ellipseIn: CGRect(x: point.x - 2.1, y: point.y - 2.1, width: 4.2, height: 4.2)),
                    with: .color(styled.color)
                )
            }
        }
    }
}

private func samples(from curve: ProcessedHDCurve) -> [TuningSample] {
    zip(curve.anglesDegrees, curve.ratesHz)
        .compactMap { angle, rate in
            guard angle.isFinite, rate.isFinite, rate >= 0 else { return nil }
            return TuningSample(
                angle: HDTuningData.normalizedAngleDegrees(angle),
                rate: rate
            )
        }
        .sorted { left, right in left.angle < right.angle }
}

private func samplesIncludingCircularSeam(_ samples: [TuningSample]) -> [TuningSample] {
    guard let first = samples.first, let last = samples.last else { return [] }
    guard samples.count > 1 else {
        return [
            TuningSample(angle: 0, rate: first.rate),
            first,
            TuningSample(angle: 360, rate: first.rate),
        ]
    }
    let wrappedFirstAngle = first.angle + 360
    let span = max(wrappedFirstAngle - last.angle, Double.leastNonzeroMagnitude)
    let fractionAtBoundary = (360 - last.angle) / span
    let boundaryRate = last.rate + (first.rate - last.rate) * fractionAtBoundary
    return [TuningSample(angle: 0, rate: boundaryRate)]
        + samples
        + [TuningSample(angle: 360, rate: boundaryRate)]
}

private func linePoint(_ sample: TuningSample, plotRect: CGRect, high: Double) -> CGPoint {
    CGPoint(
        x: plotRect.minX + plotRect.width * CGFloat(sample.angle / 360),
        y: plotRect.maxY - plotRect.height * CGFloat(sample.rate / high)
    )
}

private func polarPoint(
    angleDegrees: Double,
    radius: CGFloat,
    center: CGPoint
) -> CGPoint {
    let radians = angleDegrees * .pi / 180 - .pi / 2
    return CGPoint(
        x: center.x + radius * CGFloat(cos(radians)),
        y: center.y + radius * CGFloat(sin(radians))
    )
}

func comparisonRateMaximum(_ curves: [StyledComparisonCurve]) -> Double {
    let maximum = curves
        .flatMap { $0.curve.ratesHz }
        .filter { $0.isFinite && $0 >= 0 }
        .max() ?? 0
    return niceCeiling(maximum)
}

private func niceCeiling(_ value: Double) -> Double {
    guard value.isFinite, value > 0 else { return 1 }
    let magnitude = pow(10, floor(log10(value)))
    let normalized = value / magnitude
    let step: Double
    switch normalized {
    case ...1:
        step = 1
    case ...2:
        step = 2
    case ...2.5:
        step = 2.5
    case ...5:
        step = 5
    default:
        step = 10
    }
    return step * magnitude
}

private func formatRate(_ value: Double) -> String {
    if abs(value) >= 100 { return String(format: "%.0f", value) }
    if abs(value) >= 10 { return String(format: "%.1f", value) }
    return String(format: "%.2g", value)
}

private func accessibilitySummary(_ curves: [StyledComparisonCurve]) -> String {
    guard !curves.isEmpty else { return "No tuning curves are displayed." }
    return curves.map { styled in
        let maximum = styled.curve.ratesHz.max() ?? 0
        return "\(styled.slot.title), offset \(signedDegrees(styled.angleOffset)), maximum \(formatRate(maximum)) hertz"
    }.joined(separator: "; ")
}
