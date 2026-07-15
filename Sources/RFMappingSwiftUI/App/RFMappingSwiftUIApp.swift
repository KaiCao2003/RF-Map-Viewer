import AppKit
import SwiftUI

struct DocumentWindowRequest: Codable, Hashable {
    let id: UUID
    let path: String

    init(url: URL) {
        id = UUID()
        path = url.standardizedFileURL.path
    }
}

@MainActor
final class WindowRouter {
    static let shared = WindowRouter()

    private struct PendingExternalOpen {
        let urls: [URL]
        let completion: (Bool) -> Void
    }

    private var opener: ((DocumentWindowRequest) -> Void)?
    private var pending: [DocumentWindowRequest] = []
    private var pendingExternalOpens: [PendingExternalOpen] = []
    private var coldLaunchReplacement: ((URL) async -> Bool)?
    private var coldLaunchFallback: (() -> Void)?
    private var coldLaunchExpiration: Task<Void, Never>?
    private var didOfferColdLaunchReplacement = false
    private var preparedDocuments: [UUID: RFMappingData] = [:]

    func install(
        _ action: OpenWindowAction,
        coldLaunchReplacement replacement: ((URL) async -> Bool)? = nil,
        coldLaunchFallback fallback: (() -> Void)? = nil
    ) {
        opener = { request in action(value: request) }

        if let replacement, !didOfferColdLaunchReplacement {
            didOfferColdLaunchReplacement = true
            coldLaunchReplacement = replacement
            coldLaunchFallback = fallback
            scheduleColdLaunchFallback()
        }

        let queued = pending
        pending.removeAll()
        queued.forEach { opener?($0) }

        if !pendingExternalOpens.isEmpty {
            let externalOpens = pendingExternalOpens
            pendingExternalOpens.removeAll()
            for externalOpen in externalOpens {
                openExternal(externalOpen.urls, completion: externalOpen.completion)
            }
        }
    }

    @discardableResult
    func openAsync(_ url: URL) async -> Bool {
        let request = DocumentWindowRequest(url: url)
        do {
            preparedDocuments[request.id] = try await loadDocumentAsync(url)
            if let opener {
                opener(request)
            } else {
                pending.append(request)
            }
            return true
        } catch {
            showOpenError(error, url: url)
            return false
        }
    }

    func takePreparedDocument(for id: UUID) -> RFMappingData? {
        preparedDocuments.removeValue(forKey: id)
    }

    func claimColdInitialWindow(for url: URL) -> Bool {
        guard let replacement = coldLaunchReplacement else { return false }
        coldLaunchReplacement = nil
        coldLaunchFallback = nil
        coldLaunchExpiration?.cancel()
        coldLaunchExpiration = nil
        Task { @MainActor in
            _ = await replacement(url)
        }
        return true
    }

    func pauseColdInitialWindowFallback() {
        guard coldLaunchReplacement != nil else { return }
        coldLaunchExpiration?.cancel()
        coldLaunchExpiration = nil
    }

    func resumeColdInitialWindowFallback() {
        guard coldLaunchReplacement != nil, coldLaunchExpiration == nil else { return }
        scheduleColdLaunchFallback()
    }

    /// Finder/Launch Services should populate the otherwise-empty initial
    /// WindowGroup window during a cold launch. Later document opens always
    /// create independent windows, matching the Python viewer.
    func openExternal(_ urls: [URL], completion: @escaping (Bool) -> Void) {
        guard !urls.isEmpty else {
            completion(true)
            return
        }
        guard opener != nil else {
            pendingExternalOpens.append(PendingExternalOpen(urls: urls, completion: completion))
            return
        }

        Task { @MainActor [weak self] in
            guard let self else {
                completion(false)
                return
            }
            completion(await processExternal(urls))
        }
    }

    private func processExternal(_ urls: [URL]) async -> Bool {
        var allSucceeded = true
        var remaining = urls[...]
        if let replacement = coldLaunchReplacement, let first = remaining.first {
            coldLaunchReplacement = nil
            coldLaunchFallback = nil
            coldLaunchExpiration?.cancel()
            coldLaunchExpiration = nil
            allSucceeded = await replacement(first) && allSucceeded
            remaining = remaining.dropFirst()
        }
        for url in remaining {
            allSucceeded = await openAsync(url) && allSucceeded
        }
        return allSucceeded
    }

