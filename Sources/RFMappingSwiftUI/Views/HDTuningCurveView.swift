import SwiftUI

struct HDTuningCurveView: View {
    @Bindable var store: RFMappingStore
    @Bindable var preferences: ViewerPreferences
    let collapse: () -> Void

    @State private var processedResult: RequestBoundValue<CurrentCurveRequest, ProcessedTuningCurve>?
    @State private var sharedScaleResult: RequestBoundValue<SharedScaleRequest, Double>?
    @State private var curveErrorResult: RequestBoundValue<CurrentCurveRequest, String>?
    @State private var sharedScaleErrorResult: RequestBoundValue<SharedScaleRequest, String>?
    @State private var processingRequest: CurrentCurveRequest?
    @State private var sharedScaleProcessingRequest: SharedScaleRequest?
    @State private var showsProvenance = false

    var body: some View {
        let curveRequest = currentRequest
        let scaleRequest = sharedScaleRequest
        VStack(spacing: 0) {
            header
            Divider()

            Group {
                if store.isLoadingTuning {
                    ProgressView("Preparing HD tuning curve…")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let data = store.tuningData,
                          let clusterID = store.selectedClusterID {
                    if data.unit(for: clusterID) == nil {
                        unavailable(
                            title: "No tuning curve for cluster \(clusterID)",
                            detail: "The attached file does not contain this RF unit.",
                            symbol: "waveform.slash"
                        )
                    } else if let error = currentCurveError {
                        processingFailure(
                            title: "Could not prepare the tuning curve",
                            detail: error
                        )
                    } else if let processedCurve = currentProcessedCurve {
                        if preferences.tuningCompareScale {
                            if let error = currentSharedScaleError {
                                processingFailure(
                                    title: "Could not prepare the shared scale",
                                    detail: error
                                )
                            } else if let scaleHigh = hdTuningDisplayScaleHigh(
                                curvePeakHz: processedCurve.peakHz,
                                compareScale: true,
                                sharedScaleHigh: currentSharedScaleHigh
                            ) {
                                plot(
                                    curve: processedCurve,
                                    clusterID: clusterID,
                                    scaleHigh: scaleHigh
                                )
                            } else {
                                ProgressView("Preparing shared comparison scale…")
                                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                            }
                        } else {
                            plot(
                                curve: processedCurve,
                                clusterID: clusterID,
                                scaleHigh: processedCurve.peakHz
                            )
                        }
                    } else {
                        ProgressView("Preparing HD tuning curve…")
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                } else {
                    unavailable(
                        title: store.tuningErrorMessage == nil
                            ? "No tuning curves attached"
                            : "Could not load tuning curves",
                        detail: store.tuningErrorMessage
                            ?? "Attach tuning_curves.json to compare RF and head direction.",
                        symbol: store.tuningErrorMessage == nil
                            ? "location.north.circle"
                            : "exclamationmark.triangle"
                    )
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            Divider()
            status
        }
        .background(Color(nsColor: .controlBackgroundColor))
        .task(id: curveRequest) {
            await updateProcessedCurve(for: curveRequest)
        }
        .task(id: scaleRequest) {
            await updateSharedScale(for: scaleRequest)
        }
    }

    private var header: some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                Text("HD Tuning Curve")
                    .font(.headline)
                if let clusterID = store.selectedClusterID {
                    Text("Cluster \(clusterID)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Spacer(minLength: 8)

            if store.tuningData?.metadata != nil {
                Button {
                    showsProvenance.toggle()
                } label: {
                    Image(systemName: "info.circle")
                }
                .buttonStyle(.plain)
                .help("Show timing, angle, and classification provenance")
                .accessibilityLabel("Show tuning curve provenance")
                .popover(isPresented: $showsProvenance, arrowEdge: .top) {
                    TuningProvenanceView(data: store.tuningData)
                }
            }

            hdClassBadge

            Button(action: collapse) {
                Image(systemName: preferences.tuningLayout == .stacked
                    ? "rectangle.bottomthird.inset.filled"
                    : "rectangle.rightthird.inset.filled")
            }
            .buttonStyle(.plain)
            .help("Collapse HD tuning curve")
            .accessibilityLabel("Collapse HD tuning curve")

            Menu {
                Button("Attach Tuning Curves…") {
                    store.isImportingTuning = true
                }
                if store.tuningData != nil {
                    Button("Detach Tuning Curves", role: .destructive) {
                        store.clearTuningCurve()
                    }
                }
                Divider()
                SettingsLink {
                    Label("Tuning Settings…", systemImage: "gearshape")
                }
            } label: {
                Image(systemName: "ellipsis.circle")
            }
            .menuStyle(.borderlessButton)
            .fixedSize()
            .help("Tuning curve actions")
            .accessibilityLabel("Tuning curve actions")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
    }

    @ViewBuilder
    private var hdClassBadge: some View {
        if let clusterID = store.selectedClusterID,
           let hdClass = store.tuningData?.hdClass(for: clusterID),
           hdClass != .notSignificant {
            let isClassical = hdClass == .bothTestsSignificant
            Text(isClassical ? "2" : "1")
                .font(.callout.bold().monospacedDigit())
                .foregroundStyle(isClassical ? Color.hdClassTwo : Color.hdClassOne)
                .frame(width: 24, height: 24)
                .background(
                    (isClassical ? Color.hdClassTwo : Color.hdClassOne).opacity(0.12),
                    in: RoundedRectangle(cornerRadius: 6, style: .continuous)
                )
                .help(
                    isClassical
                        ? "HD class 2: Rayleigh and shuffle tests are both significant"
                        : "HD class 1: exactly one of the Rayleigh or shuffle tests is significant"
                )
                .accessibilityLabel(
                    isClassical
                        ? "HD class 2, Rayleigh and shuffle tests both significant"
                        : "HD class 1, exactly one test significant"
                )
        }
    }

    @ViewBuilder
    private func plot(
        curve: ProcessedTuningCurve,
        clusterID: Int,
        scaleHigh: Double
    ) -> some View {
        let mode = effectivePlotMode
        Group {
            switch mode {
            case .polar:
                HDPolarPlot(curve: curve, scaleHigh: scaleHigh)
            case .line, .automatic:
                HDLinePlot(curve: curve, scaleHigh: scaleHigh)
            }
        }
        .padding(10)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Head-direction tuning curve for cluster \(clusterID)")
        .accessibilityValue(accessibilitySummary(curve: curve, scaleHigh: scaleHigh))
        .accessibilityRepresentation {
            VStack(alignment: .leading) {
                Text("Cluster \(clusterID), peak \(formatHz(curve.peakHz)) hertz")
                ForEach(Array(curve.anglesDeg.indices), id: \.self) { index in
                    if let rate = curve.firingRatesHz[index] {
                        Text("\(formatAngle(curve.anglesDeg[index])) degrees, \(formatHz(rate)) hertz")
                    } else {
                        Text("\(formatAngle(curve.anglesDeg[index])) degrees, no occupancy")
                    }
                }
            }
        }
    }

    private func unavailable(title: String, detail: String, symbol: String) -> some View {
        ContentUnavailableView {
            Label(title, systemImage: symbol)
        } description: {
            Text(detail)
        } actions: {
            Button("Attach Tuning Curves…") {
                store.isImportingTuning = true
            }
        }
    }

    private func processingFailure(title: String, detail: String) -> some View {
        ContentUnavailableView {
            Label(title, systemImage: "exclamationmark.triangle")
        } description: {
            Text(detail)
        }
    }

    private var status: some View {
        HStack(spacing: 6) {
            if isCurrentRequestProcessing {
                ProgressView().controlSize(.mini)
            }
            Text(statusText)
                .font(.caption)
                .foregroundStyle(currentProcessingError == nil ? Color.secondary : Color.red)
                .lineLimit(2)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
    }

    private var statusText: String {
        if let currentProcessingError { return currentProcessingError }
        guard let data = store.tuningData else {
            return store.tuningErrorMessage ?? "Tuning curves are optional"
        }
        let schema = data.schema == .version2 ? "schema 2" : "legacy schema"
        let smooth = preferences.tuningSmoothing
            ? "σ=\(formatAngle(preferences.tuningSmoothingDegrees))°"
            : "smoothing off"
        let scale = preferences.tuningCompareScale ? "shared scale" : "per-cell scale"
        let missing = currentProcessedCurve?.firingRatesHz.filter { $0 == nil }.count ?? 0
        let missingText = missing > 0 ? " · \(missing) no-occupancy bins" : ""
        let legacy = data.schema == .legacy ? " · occupancy provenance unavailable" : ""
        return "\(data.url.lastPathComponent) · \(schema) · \(preferences.tuningDisplayBins) bins · \(smooth) · \(scale)\(missingText)\(legacy)"
    }

    private var effectivePlotMode: TuningPlotMode {
        switch preferences.tuningPlotMode {
        case .automatic:
            store.spatialPlotFormat == .polar ? .polar : .line
        case .line:
            .line
        case .polar:
            .polar
        }
    }

    private var currentRequest: CurrentCurveRequest {
        CurrentCurveRequest(
            dataID: store.tuningData.map(ObjectIdentifier.init),
            unitID: store.selectedClusterID,
            bins: preferences.tuningDisplayBins,
            smoothing: preferences.tuningSmoothing,
            sigmaDegrees: preferences.tuningSmoothingDegrees
        )
    }

    private var currentProcessedCurve: ProcessedTuningCurve? {
        processedResult?.value(for: currentRequest)
    }

    private var currentCurveError: String? {
        curveErrorResult?.value(for: currentRequest)
    }

    private var currentSharedScaleHigh: Double? {
        sharedScaleResult?.value(for: sharedScaleRequest)
    }

    private var currentSharedScaleError: String? {
        sharedScaleErrorResult?.value(for: sharedScaleRequest)
    }

    private var currentProcessingError: String? {
        currentCurveError ?? (preferences.tuningCompareScale ? currentSharedScaleError : nil)
    }

    private var isCurrentRequestProcessing: Bool {
        processingRequest == currentRequest
            || (preferences.tuningCompareScale
                && sharedScaleProcessingRequest == sharedScaleRequest)
    }

    private var sharedScaleRequest: SharedScaleRequest {
        SharedScaleRequest(
            dataID: store.tuningData.map(ObjectIdentifier.init),
            bins: preferences.tuningDisplayBins,
            smoothing: preferences.tuningSmoothing,
            sigmaDegrees: preferences.tuningSmoothingDegrees,
            enabled: preferences.tuningCompareScale
        )
    }

    @MainActor
    private func updateProcessedCurve(for request: CurrentCurveRequest) async {
        processedResult = nil
        curveErrorResult = nil
        guard let data = store.tuningData,
              request.dataID == ObjectIdentifier(data),
              let unitID = request.unitID else {
            return
        }
        processingRequest = request
        defer {
            if processingRequest == request { processingRequest = nil }
        }
        do {
            let bins = request.bins
            let smoothing = request.smoothing
            let sigma = request.sigmaDegrees / 12.0
            let curve = try await Task.detached(priority: .userInitiated) {
                try data.processedCurve(
                    for: unitID,
                    displayBins: bins,
                    smoothing: smoothing,
                    sigmaAtThirtyBins: sigma
                )
            }.value
            try Task.checkCancellation()
            guard currentRequest == request else { return }
            processedResult = curve.map { RequestBoundValue(request: request, value: $0) }
            curveErrorResult = nil
        } catch is CancellationError {
            return
        } catch {
            guard currentRequest == request else { return }
            processedResult = nil
            curveErrorResult = RequestBoundValue(
                request: request,
                value: error.localizedDescription
            )
        }
    }

    @MainActor
    private func updateSharedScale(for request: SharedScaleRequest) async {
        sharedScaleResult = nil
        sharedScaleErrorResult = nil
        guard request.enabled,
              let data = store.tuningData,
              request.dataID == ObjectIdentifier(data) else {
            return
        }
        sharedScaleProcessingRequest = request
        defer {
            if sharedScaleProcessingRequest == request {
                sharedScaleProcessingRequest = nil
            }
        }
        do {
            let bins = request.bins
            let smoothing = request.smoothing
            let sigma = request.sigmaDegrees / 12.0
            let high = try await Task.detached(priority: .utility) {
                var high = 0.0
                for unitID in data.unitIDs {
                    try Task.checkCancellation()
                    let curve = try data.processedCurve(
                        for: unitID,
                        displayBins: bins,
                        smoothing: smoothing,
                        sigmaAtThirtyBins: sigma
                    )
                    high = max(high, curve?.peakHz ?? 0.0)
                }
                return high
            }.value
            try Task.checkCancellation()
            guard sharedScaleRequest == request else { return }
            sharedScaleResult = RequestBoundValue(request: request, value: high)
            sharedScaleErrorResult = nil
        } catch is CancellationError {
            return
        } catch {
            guard sharedScaleRequest == request else { return }
            sharedScaleResult = nil
            sharedScaleErrorResult = RequestBoundValue(
                request: request,
                value: error.localizedDescription
            )
        }
    }

    private func accessibilitySummary(
        curve: ProcessedTuningCurve,
        scaleHigh: Double
    ) -> String {
        let missing = curve.firingRatesHz.filter { $0 == nil }.count
        let scale = preferences.tuningCompareScale ? "shared" : "per cell"
        return "Peak \(formatHz(curve.peakHz)) hertz; \(scale) scale zero to \(formatHz(scaleHigh)) hertz; \(missing) bins have no occupancy."
    }
}

private struct CurrentCurveRequest: Hashable {
    let dataID: ObjectIdentifier?
    let unitID: Int?
    let bins: Int
    let smoothing: Bool
    let sigmaDegrees: Double
}

private struct SharedScaleRequest: Hashable {
    let dataID: ObjectIdentifier?
    let bins: Int
    let smoothing: Bool
    let sigmaDegrees: Double
    let enabled: Bool
}

struct RequestBoundValue<Request: Equatable, Value> {
    let request: Request
    let value: Value

    func value(for currentRequest: Request) -> Value? {
        request == currentRequest ? value : nil
    }
}

func hdTuningDisplayScaleHigh(
    curvePeakHz: Double,
    compareScale: Bool,
    sharedScaleHigh: Double?
) -> Double? {
    guard compareScale else { return curvePeakHz }
    guard let sharedScaleHigh else { return nil }
    return max(curvePeakHz, sharedScaleHigh)
}

private struct HDLinePlot: View {
    let curve: ProcessedTuningCurve
    let scaleHigh: Double

    var body: some View {
        Canvas { context, size in
            let plot = CGRect(
                x: 52,
                y: 18,
                width: max(1, size.width - 68),
                height: max(1, size.height - 58)
            )
            let denominator = scaleHigh > 1e-12 ? scaleHigh : 1.0

            let tickFractions = scaleHigh > 1e-12 ? [0.0, 0.5, 1.0] : [0.0]
            for fraction in tickFractions {
                let y = plot.maxY - plot.height * fraction
                var grid = Path()
                grid.move(to: CGPoint(x: plot.minX, y: y))
                grid.addLine(to: CGPoint(x: plot.maxX, y: y))
                context.stroke(
                    grid,
                    with: .color(fraction == 0 ? .secondary : .secondary.opacity(0.18)),
                    lineWidth: fraction == 0 ? 1 : 0.75
                )
                context.draw(
                    Text(formatHz(scaleHigh * fraction))
                        .font(.caption2)
                        .foregroundStyle(.secondary),
                    at: CGPoint(x: plot.minX - 7, y: y),
                    anchor: .trailing
                )
            }

            var yAxis = Path()
            yAxis.move(to: CGPoint(x: plot.minX, y: plot.minY))
            yAxis.addLine(to: CGPoint(x: plot.minX, y: plot.maxY))
            context.stroke(yAxis, with: .color(.secondary), lineWidth: 1)

            for angle in stride(from: -180, through: 180, by: 90) {
                let x = plot.minX + plot.width * CGFloat(Double(angle + 180) / 360.0)
                var tick = Path()
                tick.move(to: CGPoint(x: x, y: plot.maxY))
                tick.addLine(to: CGPoint(x: x, y: plot.maxY + 4))
                context.stroke(tick, with: .color(.secondary), lineWidth: 1)
                let label = angle == -180 ? "180" : (angle < 0 ? "\(angle + 360)" : "\(angle)")
                context.draw(
                    Text(label).font(.caption2).foregroundStyle(.secondary),
                    at: CGPoint(x: x, y: plot.maxY + 15)
                )
            }

            let centered = zip(curve.anglesDeg, curve.firingRatesHz)
                .map { angle, rate in
                    var centeredAngle = (angle + 180).truncatingRemainder(dividingBy: 360) - 180
                    if centeredAngle < -180 { centeredAngle += 360 }
                    return (centeredAngle, rate)
                }
                .sorted { $0.0 < $1.0 }
            var segment: [CGPoint] = []
            func drawSegment(_ points: [CGPoint]) {
                guard !points.isEmpty else { return }
                if points.count == 1 {
                    let point = points[0]
                    context.fill(
                        Path(ellipseIn: CGRect(x: point.x - 3, y: point.y - 3, width: 6, height: 6)),
                        with: .color(.accentColor)
                    )
                    return
                }
                var path = Path()
                path.move(to: points[0])
                points.dropFirst().forEach { path.addLine(to: $0) }
                context.stroke(
                    path,
                    with: .color(.accentColor),
                    style: StrokeStyle(lineWidth: 2, lineJoin: .round)
                )
            }
            for (angle, rate) in centered {
                guard let rate, rate.isFinite else {
                    drawSegment(segment)
                    segment.removeAll(keepingCapacity: true)
                    continue
                }
                segment.append(CGPoint(
                    x: plot.minX + plot.width * CGFloat((angle + 180) / 360),
                    y: plot.maxY - plot.height * CGFloat(max(0, rate) / denominator)
                ))
            }
            drawSegment(segment)

            context.draw(
                Text("Head direction (deg)").font(.caption).foregroundStyle(.secondary),
                at: CGPoint(x: plot.midX, y: size.height - 5),
                anchor: .bottom
            )
            context.draw(
                Text("Hz").font(.caption).foregroundStyle(.secondary),
                at: CGPoint(x: 12, y: plot.midY)
            )
        }
    }
}

private struct HDPolarPlot: View {
    let curve: ProcessedTuningCurve
    let scaleHigh: Double

    var body: some View {
        Canvas { context, size in
            let center = CGPoint(x: size.width / 2, y: size.height / 2 + 4)
            let radius = max(24, min(size.width, size.height) / 2 - 36)
            let denominator = scaleHigh > 1e-12 ? scaleHigh : 1.0

            let ringFractions = scaleHigh > 1e-12 ? [0.25, 0.5, 0.75, 1.0] : [1.0]
            for fraction in ringFractions {
                let ring = radius * fraction
                context.stroke(
                    Path(ellipseIn: CGRect(
                        x: center.x - ring,
                        y: center.y - ring,
                        width: ring * 2,
                        height: ring * 2
                    )),
                    with: .color(.secondary.opacity(0.2)),
                    lineWidth: 0.75
                )
                if scaleHigh > 1e-12 {
                    context.draw(
                        Text("\(formatHz(scaleHigh * fraction)) Hz")
                            .font(.caption2)
                            .foregroundStyle(.secondary),
                        at: CGPoint(
                            x: center.x - ring * 0.707 + 4,
                            y: center.y - ring * 0.707 - 3
                        ),
                        anchor: .bottomLeading
                    )
                } else {
                    context.draw(
                        Text("0 Hz")
                            .font(.caption2)
                            .foregroundStyle(.secondary),
                        at: CGPoint(x: center.x + 5, y: center.y - 5),
                        anchor: .bottomLeading
                    )
                }
            }

            for (angle, label) in [(0.0, "0°"), (90.0, "90°"), (180.0, "180°"), (270.0, "270°")] {
                let vector = headDirectionVector(angle)
                var spoke = Path()
                spoke.move(to: center)
                spoke.addLine(to: CGPoint(
                    x: center.x + vector.x * radius,
                    y: center.y + vector.y * radius
                ))
                context.stroke(spoke, with: .color(.secondary.opacity(0.18)), lineWidth: 0.75)
                context.draw(
                    Text(label).font(.caption2).foregroundStyle(.secondary),
                    at: CGPoint(
                        x: center.x + vector.x * (radius + 15),
                        y: center.y + vector.y * (radius + 15)
                    )
                )
            }

            let points: [CGPoint?] = zip(curve.anglesDeg, curve.firingRatesHz).map { angle, rate in
                guard let rate, rate.isFinite else { return nil }
                let vector = headDirectionVector(angle)
                let scaled = radius * CGFloat(max(0, rate) / denominator)
                return CGPoint(
                    x: center.x + vector.x * scaled,
                    y: center.y + vector.y * scaled
                )
            }
            let finite = points.compactMap { $0 }
            if scaleHigh <= 1e-12, !finite.isEmpty {
                context.fill(
                    Path(ellipseIn: CGRect(x: center.x - 3, y: center.y - 3, width: 6, height: 6)),
                    with: .color(.accentColor)
                )
            } else if finite.count <= 2 {
                for point in finite {
                    context.fill(
                        Path(ellipseIn: CGRect(x: point.x - 3, y: point.y - 3, width: 6, height: 6)),
                        with: .color(.accentColor)
                    )
                }
            } else if finite.count == points.count {
                var outline = Path()
                outline.move(to: finite[0])
                finite.dropFirst().forEach { outline.addLine(to: $0) }
                outline.closeSubpath()
                context.stroke(
                    outline,
                    with: .color(.accentColor),
                    style: StrokeStyle(lineWidth: 2, lineJoin: .round)
                )
            } else {
                for segment in polarSegments(points) {
                    guard !segment.isEmpty else { continue }
                    if segment.count == 1 {
                        let point = segment[0]
                        context.fill(
                            Path(ellipseIn: CGRect(x: point.x - 3, y: point.y - 3, width: 6, height: 6)),
                            with: .color(.accentColor)
                        )
                    } else {
                        var path = Path()
                        path.move(to: segment[0])
                        segment.dropFirst().forEach { path.addLine(to: $0) }
                        context.stroke(
                            path,
                            with: .color(.accentColor),
                            style: StrokeStyle(lineWidth: 2, lineJoin: .round)
                        )
                    }
                }
            }
        }
    }
}

private struct TuningProvenanceView: View {
    let data: TuningCurveData?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Tuning provenance")
                .font(.headline)
            if let data, let metadata = data.metadata {
                provenanceRow("Schema", data.schema == .version2 ? "2" : "Legacy")
                provenanceRow("Timestamp", metadata.timestampReference ?? "Not recorded")
                provenanceRow("Timebase", metadata.timebase ?? "Not recorded")
                provenanceRow("Direction", metadata.angleConventionNote ?? "Not recorded")
                if let rate = metadata.featureRateHz {
                    provenanceRow("Tracking", "\(formatHz(rate)) Hz")
                }
                if let classification = metadata.classification {
                    provenanceRow("Classification", classification.method ?? "Not recorded")
                    if let alpha = classification.rayleighAlpha {
                        provenanceRow("Rayleigh α", formatNumber(alpha))
                    }
                    if let alpha = classification.shuffleAlpha {
                        provenanceRow("Shuffle α", formatNumber(alpha))
                    }
                    if let count = classification.numberOfShuffles {
                        provenanceRow("Shuffles", "\(count)")
                    }
                }
                if let ttl = metadata.ttlQC {
                    if let count = ttl.pulseCount { provenanceRow("Motive trigger TTLs", "\(count)") }
                    if let rate = ttl.measuredRateHz { provenanceRow("Measured rate", "\(formatHz(rate)) Hz") }
                }
            } else {
                Text("This file does not provide timing or direction metadata.")
                    .foregroundStyle(.secondary)
            }
        }
        .padding(16)
        .frame(width: 390)
    }

    private func provenanceRow(_ label: String, _ value: String) -> some View {
        LabeledContent(label) {
            Text(value)
                .multilineTextAlignment(.trailing)
                .textSelection(.enabled)
        }
        .font(.callout)
    }
}

private extension Color {
    static var hdClassOne: Color {
        Color(light: (0.541, 0.396, 0.031), dark: (1.0, 0.80, 0.25))
    }

    static var hdClassTwo: Color {
        Color(light: (0.008, 0.478, 0.282), dark: (0.25, 0.88, 0.55))
    }

    init(light: (Double, Double, Double), dark: (Double, Double, Double)) {
        self.init(nsColor: NSColor(name: nil) { appearance in
            let useDark = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            let rgb = useDark ? dark : light
            return NSColor(srgbRed: rgb.0, green: rgb.1, blue: rgb.2, alpha: 1)
        })
    }
}

private func headDirectionVector(_ angleDegrees: Double) -> CGPoint {
    let radians = angleDegrees * .pi / 180
    return CGPoint(x: -sin(radians), y: -cos(radians))
}

private func polarSegments(_ points: [CGPoint?]) -> [[CGPoint]] {
    var segments: [[CGPoint]] = []
    var current: [CGPoint] = []
    for point in points {
        if let point {
            current.append(point)
        } else if !current.isEmpty {
            segments.append(current)
            current = []
        }
    }
    if !current.isEmpty { segments.append(current) }
    if points.first != nil, points.last != nil, segments.count > 1 {
        segments[0] = segments.removeLast() + segments[0]
    }
    return segments
}

private func formatHz(_ value: Double) -> String {
    guard value.isFinite else { return "n/a" }
    return value.formatted(.number.precision(.significantDigits(1...4)))
}

private func formatAngle(_ value: Double) -> String {
    value.formatted(.number.precision(.fractionLength(0...1)))
}

private func formatNumber(_ value: Double) -> String {
    value.formatted(.number.precision(.significantDigits(1...4)))
}
