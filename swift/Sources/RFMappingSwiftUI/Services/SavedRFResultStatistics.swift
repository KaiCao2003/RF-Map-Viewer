import Foundation

/// Saved detector statistics and descriptive current-source statistics retain
/// different provenance; neither depends on the viewer's display controls.
struct SavedRFResultStatistics: Equatable, Sendable {
    var meanHz: Double?
    var sdHz: Double?
    var thresholdHz: Double?
    var threshold: String?
    var candidateThreshold = false
    var surroundDecay: Double?
    var origin = "computed"
    var processingLabel = "Native RF"
    var scope = ""
    var binCount: Int?
    var error: String?
    var basisKey = ""

    var thresholdText: String? {
        guard let threshold else { return nil }
        guard let thresholdHz else { return threshold }
        return threshold + " = " + String(format: "%.6g Hz", thresholdHz)
    }

    var meanSDText: String {
        guard error == nil, let meanHz, let sdHz else {
            return "\(origin == "saved" ? "Saved" : "Computed") mean / SD unavailable: \(error ?? "No statistics")"
        }
        return String(format: "Mean %.6g Hz · SD %.6g Hz", meanHz, sdHz)
    }

    var detailsText: String {
        let label = candidateThreshold ? "Saved candidate threshold" : "Saved threshold"
        var lines = [label + ": " + (thresholdText ?? "unavailable (no valid saved SD multiplier)")]
        if let surroundDecay {
            lines.append(String(format: "Saved surround decay: %.6g × SD (one-step seed neighbors)", surroundDecay))
        }
        lines.append(meanSDText)
        if error == nil {
            lines.append("Baseline: \(scope); \(processingLabel).")
            if origin == "saved" {
                lines.append("Mean/SD read from the saved baseline for this unit; not recomputed from the current RF source.")
            } else {
                lines.append("Population SD (ddof=0); \(binCount ?? 0) bins; missing bins counted as zero.")
                lines.append("Mean firing rate over the saved window. Original detector mean/SD and temporal aggregation were not saved.")
            }
        }
        return lines.joined(separator: "\n")
    }

    func sharesBaseline(with other: Self) -> Bool {
        error == nil && other.error == nil && !basisKey.isEmpty && basisKey == other.basisKey
            && origin == other.origin && meanHz != nil && sdHz != nil
            && meanHz == other.meanHz && sdHz == other.sdHz
    }

    static func calculate(result: SavedRFResult, source: RFMappingData?, unitID: Int) -> Self {
        let parameters = result.parameters
        let multiplier = positiveNumber(parameters["cluster_forming_z"])
        let inhibitory = parameters["alternative"] == .string("less")
        let hasDirection = inhibitory || parameters["alternative"] == .string("greater")
        var statistics = Self()
        if let multiplier, hasDirection {
            statistics.threshold = "mean \(inhibitory ? "−" : "+") " + String(format: "%g × SD", multiplier)
        }
        statistics.candidateThreshold = parameters["is_shuffle"] == .bool(true)
        statistics.surroundDecay = positiveNumber(parameters["surround_decay"])
        let baseline = object(result.manifest["baseline"])
        let hasSaved = baseline?["mean_hz"] != nil || baseline?["sd_hz"] != nil
        statistics.origin = hasSaved ? "saved" : "computed"
        statistics.processingLabel = hasSaved ? "Saved baseline" : "Native RF"
        do {
            guard result.unitIDs.contains(unitID) else { throw failure("unit is absent from this saved result") }
            if hasSaved, let baseline {
                guard result.manifest["response_units"] == .string("Hz") else {
                    throw failure("saved baseline statistics do not identify Hz units")
                }
                guard let ids = integers(baseline["unit_ids"]), !ids.isEmpty,
                      Set(ids).count == ids.count,
                      let means = numbers(baseline["mean_hz"]), let sds = numbers(baseline["sd_hz"]),
                      ids.count == means.count, ids.count == sds.count else {
                    throw failure("saved mean/SD arrays are not aligned with unique baseline unit IDs")
                }
                guard sds.allSatisfy({ $0 >= 0 }) else { throw failure("saved SD values must be nonnegative") }
                guard let index = ids.firstIndex(of: unitID) else { throw failure("unit has no saved baseline mean/SD") }
                statistics.meanHz = means[index]
                statistics.sdHz = sds[index]
                statistics.scope = baseline["mean_scope"] == .string("bottom_two_rows")
                    && baseline["sd_scope"] == .string("bottom_two_rows")
                    ? "last two stored rows (all columns)" : "saved baseline"
                if baseline["response_stage"] == .string("after_gui_smoothing1") {
                    statistics.processingLabel = "Smoothing 1"
                }
                if let count = integer(baseline["bin_count"]), count > 0 { statistics.binCount = count }
            } else {
                let (values, scope) = try baselineValues(result: result, source: source, unitID: unitID)
                let mean = compensatedSum(values) / Double(values.count)
                statistics.meanHz = mean
                statistics.sdHz = sqrt(compensatedSum(values.map { pow($0 - mean, 2) }) / Double(values.count))
                statistics.scope = scope
                statistics.binCount = values.count
            }
            if let multiplier, hasDirection, let mean = statistics.meanHz, let sd = statistics.sdHz {
                statistics.thresholdHz = mean + (inhibitory ? -1 : 1) * multiplier * sd
            }
            statistics.basisKey = try statisticsBasis(result: result, unitID: unitID, origin: statistics.origin)
        } catch {
            statistics.error = error.localizedDescription
        }
        return statistics
    }

