import AppKit
import Foundation
import Observation

enum UnitQualityVisibilityChangeReason: Equatable, Sendable {
    case filterSettings
    case plotRange
}

enum RFUnitUnavailableReason: Equatable, Sendable {
    case noQualityVisibleUnits(total: Int)
    case qualityFiltered(unitID: Int)
    case noProbeVisibleUnits
    case probeFiltered(unitID: Int)
    case pairedMissing(unitID: Int)
    case noSelection
}

@Observable
final class RFMappingStore {
    private enum PreferenceKey {
        static let tuningSession = "rfmapping.tuningSession"
        static let showWaveform = "rfmapping.showWaveform"
        static let waveformChannelMode = "rfmapping.waveformChannelMode"
        static let rfFilterUnitsWithZeroBins = "rfmapping.rfFilterUnitsWithZeroBins"
        static let rfZeroBinThreshold = "rfmapping.rfZeroBinThreshold"
    }

    private enum SpatialPlotKind: Int, Equatable {
        case current
        case delay
    }

    private struct RawMatrixCacheKey: Equatable {
        let dataID: ObjectIdentifier
        let unitIndex: Int
        let valueMode: ResponseValueMode
        let sourceStart: Int
        let sourceEnd: Int
    }

    private struct DelayCacheKey: Equatable {
        let dataID: ObjectIdentifier
        let unitIndex: Int
        let timeGroupSize: Int
        let floor: Double
    }

    private struct SpatialPlotCacheKey: Equatable {
        let kind: SpatialPlotKind
        let dataID: ObjectIdentifier
        let unitIndex: Int
        let valueMode: ResponseValueMode
        let sourceStart: Int
        let sourceEnd: Int
        let timeGroupSize: Int
        let floor: Double
        let xBins: Int
        let yBins: Int
        let flipY: Bool
        let smoothRadius: Int
    }

    private struct RGBPlotCacheKey: Equatable {
        let dataID: ObjectIdentifier
        let unitIndex: Int
        let valueMode: ResponseValueMode
        let timeGroupSize: Int
        let xBins: Int
        let yBins: Int
        let flipY: Bool
        let smoothRadius: Int
    }

    private struct TimeAxisMetadata {
        let dataID: ObjectIdentifier
        let edgesMS: [Double]
        let startMS: Double
        let endMS: Double
        let totalMS: Double
        let baseBinMS: Double
    }

    private struct TimeGroupingCache {
        let dataID: ObjectIdentifier
        let groupSize: Int
        let groups: [AxisGroup]
        let bounds: [(Double, Double)]
        let labels: [String]
        let intervalLabels: [String]
        let centers: [Double]
    }

    private struct SelectedSourceRangeCacheKey: Equatable {
        let dataID: ObjectIdentifier
        let startMS: Double
        let endMS: Double
    }

    private struct UnitQualityFilterCacheKey: Equatable {
        let dataID: ObjectIdentifier
        let sourceStart: Int
        let sourceEnd: Int
        let threshold: Int
    }

    private struct GroupResponseCacheKey: Equatable {
        let dataID: ObjectIdentifier
        let unitIndex: Int
        let valueMode: ResponseValueMode
        let timeGroupSize: Int
        let cell: CellRef
    }

    private struct CellAnalysisCacheKey: Equatable {
        let responseKey: GroupResponseCacheKey
        let selectedStart: Int
        let selectedEnd: Int
    }

    private struct CellAnalysis {
        let displayValues: [Double?]
        let countHist: [Double]
        let selectedValue: Double?
        let totalValue: Double?
        let peakBin: Int?
        let peakValue: Double?
        let delayMS: Double?
        let entropy: Double
    }

    private struct TimelineCacheKey: Equatable {
        let dataID: ObjectIdentifier
        let unitIndex: Int
        let valueMode: ResponseValueMode
        let timeGroupSize: Int
        let xBins: Int
        let yBins: Int
        let flipY: Bool
        let smoothRadius: Int
    }

    @ObservationIgnored private var timelineCaches: [(key: TimelineCacheKey, value: TimelineMatrixSnapshot)] = []
    @ObservationIgnored private var currentMatrixCache: (key: RawMatrixCacheKey, value: OptionalMatrix)?
    @ObservationIgnored private var delayMatrixCache: (key: DelayCacheKey, value: OptionalMatrix)?
    @ObservationIgnored private var spatialPlotCaches: [(key: SpatialPlotCacheKey, value: HeatmapPlot)] = []
    @ObservationIgnored private var rgbPlotCache: (key: RGBPlotCacheKey, value: RGBPlot)?
    @ObservationIgnored private var timeAxisMetadataCache: TimeAxisMetadata?
    @ObservationIgnored private var timeGroupingCache: TimeGroupingCache?
    @ObservationIgnored private var selectedSourceRangeCache: (key: SelectedSourceRangeCacheKey, value: AxisGroup)?
    @ObservationIgnored private var plotSourceRangeCache: (key: SelectedSourceRangeCacheKey, value: AxisGroup)?
    @ObservationIgnored private var groupResponseCaches: [(key: GroupResponseCacheKey, value: [Double?])] = []
    @ObservationIgnored private var cellAnalysisCaches: [(key: CellAnalysisCacheKey, value: CellAnalysis)] = []
    @ObservationIgnored private var unitQualityFilterCache:
        (key: UnitQualityFilterCacheKey, unitIDs: [Int])?
    @ObservationIgnored private var loadRequestID: UUID?
    @ObservationIgnored private var activeDecodeTask: Task<RFMappingData, Error>?
    @ObservationIgnored var pairingDataDidChange: (() -> Void)?
    @ObservationIgnored var unitQualityVisibilityDidChange:
        ((UnitQualityVisibilityChangeReason) -> Void)?
    private let preferences: UserDefaults
    private let discoversCompanionsAutomatically: Bool

    var data: RFMappingData?
    var availableJSONURLs: [URL] = []
    var selectedJSONPath = ""

    /// File-local original index. `-1` means the shared selection is absent or
    /// hidden by this window's quality/probe filters.
    private(set) var unitIndex = 0
    private(set) var selectedUnitID: Int?
    private(set) var pairedUnitIDs: [Int]?
    var valueMode: ResponseValueMode = .meanFiringRate
    var binIndex = 0
    var rangeStartMS = 0.0
    var rangeEndMS = 1.0
    var plotRangeStartMS = 0.0
    var plotRangeEndMS = 200.0
    var flipY = false
    var palette: RFPalette = .gray
    var polarRadiusMode: PolarRadiusMode = .displayBottomInner
    var spatialPlotFormat: SpatialPlotFormat = .rectangular
    var delayRGBMode: DelayRGBMode = .delay
    var responseFloor = 0.0
    var xBins = 1
    var yBins = 1
    var timeResolutionMS = 1.0
    var smoothRadius = 0
    var selectedTab: PlotTab = .rf

    private(set) var tuningSessionIndex = 1
    private(set) var showWaveform = true
    private(set) var waveformChannelMode: WaveformChannelMode = .sameXColumn
    private(set) var rfFilterUnitsWithZeroBins = true
    private(set) var rfZeroBinThreshold = 1
    private(set) var hdTuning: HDTuningData?
    private(set) var hdTuningURL: URL?
    private(set) var hdTuningError: String?
    private(set) var probeGeometry: ProbeGeometry?
    private(set) var probeGeometryError: String?
    private(set) var waveformArtifact: WaveformArtifactStore?
    private(set) var waveformPayload: WaveformPayload?
    private(set) var waveformError: String?
    private(set) var probeFilteredUnitIDs: Set<Int>?
    var isWaveformZoomed = false

    var selectedCell: CellRef?
    var hoverCell: CellRef?
    var hoverLocation: CGPoint?
    var hoverExtra = ""
    var hoverDisplayBin: Int?
    var timelineRangeAnchor: Int?
    var timelineScrollFraction = 0.0

    var isImporting = false
    var isExporting = false
    var isLoadingData = false
    var isAwaitingStartupDocument = false
    var exportDocument: CSVMatrixDocument?
    var exportFilename = "rf_matrix.csv"
    var errorMessage: String?

    init(
        initialURL: URL? = nil,
        initialData: RFMappingData? = nil,
        loadDefault: Bool = true,
        discoverJSONChoices: Bool = true,
        discoverCompanions: Bool = true,
        unitQualityFilterEnabled: Bool? = nil,
        zeroSpikeBinThreshold: Int? = nil,
        preferences: UserDefaults = .standard
    ) {
        self.preferences = preferences
        discoversCompanionsAutomatically = discoverCompanions
        let storedSession = preferences.integer(forKey: PreferenceKey.tuningSession)
        tuningSessionIndex = max(1, storedSession == 0 ? 1 : storedSession)
        if preferences.object(forKey: PreferenceKey.showWaveform) != nil {
            showWaveform = preferences.bool(forKey: PreferenceKey.showWaveform)
        }
        if let rawMode = preferences.string(forKey: PreferenceKey.waveformChannelMode),
           let mode = WaveformChannelMode(rawValue: rawMode) {
            waveformChannelMode = mode
        }
        if let unitQualityFilterEnabled {
            rfFilterUnitsWithZeroBins = unitQualityFilterEnabled
        } else if preferences.object(forKey: PreferenceKey.rfFilterUnitsWithZeroBins) != nil {
            rfFilterUnitsWithZeroBins = preferences.bool(
                forKey: PreferenceKey.rfFilterUnitsWithZeroBins
            )
        }
        if let zeroSpikeBinThreshold {
            rfZeroBinThreshold = max(1, min(100_000, zeroSpikeBinThreshold))
        } else {
            let storedThreshold = preferences.integer(forKey: PreferenceKey.rfZeroBinThreshold)
            rfZeroBinThreshold = max(
                1,
                min(100_000, storedThreshold == 0 ? 1 : storedThreshold)
            )
        }
        isAwaitingStartupDocument = initialURL == nil && initialData == nil && !loadDefault
        if discoverJSONChoices { refreshJSONChoices() }
        if let initialData {
            figureExportHangTrace(
                "store init adopt begin companions=\(discoversCompanionsAutomatically)"
            )
            adopt(initialData, refreshChoices: discoverJSONChoices)
            figureExportHangTrace("store init adopt end")
        } else if let url = initialURL {
            loadJSON(url)
        } else if loadDefault {
            loadLatestJSON()
        }
    }

