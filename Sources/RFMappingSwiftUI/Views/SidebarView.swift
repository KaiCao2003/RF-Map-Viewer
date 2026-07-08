import SwiftUI

struct SidebarView: View {
    @Bindable var store: RFMappingStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                titleSection
                Divider()
                jsonSection
                Divider()
                unitSection
                Divider()
                rfValueSection
                displaySection
                Divider()
                selectedCellSection
                actionSection
                Spacer(minLength: 12)
            }
            .padding(14)
        }
        .background(.bar)
    }

    private var titleSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("RF Mapping Viewer")
                .font(.system(size: 17, weight: .semibold))
            Text(store.dataSummary)
                .font(.callout)
                .foregroundStyle(.secondary)
                .lineLimit(4)
        }
    }

    private var jsonSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("JSON")
                .font(.headline)
            Picker("JSON", selection: Binding(
                get: { store.selectedJSONPath },
                set: { store.loadJSON(path: $0) }
            )) {
                ForEach(store.availableJSONURLs, id: \.path) { url in
                    Text(JSONDiscovery.shortLabel(for: url))
                        .tag(url.path)
                }
            }
            .labelsHidden()
            HStack {
                Button("Scan") {
                    store.rescanJSONFiles()
                }
                Button("Open JSON") {
                    store.isImporting = true
                }
            }
        }
    }

    private var unitSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Unit")
                .font(.headline)
            HStack(spacing: 6) {
                Button {
                    store.stepUnit(-1)
                } label: {
                    Image(systemName: "chevron.left")
                }
                .help("Previous unit")

                Picker("Unit", selection: Binding(
                    get: { store.unitIndex },
                    set: {
                        store.unitIndex = $0
                        store.selectedCell = nil
                        store.ensureSelectedCell()
                    }
                )) {
                    if let data = store.data {
                        ForEach(0..<data.nUnits, id: \.self) { index in
                            Text("\(String(format: "%03d", index))  cluster \(data.clusterID(for: index))")
                                .tag(index)
                        }
                    }
                }
                .labelsHidden()

                Button {
                    store.stepUnit(1)
                } label: {
                    Image(systemName: "chevron.right")
                }
                .help("Next unit")
            }

            Text(store.unitStatsText)
                .font(.callout.weight(.semibold))
                .lineLimit(4)
        }
    }

    private var rfValueSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("RF value")
                .font(.headline)
            Picker("RF value", selection: Binding(
                get: { store.mode },
                set: {
                    store.mode = $0
                    store.normalizeControls()
                }
            )) {
                ForEach(RFMode.allCases) { mode in
                    Text(mode.rawValue).tag(mode)
                }
            }
            .labelsHidden()

            LabeledContent("Bin", value: store.timeGroupLabel(store.binIndex))
                .font(.callout)
            Slider(
                value: Binding(
                    get: { Double(store.binIndex) },
                    set: {
                        store.binIndex = Int($0.rounded())
                        store.normalizeControls()
                    }
                ),
                in: 0...Double(max(0, store.timeGroupCount() - 1)),
                step: 1
            )

            Stepper(
                "Time res \(formatMS(store.timeResolutionMS)) ms",
                value: Binding(
                    get: { store.timeResolutionMS },
                    set: {
                        store.timeResolutionMS = $0
                        store.normalizeControls()
                    }
                ),
                in: store.baseBinMS()...store.totalTimeMS(),
                step: store.baseBinMS()
            )

            HStack {
                Stepper(
                    "Range \(store.rangeStart)",
                    value: Binding(
                        get: { store.rangeStart },
                        set: {
                            store.rangeStart = $0
                            store.normalizeControls()
                        }
                    ),
                    in: 0...max(0, store.timeGroupCount() - 1)
                )
                Stepper(
                    "to \(store.rangeEnd)",
                    value: Binding(
                        get: { store.rangeEnd },
                        set: {
                            store.rangeEnd = $0
                            store.normalizeControls()
                        }
                    ),
                    in: 0...max(0, store.timeGroupCount() - 1)
                )
            }
            .font(.callout)
        }
    }

    private var displaySection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Display")
                .font(.headline)
            Toggle("Invert Y (MATLAB flip)", isOn: $store.flipY)

            Stepper(
                "X bins \(store.xBins)",
                value: Binding(
                    get: { store.xBins },
                    set: {
                        store.xBins = $0
                        store.normalizeControls()
                    }
                ),
                in: 1...(store.data?.nX ?? 1)
            )

            Stepper(
                "Y bins \(store.yBins)",
                value: Binding(
                    get: { store.yBins },
                    set: {
                        store.yBins = $0
                        store.normalizeControls()
                    }
                ),
                in: 1...(store.data?.nY ?? 1)
            )

            Stepper(
                "Smooth \(store.smoothRadius)",
                value: Binding(
                    get: { store.smoothRadius },
                    set: {
                        store.smoothRadius = $0
                        store.normalizeControls()
                    }
                ),
                in: 0...3
            )

            Picker("Palette", selection: $store.palette) {
                ForEach(RFPalette.allCases) { palette in
                    Text(palette.rawValue).tag(palette)
                }
            }

            Picker("Polar radius", selection: $store.polarRadiusMode) {
                ForEach(PolarRadiusMode.allCases) { mode in
                    Text(mode.rawValue).tag(mode)
                }
            }

            Stepper(
                "Delay floor \(String(format: "%.0f", store.responseFloor))",
                value: Binding(
                    get: { store.responseFloor },
                    set: {
                        store.responseFloor = $0
                        store.normalizeControls()
                    }
                ),
                in: 0...9999,
                step: 1
            )
        }
    }

    private var selectedCellSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Selected cell")
                .font(.headline)
            Text(store.displayedCellText)
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var actionSection: some View {
        HStack {
            Button("Export CSV") {
                store.prepareExport()
            }
            .disabled(!store.hasData)
            Button("Clear") {
                store.clearTimelineSelection()
            }
            .disabled(!store.hasData)
        }
    }
}
