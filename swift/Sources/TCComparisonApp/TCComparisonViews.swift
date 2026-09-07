import AppKit
import Foundation
import SwiftUI
import TuningCurveCore
import UniformTypeIdentifiers

struct TCComparisonRootView: View {
    @Bindable var store: TCComparisonStore
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var isDropTargeted = false

    var body: some View {
        NavigationSplitView {
            TCComparisonSidebar(store: store)
                .navigationSplitViewColumnWidth(min: 285, ideal: 315, max: 370)
        } detail: {
            TCComparisonDetail(store: store)
                .navigationTitle("TC Comparison")
        }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button {
                    store.stepUnit(-1)
                } label: {
                    Label("Previous shared unit", systemImage: "chevron.left")
                }
                .disabled(store.availableUnitIDs.isEmpty)

                unitPicker

                Button {
                    store.stepUnit(1)
                } label: {
                    Label("Next shared unit", systemImage: "chevron.right")
                }
                .disabled(store.availableUnitIDs.isEmpty)

                Picker("Plot mode", selection: $store.plotMode) {
                    ForEach(ComparisonPlotMode.allCases) { mode in
                        Label(mode.label, systemImage: mode.systemImage).tag(mode)
                    }
                }
                .labelsHidden()
                .pickerStyle(.segmented)
                .frame(width: 150)
                .help("Switch line / polar view (P)")

                Button {
                    store.beginImport()
                } label: {
                    Label("Open tuning curves", systemImage: "folder.badge.plus")
                }
                .help("Choose one or two .tc/.json files")
            }
        }
        .fileImporter(
            isPresented: $store.isImporting,
            allowedContentTypes: UTType.comparisonTuningCurveReadableTypes,
            allowsMultipleSelection: true,
            onCompletion: store.finishImport
        )
        .dropDestination(for: URL.self) { urls, _ in
            store.accept(urls: urls)
        } isTargeted: { targeted in
            isDropTargeted = targeted
        }
        .overlay {
            if isDropTargeted {
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(Color.accentColor, style: StrokeStyle(lineWidth: 3, dash: [8, 6]))
                    .padding(8)
                    .allowsHitTesting(false)
                    .transition(.opacity)
            }
        }
        .overlay(alignment: .top) {
            if isDropTargeted {
                Label("Drop one or two tuning curves", systemImage: "arrow.down.doc.fill")
                    .font(.headline)
                    .padding(.horizontal, 18)
                    .padding(.vertical, 10)
                    .background(.regularMaterial, in: Capsule())
                    .shadow(radius: 12, y: 5)
                    .padding(.top, 12)
                    .allowsHitTesting(false)
                    .transition(
                        reduceMotion
                            ? .opacity
                            : .move(edge: .top).combined(with: .opacity)
                    )
            }
        }
        .background(TCComparisonKeyboardMonitor(store: store))
        .animation(
            reduceMotion ? nil : .easeOut(duration: 0.16),
            value: isDropTargeted
        )
        .onOpenURL { url in
            _ = store.accept(urls: [url])
        }
        .alert(item: $store.alert) { alert in
            Alert(
                title: Text(alert.title),
                message: Text(alert.message),
                dismissButton: .default(Text("OK"))
            )
        }
    }

    private var unitPicker: some View {
        Picker("Shared unit", selection: Binding(
            get: { store.selectedUnitID ?? store.availableUnitIDs.first ?? 0 },
            set: { store.selectedUnitID = $0 }
        )) {
            if store.availableUnitIDs.isEmpty {
                Text("No shared unit").tag(0)
            } else {
                ForEach(store.availableUnitIDs, id: \.self) { unitID in
                    Text("Unit \(unitID)").tag(unitID)
                }
            }
        }
        .frame(minWidth: 115)
        .disabled(store.availableUnitIDs.isEmpty)
        .help("Select a unit ID present in both files")
    }
}

private struct TCComparisonKeyboardMonitor: NSViewRepresentable {
    let store: TCComparisonStore

    func makeCoordinator() -> Coordinator {
        Coordinator(store: store)
    }

