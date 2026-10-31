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
    /// Nil when presentation counts or positive response exposure are unavailable.
    let maxFiringRate: Double?
    let totalSpikes: Double
    /// Full-window strongest rate cell, or strongest count cell when Hz is unavailable.
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

enum RFOverlayLayer: String, CaseIterable, Identifiable, Codable, Sendable {
    case excitatory2D = "excitatory_2d", excitatory1D = "excitatory_1d"
    case inhibitory2D = "inhibitory_2d", inhibitory1D = "inhibitory_1d"

    var id: String { rawValue }
    var polarity: RFResultPolarity {
        switch self {
        case .excitatory2D, .excitatory1D: .excitatory
        case .inhibitory2D, .inhibitory1D: .inhibitory
        }
    }
    var dimension: String {
        switch self {
        case .excitatory2D, .inhibitory2D: "2d"
        case .excitatory1D, .inhibitory1D: "1d"
        }
    }
    var title: String { polarity.rawValue.capitalized + " " + dimension.uppercased() }
    var resultKey: String { dimension + ":" + polarity.rawValue }
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
    var showProbeUnitInfo = true
    var showTuning = true
    var autoLoadProbe = true
    var autoLoadTuning = true
    var tuningStyle = TuningPlotStyle.auto
    var tuningLayout = TuningPaneLayout.sideBySide
    var tuningBins = HDTuningData.defaultDisplayBins
    var tuningSmoothing = true
    var tuningSigmaDegrees = HDTuningData.defaultSmoothSigma * 360 / Double(HDTuningData.defaultDisplayBins)
    var tuningSharedScale = false
    var overlayLayers: Set<RFOverlayLayer> = []
    var overlayEnabled = true
    var rfResultLayoutPattern = SavedRFResult.defaultLayoutPattern
    var overlayWidth = 2.0
    var overlay2DColor = "#34c759"
    var overlay1DColor = "#ffcc00"
    var overlayIntersectionColor = "#ff9500"
    var overlayInhibitory2DColor = "#af52de"
    var overlayInhibitory1DColor = "#00a1b5"
    var overlayInhibitoryIntersectionColor = "#5856d6"

    func overlayColor(membership: Int, polarity: RFResultPolarity) -> String {
        if polarity == .inhibitory {
            return membership == 3 ? overlayInhibitoryIntersectionColor
                : membership == 2 ? overlayInhibitory1DColor : overlayInhibitory2DColor
        }
        return membership == 3 ? overlayIntersectionColor
            : membership == 2 ? overlay1DColor : overlay2DColor
    }

    private enum CodingKeys: String, CodingKey {
        case visibleTabs, initialTab, valueMode, xBins, yBins
        case timeResolutionMS, smoothRadius, flipY, palette, spatialFormat
        case polarRadius, delayRGBMode, showProbe, showProbeUnitInfo, showTuning
        case autoLoadProbe, autoLoadTuning, tuningStyle, tuningLayout, tuningBins
        case tuningSmoothing, tuningSigmaDegrees, tuningSharedScale, overlayLayers, overlayEnabled, overlayWidth
        case rfResultLayoutPattern
        case overlay2DColor, overlay1DColor, overlayIntersectionColor, overlayInhibitory2DColor, overlayInhibitory1DColor
        case overlayInhibitoryIntersectionColor
    }

    private enum LegacyOverlayKeys: String, CodingKey {
        case overlayMode, overlayPolarity
    }

