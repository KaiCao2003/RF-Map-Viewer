import CoreGraphics
import Foundation
import Observation

struct FigureExportRequest: Codable, Hashable {
    let id: UUID
}

@MainActor
final class FigureExportWindowRegistry {
    static let shared = FigureExportWindowRegistry()
    private var seeds: [UUID: FigureExportSeed] = [:]

    func prepare(from store: RFMappingStore) -> FigureExportRequest? {
        guard let data = store.data,
              !data.unitPool.isEmpty,
              let currentUnitID = store.selectedUnitID ?? data.unitPool.first else { return nil }
        let request = FigureExportRequest(id: UUID())
        seeds[request.id] = FigureExportSeed(
            data: data,
            viewerSnapshot: store.viewerSyncState,
            currentUnitID: currentUnitID,
            tuningSessionIndex: store.tuningSessionIndex,
            waveformChannelMode: store.waveformChannelMode,
            companions: store.figureExportCompanions
        )
        return request
    }

    func seed(for request: FigureExportRequest) -> FigureExportSeed? {
        seeds[request.id]
    }

    func release(_ request: FigureExportRequest) {
        seeds[request.id] = nil
    }
}

@MainActor
@Observable
final class FigureExportWorkspace {
    let seed: FigureExportSeed
    private let renderer = FigureExportRenderer()
    @ObservationIgnored private var exportTask: Task<Void, Never>?

    var unitSelection: FigureUnitSelection
    var pages: [FigurePageTemplate]
    var format: FigureExportFormat = .pdf
    var pageSize: FigurePageSizePreset = .letterLandscape
    var baseName = "rfmapping-figures"
    var destinationDirectory: URL?
    var overwriteExisting = false
    var outputScale: CGFloat = 2
    var selectedPageID: UUID?
    var previewUnitID: Int
    private(set) var customSelectionAnchorIndex: Int?
    var companions = FigureExportCompanions()
    var hdTuningURL: URL?
    var isExporting = false
    var completedPageCount = 0
    var totalPageCount = 0
    var errorMessage: String?
    var successMessage: String?

    init(seed: FigureExportSeed) {
        self.seed = seed
        companions = seed.companions
        hdTuningURL = seed.companions.hdTuning?.sourceURL
        unitSelection = FigureUnitSelection(
            mode: .current,
            customUnitIDs: [seed.currentUnitID]
        )
        let defaultPlot = FigureExportPlotKind.currentViewerDefault(
            from: seed.viewerSnapshot
        )
        let firstPage = FigurePageTemplate(
            name: "Page 1",
            plots: [FigurePlotPlacement(kind: defaultPlot)]
        )
        pages = [firstPage]
        selectedPageID = firstPage.id
        previewUnitID = seed.currentUnitID
        customSelectionAnchorIndex = seed.data.unitPool.firstIndex(of: seed.currentUnitID)
        if companions.hdTuning == nil { discoverHDTuning() }
        if companions.probeGeometry == nil { discoverProbeGeometry() }
        if companions.waveformArtifact == nil { discoverWaveform() }
    }

    var resolvedUnitIDs: [Int] {
        unitSelection.resolve(
            unitPool: seed.data.unitPool,
            currentUnitID: seed.currentUnitID
        )
    }

    var selectedPageIndex: Int? {
        guard let selectedPageID else { return nil }
        return pages.firstIndex { $0.id == selectedPageID }
    }

    var configuration: FigureExportConfiguration {
        FigureExportConfiguration(
            format: format,
            pageSize: pageSize,
            baseName: baseName.trimmingCharacters(in: .whitespacesAndNewlines),
            destinationDirectory: destinationDirectory,
            overwriteExisting: overwriteExisting,
            selectedUnitIDs: resolvedUnitIDs,
            pages: pages,
            viewerSnapshot: seed.viewerSnapshot,
            outputScale: outputScale
        )
    }

    var validationIssues: [FigureExportValidationIssue] {
        FigureExportValidation.issues(for: configuration)
    }

    var previewDescriptor: FigurePageRenderDescriptor? {
        guard let pageIndex = selectedPageIndex else { return nil }
        let availableUnits = resolvedUnitIDs
        let unitID = availableUnits.contains(previewUnitID)
            ? previewUnitID
            : availableUnits.first
        guard let unitID else { return nil }
        return renderer.previewDescriptor(
            unitID: unitID,
            pageIndex: pageIndex,
            configuration: configuration,
            data: seed.data,
            companions: companions
        )
    }

