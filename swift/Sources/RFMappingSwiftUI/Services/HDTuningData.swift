import CoreFoundation
import Foundation

enum HDTuningError: LocalizedError, Equatable {
    case invalidData(String)
    case missingUnit(Int, available: [Int])

    var errorDescription: String? {
        switch self {
        case .invalidData(let message):
            return message
        case .missingUnit(let unitID, let available):
            return "HD unit \(unitID) is unavailable. Available unit IDs: \(available)."
        }
    }
}

struct HDTuningUnit: Equatable, Sendable {
    let unitID: Int
    let spikeCounts: [Double]
    /// `nil` is valid only for a zero-occupancy bin, matching JSON `null`.
    let rawRatesHz: [Double?]
    let hdClass: Int?
    let metrics: [String: RFMapJSONValue]
}

struct ProcessedHDCurve: Equatable, Sendable {
    let anglesDegrees: [Double]
    let ratesHz: [Double]

    func centeredOnZero() -> ProcessedHDCurve {
        // HD and RF both increase clockwise; wrapping must not mirror the rates.
        let samples = zip(anglesDegrees, ratesHz).map { angle, rate in
            var centered = (angle + 180).truncatingRemainder(dividingBy: 360)
            if centered < 0 { centered += 360 }
            return (angle: centered - 180, rate: rate)
        }.sorted { $0.angle < $1.angle }
        return ProcessedHDCurve(
            anglesDegrees: samples.map(\.angle),
            ratesHz: samples.map(\.rate)
        )
    }
}

/// Canvas coordinates: 0 degrees is north and positive angles turn clockwise.
func hdScreenVector(angleDegrees: Double) -> (x: Double, y: Double) {
    let radians = angleDegrees * .pi / 180
    return (sin(radians), -cos(radians))
}

private struct HDTuningPayload: Decodable {
    let metadata: [String: RFMapJSONValue]
    let angleBinEdgesDegrees: [Double]
    let occupancySamples: [Int]?
    let occupancyTimeSeconds: [Double]
    let unitIDs: [Int]
    let spikeCounts: [[Int]]
    let firingRatesHz: [[Double?]]
    let unitData: [String: [RFMapJSONValue]]

    private enum CodingKeys: String, CodingKey {
        case metadata
        case angleBinEdgesDegrees = "angle_bin_edges_deg"
        case occupancySamples = "occupancy_samples"
        case occupancyTimeSeconds = "occupancy_time_s"
        case unitIDs = "unit_id"
        case spikeCounts = "spike_counts"
        case firingRatesHz = "firing_rate_hz"
        case unitData = "unit_data"
    }
}

private enum HDTuningContractValidator {
    private struct JSONContainerFrame {
        let openingByte: UInt8
        var keys: Set<String> = []

        var isObject: Bool { openingByte == 0x7B }
    }

