import AppKit
import SwiftUI
import UniformTypeIdentifiers

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
    private var preparedDocuments: [UUID: RFMappingData] = [:]
    private weak var welcomeWindow: NSWindow?
    private var dismissWelcomeImporter: (() -> Void)?

    func registerWelcomeWindow(_ window: NSWindow, dismissImporter: @escaping () -> Void = {}) {
        welcomeWindow = window
        dismissWelcomeImporter = dismissImporter
    }

    func documentDidAppear() {
        dismissWelcomeImporter?()
        welcomeWindow?.orderOut(nil)
    }

    func showWelcome(openNew: () -> Void) {
        if let welcomeWindow, welcomeWindow.isVisible || welcomeWindow.isMiniaturized {
            welcomeWindow.deminiaturize(nil)
            welcomeWindow.makeKeyAndOrderFront(nil)
        } else {
            openNew()
        }
    }

    func install(_ openDocumentWindow: @escaping (DocumentWindowRequest) -> Void) {
        opener = openDocumentWindow

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

    /// Finder opens can precede the welcome scene's installation of openWindow.
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
        for url in urls {
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
}

struct RFMappingCommandActions {
    let exportFigures: () -> Void
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
    let toggleSpatialFormat: () -> Void
    let cyclePalette: () -> Void
    let toggleSubtract: () -> Void
    let toggleDisplayOptions: () -> Void
    let toggleFilteredUnits: () -> Void
}

struct RFMappingOpenActions {
    let openRFMap: () -> Void
    let openRecent: (URL) -> Void
}

private struct RFMappingCommandKey: FocusedValueKey {
    typealias Value = RFMappingCommandActions
}

private struct RFMappingOpenKey: FocusedValueKey {
    typealias Value = RFMappingOpenActions
}

extension FocusedValues {
    var rfMappingOpenActions: RFMappingOpenActions? {
        get { self[RFMappingOpenKey.self] }
        set { self[RFMappingOpenKey.self] = newValue }
    }

    var rfMappingCommands: RFMappingCommandActions? {
        get { self[RFMappingCommandKey.self] }
        set { self[RFMappingCommandKey.self] = newValue }
    }
}

@main
struct RFMappingSwiftUIApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        Window("Welcome to RF Map Viewer", id: "welcome") {
            RFMappingWelcomeWindow()
        }
        .defaultSize(width: 480, height: 632)
        .windowStyle(.hiddenTitleBar)
        .windowResizability(.contentSize)
        .defaultLaunchBehavior(.presented)
        .commands {
            RFMappingCommands()
        }

        WindowGroup("RF Map Viewer", id: "rf-map-document", for: DocumentWindowRequest.self) { request in
            RFMappingWindow(request: request.wrappedValue)
        }
        .defaultSize(width: 1440, height: 900)
        .windowStyle(.titleBar)
        .windowResizability(.contentMinSize)
        .defaultLaunchBehavior(.suppressed)

        WindowGroup("Figure Export Composer", for: FigureExportRequest.self) { request in
            FigureExportWindow(request: request.wrappedValue)
        }
        .defaultSize(width: 1280, height: 860)
        .windowResizability(.contentMinSize)
        .defaultLaunchBehavior(.suppressed)
    }
}

private struct RFMappingWelcomeWindow: View {
    @Environment(\.openWindow) private var openWindow
    @State private var isImporting = false
    @State private var isOpening = false

    var body: some View {
        WelcomeView(openDocument: { isImporting = true }, openRecent: openDocument)
            .frame(width: 480, height: 632)
            .ignoresSafeArea()
            .background(WelcomeWindowRegistration(dismissImporter: { isImporting = false }))
            .focusedSceneValue(\.rfMappingOpenActions, RFMappingOpenActions(
                openRFMap: { isImporting = true }, openRecent: openDocument
            ))
            .overlay {
                if isOpening {
                    ZStack {
                        Color.white.opacity(0.8)
                        ProgressView()
                    }
                }
            }
            .fileImporter(
                isPresented: $isImporting,
                allowedContentTypes: UTType.rfMappingReadableTypes,
                allowsMultipleSelection: true
            ) { result in
                switch result {
                case .success(let urls):
                    urls.forEach(openDocument)
                case .failure(let error):
                    if (error as? CocoaError)?.code != .userCancelled {
                        NSAlert(error: error).runModal()
                    }
                }
            }
            .task {
                WindowRouter.shared.install { request in
                    openWindow(id: "rf-map-document", value: request)
                }
            }
    }

    private func openDocument(_ url: URL) {
        isImporting = false
        isOpening = true
        Task { @MainActor in
            _ = await WindowRouter.shared.openAsync(url)
            isOpening = false
        }
    }
}

@MainActor
private struct FigureExportWindow: View {
    private let request: FigureExportRequest?
    @State private var workspace: FigureExportWorkspace?

    init(request: FigureExportRequest?) {
        self.request = request
        let seed = request.flatMap { FigureExportWindowRegistry.shared.seed(for: $0) }
        _workspace = State(initialValue: seed.map { FigureExportWorkspace(seed: $0) })
    }

