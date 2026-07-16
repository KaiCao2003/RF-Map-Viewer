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
            let plot = cachedPlot
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
                        valueSuffix: kind == .delay ? " ms" : store.valueMode.suffix,
                        drawInteraction: false
                    )
                }
                RectangularPlotInteractionLayer(store: store, layout: layout, size: size)
            }
            .accessibilityRepresentation {
                SpatialPlotAccessibilityRepresentation(
                    store: store,
                    title: title,
                    matrix: plot.matrix,
                    xGroups: plot.xGroups,
                    yGroups: plot.yGroups,
                    valueDescription: { _, _, value in
                        if kind == .delay {
                            return value.map { String(format: "%.1f milliseconds", $0) } ?? "no delay"
                        }
                        return "\(store.valueMode.format(value)) \(store.valueMode.unit)"
                    }
                )
            }
        }
        .background(Color(nsColor: .textBackgroundColor))
    }

    private var cachedPlot: HeatmapPlot {
        switch kind {
        case .rf:
            return store.currentHeatmapPlot()
        case .delay:
            return store.delayHeatmapPlot(floor: store.responseFloor)
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