    func makeNSView(context: Context) -> NSView {
        context.coordinator.start()
        return NSView(frame: .zero)
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        context.coordinator.store = store
    }

    static func dismantleNSView(_ nsView: NSView, coordinator: Coordinator) {
        coordinator.stop()
    }

    @MainActor
    final class Coordinator {
        var store: TCComparisonStore
        private var monitor: Any?

        init(store: TCComparisonStore) {
            self.store = store
        }

        func start() {
            guard monitor == nil else { return }
            monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
                guard !event.isARepeat,
                      event.window?.isMainWindow == true,
                      event.modifierFlags.intersection([.command, .control, .option]).isEmpty,
                      let key = event.charactersIgnoringModifiers?.lowercased()
                else { return event }

                switch key {
                case "p":
                    self?.store.togglePlotMode()
                case "[":
                    self?.store.stepUnit(-1)
                case "]":
                    self?.store.stepUnit(1)
                default:
                    return event
                }
                return nil
            }
        }

        func stop() {
            if let monitor {
                NSEvent.removeMonitor(monitor)
                self.monitor = nil
            }
        }

        deinit {
            if let monitor {
                NSEvent.removeMonitor(monitor)
            }
        }
    }
}

private struct TCComparisonSidebar: View {
    @Bindable var store: TCComparisonStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                VStack(alignment: .leading, spacing: 4) {
                    Label("TC Comparison", systemImage: "chart.xyaxis.line")
                        .font(.title3.weight(.semibold))
                    Text(store.comparisonStatus)
                        .font(.callout)
                        .foregroundStyle(statusColor)
                        .fixedSize(horizontal: false, vertical: true)
                }

                CurveSourceCard(store: store, slot: .first)
                CurveSourceCard(store: store, slot: .second)

                Button {
                    store.swapCurves()
                } label: {
                    Label("Swap TC I and TC II", systemImage: "arrow.left.arrow.right")
                        .frame(maxWidth: .infinity)
                }
                .disabled(store.loadedCurveCount < 2 || store.isLoadingAnyCurve)

                Divider()

                processingSection

                Divider()

