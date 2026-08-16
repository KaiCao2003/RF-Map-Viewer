import Foundation
import Observation

enum TuningPlotMode: String, CaseIterable, Identifiable, Sendable {
    case automatic = "Auto"
    case line = "Line"
    case polar = "Polar"

    var id: String { rawValue }
}

enum TuningLayout: String, CaseIterable, Identifiable, Sendable {
    case sideBySide = "Side by side"
    case stacked = "Stacked"

    var id: String { rawValue }
}

@MainActor
@Observable
final class ViewerPreferences {
    static let shared = ViewerPreferences()

    static let tuningBinChoices = [6, 10, 12, 15, 18, 20, 30, 36, 45, 60, 90, 180]

    @ObservationIgnored private let defaults: UserDefaults

    var showTuningCurve: Bool { didSet { save() } }
    var autoLoadTuningCurve: Bool { didSet { save() } }
    var tuningPlotMode: TuningPlotMode { didSet { save() } }
    var tuningLayout: TuningLayout { didSet { save() } }
    var tuningDisplayBins: Int { didSet { save() } }
    var tuningSmoothing: Bool { didSet { save() } }
    /// Circular Gaussian standard deviation in physical degrees. Keeping this
    /// value independent of display bins prevents a display-only control from
    /// changing the scientific smoothing width.
    var tuningSmoothingDegrees: Double { didSet { save() } }
    var tuningCompareScale: Bool { didSet { save() } }

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        showTuningCurve = defaults.object(forKey: Key.showTuningCurve) as? Bool ?? true
        autoLoadTuningCurve = defaults.object(forKey: Key.autoLoadTuningCurve) as? Bool ?? true
        tuningPlotMode = TuningPlotMode(
            rawValue: defaults.string(forKey: Key.tuningPlotMode) ?? ""
        ) ?? .automatic
        tuningLayout = TuningLayout(
            rawValue: defaults.string(forKey: Key.tuningLayout) ?? ""
        ) ?? .sideBySide

        let storedBins = defaults.integer(forKey: Key.tuningDisplayBins)
        tuningDisplayBins = Self.tuningBinChoices.contains(storedBins) ? storedBins : 30
        tuningSmoothing = defaults.object(forKey: Key.tuningSmoothing) as? Bool ?? true

        let storedDegrees = defaults.double(forKey: Key.tuningSmoothingDegrees)
        tuningSmoothingDegrees = storedDegrees.isFinite && storedDegrees > 0
            ? storedDegrees
            : 18.0
        tuningCompareScale = defaults.object(forKey: Key.tuningCompareScale) as? Bool ?? false
    }

    func restoreTuningDefaults() {
        showTuningCurve = true
        autoLoadTuningCurve = true
        tuningPlotMode = .automatic
        tuningLayout = .sideBySide
        tuningDisplayBins = 30
        tuningSmoothing = true
        tuningSmoothingDegrees = 18.0
        tuningCompareScale = false
    }

    private func save() {
        defaults.set(showTuningCurve, forKey: Key.showTuningCurve)
        defaults.set(autoLoadTuningCurve, forKey: Key.autoLoadTuningCurve)
        defaults.set(tuningPlotMode.rawValue, forKey: Key.tuningPlotMode)
        defaults.set(tuningLayout.rawValue, forKey: Key.tuningLayout)
        defaults.set(tuningDisplayBins, forKey: Key.tuningDisplayBins)
        defaults.set(tuningSmoothing, forKey: Key.tuningSmoothing)
        defaults.set(tuningSmoothingDegrees, forKey: Key.tuningSmoothingDegrees)
        defaults.set(tuningCompareScale, forKey: Key.tuningCompareScale)
    }

    private enum Key {
        static let showTuningCurve = "viewer.showTuningCurve"
        static let autoLoadTuningCurve = "viewer.autoLoadTuningCurve"
        static let tuningPlotMode = "viewer.tuningPlotMode"
        static let tuningLayout = "viewer.tuningLayout"
        static let tuningDisplayBins = "viewer.tuningDisplayBins"
        static let tuningSmoothing = "viewer.tuningSmoothing"
        static let tuningSmoothingDegrees = "viewer.tuningSmoothingDegrees"
        static let tuningCompareScale = "viewer.tuningCompareScale"
    }
}