    private static let topLevelKeys = [
        "metadata",
        "angle_bin_edges_deg",
        "occupancy_time_s",
        "unit_id",
        "spike_counts",
        "firing_rate_hz",
        "unit_data",
    ]
    static func validate(_ data: Data) throws {
        try rejectDuplicateJSONKeys(data)
        let decoded: Any
        do {
            decoded = try JSONSerialization.jsonObject(with: data)
        } catch {
            throw HDTuningError.invalidData(
                "Could not decode HD tuning JSON: \(error.localizedDescription)"
            )
        }
        guard let payload = decoded as? [String: Any], !payload.isEmpty else {
            throw HDTuningError.invalidData(
                "Tuning-curve JSON must be a non-empty object."
            )
        }
        try requireFiniteJSON(payload, context: "tuning-curve JSON")

        if payload["schema_version"] != nil {
            throw HDTuningError.invalidData(
                "Tuning-curve schema_version is obsolete; the current columnar contract has exactly eight unversioned top-level keys."
            )
        }
        let missing = topLevelKeys.filter { payload[$0] == nil }
        if !missing.isEmpty {
            throw HDTuningError.invalidData(
                "Missing tuning-curve keys: \(missing.joined(separator: ", "))."
            )
        }
        let metadata = try object(
            payload["metadata"],
            label: "Tuning-curve metadata"
        )
        _ = try validateMetadata(metadata)

        let edges = try array(
            payload["angle_bin_edges_deg"],
            count: HDTuningData.rawBinCount + 1,
            label: "Tuning-curve angle_bin_edges_deg"
        ).enumerated().map { index, rawValue in
            try number(rawValue, label: "Tuning-curve angle edge \(index + 1)")
        }
        let expectedWidth = 360.0 / Double(HDTuningData.rawBinCount)
        guard edges.enumerated().allSatisfy({ index, value in
            isClose(
                value,
                Double(index) * expectedWidth,
                relativeTolerance: 0,
                absoluteTolerance: 1e-8
            )
        }) else {
            throw HDTuningError.invalidData(
                "Tuning-curve angle bins must span 0–360° in 180 equal bins."
            )
        }

        let occupancy = try array(
            payload["occupancy_time_s"],
            count: HDTuningData.rawBinCount,
            label: "Tuning-curve occupancy_time_s"
        ).enumerated().map { index, rawValue in
            try number(
                rawValue,
                label: "Tuning-curve occupancy time \(index + 1)",
                nonnegative: true
            )
        }
        guard occupancy.contains(where: { $0 > 0 }) else {
            throw HDTuningError.invalidData(
                "Tuning-curve occupancy_time_s must contain positive occupancy."
            )
        }

        let rawUnitIDs = try array(
            payload["unit_id"],
            label: "Tuning-curve unit_id"
        )
        guard !rawUnitIDs.isEmpty else {
            throw HDTuningError.invalidData(
                "Tuning-curve unit_id must be a non-empty list."
            )
        }
        let unitIDs = try rawUnitIDs.enumerated().map { index, rawValue in
            try integer(
                rawValue,
                label: "Tuning-curve unit_id value \(index + 1)",
                nonnegative: true
            )
        }
        guard Set(unitIDs).count == unitIDs.count else {
            throw HDTuningError.invalidData(
                "Tuning-curve unit_id values must be unique."
            )
        }

        let countRows = try array(
            payload["spike_counts"],
            count: unitIDs.count,
            label: "Tuning-curve spike_counts row count"
        )
        let rateRows = try array(
            payload["firing_rate_hz"],
            count: unitIDs.count,
            label: "Tuning-curve firing_rate_hz row count"
        )
        let unitData = try object(
            payload["unit_data"],
            label: "Tuning-curve unit_data"
        )
        var columns: [String: [Any]] = [:]
        for (key, value) in unitData {
            columns[key] = try array(value, count: unitIDs.count, label: "unit_data.\(key)")
        }

        for unitIndex in unitIDs.indices {
            let unitID = unitIDs[unitIndex]
            let counts = try array(
                countRows[unitIndex],
                count: HDTuningData.rawBinCount,
                label: "Unit \(unitID) spike_counts"
            )
            let rates = try array(
                rateRows[unitIndex],
                count: HDTuningData.rawBinCount,
                label: "Unit \(unitID) firing_rate_hz"
            )
            for binIndex in occupancy.indices {
                let count = try integer(
                    counts[binIndex],
                    label: "Unit \(unitID) spike count \(binIndex + 1)",
                    nonnegative: true
                )
                let rawRate = rates[binIndex]
                if occupancy[binIndex] == 0 {
                    guard count == 0, rawRate is NSNull else {
                        throw HDTuningError.invalidData(
                            "Unit \(unitID) bin \(binIndex + 1) has zero occupancy and must contain count 0 / rate null."
                        )
                    }
                    continue
                }
                let rate = try number(
                    rawRate,
                    label: "Unit \(unitID) firing rate \(binIndex + 1)",
                    nonnegative: true
                )
                let expectedRate = Double(count) / occupancy[binIndex]
                guard expectedRate.isFinite,
                      isClose(
                          rate,
                          expectedRate,
                          relativeTolerance: 1e-7,
                          absoluteTolerance: 1e-9
                      ) else {
                    throw HDTuningError.invalidData(
                        "Unit \(unitID) firing rate \(binIndex + 1) does not match count / occupancy."
                    )
                }
            }

            let hdClass = try nullableInteger(
                columns["hd_class"]?[unitIndex],
                label: "Unit \(unitID) hd_class"
            )
            guard hdClass == nil || [0, 1, 2, 3].contains(hdClass!) else {
                throw HDTuningError.invalidData(
                    "Unit \(unitID) hd_class must be 0, 1, 2, 3, or null."
                )
            }
        }
    }