                VStack(alignment: .leading, spacing: 5) {
                    Label("Calibration direction", systemImage: "rotate.right")
                        .font(.headline)
                    Text(
                        "Positive offsets rotate clockwise/right; negative offsets rotate "
                            + "counter-clockwise/left. Zero degrees stays at the top."
                    )
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Text(
                    "Tip: drop files anywhere, or drop onto a specific TC card. "
                        + "Use [ and ] to move between shared units."
                )
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(16)
        }
        .background(.bar)
    }

    private var statusColor: Color {
        store.hasTwoCurves && store.availableUnitIDs.isEmpty ? .orange : .secondary
    }

    private var processingSection: some View {
        VStack(alignment: .leading, spacing: 11) {
            Text("Processing").font(.headline)

            Picker("Display bins", selection: Binding(
                get: { store.displayBins },
                set: { store.displayBins = $0; store.normalizeSettings() }
            )) {
                ForEach(HDTuningData.validDisplayBinCounts, id: \.self) { count in
                    Text("\(count) bins (\(formatDegrees(360 / Double(count))))")
                        .tag(count)
                }
            }

            Toggle("Circular smoothing", isOn: $store.smoothingEnabled)

            if store.smoothingEnabled {
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text("Gaussian σ")
                        Spacer()
                        TextField(
                            "Sigma",
                            value: Binding(
                                get: { store.smoothingSigma },
                                set: { store.smoothingSigma = $0; store.normalizeSettings() }
                            ),
                            format: .number.precision(.fractionLength(1...2))
                        )
                        .multilineTextAlignment(.trailing)
                        .frame(width: 58)
                        Text("bins")
                            .foregroundStyle(.secondary)
                    }
                    Slider(
                        value: Binding(
                            get: { store.smoothingSigma },
                            set: { store.smoothingSigma = $0; store.normalizeSettings() }
                        ),
                        in: TCComparisonStore.smoothingSigmaRange,
                        step: 0.25
                    )
                    Text(
                        "σ uses the original 30-bin (12°) scale · currently ≈ "
                            + formatDegrees(store.smoothingSigma * 12)
                    )
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                .transition(.opacity)
            }

            Text("Counts and occupancy are smoothed separately before rate is calculated.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct CurveSourceCard: View {
    @Bindable var store: TCComparisonStore
    let slot: ComparisonSlot
    @State private var isDropTargeted = false

    private var accent: Color {
        slot == .first ? .blue : .orange
    }

    private var offset: Binding<Double> {
        Binding(
            get: { store.angleOffset(for: slot) },
            set: { store.setAngleOffset($0, for: slot) }
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 11) {
            HStack(spacing: 8) {
                Circle()
                    .fill(accent)
                    .frame(width: 10, height: 10)
                    .accessibilityHidden(true)
                Text(slot.title)
                    .font(.headline)
                Spacer()
                if store.isLoading(slot) {
                    ProgressView().controlSize(.small)
                }
                Button {
                    store.beginImport(into: slot)
                } label: {
                    Image(systemName: "folder")
                }
                .buttonStyle(.borderless)
                .help("Choose \(slot.title) file")
                if store.curve(in: slot) != nil {
                    Button {
                        store.removeCurve(in: slot)
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(.secondary)
                    }
                    .buttonStyle(.borderless)
                    .help("Remove \(slot.title)")
                }
            }

            if let curve = store.curve(in: slot) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(curve.url.lastPathComponent)
                        .font(.callout.weight(.medium))
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Text("\(curve.data.unitIDs.count) units · \(curve.url.deletingLastPathComponent().path)")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                        .truncationMode(.middle)
                        .help(curve.url.path)
                }
            } else if !store.isLoading(slot) {
                Label("Drop .tc or .json here", systemImage: "arrow.down.doc")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 34, alignment: .leading)
            }

            Divider()

            VStack(alignment: .leading, spacing: 7) {
                HStack {
                    Label("Angle offset", systemImage: "rotate.right")
                    Spacer()
                    TextField(
                        "Degrees",
                        value: offset,
                        format: .number.precision(.fractionLength(0...1))
                    )
                    .multilineTextAlignment(.trailing)
                    .frame(width: 56)
                    Text("°")
                        .foregroundStyle(.secondary)
                    Stepper(
                        "Angle offset",
                        value: offset,
                        in: TCComparisonStore.angleOffsetRange,
                        step: 1
                    )
                    .labelsHidden()
                }
                Slider(
                    value: offset,
                    in: TCComparisonStore.angleOffsetRange,
                    step: 1
                )
                .tint(accent)
                HStack {
                    Text("−180° left")
                    Spacer()
                    Text(signedDegrees(store.angleOffset(for: slot)))
                        .fontWeight(.semibold)
                        .foregroundStyle(accent)
                    Spacer()
                    Text("+180° right")
                }
                .font(.caption2)
                .foregroundStyle(.secondary)
            }
            .disabled(store.curve(in: slot) == nil)
        }
        .padding(12)
        .background(
            isDropTargeted ? accent.opacity(0.12) : Color(nsColor: .controlBackgroundColor),
            in: RoundedRectangle(cornerRadius: 12, style: .continuous)
        )
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(
                    isDropTargeted ? accent : Color.secondary.opacity(0.2),
                    lineWidth: isDropTargeted ? 2 : 1
                )
        }
        .contentShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .dropDestination(for: URL.self) { urls, _ in
            store.accept(urls: urls, preferredSlot: slot)
        } isTargeted: { targeted in
            isDropTargeted = targeted
        }
        .animation(.easeOut(duration: 0.12), value: isDropTargeted)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(slot.accessibilityTitle)
    }
}

private struct TCComparisonDetail: View {
    @Bindable var store: TCComparisonStore
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var styledCurves: [StyledComparisonCurve] {
        ComparisonSlot.allCases.compactMap { slot in
            guard let curve = store.processedCurve(for: slot),
                  let source = store.curve(in: slot) else { return nil }
            return StyledComparisonCurve(
                slot: slot,
                sourceName: source.url.lastPathComponent,
                curve: curve,
                color: slot == .first ? .blue : .orange,
                angleOffset: store.angleOffset(for: slot)
            )
        }
    }

