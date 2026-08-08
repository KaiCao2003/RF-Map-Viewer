import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct FigureExportComposerView: View {
    @Bindable var workspace: FigureExportWorkspace

    var body: some View {
        HSplitView {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    outputSection
                    Divider()
                    unitSection
                    Divider()
                    pageSection
                    Divider()
                    companionSection
                    Divider()
                    validationSection
                }
                .padding(16)
            }
            .frame(minWidth: 390, idealWidth: 430, maxWidth: 520)

            previewSection
                .frame(minWidth: 620, maxWidth: .infinity, maxHeight: .infinity)
        }
        .navigationTitle("Figure Export Composer")
        .alert(
            "Figure Export",
            isPresented: Binding(
                get: { workspace.errorMessage != nil },
                set: { if !$0 { workspace.errorMessage = nil } }
            )
        ) {
            Button("OK") { workspace.errorMessage = nil }
        } message: {
            Text(workspace.errorMessage ?? "")
        }
        .onDisappear {
            workspace.cancelExport()
        }
    }

    private var outputSection: some View {
        VStack(alignment: .leading, spacing: 9) {
            Text("Output").font(.headline)
            Picker("Figure type", selection: $workspace.format) {
                ForEach(FigureExportFormat.allCases) { format in
                    Text(format.label).tag(format)
                }
            }
            Picker("Page size", selection: $workspace.pageSize) {
                ForEach(FigurePageSizePreset.allCases) { preset in
                    Text(preset.label).tag(preset)
                }
            }
            LabeledContent("Base name") {
                TextField("rfmapping-figures", text: $workspace.baseName)
                    .frame(minWidth: 190)
            }
            if workspace.format != .pdf {
                LabeledContent("Raster scale") {
                    Picker("Raster scale", selection: $workspace.outputScale) {
                        Text("1×").tag(CGFloat(1))
                        Text("2×").tag(CGFloat(2))
                        Text("3×").tag(CGFloat(3))
                    }
                    .labelsHidden()
                }
            }
            if workspace.format == .svg {
                Text("SVG preserves preview parity by embedding the renderer-identical PNG; plot marks are not editable vector paths.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Destination").font(.caption).foregroundStyle(.secondary)
                    Text(workspace.destinationDirectory?.path ?? "Not selected")
                        .font(.system(size: 10, design: .monospaced))
                        .lineLimit(2)
                        .textSelection(.enabled)
                }
                Spacer()
                Button("Choose…", action: chooseDestination)
            }
            Toggle("Overwrite existing output", isOn: $workspace.overwriteExisting)
                .help("Off by default. Existing files or directories are never replaced unless enabled.")
            HStack {
                Button {
                    workspace.export()
                } label: {
                    if workspace.isExporting {
                        ProgressView().controlSize(.small)
                    } else {
                        Label("Export Figures", systemImage: "square.and.arrow.up")
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(!workspace.validationIssues.isEmpty || workspace.isExporting)
                if workspace.isExporting {
                    Button("Cancel", role: .cancel) {
                        workspace.cancelExport()
                    }
                }
                if let output = workspace.configuration.outputURL {
                    Text(output.lastPathComponent)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            if workspace.isExporting, workspace.totalPageCount > 0 {
                ProgressView(
                    value: Double(workspace.completedPageCount),
                    total: Double(workspace.totalPageCount)
                ) {
                    Text(
                        "Rendering page \(min(workspace.completedPageCount + 1, workspace.totalPageCount)) of \(workspace.totalPageCount)"
                    )
                    .font(.caption)
                }
            }
            if let success = workspace.successMessage {
                Text(success)
                    .font(.caption)
                    .foregroundStyle(.green)
                    .textSelection(.enabled)
            }
        }
    }

    private var unitSection: some View {
        VStack(alignment: .leading, spacing: 9) {
            Text("Units").font(.headline)
            Picker("Unit selection", selection: Binding(
                get: { workspace.unitSelection.mode },
                set: workspace.setUnitSelectionMode
            )) {
                ForEach(FigureUnitSelectionMode.allCases) { mode in
                    Text(mode.label).tag(mode)
                }
            }
            .pickerStyle(.segmented)
            Text("\(workspace.resolvedUnitIDs.count) unit(s); output order follows original unitPool order")
                .font(.caption)
                .foregroundStyle(.secondary)
            if workspace.unitSelection.mode == .current,
               workspace.resolvedUnitIDs.isEmpty {
                Label(
                    "Current unit ID \(workspace.seed.currentUnitID) is N/A in this file. Choose All or Custom.",
                    systemImage: "waveform.slash"
                )
                .font(.caption)
                .foregroundStyle(.orange)
            }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 3) {
                    ForEach(Array(workspace.seed.data.unitPool.enumerated()), id: \.offset) { index, unitID in
                        Toggle(isOn: Binding(
                            get: { selectedForDisplay(unitID) },
                            set: { selected in
                                let currentlySelected = workspace.unitSelection.customUnitIDs.contains(unitID)
                                if workspace.unitSelection.mode == .custom,
                                   selected != currentlySelected {
                                    workspace.toggleCustomUnit(unitID)
                                }
                            }
                        )) {
                            Text("index \(String(format: "%03d", index))  ·  unit ID \(unitID)")
                                .font(.system(size: 11, design: .monospaced))
                        }
                        .disabled(workspace.unitSelection.mode != .custom)
                    }
                }
            }
            .frame(height: 145)
            .padding(6)
            .background(Color(nsColor: .controlBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 5))
        }
    }

    private var pageSection: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack {
                Text("Per-unit pages").font(.headline)
                Spacer()
                Button(action: workspace.addPage) { Image(systemName: "plus") }
                    .help("Add page")
                Button(action: workspace.removeSelectedPage) { Image(systemName: "minus") }
                    .help("Remove selected page")
                    .disabled(workspace.selectedPageIndex == nil || workspace.pages.count <= 1)
                Button { workspace.moveSelectedPage(-1) } label: { Image(systemName: "arrow.up") }
                    .help("Move page earlier")
                    .disabled((workspace.selectedPageIndex ?? 0) <= 0)
                Button { workspace.moveSelectedPage(1) } label: { Image(systemName: "arrow.down") }
                    .help("Move page later")
                    .disabled((workspace.selectedPageIndex ?? -1) >= workspace.pages.count - 1)
            }
            Picker("Page", selection: $workspace.selectedPageID) {
                Text("No page").tag(Optional<UUID>.none)
                ForEach(Array(workspace.pages.enumerated()), id: \.element.id) { index, page in
                    Text("\(index + 1). \(page.name)").tag(Optional(page.id))
                }
            }
            if let pageIndex = workspace.selectedPageIndex {
                LabeledContent("Page name") {
                    TextField("Page name", text: pageNameBinding(pageIndex))
                }
                VStack(spacing: 5) {
                    ForEach(Array(workspace.pages[pageIndex].plots.enumerated()), id: \.element.id) { index, placement in
                        HStack {
                            Image(systemName: "chart.xyaxis.line")
                                .foregroundStyle(.secondary)
                            Text(placement.kind.label)
                            Spacer()
                            Button { workspace.movePlot(placement.id, offset: -1) } label: {
                                Image(systemName: "arrow.up")
                            }
                            .buttonStyle(.plain)
                            .disabled(index == 0)
                            Button { workspace.movePlot(placement.id, offset: 1) } label: {
                                Image(systemName: "arrow.down")
                            }
                            .buttonStyle(.plain)
                            .disabled(index == workspace.pages[pageIndex].plots.count - 1)
                            Button(role: .destructive) { workspace.removePlot(placement.id) } label: {
                                Image(systemName: "trash")
                            }
                            .buttonStyle(.plain)
                        }
                        .padding(.horizontal, 7)
                        .padding(.vertical, 5)
                        .background(Color(nsColor: .controlBackgroundColor))
                        .clipShape(RoundedRectangle(cornerRadius: 4))
                    }
                }
                Menu {
                    ForEach(FigureExportPlotKind.allCases) { kind in
                        Button(kind.label) { workspace.addPlot(kind) }
                    }
                } label: {
                    Label("Add plot to page", systemImage: "plus.square.on.square")
                }
            }
        }
    }

    private var companionSection: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text("Companion views").font(.headline)
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(workspace.companions.hdTuning == nil ? "HD tuning unavailable" : "HD tuning ready")
                        .font(.callout.weight(.semibold))
                    Text(workspace.hdTuningURL?.path
                        ?? workspace.companions.hdError
                        ?? "No tuning_curves.json selected")
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .lineLimit(3)
                        .textSelection(.enabled)
                }
                Spacer()
                Button("Choose HD JSON…", action: chooseHDTuning)
            }
            VStack(alignment: .leading, spacing: 2) {
                if let geometry = workspace.companions.probeGeometry {
                    Label("\(geometry.probeName) geometry ready", systemImage: "checkmark.circle.fill")
                        .font(.callout.weight(.semibold))
                        .foregroundStyle(.green)
                    Text(geometry.positionsURL.path)
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .lineLimit(3)
                        .textSelection(.enabled)
                    Text("\(geometry.channels.count) channels · \(geometry.units.count) RF units")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Label(workspace.companions.probeUnavailableReason, systemImage: "info.circle")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var validationSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            if workspace.validationIssues.isEmpty {
                Label("Ready to export", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
            } else {
                Text("Before exporting").font(.headline)
                ForEach(Array(workspace.validationIssues.enumerated()), id: \.offset) { _, issue in
                    Label(issue.message, systemImage: "exclamationmark.circle")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
            }
        }
    }

    private var previewSection: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Live preview").font(.headline)
                Spacer()
                Picker("Preview unit", selection: $workspace.previewUnitID) {
                    ForEach(workspace.resolvedUnitIDs, id: \.self) { unitID in
                        let index = workspace.seed.data.unitIndex(forUnitID: unitID) ?? -1
                        Text("index \(String(format: "%03d", index)) / unit ID \(unitID)").tag(unitID)
                    }
                }
                .frame(width: 210)
                .disabled(workspace.resolvedUnitIDs.isEmpty)
            }
            .padding(12)
            .background(.bar)
            Divider()
            GeometryReader { proxy in
                if let descriptor = workspace.previewDescriptor {
                    let pageSize = workspace.pageSize.size
                    let scale = min(
                        max(0.05, (proxy.size.width - 32) / pageSize.width),
                        max(0.05, (proxy.size.height - 32) / pageSize.height)
                    )
                    FigureRenderedPageView(
                        descriptor: descriptor,
                        data: workspace.seed.data
                    )
                    .frame(width: pageSize.width, height: pageSize.height)
                    .scaleEffect(scale)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .shadow(color: .black.opacity(0.22), radius: 8, y: 3)
                } else {
                    ContentUnavailableView {
                        Label("Preview unavailable", systemImage: "doc.richtext")
                    } description: {
                        Text("Select at least one unit and one page.")
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .background(Color(nsColor: .windowBackgroundColor))
        }
    }

    private func selectedForDisplay(_ unitID: Int) -> Bool {
        switch workspace.unitSelection.mode {
        case .current:
            unitID == workspace.seed.currentUnitID
        case .all:
            true
        case .custom:
            workspace.unitSelection.customUnitIDs.contains(unitID)
        }
    }

    private func pageNameBinding(_ index: Int) -> Binding<String> {
        Binding(
            get: { workspace.pages.indices.contains(index) ? workspace.pages[index].name : "" },
            set: { value in
                guard workspace.pages.indices.contains(index) else { return }
                workspace.pages[index].name = value
            }
        )
    }

    private func chooseDestination() {
        let panel = NSOpenPanel()
        panel.title = "Choose Figure Export Destination"
        panel.prompt = "Choose"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = true
        panel.directoryURL = workspace.destinationDirectory
            ?? workspace.seed.data.url.deletingLastPathComponent()
        if panel.runModal() == .OK, let url = panel.url {
            workspace.destinationDirectory = url.standardizedFileURL
        }
    }

    private func chooseHDTuning() {
        let panel = NSOpenPanel()
        panel.title = "Choose tuning_curves.json"
        panel.prompt = "Load"
        panel.allowedContentTypes = [.json, .data]
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.directoryURL = workspace.hdTuningURL?.deletingLastPathComponent()
            ?? workspace.seed.data.url.deletingLastPathComponent()
        if panel.runModal() == .OK, let url = panel.url {
            workspace.setHDTuningURL(url)
        }
    }
}
