import SwiftUI

/// A semantic counterpart for the immediate-mode Canvas plots. VoiceOver gets
/// one actionable element per displayed cell without changing the visual
/// renderer or pointer hit testing.
struct SpatialPlotAccessibilityRepresentation: View {
    @Bindable var store: RFMappingStore
    let title: String
    let matrix: OptionalMatrix
    let xGroups: [AxisGroup]
    let yGroups: [AxisGroup]
    var valueDescription: ((Int, Int, Double?) -> String)? = nil

    var body: some View {
        VStack {
            Text(title)
            ForEach(yGroups.indices, id: \.self) { displayY in
                ForEach(xGroups.indices, id: \.self) { displayX in
                    let yGroup = yGroups[displayY]
                    let xGroup = xGroups[displayX]
                    let cell = CellRef(
                        yStart: yGroup.start,
                        yEnd: yGroup.end,
                        xStart: xGroup.start,
                        xEnd: xGroup.end
                    )
                    let value = matrix.indices.contains(displayY)
                        && matrix[displayY].indices.contains(displayX)
                        ? matrix[displayY][displayX]
                        : nil
                    Button {
                        store.selectCell(cell)
                    } label: {
                        Text(cellLabel(cell, displayY: displayY, displayX: displayX, value: value))
                    }
                    .accessibilityAddTraits(store.selectedCell == cell ? .isSelected : [])
                }
            }
        }
    }

    private func cellLabel(
        _ cell: CellRef,
        displayY: Int,
        displayX: Int,
        value: Double?
    ) -> String {
        let valueText = valueDescription?(displayY, displayX, value)
            ?? "\(store.valueMode.format(value)) \(store.valueMode.unit)"
        return "\(store.yGroupText(cell.yStart, cell.yEnd)); "
            + "\(store.xGroupText(cell.xStart, cell.xEnd)); \(valueText)"
    }
}

struct TimelineAccessibilityRepresentation: View {
    @Bindable var store: RFMappingStore
    let layout: TimelineLayout

    var body: some View {
        VStack {
            Text("RF response timeline")
            if store.timeAxisStartMS() < 0 {
                Text("Negative bins may include previous-stimulus responses. Visual stimulation onset is zero milliseconds.")
            } else {
                Text("Visual stimulation onset is zero milliseconds.")
            }

            ForEach(layout.timeGroups.indices, id: \.self) { bin in
                Button {
                    store.selectTimelineBin(bin, extending: false)
                } label: {
                    Text(binLabel(bin))
                }
                .accessibilityAddTraits(store.binIndex == bin ? .isSelected : [])
            }

            Text("Spatial cells for selected timeline bin")
            SpatialPlotAccessibilityRepresentation(
                store: store,
                title: "Bin \(store.timeGroupLabel(activeBin))",
                matrix: activeMatrix,
                xGroups: activeMini?.xGroups ?? [],
                yGroups: activeMini?.yGroups ?? []
            )
        }
    }

    private var activeBin: Int {
        max(0, min(layout.displayBins - 1, store.binIndex))
    }

    private var activeMini: TimelineMiniLayout? {
        layout.miniLayouts.first { $0.bin == activeBin }
    }

    private var activeMatrix: OptionalMatrix {
        layout.miniMatrices.indices.contains(activeBin) ? layout.miniMatrices[activeBin] : []
    }

    private func binLabel(_ bin: Int) -> String {
        let total = layout.timeTotals.indices.contains(bin) ? layout.timeTotals[bin] : 0
        var label = "Bin \(bin + 1), \(store.timeGroupLabel(bin)); all positions "
            + "\(store.valueMode.format(total)) \(store.valueMode.unit)"
        if let selected = layout.selectedValues, selected.indices.contains(bin) {
            label += "; selected cell \(store.valueMode.format(selected[bin])) \(store.valueMode.unit)"
        }
        return label
    }
}
