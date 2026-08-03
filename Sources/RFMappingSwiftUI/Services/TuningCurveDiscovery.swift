import Foundation

enum TuningCurveDiscovery {
    private static let sessionExpression = try! NSRegularExpression(
        pattern: #"^(\d{6,8})_(\d+)$"#
    )
    private static let filenameProbeExpression = try! NSRegularExpression(
        pattern: #"(?:^|[\s_-])([ab])$"#,
        options: [.caseInsensitive]
    )
    private static let pathProbeExpression = try! NSRegularExpression(
        pattern: #"probe[\s_-]*([ab])(?:\b|[_-])"#,
        options: [.caseInsensitive]
    )

    static func probeName(for rfJSONURL: URL) -> String? {
        let stem = rfJSONURL.deletingPathExtension().lastPathComponent
        if let letter = firstCapture(in: stem, expression: filenameProbeExpression) {
            return "Probe\(letter.uppercased())"
        }

        let directoryParts = rfJSONURL
            .deletingLastPathComponent()
            .pathComponents
            .reversed()
        for part in [rfJSONURL.lastPathComponent] + directoryParts {
            if let letter = firstCapture(in: part, expression: pathProbeExpression) {
                return "Probe\(letter.uppercased())"
            }
        }
        return nil
    }

    /// Matches the Python viewer: find the earliest numbered session on the
    /// same recording day that contains tuning curves for the inferred probe.
    /// Cross-session attachment is intentional and is not treated as an error.
    static func discoverURL(
        for rfJSONURL: URL,
        fileManager: FileManager = .default
    ) -> URL? {
        guard let probeName = probeName(for: rfJSONURL) else { return nil }
        var candidate = rfJSONURL.deletingLastPathComponent()
        var session: (date: String, index: Int, url: URL)?
        while candidate.path != candidate.deletingLastPathComponent().path {
            if let parsed = parseSession(candidate.lastPathComponent) {
                session = (parsed.date, parsed.index, candidate)
                break
            }
            candidate.deleteLastPathComponent()
        }
        guard let session else { return nil }

        let dayRoot = session.url.deletingLastPathComponent()
        let siblings: [URL]
        do {
            siblings = try fileManager.contentsOfDirectory(
                at: dayRoot,
                includingPropertiesForKeys: [.isDirectoryKey],
                options: [.skipsHiddenFiles]
            )
        } catch {
            return nil
        }

        let orderedSessions = siblings.compactMap { sibling -> (Int, URL)? in
            guard let parsed = parseSession(sibling.lastPathComponent),
                  parsed.date == session.date,
                  isDirectory(sibling) else { return nil }
            return (parsed.index, sibling)
        }.sorted { lhs, rhs in
            if lhs.0 == rhs.0 {
                return lhs.1.path < rhs.1.path
            }
            return lhs.0 < rhs.0
        }

        for (_, sibling) in orderedSessions {
            let tuningURL = sibling
                .appendingPathComponent("data", isDirectory: true)
                .appendingPathComponent("tuning_curves", isDirectory: true)
                .appendingPathComponent(probeName, isDirectory: true)
                .appendingPathComponent("tuning_curves.json")
            if isRegularFile(tuningURL) {
                return tuningURL.resolvingSymlinksInPath().standardizedFileURL
            }
        }
        return nil
    }

    private static func parseSession(_ name: String) -> (date: String, index: Int)? {
        guard let date = capture(in: name, expression: sessionExpression, index: 1),
              let rawIndex = capture(in: name, expression: sessionExpression, index: 2),
              let index = Int(rawIndex) else { return nil }
        return (date, index)
    }

    private static func firstCapture(
        in text: String,
        expression: NSRegularExpression
    ) -> String? {
        capture(in: text, expression: expression, index: 1)
    }

    private static func capture(
        in text: String,
        expression: NSRegularExpression,
        index: Int
    ) -> String? {
        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        guard let match = expression.firstMatch(in: text, range: range),
              match.numberOfRanges > index,
              let captureRange = Range(match.range(at: index), in: text) else { return nil }
        return String(text[captureRange])
    }

    private static func isDirectory(_ url: URL) -> Bool {
        let values = try? url.resourceValues(forKeys: [.isDirectoryKey])
        return values?.isDirectory == true
    }

    private static func isRegularFile(_ url: URL) -> Bool {
        let values = try? url.resourceValues(forKeys: [.isRegularFileKey])
        return values?.isRegularFile == true
    }
}