    func loadLatestJSON() {
        if let url = JSONDiscovery.latestJSONURL() {
            loadJSON(url)
        } else {
            isAwaitingStartupDocument = false
            errorMessage = "No RF mapping .rfmap or JSON files found. Use Open RF Map to choose one."
        }
    }

    @MainActor
    func loadLatestJSONAsync() async {
        if let url = JSONDiscovery.latestJSONURL() {
            _ = await loadJSONAsync(url)
        } else {
            isLoadingData = false
            isAwaitingStartupDocument = false
            errorMessage = "No RF mapping .rfmap or JSON files found. Use Open RF Map to choose one."
        }
    }

    var hasData: Bool { data != nil }

    var hasSelectedUnit: Bool {
        guard let data, let selectedUnitID else { return false }
        return data.unitIndex(forUnitID: selectedUnitID) == unitIndex && unitIndex >= 0
    }

    var selectedRFMap: RFMap? {
        guard hasSelectedUnit, let data, let selectedUnitID else { return nil }
        return try? data.rfMap(byUnitID: selectedUnitID)
    }

    /// Local source-order IDs that pass the native-grid zero-spike test for
    /// the current 2-D RF sum window. Timeline selection and display
    /// rebinning/smoothing do not participate in this calculation.
    var qualityFilteredUnitIDs: [Int] {
        guard let data else { return [] }
        guard rfFilterUnitsWithZeroBins else { return data.unitPool }
        let source = sourceBinsForPlotRange()
        let key = UnitQualityFilterCacheKey(
            dataID: ObjectIdentifier(data),
            sourceStart: source.start,
            sourceEnd: source.end,
            threshold: rfZeroBinThreshold
        )
        if let unitQualityFilterCache, unitQualityFilterCache.key == key {
            return unitQualityFilterCache.unitIDs
        }
        let unitIDs = data.unitPool.enumerated().compactMap { unitIndex, unitID in
            data.zeroSpikeSpatialBinCount(
                unitIndex: unitIndex,
                start: source.start,
                end: source.end
            ) < rfZeroBinThreshold ? unitID : nil
        }
        unitQualityFilterCache = (key, unitIDs)
        return unitIDs
    }

    var unitQualityFilterStatusText: String {
        guard let data else { return "No RF map loaded" }
        let visible = qualityFilteredUnitIDs.count
        let total = data.unitPool.count
        if !rfFilterUnitsWithZeroBins {
            return "\(visible)/\(total) units visible · zero-spike filter off"
        }
        if visible == 0 {
            return "0/\(total) units visible · no units pass the current RF window"
        }
        return "\(visible)/\(total) units visible · hide when zero-bin count ≥ \(rfZeroBinThreshold)"
    }

    /// Maximum value accepted from this window's live threshold control.
    ///
    /// Persisted values intentionally retain the application-wide 1...100,000
    /// contract when a smaller RF file is opened. Such a value can therefore
    /// exceed this file's native spatial-bin count and make every unit visible.
    /// Only a new user edit is constrained to the active file, matching the
    /// Python Settings window's save-time validation.
    var rfZeroBinThresholdEditMaximum: Int {
        guard let data else { return 100_000 }
        return max(1, min(100_000, data.nY * data.nX))
    }

    var unitQualityFilterSnapshot: RFUnitQualityFilterSnapshot? {
        guard let data else { return nil }
        let source = sourceBinsForPlotRange()
        let visible = qualityFilteredUnitIDs
        let visibleSet = Set(visible)
        return RFUnitQualityFilterSnapshot(
            enabled: rfFilterUnitsWithZeroBins,
            zeroSpikeSpatialBinThreshold: rfZeroBinThreshold,
            sourceStartBin: source.start,
            sourceEndBin: source.end,
            spatialBinCount: data.nY * data.nX,
            visibleUnitIDs: visible,
            excludedUnitIDs: data.unitPool.filter { !visibleSet.contains($0) }
        )
    }

    private var locallyNavigableUnitIDs: [Int] {
        let unitIDs = qualityFilteredUnitIDs
        guard let probeFilteredUnitIDs else { return unitIDs }
        return unitIDs.filter(probeFilteredUnitIDs.contains)
    }

    var navigationUnitIDs: [Int] {
        let unitIDs = pairedUnitIDs ?? qualityFilteredUnitIDs
        guard let probeFilteredUnitIDs else { return unitIDs }
        return unitIDs.filter(probeFilteredUnitIDs.contains)
    }

    /// Explains why the main plot cannot render the current shared selection.
    /// Keeping this classification in the store prevents an all-filtered or
    /// locally hidden unit from being mislabeled as absent from the file.
    var unitUnavailableReason: RFUnitUnavailableReason? {
        guard let data, !hasSelectedUnit else { return nil }
        let qualityVisible = qualityFilteredUnitIDs
        if qualityVisible.isEmpty {
            return .noQualityVisibleUnits(total: data.unitPool.count)
        }
        guard let selectedUnitID else {
            if probeFilteredUnitIDs != nil, locallyNavigableUnitIDs.isEmpty {
                return .noProbeVisibleUnits
            }
            return .noSelection
        }
        guard data.unitIndex(forUnitID: selectedUnitID) != nil else {
            return .pairedMissing(unitID: selectedUnitID)
        }
        guard qualityVisible.contains(selectedUnitID) else {
            return .qualityFiltered(unitID: selectedUnitID)
        }
        if let probeFilteredUnitIDs,
           !probeFilteredUnitIDs.contains(selectedUnitID) {
            return .probeFiltered(unitID: selectedUnitID)
        }
        return .noSelection
    }

    var figureExportCompanions: FigureExportCompanions {
        FigureExportCompanions(
            hdTuning: hdTuning,
            hdError: hdTuningError,
            probeGeometry: probeGeometry,
            probeError: probeGeometryError,
            waveformArtifact: waveformArtifact,
            waveformError: waveformError,
            waveformChannelMode: waveformChannelMode
        )
    }

    var viewerSyncState: ViewerSyncState {
        ViewerSyncState(
            unitID: selectedUnitID ?? data?.unitPool.first ?? 0,
            valueMode: valueMode,
            activeTimeMS: timeGroupCenterMS(binIndex),
            rangeStartMS: rangeStartMS,
            rangeEndMS: rangeEndMS,
            plotRangeStartMS: plotRangeStartMS,
            plotRangeEndMS: plotRangeEndMS,
            timeResolutionMS: timeResolutionMS,
            xBins: xBins,
            yBins: yBins,
            smoothRadius: smoothRadius,
            flipY: flipY,
            palette: palette,
            polarRadiusMode: polarRadiusMode,
            spatialPlotFormat: spatialPlotFormat,
            delayRGBMode: delayRGBMode,
            responseFloor: responseFloor,
            selectedTab: selectedTab,
            selectedCell: selectedCell,
            timelineRangeAnchorMS: timelineRangeAnchor.map(timeGroupCenterMS),
            timelineScrollFraction: timelineScrollFraction
        )
    }

    var dataSummary: String {
        guard let data else { return "No RF map loaded" }
        return """
        \(data.url.path)
        \(data.nUnits) units  \(data.nY) y x \(data.nX) x  \(data.nBins) bins
        Occupancy: \(data.nY) y x \(data.nX) x seconds; firing rate = count / occupancy
        """
    }

    var headerTitle: String {
        guard let data else { return "RF Map Viewer" }
        if qualityFilteredUnitIDs.isEmpty {
            return "No visible units — zero-spike RF-bin filter"
        }
        guard hasSelectedUnit, let selectedUnitID else {
            let missingID = selectedUnitID.map { String($0) } ?? "unknown"
            if let selectedUnitID,
               data.unitIndex(forUnitID: selectedUnitID) != nil,
               !qualityFilteredUnitIDs.contains(selectedUnitID) {
                return "Unit N/A / cluster \(missingID) is hidden by the zero-spike RF-bin filter"
            }
            return "Unit N/A / cluster \(missingID) is not present in this file"
        }
        return "Unit \(String(format: "%03d", unitIndex)) / cluster \(selectedUnitID)"
    }

    var windowTitle: String {
        data.map { "\($0.url.lastPathComponent) — RF Map Viewer" } ?? "RF Map Viewer"
    }

    var statusText: String {
        guard let data else { return "Open an RF mapping .rfmap or JSON file." }
        if qualityFilteredUnitIDs.isEmpty { return unitQualityFilterStatusText }
        if let hoverCell {
            let prefix = hoverExtra.isEmpty ? "Hover" : "Hover \(hoverExtra);"
            return "\(prefix) \(yGroupText(hoverCell.yStart, hoverCell.yEnd)), \(xGroupText(hoverCell.xStart, hoverCell.xEnd))"
        }
        if !hoverExtra.isEmpty {
            return "Hover \(hoverExtra)"
        }
        return "x: \(formatPos(data.xPositions.first ?? 0))..\(formatPos(data.xPositions.last ?? 0))  y: \(formatPos(data.yPositions.first ?? 0))..\(formatPos(data.yPositions.last ?? 0))  time: \(formatMS(timeAxisStartMS()))..\(formatMS(timeAxisEndMS())) ms  value: \(valueMode.rawValue)"
    }

    var unitStatsText: String {
        guard let data, hasSelectedUnit else {
            if self.data != nil, qualityFilteredUnitIDs.isEmpty {
                return unitQualityFilterStatusText
            }
            return selectedUnitID.map { "Cluster \($0): N/A in this file" } ?? ""
        }
        let metrics = data.metrics(for: unitIndex)
        guard let strongestRate = metrics.maxFiringRate else {
            return "Summed RF counts: \(String(format: "%.0f", metrics.totalSpikes))\nNo occupied RF cells"
        }
        let delay = metrics.delayMS[metrics.bestY][metrics.bestX]
        let delayText = delay.map { String(format: "%.1f ms", $0) } ?? "n/a"
        return "Summed RF counts: \(String(format: "%.0f", metrics.totalSpikes))\nStrongest rate cell: yIdx \(metrics.bestY + 1), xIdx \(metrics.bestX + 1) (\(ResponseValueMode.meanFiringRate.format(strongestRate)) Hz)\nRate-cell count-peak delay: \(delayText)"
    }

