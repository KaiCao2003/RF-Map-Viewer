import Foundation

let innerBlankRows = 4
let polarPadRows = 1

typealias OptionalMatrix = [[Double?]]

struct AxisGroup: Hashable, Sendable {
    let start: Int
    let end: Int
}

struct CellRef: Hashable, Sendable {
    let yStart: Int
    let yEnd: Int
    let xStart: Int
    let xEnd: Int
}

enum ResponseValueMode: String, CaseIterable, Identifiable, Hashable, Sendable {
    case spikeCount = "Spike count"
    case spikesPerPresentation = "Spikes / presentation"
    case meanFiringRate = "Mean firing rate (Hz)"

    var id: String { rawValue }

    var requiresPresentationCounts: Bool {
        self != .spikeCount
    }

    var unit: String {
        switch self {
        case .spikeCount: "spikes"
        case .spikesPerPresentation: "spikes/presentation"
        case .meanFiringRate: "Hz"
        }
    }

    var shortUnit: String {
        switch self {
        case .spikeCount: "spikes"
        case .spikesPerPresentation: "sp/pres"
        case .meanFiringRate: "Hz"
        }
    }

    var suffix: String { " \(shortUnit)" }

    var filenameSlug: String {
        switch self {
        case .spikeCount: "spike_count"
        case .spikesPerPresentation: "spikes_per_presentation"
        case .meanFiringRate: "mean_firing_rate_hz"
        }
    }

    func format(_ value: Double?) -> String {
        guard let value, value.isFinite else { return "n/a" }
        if self == .spikeCount {
            return String(format: "%.0f", value)
        }
        var text = String(format: "%.2f", value)
        while text.last == "0" { text.removeLast() }
        if text.last == "." { text.removeLast() }
        return text
    }
}

enum RFPalette: String, CaseIterable, Identifiable, Hashable, Sendable {
    case gray = "Gray"
    case viridis = "Viridis"
    case inferno = "Inferno"

    var id: String { rawValue }
}

enum PolarRadiusMode: String, CaseIterable, Identifiable, Hashable, Sendable {
    case matlabRowOneInner = "MATLAB row 1 inner"
    case displayBottomInner = "Display bottom inner"

    var id: String { rawValue }
}

enum SpatialPlotFormat: String, CaseIterable, Identifiable, Hashable, Sendable {
    case rectangular = "Rectangle"
    case polar = "Polar"

    var id: String { rawValue }
}

enum DelayRGBMode: String, CaseIterable, Identifiable, Hashable, Sendable {
    case delay = "Delay"
    case rgb = "RGB"

    var id: String { rawValue }
}

enum PlotTab: String, CaseIterable, Identifiable, Hashable, Sendable {
    case rf = "RF Map"
    case delayRGB = "Delay / RGB"
    case timeline = "Timeline"

    var id: String { rawValue }
}

struct UnitMetrics: Sendable {
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

struct SpatialGroupObservations: Sendable {
    let count: Double
    let presentations: Double?
    let sourcePixelCount: Int
}

struct SpatialGroupTemporalMetrics: Sendable {
    let meanTotalCount: Double
    let peakGroupIndex: Int?
    let delayMS: Double?
    let entropy: Double
}

struct TimelineMatrixSnapshot {
    let timeGroups: [AxisGroup]
    let matrices: [OptionalMatrix]
    let totals: [Double]
    let sharedHigh: Double
}