    private static func validateMetadata(_ metadata: [String: Any]) throws -> Double? {
        for key in [
            "session",
            "probe",
            "kilosort_dir",
            "timebase",
            "timestamp_reference",
            "angle_convention_note",
        ] where metadata[key] != nil {
            _ = try nullableString(
                metadata[key],
                label: "Tuning-curve metadata.\(key)"
            )
        }
        if metadata["adc_time_origin_raw_s"] != nil {
            _ = try nullableNumber(
                metadata["adc_time_origin_raw_s"],
                label: "Tuning-curve metadata.adc_time_origin_raw_s"
            )
        }
        let featureFrequency = try nullableNumber(
            metadata["feature_fs_hz"],
            label: "Tuning-curve metadata.feature_fs_hz"
        )
        if let featureFrequency, featureFrequency <= 0 {
            throw HDTuningError.invalidData(
                "Tuning-curve metadata.feature_fs_hz must be positive."
            )
        }
        if metadata["num_angle_bins"] != nil {
            let binCount = try nullableInteger(
                metadata["num_angle_bins"],
                label: "Tuning-curve metadata.num_angle_bins"
            )
            guard binCount == nil || binCount == HDTuningData.rawBinCount else {
                throw HDTuningError.invalidData(
                    "Tuning-curve metadata.num_angle_bins must equal \(HDTuningData.rawBinCount)."
                )
            }
        }

        if let rawClassification = metadata["classification"],
           !(rawClassification is NSNull) {
            let classification = try object(
                rawClassification,
                label: "Tuning-curve metadata.classification"
            )
            for key in [
                "method",
                "class_0",
                "class_1",
                "class_2",
                "class_3",
                "class_null",
                "rayleigh_test",
            ] where classification[key] != nil {
                _ = try nullableString(
                    classification[key],
                    label: "Tuning-curve metadata.classification.\(key)"
                )
            }
            for key in ["rayleigh_alpha", "shuffle_alpha"]
                where classification[key] != nil {
                let alpha = try nullableNumber(
                    classification[key],
                    label: "Tuning-curve metadata.classification.\(key)"
                )
                guard alpha == nil || (0...1).contains(alpha!) else {
                    throw HDTuningError.invalidData(
                        "Tuning-curve metadata.classification.\(key) must be between 0 and 1."
                    )
                }
            }
            for key in ["num_shuffle", "shuffle_seed"]
                where classification[key] != nil {
                _ = try nullableInteger(
                    classification[key],
                    label: "Tuning-curve metadata.classification.\(key)"
                )
            }
        }

        if let rawTTL = metadata["ttl_qc"], !(rawTTL is NSNull) {
            let ttl = try object(
                rawTTL,
                label: "Tuning-curve metadata.ttl_qc"
            )
            for key in [
                "ttl_pulse_count",
                "camera_input_channel",
                "motive_frame_count_raw",
                "matched_motive_frame_count",
            ] where ttl[key] != nil {
                _ = try nullableInteger(
                    ttl[key],
                    label: "Tuning-curve metadata.ttl_qc.\(key)"
                )
            }
            for key in [
                "first_exposure_s",
                "last_exposure_s",
                "median_period_s",
                "measured_rate_hz",
                "camera_ttl_threshold",
            ] where ttl[key] != nil {
                _ = try nullableNumber(
                    ttl[key],
                    label: "Tuning-curve metadata.ttl_qc.\(key)"
                )
            }
            if ttl["camera_ttl_active_high"] != nil {
                _ = try nullableBoolean(
                    ttl["camera_ttl_active_high"],
                    label: "Tuning-curve metadata.ttl_qc.camera_ttl_active_high"
                )
            }
            if let rawDropped = ttl["dropped_motive_frame_ids"],
               !(rawDropped is NSNull) {
                let dropped = try array(
                    rawDropped,
                    label: "Tuning-curve metadata.ttl_qc.dropped_motive_frame_ids"
                )
                for (index, rawValue) in dropped.enumerated() {
                    _ = try integer(
                        rawValue,
                        label: "Tuning-curve metadata.ttl_qc.dropped_motive_frame_ids[\(index)]"
                    )
                }
            }
            for key in [
                "frame_alignment_policy_requested",
                "frame_alignment_policy_applied",
                "frame_timestamp_mapping",
            ] where ttl[key] != nil {
                _ = try nullableString(
                    ttl[key],
                    label: "Tuning-curve metadata.ttl_qc.\(key)"
                )
            }
        }
        return featureFrequency
    }

