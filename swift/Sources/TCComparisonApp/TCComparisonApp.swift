import SwiftUI
import UniformTypeIdentifiers

extension UTType {
    static let comparisonTuningCurve = UTType(
        exportedAs: "org.local.rfmapping.tc",
        conformingTo: .json
    )

    static var comparisonTuningCurveReadableTypes: [UTType] {
        [.comparisonTuningCurve, .json]
    }
}

@main
struct TCComparisonApp: App {
    @State private var store = TCComparisonStore()

    var body: some Scene {
        Window("TC Comparison", id: "tc-comparison") {
            TCComparisonRootView(store: store)
                .frame(minWidth: 980, minHeight: 680)
        }
        .defaultSize(width: 1280, height: 820)
        .windowResizability(.contentMinSize)
        .commands {
            TCComparisonCommands(store: store)
        }
    }
}

struct TCComparisonCommands: Commands {
    let store: TCComparisonStore

    var body: some Commands {
        CommandGroup(replacing: .newItem) {
            Button("Open Two Tuning Curves…") {
                store.beginImport()
            }
            .keyboardShortcut("o")
        }
        CommandMenu("Curves") {
            Button("Previous Shared Unit") { store.stepUnit(-1) }
                .keyboardShortcut("[", modifiers: [])
                .disabled(store.availableUnitIDs.isEmpty)
            Button("Next Shared Unit") { store.stepUnit(1) }
                .keyboardShortcut("]", modifiers: [])
                .disabled(store.availableUnitIDs.isEmpty)
            Divider()
            Button(store.plotMode == .line ? "Show Polar View" : "Show Line View") {
                store.togglePlotMode()
            }
            .keyboardShortcut("p", modifiers: [])
            Divider()
            Button("Swap TC I and TC II") { store.swapCurves() }
                .disabled(store.loadedCurveCount < 2 || store.isLoadingAnyCurve)
            Button("Reset Angle Offsets") {
                store.setAngleOffset(0, for: .first)
                store.setAngleOffset(0, for: .second)
            }
            Divider()
            Button("Clear Both Curves") { store.clearAll() }
                .disabled(store.loadedCurveCount == 0)
        }
    }
}