    var displayedCellText: String {
        guard let cell = hoverCell ?? selectedCell else { return "" }
        let prefix = hoverCell == nil ? "" : "Hover\n"
        return prefix + cellMetricsText(cell, displayBin: hoverDisplayBin)
    }

    var hasTimeSelection: Bool {
        guard let data else { return false }
        let range = sourceBinsForSelectedRange()
        return range.start != 0 || range.end != data.nBins - 1
    }

    private func refreshJSONChoices() {
        availableJSONURLs = JSONDiscovery.discoverJSONFiles(currentURL: data?.url)
        if let data {
            selectedJSONPath = data.url.standardizedFileURL.path
        } else if selectedJSONPath.isEmpty {
            selectedJSONPath = availableJSONURLs.first?.standardizedFileURL.path ?? ""
        }
    }

    @discardableResult
    func loadJSON(path: String) -> Bool {
        guard !path.isEmpty else { return false }
        return loadJSON(URL(fileURLWithPath: path))
    }

    @MainActor
    @discardableResult
    func loadJSONAsync(path: String) async -> Bool {
        guard !path.isEmpty else { return false }
        return await loadJSONAsync(URL(fileURLWithPath: path))
    }

    @MainActor
    @discardableResult
    func loadJSONAsync(_ url: URL) async -> Bool {
        let requestID = UUID()
        loadRequestID = requestID
        isLoadingData = true
        if data == nil { isAwaitingStartupDocument = true }

        activeDecodeTask?.cancel()
        let accessing = url.startAccessingSecurityScopedResource()
        let decodeTask = RFMappingData.makeDecodeTask(url: url)
        activeDecodeTask = decodeTask
        defer {
            if accessing { url.stopAccessingSecurityScopedResource() }
            if loadRequestID == requestID {
                loadRequestID = nil
                activeDecodeTask = nil
                isLoadingData = false
                if data == nil { isAwaitingStartupDocument = false }
            }
        }

        do {
            let loaded = try await withTaskCancellationHandler {
                try await decodeTask.value
            } onCancel: {
                decodeTask.cancel()
            }
            guard loadRequestID == requestID else { return false }
            adopt(loaded)
            return true
        } catch is CancellationError {
            guard loadRequestID == requestID else { return false }
            isAwaitingStartupDocument = false
            return false
        } catch {
            guard loadRequestID == requestID else { return false }
            isAwaitingStartupDocument = false
            errorMessage = error.localizedDescription
            return false
        }
    }

    @discardableResult
    func loadJSON(_ url: URL) -> Bool {
        activeDecodeTask?.cancel()
        activeDecodeTask = nil
        loadRequestID = nil
        isLoadingData = false
        let accessing = url.startAccessingSecurityScopedResource()
        defer {
            if accessing { url.stopAccessingSecurityScopedResource() }
        }

        do {
            let loaded = try RFMappingData(url: url)
            adopt(loaded)
            return true
        } catch {
            isAwaitingStartupDocument = false
            errorMessage = error.localizedDescription
            return false
        }
    }

    private func adopt(_ loaded: RFMappingData, refreshChoices: Bool = true) {
        figureExportHangTrace("store adopt enter")
        isAwaitingStartupDocument = false
        errorMessage = nil
        clearDerivedCaches()
        data = loaded
        selectedJSONPath = loaded.url.path
        unitIndex = 0
        selectedUnitID = loaded.unitPool.first
        binIndex = 0
        rangeStartMS = loaded.timeBinEdges[0] * 1000.0
        rangeEndMS = loaded.timeBinEdges[loaded.nBins] * 1000.0
        xBins = loaded.nX
        yBins = loaded.nY
        timeResolutionMS = baseBinMS()
        valueMode = ResponseValueMode.allCases.contains(valueMode) ? valueMode : .meanFiringRate
        selectedCell = nil
        clearHover()
        timelineRangeAnchor = nil
        timelineScrollFraction = 0
        resetPlotRangeToDefault(notifyUnitVisibility: false)
        figureExportHangTrace("store adopt reset range end")
        normalizeControls()
        figureExportHangTrace("store adopt normalize end")
        ensureSelectedCell()
        figureExportHangTrace("store adopt selected cell end")
        if discoversCompanionsAutomatically {
            figureExportHangTrace("store adopt companions begin")
            discoverCompanions(for: loaded)
            figureExportHangTrace("store adopt companions end")
        } else {
            clearDiscoveredCompanions()
        }
        if refreshChoices { refreshJSONChoices() }
        pairingDataDidChange?()
    }

    /// Applies paired-window state in one normalization pass. A target may use
    /// a different time axis, spatial grid, or unit list, so every local index,
    /// range, and dimension is resolved independently.
    func applyViewerSyncState(
        _ state: ViewerSyncState,
        fields: ViewerSyncFields = .all
    ) {
        guard data != nil else { return }
        let state = viewerSyncState.merging(state, fields: fields)

        valueMode = ResponseValueMode.allCases.contains(state.valueMode)
            ? state.valueMode
            : .meanFiringRate
        timeResolutionMS = finiteOr(state.timeResolutionMS, fallback: baseBinMS())
        xBins = state.xBins
        yBins = state.yBins
        smoothRadius = state.smoothRadius
        flipY = state.flipY
        palette = state.palette
        polarRadiusMode = state.polarRadiusMode
        spatialPlotFormat = state.spatialPlotFormat
        delayRGBMode = state.delayRGBMode
        responseFloor = max(0, finiteOr(state.responseFloor, fallback: 0))
        selectedTab = state.selectedTab

        rangeStartMS = finiteOr(state.rangeStartMS, fallback: timeAxisStartMS())
        rangeEndMS = finiteOr(state.rangeEndMS, fallback: timeAxisEndMS())
        plotRangeStartMS = finiteOr(state.plotRangeStartMS, fallback: timeAxisStartMS())
        plotRangeEndMS = finiteOr(state.plotRangeEndMS, fallback: timeAxisEndMS())
        binIndex = 0
        timelineRangeAnchor = nil

        normalizeControls()
        selectUnitID(state.unitID, resetInteraction: false)
        binIndex = nearestTimeGroupIndex(to: state.activeTimeMS)
        timelineRangeAnchor = state.timelineRangeAnchorMS.map(nearestTimeGroupIndex)
        selectedCell = hasSelectedUnit ? state.selectedCell.map(normalizedCell) : nil
        timelineScrollFraction = max(
            0,
            min(1, finiteOr(state.timelineScrollFraction, fallback: 0))
        )
    }

    func applyTimelineScrollFraction(_ fraction: Double) {
        timelineScrollFraction = max(0, min(1, finiteOr(fraction, fallback: 0)))
    }

    private func normalizedCell(_ cell: CellRef) -> CellRef {
        guard let data else { return cell }
        let rawXMidpoint = cell.xStart + (cell.xEnd - cell.xStart) / 2
        let rawYMidpoint = cell.yStart + (cell.yEnd - cell.yStart) / 2
        let xMidpoint = max(0, min(data.nX - 1, rawXMidpoint))
        let yMidpoint = max(0, min(data.nY - 1, rawYMidpoint))
        let xGroup = xGroups().first {
            $0.start <= xMidpoint && xMidpoint <= $0.end
        } ?? xGroups().first ?? AxisGroup(start: 0, end: 0)
        let yGroup = displayYGroups().first {
            $0.start <= yMidpoint && yMidpoint <= $0.end
        } ?? displayYGroups().first ?? AxisGroup(start: 0, end: 0)
        return CellRef(
            yStart: yGroup.start,
            yEnd: yGroup.end,
            xStart: xGroup.start,
            xEnd: xGroup.end
        )
    }

    private func nearestTimeGroupIndex(to requestedTimeMS: Double) -> Int {
        let time = finiteOr(requestedTimeMS, fallback: timeAxisStartMS())
        let count = timeGroupCount()
        for index in 0..<count {
            let bounds = timeGroupBoundsMS(index)
            let isLast = index == count - 1
            if bounds.0 <= time && (time < bounds.1 || (isLast && time <= bounds.1)) {
                return index
            }
        }
        return (0..<count).min {
            abs(timeGroupCenterMS($0) - time) < abs(timeGroupCenterMS($1) - time)
        } ?? 0
    }

    private func finiteOr(_ value: Double, fallback: Double) -> Double {
        value.isFinite ? value : fallback
    }

    private func clearDerivedCaches() {
        timelineCaches.removeAll(keepingCapacity: true)
        currentMatrixCache = nil
        delayMatrixCache = nil
        spatialPlotCaches.removeAll(keepingCapacity: true)
        rgbPlotCache = nil
        timeAxisMetadataCache = nil
        timeGroupingCache = nil
        selectedSourceRangeCache = nil
        plotSourceRangeCache = nil
        groupResponseCaches.removeAll(keepingCapacity: true)
        cellAnalysisCaches.removeAll(keepingCapacity: true)
    }

    func setValueMode(_ mode: ResponseValueMode) {
        guard data != nil else { return }
        valueMode = mode
        clearHover()
    }

    func stepUnit(_ delta: Int) {
        let unitIDs = navigationUnitIDs
        guard !unitIDs.isEmpty else { return }
        let current = selectedUnitID.flatMap { unitIDs.firstIndex(of: $0) } ?? 0
        let target = (current + delta % unitIDs.count + unitIDs.count) % unitIDs.count
        selectUnitID(unitIDs[target])
    }

    func selectUnit(atOriginalIndex unitIndex: Int) {
        guard let data, data.unitPool.indices.contains(unitIndex) else { return }
        selectUnitID(data.clusterID(for: unitIndex))
    }

    func selectUnitID(_ unitID: Int, resetInteraction: Bool = true) {
        guard let data else { return }
        let locallyQualityVisible = qualityFilteredUnitIDs.contains(unitID)
        guard locallyQualityVisible || pairedUnitIDs?.contains(unitID) == true else { return }
        let localIndex = locallyNavigableUnitIDs.contains(unitID)
            ? data.unitIndex(forUnitID: unitID)
            : nil
        applyResolvedUnitSelection(
            unitID,
            localIndex: localIndex,
            resetInteraction: resetInteraction
        )
    }

    /// Configures the sorted cross-window union used by previous/next. Passing
    /// nil leaves pairing and restores a valid local selection if necessary.
    func setPairedUnitIDs(_ unitIDs: [Int]?) {
        pairedUnitIDs = unitIDs.map { Array(Set($0)).sorted() }
        reconcileUnitSelection()
    }

