import AppKit
import Foundation
import Observation

@Observable
final class RFMappingStore {
    var data: RFMappingData?
    var availableJSONURLs: [URL] = []
    var selectedJSONPath = ""

    var unitIndex = 0
    var mode: RFMode = .total
    var binIndex = 0
    var rangeStart = 0
    var rangeEnd = 0
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
    var timelineRangeAnchor: Int?

    var isImporting = false
    var isExporting = false
    var exportDocument: CSVMatrixDocument?
    var exportFilename = "rf_matrix.csv"
    var errorMessage: String?

    init() {
        rescanJSONFiles()
        if let url = JSONDiscovery.latestJSONURL() {
            loadJSON(url)
        } else {
            errorMessage = "No RF mapping JSON files found. Use Open JSON to choose one."
        }
    }

    var hasData: Bool {
        data != nil
    }

    var dataSummary: String {
        guard let data else { return "No JSON loaded" }
        return "\(JSONDiscovery.shortLabel(for: data.url))\n\(data.nUnits) units  \(data.nY) y x \(data.nX) x  \(data.nBins) bins"
    }

    var headerTitle: String {
        guard let data else { return "RF Mapping Viewer" }
        return "Unit \(String(format: "%03d", unitIndex)) / cluster \(data.clusterID(for: unitIndex))"
    }

    var statusText: String {
        guard let data else { return "Open a unitsSpikeCounts JSON file." }
        if let hoverCell {
            let prefix = hoverExtra.isEmpty ? "Hover" : "Hover \(hoverExtra);"
            return "\(prefix) \(yGroupText(hoverCell.yStart, hoverCell.yEnd)), \(xGroupText(hoverCell.xStart, hoverCell.xEnd))"
        }
        return "x: \(formatPos(data.xPositions.first ?? 0))..\(formatPos(data.xPositions.last ?? 0))  y: \(formatPos(data.yPositions.first ?? 0))..\(formatPos(data.yPositions.last ?? 0))  time: \(formatMS(timeAxisStartMS()))..\(formatMS(timeAxisEndMS())) ms"
    }

    var unitStatsText: String {
        guard let data else { return "" }
        let metrics = data.metrics(for: unitIndex)
        let bestDelay = metrics.delayMS[metrics.bestY][metrics.bestX].map { String(format: "%.1f ms", $0) } ?? "n/a"
        return "Total spikes: \(String(format: "%.0f", metrics.totalSpikes))\nBest cell: yIdx \(metrics.bestY + 1), xIdx \(metrics.bestX + 1)\nBest delay: \(bestDelay)"
    }

    var displayedCellText: String {
        guard let cell = hoverCell ?? selectedCell else { return "" }
        let prefix = hoverCell == nil ? "" : "Hover\n"
        return prefix + cellMetricsText(cell)
    }

    func rescanJSONFiles() {
        availableJSONURLs = JSONDiscovery.discoverJSONFiles(currentURL: data?.url)
        if let data {
            selectedJSONPath = data.url.standardizedFileURL.path
        } else if selectedJSONPath.isEmpty {
            selectedJSONPath = availableJSONURLs.first?.standardizedFileURL.path ?? ""
        }
    }

    func loadJSON(path: String) {
        guard !path.isEmpty else { return }
        loadJSON(URL(fileURLWithPath: path))
    }