    /// JSONSerialization keeps only one value for duplicate object keys. This
    /// structural token pass rejects them first at every nesting depth while
    /// leaving complete syntax validation to Foundation. Key strings are
    /// decoded with JSONDecoder so escaped spellings such as `"a"` and
    /// `"\\u0061"` are treated as the same key.
    static func rejectDuplicateJSONKeys(_ data: Data) throws {
        let bytes = [UInt8](data)
        var frames: [JSONContainerFrame] = []
        var index = 0
        while index < bytes.count {
            switch bytes[index] {
            case 0x7B, 0x5B: // { or [
                frames.append(JSONContainerFrame(openingByte: bytes[index]))
                index += 1
            case 0x7D: // }
                if frames.last?.openingByte == 0x7B { frames.removeLast() }
                index += 1
            case 0x5D: // ]
                if frames.last?.openingByte == 0x5B { frames.removeLast() }
                index += 1
            case 0x22: // JSON string
                let start = index
                index += 1
                var closed = false
                while index < bytes.count {
                    if bytes[index] == 0x5C { // escaped byte
                        index = min(index + 2, bytes.count)
                    } else if bytes[index] == 0x22 {
                        index += 1
                        closed = true
                        break
                    } else {
                        index += 1
                    }
                }
                guard closed else { return }
                var lookahead = index
                while lookahead < bytes.count, isJSONWhitespace(bytes[lookahead]) {
                    lookahead += 1
                }
                guard lookahead < bytes.count,
                      bytes[lookahead] == 0x3A,
                      frames.last?.isObject == true else { continue }
                let literal = Data(bytes[start..<index])
                guard let key = try? JSONDecoder().decode(String.self, from: literal) else {
                    continue
                }
                let objectIndex = frames.index(before: frames.endIndex)
                guard frames[objectIndex].keys.insert(key).inserted else {
                    throw HDTuningError.invalidData(
                        "Duplicate tuning-curve JSON key: \(key)."
                    )
                }
            default:
                index += 1
            }
        }
    }

    private static func isJSONWhitespace(_ byte: UInt8) -> Bool {
        byte == 0x20 || byte == 0x09 || byte == 0x0A || byte == 0x0D
    }

    private static func object(_ rawValue: Any?, label: String) throws -> [String: Any] {
        guard let value = rawValue as? [String: Any] else {
            throw HDTuningError.invalidData("\(label) must be an object.")
        }
        return value
    }

    private static func array(
        _ rawValue: Any?,
        count: Int? = nil,
        label: String
    ) throws -> [Any] {
        guard let value = rawValue as? [Any] else {
            throw HDTuningError.invalidData("\(label) must be an array.")
        }
        if let count, value.count != count {
            throw HDTuningError.invalidData(
                "\(label) must contain \(count) values."
            )
        }
        return value
    }

    private static func number(
        _ rawValue: Any,
        label: String,
        nonnegative: Bool = false
    ) throws -> Double {
        guard let value = rawValue as? NSNumber, !isBoolean(value) else {
            throw HDTuningError.invalidData("\(label) must be numeric.")
        }
        let parsed = value.doubleValue
        guard parsed.isFinite, !nonnegative || parsed >= 0 else {
            let suffix = nonnegative ? " finite and non-negative" : " finite"
            throw HDTuningError.invalidData("\(label) must be\(suffix).")
        }
        return parsed
    }

    private static func nullableNumber(
        _ rawValue: Any?,
        label: String,
        nonnegative: Bool = false
    ) throws -> Double? {
        guard let rawValue, !(rawValue is NSNull) else { return nil }
        return try number(rawValue, label: label, nonnegative: nonnegative)
    }

    private static func integer(
        _ rawValue: Any,
        label: String,
        nonnegative: Bool = false
    ) throws -> Int {
        guard let value = rawValue as? NSNumber,
              !isBoolean(value),
              !isFloatingPoint(value),
              let parsed = Int(value.stringValue),
              !nonnegative || parsed >= 0 else {
            let suffix = nonnegative ? " a non-negative integer" : " an integer"
            throw HDTuningError.invalidData("\(label) must be\(suffix).")
        }
        return parsed
    }

    private static func nullableInteger(_ rawValue: Any?, label: String) throws -> Int? {
        guard let rawValue, !(rawValue is NSNull) else { return nil }
        return try integer(rawValue, label: label)
    }

