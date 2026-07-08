import AppKit
import SwiftUI

@main
struct RFMappingSwiftUIApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @State private var store = RFMappingStore()

    var body: some Scene {
        WindowGroup("RF Mapping Viewer") {
            ContentView(store: store)
                .frame(minWidth: 1120, minHeight: 720)
        }
        .commands {
            CommandMenu("RF Mapping") {
                Button("Previous Unit") {
                    store.stepUnit(-1)
                }
                .keyboardShortcut("[", modifiers: [])

                Button("Next Unit") {
                    store.stepUnit(1)
                }
                .keyboardShortcut("]", modifiers: [])

                Divider()

                Button("Previous Bin") {
                    store.stepBin(-1)
                }
                .keyboardShortcut(.leftArrow, modifiers: [])

                Button("Next Bin") {
                    store.stepBin(1)
                }
                .keyboardShortcut(.rightArrow, modifiers: [])

                Button("Clear Time Selection") {
                    store.clearTimelineSelection()
                }
                .keyboardShortcut(.escape, modifiers: [])
            }
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
}
