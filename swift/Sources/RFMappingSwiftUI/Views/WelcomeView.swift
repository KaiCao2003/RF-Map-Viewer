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
                    .font(.system(size: 13, weight: .medium))
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .buttonBorderShape(.capsule)
            .controlSize(.large)
            .frame(height: 36)
            .padding(.top, 30)

            recentDocuments
                .frame(height: 278)
                .background(Color(nsColor: .windowBackgroundColor), in: RoundedRectangle(cornerRadius: 16))
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
        .ignoresSafeArea()
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

/// SwiftUI keeps a scene's initial size when its content changes. Resize once
/// when the welcome scene becomes a document, then leave user resizing alone.
struct ViewerWindowPresentation: NSViewRepresentable {
    let isWelcome: Bool

    func makeNSView(context: Context) -> PresentationView {
        let view = PresentationView()
        view.isWelcome = isWelcome
        return view
    }

    func updateNSView(_ view: PresentationView, context: Context) {
        view.isWelcome = isWelcome
        view.updatePresentation()
    }

    final class PresentationView: NSView {
        var isWelcome = true
        private var appliedWelcome: Bool?

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            updatePresentation()
        }

        override func hitTest(_ point: NSPoint) -> NSView? { nil }

        func updatePresentation() {
            guard let window, appliedWelcome != isWelcome else { return }
            appliedWelcome = isWelcome
            WindowRouter.shared.updateWelcomeWindow(window, isWelcome: isWelcome)
            if isWelcome {
                window.styleMask.insert(.fullSizeContentView)
                window.styleMask.remove(.resizable)
            } else {
                window.styleMask.remove(.fullSizeContentView)
                window.styleMask.insert(.resizable)
            }
            window.titleVisibility = isWelcome ? .hidden : .visible
            window.titlebarAppearsTransparent = isWelcome
            window.appearance = isWelcome ? NSAppearance(named: .aqua) : nil
            window.isMovableByWindowBackground = isWelcome
            window.backgroundColor = isWelcome ? .white : .windowBackgroundColor
            window.standardWindowButton(.closeButton)?.isHidden = isWelcome
            window.standardWindowButton(.miniaturizeButton)?.isHidden = isWelcome
            window.standardWindowButton(.zoomButton)?.isHidden = isWelcome
            window.setContentSize(isWelcome
                ? NSSize(width: 480, height: 632)
                : NSSize(width: 1440, height: 900))
        }
    }
}