    func setRFUnitQualityFilterEnabled(_ enabled: Bool) {
        guard enabled != rfFilterUnitsWithZeroBins else { return }
        let previous = qualityFilteredUnitIDs
        rfFilterUnitsWithZeroBins = enabled
        preferences.set(enabled, forKey: PreferenceKey.rfFilterUnitsWithZeroBins)
        unitQualityFilterCache = nil
        reconcileUnitSelection()
        if previous != qualityFilteredUnitIDs {
            unitQualityVisibilityDidChange?(.filterSettings)
        }
    }

    func setRFZeroBinThreshold(_ threshold: Int) {
        let normalized = max(1, min(rfZeroBinThresholdEditMaximum, threshold))
        guard normalized != rfZeroBinThreshold else { return }
        let previous = qualityFilteredUnitIDs
        rfZeroBinThreshold = normalized
        preferences.set(normalized, forKey: PreferenceKey.rfZeroBinThreshold)
        unitQualityFilterCache = nil
        reconcileUnitSelection()
        if previous != qualityFilteredUnitIDs {
            unitQualityVisibilityDidChange?(.filterSettings)
        }
    }

    func setTuningSessionIndex(_ sessionIndex: Int) {
        let normalized = max(1, sessionIndex)
        guard normalized != tuningSessionIndex else { return }
        tuningSessionIndex = normalized
        preferences.set(normalized, forKey: PreferenceKey.tuningSession)
        reloadDiscoveredHDTuning()
    }

    func setShowWaveform(_ visible: Bool) {
        guard visible != showWaveform else { return }
        showWaveform = visible
        preferences.set(visible, forKey: PreferenceKey.showWaveform)
        if visible {
            refreshWaveformPayload()
        } else {
            waveformPayload = nil
            isWaveformZoomed = false
        }
    }

    func setWaveformChannelMode(_ mode: WaveformChannelMode) {
        guard mode != waveformChannelMode else { return }
        waveformChannelMode = mode
        preferences.set(mode.rawValue, forKey: PreferenceKey.waveformChannelMode)
        refreshWaveformPayload()
    }

    func setProbeFilteredUnitIDs(_ unitIDs: Set<Int>?) {
        probeFilteredUnitIDs = unitIDs
        reconcileUnitSelection()
    }

    private func reconcileUnitSelection() {
        guard let data else { return }
        let choices = navigationUnitIDs
        guard !choices.isEmpty else {
            applyResolvedUnitSelection(nil, localIndex: nil, resetInteraction: true)
            return
        }
        let target: Int
        if let selectedUnitID, choices.contains(selectedUnitID) {
            target = selectedUnitID
        } else if let selectedUnitID {
            target = choices.first(where: { $0 > selectedUnitID }) ?? choices[0]
        } else {
            target = choices[0]
        }
        let localIndex = locallyNavigableUnitIDs.contains(target)
            ? data.unitIndex(forUnitID: target)
            : nil
        applyResolvedUnitSelection(
            target,
            localIndex: localIndex,
            resetInteraction: target != selectedUnitID || localIndex != unitIndex
        )
    }

    private func applyResolvedUnitSelection(
        _ unitID: Int?,
        localIndex: Int?,
        resetInteraction: Bool
    ) {
        let resolvedIndex = localIndex ?? -1
        let changed = selectedUnitID != unitID || unitIndex != resolvedIndex
        selectedUnitID = unitID
        unitIndex = resolvedIndex
        guard changed else { return }
        clearDerivedCaches()
        if resetInteraction || resolvedIndex < 0 {
            selectedCell = nil
            clearHover()
        }
        if resolvedIndex >= 0 { ensureSelectedCell() }
        refreshWaveformPayload()
        if resolvedIndex < 0 { isWaveformZoomed = false }
    }

    func setHDTuningURL(_ url: URL) {
        let accessing = url.startAccessingSecurityScopedResource()
        defer { if accessing { url.stopAccessingSecurityScopedResource() } }
        do {
            hdTuning = try HDTuningData(url: url)
            hdTuningURL = url.standardizedFileURL
            hdTuningError = nil
        } catch {
            hdTuning = nil
            hdTuningURL = url.standardizedFileURL
            hdTuningError = error.localizedDescription
            errorMessage = "HD tuning data could not be loaded: \(error.localizedDescription)"
        }
    }

    func setProbePositionsURL(_ url: URL) {
        guard let data else { return }
        let accessing = url.startAccessingSecurityScopedResource()
        defer { if accessing { url.stopAccessingSecurityScopedResource() } }
        let probeName = HDTuningDiscovery.probeName(forRFURL: data.url)
            ?? HDTuningDiscovery.probeName(forRFURL: url)
            ?? "Probe"
        let adjacentChannels = url.deletingLastPathComponent()
            .appendingPathComponent("channels.csv")
        let channelsURL = FileManager.default.fileExists(atPath: adjacentChannels.path)
            ? adjacentChannels.standardizedFileURL
            : nil
        do {
            probeGeometry = try ProbeGeometryDiscovery.load(
                ProbeGeometryPaths(
                    probeName: probeName,
                    positionsURL: url.standardizedFileURL,
                    channelsURL: channelsURL
                ),
                rfUnitIDs: data.unitPool
            )
            probeGeometryError = nil
            setProbeFilteredUnitIDs(nil)
        } catch {
            probeGeometry = nil
            probeGeometryError = error.localizedDescription
            errorMessage = "Probe positions could not be loaded: \(error.localizedDescription)"
        }
    }

    private func discoverCompanions(for loaded: RFMappingData) {
        clearDiscoveredCompanions()
        figureExportHangTrace("store companions hd begin")
        reloadDiscoveredHDTuning()
        figureExportHangTrace("store companions hd end")
        figureExportHangTrace("store companions probe begin")
        if let paths = ProbeGeometryDiscovery.discover(forRFURL: loaded.url) {
            do {
                probeGeometry = try ProbeGeometryDiscovery.load(
                    paths,
                    rfUnitIDs: loaded.unitPool
                )
            } catch {
                probeGeometryError = error.localizedDescription
            }
        }
        figureExportHangTrace("store companions probe end")
        figureExportHangTrace("store companions waveform begin")
        do {
            waveformArtifact = try WaveformArtifactStore.discover(forRFURL: loaded.url)
        } catch {
            waveformError = error.localizedDescription
        }
        figureExportHangTrace("store companions waveform end")
        figureExportHangTrace("store companions payload begin")
        refreshWaveformPayload()
        figureExportHangTrace("store companions payload end")
    }

    private func clearDiscoveredCompanions() {
        hdTuning = nil
        hdTuningURL = nil
        hdTuningError = nil
        probeGeometry = nil
        probeGeometryError = nil
        waveformArtifact = nil
        waveformPayload = nil
        waveformError = nil
        probeFilteredUnitIDs = nil
        isWaveformZoomed = false
    }

    private func reloadDiscoveredHDTuning() {
        guard let data else { return }
        hdTuning = nil
        hdTuningURL = nil
        hdTuningError = nil
        guard let url = HDTuningDiscovery.discover(
            forRFURL: data.url,
            sessionIndex: tuningSessionIndex
        ) else {
            hdTuningError = "No tuning curve was found for exact session \(tuningSessionIndex)."
            return
        }
        hdTuningURL = url
        do {
            hdTuning = try HDTuningData(url: url)
        } catch {
            hdTuningError = error.localizedDescription
        }
    }

    private func refreshWaveformPayload() {
        waveformPayload = nil
        guard showWaveform, let selectedUnitID, hasSelectedUnit else { return }
        guard let waveformArtifact else {
            if waveformError == nil {
                waveformError = "No companion data/waveform/Probe*/manifest.json was found."
            }
            return
        }
        do {
            waveformPayload = try waveformArtifact.payload(
                for: selectedUnitID,
                mode: waveformChannelMode
            )
            waveformError = nil
        } catch {
            waveformError = error.localizedDescription
        }
    }

    func stepBin(_ delta: Int) {
        let maximum = max(0, timeGroupCount() - 1)
        let target = max(0, min(maximum, binIndex + delta))
        selectTimelineBin(target, extending: false)
    }

    func stepTimeResolution(_ deltaMS: Double) {
        timeResolutionMS = max(baseBinMS(), min(totalTimeMS(), timeResolutionMS + deltaMS))
        timelineRangeAnchor = nil
        normalizeControls()
    }

    func clearTimelineSelection() {
        guard let data else { return }
        timelineRangeAnchor = nil
        binIndex = 0
        rangeStartMS = data.timeBinEdges[0] * 1000.0
        rangeEndMS = data.timeBinEdges[data.nBins] * 1000.0
        normalizeControls()
    }

    func normalizeControls() {
        guard let data else { return }
        xBins = max(1, min(data.nX, xBins))
        yBins = max(1, min(data.nY, yBins))
        smoothRadius = max(0, min(3, smoothRadius))
        responseFloor = max(0.0, responseFloor)
        if !ResponseValueMode.allCases.contains(valueMode) { valueMode = .meanFiringRate }

        let base = baseBinMS()
        let requested = max(base, min(totalTimeMS(), timeResolutionMS))
        let groupSize = max(1, min(data.nBins, Int((requested / base).rounded(.toNearestOrEven))))
        timeResolutionMS = Double(groupSize) * base

        let maximum = max(0, timeGroupCount() - 1)
        binIndex = max(0, min(maximum, binIndex))
        if let anchor = timelineRangeAnchor {
            timelineRangeAnchor = max(0, min(maximum, anchor))
        }
        normalizeSelectedTimeRange()
        normalizePlotTimeRange()
        if let selectedCell {
            self.selectedCell = normalizedCell(selectedCell)
        }
    }

    func normalizeSelectedTimeRange() {
        guard let data else { return }
        let source = sourceBinsForSelectedRange()
        rangeStartMS = data.timeBinEdges[source.start] * 1000.0
        rangeEndMS = data.timeBinEdges[source.end + 1] * 1000.0
    }

