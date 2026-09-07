import Foundation
import Observation
import TuningCurveCore

enum ComparisonSlot: Int, CaseIterable, Identifiable, Sendable {
    case first
    case second

    var id: Int { rawValue }
    var title: String { self == .first ? "TC I" : "TC II" }
    var accessibilityTitle: String { self == .first ? "tuning curve one" : "tuning curve two" }

    var other: ComparisonSlot { self == .first ? .second : .first }
}

enum ComparisonPlotMode: String, CaseIterable, Identifiable, Sendable {
    case line
    case polar

    var id: String { rawValue }
    var label: String { self == .line ? "Line" : "Polar" }
    var systemImage: String { self == .line ? "chart.xyaxis.line" : "circle.hexagongrid" }

    var subtitle: String {
        self == .line
            ? "Calibrated direction (degrees)"
            : "0° top · clockwise positive"
    }
}

struct LoadedTuningCurve: Identifiable, Sendable {
    let data: HDTuningData

    var id: URL { data.sourceURL }
    var url: URL { data.sourceURL }
}

struct ComparisonAlert: Identifiable {
    let id = UUID()
    let title: String
    let message: String
}

private struct CurveLoadAssignment: Sendable {
    let slot: ComparisonSlot
    let url: URL
    let token: UUID
}

private struct CurveLoadOutcome: Sendable {
    let assignment: CurveLoadAssignment
    let data: HDTuningData?
    let errorDescription: String?
}

@MainActor
@Observable
final class TCComparisonStore {
    static let angleOffsetRange = -180.0...180.0
    static let smoothingSigmaRange = 0.25...10.0
    nonisolated static let readableExtensions: Set<String> = ["tc", "json"]

    private(set) var firstCurve: LoadedTuningCurve?
    private(set) var secondCurve: LoadedTuningCurve?
    private var loadTokens: [ComparisonSlot: UUID] = [:]

    var selectedUnitID: Int?
    var displayBins = HDTuningData.defaultDisplayBins
    var smoothingEnabled = true
    var smoothingSigma = HDTuningData.defaultSmoothSigma
    var firstAngleOffset = 0.0
    var secondAngleOffset = 0.0
    var plotMode: ComparisonPlotMode = .line

    var isImporting = false
    private(set) var pendingImportSlot: ComparisonSlot?
    var alert: ComparisonAlert?

    var loadedCurveCount: Int {
        [firstCurve, secondCurve].compactMap { $0 }.count
    }

    var hasTwoCurves: Bool {
        firstCurve != nil && secondCurve != nil
    }

    var isLoadingAnyCurve: Bool {
        !loadTokens.isEmpty
    }

    var availableUnitIDs: [Int] {
        switch (firstCurve, secondCurve) {
        case let (.some(first), .some(second)):
            let secondIDs = Set(second.data.unitIDs)
            return first.data.unitIDs.filter(secondIDs.contains).sorted()
        case let (.some(first), .none):
            return first.data.unitIDs.sorted()
        case let (.none, .some(second)):
            return second.data.unitIDs.sorted()
        case (.none, .none):
            return []
        }
    }

    var comparisonStatus: String {
        switch loadedCurveCount {
        case 0:
            return "Open or drop two tuning-curve files to begin."
        case 1:
            return "One curve loaded — add the second curve to compare."
        default:
            if availableUnitIDs.isEmpty {
                return "These files do not contain a shared unit ID."
            }
            return "\(availableUnitIDs.count) shared unit\(availableUnitIDs.count == 1 ? "" : "s")"
        }
    }

    func curve(in slot: ComparisonSlot) -> LoadedTuningCurve? {
        slot == .first ? firstCurve : secondCurve
    }

    func isLoading(_ slot: ComparisonSlot) -> Bool {
        loadTokens[slot] != nil
    }

    func angleOffset(for slot: ComparisonSlot) -> Double {
        slot == .first ? firstAngleOffset : secondAngleOffset
    }

    func setAngleOffset(_ value: Double, for slot: ComparisonSlot) {
        let finite = value.isFinite ? value : 0
        let clamped = min(Self.angleOffsetRange.upperBound, max(Self.angleOffsetRange.lowerBound, finite))
        if slot == .first {
            firstAngleOffset = clamped
        } else {
            secondAngleOffset = clamped
        }
    }

    func beginImport(into slot: ComparisonSlot? = nil) {
        pendingImportSlot = slot
        isImporting = true
    }

    func finishImport(with result: Result<[URL], Error>) {
        let destination = pendingImportSlot
        pendingImportSlot = nil
        switch result {
        case .success(let urls):
            accept(urls: urls, preferredSlot: destination)
        case .failure(let error):
            if (error as? CocoaError)?.code == .userCancelled { return }
            alert = ComparisonAlert(
                title: "Could Not Open Tuning Curve",
                message: error.localizedDescription
            )
        }
    }

