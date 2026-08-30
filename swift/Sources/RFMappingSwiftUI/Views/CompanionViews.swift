import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct ProbeSidebarSection: View {
    @Bindable var store: RFMappingStore
    @State private var dragStart: CGPoint?
    @State private var dragCurrent: CGPoint?

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                Text("Probe").font(.headline)
                Spacer()
                Button("Clear") { store.setProbeFilteredUnitIDs(nil) }
                    .controlSize(.small)
                    .disabled(store.probeFilteredUnitIDs == nil)
            }
            if let geometry = store.probeGeometry {
                GeometryReader { proxy in
                    let transform = ProbeDisplayTransform(
                        geometry: geometry,
                        unitIDs: Set(store.qualityFilteredUnitIDs),
                        size: proxy.size
                    )
                    Canvas { context, _ in
                        drawProbe(
                            context: &context,
                            geometry: geometry,
                            transform: transform
                        )
                    }
                    .contentShape(Rectangle())
                    .gesture(
                        DragGesture(minimumDistance: 3)
                            .onChanged { value in
                                if dragStart == nil { dragStart = value.startLocation }
                                dragCurrent = value.location
                            }
                            .onEnded { value in
                                applyProbeSelection(
                                    from: value.startLocation,
                                    to: value.location,
                                    geometry: geometry,
                                    transform: transform
                                )
                                dragStart = nil
                                dragCurrent = nil
                            }
                    )
                    .simultaneousGesture(
                        SpatialTapGesture().onEnded { value in
                            selectNearestProbeUnit(
                                at: value.location,
                                geometry: geometry,
                                transform: transform
                            )
                        }
                    )
                }
                .frame(height: 225)
                .background(Color(nsColor: .textBackgroundColor))
                .overlay(RoundedRectangle(cornerRadius: 5).stroke(.secondary.opacity(0.45)))

                Text(probeStatus(geometry))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text(store.probeGeometryError
                    ?? "No companion positions.probe or positions.csv was discovered.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Button("Choose Probe Positions…", action: chooseProbePositions)
                    .controlSize(.small)
            }
        }
    }

    private func drawProbe(
        context: inout GraphicsContext,
        geometry: ProbeGeometry,
        transform: ProbeDisplayTransform
    ) {
        for channel in geometry.channels {
            let point = transform.point(
                x: channel.xMicrometers,
                y: channel.yMicrometers
            )
            context.fill(
                Path(ellipseIn: CGRect(x: point.x - 1.8, y: point.y - 1.8, width: 3.6, height: 3.6)),
                with: .color(Color(red: 0.58, green: 0.64, blue: 0.72).opacity(0.75))
            )
        }
        let qualityVisibleIDs = Set(store.qualityFilteredUnitIDs)
        for unit in geometry.positionedUnits where qualityVisibleIDs.contains(unit.unitID) {
            guard let position = unit.position else { continue }
            let point = transform.point(x: position.x, y: position.y)
            let selected = unit.unitID == store.selectedUnitID
            let included = store.probeFilteredUnitIDs?.contains(unit.unitID) ?? true
            let radius: CGFloat = selected ? 5.5 : 3.7
            let circle = Path(ellipseIn: CGRect(
                x: point.x - radius,
                y: point.y - radius,
                width: radius * 2,
                height: radius * 2
            ))
            context.fill(
                circle,
                with: .color(
                    (selected ? Color.red : Color.accentColor)
                        .opacity(included ? 0.95 : 0.2)
                )
            )
            if selected { context.stroke(circle, with: .color(.white), lineWidth: 1) }
        }
        if let dragStart, let dragCurrent {
            let rect = normalizedRect(from: dragStart, to: dragCurrent)
            context.fill(Path(rect), with: .color(Color.accentColor.opacity(0.12)))
            context.stroke(Path(rect), with: .color(Color.accentColor), lineWidth: 1)
        }
    }

    private func applyProbeSelection(
        from start: CGPoint,
        to end: CGPoint,
        geometry: ProbeGeometry,
        transform: ProbeDisplayTransform
    ) {
        let rect = normalizedRect(from: start, to: end)
        guard rect.width >= 3 || rect.height >= 3 else { return }
        let qualityVisibleIDs = Set(store.qualityFilteredUnitIDs)
        let selected = Set(geometry.units.compactMap { unit in
            guard qualityVisibleIDs.contains(unit.unitID) else { return nil }
            guard let position = unit.position else { return nil }
            return rect.contains(transform.point(x: position.x, y: position.y))
                ? unit.unitID
                : nil
        })
        store.setProbeFilteredUnitIDs(selected)
    }

    private func selectNearestProbeUnit(
        at location: CGPoint,
        geometry: ProbeGeometry,
        transform: ProbeDisplayTransform
    ) {
        let qualityVisibleIDs = Set(store.qualityFilteredUnitIDs)
        let candidates = geometry.positionedUnits.compactMap { unit in
            guard qualityVisibleIDs.contains(unit.unitID) else { return nil }
            return unit.position.map {
                (unit: unit, point: transform.point(x: $0.x, y: $0.y))
            }
        }
        guard let nearest = candidates.min(by: {
            distance($0.point, location) < distance($1.point, location)
        }), distance(nearest.point, location) <= 14 else { return }
        store.selectUnitID(nearest.unit.unitID)
    }

    private func probeStatus(_ geometry: ProbeGeometry) -> String {
        let qualityVisibleIDs = Set(store.qualityFilteredUnitIDs)
        let qualityUnits = geometry.units.filter { qualityVisibleIDs.contains($0.unitID) }
        let positionedCount = qualityUnits.filter { $0.position != nil }.count
        let filteredCount = store.probeFilteredUnitIDs.map {
            $0.intersection(qualityVisibleIDs).count
        }
        var suffix = filteredCount.map { " · \($0) selected" } ?? ""
        if let selectedUnitID = store.selectedUnitID,
           let selected = geometry.units.first(where: { $0.unitID == selectedUnitID }),
           selected.position == nil {
            suffix += " · unit \(selectedUnitID) position is nan,nan"
        }
        return "\(positionedCount)/\(qualityUnits.count) visible RF units positioned"
            + " · drag to filter, click to select\(suffix)"
    }

    private func chooseProbePositions() {
        let panel = NSOpenPanel()
        panel.title = "Choose probe positions (.probe or CSV)"
        panel.prompt = "Load"
        panel.allowedContentTypes = UTType.rfProbeReadableTypes
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.directoryURL = store.data?.url.deletingLastPathComponent()
        if panel.runModal() == .OK, let url = panel.url {
            store.setProbePositionsURL(url)
        }
    }
}