    func normalizePlotTimeRange(notifyUnitVisibility: Bool = true) {
        guard let data else { return }
        // Text-field bindings assign the requested millisecond value before
        // asking the store to normalize it. Preserve the last computed
        // source-window result so paired windows can detect that this edit
        // changed the shared quality-filter union.
        let previousVisibleUnitIDs: [Int]
        if rfFilterUnitsWithZeroBins,
           let cached = unitQualityFilterCache,
           cached.key.dataID == ObjectIdentifier(data),
           cached.key.threshold == rfZeroBinThreshold {
            previousVisibleUnitIDs = cached.unitIDs
        } else {
            previousVisibleUnitIDs = qualityFilteredUnitIDs
        }
        let source = sourceBinsForPlotRange()
        plotRangeStartMS = data.timeBinEdges[source.start] * 1000.0
        plotRangeEndMS = data.timeBinEdges[source.end + 1] * 1000.0
        unitQualityFilterCache = nil
        reconcileUnitSelection()
        if notifyUnitVisibility,
           previousVisibleUnitIDs != qualityFilteredUnitIDs {
            unitQualityVisibilityDidChange?(.plotRange)
        }
    }

    func resetPlotRangeToDefault(notifyUnitVisibility: Bool = true) {
        guard data != nil else { return }
        let axisStart = timeAxisStartMS()
        let axisEnd = timeAxisEndMS()
        plotRangeStartMS = max(axisStart, min(axisEnd, 0.0))
        plotRangeEndMS = max(axisStart, min(axisEnd, 200.0))
        normalizePlotTimeRange(notifyUnitVisibility: notifyUnitVisibility)
    }

    func ensureSelectedCell() {
        guard selectedCell == nil,
              let data,
              hasSelectedUnit,
              data.metrics(for: unitIndex).maxFiringRate != nil else {
            return
        }
        let metrics = data.metrics(for: unitIndex)
        selectedCell = normalizedCell(CellRef(
            yStart: metrics.bestY,
            yEnd: metrics.bestY,
            xStart: metrics.bestX,
            xEnd: metrics.bestX
        ))
    }

    func setHover(_ cell: CellRef, location: CGPoint, extra: String = "", displayBin: Int? = nil) {
        if hoverCell == cell, hoverExtra == extra, hoverDisplayBin == displayBin {
            return
        }
        hoverCell = cell
        hoverLocation = location
        hoverExtra = extra
        hoverDisplayBin = displayBin
    }

    func setTimelineBinHover(_ displayBin: Int) {
        let extra = "bin \(timeGroupLabel(displayBin))"
        if hoverCell == nil, hoverExtra == extra, hoverDisplayBin == displayBin {
            return
        }
        hoverCell = nil
        hoverLocation = nil
        hoverExtra = extra
        hoverDisplayBin = displayBin
    }

    func clearHover() {
        guard hoverCell != nil || hoverLocation != nil || !hoverExtra.isEmpty || hoverDisplayBin != nil else {
            return
        }
        hoverCell = nil
        hoverLocation = nil
        hoverExtra = ""
        hoverDisplayBin = nil
    }

    func selectCell(_ cell: CellRef) {
        selectedCell = cell
        clearHover()
    }

    func selectTimelineBin(_ bin: Int, extending: Bool) {
        guard let data else { return }
        let groups = timeGroups()
        let safeBin = max(0, min(groups.count - 1, bin))
        if extending {
            if timelineRangeAnchor == nil {
                timelineRangeAnchor = displayRangeIndices().start
            }
            let anchor = timelineRangeAnchor ?? safeBin
            let start = min(anchor, safeBin)
            let end = max(anchor, safeBin)
            let sourceStart = groups[start].start
            let sourceEnd = groups[end].end
            rangeStartMS = data.timeBinEdges[sourceStart] * 1000.0
            rangeEndMS = data.timeBinEdges[sourceEnd + 1] * 1000.0
            timelineRangeAnchor = safeBin
        } else {
            let source = groups[safeBin]
            rangeStartMS = data.timeBinEdges[source.start] * 1000.0
            rangeEndMS = data.timeBinEdges[source.end + 1] * 1000.0
            timelineRangeAnchor = safeBin
        }
        binIndex = safeBin
        normalizeControls()
    }

    func baseBinMS() -> Double {
        timeAxisMetadata().baseBinMS
    }

    func timeAxisStartMS() -> Double {
        timeAxisMetadata().startMS
    }

    func timeAxisEndMS() -> Double {
        timeAxisMetadata().endMS
    }

    func timeAxisRangeMS() -> (Double, Double) {
        let metadata = timeAxisMetadata()
        return (metadata.startMS, metadata.endMS)
    }

    func totalTimeMS() -> Double {
        timeAxisMetadata().totalMS
    }

    func timeGroupSize() -> Int {
        guard let data else { return 1 }
        let base = baseBinMS()
        let requested = max(base, min(totalTimeMS(), timeResolutionMS))
        return max(1, min(data.nBins, Int((requested / base).rounded(.toNearestOrEven))))
    }

    func timeGroups() -> [AxisGroup] {
        timeGrouping().groups
    }

    func timeGroupCount() -> Int { max(1, timeGroups().count) }

    func timeGroupBoundsMS(_ displayBin: Int) -> (Double, Double) {
        let grouping = timeGrouping()
        let index = max(0, min(grouping.bounds.count - 1, displayBin))
        return grouping.bounds[index]
    }

    func timeGroupLabel(_ displayBin: Int) -> String {
        let grouping = timeGrouping()
        let index = max(0, min(grouping.labels.count - 1, displayBin))
        return grouping.labels[index]
    }

    func timeGroupIntervalLabel(_ displayBin: Int) -> String {
        let grouping = timeGrouping()
        let index = max(0, min(grouping.intervalLabels.count - 1, displayBin))
        return grouping.intervalLabels[index]
    }

    func timeGroupCenterMS(_ displayBin: Int) -> Double {
        let grouping = timeGrouping()
        let index = max(0, min(grouping.centers.count - 1, displayBin))
        return grouping.centers[index]
    }

    func sourceBinsForDisplayBin(_ displayBin: Int) -> AxisGroup {
        let groups = timeGrouping().groups
        return groups[max(0, min(groups.count - 1, displayBin))]
    }

    func sourceBinsForSelectedRange() -> AxisGroup {
        guard let data else { return AxisGroup(start: 0, end: 0) }
        let key = SelectedSourceRangeCacheKey(
            dataID: ObjectIdentifier(data),
            startMS: rangeStartMS,
            endMS: rangeEndMS
        )
        if let selectedSourceRangeCache, selectedSourceRangeCache.key == key {
            return selectedSourceRangeCache.value
        }
        let result = sourceBins(startMS: rangeStartMS, endMS: rangeEndMS, data: data)
        selectedSourceRangeCache = (key, result)
        return result
    }

    func sourceBinsForPlotRange() -> AxisGroup {
        guard let data else { return AxisGroup(start: 0, end: 0) }
        let key = SelectedSourceRangeCacheKey(
            dataID: ObjectIdentifier(data),
            startMS: plotRangeStartMS,
            endMS: plotRangeEndMS
        )
        if let plotSourceRangeCache, plotSourceRangeCache.key == key {
            return plotSourceRangeCache.value
        }
        let result = sourceBins(startMS: plotRangeStartMS, endMS: plotRangeEndMS, data: data)
        plotSourceRangeCache = (key, result)
        return result
    }

    private func sourceBins(
        startMS: Double,
        endMS: Double,
        data: RFMappingData
    ) -> AxisGroup {
        let metadata = timeAxisMetadata()
        let edgesMS = metadata.edgesMS
        let axisStart = metadata.startMS
        let axisEnd = metadata.endMS
        var requestedStart = max(axisStart, min(axisEnd, startMS))
        var requestedEnd = max(axisStart, min(axisEnd, endMS))
        if requestedStart > requestedEnd { swap(&requestedStart, &requestedEnd) }

        let startEdge = nearestEdgeIndex(
            edgesMS,
            target: requestedStart,
            lowerBound: 0,
            upperBound: data.nBins - 1
        )
        var endEdge = nearestEdgeIndex(
            edgesMS,
            target: requestedEnd,
            lowerBound: 1,
            upperBound: data.nBins
        )
        if endEdge <= startEdge { endEdge = min(data.nBins, startEdge + 1) }
        return AxisGroup(start: startEdge, end: endEdge - 1)
    }

    private func timeAxisMetadata() -> TimeAxisMetadata {
        guard let data else {
            return TimeAxisMetadata(
                dataID: ObjectIdentifier(self),
                edgesMS: [0, 1],
                startMS: 0,
                endMS: 1,
                totalMS: 1,
                baseBinMS: 1
            )
        }
        let dataID = ObjectIdentifier(data)
        if let timeAxisMetadataCache, timeAxisMetadataCache.dataID == dataID {
            return timeAxisMetadataCache
        }
        let edgesMS = data.timeBinEdges.map { $0 * 1000.0 }
        var base = Double.infinity
        for index in 0..<data.nBins {
            let width = edgesMS[index + 1] - edgesMS[index]
            if width > 1e-9 { base = min(base, width) }
        }
        if !base.isFinite { base = 1 }
        let start = edgesMS.first ?? 0
        let rawEnd = edgesMS.last ?? (start + base)
        let end = rawEnd > start ? rawEnd : start + base
        let metadata = TimeAxisMetadata(
            dataID: dataID,
            edgesMS: edgesMS,
            startMS: start,
            endMS: end,
            totalMS: max(end - start, base),
            baseBinMS: base
        )
        timeAxisMetadataCache = metadata
        return metadata
    }

    private func timeGrouping() -> TimeGroupingCache {
        guard let data else {
            return TimeGroupingCache(
                dataID: ObjectIdentifier(self),
                groupSize: 1,
                groups: [AxisGroup(start: 0, end: 0)],
                bounds: [(0, 1)],
                labels: ["0 ms"],
                intervalLabels: ["0–1 ms"],
                centers: [0.5]
            )
        }
        let dataID = ObjectIdentifier(data)
        let size = timeGroupSize()
        if let timeGroupingCache,
           timeGroupingCache.dataID == dataID,
           timeGroupingCache.groupSize == size {
            return timeGroupingCache
        }
        let metadata = timeAxisMetadata()
        let groups = stride(from: 0, to: data.nBins, by: size).map {
            AxisGroup(start: $0, end: min($0 + size - 1, data.nBins - 1))
        }
        let bounds = groups.map {
            (metadata.edgesMS[$0.start], metadata.edgesMS[$0.end + 1])
        }
        let grouping = TimeGroupingCache(
            dataID: dataID,
            groupSize: size,
            groups: groups,
            bounds: bounds,
            labels: bounds.map { "\(formatMS($0.0)) ms" },
            intervalLabels: bounds.map { "\(formatMS($0.0))–\(formatMS($0.1)) ms" },
            centers: bounds.map { ($0.0 + $0.1) / 2 }
        )
        timeGroupingCache = grouping
        return grouping
    }

