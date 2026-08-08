import Foundation

struct ProbeChannel: Equatable, Sendable {
    let channelID: Int
    let xMicrometers: Double
    let yMicrometers: Double
    let shankID: Int
}

struct ProbeUnitPosition: Equatable, Sendable {
    let unitID: Int
    let xMicrometers: Double
    let yMicrometers: Double
}

/// Immutable companion geometry captured when a figure composer is opened.
/// Keeping the decoded values here prevents a later source-file change from
/// making live preview and final export disagree.
struct ProbeGeometry: Equatable, Sendable {
    let probeName: String
    let positionsURL: URL
    let channelsURL: URL?
    let channels: [ProbeChannel]
    let units: [ProbeUnitPosition]
}

struct ProbePlotPayload: Equatable, Sendable {
    let probeName: String
    let positionsURL: URL
    let channelsURL: URL?
    let channels: [ProbeChannel]
    /// Exactly the unit represented by this exported page. Other RF units are
    /// intentionally absent so a multi-unit export cannot leak their markers
    /// into the current unit's Probe plot.
    let unit: ProbeUnitPosition
}

struct ProbeGeometryPaths: Equatable, Sendable {
    let probeName: String
    let positionsURL: URL
    let channelsURL: URL?
}

enum ProbeGeometryError: LocalizedError, Equatable {
    case invalidCSV(String)
    case noRFUnits

    var errorDescription: String? {
        switch self {
        case .invalidCSV(let message):
            return message
        case .noRFUnits:
            return "positions.csv contains no unit IDs from this RF dataset's unitPool."
        }
    }
}

enum ProbeGeometryDiscovery {
    private static let recordingSessionPattern = try! NSRegularExpression(
        pattern: #"^\d{6,8}_\d+$"#
    )

    /// Locates geometry only inside the nearest recording's `data` boundary.
    /// Compact legacy exports without a recording/data hierarchy are limited
    /// to the RF JSON's own directory, avoiding an unbounded ancestor search.
    static func discover(
        forRFURL sourceURL: URL,
        fileManager: FileManager = .default
    ) -> ProbeGeometryPaths? {
        guard let probeName = HDTuningDiscovery.probeName(forRFURL: sourceURL) else {
            return nil
        }
        let source = sourceURL.standardizedFileURL
        let parents = ancestorDirectories(of: source)
        guard let sourceParent = parents.first else { return nil }

        let boundary: URL
        let searchBases: [URL]
        if let session = parents.first(where: { isRecordingSession($0.lastPathComponent) }) {
            boundary = parents.first(where: {
                $0.lastPathComponent == "data"
                    && $0.deletingLastPathComponent().standardizedFileURL
                        == session.standardizedFileURL
            }) ?? session
            searchBases = prefixThrough(boundary, in: parents)
        } else if let dataBoundary = parents.first(where: { $0.lastPathComponent == "data" }) {
            boundary = dataBoundary
            searchBases = prefixThrough(boundary, in: parents)
        } else {
            boundary = sourceParent
            searchBases = [sourceParent]
        }

        let resolvedBoundary = boundary.resolvingSymlinksInPath().standardizedFileURL
        func inScopeRegularFile(_ candidate: URL) -> URL? {
            var isDirectory: ObjCBool = false
            guard fileManager.fileExists(atPath: candidate.path, isDirectory: &isDirectory),
                  !isDirectory.boolValue else { return nil }
            let resolved = candidate.resolvingSymlinksInPath().standardizedFileURL
            let boundaryPath = resolvedBoundary.path
            guard resolved.path == boundaryPath
                    || resolved.path.hasPrefix(boundaryPath + "/") else { return nil }
            guard let values = try? resolved.resourceValues(forKeys: [.isRegularFileKey]),
                  values.isRegularFile == true else { return nil }
            return resolved
        }

        for base in searchBases {
            let candidates: [(URL, URL)] = [
                (
                    base.appendingPathComponent("spike_position", isDirectory: true)
                        .appendingPathComponent(probeName, isDirectory: true)
                        .appendingPathComponent("positions.csv"),
                    base.appendingPathComponent("waveform", isDirectory: true)
                        .appendingPathComponent(probeName, isDirectory: true)
                        .appendingPathComponent("channels.csv")
                ),
                (
                    base.appendingPathComponent(probeName, isDirectory: true)
                        .appendingPathComponent("positions.csv"),
                    base.appendingPathComponent(probeName, isDirectory: true)
                        .appendingPathComponent("channels.csv")
                ),
                (
                    base.appendingPathComponent("positions.csv"),
                    base.appendingPathComponent("channels.csv")
                ),
            ]
            for (positionsCandidate, channelsCandidate) in candidates {
                guard let positionsURL = inScopeRegularFile(positionsCandidate) else {
                    continue
                }
                return ProbeGeometryPaths(
                    probeName: probeName,
                    positionsURL: positionsURL,
                    channelsURL: inScopeRegularFile(channelsCandidate)
                )
            }
        }
        return nil
    }

