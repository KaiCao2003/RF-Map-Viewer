import AppKit
import SwiftUI

enum HeatmapKind {
    case rf
    case delay
}

struct HeatmapView: View {
    @Bindable var store: RFMappingStore
    let kind: HeatmapKind

    var body: some View {
        GeometryReader { proxy in
            let size = proxy.size
            let matrix = sourceMatrix
            let plot = makeHeatmapPlot(
                store: store,
                matrix: matrix,
                fixedRange: fixedRange,
                smooth: true
            )
            let layout = makeHeatmapLayout(size: size, plot: plot)

            ZStack(alignment: .topLeading) {
                Canvas { context, _ in
                    var context = context
                    drawHeatmap(
                        context: &context,
                        store: store,
                        plot: plot,
                        layout: layout,
                        title: title,
                        subtitle: subtitle,
                        palette: kind == .delay ? nil : store.palette,
                        valueSuffix: kind == .delay ? " ms" : ""
                    )
                }
                PointerCaptureView(
                    onMove: { point in
                        if let cell = layout.cellRef(at: point) {
                            store.setHover(cell, location: point)
                        } else {
                            store.clearHover()
                        }
                    },
                    onClick: { point, _ in
                        if let cell = layout.cellRef(at: point) {
                            store.selectCell(cell)
                        }
                    },
                    onLeave: {
                        store.clearHover()
                    }
                )
                if let cell = store.hoverCell, let location = store.hoverLocation {
                    PlotTooltip(text: store.tooltipText(cell), location: location, canvasSize: size)
                }
            }
        }
        .background(Color(nsColor: .textBackgroundColor))
    }

    private var sourceMatrix: OptionalMatrix {
        switch kind {
        case .rf:
            return optionalMatrix(store.currentMatrix())
        case .delay:
            return store.delayMatrixForTimeGroups(floor: store.responseFloor)
        }
    }

    private var fixedRange: (Double, Double)? {
        switch kind {
        case .rf:
            return nil
        case .delay:
            return store.timeAxisRangeMS()
        }
    }

    private var title: String {
        switch kind {
        case .rf:
            return "2D RF map - \(store.currentMatrixLabel())"
        case .delay:
            return "Delay map - peak displayed bin center"
        }
    }

    private var subtitle: String {
        guard let data = store.data else { return "" }
        return "Unit \(String(format: "%03d", store.unitIndex)) / cluster \(data.clusterID(for: store.unitIndex))"
    }
}