    var body: some View {
        Group {
            if let workspace {
                FigureExportComposerView(workspace: workspace)
            } else {
                ContentUnavailableView {
                    Label("Figure export unavailable", systemImage: "doc.badge.ellipsis")
                } description: {
                    Text("Return to a loaded RF viewer and choose Export Figures again.")
                }
            }
        }
        .frame(minWidth: 1020, minHeight: 700)
        .onDisappear {
            if let request { FigureExportWindowRegistry.shared.release(request) }
        }
    }
}

private struct RFMappingWindow: View {
    @Environment(\.openWindow) private var openWindow
    @State private var store: RFMappingStore
    @State private var pairingCoordinator: WindowPairingCoordinator
    @State private var pairingWindowID: UUID
    private let initialURL: URL?

    init(request: DocumentWindowRequest?) {
        let url = request.map { URL(fileURLWithPath: $0.path) }
        let prepared = request.flatMap { WindowRouter.shared.takePreparedDocument(for: $0.id) }
        let store = RFMappingStore(
            initialData: prepared,
            loadDefault: false
        )
        _store = State(initialValue: store)
        _pairingCoordinator = State(initialValue: WindowPairingCoordinator.shared)
        _pairingWindowID = State(initialValue: UUID())
        initialURL = prepared == nil ? url : nil
    }

    var body: some View {
        ContentView(
            store: store,
            pairingCoordinator: pairingCoordinator,
            pairingWindowID: pairingWindowID,
            openFigureExporter: openFigureExporter,
            openRFMapInNewWindow: openDocument
        )
        .frame(minWidth: 1120, minHeight: 720)
        .navigationTitle(store.windowTitle)
        .focusedSceneValue(\.rfMappingCommands, commandActions)
        .focusedSceneValue(\.rfMappingOpenActions, RFMappingOpenActions(
            openRFMap: { store.isImporting = true }, openRecent: openDocument
        ))
        .background {
            if store.hasData { WindowShortcutMonitor(actions: commandActions) }
        }
        .background(WindowCloseObserver {
            store.cancelPendingLoads()
            pairingCoordinator.unregister(id: pairingWindowID)
        })
        .onAppear {
            pairingCoordinator.register(store, id: pairingWindowID)
            if store.hasData { WindowRouter.shared.documentDidAppear() }
        }
        .onDisappear {
            pairingCoordinator.unregister(id: pairingWindowID)
        }
        .onChange(of: store.viewerSyncState) { _, state in
            pairingCoordinator.synchronizedStateDidChange(state, from: pairingWindowID)
        }
        .onChange(of: store.data?.url, initial: true) { _, url in
            if let url {
                RecentDocuments.shared.record(url)
                WindowRouter.shared.documentDidAppear()
            }
        }
        .task {
            WindowRouter.shared.install { request in
                openWindow(id: "rf-map-document", value: request)
            }
            if let initialURL, !store.hasData {
                _ = await store.loadJSONAsync(initialURL)
            }
        }
    }

    private var commandActions: RFMappingCommandActions {
        RFMappingCommandActions(
            exportFigures: openFigureExporter,
            exportDisplayed: store.prepareExport,
            previousUnit: { store.stepUnit(-1) },
            nextUnit: { store.stepUnit(1) },
            previousBin: { store.stepBin(-1) },
            nextBin: { store.stepBin(1) },
            decreaseResolution: { store.stepTimeResolution(1.0) },
            increaseResolution: { store.stepTimeResolution(-1.0) },
            showFullRange: store.handleEscape,
            selectTab: store.selectTab,
            toggleFlipY: { store.flipY.toggle() },
            toggleSpatialFormat: {
                store.spatialPlotFormat = store.spatialPlotFormat == .rectangular
                    ? .polar
                    : .rectangular
            },
            cyclePalette: store.cyclePalette,
            toggleSubtract: { store.setRFSubtractEnabled(!store.rfSubtractEnabled) },
            toggleDisplayOptions: { store.showDisplayOptions.toggle() },
            toggleFilteredUnits: { store.setRFUnitQualityFilterEnabled(!store.rfFilterUnitsWithZeroBins) }
        )
    }

    private func openDocument(_ url: URL) {
        Task { @MainActor in
            _ = await WindowRouter.shared.openAsync(url)
        }
    }