    @discardableResult
    func accept(urls: [URL], preferredSlot: ComparisonSlot? = nil) -> Bool {
        let readable = urls.filter(Self.isReadableTuningCurveURL)
        guard !readable.isEmpty else {
            alert = ComparisonAlert(
                title: "Unsupported File",
                message: "Choose a .tc or .json tuning-curve file."
            )
            return false
        }

        let selectedURLs = Array(readable.prefix(2))
        let slots: [ComparisonSlot]
        if let preferredSlot {
            slots = selectedURLs.count == 1
                ? [preferredSlot]
                : [preferredSlot, preferredSlot.other]
        } else if selectedURLs.count > 1 {
            slots = [.first, .second]
        } else if firstCurve == nil && !isLoading(.first) {
            slots = [.first]
        } else if secondCurve == nil && !isLoading(.second) {
            slots = [.second]
        } else {
            slots = [.first]
        }

        var assignments: [CurveLoadAssignment] = []
        for (slot, url) in zip(slots, selectedURLs) {
            let token = UUID()
            loadTokens[slot] = token
            assignments.append(CurveLoadAssignment(slot: slot, url: url, token: token))
        }
        Task { await load(assignments) }
        return true
    }

    func removeCurve(in slot: ComparisonSlot) {
        loadTokens[slot] = nil
        setCurve(nil, in: slot)
        setAngleOffset(0, for: slot)
        reconcileSelectedUnit()
    }

    func clearAll() {
        ComparisonSlot.allCases.forEach(removeCurve)
    }

    func swapCurves() {
        let curve = firstCurve
        firstCurve = secondCurve
        secondCurve = curve
        let offset = firstAngleOffset
        firstAngleOffset = secondAngleOffset
        secondAngleOffset = offset
        reconcileSelectedUnit()
    }

    func stepUnit(_ delta: Int) {
        let units = availableUnitIDs
        guard !units.isEmpty else { return }
        let currentIndex = selectedUnitID.flatMap { units.firstIndex(of: $0) } ?? 0
        let next = (currentIndex + delta % units.count + units.count) % units.count
        selectedUnitID = units[next]
    }

    func togglePlotMode() {
        plotMode = plotMode == .line ? .polar : .line
    }

    func processedCurve(for slot: ComparisonSlot) -> ProcessedHDCurve? {
        guard let unitID = selectedUnitID,
              let tuning = curve(in: slot)?.data else { return nil }
        return try? tuning.processedCurve(
            unitID: unitID,
            displayBins: displayBins,
            smoothing: smoothingEnabled,
            sigma: smoothingSigma
        ).applyingAngleOffset(angleOffset(for: slot))
    }

    func normalizeSettings() {
        displayBins = HDTuningData.normalizedDisplayBinCount(displayBins)
        let finiteSigma = smoothingSigma.isFinite
            ? smoothingSigma
            : HDTuningData.defaultSmoothSigma
        smoothingSigma = min(
            Self.smoothingSigmaRange.upperBound,
            max(Self.smoothingSigmaRange.lowerBound, finiteSigma)
        )
    }

    private func load(_ assignments: [CurveLoadAssignment]) async {
        var outcomes: [CurveLoadOutcome] = []
        await withTaskGroup(of: CurveLoadOutcome.self) { group in
            for assignment in assignments {
                group.addTask {
                    do {
                        let data = try await Self.decode(url: assignment.url)
                        return CurveLoadOutcome(
                            assignment: assignment,
                            data: data,
                            errorDescription: nil
                        )
                    } catch {
                        return CurveLoadOutcome(
                            assignment: assignment,
                            data: nil,
                            errorDescription: error.localizedDescription
                        )
                    }
                }
            }
            for await outcome in group {
                outcomes.append(outcome)
            }
        }

        var errors: [String] = []
        for outcome in outcomes {
            let assignment = outcome.assignment
            guard loadTokens[assignment.slot] == assignment.token else { continue }
            loadTokens[assignment.slot] = nil
            if let data = outcome.data {
                setCurve(LoadedTuningCurve(data: data), in: assignment.slot)
            } else if let errorDescription = outcome.errorDescription {
                errors.append("\(assignment.url.lastPathComponent): \(errorDescription)")
            }
        }
        reconcileSelectedUnit()
        if !errors.isEmpty {
            alert = ComparisonAlert(
                title: "Could Not Open Tuning Curve",
                message: errors.joined(separator: "\n\n")
            )
        }
    }

    nonisolated private static func decode(url: URL) async throws -> HDTuningData {
        try await Task.detached(priority: .userInitiated) {
            let accessing = url.startAccessingSecurityScopedResource()
            defer {
                if accessing { url.stopAccessingSecurityScopedResource() }
            }
            return try HDTuningData(url: url)
        }.value
    }

    nonisolated private static func isReadableTuningCurveURL(_ url: URL) -> Bool {
        url.isFileURL && readableExtensions.contains(url.pathExtension.lowercased())
    }

    private func setCurve(_ curve: LoadedTuningCurve?, in slot: ComparisonSlot) {
        if slot == .first {
            firstCurve = curve
        } else {
            secondCurve = curve
        }
    }

    private func reconcileSelectedUnit() {
        let units = availableUnitIDs
        if let selectedUnitID, units.contains(selectedUnitID) { return }
        selectedUnitID = units.first
    }
}
