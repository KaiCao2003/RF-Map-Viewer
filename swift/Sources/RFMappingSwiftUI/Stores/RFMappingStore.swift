import AppKit
import Foundation
import Observation

@Observable
final class RFMappingStore {
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
    @ObservationIgnored private var loadRequestID: UUID?
    @ObservationIgnored private var activeDecodeTask: Task<RFMappingData, Error>?
    @ObservationIgnored var pairingDataDidChange: (() -> Void)?

    var data: RFMappingData?
    var availableJSONURLs: [URL] = []
    var selectedJSONPath = ""

    /// File-local original index. `-1` means the paired union currently points
    /// at a unit ID that this file does not contain.
    private(set) var unitIndex = 0
    private(set) var selectedUnitID: Int?
    private(set) var pairedUnitIDs: [Int]?
    var valueMode: ResponseValueMode = .spikeCount
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
        discoverJSONChoices: Bool = true
    ) {
        isAwaitingStartupDocument = initialURL == nil && initialData == nil && !loadDefault
        if discoverJSONChoices { refreshJSONChoices() }
        if let initialData {
            adopt(initialData, refreshChoices: discoverJSONChoices)
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
        guard let data, let selectedUnitID else { return nil }
        return try? data.rfMap(byUnitID: selectedUnitID)
    }

    var navigationUnitIDs: [Int] {
        pairedUnitIDs ?? data?.unitPool ?? []
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
        Firing-rate metadata: \(data.hasPresentationCounts ? "yes" : "no")
        """
    }

    var headerTitle: String {
        guard data != nil else { return "RF Map Viewer" }
        guard hasSelectedUnit, let selectedUnitID else {
            let missingID = selectedUnitID.map { String($0) } ?? "unknown"
            return "Unit N/A / cluster \(missingID) is not present in this file"
        }
        return "Unit \(String(format: "%03d", unitIndex)) / cluster \(selectedUnitID)"
    }

    var windowTitle: String {
        data.map { "\($0.url.lastPathComponent) — RF Map Viewer" } ?? "RF Map Viewer"
    }

    var statusText: String {
        guard let data else { return "Open an RF mapping .rfmap or JSON file." }
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
            return selectedUnitID.map { "Cluster \($0): N/A in this file" } ?? ""
        }
        let metrics = data.metrics(for: unitIndex)
        let delay = metrics.delayMS[metrics.bestY][metrics.bestX]
        let delayText = delay.map { String(format: "%.1f ms", $0) } ?? "n/a"
        return "Total spikes: \(String(format: "%.0f", metrics.totalSpikes))\nBest count cell: yIdx \(metrics.bestY + 1), xIdx \(metrics.bestX + 1)\nCount-peak delay: \(delayText)"
    }

    var displayedCellText: String {
        guard let cell = hoverCell ?? selectedCell else { return "" }
        let prefix = hoverCell == nil ? "" : "Hover\n"
        return prefix + cellMetricsText(cell, displayBin: hoverDisplayBin)
    }

    var supportsNormalizedValues: Bool {
        data?.hasPresentationCounts == true
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
        valueMode = loaded.supports(valueMode) ? valueMode : .spikeCount
        selectedCell = nil
        clearHover()
        timelineRangeAnchor = nil
        timelineScrollFraction = 0
        resetPlotRangeToDefault()
        normalizeControls()
        ensureSelectedCell()
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
        guard let data else { return }
        let state = viewerSyncState.merging(state, fields: fields)

        selectUnitID(state.unitID, resetInteraction: false)
        valueMode = data.supports(state.valueMode) ? state.valueMode : .spikeCount
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
        guard let data else { return }
        guard data.supports(mode) else {
            valueMode = .spikeCount
            errorMessage = "This legacy JSON contains pooled spike counts but no stimulusPresentationCounts. True per-presentation values and firing rates require regenerated JSON metadata."
            return
        }
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
        let localIndex = data.unitIndex(forUnitID: unitID)
        guard localIndex != nil || pairedUnitIDs?.contains(unitID) == true else { return }
        selectedUnitID = unitID
        unitIndex = localIndex ?? -1
        clearDerivedCaches()
        if resetInteraction {
            selectedCell = nil
            clearHover()
            ensureSelectedCell()
        }
    }

    /// Configures the sorted cross-window union used by previous/next. Passing
    /// nil leaves pairing and restores a valid local selection if necessary.
    func setPairedUnitIDs(_ unitIDs: [Int]?) {
        pairedUnitIDs = unitIDs.map { Array(Set($0)).sorted() }
        if pairedUnitIDs == nil, !hasSelectedUnit, let first = data?.unitPool.first {
            selectUnitID(first)
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
        if let selectedUnitID {
            unitIndex = data.unitIndex(forUnitID: selectedUnitID) ?? -1
        } else {
            selectedUnitID = data.unitPool.first
            unitIndex = 0
        }
        xBins = max(1, min(data.nX, xBins))
        yBins = max(1, min(data.nY, yBins))
        smoothRadius = max(0, min(3, smoothRadius))
        responseFloor = max(0.0, responseFloor)
        if !data.supports(valueMode) { valueMode = .spikeCount }

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

    func normalizePlotTimeRange() {
        guard let data else { return }
        let source = sourceBinsForPlotRange()
        plotRangeStartMS = data.timeBinEdges[source.start] * 1000.0
        plotRangeEndMS = data.timeBinEdges[source.end + 1] * 1000.0
    }

    func resetPlotRangeToDefault() {
        guard data != nil else { return }
        let axisStart = timeAxisStartMS()
        let axisEnd = timeAxisEndMS()
        plotRangeStartMS = max(axisStart, min(axisEnd, 0.0))
        plotRangeEndMS = max(axisStart, min(axisEnd, 200.0))
        normalizePlotTimeRange()
    }

    func ensureSelectedCell() {
        guard selectedCell == nil, let data, hasSelectedUnit else { return }
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
        let matrix: OptionalMatrix
        if valueMode == .spikeCount,
           let selectedRFMap,
           let summed = try? selectedRFMap.sumBetweenSeconds(
               data.timeBinEdges[range.start],
               data.timeBinEdges[range.end + 1]
           ) {
            matrix = summed.spikeCounts.map { row in
                row.map { histogram in histogram.first }
            }
        } else {
            matrix = (try? data.responseMatrix(
                unitIndex: unitIndex,
                start: range.start,
                end: range.end,
                valueMode: valueMode
            )) ?? []
        }
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
        let prepared = preparePlotMatrix(currentMatrix(), smooth: true)
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

        let fullWindowResponse = (try? data.responseMatrix(
            unitIndex: unitIndex,
            start: 0,
            end: data.nBins - 1,
            valueMode: valueMode
        )) ?? []
        let totalPrepared = preparePlotMatrix(fullWindowResponse)
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
            let raw = (try? data.responseMatrix(
                unitIndex: unitIndex,
                start: group.start,
                end: group.end,
                valueMode: valueMode
            )) ?? []
            var prepared = reduceMatrixXY(raw, yGroups: yGroups, xGroups: xGroups)
            prepared = smoothMatrix(prepared, radius: smoothRadius)
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
        guard let presentations = data.presentationCounts else {
            return Array(repeating: 0.0, count: groups.count)
        }
        var presentationValues: [Double] = []
        presentationValues.reserveCapacity(data.nY * data.nX)
        for row in presentations {
            for value in row where value > 0 { presentationValues.append(value) }
        }
        let presentationTotal = compensatedSum(presentationValues)
        guard presentationTotal > 0 else { return Array(repeating: 0.0, count: groups.count) }
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
            var value = count / presentationTotal
            if valueMode == .meanFiringRate {
                value /= data.timeSpanSeconds(start: group.start, end: group.end)
            }
            return value
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
        var values: [Double] = []
        for yIndex in cell.yStart...cell.yEnd {
            for xIndex in cell.xStart...cell.xEnd {
                do {
                    if let value = try data.responseValue(
                        unitIndex: unitIndex,
                        yIndex: yIndex,
                        xIndex: xIndex,
                        start: sourceStart,
                        end: sourceEnd,
                        valueMode: valueMode
                    ), value.isFinite {
                        values.append(value)
                    }
                } catch {
                    return nil
                }
            }
        }
        guard !values.isEmpty else { return nil }
        return compensatedSum(values) / Double(values.count)
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
            "value_unit", "presentation_count_min", "presentation_count_max", "mode",
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
                var presentationValues: [Double] = []
                if let counts = data.presentationCounts {
                    for yIndex in yGroup.start...yGroup.end {
                        for xIndex in xGroup.start...xGroup.end {
                            presentationValues.append(counts[yIndex][xIndex])
                        }
                    }
                }
                let value: String
                if matrix.indices.contains(displayY), matrix[displayY].indices.contains(displayX),
                   let matrixValue = matrix[displayY][displayX] {
                    value = String(matrixValue)
                } else {
                    value = ""
                }
                let presentationMinimum = presentationValues.min().map { String($0) } ?? ""
                let presentationMaximum = presentationValues.max().map { String($0) } ?? ""
                var fields = [
                    String(unitIndex), String(data.clusterID(for: unitIndex)),
                    String(yGroup.start), String(yGroup.start + 1),
                    String((data.yPositions[yGroup.start] + data.yPositions[yGroup.end]) / 2.0),
                    String(xGroup.start), String(xGroup.start + 1),
                    String((data.xPositions[xGroup.start] + data.xPositions[xGroup.end]) / 2.0),
                    value, valueMode.rawValue, valueMode.unit,
                    presentationMinimum, presentationMaximum,
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