    private static func nullableString(_ rawValue: Any?, label: String) throws -> String? {
        guard let rawValue, !(rawValue is NSNull) else { return nil }
        guard let value = rawValue as? String else {
            throw HDTuningError.invalidData("\(label) must be a string or null.")
        }
        return value
    }

    private static func nullableBoolean(_ rawValue: Any?, label: String) throws -> Bool? {
        guard let rawValue, !(rawValue is NSNull) else { return nil }
        guard let value = rawValue as? NSNumber, isBoolean(value) else {
            throw HDTuningError.invalidData("\(label) must be boolean or null.")
        }
        return value.boolValue
    }

    private static func isBoolean(_ value: NSNumber) -> Bool {
        CFGetTypeID(value) == CFBooleanGetTypeID()
    }

    private static func isFloatingPoint(_ value: NSNumber) -> Bool {
        let encoding = String(cString: value.objCType)
        return encoding == "f" || encoding == "d"
    }

    private static func requireFiniteJSON(_ rawValue: Any, context: String) throws {
        if let number = rawValue as? NSNumber, !isBoolean(number) {
            guard number.doubleValue.isFinite else {
                throw HDTuningError.invalidData(
                    "\(context) contains a non-finite number."
                )
            }
        } else if let values = rawValue as? [Any] {
            for (index, value) in values.enumerated() {
                try requireFiniteJSON(value, context: "\(context)[\(index)]")
            }
        } else if let values = rawValue as? [String: Any] {
            for (key, value) in values {
                try requireFiniteJSON(value, context: "\(context).\(key)")
            }
        }
    }

    private static func isClose(
        _ lhs: Double,
        _ rhs: Double,
        relativeTolerance: Double,
        absoluteTolerance: Double
    ) -> Bool {
        abs(lhs - rhs) <= max(
            absoluteTolerance,
            relativeTolerance * max(abs(lhs), abs(rhs))
        )
    }
}

struct HDTuningData: Sendable {
    static let rawBinCount = 180
    static let defaultDisplayBins = 30
    static let defaultSmoothSigma = 1.5

    let sourceURL: URL
    let angleBinEdgesDegrees: [Double]
    let occupancySamples: [Int]
    let occupancyTimeSeconds: [Double]
    let unitIDs: [Int]
    let metadata: [String: RFMapJSONValue]
    private let unitsByID: [Int: HDTuningUnit]