struct WaveformSidebarSection: View {
    @Bindable var store: RFMappingStore

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                Text("Local average waveform").font(.headline)
                Spacer()
                Toggle("Show", isOn: Binding(
                    get: { store.showWaveform },
                    set: store.setShowWaveform
                ))
                .labelsHidden()
                .toggleStyle(.switch)
                .controlSize(.mini)
            }
            if store.showWaveform {
                Picker("Channels", selection: Binding(
                    get: { store.waveformChannelMode },
                    set: store.setWaveformChannelMode
                )) {
                    ForEach(WaveformChannelMode.allCases) { mode in
                        Text(mode.label).tag(mode)
                    }
                }
                .controlSize(.small)

                if let payload = store.waveformPayload {
                    WaveformHeatmapCanvas(
                        payload: payload,
                        amplitudeLimitMicrovolts: payload.amplitudeLimitMicrovolts,
                        compact: true
                    )
                    .frame(height: 150)
                    .contentShape(Rectangle())
                    .onTapGesture(count: 2) { store.isWaveformZoomed = true }
                    .help("Double-click to enlarge")
                    Text(waveformSummary(payload))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Text(store.waveformError ?? "Waveform unavailable for the selected unit.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private func waveformSummary(_ payload: WaveformPayload) -> String {
        "\(payload.mode.label) · best + \(max(0, payload.channels.count - 1)) nearest · "
            + "max PTP \(String(format: "%.3g", payload.summary.maxPeakToPeakMicrovolts)) µV\n"
            + "best ch \(payload.summary.bestChannelID) · "
            + "\(payload.summary.selectedSpikeCount)/\(payload.summary.totalSpikeCount) spikes · "
            + "±\(String(format: "%.1f", payload.amplitudeLimitMicrovolts)) µV"
    }
}

struct WaveformZoomOverlay: View {
    @Bindable var store: RFMappingStore

    var body: some View {
        ZStack {
            Rectangle().fill(.ultraThickMaterial)
            VStack(spacing: 12) {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Local average waveform").font(.title2.weight(.semibold))
                        if let payload = store.waveformPayload {
                            Text("Unit ID \(payload.summary.unitID) · \(payload.mode.label)")
                                .foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                    Button("Done") { store.isWaveformZoomed = false }
                        .keyboardShortcut(.cancelAction)
                }
                if let payload = store.waveformPayload {
                    WaveformHeatmapCanvas(
                        payload: payload,
                        amplitudeLimitMicrovolts: payload.amplitudeLimitMicrovolts,
                        compact: false
                    )
                    .contentShape(Rectangle())
                    .onTapGesture(count: 2) { store.isWaveformZoomed = false }
                } else {
                    ContentUnavailableView(
                        "Waveform unavailable",
                        systemImage: "waveform.slash",
                        description: Text(store.waveformError ?? "No payload is loaded.")
                    )
                }
                Text("Double-click the waveform or press Esc to return")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(28)
        }
    }
}

struct WaveformHeatmapCanvas: View {
    let payload: WaveformPayload
    let amplitudeLimitMicrovolts: Double
    let compact: Bool

    var body: some View {
        Canvas { context, size in
            let left: CGFloat = compact ? 48 : 76
            let bottom: CGFloat = compact ? 22 : 42
            let top: CGFloat = compact ? 8 : 22
            let right: CGFloat = compact ? 52 : 76
            let plotRect = CGRect(
                x: left,
                y: top,
                width: max(10, size.width - left - right),
                height: max(10, size.height - top - bottom)
            )
            guard !payload.valuesMicrovolts.isEmpty,
                  !payload.timesMilliseconds.isEmpty else { return }
            let rows = payload.valuesMicrovolts.count
            let columns = payload.timesMilliseconds.count
            let rowHeight = plotRect.height / CGFloat(rows)
            let columnWidth = plotRect.width / CGFloat(columns)
            let limit = max(amplitudeLimitMicrovolts, Double.leastNonzeroMagnitude)
            for row in payload.valuesMicrovolts.indices {
                for column in payload.valuesMicrovolts[row].indices {
                    let rect = CGRect(
                        x: plotRect.minX + CGFloat(column) * columnWidth,
                        y: plotRect.minY + CGFloat(row) * rowHeight,
                        width: columnWidth + 0.5,
                        height: rowHeight + 0.5
                    )
                    context.fill(
                        Path(rect),
                        with: .color(waveformDivergingColor(
                            payload.valuesMicrovolts[row][column] / limit
                        ))
                    )
                }
            }
            context.stroke(Path(plotRect), with: .color(.secondary.opacity(0.6)), lineWidth: 1)
            let bestRect = CGRect(
                x: plotRect.minX,
                y: plotRect.minY + CGFloat(payload.bestChannelRow) * rowHeight,
                width: plotRect.width,
                height: rowHeight
            )
            context.stroke(Path(bestRect), with: .color(.red), lineWidth: compact ? 1.2 : 2)
            for row in payload.channels.indices {
                context.draw(
                    Text((row == payload.bestChannelRow ? "★ " : "")
                        + "ch \(payload.channels[row].channelID)")
                        .font(.system(size: compact ? 7 : 10, weight: row == payload.bestChannelRow ? .bold : .regular))
                        .foregroundStyle(row == payload.bestChannelRow ? .red : .secondary),
                    at: CGPoint(
                        x: plotRect.minX - 5,
                        y: plotRect.minY + (CGFloat(row) + 0.5) * rowHeight
                    ),
                    anchor: .trailing
                )
            }
            let low = payload.timeEdgesMilliseconds.first
                ?? payload.timesMilliseconds.first ?? 0
            let high = payload.timeEdgesMilliseconds.last
                ?? payload.timesMilliseconds.last ?? 0
            let tickValues = [
                payload.timesMilliseconds.first ?? low,
                0.0,
                payload.timesMilliseconds.last ?? high,
            ].filter { low <= $0 && $0 <= high }
            for value in tickValues {
                let fraction = (value - low) / max(high - low, 1e-12)
                context.draw(
                    Text(String(format: compact ? "%.1f" : "%.2f ms", value))
                        .font(.system(size: compact ? 7 : 10))
                        .foregroundStyle(.secondary),
                    at: CGPoint(
                        x: plotRect.minX + plotRect.width * CGFloat(fraction),
                        y: plotRect.maxY + (compact ? 10 : 17)
                    ),
                    anchor: .center
                )
            }
            let zeroFraction = (0 - low) / max(high - low, 1e-12)
            if (0.0...1.0).contains(zeroFraction) {
                let x = plotRect.minX + plotRect.width * CGFloat(zeroFraction)
                var path = Path()
                path.move(to: CGPoint(x: x, y: plotRect.minY))
                path.addLine(to: CGPoint(x: x, y: plotRect.maxY))
                context.stroke(path, with: .color(.black.opacity(0.45)), lineWidth: 0.8)
            }
            let barX = plotRect.maxX + (compact ? 8 : 16)
            let barWidth: CGFloat = compact ? 8 : 13
            let steps = 64
            for step in 0..<steps {
                let fraction = Double(step) / Double(steps - 1)
                let rect = CGRect(
                    x: barX,
                    y: plotRect.minY + CGFloat(step) * plotRect.height / CGFloat(steps),
                    width: barWidth,
                    height: plotRect.height / CGFloat(steps) + 0.5
                )
                context.fill(
                    Path(rect),
                    with: .color(waveformDivergingColor(1 - 2 * fraction))
                )
            }
            context.stroke(
                Path(CGRect(x: barX, y: plotRect.minY, width: barWidth, height: plotRect.height)),
                with: .color(.secondary),
                lineWidth: 0.8
            )
            let legendFont: CGFloat = compact ? 6.5 : 9
            context.draw(
                Text("µV").font(.system(size: legendFont)).foregroundStyle(.secondary),
                at: CGPoint(x: barX, y: plotRect.minY - (compact ? 3 : 8)),
                anchor: .bottomLeading
            )
            for (value, y) in [
                (limit, plotRect.minY),
                (0.0, plotRect.midY),
                (-limit, plotRect.maxY),
            ] {
                context.draw(
                    Text(String(format: "%.2g", value))
                        .font(.system(size: legendFont))
                        .foregroundStyle(.secondary),
                    at: CGPoint(x: barX + barWidth + 3, y: y),
                    anchor: .leading
                )
            }
        }
        .background(Color(nsColor: .textBackgroundColor))
        .overlay(RoundedRectangle(cornerRadius: 5).stroke(.secondary.opacity(0.4)))
    }
}

struct HDTuningCompanionView: View {
    @Bindable var store: RFMappingStore

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("HD tuning").font(.headline)
                    Text("Exact session \(store.tuningSessionIndex)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Choose…", action: chooseHDTuning)
                    .controlSize(.small)
            }
            .padding(10)
            Divider()
            if let curve = processedCurve {
                GeometryReader { _ in
                    Canvas { context, size in
                        drawHDTuning(context: &context, size: size, curve: curve)
                    }
                }
                Text(store.hdTuningURL?.path ?? "")
                    .font(.system(size: 8, design: .monospaced))
                    .foregroundStyle(.tertiary)
                    .lineLimit(2)
                    .textSelection(.enabled)
                    .padding([.horizontal, .bottom], 8)
            } else {
                ContentUnavailableView {
                    Label("HD tuning unavailable", systemImage: "chart.xyaxis.line")
                } description: {
                    Text(store.hdTuningError ?? "The selected RF unit is absent from the tuning curve.")
                } actions: {
                    Button("Choose Tuning Curve…", action: chooseHDTuning)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .background(Color(nsColor: .controlBackgroundColor))
    }

    private var processedCurve: ProcessedHDCurve? {
        guard let unitID = store.selectedUnitID,
              let hdTuning = store.hdTuning else { return nil }
        return try? hdTuning.processedCurve(unitID: unitID)
    }

    private func drawHDTuning(
        context: inout GraphicsContext,
        size: CGSize,
        curve: ProcessedHDCurve
    ) {
        let lineRect = CGRect(x: 42, y: 34, width: max(10, size.width - 64), height: max(10, size.height * 0.42 - 48))
        let polarCenter = CGPoint(x: size.width / 2, y: size.height * 0.72)
        let polarRadius = max(10, min(size.width * 0.34, size.height * 0.2))
        let high = max(curve.ratesHz.max() ?? 0, 1e-12)
        context.draw(
            Text("Unit ID \(store.selectedUnitID ?? 0) · max \(String(format: "%.2f", high)) Hz")
                .font(.system(size: 10, weight: .semibold)),
            at: CGPoint(x: 12, y: 14),
            anchor: .leading
        )
        context.stroke(Path(lineRect), with: .color(.secondary.opacity(0.5)), lineWidth: 1)
        var line = Path()
        for index in curve.ratesHz.indices {
            let point = CGPoint(
                x: lineRect.minX + lineRect.width * CGFloat(index) / CGFloat(max(1, curve.ratesHz.count - 1)),
                y: lineRect.maxY - lineRect.height * CGFloat(curve.ratesHz[index] / high)
            )
            if index == 0 {
                line.move(to: point)
            } else {
                line.addLine(to: point)
            }
        }
        context.stroke(line, with: .color(.blue), lineWidth: 2)
        for fraction in [0.25, 0.5, 0.75, 1.0] {
            let radius = polarRadius * CGFloat(fraction)
            context.stroke(
                Path(ellipseIn: CGRect(
                    x: polarCenter.x - radius,
                    y: polarCenter.y - radius,
                    width: radius * 2,
                    height: radius * 2
                )),
                with: .color(.secondary.opacity(0.22)),
                lineWidth: 1
            )
        }
        var polar = Path()
        for index in 0...curve.ratesHz.count {
            let source = index % curve.ratesHz.count
            let angle = curve.anglesDegrees[source] * .pi / 180 - .pi / 2
            let radius = polarRadius * CGFloat(curve.ratesHz[source] / high)
            let point = CGPoint(
                x: polarCenter.x + radius * CGFloat(cos(angle)),
                y: polarCenter.y + radius * CGFloat(sin(angle))
            )
            if index == 0 {
                polar.move(to: point)
            } else {
                polar.addLine(to: point)
            }
        }
        context.stroke(polar, with: .color(.blue), lineWidth: 2)
    }

    private func chooseHDTuning() {
        let panel = NSOpenPanel()
        panel.title = "Choose tuning curve (.tc or JSON)"
        panel.prompt = "Load"
        panel.allowedContentTypes = UTType.rfTuningCurveReadableTypes
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.directoryURL = store.hdTuningURL?.deletingLastPathComponent()
            ?? store.data?.url.deletingLastPathComponent()
        if panel.runModal() == .OK, let url = panel.url {
            store.setHDTuningURL(url)
        }
    }
}

private struct ProbeDisplayTransform {
    let plotRect: CGRect
    let xRange: ClosedRange<Double>
    let yRange: ClosedRange<Double>

    init(geometry: ProbeGeometry, unitIDs: Set<Int>, size: CGSize) {
        plotRect = CGRect(x: 12, y: 10, width: max(10, size.width - 24), height: max(10, size.height - 20))
        let xValues = geometry.channels.map(\.xMicrometers)
            + geometry.units.compactMap {
                unitIDs.contains($0.unitID) ? $0.xMicrometers : nil
            }
        let yValues = geometry.channels.map(\.yMicrometers)
            + geometry.units.compactMap {
                unitIDs.contains($0.unitID) ? $0.yMicrometers : nil
            }
        xRange = Self.padded(low: xValues.min() ?? 0, high: xValues.max() ?? 1)
        yRange = Self.padded(low: yValues.min() ?? 0, high: yValues.max() ?? 1)
    }

    func point(x: Double, y: Double) -> CGPoint {
        let xFraction = (x - xRange.lowerBound) / max(xRange.upperBound - xRange.lowerBound, 1e-12)
        let yFraction = (y - yRange.lowerBound) / max(yRange.upperBound - yRange.lowerBound, 1e-12)
        return CGPoint(
            x: plotRect.minX + plotRect.width * CGFloat(xFraction),
            y: plotRect.maxY - plotRect.height * CGFloat(yFraction)
        )
    }

    private static func padded(low: Double, high: Double) -> ClosedRange<Double> {
        let span = high - low
        let padding = span > 1e-12 ? span * 0.06 : max(abs(low) * 0.06, 1)
        return (low - padding)...(high + padding)
    }
}

private func normalizedRect(from start: CGPoint, to end: CGPoint) -> CGRect {
    CGRect(
        x: min(start.x, end.x),
        y: min(start.y, end.y),
        width: abs(end.x - start.x),
        height: abs(end.y - start.y)
    )
}

private func distance(_ left: CGPoint, _ right: CGPoint) -> CGFloat {
    hypot(left.x - right.x, left.y - right.y)
}
