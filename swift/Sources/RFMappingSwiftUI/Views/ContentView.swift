import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @Bindable var store: RFMappingStore
    @Bindable var pairingCoordinator: WindowPairingCoordinator
    let pairingWindowID: UUID
    let openFigureExporter: () -> Void
    let openRFMapInNewWindow: (URL) -> Void

    var body: some View {
        HStack(spacing: 0) {
            SidebarView(
                store: store,
                pairingCoordinator: pairingCoordinator,
                pairingWindowID: pairingWindowID,
                openFigureExporter: openFigureExporter
            )
            .frame(width: 318)
            Divider()
            mainContent
        }
        .overlay {
            if store.isWaveformZoomed {
                WaveformZoomOverlay(store: store)
            } else if store.isLoadingData {
                ZStack {
                    Rectangle()
                        .fill(.ultraThinMaterial)
                    ProgressView("Opening RF mapping data…")
                        .padding(18)
                        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10))
                }
            }
        }
        .fileImporter(
            isPresented: $store.isImporting,
            allowedContentTypes: UTType.rfMappingReadableTypes,
            allowsMultipleSelection: true
        ) { result in
            switch result {
            case .success(let urls):
                if urls.isEmpty {
                    if !store.hasData {
                        WindowRouter.shared.expireColdInitialWindowClaim()
                    }
                } else {
                    urls.forEach(openRFMapInNewWindow)
                }
            case .failure(let error):
                if (error as? CocoaError)?.code == .userCancelled {
                    if !store.hasData {
                        WindowRouter.shared.expireColdInitialWindowClaim()
                    }
                } else {
                    store.errorMessage = error.localizedDescription
                }
            }
        }
        .fileExporter(
            isPresented: $store.isExporting,
            document: store.exportDocument,
            contentType: .rfCSV,
            defaultFilename: store.exportFilename
        ) { result in
            if case .failure(let error) = result {
                store.errorMessage = error.localizedDescription
            }
        }
        .alert(
            "RF Map Viewer",
            isPresented: Binding(
                get: { store.errorMessage != nil },
                set: { if !$0 { store.errorMessage = nil } }
            )
        ) {
            Button("OK") { store.errorMessage = nil }
        } message: {
            Text(store.errorMessage ?? "")
        }
        .onChange(of: hoverContext) { _, _ in
            store.clearHover()
        }
        .onChange(of: store.isImporting) { _, isImporting in
            if isImporting, !store.hasData {
                WindowRouter.shared.pauseColdInitialWindowFallback()
            } else if !isImporting, !store.hasData {
                DispatchQueue.main.async {
                    WindowRouter.shared.resumeColdInitialWindowFallback()
                }
            }
        }
    }

    private var hoverContext: HoverContext {
        HoverContext(
            dataPath: store.data?.url.path,
            unitIndex: store.unitIndex,
            valueMode: store.valueMode,
            rangeStartMS: store.rangeStartMS,
            rangeEndMS: store.rangeEndMS,
            plotRangeStartMS: store.plotRangeStartMS,
            plotRangeEndMS: store.plotRangeEndMS,
            flipY: store.flipY,
            palette: store.palette,
            polarRadiusMode: store.polarRadiusMode,
            spatialPlotFormat: store.spatialPlotFormat,
            delayRGBMode: store.delayRGBMode,
            responseFloor: store.responseFloor,
            xBins: store.xBins,
            yBins: store.yBins,
            timeResolutionMS: store.timeResolutionMS,
            smoothRadius: store.smoothRadius,
            selectedTab: store.selectedTab
        )
    }

    @ViewBuilder
    private var mainContent: some View {
        if store.isAwaitingStartupDocument {
            ProgressView("Opening RF mapping data…")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if store.hasData {
            VStack(spacing: 0) {
                HeaderView(store: store)
                Divider()
                PlotControlBar(store: store)
                Divider()
                if store.hasSelectedUnit {
                    PlotTabsView(store: store)
                } else {
                    unavailableUnitContent
                }
            }
        } else {
            ContentUnavailableView {
                Label("RF Map Viewer", systemImage: "waveform.path.ecg.rectangle")
            } description: {
                Text("Open an RF mapping .rfmap or JSON file to begin.")
            } actions: {
                Button("Open RF Map") { store.isImporting = true }
                    .keyboardShortcut("o", modifiers: [.command])
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    @ViewBuilder
    private var unavailableUnitContent: some View {
        switch store.unitUnavailableReason {
        case .some(.noQualityVisibleUnits(let total)):
            unavailableUnitView(
                title: "No visible units",
                systemImage: "line.3.horizontal.decrease.circle",
                description: "0/\(total) units pass the zero-spike RF-bin filter for the current RF sum window. Change the RF sum range, threshold, or filter setting."
            )
        case .some(.qualityFiltered(let unitID)):
            unavailableUnitView(
                title: "Unit hidden by RF-bin filter",
                systemImage: "line.3.horizontal.decrease.circle",
                description: "Cluster \(unitID) is present in this file but does not pass its current zero-spike RF-bin filter. It can remain in the paired union when another window still shows it."
            )
        case .some(.noProbeVisibleUnits):
            unavailableUnitView(
                title: "No units in Probe selection",
                systemImage: "scope",
                description: "The current Probe region excludes every quality-visible unit. Clear or change the Probe selection to continue."
            )
        case .some(.probeFiltered(let unitID)):
            unavailableUnitView(
                title: "Unit outside Probe selection",
                systemImage: "scope",
                description: "Cluster \(unitID) is present and quality-visible in this file, but is outside the current Probe region."
            )
        case .some(.pairedMissing(let unitID)):
            unavailableUnitView(
                title: "Unit not available",
                systemImage: "waveform.slash",
                description: "Cluster \(unitID) is part of the paired unit-ID union but is not present in this file. Use Previous/Next Unit to continue through the shared union."
            )
        case .some(.noSelection), .none:
            unavailableUnitView(
                title: "No unit selected",
                systemImage: "waveform.slash",
                description: "Choose a visible unit or adjust the current unit filters."
            )
        }
    }

    private func unavailableUnitView(
        title: String,
        systemImage: String,
        description: String
    ) -> some View {
        ContentUnavailableView {
            Label(title, systemImage: systemImage)
        } description: {
            Text(description)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

private struct HoverContext: Hashable {
    let dataPath: String?
    let unitIndex: Int
    let valueMode: ResponseValueMode
    let rangeStartMS: Double
    let rangeEndMS: Double
    let plotRangeStartMS: Double
    let plotRangeEndMS: Double
    let flipY: Bool
    let palette: RFPalette
    let polarRadiusMode: PolarRadiusMode
    let spatialPlotFormat: SpatialPlotFormat
    let delayRGBMode: DelayRGBMode
    let responseFloor: Double
    let xBins: Int
    let yBins: Int
    let timeResolutionMS: Double
    let smoothRadius: Int
    let selectedTab: PlotTab
}

private struct HeaderView: View {
    @Bindable var store: RFMappingStore

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(store.headerTitle)
                .font(.system(size: 17, weight: .semibold))
            Text(store.statusText)
                .font(.callout)
                .foregroundStyle(.secondary)
                .lineLimit(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }
}

private struct PlotControlBar: View {
    @Bindable var store: RFMappingStore

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 12) {
                LabeledContent("Value") {
                    Picker("Value", selection: Binding(
                        get: { store.valueMode },
                        set: { store.setValueMode($0) }
                    )) {
                        ForEach(ResponseValueMode.allCases) { mode in
                            Text(mode.rawValue)
                                .tag(mode)
                        }
                    }
                    .labelsHidden()
                    .frame(width: 190)
                }

                Divider().frame(height: 24)

                compactTimeControl(
                    title: "Time resolution",
                    normalizedValue: Binding(
                        get: { store.timeResolutionMS },
                        set: { store.timeResolutionMS = $0; store.timelineRangeAnchor = nil; store.normalizeControls() }
                    ),
                    range: store.baseBinMS()...store.totalTimeMS(),
                    step: store.baseBinMS()
                )

                Divider().frame(height: 24)

                Toggle("Polar layout", isOn: Binding(
                    get: { store.spatialPlotFormat == .polar },
                    set: { store.spatialPlotFormat = $0 ? .polar : .rectangular }
                ))
                .toggleStyle(.switch)
                .help("Off: rectangular spatial maps. On: polar spatial maps.")

                Spacer(minLength: 0)
            }

            switch store.selectedTab {
            case .rf:
                HStack(spacing: 8) {
                    Text("RF sum range (ms)")
                        .foregroundStyle(.secondary)
                    compactTimeControl(
                        title: "Start",
                        normalizedValue: Binding(
                            get: { store.plotRangeStartMS },
                            set: { store.plotRangeStartMS = $0; store.normalizePlotTimeRange() }
                        ),
                        range: store.timeAxisStartMS()...store.timeAxisEndMS(),
                        step: store.baseBinMS(),
                        showTitle: false,
                        showUnit: false
                    )
                    Text("to").foregroundStyle(.secondary)
                    compactTimeControl(
                        title: "End",
                        normalizedValue: Binding(
                            get: { store.plotRangeEndMS },
                            set: { store.plotRangeEndMS = $0; store.normalizePlotTimeRange() }
                        ),
                        range: store.timeAxisStartMS()...store.timeAxisEndMS(),
                        step: store.baseBinMS(),
                        showTitle: false,
                        showUnit: false
                    )
                    Button("Reset 0–200") { store.resetPlotRangeToDefault() }
                        .help("Use 0–200 ms, clamped and snapped to the available source bins")
                    Text("Timeline remains full and independent")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                    Spacer(minLength: 0)
                }
            case .delayRGB:
                HStack(spacing: 8) {
                    Toggle("RGB composite", isOn: Binding(
                        get: { store.delayRGBMode == .rgb },
                        set: { store.delayRGBMode = $0 ? .rgb : .delay }
                    ))
                    .toggleStyle(.switch)
                    .help("Off: delay only. On: RGB response/delay/entropy composite.")
                    Text("Off: Delay   On: RGB")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                    Spacer(minLength: 0)
                }
            case .timeline:
                HStack(spacing: 8) {
                    Text("Timeline always shows the full time axis")
                        .foregroundStyle(.secondary)
                    Button("Clear selection") { store.clearTimelineSelection() }
                        .disabled(!store.hasTimeSelection)
                    Spacer(minLength: 0)
                }
            }
        }
        .controlSize(.small)
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(.bar)
    }

    private func compactTimeControl(
        title: String,
        normalizedValue: Binding<Double>,
        range: ClosedRange<Double>,
        step: Double,
        showTitle: Bool = true,
        showUnit: Bool = true
    ) -> some View {
        HStack(spacing: 4) {
            if showTitle {
                Text(title).foregroundStyle(.secondary)
            }
            TextField(title, value: normalizedValue, format: .number.precision(.fractionLength(0...3)))
                .multilineTextAlignment(.trailing)
                .frame(width: 58)
                .onSubmit {
                    if title == "Start" || title == "End" {
                        store.normalizePlotTimeRange()
                    } else {
                        store.timelineRangeAnchor = nil
                        store.normalizeControls()
                    }
                }
            if showUnit {
                Text("ms").foregroundStyle(.secondary)
            }
            Stepper(title, value: normalizedValue, in: range, step: step)
                .labelsHidden()
        }
    }
}

private struct PlotTabsView: View {
    @Bindable var store: RFMappingStore

    var body: some View {
        VStack(spacing: 0) {
            Picker("View", selection: $store.selectedTab) {
                ForEach(PlotTab.allCases) { tab in
                    Text(tab.rawValue).tag(tab)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .padding([.horizontal, .top], 12)
            .padding(.bottom, 8)

            Group {
                switch store.selectedTab {
                case .rf:
                    HSplitView {
                        Group {
                            if store.spatialPlotFormat == .polar {
                                PolarMapView(store: store, kind: .rf)
                            } else {
                                HeatmapView(store: store, kind: .rf)
                            }
                        }
                        .frame(minWidth: 460, maxWidth: .infinity, maxHeight: .infinity)
                        HDTuningCompanionView(store: store)
                            .frame(minWidth: 260, idealWidth: 330, maxWidth: 420)
                    }
                case .delayRGB:
                    if store.delayRGBMode == .rgb {
                        RGBMapView(store: store)
                    } else if store.spatialPlotFormat == .polar {
                        PolarMapView(store: store, kind: .delay)
                    } else {
                        HeatmapView(store: store, kind: .delay)
                    }
                case .timeline:
                    TimelineView(store: store)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}
