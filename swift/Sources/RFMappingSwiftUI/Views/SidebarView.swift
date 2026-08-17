import SwiftUI

struct SidebarView: View {
    @Bindable var store: RFMappingStore
    @Bindable var pairingCoordinator: WindowPairingCoordinator
    let pairingWindowID: UUID
    let openFigureExporter: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                titleSection
                Divider()
                rfMapSection
                Divider()
                pairingSection
                Divider()
                unitSection
                Divider()
                displaySection
                Divider()
                selectedCellSection
                actionSection
                shortcutHint
                Spacer(minLength: 12)
            }
            .padding(14)
        }
        .background(.bar)
    }

    private var pairingSection: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text("Window pairing").font(.headline)
            Toggle("Sync viewer windows", isOn: Binding(
                get: { pairingCoordinator.isPairingEnabled },
                set: { pairingCoordinator.setPairingEnabled($0, sourceID: pairingWindowID) }
            ))
            .disabled(!pairingCoordinator.isPairingEnabled && !pairingCoordinator.eligibility.canEnable)
            .help("Pair loaded windows by a shared sorted union of unit IDs")

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

    private var titleSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("RF Map Viewer", systemImage: "waveform.path.ecg.rectangle")
                .font(.system(size: 17, weight: .semibold))
            Text(store.dataSummary)
                .font(.callout)
                .foregroundStyle(.secondary)
                .lineLimit(6)
                .textSelection(.enabled)
        }
    }

    private var rfMapSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Current RF map").font(.headline)
            Picker("RF map", selection: Binding(
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
            Button("Open…") { store.isImporting = true }
                .disabled(store.isLoadingData)
        }
    }

    private var unitSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Unit").font(.headline)
            HStack(spacing: 6) {
                Button { store.stepUnit(-1) } label: { Image(systemName: "chevron.left") }
                    .help("Previous unit (← or [)")

                Picker("Unit", selection: Binding(
                    get: { store.selectedUnitID ?? store.navigationUnitIDs.first ?? 0 },
                    set: { store.selectUnitID($0) }
                )) {
                    if let data = store.data {
                        ForEach(store.navigationUnitIDs, id: \.self) { unitID in
                            if let index = data.unitIndex(forUnitID: unitID) {
                                Text("\(String(format: "%03d", index))  cluster \(unitID)")
                                    .tag(unitID)
                            } else {
                                Text("N/A  cluster \(unitID)")
                                    .tag(unitID)
                            }
                        }
                    }
                }
                .labelsHidden()

                Button { store.stepUnit(1) } label: { Image(systemName: "chevron.right") }
                    .help("Next unit (→ or ])")
            }

            Text(store.unitStatsText)
                .font(.callout.weight(.semibold))
                .lineLimit(4)
        }
    }

    private var displaySection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Display").font(.headline)
            Toggle("Invert Y (MATLAB flip)", isOn: $store.flipY)

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
        VStack(alignment: .leading, spacing: 8) {
            Text("Selected cell").font(.headline)
            Text(store.displayedCellText)
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var actionSection: some View {
        VStack(alignment: .leading, spacing: 7) {
            Button("Export Figures…", action: openFigureExporter)
                .disabled(!store.hasData)
            HStack {
                Button("Export displayed CSV") { store.prepareExport() }
                    .disabled(!store.hasSelectedUnit)
                Button("Full range") { store.clearTimelineSelection() }
                    .disabled(!store.hasTimeSelection)
            }
        }
    }

    private var shortcutHint: some View {
        Text("←/→ unit   ↑/↓ timeline\n⇧,/⇧. time resolution   1–3 views")
            .font(.caption)
            .foregroundStyle(.tertiary)
    }
}