    func showOpenError(_ error: Error, url: URL) {
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = "Could not open \(url.lastPathComponent)"
        alert.informativeText = error.localizedDescription
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    private func loadDocumentAsync(_ url: URL) async throws -> RFMappingData {
        let accessing = url.startAccessingSecurityScopedResource()
        defer {
            if accessing { url.stopAccessingSecurityScopedResource() }
        }
        return try await RFMappingData.decodeOffMain(url: url)
    }

    private func scheduleColdLaunchFallback() {
        coldLaunchExpiration?.cancel()
        coldLaunchExpiration = Task { @MainActor [weak self] in
            try? await Task.sleep(for: .milliseconds(150))
            guard !Task.isCancelled, let self, self.coldLaunchReplacement != nil else { return }
            let fallback = self.coldLaunchFallback
            self.coldLaunchReplacement = nil
            self.coldLaunchFallback = nil
            self.coldLaunchExpiration = nil
            fallback?()
        }
    }
}

struct RFMappingCommandActions {
    let openJSON: () -> Void
    let exportDisplayed: () -> Void
    let previousUnit: () -> Void
    let nextUnit: () -> Void
    let previousBin: () -> Void
    let nextBin: () -> Void
    let decreaseResolution: () -> Void
    let increaseResolution: () -> Void
    let showFullRange: () -> Void
    let selectTab: (Int) -> Void
    let toggleFlipY: () -> Void
    let cyclePalette: () -> Void
}

private struct RFMappingCommandKey: FocusedValueKey {
    typealias Value = RFMappingCommandActions
}

extension FocusedValues {
    var rfMappingCommands: RFMappingCommandActions? {
        get { self[RFMappingCommandKey.self] }
        set { self[RFMappingCommandKey.self] = newValue }
    }
}

@main
struct RFMappingSwiftUIApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        WindowGroup("RF Mapping Viewer", for: DocumentWindowRequest.self) { request in
            RFMappingWindow(request: request.wrappedValue)
        }
        .defaultSize(width: 1440, height: 900)
        .windowResizability(.contentMinSize)
        .commands {
            RFMappingCommands()
        }
    }
}

private struct RFMappingWindow: View {
    @Environment(\.openWindow) private var openWindow
    @Environment(\.dismiss) private var dismiss
    @State private var store: RFMappingStore
    private let isInitialWindow: Bool
    private let initialURL: URL?

    init(request: DocumentWindowRequest?) {
        let url = request.map { URL(fileURLWithPath: $0.path) }
        let prepared = request.flatMap { WindowRouter.shared.takePreparedDocument(for: $0.id) }
        _store = State(initialValue: RFMappingStore(
            initialData: prepared,
            loadDefault: false
        ))
        isInitialWindow = request == nil
        initialURL = prepared == nil ? url : nil
    }

    var body: some View {
        ContentView(
            store: store,
            openJSONInNewWindow: { url in
                if isInitialWindow, !store.hasData,
                   WindowRouter.shared.claimColdInitialWindow(for: url) {
                    return
                }
                Task { @MainActor in
                    _ = await WindowRouter.shared.openAsync(url)
                }
            }
        )
        .frame(minWidth: 1120, minHeight: 720)
        .navigationTitle(store.windowTitle)
        .focusedSceneValue(\.rfMappingCommands, commandActions)
        .background(WindowShortcutMonitor(actions: commandActions))
        .task {
            WindowRouter.shared.install(
                openWindow,
                coldLaunchReplacement: isInitialWindow ? { url in
                    guard await store.loadJSONAsync(url) else {
                        let error = RFMappingError.invalidData(store.errorMessage ?? "Unknown document error")
                        WindowRouter.shared.showOpenError(error, url: url)
                        store.errorMessage = nil
                        dismiss()
                        return false
                    }
                    return true
                } : nil,
                coldLaunchFallback: isInitialWindow ? {
                    Task { @MainActor in
                        await store.loadLatestJSONAsync()
                    }
                } : nil
            )
            if let initialURL, !store.hasData {
                _ = await store.loadJSONAsync(initialURL)
            }
        }
    }

    private var commandActions: RFMappingCommandActions {
        RFMappingCommandActions(
            openJSON: { store.isImporting = true },
            exportDisplayed: store.prepareExport,
            previousUnit: { store.stepUnit(-1) },
            nextUnit: { store.stepUnit(1) },
            previousBin: { store.stepBin(-1) },
            nextBin: { store.stepBin(1) },
            decreaseResolution: { store.stepTimeResolution(-1.0) },
            increaseResolution: { store.stepTimeResolution(1.0) },
            showFullRange: store.clearTimelineSelection,
            selectTab: store.selectTab,
            toggleFlipY: { store.flipY.toggle() },
            cyclePalette: store.cyclePalette
        )
    }
}

private struct RFMappingCommands: Commands {
    @FocusedValue(\.rfMappingCommands) private var actions