    private func nearestEdgeIndex(
        _ edges: [Double],
        target: Double,
        lowerBound: Int,
        upperBound: Int
    ) -> Int {
        var low = lowerBound
        var high = upperBound
        while low < high {
            let middle = (low + high) / 2
            if edges[middle] < target {
                low = middle + 1
            } else {
                high = middle
            }
        }
        if low <= lowerBound { return lowerBound }
        let previous = low - 1
        return abs(edges[previous] - target) <= abs(edges[low] - target) ? previous : low
    }

    func displayRangeIndices() -> AxisGroup {
        let groups = timeGroups()
        let source = sourceBinsForSelectedRange()
        let start = groups.firstIndex { $0.start <= source.start && source.start <= $0.end } ?? 0
        let end = groups.firstIndex { $0.start <= source.end && source.end <= $0.end } ?? max(0, groups.count - 1)
        return AxisGroup(start: min(start, end), end: max(start, end))
    }

    func selectedTimeBoundsMS() -> (Double, Double) {
        guard let data else { return (0.0, 1.0) }
        let source = sourceBinsForSelectedRange()
        return (data.timeBinEdges[source.start] * 1000.0, data.timeBinEdges[source.end + 1] * 1000.0)
    }

    func plotTimeBoundsMS() -> (Double, Double) {
        guard let data else { return (0.0, 1.0) }
        let source = sourceBinsForPlotRange()
        return (data.timeBinEdges[source.start] * 1000.0, data.timeBinEdges[source.end + 1] * 1000.0)
    }

    func plotDisplayRangeIndices() -> AxisGroup {
        let groups = timeGroups()
        let source = sourceBinsForPlotRange()
        let start = groups.firstIndex { $0.start <= source.start && source.start <= $0.end } ?? 0
        let end = groups.firstIndex { $0.start <= source.end && source.end <= $0.end } ?? max(0, groups.count - 1)
        return AxisGroup(start: min(start, end), end: max(start, end))
    }

    func selectedRangeOverlaps(_ group: AxisGroup) -> Bool {
        let selected = sourceBinsForSelectedRange()
        return group.start <= selected.end && group.end >= selected.start
    }

    func currentMatrix() -> OptionalMatrix {
        guard let data, hasSelectedUnit else { return [] }
        let range = sourceBinsForPlotRange()
        let key = RawMatrixCacheKey(
            dataID: ObjectIdentifier(data),
            unitIndex: unitIndex,
            valueMode: valueMode,
            sourceStart: range.start,
            sourceEnd: range.end
        )
        if let currentMatrixCache, currentMatrixCache.key == key {
            return currentMatrixCache.value
        }
        let matrix = (try? data.responseMatrix(
            unitIndex: unitIndex,
            start: range.start,
            end: range.end,
            valueMode: valueMode
        )) ?? []
        currentMatrixCache = (key, matrix)
        return matrix
    }

    func delayMatrixForTimeGroups(floor: Double = 0.0) -> OptionalMatrix {
        guard let data, hasSelectedUnit else { return [] }
        let safeFloor = max(0.0, floor)
        let key = DelayCacheKey(
            dataID: ObjectIdentifier(data),
            unitIndex: unitIndex,
            timeGroupSize: timeGroupSize(),
            floor: safeFloor
        )
        if let delayMatrixCache, delayMatrixCache.key == key {
            return delayMatrixCache.value
        }
        let metrics = data.metrics(for: unitIndex)
        let grouping = timeGrouping()
        let matrix: OptionalMatrix = (0..<data.nY).map { yIndex in
            (0..<data.nX).map { xIndex -> Double? in
                guard metrics.total[yIndex][xIndex] > safeFloor else { return nil }
                var peakIndex = 0
                var peakCount = 0.0
                for (index, group) in grouping.groups.enumerated() {
                    let count = data.rangeCount(
                        unitIndex: unitIndex,
                        yIndex: yIndex,
                        xIndex: xIndex,
                        start: group.start,
                        end: group.end
                    )
                    if count > peakCount {
                        peakIndex = index
                        peakCount = count
                    }
                }
                return peakCount > 0 ? grouping.centers[peakIndex] : nil
            }
        }
        delayMatrixCache = (key, matrix)
        return matrix
    }

    func currentHeatmapPlot() -> HeatmapPlot {
        guard let data, hasSelectedUnit else { return emptyHeatmapPlot() }
        let range = sourceBinsForPlotRange()
        let key = SpatialPlotCacheKey(
            kind: .current,
            dataID: ObjectIdentifier(data),
            unitIndex: unitIndex,
            valueMode: valueMode,
            sourceStart: range.start,
            sourceEnd: range.end,
            timeGroupSize: 0,
            floor: 0,
            xBins: xBins,
            yBins: yBins,
            flipY: flipY,
            smoothRadius: smoothRadius
        )
        if let cached = spatialPlot(for: key) { return cached }
        let prepared = prepareResponsePlotMatrix(
            sourceStart: range.start,
            sourceEnd: range.end,
            smooth: true
        )
        let valueRange = finiteMinMax(prepared.0)
        let plot = HeatmapPlot(
            matrix: prepared.0,
            xGroups: prepared.1,
            yGroups: prepared.2,
            low: valueRange.0,
            high: valueRange.1
        )
        cacheSpatialPlot(plot, for: key)
        return plot
    }

    func delayHeatmapPlot(floor: Double) -> HeatmapPlot {
        guard let data, hasSelectedUnit else { return emptyHeatmapPlot() }
        let safeFloor = max(0.0, floor)
        let key = SpatialPlotCacheKey(
            kind: .delay,
            dataID: ObjectIdentifier(data),
            unitIndex: unitIndex,
            valueMode: .spikeCount,
            sourceStart: 0,
            sourceEnd: data.nBins - 1,
            timeGroupSize: timeGroupSize(),
            floor: safeFloor,
            xBins: xBins,
            yBins: yBins,
            flipY: flipY,
            smoothRadius: smoothRadius
        )
        if let cached = spatialPlot(for: key) { return cached }
        let prepared = preparePlotMatrix(delayMatrixForTimeGroups(floor: safeFloor), smooth: true)
        let range = timeAxisRangeMS()
        let plot = HeatmapPlot(
            matrix: prepared.0,
            xGroups: prepared.1,
            yGroups: prepared.2,
            low: range.0,
            high: range.1
        )
        cacheSpatialPlot(plot, for: key)
        return plot
    }

    func cachedRGBPlot() -> RGBPlot {
        guard let data, hasSelectedUnit else {
            let empty = emptyHeatmapPlot()
            return RGBPlot(
                total: [],
                delay: [],
                entropy: [],
                reference: empty,
                maxTotal: 1,
                minDelay: 0,
                delaySpan: 1
            )
        }
        let key = RGBPlotCacheKey(
            dataID: ObjectIdentifier(data),
            unitIndex: unitIndex,
            valueMode: valueMode,
            timeGroupSize: timeGroupSize(),
            xBins: xBins,
            yBins: yBins,
            flipY: flipY,
            smoothRadius: smoothRadius
        )
        if let rgbPlotCache, rgbPlotCache.key == key {
            return rgbPlotCache.value
        }

        let totalPrepared = prepareResponsePlotMatrix(
            sourceStart: 0,
            sourceEnd: data.nBins - 1,
            smooth: true
        )
        let delayPrepared = preparePlotMatrix(delayMatrixForTimeGroups(floor: 0.0))
        let entropyPrepared = preparePlotMatrix(optionalMatrix(data.metrics(for: unitIndex).entropy))
        let responseRange = finiteMinMax(totalPrepared.0)
        let maxResponse = max(responseRange.1, 1.0)
        let reference = HeatmapPlot(
            matrix: totalPrepared.0,
            xGroups: totalPrepared.1,
            yGroups: totalPrepared.2,
            low: 0,
            high: maxResponse
        )
        let timeRange = timeAxisRangeMS()
        let plot = RGBPlot(
            total: totalPrepared.0,
            delay: delayPrepared.0,
            entropy: entropyPrepared.0,
            reference: reference,
            maxTotal: maxResponse,
            minDelay: timeRange.0,
            delaySpan: max(timeRange.1 - timeRange.0, 1.0)
        )
        rgbPlotCache = (key, plot)
        return plot
    }

    private func emptyHeatmapPlot() -> HeatmapPlot {
        HeatmapPlot(matrix: [], xGroups: [], yGroups: [], low: 0, high: 1)
    }

    private func spatialPlot(for key: SpatialPlotCacheKey) -> HeatmapPlot? {
        guard let index = spatialPlotCaches.firstIndex(where: { $0.key == key }) else { return nil }
        let cached = spatialPlotCaches.remove(at: index)
        spatialPlotCaches.insert(cached, at: 0)
        return cached.value
    }

    private func cacheSpatialPlot(_ plot: HeatmapPlot, for key: SpatialPlotCacheKey) {
        spatialPlotCaches.insert((key, plot), at: 0)
        if spatialPlotCaches.count > 6 { spatialPlotCaches.removeLast() }
    }

    func timelineMatrix(for displayBin: Int) -> OptionalMatrix {
        guard let data, hasSelectedUnit else { return [] }
        let source = sourceBinsForDisplayBin(displayBin)
        return (try? data.responseMatrix(
            unitIndex: unitIndex,
            start: source.start,
            end: source.end,
            valueMode: valueMode
        )) ?? []
    }

