import AppKit
import SwiftUI

struct WelcomeWindowContent<Content: View>: View {
    @ViewBuilder let content: () -> Content
    @State private var titlebarInset: CGFloat = 0

    var body: some View {
        GeometryReader { geometry in
            content()
                .ignoresSafeArea()
                .onChange(of: geometry.safeAreaInsets.top, initial: true) { _, inset in
                    titlebarInset = inset
                }
        }
        // Hidden titlebars still contribute a safe area to the outer window size.
        .frame(width: 480, height: 632 - titlebarInset)
    }
}

struct WelcomeView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.controlActiveState) private var controlActiveState
    let openDocument: () -> Void
    let openRecent: (URL) -> Void
    @State private var recents = RecentDocuments.shared
    @State private var selectedURL: URL?
    @FocusState private var focusedControl: Control?

    private enum Control: Hashable {
        case close, open, recents
    }

    var body: some View {
        VStack(spacing: 0) {
            Image(nsImage: NSWorkspace.shared.icon(forFile: Bundle.main.bundlePath))
                .resizable()
                .interpolation(.high)
                .frame(width: 128, height: 128)
                .accessibilityHidden(true)
            Text("RF Map Viewer")
                .font(.system(size: 18, weight: .bold))
                .foregroundStyle(Color(red: 32.0 / 255.0, green: 32.0 / 255.0, blue: 35.0 / 255.0))
                .frame(height: 22)
            Button(action: openDocument) {
                Text("Open…")
                    .font(.system(size: 13))
                    .foregroundStyle(Color(white: 36.0 / 255.0))
                    .frame(maxWidth: .infinity)
                    .frame(height: 36)
            }
            .buttonStyle(WelcomeButtonStyle(close: false, focused: focusedControl == .open))
            .focused($focusedControl, equals: .open)
            .focusEffectDisabled()
            .onKeyPress(.return) {
                openDocument()
                return .handled
            }
            .padding(.top, 30)

            recentDocuments
                .frame(height: 278)
                .background(Color(white: 247.0 / 255.0), in: RoundedRectangle(cornerRadius: 16))
                .clipShape(RoundedRectangle(cornerRadius: 16))
                .padding(.top, 16)
            Spacer(minLength: 28)
        }
        .frame(width: 360)
        .padding(.top, 62)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(.white)
        .overlay(alignment: .topLeading) {
            Button(action: { dismiss() }) {
                Path { path in
                    path.move(to: CGPoint(x: 12, y: 12))
                    path.addLine(to: CGPoint(x: 24, y: 24))
                    path.move(to: CGPoint(x: 24, y: 12))
                    path.addLine(to: CGPoint(x: 12, y: 24))
                }
                    .stroke(Color(white: 165.0 / 255.0), lineWidth: 1.5)
                    .frame(width: 36, height: 36)
            }
            .buttonStyle(WelcomeButtonStyle(close: true, focused: focusedControl == .close))
            .focused($focusedControl, equals: .close)
            .focusEffectDisabled()
            .onKeyPress(.return) {
                dismiss()
                return .handled
            }
            .accessibilityLabel("Close Window")
            .padding(20)
        }
        .preferredColorScheme(.light)
        .onAppear(perform: recents.refresh)
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            recents.refresh()
        }
        .onChange(of: recents.urls, initial: true) { _, urls in
            if let selectedURL, urls.contains(selectedURL) { return }
            selectedURL = urls.first
        }
    }

    private var recentDocuments: some View {
        ScrollViewReader { scroll in
            ScrollView(.vertical) {
                LazyVStack(spacing: 0) {
                    ForEach(recents.urls, id: \.self) { url in
                        recentRow(url)
                            .id(url)
                            .onTapGesture(count: 2) {
                                select(url)
                                openRecent(url)
                            }
                            .onTapGesture { select(url) }
                    }
                }
            }
            .focusable()
            .focused($focusedControl, equals: .recents)
            .focusEffectDisabled()
            .onKeyPress(keys: [.upArrow, .downArrow, .home, .end, .return]) { press in
                guard !recents.urls.isEmpty else { return .ignored }
                let index = selectedURL.flatMap { recents.urls.firstIndex(of: $0) } ?? 0
                switch press.key {
                case .upArrow: selectedURL = recents.urls[max(0, index - 1)]
                case .downArrow: selectedURL = recents.urls[min(recents.urls.count - 1, index + 1)]
                case .home: selectedURL = recents.urls.first
                case .end: selectedURL = recents.urls.last
                case .return: openRecent(recents.urls[index])
                default: return .ignored
                }
                return .handled
            }
            .onChange(of: selectedURL) { _, url in
                if let url { scroll.scrollTo(url) }
            }
        }
        .padding(8)
    }

    private func recentRow(_ url: URL) -> some View {
        let path = (url.deletingLastPathComponent().path as NSString).abbreviatingWithTildeInPath
        return HStack(spacing: 12) {
            Image(nsImage: NSWorkspace.shared.icon(forFile: url.path))
                .resizable()
                .frame(width: 24, height: 24)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 0) {
                Text(url.lastPathComponent)
                    .font(.system(size: 13))
                    .foregroundStyle(Color(red: 37.0 / 255.0, green: 37.0 / 255.0, blue: 40.0 / 255.0))
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .frame(height: 18)
                Text(path)
                    .font(.system(size: 11))
                    .foregroundStyle(Color(red: 116.0 / 255.0, green: 116.0 / 255.0, blue: 122.0 / 255.0))
                    .lineLimit(1)
                    .truncationMode(.head)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .frame(height: 16)
            }
        }
        .padding(.leading, 12)
        .padding(.trailing, 10)
        .frame(height: 48)
        .background(alignment: .top) {
            if selectedURL == url {
                RoundedRectangle(cornerRadius: 10)
                    .fill(focusedControl == .recents && controlActiveState == .key
                        ? Color(red: 220.0 / 255.0, green: 232.0 / 255.0, blue: 248.0 / 255.0)
                        : Color(white: 222.0 / 255.0))
                    .frame(height: 47)
            }
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(url.lastPathComponent)
        .accessibilityValue(path)
        .accessibilityAddTraits(selectedURL == url ? [.isButton, .isSelected] : .isButton)
        .accessibilityAction { openRecent(url) }
        .accessibilityAction(named: "Select") { select(url) }
    }

    private func select(_ url: URL) {
        selectedURL = url
        focusedControl = .recents
    }
}