    func setUnitSelectionMode(_ mode: FigureUnitSelectionMode) {
        unitSelection.mode = mode
        if mode == .custom {
            let anchorIsValid = customSelectionAnchorIndex.map {
                seed.data.unitPool.indices.contains($0)
            } ?? false
            if !anchorIsValid {
                customSelectionAnchorIndex = seed.data.unitPool.firstIndex {
                    unitSelection.customUnitIDs.contains($0)
                }
            }
        }
        normalizePreviewSelection()
    }

    /// Finder-style unit-row selection. A plain click replaces the selection,
    /// Command-click toggles one unit while preserving the rest, and
    /// Shift-click selects the inclusive range from the last anchor. Holding
    /// Command with Shift adds that range instead of replacing the selection.
    func selectCustomUnit(
        at index: Int,
        modifiers: FigureUnitSelectionModifiers = []
    ) {
        guard unitSelection.mode == .custom,
              seed.data.unitPool.indices.contains(index) else { return }
        let unitID = seed.data.unitPool[index]
        if modifiers.contains(.shift) {
            let anchor = customSelectionAnchorIndex.flatMap {
                seed.data.unitPool.indices.contains($0) ? $0 : nil
            } ?? index
            let bounds = min(anchor, index)...max(anchor, index)
            let rangeIDs = Set(bounds.map { seed.data.unitPool[$0] })
            if modifiers.contains(.command) {
                unitSelection.customUnitIDs.formUnion(rangeIDs)
            } else {
                unitSelection.customUnitIDs = rangeIDs
            }
            customSelectionAnchorIndex = anchor
        } else if modifiers.contains(.command) {
            if unitSelection.customUnitIDs.contains(unitID) {
                unitSelection.customUnitIDs.remove(unitID)
            } else {
                unitSelection.customUnitIDs.insert(unitID)
            }
            customSelectionAnchorIndex = index
        } else {
            unitSelection.customUnitIDs = [unitID]
            customSelectionAnchorIndex = index
        }
        normalizePreviewSelection()
    }

    /// The visible checkbox is an explicit additive toggle, independent of
    /// keyboard modifiers. It also becomes the anchor for the next range.
    func setCustomUnitSelected(_ selected: Bool, at index: Int) {
        guard unitSelection.mode == .custom,
              seed.data.unitPool.indices.contains(index) else { return }
        let unitID = seed.data.unitPool[index]
        if selected {
            unitSelection.customUnitIDs.insert(unitID)
        } else {
            unitSelection.customUnitIDs.remove(unitID)
        }
        customSelectionAnchorIndex = index
        normalizePreviewSelection()
    }

    func addPage() {
        let existingNames = Set(pages.map { $0.name.trimmingCharacters(in: .whitespacesAndNewlines) })
        var pageNumber = pages.count + 1
        while existingNames.contains("Page \(pageNumber)") {
            pageNumber += 1
        }
        let page = FigurePageTemplate(
            name: "Page \(pageNumber)",
            plots: [
                FigurePlotPlacement(
                    kind: FigureExportPlotKind.currentViewerDefault(
                        from: seed.viewerSnapshot
                    )
                )
            ]
        )
        pages.append(page)
        selectedPageID = page.id
    }

    func removeSelectedPage() {
        guard pages.count > 1 else { return }
        guard let index = selectedPageIndex else { return }
        pages.remove(at: index)
        selectedPageID = pages[min(index, pages.count - 1)].id
    }

    func moveSelectedPage(_ offset: Int) {
        guard let index = selectedPageIndex else { return }
        let destination = index + offset
        guard pages.indices.contains(destination) else { return }
        pages.swapAt(index, destination)
    }

    func addPlot(_ kind: FigureExportPlotKind) {
        guard let index = selectedPageIndex else { return }
        pages[index].plots.append(FigurePlotPlacement(kind: kind))
    }

    func removePlot(_ placementID: UUID) {
        guard let pageIndex = selectedPageIndex else { return }
        pages[pageIndex].plots.removeAll { $0.id == placementID }
    }

    func movePlot(_ placementID: UUID, offset: Int) {
        guard let pageIndex = selectedPageIndex,
              let plotIndex = pages[pageIndex].plots.firstIndex(where: { $0.id == placementID }) else {
            return
        }
        let destination = plotIndex + offset
        guard pages[pageIndex].plots.indices.contains(destination) else { return }
        pages[pageIndex].plots.swapAt(plotIndex, destination)
    }

