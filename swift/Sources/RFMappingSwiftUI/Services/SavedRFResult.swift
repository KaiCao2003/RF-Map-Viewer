import Foundation
import CoreFoundation

struct SavedRFUnitResult: Sendable {
    let mask: [[Bool]]
    let center: [[Bool]]
}

/// Saved detections are read-only companions. This reader never reconstructs an RF.
struct SavedRFResult: Sendable {
    let path: URL
    let sourceURL: URL
    let xPositions: [Double]
    let yPositions: [Double]
    let dimension: String
    let polarity: String
    let axis: String?
    let projectedFrom2D: Bool
    let timeRangeSeconds: [Double]
    let parameters: [String: RFMapJSONValue]
    let manifest: [String: RFMapJSONValue]
    let cacheKey: String
    let summaryWarning: String?
    let unitIDs: [Int]
    let mask: [[[Bool]]]
    let center: [[[Bool]]]
    private let sourceShape: [Int]
    private let summary: [String: RFMapJSONValue]?

    static let defaultLayoutPattern = #"(?<kind>regular|bar_\d+(?:\.\d+)?deg|(?:rotation|egocentric|allocentric_pixelbins)(?:_30)?|free_moving)/\k<kind>_\d{6}_\d+"#

    static func validateLayoutPattern(_ pattern: String) throws {
        guard !pattern.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw failure("Saved RF layout regex must not be empty.")
        }
        do { _ = try NSRegularExpression(pattern: pattern) }
        catch { throw failure("Saved RF layout regex is invalid: \(error.localizedDescription)") }
    }

    private static func usesKindDirectory(_ source: URL, layoutPattern: String) throws -> Bool {
        try validateLayoutPattern(layoutPattern)
        guard ["rfmap", "json"].contains(source.pathExtension.lowercased()) else { return false }
        let pair = source.deletingLastPathComponent().lastPathComponent + "/"
            + source.deletingPathExtension().lastPathComponent
        let expression = try NSRegularExpression(pattern: #"\A(?:"# + layoutPattern + #")\z"#)
        return expression.firstMatch(in: pair, range: NSRange(pair.startIndex..., in: pair)) != nil
    }

    static func resultURL(source: URL, dimension: String = "2d", polarity: String = "excitatory",
                          layoutPattern: String = defaultLayoutPattern) throws -> URL {
        guard ["1d", "2d"].contains(dimension), ["excitatory", "inhibitory"].contains(polarity) else {
            throw failure("Choose 1d/2d and excitatory/inhibitory.")
        }
        guard source.pathExtension.lowercased() != "npz" else {
            throw failure("Select the source RF document, not a result .npz.")
        }
        if try usesKindDirectory(source, layoutPattern: layoutPattern) {
            let suffix = polarity == "inhibitory" ? "inh" : "ext"
            return source.deletingLastPathComponent().appendingPathComponent("rf_\(dimension)_\(suffix).npz")
        }
        let stem = source.deletingPathExtension().lastPathComponent
            + (polarity == "inhibitory" ? "_inhibitory" : "")
            + (dimension == "1d" ? "_1d" : "")
        return source.deletingLastPathComponent().appendingPathComponent(stem + ".npz")
    }

    static func load(source: RFMappingData, dimension: String = "2d", polarity: String = "excitatory",
                     layoutPattern: String = defaultLayoutPattern) throws -> SavedRFResult {
        let path = try resultURL(source: source.url, dimension: dimension, polarity: polarity,
                                 layoutPattern: layoutPattern)
        let archive = try IndexedRFArchive(url: path)
        guard archive.arrayNames == ["schema_version", "mask_2d", "center_2d", "unit_ids", "manifest_json", "cache_key"] else {
            throw failure("Saved RF result fields do not match schema v1.")
        }
        let version = try archive.array(named: "schema_version")
        guard version.shape.isEmpty, try version.integerValues() == [1] else {
            throw failure("Unsupported saved RF result schema; expected version 1.")
        }
        let manifestText = try archive.array(named: "manifest_json").stringValue()
        let manifestBytes = Data(manifestText.utf8)
        guard isCanonicalObject(manifestText),
              let rawManifest = try JSONSerialization.jsonObject(with: manifestBytes) as? [String: Any] else {
            throw failure("Saved result manifest must be a canonical JSON object.")
        }
        let manifest = try JSONDecoder().decode([String: RFMapJSONValue].self, from: manifestBytes)
        let cacheKey = try archive.array(named: "cache_key").stringValue()
        let idArray = try archive.array(named: "unit_ids")
        guard idArray.shape.count == 1 else { throw failure("unit_ids must be a one-dimensional integer array.") }
        let ids = try idArray.integerValues()
        guard !ids.isEmpty, Set(ids).count == ids.count, Set(ids).isSubset(of: Set(source.unitPool)) else {
            throw failure("Saved unit IDs must be unique IDs from this RF document.")
        }
        func binaryVolume(_ name: String) throws -> (shape: [Int], values: [[[Bool]]]) {
            let array = try archive.array(named: name)
            let shape = array.shape.count == 2 ? [1] + array.shape : array.shape
            guard shape.count == 3, shape[0] == ids.count, shape.allSatisfy({ $0 > 0 }) else {
                throw failure("\(name) must be a binary (unit, y, x) array aligned with unit_ids.")
            }
            let numbers = try array.doubleValues(allowBoolean: true)
            guard numbers.allSatisfy({ $0 == 0 || $0 == 1 }) else { throw failure("\(name) must contain only 0 and 1.") }
            let volume = (0..<shape[0]).map { unit in
                (0..<shape[1]).map { y in
                    (0..<shape[2]).map { x in numbers[(unit * shape[1] + y) * shape[2] + x] == 1 }
                }
            }
            return (shape, volume)
        }
        let masks = try binaryVolume("mask_2d")
        let centers = try binaryVolume("center_2d")
        guard masks.shape == centers.shape else { throw failure("Saved masks and centers have different shapes.") }
        for index in ids.indices {
            let mask = masks.values[index].flatMap { $0 }
            let center = centers.values[index].flatMap { $0 }
            guard zip(mask, center).allSatisfy({ !$0.1 || $0.0 }),
                  center.contains(true) == mask.contains(true) else {
                throw failure("Each nonempty RF needs a center inside its mask.")
            }
        }
        guard rawManifest["schema_name"] as? String == "rfmapping-rf-result",
              rawManifest["grid_shape"] as? [Int] == Array(masks.shape.dropFirst()),
              rawManifest["storage_shape"] as? [Int] == masks.shape else {
            throw failure("Manifest schema/grid/storage shape differs from the saved arrays.")
        }
        guard let times = rawManifest["time_range_s"] as? [NSNumber], times.count == 2,
              times.allSatisfy({ CFGetTypeID($0) != CFBooleanGetTypeID() && $0.doubleValue.isFinite }) else {
            throw failure("Saved time range must contain two finite numbers.")
        }
        let range = times.map(\.doubleValue)
        guard source.timeBinEdges[0] <= range[0], range[0] < range[1], range[1] <= source.timeBinEdges.last!,
              range.allSatisfy({ boundary in source.timeBinEdges.contains { abs($0 - boundary) <= 1e-12 } }) else {
            throw failure("Saved time range does not match this RF document's time edges.")
        }
        guard case .object(let parameters) = manifest["parameters"],
              parameters["alternative"] == .string(polarity == "inhibitory" ? "less" : "greater") else {
            throw failure("Saved detection polarity differs from the selected result.")
        }
        var summary: [String: RFMapJSONValue]?
        var warning: String?
        do {
            summary = try readSummary(source: source, path: path, unitIDs: ids, manifest: manifest,
                                      dimension: dimension, polarity: polarity, layoutPattern: layoutPattern)
        } catch {
            warning = "Analysis summary ignored: \(error.localizedDescription)"
        }
        let declaredAxis = rawManifest["collapse_axis"] as? String
        let projected = dimension == "1d" && declaredAxis == nil
        var axis = declaredAxis
        let expectedShape: [Int]
        if dimension == "2d" {
            guard axis == nil else { throw failure("A 2-D result cannot have a collapsed axis.") }
            expectedShape = [source.nY, source.nX]
        } else if projected {
            guard case .object(let parameters) = summary?["parameters"], parameters["collapse_from_2d"] == .bool(true) else {
                throw failure("A legacy 1-D projection requires its matching analysis summary.")
            }
            axis = "x"
            expectedShape = [source.nY, source.nX]
        } else if axis == "x" {
            expectedShape = [1, source.nX]
        } else if axis == "y" {
            expectedShape = [source.nY, 1]
        } else {
            throw failure("Saved 1-D result has an unknown collapsed axis.")
        }
        guard Array(masks.shape.dropFirst()) == expectedShape else {
            throw failure("Saved result grid does not match this RF document's axes.")
        }
        // A direct 1-D result's collapsed coordinate is a producer placeholder.
        // Retained coordinates, and both full-grid axes, must preserve source order.
        let coordinates = dimension == "2d" || projected ? ["x", "y"] : [axis!]
        for coordinate in coordinates {
            let key = coordinate + "_positions"
            if let saved = manifest[key] {
                let positions = coordinate == "x" ? source.xPositions : source.yPositions
                guard saved == .array(positions.map(RFMapJSONValue.number)) else {
                    throw failure("Saved result \(key) do not match this RF document's axes.")
                }
            }
        }
        try archive.verifySource()
        return SavedRFResult(path: path, sourceURL: source.url, xPositions: source.xPositions,
                             yPositions: source.yPositions, dimension: dimension, polarity: polarity, axis: axis,
                             projectedFrom2D: projected, timeRangeSeconds: range, parameters: parameters,
                             manifest: manifest, cacheKey: cacheKey, summaryWarning: warning, unitIDs: ids,
                             mask: masks.values, center: centers.values, sourceShape: [source.nY, source.nX], summary: summary)
    }

    func forUnit(_ unitID: Int) -> SavedRFUnitResult? {
        guard let index = unitIDs.firstIndex(of: unitID) else { return nil }
        func projected(_ array: [[Bool]]) -> [[Bool]] {
            guard projectedFrom2D else { return array }
            return [(0..<sourceShape[1]).map { x in array.contains { $0[x] } }]
        }
        return SavedRFUnitResult(mask: projected(mask[index]), center: projected(center[index]))
    }

    func nativeMask(for unitID: Int) -> [[Bool]]? { forUnit(unitID).map { expanded($0.mask) } }
    func nativeCenter(for unitID: Int) -> [[Bool]]? { forUnit(unitID).map { expanded($0.center) } }

    func unitQC(_ unitID: Int) -> [String: RFMapJSONValue]? {
        guard case .object(let qc) = summary?["bin_qc"], case .array(let ids) = qc["unit_ids"],
              let index = ids.firstIndex(of: .number(Double(unitID))) else { return nil }
        var values: [String: RFMapJSONValue] = [:]
        for key in ["zero_bins", "valid_bins", "keep"] {
            if case .array(let column) = qc[key] { values[key] = column[index] }
        }
        return values
    }

    private func expanded(_ array: [[Bool]]) -> [[Bool]] {
        if dimension == "2d" { return array }
        if axis == "x" { return Array(repeating: array[0], count: sourceShape[0]) }
        return array.map { Array(repeating: $0[0], count: sourceShape[1]) }
    }

    func diagnostics(for unitID: Int) -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        var details = "\(path.path)\n\(dimension.uppercased()) · \(polarity)\nSaved interval: \(timeRangeSeconds[0] * 1000)–\(timeRangeSeconds[1] * 1000) ms\nCache key: \(cacheKey)"
        if let bytes = try? encoder.encode(manifest), let text = String(data: bytes, encoding: .utf8) { details += "\n\nManifest:\n" + text }
        if !unitIDs.contains(unitID) { details += "\nUnit \(unitID) is absent or was excluded by saved QC." }
        if case .object(let qc) = summary?["bin_qc"], case .array(let ids) = qc["unit_ids"],
           let index = ids.firstIndex(of: .number(Double(unitID))) {
            details += "\n\nSaved QC for unit \(unitID):"
            for key in ["zero_bins", "valid_bins", "keep"] {
                if case .array(let values) = qc[key] { details += "\n\(key): \(values[index])" }
            }
        }
        if let summaryWarning { details += "\n\n" + summaryWarning }
        return details
    }

    private static func readSummary(source: RFMappingData, path: URL, unitIDs: [Int],
                                    manifest: [String: RFMapJSONValue], dimension: String,
                                    polarity: String, layoutPattern: String) throws -> [String: RFMapJSONValue]? {
        let filename: String
        if try usesKindDirectory(source.url, layoutPattern: layoutPattern) {
            filename = polarity == "inhibitory" ? "analysis_inh.json" : "analysis_ext.json"
        } else {
            filename = source.url.deletingPathExtension().lastPathComponent
                + (polarity == "inhibitory" ? "_inhibitory" : "") + "_analysis.json"
        }
        let url = source.url.deletingLastPathComponent().appendingPathComponent(filename)
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        let summary = try JSONDecoder().decode([String: RFMapJSONValue].self, from: Data(contentsOf: url))
        guard case .string(let sourcePath) = summary["source_path"], URL(fileURLWithPath: sourcePath).lastPathComponent == source.url.lastPathComponent,
              case .object(let parameters) = summary["parameters"], parameters["rf_type"] == .string(polarity),
              case .array(let times) = parameters["time_range_s"], case .array(let savedTimes) = manifest["time_range_s"],
              times.count == 2, savedTimes.count == 2,
              zip(times, savedTimes).allSatisfy({ pair in
                  if case .number(let a) = pair.0, case .number(let b) = pair.1 { return abs(a - b) <= 1e-12 }
                  return false
              }),
              case .object(let savedParameters) = manifest["parameters"] else {
            throw failure("Analysis summary source, polarity or time range differs from the saved result.")
        }
        guard (parameters["drop_bins_\(dimension)"] ?? parameters["drop_bins"]) == savedParameters["drop_bins"] else {
            throw failure("Analysis summary drop_bins_\(dimension) differs from the saved result.")
        }
        for key in ["wrap_x", "is_shuffle", "exclude_zero_bins"] where parameters[key] != savedParameters[key] {
            throw failure("Analysis summary \(key) differs from the saved result.")
        }
        guard parameters["cluster_forming_z_\(dimension)"] == savedParameters["cluster_forming_z"],
              try ids(summary["kept_unit_ids"]) == Set(unitIDs),
              case .object(let outputs) = summary["output_paths"], case .string(let resultPath) = outputs["result_\(dimension)"],
              URL(fileURLWithPath: resultPath).lastPathComponent == path.lastPathComponent,
              case .object(let qc) = summary["bin_qc"], case .array(let qcIDs) = qc["unit_ids"],
              try ids(qc["unit_ids"]).isSubset(of: Set(source.unitPool)) else {
            throw failure("Analysis summary units, threshold or output path differs from the saved result.")
        }
        for key in ["zero_bins", "valid_bins", "keep"] {
            guard case .array(let values) = qc[key], values.count == qcIDs.count else { throw failure("Analysis summary QC columns are not aligned.") }
            for value in values {
                if key == "keep" {
                    guard case .bool = value else { throw failure("QC keep must be boolean.") }
                } else {
                    guard case .number(let number) = value, number >= 0, Int(exactly: number) != nil else { throw failure("QC counts must be non-negative integers.") }
                }
            }
        }
        if case .array(let keep) = qc["keep"] {
            let kept = zip(qcIDs, keep).compactMap { id, flag -> Int? in
                if case .number(let value) = id, flag == .bool(true) { return Int(exactly: value) }
                return nil
            }
            guard Set(kept) == Set(unitIDs) else { throw failure("Analysis summary QC keep flags differ from saved units.") }
        }
        return summary
    }

    private static func ids(_ value: RFMapJSONValue?) throws -> Set<Int> {
        guard case .array(let values) = value, !values.isEmpty else { throw failure("Expected integer unit IDs.") }
        let ids = try values.map { value -> Int in
            guard case .number(let number) = value, let id = Int(exactly: number) else { throw failure("Expected integer unit IDs.") }
            return id
        }
        guard Set(ids).count == ids.count else { throw failure("Duplicate unit IDs.") }
        return Set(ids)
    }

    /// Check canonical separators and object-key order without rewriting number spellings.
    private static func isCanonicalObject(_ text: String) -> Bool {
        let bytes = Array(text.utf8)
        guard bytes.first == 123, bytes.last == 125 else { return false }
        var index = 0
        var objects: [(isObject: Bool, expectsKey: Bool, lastKey: String?)] = []
        while index < bytes.count {
            let byte = bytes[index]
            if byte == 34 {
                let start = index
                index += 1
                while index < bytes.count && bytes[index] != 34 {
                    if bytes[index] == 92 { index += 1 }
                    index += 1
                }
                guard index < bytes.count else { return false }
                if let frame = objects.last, frame.isObject && frame.expectsKey {
                    guard let key = try? JSONDecoder().decode(String.self, from: Data(bytes[start...index])),
                          frame.lastKey == nil || frame.lastKey! < key else { return false }
                    objects[objects.count - 1].lastKey = key
                    objects[objects.count - 1].expectsKey = false
                }
            } else if byte == 123 || byte == 91 {
                objects.append((byte == 123, byte == 123, nil))
            } else if byte == 125 || byte == 93 {
                guard !objects.isEmpty else { return false }
                objects.removeLast()
            } else if byte == 44, !objects.isEmpty, objects[objects.count - 1].isObject {
                objects[objects.count - 1].expectsKey = true
            } else if byte == 32 || byte == 9 || byte == 10 || byte == 13 {
                return false
            }
            index += 1
        }
        return objects.isEmpty
    }

    private static func failure(_ message: String) -> RFMappingError {
        .invalidData("Saved RF result: " + message)
    }
}
