import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @Bindable var store: RFMappingStore
    @Bindable var pairingCoordinator: WindowPairingCoordinator
    @State private var preferences = ViewerPreferences.shared
    @State private var columnVisibility: NavigationSplitViewVisibility = .all
    let pairingWindowID: UUID
    let openJSONInNewWindow: (URL) -> Void

    var body: some View {
        NavigationSplitView(columnVisibility: $columnVisibility) {
            SidebarView(
                store: store,
                pairingCoordinator: pairingCoordinator,
                pairingWindowID: pairingWindowID
            )
            .navigationSplitViewColumnWidth(min: 236, ideal: 272, max: 320)
        } detail: {
            mainContent
                .frame(minWidth: 720)
        }
        .navigationSplitViewStyle(.balanced)
        .background(Color(nsColor: .windowBackgroundColor))
        .toolbar { viewerToolbar }
        .overlay {
            if store.isLoadingData {
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
            allowedContentTypes: [.json, .data],
            // A real RF document can transiently require about a gigabyte while
            // Foundation decodes it. Keep the picker single-document so several
            // full parses cannot overlap and exhaust memory.
            allowsMultipleSelection: false
        ) { result in
            switch result {
            case .success(let urls):
                if let url = urls.first { openJSONInNewWindow(url) }
            case .failure(let error):
                if (error as? CocoaError)?.code != .userCancelled {
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
        .fileImporter(
            isPresented: $store.isImportingTuning,
            allowedContentTypes: [.json, .data],
            allowsMultipleSelection: false
        ) { result in
            switch result {
            case .success(let urls):
                guard let url = urls.first else { return }
                Task { @MainActor in
                    _ = await store.loadTuningCurveAsync(url)
                }
            case .failure(let error):
                if (error as? CocoaError)?.code != .userCancelled {
                    store.errorMessage = error.localizedDescription
                }
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
        .task(id: tuningAutoloadRequest) {
            guard tuningAutoloadRequest.shouldLoad, store.tuningData == nil else { return }
            await store.autoLoadTuningCurveIfAvailable()
        }
    }

    private var tuningAutoloadRequest: TuningAutoloadRequest {
        TuningAutoloadRequest(
            rfPath: store.data?.url.path,
            shouldLoad: preferences.showTuningCurve
                && preferences.autoLoadTuningCurve
        )
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
                PlotControlBar(store: store)
                Divider()
                PlotTabsView(store: store, preferences: preferences)
            }
        } else {
            ContentUnavailableView {
                Label("RF Map Viewer", systemImage: "waveform.path.ecg.rectangle")
            } description: {
                Text("Open a unitsSpikeCounts JSON file to begin.")
            } actions: {
                Button("Open JSON") { store.isImporting = true }
                    .keyboardShortcut("o", modifiers: [.command])
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    @ToolbarContentBuilder
    private var viewerToolbar: some ToolbarContent {
        ToolbarItemGroup(placement: .navigation) {
            ControlGroup {
                Button { store.stepUnit(-1) } label: {
                    Image(systemName: "chevron.left")
                }
                .accessibilityLabel("Previous unit")
                .help("Previous unit")
                .disabled(!store.hasData)

                unitPicker

                Button { store.stepUnit(1) } label: {
                    Image(systemName: "chevron.right")
                }
                .accessibilityLabel("Next unit")
                .help("Next unit")
                .disabled(!store.hasData)
            }
            .controlSize(.small)
        }

        ToolbarItem(placement: .principal) {
            Picker("View", selection: $store.selectedTab) {
                ForEach(PlotTab.allCases) { tab in
                    Text(tab.rawValue).tag(tab)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 320)
            .disabled(!store.hasData)
        }

        ToolbarItemGroup(placement: .primaryAction) {
            Button { store.isImporting = true } label: {
                Label("Open JSON", systemImage: "doc.badge.plus")
            }
            .labelStyle(.iconOnly)
            .help("Open JSON in a new window")

            Button { store.prepareExport() } label: {
                Label("Export Displayed", systemImage: "square.and.arrow.up")
            }
            .labelStyle(.iconOnly)
            .help("Export displayed values")
            .disabled(!store.hasData)

            Menu {
                Button("Attach Tuning Curves…") {
                    store.isImportingTuning = true
                }
                SettingsLink {
                    Label("Viewer Settings…", systemImage: "gearshape")
                }
            } label: {
                Label("More", systemImage: "ellipsis.circle")
            }
            .help("More viewer actions")
        }
    }

    @ViewBuilder
    private var unitPicker: some View {
        if let data = store.data {
            Picker("Unit", selection: Binding(
                get: { store.unitIndex },
                set: {
                    store.unitIndex = $0
                    store.selectedCell = nil
                    store.clearHover()
                    store.ensureSelectedCell()
                }
            )) {
                ForEach(0..<data.nUnits, id: \.self) { index in
                    Text("Unit \(String(format: "%03d", index)) · Cluster \(data.clusterID(for: index))")
                        .tag(index)
                }
            }
            .labelsHidden()
            .frame(width: 190)
        } else {
            Text("No unit")
                .foregroundStyle(.secondary)
                .frame(width: 190)
        }
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

private struct TuningAutoloadRequest: Hashable {
    let rfPath: String?
    let shouldLoad: Bool
}

private struct PlotControlBar: View {
    @Bindable var store: RFMappingStore

    var body: some View {
        HStack(spacing: 10) {
            Picker("Metric", selection: Binding(
                get: { store.valueMode },
                set: { store.setValueMode($0) }
            )) {
                ForEach(ResponseValueMode.allCases) { mode in
                    Text(mode.rawValue)
                        .tag(mode)
                        .disabled(mode.requiresPresentationCounts && !store.supportsNormalizedValues)
                }
            }
            .frame(width: 188)

            Divider().frame(height: 22)

            compactTimeControl(
                title: "Target width",
                normalizedValue: Binding(
                    get: { store.timeResolutionMS },
                    set: {
                        store.timeResolutionMS = $0
                        store.timelineRangeAnchor = nil
                        store.normalizeControls()
                    }
                ),
                range: store.baseBinMS()...store.totalTimeMS(),
                step: store.baseBinMS()
            )

            Divider().frame(height: 22)

            contextControls

            Spacer(minLength: 0)
        }
        .controlSize(.small)
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
        .background(.bar)
    }

    @ViewBuilder
    private var contextControls: some View {
        switch store.selectedTab {
        case .rf:
            Text("RF window")
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
            Text("–").foregroundStyle(.tertiary)
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
            Text("ms").foregroundStyle(.secondary)
            Button { store.resetPlotRangeToDefault() } label: {
                Image(systemName: "arrow.counterclockwise")
            }
            .help("Reset the RF display window to 0–20 ms; the timeline remains full")
            .accessibilityLabel("Reset RF display window")

        case .delayRGB:
            Picker("Map", selection: Binding(
                get: { store.delayRGBMode },
                set: { store.delayRGBMode = $0 }
            )) {
                Text("Delay").tag(DelayRGBMode.delay)
                Text("RGB").tag(DelayRGBMode.rgb)
            }
            .pickerStyle(.segmented)
            .frame(width: 142)
            .help("RGB maps response, delay, and temporal entropy to color channels")

        case .timeline:
            Text("Full time axis")
                .foregroundStyle(.secondary)
            Button("Clear Selection") { store.clearTimelineSelection() }
                .disabled(!store.hasTimeSelection)
        }
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
    @Bindable var preferences: ViewerPreferences

    var body: some View {
        Group {
            switch store.selectedTab {
            case .rf:
                RFTuningSplitView(store: store, preferences: preferences)
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
        .background(Color(nsColor: .textBackgroundColor))
    }
}

private struct RFTuningSplitView: View {
    @Bindable var store: RFMappingStore
    @Bindable var preferences: ViewerPreferences
    @State private var isTuningCollapsed = false

    var body: some View {
        if !preferences.showTuningCurve {
            rfMap
        } else if isTuningCollapsed {
            rfMap
                .overlay(alignment: .topTrailing) {
                    Button {
                        isTuningCollapsed = false
                    } label: {
                        Label(
                            "Show HD",
                            systemImage: preferences.tuningLayout == .stacked
                                ? "rectangle.bottomthird.inset.filled"
                                : "rectangle.rightthird.inset.filled"
                        )
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .help("Show HD tuning curve")
                    .padding(10)
                }
        } else if preferences.tuningLayout == .stacked {
            VSplitView {
                rfMap
                    .frame(minHeight: 320)
                HDTuningCurveView(
                    store: store,
                    preferences: preferences,
                    collapse: { isTuningCollapsed = true }
                )
                .frame(minHeight: 240, idealHeight: 320)
            }
        } else {
            HSplitView {
                rfMap
                    .frame(minWidth: 520, idealWidth: 780)
                HDTuningCurveView(
                    store: store,
                    preferences: preferences,
                    collapse: { isTuningCollapsed = true }
                )
                .frame(minWidth: 300, idealWidth: 350, maxWidth: 430)
            }
        }
    }

    @ViewBuilder
    private var rfMap: some View {
        if store.spatialPlotFormat == .polar {
            PolarMapView(store: store, kind: .rf)
        } else {
            HeatmapView(store: store, kind: .rf)
        }
    }
}
