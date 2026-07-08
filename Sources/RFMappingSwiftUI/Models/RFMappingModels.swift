import Foundation

let innerBlankRows = 4
let polarPadRows = 1

typealias OptionalMatrix = [[Double?]]

struct AxisGroup: Hashable {
    let start: Int
    let end: Int
}

struct CellRef: Hashable {
    let yStart: Int
    let yEnd: Int
    let xStart: Int
    let xEnd: Int
}

enum RFMode: String, CaseIterable, Identifiable {
    case total = "Total"
    case peak = "Peak"
    case bin = "Bin"
    case rangeSum = "Range sum"

    var id: String { rawValue }
}

enum RFPalette: String, CaseIterable, Identifiable {
    case gray = "Gray"
    case viridis = "Viridis"
    case inferno = "Inferno"

    var id: String { rawValue }
}

enum PolarRadiusMode: String, CaseIterable, Identifiable {
    case matlabRowOneInner = "MATLAB row 1 inner"
    case displayBottomInner = "Display bottom inner"

    var id: String { rawValue }
}

enum PlotTab: String, CaseIterable, Identifiable {
    case rf = "2D RF"
    case delay = "Delay"
    case polar = "Polar"
    case timeline = "Timeline"
    case rgb = "RGB"
    case stack = "Stack"

    var id: String { rawValue }
}

struct UnitMetrics {
    let total: [[Double]]
    let peak: [[Double]]
    let peakBin: [[Int?]]
    let delayMS: [[Double?]]
    let entropy: [[Double]]
    let binTotals: [Double]
    let maxTotal: Double
    let maxPeak: Double
    let maxBinCount: Double
    let totalSpikes: Double
    let bestY: Int
    let bestX: Int
}
