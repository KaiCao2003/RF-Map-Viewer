import SwiftUI

struct SavedRFResultInspectorView: View {
    let source: RFMappingData?
    let unitID: Int?
    let results: [String: SavedRFResult]
    let errors: [String: String]
    let layers: [RFOverlayLayer]

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let unitID {
                Text("Unit \(unitID)").font(.headline)
                ForEach(groups(unitID: unitID)) { group in
                    VStack(alignment: .leading, spacing: 8) {
                        Text(group.title).font(.headline)
                        if let shared = group.sharedStatistics { statisticsLabel(shared) }
                        ForEach(group.rows) { row in
                            VStack(alignment: .leading, spacing: 5) {
                                Text(row.layer.polarity.rawValue.capitalized + " · " + row.status)
                                    .fontWeight(.semibold)
                                if !row.centers.isEmpty { Text("Centers: " + row.centers) }
                                if let statistics = row.statistics {
                                    if group.sharedStatistics == nil { statisticsLabel(statistics) }
                                    Text((statistics.candidateThreshold ? "Candidate threshold: " : "Threshold: ")
                                         + (statistics.thresholdText ?? "unavailable"))
                                }
                                if let warning = row.result?.summaryWarning { Text(warning).foregroundStyle(.secondary) }
                                DisclosureGroup("Details") {
                                    Text(row.details).font(.system(.caption, design: .monospaced))
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                }
                            }
                            if row.id != group.rows.last?.id { Divider() }
                        }
                    }
                    Divider()
                }
                if layers.isEmpty { Text("No saved result layers selected.") }
            } else {
                Text("No unit selected")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .foregroundStyle(.black)
        .background(Color.white)
        .textSelection(.enabled)
    }

    private func statisticsLabel(_ statistics: SavedRFResultStatistics) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(statistics.meanSDText)
            if statistics.error == nil {
                Text("\(statistics.origin == "saved" ? "Saved baseline" : "Current source") · \(statistics.processingLabel)")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func groups(unitID: Int) -> [SavedRFInspectorGroup] {
        var groups: [SavedRFInspectorGroup] = []
        for layer in layers {
            let result = results[layer.resultKey]
            let row = SavedRFInspectorRow(layer: layer, unitID: unitID, result: result,
                                          error: errors[layer.resultKey], source: source)
            if let index = groups.firstIndex(where: {
                $0.dimension == layer.dimension && $0.timeRange == result?.timeRangeSeconds
            }) {
                groups[index].rows.append(row)
            } else {
                groups.append(SavedRFInspectorGroup(id: groups.count, dimension: layer.dimension,
                                                    timeRange: result?.timeRangeSeconds, rows: [row]))
            }
        }
        return groups
    }
}

private struct SavedRFInspectorGroup: Identifiable {
    let id: Int
    let dimension: String
    let timeRange: [Double]?
    var rows: [SavedRFInspectorRow]

    var title: String {
        guard let timeRange else { return dimension.uppercased() }
        return dimension.uppercased() + String(format: " · %g–%g ms", timeRange[0] * 1000, timeRange[1] * 1000)
    }

    var sharedStatistics: SavedRFResultStatistics? {
        guard rows.count > 1, let first = rows.first?.statistics,
              rows.allSatisfy({ row in
                  guard let current = row.statistics else { return false }
                  return first.sharesBaseline(with: current)
              }) else { return nil }
        return first
    }
}

private struct SavedRFInspectorRow: Identifiable {
    let layer: RFOverlayLayer
    let unitID: Int
    let result: SavedRFResult?
    let error: String?
    let statistics: SavedRFResultStatistics?
    var id: RFOverlayLayer { layer }

    init(layer: RFOverlayLayer, unitID: Int, result: SavedRFResult?, error: String?, source: RFMappingData?) {
        self.layer = layer
        self.unitID = unitID
        self.result = result
        self.error = error
        statistics = result.map { SavedRFResultStatistics.calculate(result: $0, source: source, unitID: unitID) }
    }

    var status: String {
        guard let result else { return "Unavailable" }
        if result.unitQC(unitID)?["keep"] == .bool(false) { return "Excluded by QC" }
        guard let unit = result.forUnit(unitID) else { return "Unit not saved" }
        let count = unit.mask.joined().filter { $0 }.count
        return count == 0 ? "No RF detected" : "\(count) detected \(count == 1 ? "bin" : "bins")"
    }

    var centers: String {
        guard let result, let unit = result.forUnit(unitID) else { return "" }
        var centers: [String] = []
        for y in unit.center.indices {
            for x in unit.center[y].indices where unit.center[y][x] {
                if result.dimension == "2d" {
                    centers.append(String(format: "(%g, %g)", result.xPositions[x], result.yPositions[y]))
                } else if result.axis == "x" {
                    centers.append(String(format: "X %g", result.xPositions[x]))
                } else {
                    centers.append(String(format: "Y %g", result.yPositions[y]))
                }
            }
        }
        let suffix = centers.count > 3 ? "; +\(centers.count - 3) more" : ""
        return centers.prefix(3).joined(separator: "; ") + suffix
    }

    var details: String {
        guard let result else { return error ?? "No saved result" }
        return (statistics.map { $0.detailsText + "\n\n" } ?? "") + result.diagnostics(for: unitID)
    }
}
