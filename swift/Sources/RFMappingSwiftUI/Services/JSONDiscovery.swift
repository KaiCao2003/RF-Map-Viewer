import Foundation

enum JSONDiscovery {
    static let defaultJSONDirectory = "data"
    static let defaultJSONName = "unitsSpikeCounts_260701_1.json"
    private static let choiceDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm"
        return formatter
    }()

    static func candidateRoots() -> [URL] {
        var roots: [URL] = []
        let fileManager = FileManager.default
        let isPackagedApplication = Bundle.main.bundleURL.pathExtension.lowercased() == "app"

        if let resourceURL = Bundle.main.resourceURL {
            roots.append(resourceURL)
        }
        if !isPackagedApplication {
            roots.append(URL(fileURLWithPath: fileManager.currentDirectoryPath))
        }

        var seen: Set<String> = []
        return roots.filter { url in
            let key = url.standardizedFileURL.path
            guard !seen.contains(key) else { return false }
            seen.insert(key)
            return true
        }
    }

    static func discoverJSONFiles(root: URL? = nil, currentURL: URL? = nil) -> [URL] {
        let roots = root.map { [$0] } ?? candidateRoots()
        var candidates: [URL] = []
        let fileManager = FileManager.default

        for root in roots {
            let dataDir = root.appendingPathComponent(defaultJSONDirectory, isDirectory: true)
            for folder in [dataDir, root] {
                guard let contents = try? fileManager.contentsOfDirectory(
                    at: folder,
                    includingPropertiesForKeys: [.contentModificationDateKey, .isRegularFileKey],
                    options: [.skipsHiddenFiles]
                ) else { continue }
                candidates.append(contentsOf: contents.filter(
                    RFMappingFileTypes.isDiscoverableRFMappingURL
                ))
            }
        }

        if let currentURL {
            candidates.append(currentURL)
        }

        var unique: [String: URL] = [:]
        for candidate in candidates where isRegularFile(candidate) {
            unique[candidate.standardizedFileURL.path] = candidate.standardizedFileURL
        }

        return unique.values
            .map { (url: $0, date: modificationDate($0)) }
            .sorted { lhs, rhs in
                if lhs.date == rhs.date {
                    return lhs.url.lastPathComponent < rhs.url.lastPathComponent
                }
                return lhs.date > rhs.date
            }
            .map(\.url)
    }

    static func latestJSONURL() -> URL? {
        if let latest = discoverJSONFiles().first {
            return latest
        }
        for root in candidateRoots() {
            let fallback = root
                .appendingPathComponent(defaultJSONDirectory, isDirectory: true)
                .appendingPathComponent(defaultJSONName)
            if isRegularFile(fallback) {
                return fallback
            }
        }
        return nil
    }

    static func shortLabel(for url: URL, relativeTo roots: [URL] = candidateRoots()) -> String {
        let path = url.standardizedFileURL.path
        for root in roots {
            let rootPath = root.standardizedFileURL.path
            if path.hasPrefix(rootPath + "/") {
                return String(path.dropFirst(rootPath.count + 1))
            }
        }
        return url.lastPathComponent
    }

    static func choiceLabel(for url: URL) -> String {
        let date = modificationDate(url)
        let stamp = date == .distantPast ? "" : "  \(choiceDateFormatter.string(from: date))"
        return "\(shortLabel(for: url))\(stamp)"
    }

    static func modificationDate(_ url: URL) -> Date {
        let values = try? url.resourceValues(forKeys: [.contentModificationDateKey])
        return values?.contentModificationDate ?? .distantPast
    }

    private static func isRegularFile(_ url: URL) -> Bool {
        let values = try? url.resourceValues(forKeys: [.isRegularFileKey])
        return values?.isRegularFile == true
    }
}
