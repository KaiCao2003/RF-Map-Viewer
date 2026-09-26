import AppKit
import SwiftUI

struct WelcomeView: View {
    @Environment(\.dismiss) private var dismiss
    let openDocument: () -> Void
    let openRecent: (URL) -> Void
    @State private var recents = RecentDocuments.shared
    @State private var selectedURL: URL?

    var body: some View {
        VStack(spacing: 0) {
            Image(nsImage: NSApplication.shared.applicationIconImage)
                .resizable()
                .interpolation(.high)
                .frame(width: 128, height: 128)
                .accessibilityHidden(true)
            Text("RF Map Viewer")
                .font(.system(size: 18, weight: .semibold))
                .frame(height: 22)
            Button(action: openDocument) {
                Text("Open…")
                    .font(.system(size: 13))
                    .frame(maxWidth: .infinity)
                    .frame(height: 36)
                    .background(Color(white: 236.0 / 255.0), in: Capsule())
            }
            .buttonStyle(.plain)
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
                Image(systemName: "xmark")
                    .font(.system(size: 14, weight: .light))
                    .foregroundStyle(.secondary)
                    .frame(width: 36, height: 36)
                    .background(Color(white: 248.0 / 255.0), in: Circle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Close Window")
            .padding(20)
        }
        .preferredColorScheme(.light)
        .onAppear(perform: recents.refresh)
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            recents.refresh()
        }
        .onChange(of: recents.urls) { _, urls in
            if let selectedURL, !urls.contains(selectedURL) {
                self.selectedURL = nil
            }
        }
    }

    private var recentDocuments: some View {
        List(selection: $selectedURL) {
            ForEach(recents.urls, id: \.self) { url in
                HStack(spacing: 10) {
                    Image(nsImage: NSWorkspace.shared.icon(forFile: url.path))
                        .resizable()
                        .frame(width: 24, height: 24)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(url.lastPathComponent)
                            .font(.system(size: 13, weight: .semibold))
                            .lineLimit(1)
                        Text((url.deletingLastPathComponent().path as NSString).abbreviatingWithTildeInPath)
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }
                    Spacer(minLength: 0)
                }
                .frame(height: 47)
                .contentShape(Rectangle())
                .tag(url)
                .listRowSeparator(.hidden)
                .onTapGesture(count: 2) { openRecent(url) }
                .accessibilityAction { openRecent(url) }
            }
        }
        .listStyle(.inset)
        .scrollContentBackground(.hidden)
        .onKeyPress(.return) {
            guard let selectedURL else { return .ignored }
            openRecent(selectedURL)
            return .handled
        }
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
            window.isMovableByWindowBackground = true
            window.standardWindowButton(.closeButton)?.isHidden = true
            window.standardWindowButton(.miniaturizeButton)?.isHidden = true
            window.standardWindowButton(.zoomButton)?.isHidden = true
        }
    }
}