    func timelineSnapshot() -> TimelineMatrixSnapshot {
        guard let data, hasSelectedUnit else {
            return TimelineMatrixSnapshot(
                timeGroups: [AxisGroup(start: 0, end: 0)],
                matrices: [],
                totals: [],
                sharedHigh: 1.0
            )
        }
        let key = TimelineCacheKey(
            dataID: ObjectIdentifier(data),
            unitIndex: unitIndex,
            valueMode: valueMode,
            timeGroupSize: timeGroupSize(),
            xBins: xBins,
            yBins: yBins,
            flipY: flipY,
            smoothRadius: smoothRadius
        )
        if let index = timelineCaches.firstIndex(where: { $0.key == key }) {
            let cached = timelineCaches.remove(at: index)
            timelineCaches.insert(cached, at: 0)
            return cached.value
        }

        let groups = timeGrouping().groups
        let xGroups = xGroups()
        let yGroups = displayYGroups()
        var matrices: [OptionalMatrix] = []
        matrices.reserveCapacity(groups.count)
        var sharedHigh = 0.0
        for group in groups {
            let prepared = responsePlotMatrix(
                sourceStart: group.start,
                sourceEnd: group.end,
                yGroups: yGroups,
                xGroups: xGroups,
                smoothingRadius: smoothRadius
            )
            for row in prepared {
                for value in row {
                    if let value, value.isFinite { sharedHigh = max(sharedHigh, value) }
                }
            }
            matrices.append(prepared)
        }
        let snapshot = TimelineMatrixSnapshot(
            timeGroups: groups,
            matrices: matrices,
            totals: allPositionsTimelineValues(groups: groups),
            sharedHigh: max(sharedHigh, 1.0)
        )
        timelineCaches.insert((key, snapshot), at: 0)
        if timelineCaches.count > 3 { timelineCaches.removeLast() }
        return snapshot
    }

    func allPositionsTimelineValues() -> [Double] {
        allPositionsTimelineValues(groups: timeGrouping().groups)
    }

    private func allPositionsTimelineValues(groups: [AxisGroup]) -> [Double] {
        guard let data, hasSelectedUnit else { return [] }
        if valueMode == .spikeCount {
            let totals = data.metrics(for: unitIndex).binTotals
            return groups.map { compensatedSum(totals[$0.start...$0.end]) }
        }
        let occupancyTotal = compensatedSum(
            data.occupancyTimeSeconds.flatMap { row in row.filter { $0 > 0 } }
        )
        guard occupancyTotal > 0 else {
            return Array(repeating: 0.0, count: groups.count)
        }
        return groups.map { group in
            var cellCounts: [Double] = []
            cellCounts.reserveCapacity(data.nY * data.nX)
            for yIndex in 0..<data.nY {
                for xIndex in 0..<data.nX {
                    cellCounts.append(data.rangeCount(
                        unitIndex: unitIndex,
                        yIndex: yIndex,
                        xIndex: xIndex,
                        start: group.start,
                        end: group.end
                    ))
                }
            }
            let count = compensatedSum(cellCounts)
            return count / occupancyTotal
        }
    }

    func timeGroupedHist(_ hist: [Double]) -> [Double] {
        timeGroups().map { compensatedSum(hist[$0.start...$0.end]) }
    }

    func visibleTimelineBins(displayBins: Int) -> [Int] {
        Array(0..<max(0, displayBins))
    }

    func xGroups() -> [AxisGroup] {
        guard let data else { return [] }
        return axisGroupsForTarget(sourceCount: data.nX, targetCount: xBins)
    }

    func displayYGroups() -> [AxisGroup] {
        guard let data else { return [] }
        var groups = axisGroupsForTarget(sourceCount: data.nY, targetCount: yBins)
        if flipY { groups.reverse() }
        return groups
    }

    private func prepareResponsePlotMatrix(
        sourceStart: Int,
        sourceEnd: Int,
        smooth: Bool
    ) -> (OptionalMatrix, [AxisGroup], [AxisGroup]) {
        let xGroups = xGroups()
        let yGroups = displayYGroups()
        let matrix = responsePlotMatrix(
            sourceStart: sourceStart,
            sourceEnd: sourceEnd,
            yGroups: yGroups,
            xGroups: xGroups,
            smoothingRadius: smooth ? smoothRadius : 0
        )
        return (matrix, xGroups, yGroups)
    }

    /// Pools source counts and occupancy independently before normalization.
    /// This keeps sparsely occupied source positions from receiving the same
    /// weight as well-observed positions when display bins are merged or
    /// smoothed.
    private func responsePlotMatrix(
        sourceStart: Int,
        sourceEnd: Int,
        yGroups: [AxisGroup],
        xGroups: [AxisGroup],
        smoothingRadius: Int
    ) -> OptionalMatrix {
        guard let data, hasSelectedUnit, !xGroups.isEmpty, !yGroups.isEmpty else {
            return []
        }
        let observations = yGroups.map { yGroup in
            xGroups.map { xGroup in
                data.spatialObservations(
                    unitIndex: unitIndex,
                    yGroup: yGroup,
                    xGroup: xGroup,
                    start: sourceStart,
                    end: sourceEnd
                )
            }
        }
        let valid = observations.map { row in
            row.map { $0.sourcePixelCount > 0 && $0.occupancyTimeSeconds > 0 }
        }

        if valueMode == .spikeCount {
            var matrix: OptionalMatrix = observations.map { row in
                row.map { value in
                    guard value.sourcePixelCount > 0 else { return nil }
                    return value.count / Double(value.sourcePixelCount)
                }
            }
            matrix = smoothMatrix(matrix, radius: smoothingRadius)
            return matrix.enumerated().map { yIndex, row in
                row.enumerated().map { xIndex, value in
                    valid[yIndex][xIndex] ? value : nil
                }
            }
        }

        var pooledCounts: OptionalMatrix = observations.map { row in
            row.map { value in value.sourcePixelCount > 0 ? value.count : nil }
        }
        var pooledOccupancy: OptionalMatrix = observations.map { row in
            row.map { value in
                value.sourcePixelCount > 0 ? value.occupancyTimeSeconds : nil
            }
        }
        pooledCounts = smoothMatrix(pooledCounts, radius: smoothingRadius)
        pooledOccupancy = smoothMatrix(pooledOccupancy, radius: smoothingRadius)
        return pooledCounts.enumerated().map { yIndex, row in
            row.enumerated().map { xIndex, count in
                guard valid[yIndex][xIndex],
                      let count,
                      let occupancy = pooledOccupancy[yIndex][xIndex],
                      occupancy > 0 else {
                    return nil
                }
                return count / occupancy
            }
        }
    }

    func preparePlotMatrix(_ matrix: OptionalMatrix, smooth: Bool = true) -> (OptionalMatrix, [AxisGroup], [AxisGroup]) {
        let xGroups = xGroups()
        let yGroups = displayYGroups()
        guard !matrix.isEmpty, !xGroups.isEmpty, !yGroups.isEmpty else { return ([], xGroups, yGroups) }
        var prepared = reduceMatrixXY(matrix, yGroups: yGroups, xGroups: xGroups)
        if smooth { prepared = smoothMatrix(prepared, radius: smoothRadius) }
        return (prepared, xGroups, yGroups)
    }

    func groupHist(_ cell: CellRef) -> [Double] {
        guard let data, hasSelectedUnit else { return [] }
        var hist = Array(repeating: 0.0, count: data.nBins)
        let pixelCount = max(1, (cell.yEnd - cell.yStart + 1) * (cell.xEnd - cell.xStart + 1))
        guard let unit = selectedRFMap?.spikeCounts else { return [] }
        for yIndex in cell.yStart...cell.yEnd {
            for xIndex in cell.xStart...cell.xEnd {
                for bin in 0..<data.nBins {
                    hist[bin] += unit[yIndex][xIndex][bin] / Double(pixelCount)
                }
            }
        }
        return hist
    }

    func groupResponseValue(_ cell: CellRef, sourceStart: Int, sourceEnd: Int) -> Double? {
        guard let data, hasSelectedUnit else { return nil }
        let observations = data.spatialObservations(
            unitIndex: unitIndex,
            yGroup: AxisGroup(start: cell.yStart, end: cell.yEnd),
            xGroup: AxisGroup(start: cell.xStart, end: cell.xEnd),
            start: sourceStart,
            end: sourceEnd
        )
        guard observations.sourcePixelCount > 0,
              observations.occupancyTimeSeconds > 0 else {
            return nil
        }
        switch valueMode {
        case .spikeCount:
            return observations.count / Double(observations.sourcePixelCount)
        case .meanFiringRate:
            return observations.count / observations.occupancyTimeSeconds
        }
    }

    func groupResponseValues(_ cell: CellRef) -> [Double?] {
        guard let data, hasSelectedUnit else { return [] }
        let key = GroupResponseCacheKey(
            dataID: ObjectIdentifier(data),
            unitIndex: unitIndex,
            valueMode: valueMode,
            timeGroupSize: timeGroupSize(),
            cell: cell
        )
        if let index = groupResponseCaches.firstIndex(where: { $0.key == key }) {
            let cached = groupResponseCaches.remove(at: index)
            groupResponseCaches.insert(cached, at: 0)
            return cached.value
        }
        let values = timeGrouping().groups.map {
            groupResponseValue(cell, sourceStart: $0.start, sourceEnd: $0.end)
        }
        groupResponseCaches.insert((key, values), at: 0)
        if groupResponseCaches.count > 12 { groupResponseCaches.removeLast() }
        return values
    }

    func yGroupText(_ yStart: Int, _ yEnd: Int) -> String {
        guard let data else { return "" }
        if yStart == yEnd { return "yIdx \(yStart + 1); y \(formatPos(data.yPositions[yStart]))" }
        return "yIdx \(yStart + 1)-\(yEnd + 1); y \(formatPos(data.yPositions[yStart]))..\(formatPos(data.yPositions[yEnd]))"
    }

    func xGroupText(_ xStart: Int, _ xEnd: Int) -> String {
        guard let data else { return "" }
        if xStart == xEnd { return "xIdx \(xStart + 1); x \(formatPos(data.xPositions[xStart]))" }
        return "xIdx \(xStart + 1)-\(xEnd + 1); x \(formatPos(data.xPositions[xStart]))..\(formatPos(data.xPositions[xEnd]))"
    }

    func currentMatrixLabel() -> String {
        let bounds = plotTimeBoundsMS()
        return "\(valueMode.rawValue): \(formatMS(bounds.0)) to \(formatMS(bounds.1)) ms"
    }

