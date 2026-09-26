import AppKit
import Observation

@MainActor
@Observable
final class RecentDocuments {
    static let shared = RecentDocuments()

    private(set) var urls: [URL] = []

    init() {
        refresh()
    }

    func refresh() {
        urls = NSDocumentController.shared.recentDocumentURLs
    }

    func record(_ url: URL) {
        NSDocumentController.shared.noteNewRecentDocumentURL(url)
        refresh()
    }

    func clear() {
        NSDocumentController.shared.clearRecentDocuments(nil)
        refresh()
    }
}
