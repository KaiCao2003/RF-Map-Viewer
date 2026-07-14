import AppKit
import Foundation
import Observation

@Observable
final class RFMappingStore {
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

    @ObservationIgnored private var timelineCache: (key: TimelineCacheKey, value: TimelineMatrixSnapshot)?

    var data: RFMappingData?
    var availableJSONURLs: [URL] = []
    var selectedJSONPath = ""

    var unitIndex = 0
    var valueMode: ResponseValueMode = .spikeCount
    var binIndex = 0
    var rangeStartMS = 0.0
    var rangeEndMS = 1.0
    var flipY = false
    var palette: RFPalette = .gray
    var polarRadiusMode: PolarRadiusMode = .displayBottomInner
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
    var isAwaitingStartupDocument = false
    var exportDocument: CSVMatrixDocument?
    var exportFilename = "rf_matrix.csv"
    var errorMessage: String?

    init(initialURL: URL? = nil, initialData: RFMappingData? = nil, loadDefault: Bool = true) {
        isAwaitingStartupDocument = initialURL == nil && initialData == nil && !loadDefault
        rescanJSONFiles()
        if let initialData {
            adopt(initialData)
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
            errorMessage = "No RF mapping JSON files found. Use Open JSON to choose one."
        }
    }

    var hasData: Bool { data != nil }

    var dataSummary: String {
        guard let data else { return "No JSON loaded" }
        return """
        \(data.url.path)
        \(data.nUnits) units  \(data.nY) y x \(data.nX) x  \(data.nBins) bins
        Firing-rate metadata: \(data.hasPresentationCounts ? "yes" : "no")
        """
    }

    var headerTitle: String {
        guard let data else { return "RF Mapping Viewer" }
        return "Unit \(String(format: "%03d", unitIndex)) / cluster \(data.clusterID(for: unitIndex))"
    }

    var windowTitle: String {
        data.map { "\($0.url.lastPathComponent) — RF Mapping Viewer" } ?? "RF Mapping Viewer"
    }

