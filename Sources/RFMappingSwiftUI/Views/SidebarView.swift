import SwiftUI

struct SidebarView: View {
    @Bindable var store: RFMappingStore
    @Bindable var pairingCoordinator: WindowPairingCoordinator
    let pairingWindowID: UUID

    var body: some View {
        List {
            Section("Source") {
                jsonSection
            }

            if store.hasData {
                Section("Current Unit") {
                    unitSummary
                }

                Section("Display") {
                    displaySection
                }

                Section("Windows") {
                    pairingSection
                }

                if !store.displayedCellText.isEmpty {
                    Section("Selection") {
                        selectedCellSection
                    }
                }
            }
        }
        .listStyle(.sidebar)
        .scrollContentBackground(.hidden)
        .background(.thinMaterial)
        .controlSize(.small)
    }

    private var pairingSection: some View {
        VStack(alignment: .leading, spacing: 5) {
            Toggle("Sync viewer windows", isOn: Binding(
                get: { pairingCoordinator.isPairingEnabled },
                set: { pairingCoordinator.setPairingEnabled($0, sourceID: pairingWindowID) }
            ))
            .disabled(!pairingCoordinator.isPairingEnabled && !pairingCoordinator.eligibility.canEnable)
            .help("Pair loaded windows whose ordered unit lists match exactly")

            Text(pairingCoordinator.statusText())
                .font(.caption)
                .foregroundStyle(pairingStatusColor)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var pairingStatusColor: Color {
        switch pairingCoordinator.eligibility {
        case .matching:
            pairingCoordinator.isPairingEnabled ? .green : .secondary
        case .noSecondWindow:
            .secondary
        case .mismatch:
            .orange
        }
    }

    private var jsonSection: some View {
        VStack(alignment: .leading, spacing: 7) {
            Picker("JSON", selection: Binding(
                get: { store.selectedJSONPath },
                set: { path in
                    Task { @MainActor in
                        _ = await store.loadJSONAsync(path: path)
                    }
                }
            )) {
                ForEach(store.availableJSONURLs, id: \.path) { url in
                    Text(JSONDiscovery.choiceLabel(for: url)).tag(url.path)
                }
            }
            .labelsHidden()
            .disabled(store.isAwaitingStartupDocument || store.isLoadingData)
            Text(store.dataSummary)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(3)
                .textSelection(.enabled)
                .help(store.data?.url.path ?? "No JSON loaded")
            Button {
                store.isImporting = true
            } label: {
                Label("Open JSON…", systemImage: "doc.badge.plus")
            }
                .disabled(store.isLoadingData)
        }
    }

    @ViewBuilder
    private var unitSummary: some View {
        if let data = store.data {
            let metrics = data.metrics(for: store.unitIndex)
            let delay = metrics.delayMS[metrics.bestY][metrics.bestX]
            LabeledContent("Spikes") {
                Text(String(format: "%.0f", metrics.totalSpikes))
                    .monospacedDigit()
            }
            LabeledContent("Best cell") {
                Text("y\(metrics.bestY + 1) · x\(metrics.bestX + 1)")
                    .monospacedDigit()
            }
            LabeledContent("Peak delay") {
                Text(delay.map { String(format: "%.1f ms", $0) } ?? "n/a")
                    .monospacedDigit()
            }
        }
    }

    private var displaySection: some View {
        VStack(alignment: .leading, spacing: 9) {
            Picker("Map layout", selection: $store.spatialPlotFormat) {
                ForEach(SpatialPlotFormat.allCases) { format in
                    Text(format.rawValue).tag(format)
                }
            }
            .pickerStyle(.segmented)

            Toggle("Invert Y", isOn: $store.flipY)
                .help("Match MATLAB flip-y display convention")

            integerControl(
                title: "X bins",
                value: Binding(
                    get: { store.xBins },
                    set: { store.xBins = $0; store.normalizeControls() }
                ),
                range: 1...(store.data?.nX ?? 1)
            )

            integerControl(
                title: "Y bins",
                value: Binding(
                    get: { store.yBins },
                    set: { store.yBins = $0; store.normalizeControls() }
                ),
                range: 1...(store.data?.nY ?? 1)
            )

            integerControl(
                title: "Smooth",
                value: Binding(
                    get: { store.smoothRadius },
                    set: { store.smoothRadius = $0; store.normalizeControls() }
                ),
                range: 0...3
            )

            Picker("Palette", selection: $store.palette) {
                ForEach(RFPalette.allCases) { palette in
                    Text(palette.rawValue).tag(palette)
                }
            }

            if store.spatialPlotFormat == .polar {
                Picker("Polar radius", selection: $store.polarRadiusMode) {
                    ForEach(PolarRadiusMode.allCases) { mode in
                        Text(mode.rawValue).tag(mode)
                    }
                }
            }

            DisclosureGroup("Advanced") {
                Stepper(
                    "Delay response floor \(String(format: "%.0f", store.responseFloor))",
                    value: Binding(
                        get: { store.responseFloor },
                        set: { store.responseFloor = $0; store.normalizeControls() }
                    ),
                    in: 0...9999,
                    step: 1
                )
                .padding(.top, 6)
            }
            .foregroundStyle(.secondary)
        }
    }

    private func integerControl(
        title: String,
        value: Binding<Int>,
        range: ClosedRange<Int>
    ) -> some View {
        HStack {
            Text(title)
            Spacer()
            TextField(title, value: value, format: .number)
                .multilineTextAlignment(.trailing)
                .frame(width: 44)
            Stepper(title, value: value, in: range)
                .labelsHidden()
        }
    }

    private var selectedCellSection: some View {
        Text(store.displayedCellText)
            .font(.system(size: 11, design: .monospaced))
            .foregroundStyle(.secondary)
            .lineLimit(nil)
            .fixedSize(horizontal: false, vertical: true)
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

}
