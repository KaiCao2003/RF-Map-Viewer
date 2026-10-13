import SwiftUI

struct ViewerSettingsView: View {
    @Bindable var store: RFMappingStore
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 0) {
            Text("Viewer Settings").font(.title2).padding()
            TabView {
                general.tabItem { Text("General") }
                rf.tabItem { Text("RF Map") }
                waveform.tabItem { Text("Waveform") }
                tuning.tabItem { Text("Tuning Curve") }
            }
            .padding(.horizontal)
            Divider()
            HStack {
                Text("Save defaults for open and new document windows.")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Save Defaults") {
                    store.normalizeControls()
                    store.saveDisplayDefaults()
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
                .disabled(!validSettings)
            }.padding()
        }
        .frame(width: 600, height: 650)
        .preferredColorScheme(.light)
    }

    private var general: some View {
        Form {
            Section("Companion views") {
                Toggle("Probe layout", isOn: $store.displayDefaults.showProbe)
                Toggle("Unit info on hover", isOn: $store.displayDefaults.showProbeUnitInfo)
                Toggle("Auto-load probe layout", isOn: $store.displayDefaults.autoLoadProbe)
                Toggle("HD tuning curve", isOn: $store.displayDefaults.showTuning)
                Toggle("Auto-load tuning curve", isOn: $store.displayDefaults.autoLoadTuning)
            }
            Section("Visible tabs") {
                ForEach(PlotTab.allCases) { tab in
                    Toggle(tab.rawValue, isOn: Binding(
                        get: { store.visibleTabs.contains(tab) },
                        set: { store.setTabVisible(tab, visible: $0) }
                    ))
                    .disabled(store.visibleTabs.count == 1 && store.visibleTabs.contains(tab))
                }
                Picker("Initial tab", selection: $store.displayDefaults.initialTab) {
                    ForEach(store.visibleTabs) { Text($0.rawValue).tag($0) }
                }
            }
        }.formStyle(.grouped)
    }

    private var rf: some View {
        Form {
            Section("Display") {
                Picker("Value", selection: Binding(get: { store.valueMode }, set: store.setValueMode)) {
                    ForEach(ResponseValueMode.allCases) { mode in
                        Text(mode.rawValue).tag(mode)
                            .disabled(mode == .meanFiringRate && store.data?.supportsMeanFiringRate != true)
                    }
                }
                TextField("Time bin (ms)", value: $store.timeResolutionMS, format: .number)
                Stepper("X bins: \(store.xBins)", value: $store.xBins, in: 1...max(1, store.data?.nX ?? 1))
                Stepper("Y bins: \(store.yBins)", value: $store.yBins, in: 1...max(1, store.data?.nY ?? 1))
                Stepper("Spatial smoothing: \(store.smoothRadius)", value: $store.smoothRadius, in: 0...3)
                Picker("Palette", selection: $store.palette) {
                    ForEach(RFPalette.allCases) { Text($0.rawValue).tag($0) }
                }
                Picker("Layout", selection: $store.spatialPlotFormat) {
                    ForEach(SpatialPlotFormat.allCases) { Text($0.rawValue).tag($0) }
                }
                Picker("Polar radius", selection: $store.polarRadiusMode) {
                    ForEach(PolarRadiusMode.allCases) { Text($0.rawValue).tag($0) }
                }
                Toggle("Flip Y", isOn: $store.flipY)
                Toggle("RGB composite", isOn: Binding(get: { store.delayRGBMode == .rgb }, set: { store.delayRGBMode = $0 ? .rgb : .delay }))
            }
            Section("Saved RF overlay") {
                RFOverlayLayerControls(store: store)
                TextField("Border width (pt)", value: $store.displayDefaults.overlayWidth, format: .number)
                TextField("Excitatory 2D color (#RRGGBB)", text: $store.displayDefaults.overlay2DColor)
                TextField("Excitatory 1D color (#RRGGBB)", text: $store.displayDefaults.overlay1DColor)
                TextField("Excitatory overlap color (#RRGGBB)", text: $store.displayDefaults.overlayIntersectionColor)
                TextField("Inhibitory 2D color (#RRGGBB)", text: $store.displayDefaults.overlayInhibitory2DColor)
                TextField("Inhibitory 1D color (#RRGGBB)", text: $store.displayDefaults.overlayInhibitory1DColor)
                TextField("Inhibitory overlap color (#RRGGBB)", text: $store.displayDefaults.overlayInhibitoryIntersectionColor)
            }
        }.formStyle(.grouped)
    }

    private var waveform: some View {
        Form {
            Toggle("Local average waveform", isOn: Binding(get: { store.showWaveform }, set: store.setShowWaveform))
            Picker("Channels", selection: Binding(get: { store.waveformChannelMode }, set: store.setWaveformChannelMode)) {
                ForEach(WaveformChannelMode.allCases) { mode in Text(mode.rawValue).tag(mode) }
            }
        }.formStyle(.grouped)
    }

    private var tuning: some View {
        Form {
            Stepper("Tuning session: \(store.tuningSessionIndex)", value: Binding(get: { store.tuningSessionIndex }, set: store.setTuningSessionIndex), in: 1...999)
            Picker("Plot", selection: $store.displayDefaults.tuningStyle) {
                ForEach(TuningPlotStyle.allCases) { Text($0.rawValue).tag($0) }
            }
            Picker("Layout", selection: $store.displayDefaults.tuningLayout) {
                ForEach(TuningPaneLayout.allCases) { Text($0.rawValue).tag($0) }
            }
            TextField("HD bins (divisors of 180)", value: $store.displayDefaults.tuningBins, format: .number)
            Toggle("Smooth tuning curve", isOn: $store.displayDefaults.tuningSmoothing)
            TextField("Gaussian σ (degrees)", value: $store.displayDefaults.tuningSigmaDegrees, format: .number)
                .disabled(!store.displayDefaults.tuningSmoothing)
            Toggle("Shared 0–peak Hz scale", isOn: $store.displayDefaults.tuningSharedScale)
        }.formStyle(.grouped)
    }

    private var validSettings: Bool {
        let d = store.displayDefaults
        return store.timeResolutionMS.isFinite && store.timeResolutionMS > 0
            && d.overlayWidth.isFinite && d.overlayWidth > 0
            && d.tuningSigmaDegrees.isFinite && d.tuningSigmaDegrees > 0
            && [d.overlay2DColor, d.overlay1DColor, d.overlayIntersectionColor,
                d.overlayInhibitory2DColor, d.overlayInhibitory1DColor, d.overlayInhibitoryIntersectionColor].allSatisfy {
                $0.count == 7 && $0.first == "#" && UInt64($0.dropFirst(), radix: 16) != nil
            }
    }
}

struct RFInspectorView: View {
    @Bindable var store: RFMappingStore

    var body: some View {
        TabView {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Unit").font(.headline)
                    Text(store.unitStatsText)
                    Text("Response").font(.headline)
                    Text(store.displayedCellText).font(.system(.body, design: .monospaced))
                }.frame(maxWidth: .infinity, alignment: .leading).padding()
            }.tabItem { Text("Selection") }
            ScrollView {
                Text(store.savedResultDetails)
                    .frame(maxWidth: .infinity, alignment: .leading).padding()
            }.tabItem { Text("Saved results") }
        }
        .textSelection(.enabled)
        .padding(12)
        .frame(minWidth: 360, minHeight: 420)
        .preferredColorScheme(.light)
    }
}