private struct WelcomeButtonStyle: ButtonStyle {
    let close: Bool
    let focused: Bool

    func makeBody(configuration: Configuration) -> some View {
        let shape = RoundedRectangle(cornerRadius: 18)
        return configuration.label
            .background(Color(white: (configuration.isPressed ? 222.0 : close ? 248.0 : 236.0) / 255.0), in: shape)
            .overlay {
                if focused && !configuration.isPressed {
                    shape.strokeBorder(Color(red: 124.0 / 255.0, green: 168.0 / 255.0, blue: 232.0 / 255.0), lineWidth: 1.5)
                }
            }
            .contentShape(shape)
    }
}

struct WelcomeWindowRegistration: NSViewRepresentable {
    let dismissImporter: () -> Void

    func makeNSView(context: Context) -> RegistrationView {
        let view = RegistrationView()
        view.dismissImporter = dismissImporter
        return view
    }

    func updateNSView(_ view: RegistrationView, context: Context) {
        view.dismissImporter = dismissImporter
        view.registerWindow()
    }

    final class RegistrationView: NSView {
        var dismissImporter: () -> Void = {}

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            registerWindow()
        }

        override func hitTest(_ point: NSPoint) -> NSView? { nil }

        func registerWindow() {
            guard let window else { return }
            WindowRouter.shared.registerWelcomeWindow(window, dismissImporter: dismissImporter)
            window.appearance = NSAppearance(named: .aqua)
            window.backgroundColor = .white
            window.standardWindowButton(.closeButton)?.isHidden = true
            window.standardWindowButton(.miniaturizeButton)?.isHidden = true
            window.standardWindowButton(.zoomButton)?.isHidden = true
        }
    }
}