    var statusText: String {
        guard let data else { return "Open a unitsSpikeCounts JSON file." }
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
        guard let data else { return "" }
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

    func rescanJSONFiles() {
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

    @discardableResult
    func loadJSON(_ url: URL) -> Bool {
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

    private func adopt(_ loaded: RFMappingData) {
        isAwaitingStartupDocument = false
        errorMessage = nil
        data = loaded
        selectedJSONPath = loaded.url.path
        unitIndex = 0
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
        normalizeControls()
        ensureSelectedCell()
        rescanJSONFiles()
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
        guard let data, data.nUnits > 0 else { return }
        unitIndex = (unitIndex + delta + data.nUnits) % data.nUnits
        selectedCell = nil
        clearHover()
        ensureSelectedCell()
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
        unitIndex = max(0, min(data.nUnits - 1, unitIndex))
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
    }

    func normalizeSelectedTimeRange() {
        guard let data else { return }
        let source = sourceBinsForSelectedRange()
        rangeStartMS = data.timeBinEdges[source.start] * 1000.0
        rangeEndMS = data.timeBinEdges[source.end + 1] * 1000.0
    }

    func ensureSelectedCell() {
        guard selectedCell == nil, let data else { return }
        let metrics = data.metrics(for: unitIndex)
        selectedCell = CellRef(yStart: metrics.bestY, yEnd: metrics.bestY, xStart: metrics.bestX, xEnd: metrics.bestX)
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
        guard let data, data.timeBinEdges.count >= 2 else { return 1.0 }
        let positive = zip(data.timeBinEdges, data.timeBinEdges.dropFirst())
            .map { pair in (pair.1 - pair.0) * 1000.0 }
            .filter { $0 > 1e-9 }
        return positive.min() ?? 1.0
    }

    func timeAxisStartMS() -> Double {
        data?.timeBinEdges.first.map { $0 * 1000.0 } ?? 0.0
    }

    func timeAxisEndMS() -> Double {
        guard let data else { return baseBinMS() }
        return data.timeBinEdges.last.map { $0 * 1000.0 } ?? baseBinMS() * Double(data.nBins)
    }

    func timeAxisRangeMS() -> (Double, Double) {
        let start = timeAxisStartMS()
        let end = timeAxisEndMS()
        return end <= start ? (start, start + baseBinMS()) : (start, end)
    }

    func totalTimeMS() -> Double {
        let range = timeAxisRangeMS()
        return max(range.1 - range.0, baseBinMS())
    }

    func timeGroupSize() -> Int {
        guard let data else { return 1 }
        let base = baseBinMS()
        let requested = max(base, min(totalTimeMS(), timeResolutionMS))
        return max(1, min(data.nBins, Int((requested / base).rounded(.toNearestOrEven))))
    }

    func timeGroups() -> [AxisGroup] {
        guard let data else { return [AxisGroup(start: 0, end: 0)] }
        let size = timeGroupSize()
        return stride(from: 0, to: data.nBins, by: size).map {
            AxisGroup(start: $0, end: min($0 + size - 1, data.nBins - 1))
        }
    }

    func timeGroupCount() -> Int { max(1, timeGroups().count) }

    func timeGroupBoundsMS(_ displayBin: Int) -> (Double, Double) {
        guard let data else { return (0.0, baseBinMS()) }
        let groups = timeGroups()
        let index = max(0, min(groups.count - 1, displayBin))
        let group = groups[index]
        return (data.timeBinEdges[group.start] * 1000.0, data.timeBinEdges[group.end + 1] * 1000.0)
    }

    func timeGroupLabel(_ displayBin: Int) -> String {
        "\(formatMS(timeGroupBoundsMS(displayBin).0)) ms"
    }

    func timeGroupIntervalLabel(_ displayBin: Int) -> String {
        let bounds = timeGroupBoundsMS(displayBin)
        return "\(formatMS(bounds.0))–\(formatMS(bounds.1)) ms"
    }

    func timeGroupCenterMS(_ displayBin: Int) -> Double {
        let bounds = timeGroupBoundsMS(displayBin)
        return (bounds.0 + bounds.1) / 2.0
    }

    func sourceBinsForDisplayBin(_ displayBin: Int) -> AxisGroup {
        let groups = timeGroups()
        return groups[max(0, min(groups.count - 1, displayBin))]
    }

    func sourceBinsForSelectedRange() -> AxisGroup {
        guard let data else { return AxisGroup(start: 0, end: 0) }
        let edgesMS = data.timeBinEdges.map { $0 * 1000.0 }
        let axisStart = edgesMS[0]
        let axisEnd = edgesMS[data.nBins]
        var requestedStart = max(axisStart, min(axisEnd, rangeStartMS))
        var requestedEnd = max(axisStart, min(axisEnd, rangeEndMS))
        if requestedStart > requestedEnd { swap(&requestedStart, &requestedEnd) }

        let startEdge = (0..<data.nBins).min { lhs, rhs in
            abs(edgesMS[lhs] - requestedStart) < abs(edgesMS[rhs] - requestedStart)
        } ?? 0
        var endEdge = (1...data.nBins).min { lhs, rhs in
            abs(edgesMS[lhs] - requestedEnd) < abs(edgesMS[rhs] - requestedEnd)
        } ?? data.nBins
        if endEdge <= startEdge { endEdge = min(data.nBins, startEdge + 1) }
        return AxisGroup(start: startEdge, end: endEdge - 1)
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

    func selectedRangeOverlaps(_ group: AxisGroup) -> Bool {
        let selected = sourceBinsForSelectedRange()
        return group.start <= selected.end && group.end >= selected.start
    }

    func currentMatrix() -> OptionalMatrix {
        guard let data else { return [] }
        let range = sourceBinsForSelectedRange()
        return (try? data.responseMatrix(
            unitIndex: unitIndex,
            start: range.start,
            end: range.end,
            valueMode: valueMode
        )) ?? []
    }

    func delayMatrixForTimeGroups(floor: Double = 0.0) -> OptionalMatrix {
        guard let data else { return [] }
        let unit = data.counts[unitIndex]
        let metrics = data.metrics(for: unitIndex)
        let groups = timeGroups()
        return (0..<data.nY).map { yIndex in
            (0..<data.nX).map { xIndex -> Double? in
                guard metrics.total[yIndex][xIndex] > floor else { return nil }
                let hist = unit[yIndex][xIndex]
                let grouped = groups.map { compensatedSum(hist[$0.start...$0.end]) }
                guard let maximum = grouped.max(), maximum > 0 else { return nil }
                var peak = 0
                for index in 1..<grouped.count where grouped[index] > grouped[peak] { peak = index }
                return timeGroupCenterMS(peak)
            }
        }
    }

    func timelineMatrix(for displayBin: Int) -> OptionalMatrix {
        guard let data else { return [] }
        let source = sourceBinsForDisplayBin(displayBin)
        return (try? data.responseMatrix(
            unitIndex: unitIndex,
            start: source.start,
            end: source.end,
            valueMode: valueMode
        )) ?? []
    }

    func timelineSnapshot() -> TimelineMatrixSnapshot {
        guard let data else {
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
        if let timelineCache, timelineCache.key == key {
            return timelineCache.value
        }

        let groups = timeGroups()
        let matrices = groups.indices.map { displayBin in
            preparePlotMatrix(timelineMatrix(for: displayBin), smooth: true).0
        }
        let sharedHigh = matrices
            .flatMap { $0 }
            .flatMap { $0 }
            .compactMap { value -> Double? in
                guard let value, value.isFinite else { return nil }
                return value
            }
            .max() ?? 0.0
        let snapshot = TimelineMatrixSnapshot(
            timeGroups: groups,
            matrices: matrices,
            totals: allPositionsTimelineValues(),
            sharedHigh: max(sharedHigh, 1.0)
        )
        timelineCache = (key, snapshot)
        return snapshot
    }

    func allPositionsTimelineValues() -> [Double] {
        guard let data else { return [] }
        let groups = timeGroups()
        if valueMode == .spikeCount {
            let totals = data.metrics(for: unitIndex).binTotals
            return groups.map { compensatedSum(totals[$0.start...$0.end]) }
        }
        guard let presentations = data.presentationCounts else {
            return Array(repeating: 0.0, count: groups.count)
        }
        let presentationTotal = compensatedSum(presentations.flatMap { $0 }.filter { $0 > 0 })
        guard presentationTotal > 0 else { return Array(repeating: 0.0, count: groups.count) }
        let unit = data.counts[unitIndex]
        return groups.map { group in
            var cellCounts: [Double] = []
            cellCounts.reserveCapacity(data.nY * data.nX)
            for yIndex in 0..<data.nY {
                for xIndex in 0..<data.nX {
                    cellCounts.append(compensatedSum(unit[yIndex][xIndex][group.start...group.end]))
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
        guard let data else { return [] }
        var hist = Array(repeating: 0.0, count: data.nBins)
        let pixelCount = max(1, (cell.yEnd - cell.yStart + 1) * (cell.xEnd - cell.xStart + 1))
        let unit = data.counts[unitIndex]
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
        guard let data else { return nil }
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
        timeGroups().map { groupResponseValue(cell, sourceStart: $0.start, sourceEnd: $0.end) }
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
        let bounds = selectedTimeBoundsMS()
        return "\(valueMode.rawValue): \(formatMS(bounds.0)) to \(formatMS(bounds.1)) ms"
    }

    func cellMetricsText(_ cell: CellRef, displayBin: Int? = nil) -> String {
        guard let data else { return "" }
        let countHist = timeGroupedHist(groupHist(cell))
        let displayValues = groupResponseValues(cell)
        guard !displayValues.isEmpty else { return "" }
        let bin = max(0, min(displayValues.count - 1, displayBin ?? binIndex))
        let selected = sourceBinsForSelectedRange()
        let rangeValue = groupResponseValue(cell, sourceStart: selected.start, sourceEnd: selected.end)
        let totalValue = groupResponseValue(cell, sourceStart: 0, sourceEnd: data.nBins - 1)

        var peakBin: Int?
        var peakValue: Double?
        for (index, value) in displayValues.enumerated() {
            guard let value, value.isFinite else { continue }
            if peakValue == nil || value > (peakValue ?? -.infinity) {
                peakBin = index
                peakValue = value
            }
        }
        if (peakValue ?? 0) <= 0 { peakBin = nil; peakValue = nil }
        let delay = peakBin.map(timeGroupCenterMS)

        let countTotal = compensatedSum(countHist)
        var entropy = 0.0
        if countTotal > 0 {
            for count in countHist where count > 0 {
                let probability = count / countTotal
                entropy -= probability * log(probability)
            }
            if countHist.count > 1 { entropy /= log(Double(countHist.count)) }
        }

        let grouped = cell.yStart != cell.yEnd || cell.xStart != cell.xEnd
        let groupNote = grouped ? "avg over source pixels\n" : ""
        let peakText = peakBin.map { "\($0 + 1) (\(timeGroupLabel($0)))" } ?? "n/a"
        let delayText = delay.map { String(format: "%.1f ms", $0) } ?? "n/a"
        return """
        cluster \(data.clusterID(for: unitIndex))
        \(yGroupText(cell.yStart, cell.yEnd)), \(xGroupText(cell.xStart, cell.xEnd))
        \(groupNote)bin \(valueMode.format(displayValues[bin])) \(valueMode.unit) (\(timeGroupLabel(bin)))
        selected range \(valueMode.format(rangeValue)) \(valueMode.unit)
        full window \(valueMode.format(totalValue)) \(valueMode.unit)
        peak \(valueMode.format(peakValue)) \(valueMode.unit)
        peak bin \(peakText)
        delay \(delayText), count entropy \(String(format: "%.3f", entropy))
        """
    }

    func tooltipText(_ cell: CellRef, displayBin: Int? = nil) -> String {
        guard let data else { return "" }
        let values = groupResponseValues(cell)
        guard !values.isEmpty else { return "" }
        let bin = max(0, min(values.count - 1, displayBin ?? binIndex))
        var peakIndex: Int?
        var peakValue = 0.0
        for (index, value) in values.enumerated() {
            if let value, value > peakValue { peakValue = value; peakIndex = index }
        }
        let total = groupResponseValue(cell, sourceStart: 0, sourceEnd: data.nBins - 1)
        return [
            yGroupText(cell.yStart, cell.yEnd),
            xGroupText(cell.xStart, cell.xEnd),
            "bin \(bin + 1): \(valueMode.format(values[bin])) \(valueMode.unit)",
            "full window: \(valueMode.format(total)) \(valueMode.unit)",
            peakIndex.map { String(format: "delay %.1f ms", timeGroupCenterMS($0)) } ?? "delay n/a"
        ].joined(separator: "\n")
    }

    func prepareExport() {
        guard let data else { return }
        exportDocument = CSVMatrixDocument(text: exportCSV())
        exportFilename = "unit_\(String(format: "%03d", unitIndex))_cluster_\(data.clusterID(for: unitIndex))_\(valueMode.filenameSlug)_displayed.csv"
        isExporting = true
    }

    func exportCSV() -> String {
        guard let data else { return "" }
        let prepared = preparePlotMatrix(currentMatrix(), smooth: true)
        let matrix = prepared.0
        let xGroups = prepared.1
        let yGroups = prepared.2
        let displayRange = displayRangeIndices()
        let timeBounds = selectedTimeBoundsMS()
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