    private static func baselineValues(result: SavedRFResult, source: RFMappingData?, unitID: Int) throws -> ([Double], String) {
        let manifest = result.manifest, parameters = result.parameters
        guard parameters["is_shuffle"] == .bool(false) else {
            throw failure("shuffle or unspecified detection cannot be reconstructed from the RF map")
        }
        guard parameters["exclude_zero_bins"] == .bool(false) else {
            throw failure("zero-bin exclusion is unsupported or unspecified")
        }
        let bottom = parameters["reference"] == .string("bottom_row")
        guard (bottom && manifest["detector_algorithm"] == .string("bottom-two-rows-mean-sd-v1"))
            || (parameters["reference"] == .string("global")
                && manifest["detector_algorithm"] == .string("pooled-spatial-z-v3-zero-filled")) else {
            throw failure("saved baseline algorithm or reference is unsupported")
        }
        guard manifest["response_units"] == .string("Hz"),
              manifest["response_normalization"] == .string("presentation_count_time") else {
            throw failure("saved response units or normalization do not identify presentation-based Hz")
        }
        guard let source else { throw failure("the RF source is unavailable") }
        let unit = try source.rfMap(byUnitID: unitID)
        guard let presentations = unit.stimulusPresentationCounts else {
            throw failure("source has no saved stimulus presentation counts")
        }
        guard unit.xPositions == result.xPositions, unit.yPositions == result.yPositions else {
            throw failure("source coordinates changed since these results were loaded")
        }
        let first = result.timeRangeSeconds[0], last = result.timeRangeSeconds[1]
        let summed = try unit.sumBetweenSeconds(first, last)
        // The producer zero-fills missing native bins before spatial mean/SD.
        // Occupancy identifies missing bins; presentation count defines exposure.
        var values = (0..<unit.nY).map { y in
            (0..<unit.nX).map { x in
                let exposure = presentations[y][x] * (last - first)
                return unit.occupancyTimeSeconds[y][x] > 0 && exposure > 0
                    ? summed.spikeCounts[y][x][0] / exposure : 0
            }
        }
        guard values.joined().allSatisfy(\.isFinite) else { throw failure("source response contains nonfinite values") }
        let projectionKey = string(manifest["projection_source_cache_key"]) ?? ""
        let projected = result.projectedFrom2D || !projectionKey.isEmpty
        let aggregations = array(manifest["spatial_aggregation"])
        if result.dimension == "2d", aggregations?.contains(where: { object($0) != nil }) == true {
            throw failure("2D result has a computed spatial aggregation that cannot be matched to the native source")
        }
        let resultIndex = result.unitIDs.firstIndex(of: unitID)!
        var native = result.dimension == "2d" || projected
        if !native, values.count == result.mask[resultIndex].count,
           values[0].count == result.mask[resultIndex][0].count {
            let coverageOnly = manifest["spatial_aggregation"] == nil || manifest["spatial_aggregation"] == .null
                || (aggregations?.count == result.unitIDs.count && string(aggregations![resultIndex]) != nil)
            native = (unit.nY == 1 || unit.nX == 1) && coverageOnly
        }
        var scope: String
        if native {
            var x = unit.xPositions, y = unit.yPositions
            if !projectionKey.isEmpty && !result.projectedFrom2D {
                if result.axis == "x" { y = Array(y.prefix(1)) }
                else { x = Array(x.prefix(1)) }
            }
            guard numbers(manifest["x_positions"]) == x, numbers(manifest["y_positions"]) == y else {
                throw failure("saved coordinates do not match the source")
            }
            scope = "global native RF grid"
        } else {
            guard let aggregations, aggregations.count == result.unitIDs.count else {
                throw failure("independent 1D result has no aligned spatial-sum provenance")
            }
            guard let aggregation = object(aggregations[resultIndex]),
                  aggregation["operation"] == .string("sum"), aggregation["rf_only"] == .bool(false),
                  aggregation["missing_as_zero"] == .bool(true),
                  aggregation["retained_axis"] == result.axis.map(RFMapJSONValue.string) else {
                throw failure("independent 1D spatial-sum provenance is unsupported")
            }
            guard numbers(aggregation["x_positions"]) == unit.xPositions,
                  numbers(aggregation["y_positions"]) == unit.yPositions else {
                throw failure("1D spatial-sum coordinates do not match the source")
            }
            let collapsedCount = result.axis == "x" ? unit.nY : unit.nX
            guard let indices = integers(aggregation["selected_indices"]), !indices.isEmpty,
                  Set(indices).count == indices.count,
                  indices.allSatisfy({ 0 <= $0 && $0 < collapsedCount }) else {
                throw failure("1D spatial-sum indices are invalid")
            }
            if result.axis == "x" {
                values = [(0..<unit.nX).map { x in compensatedSum(indices.map { values[$0][x] }) }]
            } else {
                values = (0..<unit.nY).map { y in [compensatedSum(indices.map { values[y][$0] })] }
            }
            scope = "global 1D sum onto \(result.axis!.uppercased())"
        }
        if bottom {
            guard native, values.count >= 2, let baseline = object(manifest["baseline"]),
                  integers(baseline["row_indices"]) == [values.count - 2, values.count - 1],
                  baseline["mean_scope"] == .string("bottom_two_rows"),
                  baseline["sd_scope"] == .string("bottom_two_rows"),
                  integer(baseline["sd_ddof"]) == 0,
                  integer(baseline["bin_count"]) == 2 * values[0].count,
                  numbers(baseline["y_positions"]) == Array(unit.yPositions.suffix(2)) else {
                throw failure("bottom-row baseline provenance does not match the source")
            }
            values = Array(values.suffix(2))
            scope = "last two stored rows (all columns)"
        }
        if projected { scope += "; projected from 2D" }
        return (values.flatMap { $0 }, scope)
    }