    func loadJSON(_ url: URL) {
        let accessing = url.startAccessingSecurityScopedResource()
        defer {
            if accessing {
                url.stopAccessingSecurityScopedResource()
            }
        }

        do {
            let loaded = try RFMappingData(url: url.standardizedFileURL)
            data = loaded
            selectedJSONPath = loaded.url.path
            unitIndex = 0
            binIndex = 0
            rangeStart = 0
            rangeEnd = max(0, loaded.nBins - 1)
            xBins = loaded.nX
            yBins = loaded.nY
            timeResolutionMS = baseBinMS()
            smoothRadius = 0
            mode = .total
            selectedCell = nil
            hoverCell = nil
            hoverLocation = nil
            timelineRangeAnchor = nil
            normalizeControls()
            ensureSelectedCell()
            rescanJSONFiles()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func stepUnit(_ delta: Int) {
        guard let data, data.nUnits > 0 else { return }
        unitIndex = (unitIndex + delta + data.nUnits) % data.nUnits
        selectedCell = nil
        hoverCell = nil
        ensureSelectedCell()
    }

    func stepBin(_ delta: Int) {
        let maxBin = max(0, timeGroupCount() - 1)
        binIndex = max(0, min(maxBin, binIndex + delta))
        mode = .bin
        normalizeControls()
    }

    func clearTimelineSelection() {
        timelineRangeAnchor = nil
        mode = .total
        rangeStart = 0
        rangeEnd = max(0, timeGroupCount() - 1)
        normalizeControls()
    }

    func normalizeControls() {
        guard let data else { return }
        unitIndex = max(0, min(data.nUnits - 1, unitIndex))
        xBins = max(1, min(data.nX, xBins))
        yBins = max(1, min(data.nY, yBins))
        smoothRadius = max(0, min(3, smoothRadius))
        responseFloor = max(0.0, responseFloor)

        let base = baseBinMS()
        let total = totalTimeMS()
        let requested = max(base, min(total, timeResolutionMS))
        let groupSize = max(1, min(data.nBins, Int((requested / base).rounded())))
        timeResolutionMS = Double(groupSize) * base

        let maxBin = max(0, timeGroupCount() - 1)
        binIndex = max(0, min(maxBin, binIndex))
        rangeStart = max(0, min(maxBin, rangeStart))
        rangeEnd = max(0, min(maxBin, rangeEnd))
        if let anchor = timelineRangeAnchor {
            timelineRangeAnchor = max(0, min(maxBin, anchor))
        }
    }

    func ensureSelectedCell() {
        guard selectedCell == nil, let data else { return }
        let metrics = data.metrics(for: unitIndex)
        selectedCell = CellRef(yStart: metrics.bestY, yEnd: metrics.bestY, xStart: metrics.bestX, xEnd: metrics.bestX)
    }

    func setHover(_ cell: CellRef, location: CGPoint, extra: String = "") {
        hoverCell = cell
        hoverLocation = location
        hoverExtra = extra
    }

    func clearHover() {
        hoverCell = nil
        hoverLocation = nil
        hoverExtra = ""
    }

    func selectCell(_ cell: CellRef) {
        selectedCell = cell
        hoverCell = nil
    }

    func selectTimelineBin(_ bin: Int, extending: Bool) {
        let maxBin = max(0, timeGroupCount() - 1)
        let bin = max(0, min(maxBin, bin))
        if extending {
            if timelineRangeAnchor == nil {
                timelineRangeAnchor = binIndex
            }
            let anchor = timelineRangeAnchor ?? bin
            rangeStart = min(anchor, bin)
            rangeEnd = max(anchor, bin)
            binIndex = bin
            mode = .rangeSum
            timelineRangeAnchor = bin
        } else {
            timelineRangeAnchor = bin
            binIndex = bin
            mode = .bin
        }
        normalizeControls()
    }

    func prepareExport() {
        guard let data else { return }
        exportDocument = CSVMatrixDocument(text: exportCSV())
        exportFilename = "unit_\(String(format: "%03d", unitIndex))_cluster_\(data.clusterID(for: unitIndex))_\(mode.rawValue.lowercased().replacingOccurrences(of: " ", with: "_")).csv"
        isExporting = true
    }

    func exportCSV() -> String {
        guard let data else { return "" }
        let matrix = currentMatrix()
        var rows: [String] = [
            "unit_index,cluster_id,y_index_0based,y_index_matlab,y_position,x_index_0based,x_index_matlab,x_position,value,mode"
        ]
        for yIndex in 0..<data.nY {
            for xIndex in 0..<data.nX {
                rows.append([
                    "\(unitIndex)",
                    "\(data.clusterID(for: unitIndex))",
                    "\(yIndex)",
                    "\(yIndex + 1)",
                    "\(data.yPositions[yIndex])",
                    "\(xIndex)",
                    "\(xIndex + 1)",
                    "\(data.xPositions[xIndex])",
                    "\(matrix[yIndex][xIndex])",
                    csvEscape(currentMatrixLabel())
                ].joined(separator: ","))
            }
        }
        return rows.joined(separator: "\n") + "\n"
    }

    func baseBinMS() -> Double {
        guard let data, data.timeBinEdges.count >= 2 else { return 1.0 }
        let diffs = (0..<(data.timeBinEdges.count - 1)).map {
            (data.timeBinEdges[$0 + 1] - data.timeBinEdges[$0]) * 1000.0
        }
        let positive = diffs.filter { $0 > 1e-9 }
        return positive.min() ?? 1.0
    }

    func timeAxisStartMS() -> Double {
        data?.timeBinEdges.first.map { $0 * 1000.0 } ?? 0.0
    }

    func timeAxisEndMS() -> Double {
        guard let data else { return baseBinMS() }
        return data.timeBinEdges.last.map { $0 * 1000.0 } ?? (baseBinMS() * Double(data.nBins))
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
        return max(1, min(data.nBins, Int((requested / base).rounded())))
    }

    func timeGroups() -> [AxisGroup] {
        guard let data else { return [AxisGroup(start: 0, end: 0)] }
        let groupSize = timeGroupSize()
        return stride(from: 0, to: data.nBins, by: groupSize).map {
            AxisGroup(start: $0, end: min($0 + groupSize - 1, data.nBins - 1))
        }
    }

    func timeGroupCount() -> Int {
        max(1, timeGroups().count)
    }

    func timeGroupBoundsMS(_ displayBin: Int) -> (Double, Double) {
        guard let data else { return (0.0, baseBinMS()) }
        let groups = timeGroups()
        let index = max(0, min(groups.count - 1, displayBin))
        let group = groups[index]
        return (data.timeBinEdges[group.start] * 1000.0, data.timeBinEdges[group.end + 1] * 1000.0)
    }

    func timeGroupLabel(_ displayBin: Int) -> String {
        let bounds = timeGroupBoundsMS(displayBin)
        let index = max(0, min(timeGroupCount() - 1, displayBin)) + 1
        return "\(index): \(formatMS(bounds.0))-\(formatMS(bounds.1)) ms"
    }

    func timeGroupEndLabel(_ displayBin: Int) -> String {
        "\(formatMS(timeGroupBoundsMS(displayBin).1)) ms"
    }

    func timeGroupCenterMS(_ displayBin: Int) -> Double {
        let bounds = timeGroupBoundsMS(displayBin)
        return (bounds.0 + bounds.1) / 2.0
    }

    func sourceBinsForDisplayBin(_ displayBin: Int) -> AxisGroup {
        let groups = timeGroups()
        let index = max(0, min(groups.count - 1, displayBin))
        return groups[index]
    }

    func sourceBinsForDisplayRange() -> AxisGroup {
        let groups = timeGroups()
        let startIndex = max(0, min(groups.count - 1, min(rangeStart, rangeEnd)))
        let endIndex = max(0, min(groups.count - 1, max(rangeStart, rangeEnd)))
        return AxisGroup(start: groups[startIndex].start, end: groups[endIndex].end)
    }

    func currentMatrix() -> [[Double]] {
        guard let data else { return [] }
        switch mode {
        case .bin:
            let range = sourceBinsForDisplayBin(binIndex)
            return data.aggregateMatrix(unitIndex: unitIndex, mode: .rangeSum, binIndex: 0, rangeStart: range.start, rangeEnd: range.end)
        case .rangeSum:
            let range = sourceBinsForDisplayRange()
            return data.aggregateMatrix(unitIndex: unitIndex, mode: .rangeSum, binIndex: 0, rangeStart: range.start, rangeEnd: range.end)
        default:
            return data.aggregateMatrix(unitIndex: unitIndex, mode: mode, binIndex: binIndex, rangeStart: rangeStart, rangeEnd: rangeEnd)
        }
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
                let grouped = groups.map { hist[$0.start...$0.end].reduce(0.0, +) }
                guard let maxValue = grouped.max(), maxValue > 0 else { return nil }
                let peakGroup = grouped.indices.max { grouped[$0] < grouped[$1] } ?? 0
                return timeGroupCenterMS(peakGroup)
            }
        }
    }

