import SwiftUI

struct ADNAnnotationView: View {
    let geometry: ProbeGeometry
    let selectionURL: URL
    let showUnitInfo: Bool
    let save: (Set<Int>) throws -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var selectedUnitIDs: Set<Int>
    @State private var undoSelections: [Set<Int>] = []
    @State private var lasso: [CGPoint] = []
    @State private var depthZoom = 1
    @State private var scrollOffset: CGFloat = 0
    @State private var hoveredUnit: ProbeUnitPosition?
    @State private var hoverLocation: CGPoint?
    @State private var saved = false
    @State private var errorMessage: String?

    init(
        geometry: ProbeGeometry,
        savedUnitIDs: Set<Int>,
        selectionURL: URL,
        showUnitInfo: Bool = true,
        save: @escaping (Set<Int>) throws -> Void
    ) {
        self.geometry = geometry
        self.selectionURL = selectionURL
        self.showUnitInfo = showUnitInfo
        self.save = save
        _selectedUnitIDs = State(initialValue: savedUnitIDs)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Annotate ADN · \(geometry.probeName)")
                        .font(.title2.weight(.semibold))
                    Text("Circle units to add them. Larger probe y is shallower.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Close") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
            HStack(spacing: 10) {
                Button("Undo", action: undo).disabled(undoSelections.isEmpty)
                Button("Clear selection") { replaceSelection([]) }
                    .disabled(selectedUnitIDs.isEmpty)
                Spacer()
                Text("Depth zoom")
                Button("−") { changeDepthZoom(-1) }.disabled(depthZoom == 1)
                    .accessibilityLabel("Decrease depth zoom")
                Text("\(depthZoom)×").monospacedDigit().frame(width: 28)
                Button("+") { changeDepthZoom(1) }.disabled(depthZoom == 8)
                    .accessibilityLabel("Increase depth zoom")
            }
            HStack(alignment: .top, spacing: 12) {
                GeometryReader { viewport in
                    let canvasSize = CGSize(
                        width: viewport.size.width,
                        height: max(380, viewport.size.height) * CGFloat(depthZoom)
                    )
                    let transform = ProbeDisplayTransform(
                        geometry: geometry,
                        unitIDs: Set(geometry.units.map(\.unitID)),
                        size: canvasSize,
                        axes: true
                    )
                    ScrollView(.vertical) {
                        Canvas { context, _ in
                            drawGeometry(context: &context, transform: transform)
                            if showUnitInfo, let hoveredUnit, let hoverLocation {
                                drawProbeUnitTooltip(
                                    context: &context,
                                    unit: hoveredUnit,
                                    at: hoverLocation,
                                    bounds: CGRect(
                                        x: 0, y: scrollOffset,
                                        width: canvasSize.width, height: viewport.size.height
                                    )
                                )
                            }
                        }
                        .frame(width: canvasSize.width, height: canvasSize.height)
                        .background(.white)
                        .contentShape(Rectangle())
                        .gesture(
                            DragGesture(minimumDistance: 2)
                                .onChanged { value in
                                    hoveredUnit = nil
                                    if lasso.isEmpty { lasso.append(value.startLocation) }
                                    lasso.append(value.location)
                                }
                                .onEnded { value in
                                    lasso.append(value.location)
                                    let polygon = lasso.map { transform.position(at: $0) }
                                    let enclosed = ADNSelection.unitIDs(in: polygon, geometry: geometry)
                                    replaceSelection(selectedUnitIDs.union(enclosed))
                                    lasso = []
                                }
                        )
                        .onContinuousHover { phase in
                            switch phase {
                            case .active(let location):
                                hoverLocation = location
                                hoveredUnit = showUnitInfo && lasso.isEmpty
                                    ? nearestUnit(at: location, transform: transform) : nil
                            case .ended:
                                hoveredUnit = nil
                                hoverLocation = nil
                            }
                        }
                    }
                    .onScrollGeometryChange(for: CGFloat.self) { $0.contentOffset.y } action: { _, offset in
                        scrollOffset = offset
                        hoveredUnit = nil
                    }
                }
                .background(.white)
                .overlay(Rectangle().stroke(.gray.opacity(0.4)))
                VStack(alignment: .leading, spacing: 8) {
                    Text("\(selectedUnitIDs.count) selected").font(.headline)
                    Text("\(geometry.positionedUnits.count)/\(geometry.units.count) units positioned")
                        .font(.caption).foregroundStyle(.secondary)
                    ScrollView {
                        Text(selectedUnitIDs.sorted().map(String.init).joined(separator: ", "))
                            .font(.system(.callout, design: .monospaced))
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .frame(width: 140)
            }
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Save to session/data/adn_neuron.npy").font(.callout)
                    Text(saved ? "Saved \(selectedUnitIDs.count) units for \(geometry.probeName)." : selectionURL.path)
                        .font(.caption).foregroundStyle(.secondary)
                        .lineLimit(1).truncationMode(.middle)
                        .help(selectionURL.path)
                }
                Spacer()
                Button("Save ADN", action: saveSelection)
                    .keyboardShortcut("s", modifiers: .command)
                    .buttonStyle(.borderedProminent)
            }
        }
        .padding(18)
        .frame(width: 800, height: 640)
        .preferredColorScheme(.light)
        .alert("ADN selection", isPresented: Binding(
            get: { errorMessage != nil },
            set: { if !$0 { errorMessage = nil } }
        )) {
            Button("OK") { errorMessage = nil }
        } message: {
            Text(errorMessage ?? "")
        }
    }

    private func replaceSelection(_ unitIDs: Set<Int>) {
        guard unitIDs != selectedUnitIDs else { return }
        undoSelections.append(selectedUnitIDs)
        selectedUnitIDs = unitIDs
        saved = false
    }

    private func undo() {
        guard let previous = undoSelections.popLast() else { return }
        selectedUnitIDs = previous
        saved = false
    }

    private func saveSelection() {
        do {
            try save(selectedUnitIDs)
            saved = true
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func changeDepthZoom(_ delta: Int) {
        depthZoom += delta
        hoveredUnit = nil
        lasso = []
    }

    private func nearestUnit(at location: CGPoint, transform: ProbeDisplayTransform) -> ProbeUnitPosition? {
        let candidates = geometry.positionedUnits.compactMap { unit -> (ProbeUnitPosition, CGFloat)? in
            guard let position = unit.position else { return nil }
            let point = transform.point(x: position.x, y: position.y)
            return (unit, hypot(point.x - location.x, point.y - location.y))
        }
        guard let nearest = candidates.min(by: { $0.1 < $1.1 }), nearest.1 <= 8 else { return nil }
        return nearest.0
    }

    private func drawGeometry(context: inout GraphicsContext, transform: ProbeDisplayTransform) {
        let rect = transform.plotRect
        context.stroke(Path(rect), with: .color(.gray.opacity(0.5)), lineWidth: 0.7)
        for index in 0...5 {
            let fraction = Double(index) / 5
            let x = transform.xRange.lowerBound + fraction * (transform.xRange.upperBound - transform.xRange.lowerBound)
            let y = transform.yRange.lowerBound + fraction * (transform.yRange.upperBound - transform.yRange.lowerBound)
            let xPoint = transform.point(x: x, y: transform.yRange.lowerBound)
            let yPoint = transform.point(x: transform.xRange.lowerBound, y: y)
            context.draw(Text(String(format: "%.0f", x)).font(.system(size: 10)).foregroundColor(.black),
                         at: CGPoint(x: xPoint.x, y: rect.maxY + 12))
            context.draw(Text(String(format: "%.0f", y)).font(.system(size: 10)).foregroundColor(.black),
                         at: CGPoint(x: rect.minX - 7, y: yPoint.y), anchor: .trailing)
        }
        context.draw(Text("Probe x (µm)").font(.system(size: 11)).foregroundColor(.black),
                     at: CGPoint(x: rect.midX, y: rect.maxY + 32))
        var yContext = context
        yContext.translateBy(x: 13, y: rect.midY)
        yContext.rotate(by: .degrees(-90))
        yContext.draw(Text("Probe y (µm; larger = shallower)").font(.system(size: 11)).foregroundColor(.black), at: .zero)
        for channel in geometry.channels {
            let point = transform.point(x: channel.xMicrometers, y: channel.yMicrometers)
            context.fill(Path(ellipseIn: CGRect(x: point.x - 1.5, y: point.y - 1.5, width: 3, height: 3)),
                         with: .color(.gray.opacity(0.22)))
        }
        for unit in geometry.positionedUnits {
            guard let position = unit.position else { continue }
            let point = transform.point(x: position.x, y: position.y)
            let selected = selectedUnitIDs.contains(unit.unitID)
            context.fill(Path(ellipseIn: CGRect(x: point.x - 3.5, y: point.y - 3.5, width: 7, height: 7)),
                         with: .color(selected ? .orange : .blue))
            context.draw(Text(String(unit.unitID)).font(.system(size: 8)).foregroundColor(.black),
                         at: CGPoint(x: point.x + 5, y: point.y - 4), anchor: .leading)
        }
        if let first = lasso.first {
            var path = Path()
            path.move(to: first)
            path.addLines(lasso.dropFirst().map { $0 })
            path.closeSubpath()
            context.fill(path, with: .color(.orange.opacity(0.1)))
            context.stroke(path, with: .color(.orange), lineWidth: 1.5)
        }
    }
}

func drawProbeUnitTooltip(
    context: inout GraphicsContext,
    unit: ProbeUnitPosition,
    at location: CGPoint,
    bounds: CGRect
) {
    guard let position = unit.position else { return }
    let label = Text("Unit ID: \(unit.unitID)\nx: \(String(format: "%.1f", position.x)) µm\ny: \(String(format: "%.1f", position.y)) µm")
        .font(.system(size: 11)).foregroundColor(.black)
    let resolved = context.resolve(label)
    let textSize = resolved.measure(in: CGSize(width: 200, height: 100))
    let size = CGSize(width: textSize.width + 14, height: textSize.height + 10)
    let origin = CGPoint(
        x: max(bounds.minX + 3, min(location.x + 12, bounds.maxX - size.width - 3)),
        y: max(bounds.minY + 3, min(location.y + 12, bounds.maxY - size.height - 3))
    )
    let bubble = Path(roundedRect: CGRect(origin: origin, size: size), cornerRadius: 4)
    context.fill(bubble, with: .color(.white))
    context.stroke(bubble, with: .color(.gray.opacity(0.75)), lineWidth: 0.8)
    context.draw(resolved, at: CGPoint(x: origin.x + 7, y: origin.y + 5), anchor: .topLeading)
}
