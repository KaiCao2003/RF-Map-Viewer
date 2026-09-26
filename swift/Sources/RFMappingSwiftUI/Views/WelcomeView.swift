import AppKit
import SwiftUI

struct WelcomeView: View {
    let openDocument: () -> Void
    let openRecent: (URL) -> Void
    @State private var recents = RecentDocuments.shared
    @State private var selectedURL: URL?

    var body: some View {
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                Image(nsImage: NSApplication.shared.applicationIconImage)
                    .resizable()
                    .interpolation(.high)
                    .frame(width: 104, height: 104)
                    .accessibilityHidden(true)
                Text("RF Map Viewer")
                    .font(.system(size: 22, weight: .semibold))
                    .padding(.top, 18)
                Button(action: openDocument) {
                    Label("Open RF Map…", systemImage: "folder")
                        .frame(width: 176)
                }
                .controlSize(.large)
                .padding(.top, 30)
            }
            .frame(width: 320)
            .frame(maxHeight: .infinity)
            .background(Color(nsColor: .windowBackgroundColor))

            Divider()

            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Text("Recent")
                        .font(.headline)
                    Spacer()
                    if !recents.urls.isEmpty {
                        Button("Clear Recent", action: recents.clear)
                            .buttonStyle(.plain)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(.horizontal, 10)

                List(selection: $selectedURL) {
                    ForEach(recents.urls, id: \.self) { url in
                        HStack(spacing: 12) {
                            Image(nsImage: NSWorkspace.shared.icon(forFile: url.path))
                                .resizable()
                                .frame(width: 30, height: 30)
                                .accessibilityHidden(true)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(url.lastPathComponent)
                                    .font(.body.weight(.medium))
                                    .lineLimit(1)
                                Text((url.deletingLastPathComponent().path as NSString).abbreviatingWithTildeInPath)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                            }
                            Spacer(minLength: 0)
                        }
                        .padding(.vertical, 7)
                        .contentShape(Rectangle())
                        .tag(url)
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
                .overlay {
                    if recents.urls.isEmpty {
                        Text("No Recent Documents")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .padding(24)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color(nsColor: .controlBackgroundColor))
        }
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
            window.titleVisibility = isWelcome ? .hidden : .visible
            window.titlebarAppearsTransparent = isWelcome
            window.setContentSize(isWelcome
                ? NSSize(width: 800, height: 480)
                : NSSize(width: 1440, height: 900))
        }
    }
}