    func setHDTuningURL(_ url: URL) {
        do {
            let accessing = url.startAccessingSecurityScopedResource()
            defer { if accessing { url.stopAccessingSecurityScopedResource() } }
            companions.hdTuning = try HDTuningData(url: url)
            companions.hdError = nil
            hdTuningURL = url.standardizedFileURL
            successMessage = "Loaded HD tuning data from \(url.lastPathComponent)."
        } catch {
            companions.hdTuning = nil
            companions.hdError = error.localizedDescription
            hdTuningURL = url.standardizedFileURL
            errorMessage = "HD tuning data could not be loaded: \(error.localizedDescription)"
        }
    }

    func setProbePositionsURL(_ url: URL) {
        let accessing = url.startAccessingSecurityScopedResource()
        defer { if accessing { url.stopAccessingSecurityScopedResource() } }
        let probeName = HDTuningDiscovery.probeName(forRFURL: seed.data.url)
            ?? HDTuningDiscovery.probeName(forRFURL: url)
            ?? "Probe"
        let directory = url.deletingLastPathComponent()
        let adjacentChannels = directory.appendingPathComponent("channels.csv")
        let channelsURL = FileManager.default.fileExists(atPath: adjacentChannels.path)
            ? adjacentChannels
            : nil
        let paths = ProbeGeometryPaths(
            probeName: probeName,
            positionsURL: url.standardizedFileURL,
            channelsURL: channelsURL?.standardizedFileURL
        )
        do {
            companions.probeGeometry = try ProbeGeometryDiscovery.load(
                paths,
                rfUnitIDs: seed.data.unitPool
            )
            companions.probeError = nil
            successMessage = "Loaded probe positions from \(url.lastPathComponent)."
        } catch {
            companions.probeGeometry = nil
            companions.probeError = error.localizedDescription
            errorMessage = "Probe positions could not be loaded: \(error.localizedDescription)"
        }
    }

    func export() {
        guard !isExporting else { return }
        let issues = validationIssues
        guard issues.isEmpty else {
            errorMessage = issues.map(\.message).joined(separator: "\n")
            return
        }
        let exportConfiguration = configuration
        let exportCompanions = companions
        isExporting = true
        completedPageCount = 0
        totalPageCount = exportConfiguration.selectedUnitIDs.count * exportConfiguration.pages.count
        errorMessage = nil
        successMessage = nil
        // Yield once so the independent composer window can display its
        // progress state before ImageRenderer begins its main-actor work.
        exportTask = Task { @MainActor [weak self] in
            guard let self else { return }
            await Task.yield()
            defer {
                self.isExporting = false
                self.exportTask = nil
            }
            do {
                let result = try await self.renderer.export(
                    configuration: exportConfiguration,
                    data: self.seed.data,
                    companions: exportCompanions,
                    progress: { [weak self] progress in
                        self?.completedPageCount = progress.completedPages
                        self?.totalPageCount = progress.totalPages
                    }
                )
                self.successMessage = "Exported \(result.pageCount) page(s) to \(result.outputURL.path)."
            } catch is CancellationError {
                self.successMessage = "Figure export cancelled; no partial output was published."
            } catch {
                self.errorMessage = error.localizedDescription
            }
        }
    }

    func cancelExport() {
        exportTask?.cancel()
    }

    private func discoverHDTuning() {
        guard let url = HDTuningDiscovery.discover(
            forRFURL: seed.data.url,
            sessionIndex: seed.tuningSessionIndex
        ) else {
            companions.hdError = "No tuning curve was found for exact session \(seed.tuningSessionIndex)."
            return
        }
        hdTuningURL = url
        do {
            companions.hdTuning = try HDTuningData(url: url)
            companions.hdError = nil
        } catch {
            companions.hdError = error.localizedDescription
        }
    }

    private func discoverProbeGeometry() {
        guard let paths = ProbeGeometryDiscovery.discover(forRFURL: seed.data.url) else {
            companions.probeError = nil
            return
        }
        do {
            companions.probeGeometry = try ProbeGeometryDiscovery.load(
                paths,
                rfUnitIDs: seed.data.unitPool
            )
            companions.probeError = nil
        } catch {
            companions.probeGeometry = nil
            companions.probeError = error.localizedDescription
        }
    }

    private func discoverWaveform() {
        companions.waveformChannelMode = seed.waveformChannelMode
        do {
            companions.waveformArtifact = try WaveformArtifactStore.discover(
                forRFURL: seed.data.url
            )
            companions.waveformError = nil
        } catch {
            companions.waveformArtifact = nil
            companions.waveformError = error.localizedDescription
        }
    }

    private func normalizePreviewSelection() {
        if !resolvedUnitIDs.contains(previewUnitID) {
            previewUnitID = resolvedUnitIDs.first ?? seed.currentUnitID
        }
    }
}
