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
        return try await RFMappingDecodeCoordinator.decode(url: url)
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
    let attachTuningCurves: () -> Void
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
        WindowGroup("RF Map Viewer", for: DocumentWindowRequest.self) { request in
            RFMappingWindow(request: request.wrappedValue)
        }
        .defaultSize(width: 1440, height: 900)
        .windowResizability(.contentMinSize)
        .commands {
            RFMappingCommands()
        }

        Settings {
            ViewerSettingsView(preferences: .shared)
        }
    }
}

private struct RFMappingWindow: View {
    @Environment(\.openWindow) private var openWindow
    @Environment(\.dismiss) private var dismiss
    @State private var store: RFMappingStore
    @State private var pairingCoordinator: WindowPairingCoordinator
    @State private var pairingWindowID: UUID
    private let isInitialWindow: Bool
    private let initialURL: URL?

    init(request: DocumentWindowRequest?) {
        let url = request.map { URL(fileURLWithPath: $0.path) }
        let prepared = request.flatMap { WindowRouter.shared.takePreparedDocument(for: $0.id) }
        _store = State(initialValue: RFMappingStore(
            initialData: prepared,
            loadDefault: false
        ))
        _pairingCoordinator = State(initialValue: WindowPairingCoordinator.shared)
        _pairingWindowID = State(initialValue: UUID())
        isInitialWindow = request == nil
        initialURL = prepared == nil ? url : nil
    }

    var body: some View {
        ContentView(
            store: store,
            pairingCoordinator: pairingCoordinator,
            pairingWindowID: pairingWindowID,
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
        .background(WindowCloseObserver {
            pairingCoordinator.unregister(id: pairingWindowID)
        })
        .onAppear {
            pairingCoordinator.register(store, id: pairingWindowID)
        }
        .onDisappear {
            pairingCoordinator.unregister(id: pairingWindowID)
        }
        .onChange(of: store.viewerSyncState) { _, state in
            pairingCoordinator.synchronizedStateDidChange(state, from: pairingWindowID)
        }
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
            attachTuningCurves: { store.isImportingTuning = true },
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

/// Uses the actual NSWindow close notification as the authoritative lifecycle
/// signal. `onDisappear` remains as an idempotent fallback for scene teardown.
private struct WindowCloseObserver: NSViewRepresentable {
    let onClose: () -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onClose: onClose)
    }

    func makeNSView(context: Context) -> ObserverView {
        let view = ObserverView()
        view.coordinator = context.coordinator
        return view
    }

    func updateNSView(_ nsView: ObserverView, context: Context) {
        context.coordinator.onClose = onClose
        context.coordinator.attach(to: nsView.window)
    }

    static func dismantleNSView(_ nsView: ObserverView, coordinator: Coordinator) {
        coordinator.detach()
    }

    final class ObserverView: NSView {
        weak var coordinator: Coordinator?

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            coordinator?.attach(to: window)
        }

        override func hitTest(_ point: NSPoint) -> NSView? { nil }
    }

    @MainActor
    final class Coordinator {
        var onClose: () -> Void
        private weak var window: NSWindow?
        private var observer: NSObjectProtocol?

        init(onClose: @escaping () -> Void) {
            self.onClose = onClose
        }

        func attach(to window: NSWindow?) {
            guard let window, self.window !== window else { return }
            detach()
            self.window = window
            observer = NotificationCenter.default.addObserver(
                forName: NSWindow.willCloseNotification,
                object: window,
                queue: .main
            ) { [weak self] _ in
                MainActor.assumeIsolated {
                    self?.onClose()
                }
            }
        }

        func detach() {
            if let observer { NotificationCenter.default.removeObserver(observer) }
            observer = nil
            window = nil
        }

        deinit {
            MainActor.assumeIsolated {
                if let observer { NotificationCenter.default.removeObserver(observer) }
            }
        }
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
            Button("Attach Tuning Curves…") { actions?.attachTuningCurves() }
                .disabled(actions == nil)

