import Foundation

func clamp(_ value: Double, _ low: Double = 0.0, _ high: Double = 1.0) -> Double {
    max(low, min(high, value))
}

func formatPos(_ value: Double) -> String {
    if abs(value - value.rounded()) < 1e-9 {
        return String(Int(value.rounded()))
    }
    return String(format: "%.2f", value)
}

func formatMS(_ value: Double) -> String {
    if abs(value - value.rounded()) < 1e-9 {
        return String(Int(value.rounded()))
    }
    var text = String(format: "%.3f", value)
    while text.last == "0" {
        text.removeLast()
    }
    if text.last == "." {
        text.removeLast()
    }
    return text
}

func optionalMatrix(_ matrix: [[Double]]) -> OptionalMatrix {
    matrix.map { row in row.map { Optional($0) } }
}

/// CPython 3.12+ uses compensated summation for homogeneous floating-point
/// inputs. RF Mapping's required Python 3.14 runtime therefore produces more
/// accurate results than a naïve Swift `reduce(0, +)` for mixed magnitudes.
func compensatedSum<S: Sequence>(_ values: S) -> Double where S.Element == Double {
    var high = 0.0
    var low = 0.0
    for value in values {
        let next = high + value
        if !next.isFinite {
            high = next
            low = 0.0
            continue
        }
        if abs(high) >= abs(value) {
            low += (high - next) + value
        } else {
            low += (value - next) + high
        }
        high = next
    }
    return high + low
}

func axisGroupsForTarget(sourceCount: Int, targetCount: Int) -> [AxisGroup] {
    let target = max(1, min(sourceCount, targetCount))
    return (0..<target).map { groupIndex in
        let start = groupIndex * sourceCount / target
        let end = ((groupIndex + 1) * sourceCount / target) - 1
        return AxisGroup(start: start, end: max(start, end))
    }
}

func reduceMatrixXY(
    _ matrix: OptionalMatrix,
    yGroups: [AxisGroup],
    xGroups: [AxisGroup]
) -> OptionalMatrix {
    yGroups.map { yGroup in
        xGroups.map { xGroup in
            var values: [Double] = []
            for yIndex in yGroup.start...yGroup.end {
                for xIndex in xGroup.start...xGroup.end {
                    if let value = matrix[yIndex][xIndex], value.isFinite {
                        values.append(value)
                    }
                }
            }
            guard !values.isEmpty else { return nil }
            return compensatedSum(values) / Double(values.count)
        }
    }
}

func smoothMatrix(_ matrix: OptionalMatrix, radius: Int) -> OptionalMatrix {
    let radius = max(0, radius)
    guard radius > 0 else { return matrix }
    let rows = matrix.count
    let cols = matrix.first?.count ?? 0
    var current = matrix

    for _ in 0..<radius {
        var output = current
        for y in 0..<rows {
            for x in 0..<cols {
                guard let center = current[y][x], center.isFinite else {
                    output[y][x] = nil
                    continue
                }
                var total = 0.0
                var weightTotal = 0.0
                for dy in -1...1 {
                    let yy = y + dy
                    guard yy >= 0 && yy < rows else { continue }
                    for dx in -1...1 {
                        let xx = x + dx
                        guard xx >= 0 && xx < cols else { continue }
                        guard let value = current[yy][xx], value.isFinite else { continue }
                        let weight = dx == 0 && dy == 0 ? 4.0 : ((dx == 0 || dy == 0) ? 2.0 : 1.0)
                        total += value * weight
                        weightTotal += weight
                    }
                }
                output[y][x] = weightTotal > 0 ? total / weightTotal : nil
            }
        }
        current = output
    }

    return current
}

func finiteMinMax(_ matrix: OptionalMatrix) -> (Double, Double) {
    let values = matrix.flatMap { $0 }.compactMap { value -> Double? in
        guard let value, value.isFinite else { return nil }
        return value
    }
    guard let low = values.min(), var high = values.max() else {
        return (0.0, 1.0)
    }
    if abs(high - low) < 1e-12 {
        high = low + 1.0
    }
    return (low, high)
}