    func timeGroupedHist(_ hist: [Double]) -> [Double] {
        timeGroups().map { hist[$0.start...$0.end].reduce(0.0, +) }
    }

    func visibleTimelineBins(displayBins: Int) -> [Int] {
        switch mode {
        case .bin:
            return [max(0, min(displayBins - 1, binIndex))]
        case .rangeSum:
            let start = max(0, min(displayBins - 1, min(rangeStart, rangeEnd)))
            let end = max(0, min(displayBins - 1, max(rangeStart, rangeEnd)))
            return Array(start...end)
        default:
            return Array(0..<displayBins)
        }
    }

    func xGroups() -> [AxisGroup] {
        guard let data else { return [] }
        return axisGroupsForTarget(sourceCount: data.nX, targetCount: xBins)
    }

    func displayYGroups() -> [AxisGroup] {
        guard let data else { return [] }
        var groups = axisGroupsForTarget(sourceCount: data.nY, targetCount: yBins)
        if flipY {
            groups.reverse()
        }
        return groups
    }

    func preparePlotMatrix(_ matrix: OptionalMatrix, smooth: Bool = true) -> (OptionalMatrix, [AxisGroup], [AxisGroup]) {
        let xGroups = xGroups()
        let yGroups = displayYGroups()
        var prepared = reduceMatrixXY(matrix, yGroups: yGroups, xGroups: xGroups)
        if smooth {
            prepared = smoothMatrix(prepared, radius: smoothRadius)
        }
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

    func groupTotal(_ matrix: [[Double]], cell: CellRef) -> Double {
        var values: [Double] = []
        for yIndex in cell.yStart...cell.yEnd {
            for xIndex in cell.xStart...cell.xEnd {
                values.append(matrix[yIndex][xIndex])
            }
        }
        guard !values.isEmpty else { return 0.0 }
        return values.reduce(0.0, +) / Double(values.count)
    }

    func yGroupText(_ yStart: Int, _ yEnd: Int) -> String {
        guard let data else { return "" }
        if yStart == yEnd {
            return "yIdx \(yStart + 1); y \(formatPos(data.yPositions[yStart]))"
        }
        return "yIdx \(yStart + 1)-\(yEnd + 1); y \(formatPos(data.yPositions[yStart]))..\(formatPos(data.yPositions[yEnd]))"
    }

    func xGroupText(_ xStart: Int, _ xEnd: Int) -> String {
        guard let data else { return "" }
        if xStart == xEnd {
            return "xIdx \(xStart + 1); x \(formatPos(data.xPositions[xStart]))"
        }
        return "xIdx \(xStart + 1)-\(xEnd + 1); x \(formatPos(data.xPositions[xStart]))..\(formatPos(data.xPositions[xEnd]))"
    }

    func currentMatrixLabel() -> String {
        switch mode {
        case .bin:
            return timeGroupLabel(binIndex)
        case .rangeSum:
            let start = min(rangeStart, rangeEnd)
            let end = max(rangeStart, rangeEnd)
            return "Range sum: \(timeGroupLabel(start)) to \(timeGroupLabel(end))"
        default:
            return mode.rawValue
        }
    }

    func cellMetricsText(_ cell: CellRef) -> String {
        guard let data else { return "" }
        let metrics = data.metrics(for: unitIndex)
        let hist = groupHist(cell)
        let displayHist = timeGroupedHist(hist)
        let bin = max(0, min(displayHist.count - 1, binIndex))
        let start = max(0, min(displayHist.count - 1, min(rangeStart, rangeEnd)))
        let end = max(0, min(displayHist.count - 1, max(rangeStart, rangeEnd)))
        let totalValue = groupTotal(metrics.total, cell: cell)
        let peakValue = displayHist.max() ?? 0.0

        let peakBin: Int?
        let delay: Double?
        let entropy: Double
        let totalHist = displayHist.reduce(0.0, +)
        if totalHist > 0 {
            let best = displayHist.indices.max { displayHist[$0] < displayHist[$1] } ?? 0
            peakBin = best
            delay = timeGroupCenterMS(best)
            var entropyValue = 0.0
            for count in displayHist where count > 0 {
                let p = count / totalHist
                entropyValue -= p * log(p)
            }
            entropy = displayHist.count > 1 ? entropyValue / log(Double(displayHist.count)) : 0.0
        } else {
            peakBin = nil
            delay = nil
            entropy = 0.0
        }

        let peakText = peakBin.map { "\($0 + 1) (\(timeGroupLabel($0)))" } ?? "n/a"
        let delayText = delay.map { String(format: "%.1f ms", $0) } ?? "n/a"
        let grouped = cell.yStart != cell.yEnd || cell.xStart != cell.xEnd
        let groupNote = grouped ? "avg over source pixels\n" : ""

        return """
        cluster \(data.clusterID(for: unitIndex))
        \(yGroupText(cell.yStart, cell.yEnd)), \(xGroupText(cell.xStart, cell.xEnd))
        \(groupNote)bin count \(String(format: "%.0f", displayHist[bin])) (\(timeGroupLabel(bin)))
        range sum \(String(format: "%.0f", displayHist[start...end].reduce(0.0, +)))
        total \(String(format: "%.0f", totalValue)), peak \(String(format: "%.0f", peakValue))
        peak bin \(peakText)
        delay \(delayText), entropy \(String(format: "%.3f", entropy))
        """
    }

    func tooltipText(_ cell: CellRef) -> String {
        guard let data else { return "" }
        let metrics = data.metrics(for: unitIndex)
        let displayHist = timeGroupedHist(groupHist(cell))
        let bin = max(0, min(displayHist.count - 1, binIndex))
        let delay: Double?
        if displayHist.reduce(0.0, +) > 0 {
            let peakBin = displayHist.indices.max { displayHist[$0] < displayHist[$1] } ?? 0
            delay = timeGroupCenterMS(peakBin)
        } else {
            delay = nil
        }
        return [
            yGroupText(cell.yStart, cell.yEnd),
            xGroupText(cell.xStart, cell.xEnd),
            "bin \(bin + 1): \(String(format: "%.0f", displayHist[bin]))",
            "total \(String(format: "%.0f", groupTotal(metrics.total, cell: cell)))",
            delay.map { String(format: "delay %.1f ms", $0) } ?? "delay n/a"
        ].joined(separator: "\n")
    }

    func maxTimeGroupCellCount(timeGroups: [AxisGroup]) -> Double {
        guard let data else { return 1.0 }
        let unit = data.counts[unitIndex]
        var high = 0.0
        for yIndex in 0..<data.nY {
            for xIndex in 0..<data.nX {
                let hist = unit[yIndex][xIndex]
                for group in timeGroups {
                    high = max(high, hist[group.start...group.end].reduce(0.0, +))
                }
            }
        }
        return max(high, 1.0)
    }

    private func csvEscape(_ value: String) -> String {
        if value.contains(",") || value.contains("\"") || value.contains("\n") {
            return "\"\(value.replacingOccurrences(of: "\"", with: "\"\""))\""
        }
        return value
    }
}