    init(data: Data, sourceURL: URL) throws {
        try HDTuningContractValidator.rejectDuplicateJSONKeys(data)
        guard var document = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              !document.isEmpty else {
            throw HDTuningError.invalidData("Tuning-curve JSON must be a non-empty object.")
        }
        let columnarMarkers: Set<String> = ["metadata", "angle_bin_edges_deg", "occupancy_time_s",
                                            "unit_id", "spike_counts", "firing_rate_hz", "unit_data"]
        if document["schema_version"] == nil && columnarMarkers.isDisjoint(with: document.keys) {
            var parsed: [Int: HDTuningUnit] = [:]
            var ids: [Int] = []
            for key in document.keys.sorted() {
                guard let id = Int(key), parsed[id] == nil,
                      let values = document[key] as? [NSNumber], values.count == Self.rawBinCount,
                      values.allSatisfy({ CFGetTypeID($0) != CFBooleanGetTypeID() && $0.doubleValue.isFinite && $0.doubleValue >= 0 }) else {
                    throw HDTuningError.invalidData("Legacy tuning curves require unique integer IDs and 180 non-negative rates per unit.")
                }
                ids.append(id)
                parsed[id] = HDTuningUnit(unitID: id, spikeCounts: [], rawRatesHz: values.map { $0.doubleValue }, hdClass: nil, metrics: [:])
            }
            self.sourceURL = sourceURL.standardizedFileURL
            angleBinEdgesDegrees = (0...Self.rawBinCount).map { Double($0) * 2 }
            occupancySamples = []
            occupancyTimeSeconds = []
            unitIDs = ids
            metadata = [:]
            unitsByID = parsed
            return
        }
        if let version = document.removeValue(forKey: "schema_version") {
            guard let number = version as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(), number == 2,
                  let units = document.removeValue(forKey: "units") as? [[String: Any]], !units.isEmpty else {
                throw HDTuningError.invalidData("Schema version 2 requires a non-empty units array.")
            }
            document["unit_id"] = units.map { $0["unit_id"] ?? NSNull() }
            document["spike_counts"] = units.map { $0["spike_counts"] ?? NSNull() }
            document["firing_rate_hz"] = units.map { $0["firing_rate_hz"] ?? NSNull() }
            let keys = Set(units.flatMap { $0.keys }).subtracting(["unit_id", "spike_counts", "firing_rate_hz"])
            document["unit_data"] = Dictionary(uniqueKeysWithValues: keys.map { key in
                (key, units.map { $0[key] ?? NSNull() })
            })
        }
        if document["metadata"] == nil || document["metadata"] is NSNull { document["metadata"] = [String: Any]() }
        let canonicalData = try JSONSerialization.data(withJSONObject: document)
        try HDTuningContractValidator.validate(canonicalData)
        let payload: HDTuningPayload
        do {
            payload = try JSONDecoder().decode(HDTuningPayload.self, from: canonicalData)
        } catch {
            throw HDTuningError.invalidData(
                "Could not decode HD tuning JSON: \(error.localizedDescription)"
            )
        }
        guard payload.angleBinEdgesDegrees.count == Self.rawBinCount + 1,
              payload.angleBinEdgesDegrees.allSatisfy(\.isFinite),
              zip(payload.angleBinEdgesDegrees, payload.angleBinEdgesDegrees.dropFirst())
                .allSatisfy({ pair in pair.0 < pair.1 }) else {
            throw HDTuningError.invalidData(
                "angle_bin_edges_deg must contain 181 strictly increasing finite edges."
            )
        }
        guard payload.occupancyTimeSeconds.count == Self.rawBinCount,
              payload.occupancyTimeSeconds.allSatisfy({ $0.isFinite && $0 >= 0 }) else {
            throw HDTuningError.invalidData(
                "occupancy_time_s must contain 180 finite non-negative values."
            )
        }
        guard !payload.unitIDs.isEmpty,
              Set(payload.unitIDs).count == payload.unitIDs.count else {
            throw HDTuningError.invalidData("unit_id must contain unique unit IDs.")
        }
        let unitCount = payload.unitIDs.count
        guard payload.spikeCounts.count == unitCount,
              payload.firingRatesHz.count == unitCount else {
            throw HDTuningError.invalidData(
                "spike_counts and firing_rate_hz unit dimensions must match unit_id."
            )
        }
        for key in payload.unitData.keys {
            guard payload.unitData[key]?.count == unitCount else {
                throw HDTuningError.invalidData(
                    "unit_data.\(key) must contain \(unitCount) values."
                )
            }
        }

        var unitsByID: [Int: HDTuningUnit] = [:]
        for (index, unitID) in payload.unitIDs.enumerated() {
            let counts = payload.spikeCounts[index].map(Double.init)
            let rates = payload.firingRatesHz[index]
            guard counts.count == Self.rawBinCount,
                  rates.count == Self.rawBinCount,
                  counts.allSatisfy({ $0.isFinite && $0 >= 0 }) else {
                throw HDTuningError.invalidData(
                    "HD unit \(unitID) must have 180 finite non-negative count values and 180 rate entries."
                )
            }
            for binIndex in rates.indices {
                if let rate = rates[binIndex] {
                    guard rate.isFinite, rate >= 0 else {
                        throw HDTuningError.invalidData(
                            "HD unit \(unitID) firing rates must be finite and non-negative."
                        )
                    }
                } else if payload.occupancyTimeSeconds[binIndex] > 1e-12
                    || counts[binIndex] != 0 {
                    throw HDTuningError.invalidData(
                        "HD unit \(unitID) has a null firing rate in a non-empty occupancy bin."
                    )
                }
            }
            var metrics: [String: RFMapJSONValue] = [:]
            for (key, column) in payload.unitData {
                metrics[key] = column[index]
            }
            let hdClass: Int?
            switch metrics["hd_class"] {
            case nil, .some(.null):
                hdClass = nil
            case .some(.number(let value)):
                guard let exact = Int(exactly: value), [0, 1, 2, 3].contains(exact) else {
                    throw HDTuningError.invalidData(
                        "HD unit \(unitID) hd_class must be 0, 1, 2, 3, or null."
                    )
                }
                hdClass = exact
            default:
                throw HDTuningError.invalidData(
                    "HD unit \(unitID) hd_class must be 0, 1, 2, 3, or null."
                )
            }
            unitsByID[unitID] = HDTuningUnit(
                unitID: unitID,
                spikeCounts: counts,
                rawRatesHz: rates,
                hdClass: hdClass,
                metrics: metrics
            )
        }

        self.sourceURL = sourceURL.standardizedFileURL
        angleBinEdgesDegrees = payload.angleBinEdgesDegrees
        occupancySamples = payload.occupancySamples ?? []
        occupancyTimeSeconds = payload.occupancyTimeSeconds
        unitIDs = payload.unitIDs
        metadata = payload.metadata
        self.unitsByID = unitsByID
    }