    static func load(
        _ paths: ProbeGeometryPaths,
        rfUnitIDs: [Int]
    ) throws -> ProbeGeometry {
        let positions = try ProbeCSVTable(
            url: paths.positionsURL,
            requiredColumns: ["unit_index", "unit_id", "x_um", "y_um"]
        )
        var units: [ProbeUnitPosition] = []
        var seenUnitIDs: Set<Int> = []
        for (offset, row) in positions.rows.enumerated() {
            let rowNumber = offset + 2
            do {
                _ = try integer(row["unit_index"], label: "unit_index")
                let unitID = try integer(row["unit_id"], label: "unit_id")
                let x = try finiteDouble(row["x_um"], label: "unit x_um")
                let y = try finiteDouble(row["y_um"], label: "unit y_um")
                guard seenUnitIDs.insert(unitID).inserted else {
                    throw ProbeGeometryError.invalidCSV(
                        "Duplicate unit_id \(unitID) in positions.csv."
                    )
                }
                units.append(ProbeUnitPosition(
                    unitID: unitID,
                    xMicrometers: x,
                    yMicrometers: y
                ))
            } catch {
                if case ProbeGeometryError.invalidCSV(let message) = error,
                   message.hasPrefix("Duplicate unit_id") {
                    throw error
                }
                throw ProbeGeometryError.invalidCSV(
                    "Invalid positions.csv value on row \(rowNumber): "
                        + error.localizedDescription
                )
            }
        }

        let allowedUnitIDs = Set(rfUnitIDs)
        units = units.filter { allowedUnitIDs.contains($0.unitID) }
        guard !units.isEmpty else { throw ProbeGeometryError.noRFUnits }

        var channels: [ProbeChannel] = []
        var validatedChannelsURL: URL?
        if let channelsURL = paths.channelsURL {
            do {
                let table = try ProbeCSVTable(
                    url: channelsURL,
                    requiredColumns: [
                        "channel_index", "channel_id", "raw_channel_index",
                        "x_um", "y_um", "shank_id",
                    ]
                )
                for (offset, row) in table.rows.enumerated() {
                    let rowNumber = offset + 2
                    do {
                        _ = try integer(row["channel_index"], label: "channel_index")
                        _ = try integer(row["raw_channel_index"], label: "raw_channel_index")
                        let channelID = try integer(row["channel_id"], label: "channel_id")
                        let x = try finiteDouble(row["x_um"], label: "channel x_um")
                        let y = try finiteDouble(row["y_um"], label: "channel y_um")
                        let shankID = try integer(row["shank_id"], label: "shank_id")
                        channels.append(ProbeChannel(
                            channelID: channelID,
                            xMicrometers: x,
                            yMicrometers: y,
                            shankID: shankID
                        ))
                    } catch {
                        throw ProbeGeometryError.invalidCSV(
                            "Invalid channels.csv value on row \(rowNumber): "
                                + error.localizedDescription
                        )
                    }
                }
                validatedChannelsURL = channelsURL
            } catch {
                // Positions alone still form a scientifically useful layout.
                // A stale optional channel file must not hide valid units.
                channels = []
                validatedChannelsURL = nil
            }
        }

        return ProbeGeometry(
            probeName: paths.probeName,
            positionsURL: paths.positionsURL,
            channelsURL: validatedChannelsURL,
            channels: channels,
            units: units
        )
    }

    private static func ancestorDirectories(of fileURL: URL) -> [URL] {
        var result: [URL] = []
        var current = fileURL.deletingLastPathComponent().standardizedFileURL
        while true {
            result.append(current)
            let parent = current.deletingLastPathComponent().standardizedFileURL
            if parent.path == current.path { break }
            current = parent
        }
        return result
    }

    private static func prefixThrough(_ boundary: URL, in parents: [URL]) -> [URL] {
        guard let index = parents.firstIndex(where: {
            $0.standardizedFileURL == boundary.standardizedFileURL
        }) else { return [] }
        return Array(parents[...index])
    }

    private static func isRecordingSession(_ name: String) -> Bool {
        let range = NSRange(name.startIndex..<name.endIndex, in: name)
        return recordingSessionPattern.firstMatch(in: name, range: range) != nil
    }