    func cellMetricsText(_ cell: CellRef, displayBin: Int? = nil) -> String {
        guard let data, hasSelectedUnit else { return "" }
        let analysis = cellAnalysis(for: cell)
        let displayValues = analysis.displayValues
        guard !displayValues.isEmpty else { return "" }
        let bin = max(0, min(displayValues.count - 1, displayBin ?? binIndex))

        let grouped = cell.yStart != cell.yEnd || cell.xStart != cell.xEnd
        let groupNote = grouped ? "avg over source pixels\n" : ""
        let peakText = analysis.peakBin.map { "\($0 + 1) (\(timeGroupLabel($0)))" } ?? "n/a"
        let delayText = analysis.delayMS.map { String(format: "%.1f ms", $0) } ?? "n/a"
        let plotBounds = plotTimeBoundsMS()
        return """
        cluster \(data.clusterID(for: unitIndex))
        \(yGroupText(cell.yStart, cell.yEnd)), \(xGroupText(cell.xStart, cell.xEnd))
        \(groupNote)bin \(valueMode.format(displayValues[bin])) \(valueMode.unit) (\(timeGroupLabel(bin)))
        RF sum range \(formatMS(plotBounds.0))–\(formatMS(plotBounds.1)) ms: \(valueMode.format(analysis.selectedValue)) \(valueMode.unit)
        full window \(valueMode.format(analysis.totalValue)) \(valueMode.unit)
        peak \(valueMode.format(analysis.peakValue)) \(valueMode.unit)
        peak bin \(peakText)
        delay \(delayText), count entropy \(String(format: "%.3f", analysis.entropy))
        """
    }

    func tooltipText(_ cell: CellRef, displayBin: Int? = nil) -> String {
        guard data != nil, hasSelectedUnit else { return "" }
        let analysis = cellAnalysis(for: cell)
        let values = analysis.displayValues
        guard !values.isEmpty else { return "" }
        let bin = max(0, min(values.count - 1, displayBin ?? binIndex))
        let plotBounds = plotTimeBoundsMS()
        return [
            yGroupText(cell.yStart, cell.yEnd),
            xGroupText(cell.xStart, cell.xEnd),
            "bin \(bin + 1): \(valueMode.format(values[bin])) \(valueMode.unit)",
            "RF sum range \(formatMS(plotBounds.0))–\(formatMS(plotBounds.1)) ms: \(valueMode.format(analysis.selectedValue)) \(valueMode.unit)",
            "full window: \(valueMode.format(analysis.totalValue)) \(valueMode.unit)",
            analysis.delayMS.map { String(format: "delay %.1f ms", $0) } ?? "delay n/a"
        ].joined(separator: "\n")
    }

    private func cellAnalysis(for cell: CellRef) -> CellAnalysis {
        guard let data, hasSelectedUnit else {
            return CellAnalysis(
                displayValues: [],
                countHist: [],
                selectedValue: nil,
                totalValue: nil,
                peakBin: nil,
                peakValue: nil,
                delayMS: nil,
                entropy: 0
            )
        }
        let selected = sourceBinsForPlotRange()
        let responseKey = GroupResponseCacheKey(
            dataID: ObjectIdentifier(data),
            unitIndex: unitIndex,
            valueMode: valueMode,
            timeGroupSize: timeGroupSize(),
            cell: cell
        )
        let key = CellAnalysisCacheKey(
            responseKey: responseKey,
            selectedStart: selected.start,
            selectedEnd: selected.end
        )
        if let index = cellAnalysisCaches.firstIndex(where: { $0.key == key }) {
            let cached = cellAnalysisCaches.remove(at: index)
            cellAnalysisCaches.insert(cached, at: 0)
            return cached.value
        }

        let displayValues = groupResponseValues(cell)
        let groups = timeGrouping().groups
        let pixelCount = Double(max(1, (cell.yEnd - cell.yStart + 1) * (cell.xEnd - cell.xStart + 1)))
        let countHist: [Double]
        if valueMode == .spikeCount {
            countHist = displayValues.map { $0 ?? 0.0 }
        } else {
            var values: [Double] = []
            values.reserveCapacity(groups.count)
            for group in groups {
                var counts: [Double] = []
                counts.reserveCapacity(Int(pixelCount))
                for yIndex in cell.yStart...cell.yEnd {
                    for xIndex in cell.xStart...cell.xEnd {
                        counts.append(data.rangeCount(
                            unitIndex: unitIndex,
                            yIndex: yIndex,
                            xIndex: xIndex,
                            start: group.start,
                            end: group.end
                        ))
                    }
                }
                values.append(compensatedSum(counts) / pixelCount)
            }
            countHist = values
        }

        var peakBin: Int?
        var peakValue: Double?
        for (index, value) in displayValues.enumerated() {
            guard let value, value.isFinite else { continue }
            if peakValue == nil || value > (peakValue ?? -.infinity) {
                peakBin = index
                peakValue = value
            }
        }
        if (peakValue ?? 0) <= 0 {
            peakBin = nil
            peakValue = nil
        }

        let countTotal = compensatedSum(countHist)
        var entropy = 0.0
        if countTotal > 0 {
            for count in countHist where count > 0 {
                let probability = count / countTotal
                entropy -= probability * log(probability)
            }
            if countHist.count > 1 { entropy /= log(Double(countHist.count)) }
        }

        let analysis = CellAnalysis(
            displayValues: displayValues,
            countHist: countHist,
            selectedValue: groupResponseValue(
                cell,
                sourceStart: selected.start,
                sourceEnd: selected.end
            ),
            totalValue: groupResponseValue(
                cell,
                sourceStart: 0,
                sourceEnd: data.nBins - 1
            ),
            peakBin: peakBin,
            peakValue: peakValue,
            delayMS: peakBin.map { timeGrouping().centers[$0] },
            entropy: entropy
        )
        cellAnalysisCaches.insert((key, analysis), at: 0)
        if cellAnalysisCaches.count > 12 { cellAnalysisCaches.removeLast() }
        return analysis
    }

    func prepareExport() {
        guard let data, hasSelectedUnit else { return }
        exportDocument = CSVMatrixDocument(text: exportCSV())
        exportFilename = "unit_\(String(format: "%03d", unitIndex))_cluster_\(data.clusterID(for: unitIndex))_\(valueMode.filenameSlug)_displayed.csv"
        isExporting = true
    }

    func exportCSV() -> String {
        guard let data, hasSelectedUnit else { return "" }
        let plot = currentHeatmapPlot()
        let matrix = plot.matrix
        let xGroups = plot.xGroups
        let yGroups = plot.yGroups
        let displayRange = plotDisplayRangeIndices()
        let timeBounds = plotTimeBoundsMS()
        let header = [
            "unit_index", "cluster_id", "y_index_0based", "y_index_matlab", "y_position",
            "x_index_0based", "x_index_matlab", "x_position", "value", "value_mode",
            "value_unit", "occupancy_time_sec_min", "occupancy_time_sec_max", "mode",
            "display_y_index_0based", "source_y_start_0based", "source_y_end_0based",
            "source_y_start_matlab", "source_y_end_matlab", "y_position_start", "y_position_end",
            "display_x_index_0based", "source_x_start_0based", "source_x_end_0based",
            "source_x_start_matlab", "source_x_end_matlab", "x_position_start", "x_position_end",
            "export_space", "time_resolution_ms", "rf_range_start_group_0based",
            "rf_range_end_group_0based", "rf_range_start_ms", "rf_range_end_ms",
            "display_x_bins", "display_y_bins", "smooth_radius", "flip_y", "palette", "source_json"
        ]
        var rows = [csvRow(header)]
        for (displayY, yGroup) in yGroups.enumerated() {
            for (displayX, xGroup) in xGroups.enumerated() {
                var occupancyValues: [Double] = []
                for yIndex in yGroup.start...yGroup.end {
                    for xIndex in xGroup.start...xGroup.end {
                        occupancyValues.append(data.occupancyTimeSeconds[yIndex][xIndex])
                    }
                }
                let value: String
                if matrix.indices.contains(displayY), matrix[displayY].indices.contains(displayX),
                   let matrixValue = matrix[displayY][displayX] {
                    value = String(matrixValue)
                } else {
                    value = ""
                }
                let occupancyMinimum = occupancyValues.min().map { String($0) } ?? ""
                let occupancyMaximum = occupancyValues.max().map { String($0) } ?? ""
                var fields = [
                    String(unitIndex), String(data.clusterID(for: unitIndex)),
                    String(yGroup.start), String(yGroup.start + 1),
                    String((data.yPositions[yGroup.start] + data.yPositions[yGroup.end]) / 2.0),
                    String(xGroup.start), String(xGroup.start + 1),
                    String((data.xPositions[xGroup.start] + data.xPositions[xGroup.end]) / 2.0),
                    value, valueMode.rawValue, valueMode.unit,
                    occupancyMinimum, occupancyMaximum,
                    currentMatrixLabel(), String(displayY), String(yGroup.start), String(yGroup.end),
                    String(yGroup.start + 1), String(yGroup.end + 1),
                    String(data.yPositions[yGroup.start]), String(data.yPositions[yGroup.end])
                ]
                fields += [
                    String(displayX), String(xGroup.start), String(xGroup.end),
                    String(xGroup.start + 1), String(xGroup.end + 1),
                    String(data.xPositions[xGroup.start]), String(data.xPositions[xGroup.end]),
                    "displayed", formatMS(Double(timeGroupSize()) * baseBinMS()),
                    String(displayRange.start), String(displayRange.end),
                    String(timeBounds.0), String(timeBounds.1), String(xBins), String(yBins),
                    String(smoothRadius), flipY ? "True" : "False", palette.rawValue, data.url.path
                ]
                rows.append(csvRow(fields))
            }
        }
        return rows.joined(separator: "\r\n") + "\r\n"
    }

    func cyclePalette() {
        let choices = RFPalette.allCases
        let index = choices.firstIndex(of: palette) ?? 0
        palette = choices[(index + 1) % choices.count]
    }

    func selectTab(_ index: Int) {
        let tabs = PlotTab.allCases
        guard tabs.indices.contains(index) else { return }
        selectedTab = tabs[index]
    }

    private func csvRow(_ fields: [String]) -> String {
        fields.map(csvEscape).joined(separator: ",")
    }

    private func csvEscape(_ value: String) -> String {
        if value.contains(",") || value.contains("\"") || value.contains("\n") || value.contains("\r") {
            return "\"\(value.replacingOccurrences(of: "\"", with: "\"\""))\""
        }
        return value
    }
}