    init(url: URL) throws {
        try self.init(
            data: Data(contentsOf: url, options: .mappedIfSafe),
            sourceURL: url
        )
    }

    func unit(byID unitID: Int) throws -> HDTuningUnit {
        guard let unit = unitsByID[unitID] else {
            throw HDTuningError.missingUnit(unitID, available: unitIDs)
        }
        return unit
    }

    func processedCurve(
        unitID: Int,
        displayBins: Int = HDTuningData.defaultDisplayBins,
        smoothing: Bool = true,
        sigma: Double = HDTuningData.defaultSmoothSigma
    ) throws -> ProcessedHDCurve {
        let unit = try unit(byID: unitID)
        let bins = Self.normalizedDisplayBinCount(displayBins)
        if unit.spikeCounts.isEmpty {
            var rates = unit.rawRatesHz.map { $0 ?? .nan }
            if smoothing {
                guard sigma.isFinite, sigma > 0 else { throw HDTuningError.invalidData("HD smoothing sigma must be positive and finite.") }
                rates = Self.smoothCircular(rates, sigma: sigma * Double(Self.rawBinCount) / Double(Self.defaultDisplayBins))
            }
            let groupSize = Self.rawBinCount / bins
            let grouped = (0..<bins).map { group in
                compensatedSum(rates[(group * groupSize)..<((group + 1) * groupSize)]) / Double(groupSize)
            }
            return ProcessedHDCurve(anglesDegrees: (0..<bins).map { (Double($0) + 0.5) * 360 / Double(bins) }, ratesHz: grouped)
        }
        var counts = unit.spikeCounts
        var occupancy = occupancyTimeSeconds
        if smoothing {
            guard sigma.isFinite, sigma > 0 else {
                throw HDTuningError.invalidData(
                    "HD smoothing sigma must be positive and finite."
                )
            }
            let rawSigma = sigma * Double(Self.rawBinCount) / Double(Self.defaultDisplayBins)
            counts = Self.smoothCircular(counts, sigma: rawSigma)
            occupancy = Self.smoothCircular(occupancy, sigma: rawSigma)
        }

        let groupSize = Self.rawBinCount / bins
        var groupedRates: [Double] = []
        groupedRates.reserveCapacity(bins)
        for group in 0..<bins {
            let start = group * groupSize
            let end = start + groupSize
            let groupedCounts = compensatedSum(counts[start..<end])
            let groupedOccupancy = compensatedSum(occupancy[start..<end])
            groupedRates.append(
                groupedOccupancy > 1e-12 ? groupedCounts / groupedOccupancy : .nan
            )
        }
        let width = 360.0 / Double(bins)
        let angles = (0..<bins).map { (Double($0) + 0.5) * width }
        return ProcessedHDCurve(anglesDegrees: angles, ratesHz: groupedRates)
    }

    static func normalizedDisplayBinCount(_ requested: Int) -> Int {
        let clamped = max(1, min(rawBinCount, requested))
        for candidate in stride(from: clamped, through: 1, by: -1)
            where rawBinCount.isMultiple(of: candidate) {
            return candidate
        }
        return 1
    }

    static func smoothCircular(_ values: [Double], sigma: Double) -> [Double] {
        guard !values.isEmpty, sigma.isFinite, sigma > 0 else { return values }
        let radius = Int(floor(4.0 * sigma + 0.5))
        let offsets = Array(-radius...radius)
        var weights = offsets.map { exp(-0.5 * pow(Double($0) / sigma, 2)) }
        let weightSum = compensatedSum(weights)
        weights = weights.map { $0 / weightSum }
        var result = Array(repeating: 0.0, count: values.count)
        for index in values.indices {
            for (offset, weight) in zip(offsets, weights) {
                let source = (index + offset % values.count + values.count) % values.count
                result[index] += weight * values[source]
            }
        }
        return result
    }
}

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