    private func openFigureExporter() {
        guard let request = FigureExportWindowRegistry.shared.prepare(from: store) else {
            store.errorMessage = "Load an RF dataset and wait for all units to cache before opening Figure Export."
            return
        }
        openWindow(value: request)
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

@MainActor
private struct RFMappingCommands: Commands {
    @FocusedValue(\.rfMappingCommands) private var actions
    @FocusedValue(\.rfMappingOpenActions) private var openActions
    @Environment(\.openWindow) private var openWindow
    @State private var recents = RecentDocuments.shared

    var body: some Commands {
        CommandGroup(replacing: .newItem) {
            Button("Open RF Map…") { openActions?.openRFMap() }
                .keyboardShortcut("o", modifiers: [.command])
                .disabled(openActions == nil)
            Menu("Open Recent") {
                ForEach(recents.urls, id: \.self) { url in
                    Button(url.lastPathComponent) {
                        if let openActions {
                            openActions.openRecent(url)
                        } else {
                            Task { @MainActor in _ = await WindowRouter.shared.openAsync(url) }
                        }
                    }
                    .help(url.path)
                }
                if recents.urls.isEmpty { Text("No Recent Documents") }
                Divider()
                Button("Clear Recent Documents", action: recents.clear)
                    .disabled(recents.urls.isEmpty)
            }
            Divider()
            Button("Welcome to RF Map Viewer") {
                recents.refresh()
                WindowRouter.shared.showWelcome { openWindow(id: "welcome") }
            }
        }

        CommandGroup(after: .saveItem) {
            Button("Export Figures…") { actions?.exportFigures() }
                .keyboardShortcut("e", modifiers: [.command])
                .disabled(actions == nil)
            Button("Export Displayed CSV…") { actions?.exportDisplayed() }
                .keyboardShortcut("e", modifiers: [.command, .shift])
                .disabled(actions == nil)
        }

        CommandMenu("Navigate") {
            Group {
                Button("Previous Unit (← or [)") { actions?.previousUnit() }
                Button("Next Unit (→ or ])") { actions?.nextUnit() }

                Divider()

                Button("Previous Timeline Bin (↑)") { actions?.previousBin() }
                Button("Next Timeline Bin (↓)") { actions?.nextBin() }
                Button("Coarser Time Resolution (Shift-,)") { actions?.decreaseResolution() }
                Button("Finer Time Resolution (Shift-.)") { actions?.increaseResolution() }

                Divider()

                Button("Clear Selection / Show Full Time Range (Esc)") { actions?.showFullRange() }
            }
            .disabled(actions == nil)
        }

        CommandMenu("View") {
            Group {
                ForEach(Array(PlotTab.allCases.enumerated()), id: \.element) { index, tab in
                    Button("\(tab.rawValue) (\(index + 1))") { actions?.selectTab(index) }
                }
                Divider()
                Button("Invert Y (F)") { actions?.toggleFlipY() }
                Button("Toggle Rectangle / Polar (P)") { actions?.toggleSpatialFormat() }
                Button("Cycle Palette (Shift-P)") { actions?.cyclePalette() }
                Button("Subtract RF Windows (A − B) (-)") { actions?.toggleSubtract() }
                Button("Show / Hide Display Options (D)") { actions?.toggleDisplayOptions() }
                Button("Show / Hide Filtered Units") { actions?.toggleFilteredUnits() }
                    .keyboardShortcut(".", modifiers: [.command, .shift])
            }
            .disabled(actions == nil)
        }

        CommandGroup(after: .help) {
            Button("RF Map Viewer Keyboard Shortcuts (?)") { showKeyboardShortcuts() }
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let windowRouter: WindowRouter

    override convenience init() {
        self.init(windowRouter: .shared)
    }

    init(windowRouter: WindowRouter) {
        self.windowRouter = windowRouter
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }

    func application(_ sender: NSApplication, openFiles filenames: [String]) {
        windowRouter.openExternal(filenames.map { URL(fileURLWithPath: $0) }) { succeeded in
            sender.reply(toOpenOrPrint: succeeded ? .success : .failure)
        }
    }

    func application(_ sender: NSApplication, openFile filename: String) -> Bool {
        // NSDocumentController uses this delegate entry point for native recents.
        windowRouter.openExternal([URL(fileURLWithPath: filename)]) { _ in }
        return true
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        // SwiftUI's application delegate routes Launch Services through the
        // URL callback, which takes precedence over the legacy openFiles one.
        windowRouter.openExternal(urls) { _ in }
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
                if current is NSTextField || current is NSComboBox || current is NSStepper {
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
                case "p": actions.toggleSpatialFormat(); return true
                case "-": actions.toggleSubtract(); return true
                case "d": actions.toggleDisplayOptions(); return true
                case "?": showKeyboardShortcuts(); return true
                case "1", "2", "3":
                    actions.selectTab(Int(character!)! - 1)
                    return true
                default: return false
                }
            }

            if modifiers == [.shift] {
                if character == "," || character == "<" { actions.decreaseResolution(); return true }
                if character == "." || character == ">" { actions.increaseResolution(); return true }
                if character == "p" { actions.cyclePalette(); return true }
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
    alert.messageText = "RF Map Viewer Keyboard Shortcuts"
    alert.informativeText = """
    ← / →   Previous / next unit
    ↑ / ↓   Previous / next timeline bin
    Shift+, / Shift+.   Coarser / finer time resolution (one source bin)
    1–3   Switch plot tab
    F   Invert Y
    P   Toggle rectangular / polar layout
    Shift-P   Cycle palette
    -   Subtract RF windows (A − B)
    D   Show / hide display options
    Command-Shift-.   Show / hide filtered units
    Esc   Close waveform zoom, clear probe selection, then show full time range
    [ / ]   Previous / next unit
    Command-O   Open RF map in a new window
    Command-E   Open Figure Export Composer
    Shift-Command-E   Export displayed CSV matrix
    Command-W   Close current window
    """
    alert.addButton(withTitle: "OK")
    alert.runModal()
}