    private var sharedRateMaximum: Double {
        comparisonRateMaximum(styledCurves)
    }

    var body: some View {
        VStack(spacing: 0) {
            detailHeader
            Divider()

            if store.loadedCurveCount == 0 {
                emptyState(
                    title: "Compare two tuning curves",
                    systemImage: "chart.xyaxis.line",
                    description: "Drop two .tc/.json files here, or choose Open Two Tuning Curves."
                )
            } else if store.hasTwoCurves && store.availableUnitIDs.isEmpty {
                emptyState(
                    title: "No shared unit ID",
                    systemImage: "person.crop.circle.badge.exclamationmark",
                    description: "Choose two files that contain at least one matching unit ID."
                )
            } else if styledCurves.isEmpty {
                emptyState(
                    title: "Select a unit",
                    systemImage: "scope",
                    description: "Choose a unit ID available in the loaded tuning curves."
                )
            } else {
                charts
            }
        }
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private var detailHeader: some View {
        HStack(alignment: .center, spacing: 16) {
            VStack(alignment: .leading, spacing: 3) {
                Text(store.selectedUnitID.map { "Unit \($0)" } ?? "No unit selected")
                    .font(.title2.weight(.semibold))
                Text(
                    "Shared firing-rate scale · \(store.displayBins) angular bins · "
                        + "\(store.plotMode.label) view"
                )
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Label(
                store.plotMode == .line ? "P: show Polar" : "P: show Line",
                systemImage: "keyboard"
            )
            .font(.callout)
            .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 14)
    }

    private var charts: some View {
        HStack(spacing: 14) {
            curvePanel(slot: .first)
            curvePanel(slot: .second)
        }
        .padding(16)
        .animation(
            reduceMotion ? nil : .easeInOut(duration: 0.18),
            value: store.plotMode
        )
    }

    private func curvePanel(slot: ComparisonSlot) -> some View {
        let styled = styledCurves.first { $0.slot == slot }
        let color: Color = slot == .first ? .blue : .orange

        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Circle()
                    .fill(color)
                    .frame(width: 10, height: 10)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(slot.title).font(.headline)
                    if let styled {
                        Text("\(styled.sourceName) · \(signedDegrees(styled.angleOffset))")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    } else {
                        Text("No tuning curve loaded")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer()
                Label(store.plotMode.label, systemImage: store.plotMode.systemImage)
                    .font(.caption.weight(.medium))
                    .foregroundStyle(.secondary)
            }

            Text(store.plotMode.subtitle)
                .font(.caption)
                .foregroundStyle(.secondary)

            if let styled {
                Group {
                    switch store.plotMode {
                    case .line:
                        TuningLineChart(
                            curves: [styled],
                            rateMaximum: sharedRateMaximum
                        )
                    case .polar:
                        TuningPolarChart(
                            curves: [styled],
                            rateMaximum: sharedRateMaximum
                        )
                    }
                }
                .id(store.plotMode)
                .transition(.opacity)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView {
                    Label("Load \(slot.title)", systemImage: "arrow.down.doc")
                } description: {
                    Text("Drop a .tc/.json file onto the \(slot.title) card in the sidebar.")
                } actions: {
                    Button("Choose \(slot.title)…") { store.beginImport(into: slot) }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(14)
        .background(.background, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(.secondary.opacity(0.18))
        }
    }

    private func emptyState(title: String, systemImage: String, description: String) -> some View {
        ContentUnavailableView {
            Label(title, systemImage: systemImage)
        } description: {
            Text(description)
        } actions: {
            Button("Open Two Tuning Curves…") { store.beginImport() }
                .buttonStyle(.borderedProminent)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

func signedDegrees(_ value: Double) -> String {
    if abs(value) < 0.05 { return "0°" }
    let decimals = abs(value - value.rounded()) < 0.05 ? 0 : 1
    return String(format: "%+.*f°", decimals, value)
}

private func formatDegrees(_ value: Double) -> String {
    let decimals = abs(value - value.rounded()) < 0.05 ? 0 : 1
    return String(format: "%.*f°", decimals, value)
}