    private static func statisticsBasis(result: SavedRFResult, unitID: Int, origin: String) throws -> String {
        let manifest = result.manifest
        var baseline = object(manifest["baseline"])
        if origin == "saved" {
            guard let baseline, let algorithm = string(manifest["detector_algorithm"]), !algorithm.isEmpty,
                  let meanScope = string(baseline["mean_scope"]), !meanScope.isEmpty,
                  let sdScope = string(baseline["sd_scope"]), !sdScope.isEmpty,
                  let ddof = integer(baseline["sd_ddof"]), ddof >= 0 else {
                // Values can be shown without enough provenance to share a baseline.
                return ""
            }
        }
        for key in ["unit_ids", "mean_hz", "sd_hz", "threshold_hz"] { baseline?.removeValue(forKey: key) }
        var aggregation = manifest["spatial_aggregation"]
        if let values = array(aggregation), values.count == result.unitIDs.count {
            aggregation = values[result.unitIDs.firstIndex(of: unitID)!]
        }
        let excluded: Set<String> = ["alternative", "rf_type", "cluster_forming_z", "surround_decay", "drop_bins",
                                     "fill_single_holes", "min_hole_neighbors"]
        var basis: [String: RFMapJSONValue] = [
            "origin": .string(origin), "source": .string(result.sourceURL.path),
            "algorithm": manifest["detector_algorithm"] ?? .null,
            "parameters": .object(result.parameters.filter { !excluded.contains($0.key) }),
            "baseline": baseline.map(RFMapJSONValue.object) ?? .null,
            "spatial_aggregation": aggregation ?? .null,
            "projected_from_2d": .bool(result.projectedFrom2D || !(string(manifest["projection_source_cache_key"]) ?? "").isEmpty),
            "axis": result.axis.map(RFMapJSONValue.string) ?? .null,
        ]
        for key in ["time_range_s", "response_units", "response_normalization", "response_stage",
                    "x_positions", "y_positions", "spatial_preprocessing"] {
            basis[key] = manifest[key] ?? .null
        }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return String(decoding: try encoder.encode(basis), as: UTF8.self)
    }

    private static func number(_ value: RFMapJSONValue?) -> Double? {
        guard case .number(let number) = value, number.isFinite else { return nil }
        return number
    }
    private static func positiveNumber(_ value: RFMapJSONValue?) -> Double? {
        guard let number = number(value), number > 0 else { return nil }
        return number
    }
    private static func integer(_ value: RFMapJSONValue?) -> Int? { number(value).flatMap { Int(exactly: $0) } }
    private static func string(_ value: RFMapJSONValue?) -> String? {
        guard case .string(let text) = value else { return nil }
        return text
    }
    private static func object(_ value: RFMapJSONValue?) -> [String: RFMapJSONValue]? {
        guard case .object(let values) = value else { return nil }
        return values
    }
    private static func array(_ value: RFMapJSONValue?) -> [RFMapJSONValue]? {
        guard case .array(let values) = value else { return nil }
        return values
    }
    private static func numbers(_ value: RFMapJSONValue?) -> [Double]? {
        guard let values = array(value) else { return nil }
        let parsed = values.compactMap(number)
        return parsed.count == values.count ? parsed : nil
    }
    private static func integers(_ value: RFMapJSONValue?) -> [Int]? {
        guard let values = array(value) else { return nil }
        let parsed = values.compactMap(integer)
        return parsed.count == values.count ? parsed : nil
    }
    private static func failure(_ message: String) -> RFMappingError { .invalidData(message) }
}