            Button("Export Displayed…") { actions?.exportDisplayed() }
                .keyboardShortcut("e", modifiers: [.command])
                .disabled(actions == nil)
        }

        CommandMenu("Navigate") {
            Button("Previous Unit") { actions?.previousUnit() }
                .keyboardShortcut(.leftArrow, modifiers: [])
                .disabled(actions == nil)
            Button("Next Unit") { actions?.nextUnit() }
                .keyboardShortcut(.rightArrow, modifiers: [])
                .disabled(actions == nil)

            Divider()

            Button("Previous Timeline Bin") { actions?.previousBin() }
                .keyboardShortcut(.upArrow, modifiers: [])
                .disabled(actions == nil)
            Button("Next Timeline Bin") { actions?.nextBin() }
                .keyboardShortcut(.downArrow, modifiers: [])
                .disabled(actions == nil)
            Button("Decrease Time Resolution") { actions?.decreaseResolution() }
                .keyboardShortcut(",", modifiers: [.shift])
                .disabled(actions == nil)
            Button("Increase Time Resolution") { actions?.increaseResolution() }
                .keyboardShortcut(".", modifiers: [.shift])
                .disabled(actions == nil)

            Divider()

            Button("Show Full Time Range") { actions?.showFullRange() }
                .keyboardShortcut(.escape, modifiers: [])
                .disabled(actions == nil)
        }

        CommandGroup(after: .toolbar) {
            ForEach(Array(PlotTab.allCases.enumerated()), id: \.element) { index, tab in
                Button("Show \(tab.rawValue)") { actions?.selectTab(index) }
                    .keyboardShortcut(KeyEquivalent(Character(String(index + 1))), modifiers: [])
                    .disabled(actions == nil)
            }
            Divider()
            Button("Invert Y") { actions?.toggleFlipY() }
                .keyboardShortcut("f", modifiers: [])
                .disabled(actions == nil)
            Button("Cycle Palette") { actions?.cyclePalette() }
                .keyboardShortcut("p", modifiers: [])
                .disabled(actions == nil)
        }

        CommandGroup(replacing: .help) {
            Button("Support Documentation") { openSupportDocumentation() }
                .keyboardShortcut("?", modifiers: [.command])
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

/// Native menu key equivalents make shortcuts discoverable in the menu bar.
/// A window-scoped monitor routes those keys directly to an active field editor
/// and dispatches viewer shortcuts everywhere else.
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
                if Self.isEditingText(in: window), Self.isViewerShortcut(event) {
                    window.firstResponder?.keyDown(with: event)
                    return nil
                }
                guard self.handle(event) else {
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

        private static func isViewerShortcut(_ event: NSEvent) -> Bool {
            var modifiers = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
            modifiers.remove(.capsLock)
            modifiers.remove(.numericPad)
            modifiers.remove(.function)
            let character = event.charactersIgnoringModifiers?.lowercased()

            if modifiers.isEmpty {
                if [53, 123, 124, 125, 126].contains(event.keyCode) { return true }
                return ["[", "]", "f", "p", "1", "2", "3"].contains(character)
            }
            return modifiers == [.shift] && (character == "," || character == ".")
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
                case "1", "2", "3":
                    actions.selectTab(Int(character!)! - 1)
                    return true
                default: return false
                }
            }

            if modifiers == [.shift] {
                if character == "," { actions.decreaseResolution(); return true }
                if character == "." { actions.increaseResolution(); return true }
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
private func openSupportDocumentation() {
    guard let url = URL(string: "https://github.com/KaiCao2003/RF-Map-Viewer") else {
        showSupportDocumentationError()
        return
    }
    guard NSWorkspace.shared.open(url) else {
        showSupportDocumentationError()
        return
    }
}

@MainActor
private func showSupportDocumentationError() {
    let alert = NSAlert()
    alert.alertStyle = .warning
    alert.messageText = "Support Documentation Is Unavailable"
    alert.informativeText = "RF Map Viewer could not open its documentation in your default browser."
    alert.addButton(withTitle: "OK")
    alert.runModal()
}