    init() {}

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        visibleTabs = try values.decodeIfPresent([PlotTab].self, forKey: .visibleTabs) ?? visibleTabs
        initialTab = try values.decodeIfPresent(PlotTab.self, forKey: .initialTab) ?? initialTab
        valueMode = try values.decodeIfPresent(ResponseValueMode.self, forKey: .valueMode) ?? valueMode
        xBins = try values.decodeIfPresent(Int.self, forKey: .xBins) ?? xBins
        yBins = try values.decodeIfPresent(Int.self, forKey: .yBins) ?? yBins
        timeResolutionMS = try values.decodeIfPresent(Double.self, forKey: .timeResolutionMS) ?? timeResolutionMS
        smoothRadius = try values.decodeIfPresent(Int.self, forKey: .smoothRadius) ?? smoothRadius
        flipY = try values.decodeIfPresent(Bool.self, forKey: .flipY) ?? flipY
        palette = try values.decodeIfPresent(RFPalette.self, forKey: .palette) ?? palette
        spatialFormat = try values.decodeIfPresent(SpatialPlotFormat.self, forKey: .spatialFormat) ?? spatialFormat
        polarRadius = try values.decodeIfPresent(PolarRadiusMode.self, forKey: .polarRadius) ?? polarRadius
        delayRGBMode = try values.decodeIfPresent(DelayRGBMode.self, forKey: .delayRGBMode) ?? delayRGBMode
        showProbe = try values.decodeIfPresent(Bool.self, forKey: .showProbe) ?? showProbe
        showProbeUnitInfo = try values.decodeIfPresent(Bool.self, forKey: .showProbeUnitInfo) ?? showProbeUnitInfo
        showTuning = try values.decodeIfPresent(Bool.self, forKey: .showTuning) ?? showTuning
        autoLoadProbe = try values.decodeIfPresent(Bool.self, forKey: .autoLoadProbe) ?? autoLoadProbe
        autoLoadTuning = try values.decodeIfPresent(Bool.self, forKey: .autoLoadTuning) ?? autoLoadTuning
        tuningStyle = try values.decodeIfPresent(TuningPlotStyle.self, forKey: .tuningStyle) ?? tuningStyle
        tuningLayout = try values.decodeIfPresent(TuningPaneLayout.self, forKey: .tuningLayout) ?? tuningLayout
        tuningBins = try values.decodeIfPresent(Int.self, forKey: .tuningBins) ?? tuningBins
        tuningSmoothing = try values.decodeIfPresent(Bool.self, forKey: .tuningSmoothing) ?? tuningSmoothing
        tuningSigmaDegrees = try values.decodeIfPresent(Double.self, forKey: .tuningSigmaDegrees) ?? tuningSigmaDegrees
        tuningSharedScale = try values.decodeIfPresent(Bool.self, forKey: .tuningSharedScale) ?? tuningSharedScale
        overlayLayers = try values.decodeIfPresent(Set<RFOverlayLayer>.self, forKey: .overlayLayers) ?? overlayLayers
        overlayEnabled = try values.decodeIfPresent(Bool.self, forKey: .overlayEnabled) ?? overlayEnabled
        rfResultLayoutPattern = try values.decodeIfPresent(String.self, forKey: .rfResultLayoutPattern) ?? rfResultLayoutPattern
        overlayWidth = try values.decodeIfPresent(Double.self, forKey: .overlayWidth) ?? overlayWidth
        overlay2DColor = try values.decodeIfPresent(String.self, forKey: .overlay2DColor) ?? overlay2DColor
        overlay1DColor = try values.decodeIfPresent(String.self, forKey: .overlay1DColor) ?? overlay1DColor
        overlayIntersectionColor = try values.decodeIfPresent(String.self, forKey: .overlayIntersectionColor) ?? overlayIntersectionColor
        overlayInhibitory2DColor = try values.decodeIfPresent(String.self, forKey: .overlayInhibitory2DColor) ?? overlayInhibitory2DColor
        overlayInhibitory1DColor = try values.decodeIfPresent(String.self, forKey: .overlayInhibitory1DColor) ?? overlayInhibitory1DColor
        overlayInhibitoryIntersectionColor = try values.decodeIfPresent(String.self, forKey: .overlayInhibitoryIntersectionColor) ?? overlayInhibitoryIntersectionColor

        if !values.contains(.overlayLayers) {
            let legacy = try decoder.container(keyedBy: LegacyOverlayKeys.self)
            let mode = try legacy.decodeIfPresent(RFOverlayMode.self, forKey: .overlayMode) ?? .none
            let polarity = try legacy.decodeIfPresent(RFResultPolarity.self, forKey: .overlayPolarity) ?? .excitatory
            overlayLayers = Set(RFOverlayLayer.allCases.filter {
                $0.polarity == polarity && mode.dimensions.contains($0.dimension)
            })
        }
    }
}

struct RFOverlayCell: Equatable {
    let polarity: RFResultPolarity
    let y: Int
    let x: Int
    let membership: Int
    let center2D: Bool
    let center1D: Bool
    let oneDAxis: String?
}
