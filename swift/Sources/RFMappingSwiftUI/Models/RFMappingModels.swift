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

enum ResponseValueMode: String, CaseIterable, Identifiable, Hashable, Codable, Sendable {
    case spikeCount = "Spike count"
    case meanFiringRate = "Mean firing rate (Hz)"

    var id: String { rawValue }

    var unit: String {
        switch self {
        case .spikeCount: "spikes"
        case .meanFiringRate: "Hz"
        }
    }

    var shortUnit: String {
        switch self {
        case .spikeCount: "spikes"
        case .meanFiringRate: "Hz"
        }
    }

    var suffix: String { " \(shortUnit)" }

    var filenameSlug: String {
        switch self {
        case .spikeCount: "spike_count"
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

enum RFPalette: String, CaseIterable, Identifiable, Hashable, Codable, Sendable {
    case gray = "Gray"
    case viridis = "Viridis"
    case inferno = "Inferno"

    var id: String { rawValue }
}

enum PolarRadiusMode: String, CaseIterable, Identifiable, Hashable, Codable, Sendable {
    case matlabRowOneInner = "MATLAB row 1 inner"
    case displayBottomInner = "Display bottom inner"

    var id: String { rawValue }
}

enum SpatialPlotFormat: String, CaseIterable, Identifiable, Hashable, Codable, Sendable {
    case rectangular = "Rectangle"
    case polar = "Polar"

    var id: String { rawValue }
}

enum DelayRGBMode: String, CaseIterable, Identifiable, Hashable, Codable, Sendable {
    case delay = "Delay"
    case rgb = "RGB"

    var id: String { rawValue }
}

enum PlotTab: String, CaseIterable, Identifiable, Hashable, Codable, Sendable {
    case rf = "RF Map"
    case delayRGB = "Delay / RGB"
    case timeline = "Timeline"
    case results = "RF Results"

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
    /// Nil only when every spatial cell has zero occupancy.
    let maxFiringRate: Double?
    let totalSpikes: Double
    /// Full-window strongest occupancy-normalized response cell.
    let bestY: Int
    let bestX: Int
}

struct TimelineMatrixSnapshot {
    let timeGroups: [AxisGroup]
    let matrices: [OptionalMatrix]
    let totals: [Double]
    let sharedHigh: Double
}


enum RFOverlayMode: String, CaseIterable, Identifiable, Codable, Sendable {
    case none = "None", twoD = "2D", oneD = "1D", both = "Both"
    var id: String { rawValue }
    var dimensions: [String] {
        switch self {
        case .none: []
        case .twoD: ["2d"]
        case .oneD: ["1d"]
        case .both: ["2d", "1d"]
        }
    }
}

enum RFResultPolarity: String, CaseIterable, Identifiable, Codable, Sendable {
    case excitatory, inhibitory
    var id: String { rawValue }
}

enum TuningPlotStyle: String, CaseIterable, Identifiable, Codable, Sendable {
    case auto = "Auto", line = "Line", polar = "Polar"
    var id: String { rawValue }
}

enum TuningPaneLayout: String, CaseIterable, Identifiable, Codable, Sendable {
    case sideBySide = "Side by side", stacked = "Stacked"
    var id: String { rawValue }
}

struct ViewerDisplayDefaults: Codable, Equatable {
    var visibleTabs = PlotTab.allCases
    var initialTab = PlotTab.rf
    var valueMode = ResponseValueMode.meanFiringRate
    var xBins = 0
    var yBins = 0
    var timeResolutionMS = 1.0
    var smoothRadius = 0
    var flipY = false
    var palette = RFPalette.gray
    var spatialFormat = SpatialPlotFormat.rectangular
    var polarRadius = PolarRadiusMode.displayBottomInner
    var delayRGBMode = DelayRGBMode.delay
    var showProbe = true
    var showTuning = true
    var autoLoadProbe = true
    var autoLoadTuning = true
    var tuningStyle = TuningPlotStyle.auto
    var tuningLayout = TuningPaneLayout.sideBySide
    var tuningBins = HDTuningData.defaultDisplayBins
    var tuningSmoothing = true
    var tuningSigmaDegrees = HDTuningData.defaultSmoothSigma * 360 / Double(HDTuningData.defaultDisplayBins)
    var tuningSharedScale = false
    var overlayMode = RFOverlayMode.none
    var overlayPolarity = RFResultPolarity.excitatory
    var overlayWidth = 2.0
    var overlay2DColor = "#34c759"
    var overlay1DColor = "#ffcc00"
    var overlayIntersectionColor = "#ff9500"
}

struct RFOverlayCell: Equatable {
    let y: Int
    let x: Int
    let membership: Int
    let center2D: Bool
    let center1D: Bool
    let oneDAxis: String?
}