    var body: some Commands {
        CommandGroup(replacing: .newItem) {
            Button("Open JSON in New Window…") { actions?.openJSON() }
                .keyboardShortcut("o", modifiers: [.command])
                .disabled(actions == nil)
        }

        CommandGroup(after: .saveItem) {
            Button("Export Displayed…") { actions?.exportDisplayed() }
                .keyboardShortcut("e", modifiers: [.command])
                .disabled(actions == nil)
        }

        CommandMenu("Navigate") {
            Button("Previous Unit (← or [)") { actions?.previousUnit() }
            Button("Next Unit (→ or ])") { actions?.nextUnit() }

            Divider()

            Button("Previous Timeline Bin (↑)") { actions?.previousBin() }
            Button("Next Timeline Bin (↓)") { actions?.nextBin() }
            Button("Decrease Time Resolution 1 ms (Shift-,)") { actions?.decreaseResolution() }
            Button("Increase Time Resolution 1 ms (Shift-.)") { actions?.increaseResolution() }

            Divider()

            Button("Show Full Time Range (Esc)") { actions?.showFullRange() }
        }

        CommandMenu("View") {
            ForEach(Array(PlotTab.allCases.enumerated()), id: \.element) { index, tab in
                Button("\(tab.rawValue) (\(index + 1))") { actions?.selectTab(index) }
            }
            Divider()
            Button("Invert Y (F)") { actions?.toggleFlipY() }
            Button("Cycle Palette (P)") { actions?.cyclePalette() }
        }

        CommandGroup(after: .help) {
            Button("RF Mapping Keyboard Shortcuts (?)") { showKeyboardShortcuts() }
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }

    func application(_ sender: NSApplication, openFiles filenames: [String]) {
        WindowRouter.shared.openExternal(filenames.map { URL(fileURLWithPath: $0) }) { succeeded in
            sender.reply(toOpenOrPrint: succeeded ? .success : .failure)
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }
}

/// SwiftUI menu key equivalents are resolved before AppKit's field editor, so
/// unmodified shortcuts would otherwise swallow digits/arrows typed in a
/// TextField. A window-scoped monitor lets text controls handle those keys and
/// dispatches the viewer shortcuts everywhere else.
private struct WindowShortcutMonitor: NSViewRepresentable {
    let actions: RFMappingCommandActions

    func makeCoordinator() -> Coordinator {
        Coordinator(actions: actions)
    }

    func makeNSView(context: Context) -> MonitorView {
        let view = MonitorView()
        context.coordinator.install(for: view)
        return view
    }

    func updateNSView(_ nsView: MonitorView, context: Context) {
        context.coordinator.actions = actions
    }

    static func dismantleNSView(_ nsView: MonitorView, coordinator: Coordinator) {
        coordinator.uninstall()
    }

    final class MonitorView: NSView {
        override func hitTest(_ point: NSPoint) -> NSView? { nil }
    }

    @MainActor
    final class Coordinator {
        var actions: RFMappingCommandActions
        private weak var view: MonitorView?
        private var monitor: Any?

        init(actions: RFMappingCommandActions) {
            self.actions = actions
        }

        func install(for view: MonitorView) {
            self.view = view
            monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
                guard let self, let window = self.view?.window, event.window === window else {
                    return event
                }
                guard !Self.isEditingText(in: window), self.handle(event) else {
                    return event
                }
                return nil
            }
        }

        func uninstall() {
            if let monitor { NSEvent.removeMonitor(monitor) }
            monitor = nil
        }

        private static func isEditingText(in window: NSWindow) -> Bool {
            guard let responder = window.firstResponder as? NSView else { return false }
            if responder is NSTextView { return true }
            var view: NSView? = responder
            while let current = view {
                if current is NSTextField || current is NSComboBox
                    || current is NSPopUpButton || current is NSStepper {
                    return true
                }
                view = current.superview
            }
            return false
        }

        private func handle(_ event: NSEvent) -> Bool {
            var modifiers = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
            modifiers.remove(.capsLock)
            modifiers.remove(.numericPad)
            modifiers.remove(.function)
            let character = event.charactersIgnoringModifiers?.lowercased()

            if modifiers.isEmpty {
                switch event.keyCode {
                case 123: actions.previousUnit(); return true
                case 124: actions.nextUnit(); return true
                case 125: actions.nextBin(); return true
                case 126: actions.previousBin(); return true
                case 53: actions.showFullRange(); return true
                default: break
                }
                switch character {
                case "[": actions.previousUnit(); return true
                case "]": actions.nextUnit(); return true
                case "f": actions.toggleFlipY(); return true
                case "p": actions.cyclePalette(); return true
                case "?": showKeyboardShortcuts(); return true
                case "1", "2", "3":
                    actions.selectTab(Int(character!)! - 1)
                    return true
                default: return false
                }
            }

            if modifiers == [.shift] {
                if character == "," { actions.decreaseResolution(); return true }
                if character == "." { actions.increaseResolution(); return true }
                if event.characters == "?" { showKeyboardShortcuts(); return true }
            }
            return false
        }

        deinit {
            MainActor.assumeIsolated {
                if let monitor { NSEvent.removeMonitor(monitor) }
            }
        }
    }
}

@MainActor
private func showKeyboardShortcuts() {
    let alert = NSAlert()
    alert.messageText = "RF Mapping Keyboard Shortcuts"
    alert.informativeText = """
    ← / →   Previous / next unit
    ↑ / ↓   Previous / next timeline bin
    Shift+, / Shift+.   Time resolution −/+ 1 ms
    1–3   Switch plot tab
    F   Invert Y
    P   Cycle palette
    Esc   Show full time range
    [ / ]   Previous / next unit
    Command-O   Open JSON in a new window
    Command-E   Export displayed matrix
    Command-W   Close current window
    """
    alert.addButton(withTitle: "OK")
    alert.runModal()
}
