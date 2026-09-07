import Foundation
import TuningCurveCore

// Keep the viewer's existing internal API stable while sharing all parsing and
// processing code with the standalone comparison app.
typealias HDTuningError = TuningCurveCore.HDTuningError
typealias HDTuningUnit = TuningCurveCore.HDTuningUnit
typealias ProcessedHDCurve = TuningCurveCore.ProcessedHDCurve
typealias HDTuningData = TuningCurveCore.HDTuningData

enum HDTuningDiscovery {
    private static let probePattern = try! NSRegularExpression(
        pattern: #"probe[\s_-]*([ab])(?:\b|[_-])"#,
        options: [.caseInsensitive]
    )
    private static let sessionPattern = try! NSRegularExpression(
        pattern: #"^(\d{6,8})_(\d+)$"#
    )

    static func probeName(forRFURL sourceURL: URL) -> String? {
        var candidates = [sourceURL.deletingPathExtension().lastPathComponent]
        var current = sourceURL.deletingLastPathComponent()
        while current.path != "/" && !current.path.isEmpty {
            candidates.append(current.lastPathComponent)
            let parent = current.deletingLastPathComponent()
            if parent == current { break }
            current = parent
        }
        for candidate in candidates {
            let range = NSRange(candidate.startIndex..<candidate.endIndex, in: candidate)
            guard let match = probePattern.firstMatch(in: candidate, range: range),
                  let letterRange = Range(match.range(at: 1), in: candidate) else { continue }
            return "Probe\(candidate[letterRange].uppercased())"
        }
        return nil
    }

    /// Resolves the requested positive tuning-curve session exactly. Passing
    /// `nil` preserves the legacy earliest-session search used by callers that
    /// have not opted into an explicit preference.
    static func discover(
        forRFURL sourceURL: URL,
        fileManager: FileManager = .default,
        sessionIndex: Int? = nil
    ) -> URL? {
        if let sessionIndex, sessionIndex < 1 { return nil }
        guard let probe = probeName(forRFURL: sourceURL) else { return nil }
        var sessionURL: URL?
        var recordingDate: String?
        var current = sourceURL.deletingLastPathComponent()
        while current.path != "/" && !current.path.isEmpty {
            if let components = sessionComponents(current.lastPathComponent) {
                sessionURL = current
                recordingDate = components.date
                break
            }
            let parent = current.deletingLastPathComponent()
            if parent == current { break }
            current = parent
        }
        guard let sessionURL, let recordingDate else { return nil }
        let parent = sessionURL.deletingLastPathComponent()
        guard let siblings = try? fileManager.contentsOfDirectory(
            at: parent,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        ) else { return nil }
        let matching = siblings.compactMap { sibling -> (Int, URL)? in
            guard let values = try? sibling.resourceValues(forKeys: [.isDirectoryKey]),
                  values.isDirectory == true,
                  let components = sessionComponents(sibling.lastPathComponent),
                  components.date == recordingDate else { return nil }
            return (components.index, sibling)
        }.sorted { $0.0 < $1.0 }

        let candidates = sessionIndex.map { requested in
            matching.filter { $0.0 == requested }
        } ?? matching
        for (_, sibling) in candidates {
            let directory = sibling
                .appendingPathComponent("data", isDirectory: true)
                .appendingPathComponent("tuning_curves", isDirectory: true)
                .appendingPathComponent(probe, isDirectory: true)
            for filename in ["tuning_curves.tc", "tuning_curves.json"] {
                let candidate = directory.appendingPathComponent(filename)
                if fileManager.fileExists(atPath: candidate.path) { return candidate }
            }
        }
        return nil
    }

    static func isSessionName(_ name: String) -> Bool {
        sessionComponents(name) != nil
    }

    private static func sessionComponents(_ name: String) -> (date: String, index: Int)? {
        let range = NSRange(name.startIndex..<name.endIndex, in: name)
        guard let match = sessionPattern.firstMatch(in: name, range: range),
              let dateRange = Range(match.range(at: 1), in: name),
              let indexRange = Range(match.range(at: 2), in: name),
              let index = Int(name[indexRange]) else { return nil }
        return (String(name[dateRange]), index)
    }
}