    private static func finiteDouble(_ value: String?, label: String) throws -> Double {
        guard let value,
              let parsed = Double(value.trimmingCharacters(in: .whitespacesAndNewlines)),
              parsed.isFinite else {
            throw ProbeGeometryError.invalidCSV("\(label) must be a finite number.")
        }
        return parsed
    }

    private static func integer(_ value: String?, label: String) throws -> Int {
        let parsed = try finiteDouble(value, label: label)
        guard let integer = Int(exactly: parsed) else {
            throw ProbeGeometryError.invalidCSV("\(label) must be an in-range integer.")
        }
        return integer
    }
}

private struct ProbeCSVTable {
    let rows: [[String: String]]

    init(url: URL, requiredColumns: [String]) throws {
        let data: Data
        do {
            data = try Data(contentsOf: url, options: .mappedIfSafe)
        } catch {
            throw ProbeGeometryError.invalidCSV(
                "\(url.lastPathComponent) could not be read: \(error.localizedDescription)"
            )
        }
        guard var text = String(data: data, encoding: .utf8) else {
            throw ProbeGeometryError.invalidCSV(
                "\(url.lastPathComponent) must be UTF-8 encoded."
            )
        }
        if text.first == "\u{FEFF}" { text.removeFirst() }
        let parsed = try Self.parse(text, filename: url.lastPathComponent)
        guard let header = parsed.first, !header.isEmpty else {
            throw ProbeGeometryError.invalidCSV(
                "\(url.lastPathComponent) is missing a header."
            )
        }
        let duplicateColumns = Set(header.filter { name in
            header.filter { $0 == name }.count > 1
        }).sorted()
        guard duplicateColumns.isEmpty else {
            throw ProbeGeometryError.invalidCSV(
                "\(url.lastPathComponent) contains duplicate columns: "
                    + duplicateColumns.joined(separator: ", ") + "."
            )
        }
        let missing = requiredColumns.filter { !header.contains($0) }
        guard missing.isEmpty else {
            throw ProbeGeometryError.invalidCSV(
                "\(url.lastPathComponent) is missing required columns: "
                    + missing.joined(separator: ", ") + "."
            )
        }
        var decodedRows: [[String: String]] = []
        for (offset, fields) in parsed.dropFirst().enumerated() {
            if fields.allSatisfy({ $0.isEmpty }) { continue }
            guard fields.count == header.count else {
                throw ProbeGeometryError.invalidCSV(
                    "\(url.lastPathComponent) row \(offset + 2) has \(fields.count) "
                        + "fields; expected \(header.count)."
                )
            }
            decodedRows.append(Dictionary(uniqueKeysWithValues: zip(header, fields)))
        }
        rows = decodedRows
    }

    /// RFC 4180 field parser, including escaped quotes and newlines inside a
    /// quoted field. Probe files are small, so a direct scalar walk is ample.
    private static func parse(_ text: String, filename: String) throws -> [[String]] {
        var records: [[String]] = []
        var record: [String] = []
        var field = ""
        var inQuotes = false
        // Iterate Unicode scalars rather than extended grapheme clusters.
        // Swift treats CRLF as one `Character`, so Character iteration would
        // miss both the `\r` and `\n` cases below and fold the next CSV row
        // into the current field.
        let scalars = text.unicodeScalars
        var index = scalars.startIndex

        func finishField() {
            record.append(field)
            field.removeAll(keepingCapacity: true)
        }
        func finishRecord() {
            finishField()
            records.append(record)
            record.removeAll(keepingCapacity: true)
        }

        while index < scalars.endIndex {
            let character = scalars[index]
            let next = scalars.index(after: index)
            if inQuotes {
                if character == "\"" {
                    if next < scalars.endIndex, scalars[next] == "\"" {
                        field.append("\"")
                        index = scalars.index(after: next)
                        continue
                    }
                    inQuotes = false
                } else {
                    field.unicodeScalars.append(character)
                }
            } else {
                switch character {
                case "\"" where field.isEmpty:
                    inQuotes = true
                case ",":
                    finishField()
                case "\n":
                    finishRecord()
                case "\r":
                    finishRecord()
                    if next < scalars.endIndex, scalars[next] == "\n" {
                        index = scalars.index(after: next)
                        continue
                    }
                default:
                    field.unicodeScalars.append(character)
                }
            }
            index = next
        }
        guard !inQuotes else {
            throw ProbeGeometryError.invalidCSV(
                "\(filename) contains an unterminated quoted field."
            )
        }
        if !field.isEmpty || !record.isEmpty {
            finishRecord()
        }
        return records
    }
}
