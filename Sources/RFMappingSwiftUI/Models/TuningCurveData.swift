import CoreFoundation
import Foundation

let hdRawBinCount = 180
let defaultHDTuningDisplayBins = 30
let defaultHDTuningSmoothSigma = 1.5

private let hdDisplayBinDivisors = (1...hdRawBinCount).filter {
    hdRawBinCount.isMultiple(of: $0)
}

enum TuningCurveSchema: String, Sendable {
    case legacy
    case version2
}

enum HDCellClass: Int, Sendable {
    case notSignificant = 0
    case oneTestSignificant = 1
    case bothTestsSignificant = 2
}

struct TuningCurveClassificationProvenance: Equatable, Sendable {
    let method: String?
    let class0Description: String?
    let class1Description: String?
    let class2Description: String?
    let classNullDescription: String?
    let rayleighAlpha: Double?
    let rayleighTest: String?
    let shuffleAlpha: Double?
    let numberOfShuffles: Int?
    let shuffleSeed: Int?
}

struct TuningCurveTTLProvenance: Equatable, Sendable {
    let pulseCount: Int?
    let firstExposureSeconds: Double?
    let lastExposureSeconds: Double?
    let medianPeriodSeconds: Double?
    let measuredRateHz: Double?
    let cameraInputChannel: Int?
    let cameraTTLThreshold: Double?
    let cameraTTLActiveHigh: Bool?
    let rawMotiveFrameCount: Int?
    let matchedMotiveFrameCount: Int?
    let droppedMotiveFrameIDs: [Int]?
    let frameAlignmentPolicyRequested: String?
    let frameAlignmentPolicyApplied: String?
    let frameTimestampMapping: String?
}

struct TuningCurveMetadata: Equatable, Sendable {
    let session: String?
    let probe: String?
    let kilosortDirectory: String?
    let timebase: String?
    let adcTimeOriginRawSeconds: Double?
    let timestampReference: String?
    let angleConventionNote: String?
    let numberOfAngleBins: Int?
    let featureRateHz: Double?
    let classification: TuningCurveClassificationProvenance?
    let ttlQC: TuningCurveTTLProvenance?
}

struct TuningCurveUnit: Equatable, Sendable {
    let unitID: Int
    let spikeCounts: [Int]?
    /// Raw schema-v2 bins with zero occupancy remain `nil`, never zero Hz.
    let firingRatesHz: [Double?]
    let hdClass: HDCellClass?
}

struct ProcessedTuningCurve: Equatable, Sendable {
    let anglesDeg: [Double]
    let firingRatesHz: [Double?]

    var peakHz: Double {
        firingRatesHz.compactMap { rate in
            guard let rate, rate.isFinite else { return nil }
            return rate
        }.max() ?? 0.0
    }
}

enum TuningCurveError: LocalizedError {
    case invalidData(String)

    var errorDescription: String? {
        switch self {
        case .invalidData(let message):
            message
        }
    }
}

/// Immutable tuning-curve data can be decoded on a worker and then safely
/// transferred to the main actor. All expensive derived values are returned
/// to the caller so a store can cache them without mutating this object.
final class TuningCurveData: @unchecked Sendable {
    private struct DecodedData {
        let schema: TuningCurveSchema
        let angleBinEdgesDeg: [Double]
        let occupancyTimeSeconds: [Double]?
        let metadata: TuningCurveMetadata?
        let unitIDs: [Int]
        let unitsByID: [Int: TuningCurveUnit]
    }

    let url: URL
    let schema: TuningCurveSchema
    let angleBinEdgesDeg: [Double]
    let occupancyTimeSeconds: [Double]?
    let metadata: TuningCurveMetadata?
    let unitIDs: [Int]
    let unitsByID: [Int: TuningCurveUnit]

    convenience init(url: URL) throws {
        try self.init(
            data: Data(contentsOf: url, options: .mappedIfSafe),
            url: url
        )
    }

    init(data jsonData: Data, url: URL) throws {
        self.url = url.standardizedFileURL
        let decoded = try Self.decode(jsonData)
        schema = decoded.schema
        angleBinEdgesDeg = decoded.angleBinEdgesDeg
        occupancyTimeSeconds = decoded.occupancyTimeSeconds
        metadata = decoded.metadata
        unitIDs = decoded.unitIDs
        unitsByID = decoded.unitsByID
    }

    static func makeDecodeTask(url: URL) -> Task<TuningCurveData, Error> {
        Task.detached(priority: .userInitiated) {
            try Task.checkCancellation()
            let decoded = try TuningCurveData(url: url)
            try Task.checkCancellation()
            return decoded
        }
    }

    static func decodeOffMain(url: URL) async throws -> TuningCurveData {
        let task = makeDecodeTask(url: url)
        return try await withTaskCancellationHandler {
            try await task.value
        } onCancel: {
            task.cancel()
        }
    }

    func unit(for unitID: Int) -> TuningCurveUnit? {
        unitsByID[unitID]
    }

    func rates(for unitID: Int) -> [Double?]? {
        unitsByID[unitID]?.firingRatesHz
    }

    func hdClass(for unitID: Int) -> HDCellClass? {
        unitsByID[unitID]?.hdClass
    }

    func processedCurve(
        for unitID: Int,
        displayBins: Int,
        smoothing: Bool,
        sigmaAtThirtyBins: Double = defaultHDTuningSmoothSigma
    ) throws -> ProcessedTuningCurve? {
        guard let unit = unitsByID[unitID] else { return nil }
        let normalizedBins = normalizeHDTuningBinCount(displayBins)

        if let spikeCounts = unit.spikeCounts,
           let occupancyTimeSeconds {
            let sourceCounts: [Double]
            let sourceOccupancy: [Double]
            if smoothing {
                let sigmaBins = try hdTuningSmoothingSigmaBins(
                    sigmaAtThirtyBins,
                    displayBins: hdRawBinCount
                )
                sourceCounts = try circularGaussianSmooth(
                    spikeCounts.map(Double.init),
                    sigma: sigmaBins
                )
                sourceOccupancy = try circularGaussianSmooth(
                    occupancyTimeSeconds,
                    sigma: sigmaBins
                )
            } else {
                sourceCounts = spikeCounts.map(Double.init)
                sourceOccupancy = occupancyTimeSeconds
            }
            let observations = try aggregateHDTuningObservations(
                spikeCounts: sourceCounts,
                occupancyTimeSeconds: sourceOccupancy,
                displayBins: normalizedBins
            )
            let minimumOccupancy = smoothing ? 1e-12 : 0.0
            return ProcessedTuningCurve(
                anglesDeg: observations.anglesDeg,
                firingRatesHz: zip(
                    observations.spikeCounts,
                    observations.occupancyTimeSeconds
                ).map { count, occupiedSeconds in
                    occupiedSeconds > minimumOccupancy ? count / occupiedSeconds : nil
                }
            )
        }

        let sourceRates: [Double?]
        if smoothing {
            let sigmaBins = try hdTuningSmoothingSigmaBins(
                sigmaAtThirtyBins,
                displayBins: hdRawBinCount
            )
            sourceRates = try circularGaussianSmoothMissingAware(
                unit.firingRatesHz,
                sigma: sigmaBins
            )
        } else {
            sourceRates = unit.firingRatesHz
        }
        return try aggregateLegacyHDTuningRates(
            sourceRates,
            displayBins: normalizedBins
        )
    }

    private static func decode(_ jsonData: Data) throws -> DecodedData {
        let rawRoot: Any
        do {
            rawRoot = try JSONSerialization.jsonObject(with: jsonData)
        } catch {
            throw TuningCurveError.invalidData(
                "Invalid tuning-curve JSON: \(error.localizedDescription)"
            )
        }
        guard let root = rawRoot as? [String: Any], !root.isEmpty else {
            throw TuningCurveError.invalidData(
                "Tuning-curve JSON must be a non-empty cluster mapping."
            )
        }
        try Task.checkCancellation()

        if let rawVersion = root["schema_version"] {
            guard let version = strictInteger(rawVersion), version == 2 else {
                throw TuningCurveError.invalidData(
                    "Unsupported tuning-curve schema version: \(displayValue(rawVersion))."
                )
            }
            return try decodeVersion2(root)
        }
        return try decodeLegacy(root)
    }

    private static func decodeLegacy(_ root: [String: Any]) throws -> DecodedData {
        var unitsByID: [Int: TuningCurveUnit] = [:]
        for rawUnitID in root.keys.sorted() {
            try Task.checkCancellation()
            guard let unitID = Int(rawUnitID.trimmingCharacters(in: .whitespacesAndNewlines)) else {
                throw TuningCurveError.invalidData("Invalid cluster ID: \(rawUnitID).")
            }
            guard unitsByID[unitID] == nil else {
                throw TuningCurveError.invalidData(
                    "Duplicate cluster ID after normalization: \(unitID)"
                )
            }
            guard let rawRates = root[rawUnitID] as? [Any],
                  rawRates.count == hdRawBinCount else {
                let count = (root[rawUnitID] as? [Any])?.count
                throw TuningCurveError.invalidData(
                    "Cluster \(unitID) must contain exactly \(hdRawBinCount) rates; got \(count.map(String.init) ?? "non-list")."
                )
            }
            var rates: [Double?] = []
            rates.reserveCapacity(hdRawBinCount)
            for (index, rawRate) in rawRates.enumerated() {
                guard let rate = finiteNumber(rawRate) else {
                    throw TuningCurveError.invalidData(
                        "Cluster \(unitID) rate \(index + 1) is not numeric."
                    )
                }
                guard rate >= 0.0 else {
                    throw TuningCurveError.invalidData(
                        "Cluster \(unitID) rate \(index + 1) must be finite and non-negative."
                    )
                }
                rates.append(rate)
            }
            unitsByID[unitID] = TuningCurveUnit(
                unitID: unitID,
                spikeCounts: nil,
                firingRatesHz: rates,
                hdClass: nil
            )
        }
        return DecodedData(
            schema: .legacy,
            angleBinEdgesDeg: (0...hdRawBinCount).map { Double($0) * 2.0 },
            occupancyTimeSeconds: nil,
            metadata: nil,
            unitIDs: unitsByID.keys.sorted(),
            unitsByID: unitsByID
        )
    }

    private static func decodeVersion2(_ root: [String: Any]) throws -> DecodedData {
        guard let rawEdges = root["angle_bin_edges_deg"] as? [Any],
              rawEdges.count == hdRawBinCount + 1 else {
            throw TuningCurveError.invalidData(
                "Schema v2 angle_bin_edges_deg must contain \(hdRawBinCount + 1) values."
            )
        }
        let edges = try rawEdges.enumerated().map { index, rawEdge in
            guard let edge = finiteNumber(rawEdge) else {
                throw TuningCurveError.invalidData(
                    "Schema v2 angle edge \(index + 1) is not numeric."
                )
            }
            return edge
        }
        guard zip(edges, edges.dropFirst()).allSatisfy({ $0.0 < $0.1 }) else {
            throw TuningCurveError.invalidData(
                "Schema v2 angle_bin_edges_deg must be strictly increasing."
            )
        }
        guard edges.enumerated().allSatisfy({ index, edge in
            abs(edge - Double(index) * 2.0) <= 1e-8
        }) else {
            throw TuningCurveError.invalidData(
                "Schema v2 angle bins must span 0–360° in 180 equal bins."
            )
        }

        guard let rawOccupancy = root["occupancy_time_s"] as? [Any],
              rawOccupancy.count == hdRawBinCount else {
            throw TuningCurveError.invalidData(
                "Schema v2 occupancy_time_s must contain \(hdRawBinCount) values."
            )
        }
        let occupancy = try rawOccupancy.enumerated().map { index, rawValue in
            guard let value = finiteNumber(rawValue) else {
                throw TuningCurveError.invalidData(
                    "Schema v2 occupancy time \(index + 1) is not numeric."
                )
            }
            guard value >= 0.0 else {
                throw TuningCurveError.invalidData(
                    "Schema v2 occupancy time \(index + 1) must be finite and non-negative."
                )
            }
            return value
        }
        guard occupancy.contains(where: { $0 > 0.0 }) else {
            throw TuningCurveError.invalidData(
                "Schema v2 occupancy_time_s must contain positive occupancy."
            )
        }

        guard let rawUnits = root["units"] as? [Any], !rawUnits.isEmpty else {
            throw TuningCurveError.invalidData("Schema v2 units must be a non-empty list.")
        }
        var unitsByID: [Int: TuningCurveUnit] = [:]
        var unitIDs: [Int] = []
        unitIDs.reserveCapacity(rawUnits.count)
        for (unitIndex, rawUnit) in rawUnits.enumerated() {
            try Task.checkCancellation()
            guard let unitObject = rawUnit as? [String: Any] else {
                throw TuningCurveError.invalidData(
                    "Schema v2 unit \(unitIndex + 1) must be an object."
                )
            }
            guard let rawUnitID = unitObject["unit_id"],
                  let unitID = strictInteger(rawUnitID) else {
                throw TuningCurveError.invalidData(
                    "Schema v2 unit \(unitIndex + 1) has an invalid unit_id."
                )
            }
            guard unitsByID[unitID] == nil else {
                throw TuningCurveError.invalidData("Duplicate schema v2 unit_id: \(unitID)")
            }
            guard let rawCounts = unitObject["spike_counts"] as? [Any],
                  rawCounts.count == hdRawBinCount else {
                throw TuningCurveError.invalidData(
                    "Unit \(unitID) spike_counts must contain \(hdRawBinCount) values."
                )
            }
            guard let rawRates = unitObject["firing_rate_hz"] as? [Any],
                  rawRates.count == hdRawBinCount else {
                throw TuningCurveError.invalidData(
                    "Unit \(unitID) firing_rate_hz must contain \(hdRawBinCount) values."
                )
            }

            var counts: [Int] = []
            var rates: [Double?] = []
            counts.reserveCapacity(hdRawBinCount)
            rates.reserveCapacity(hdRawBinCount)
            for binIndex in 0..<hdRawBinCount {
                guard let count = strictInteger(rawCounts[binIndex]), count >= 0 else {
                    throw TuningCurveError.invalidData(
                        "Unit \(unitID) spike count \(binIndex + 1) must be a non-negative integer."
                    )
                }
                let rawRate = rawRates[binIndex]
                let occupiedSeconds = occupancy[binIndex]
                if occupiedSeconds == 0.0 {
                    guard count == 0, rawRate is NSNull else {
                        throw TuningCurveError.invalidData(
                            "Unit \(unitID) bin \(binIndex + 1) has zero occupancy and must contain count 0 / rate null."
                        )
                    }
                    rates.append(nil)
                } else {
                    guard let rate = finiteNumber(rawRate), rate >= 0.0 else {
                        throw TuningCurveError.invalidData(
                            "Unit \(unitID) firing rate \(binIndex + 1) is not numeric."
                        )
                    }
                    let expectedRate = Double(count) / occupiedSeconds
                    guard numbersAreClose(
                        rate,
                        expectedRate,
                        relativeTolerance: 1e-7,
                        absoluteTolerance: 1e-9
                    ) else {
                        throw TuningCurveError.invalidData(
                            "Unit \(unitID) firing rate \(binIndex + 1) does not match count / occupancy."
                        )
                    }
                    rates.append(rate)
                }
                counts.append(count)
            }

            let hdClass: HDCellClass?
            if let rawClass = unitObject["hd_class"], !(rawClass is NSNull) {
                guard let classValue = strictInteger(rawClass),
                      let parsedClass = HDCellClass(rawValue: classValue) else {
                    throw TuningCurveError.invalidData(
                        "Unit \(unitID) hd_class must be 0, 1, 2, or null."
                    )
                }
                hdClass = parsedClass
            } else {
                hdClass = nil
            }

            unitIDs.append(unitID)
            unitsByID[unitID] = TuningCurveUnit(
                unitID: unitID,
                spikeCounts: counts,
                firingRatesHz: rates,
                hdClass: hdClass
            )
        }

        return DecodedData(
            schema: .version2,
            angleBinEdgesDeg: edges,
            occupancyTimeSeconds: occupancy,
            metadata: try decodeMetadata(root["metadata"]),
            unitIDs: unitIDs,
            unitsByID: unitsByID
        )
    }

    private static func decodeMetadata(_ rawMetadata: Any?) throws -> TuningCurveMetadata? {
        guard let rawMetadata, !(rawMetadata is NSNull) else { return nil }
        guard let object = rawMetadata as? [String: Any] else {
            throw TuningCurveError.invalidData("Schema v2 metadata must be an object or null.")
        }
        return TuningCurveMetadata(
            session: try optionalString(object, key: "session", context: "metadata"),
            probe: try optionalString(object, key: "probe", context: "metadata"),
            kilosortDirectory: try optionalString(object, key: "kilosort_dir", context: "metadata"),
            timebase: try optionalString(object, key: "timebase", context: "metadata"),
            adcTimeOriginRawSeconds: try optionalFiniteNumber(
                object,
                key: "adc_time_origin_raw_s",
                context: "metadata"
            ),
            timestampReference: try optionalString(
                object,
                key: "timestamp_reference",
                context: "metadata"
            ),
            angleConventionNote: try optionalString(
                object,
                key: "angle_convention_note",
                context: "metadata"
            ),
            numberOfAngleBins: try optionalInteger(
                object,
                key: "num_angle_bins",
                context: "metadata"
            ),
            featureRateHz: try optionalFiniteNumber(
                object,
                key: "feature_fs_hz",
                context: "metadata"
            ),
            classification: try decodeClassification(object["classification"]),
            ttlQC: try decodeTTLQC(object["ttl_qc"])
        )
    }

    private static func decodeClassification(
        _ rawClassification: Any?
    ) throws -> TuningCurveClassificationProvenance? {
        guard let rawClassification, !(rawClassification is NSNull) else { return nil }
        guard let object = rawClassification as? [String: Any] else {
            throw TuningCurveError.invalidData(
                "Schema v2 metadata.classification must be an object or null."
            )
        }
        return TuningCurveClassificationProvenance(
            method: try optionalString(object, key: "method", context: "metadata.classification"),
            class0Description: try optionalString(object, key: "class_0", context: "metadata.classification"),
            class1Description: try optionalString(object, key: "class_1", context: "metadata.classification"),
            class2Description: try optionalString(object, key: "class_2", context: "metadata.classification"),
            classNullDescription: try optionalString(object, key: "class_null", context: "metadata.classification"),
            rayleighAlpha: try optionalFiniteNumber(object, key: "rayleigh_alpha", context: "metadata.classification"),
            rayleighTest: try optionalString(object, key: "rayleigh_test", context: "metadata.classification"),
            shuffleAlpha: try optionalFiniteNumber(object, key: "shuffle_alpha", context: "metadata.classification"),
            numberOfShuffles: try optionalInteger(object, key: "num_shuffle", context: "metadata.classification"),
            shuffleSeed: try optionalInteger(object, key: "shuffle_seed", context: "metadata.classification")
        )
    }

    private static func decodeTTLQC(_ rawTTLQC: Any?) throws -> TuningCurveTTLProvenance? {
        guard let rawTTLQC, !(rawTTLQC is NSNull) else { return nil }
        guard let object = rawTTLQC as? [String: Any] else {
            throw TuningCurveError.invalidData(
                "Schema v2 metadata.ttl_qc must be an object or null."
            )
        }
        return TuningCurveTTLProvenance(
            pulseCount: try optionalInteger(object, key: "ttl_pulse_count", context: "metadata.ttl_qc"),
            firstExposureSeconds: try optionalFiniteNumber(object, key: "first_exposure_s", context: "metadata.ttl_qc"),
            lastExposureSeconds: try optionalFiniteNumber(object, key: "last_exposure_s", context: "metadata.ttl_qc"),
            medianPeriodSeconds: try optionalFiniteNumber(object, key: "median_period_s", context: "metadata.ttl_qc"),
            measuredRateHz: try optionalFiniteNumber(object, key: "measured_rate_hz", context: "metadata.ttl_qc"),
            cameraInputChannel: try optionalInteger(object, key: "camera_input_channel", context: "metadata.ttl_qc"),
            cameraTTLThreshold: try optionalFiniteNumber(object, key: "camera_ttl_threshold", context: "metadata.ttl_qc"),
            cameraTTLActiveHigh: try optionalBoolean(object, key: "camera_ttl_active_high", context: "metadata.ttl_qc"),
            rawMotiveFrameCount: try optionalInteger(object, key: "motive_frame_count_raw", context: "metadata.ttl_qc"),
            matchedMotiveFrameCount: try optionalInteger(object, key: "matched_motive_frame_count", context: "metadata.ttl_qc"),
            droppedMotiveFrameIDs: try optionalIntegerArray(object, key: "dropped_motive_frame_ids", context: "metadata.ttl_qc"),
            frameAlignmentPolicyRequested: try optionalString(object, key: "frame_alignment_policy_requested", context: "metadata.ttl_qc"),
            frameAlignmentPolicyApplied: try optionalString(object, key: "frame_alignment_policy_applied", context: "metadata.ttl_qc"),
            frameTimestampMapping: try optionalString(object, key: "frame_timestamp_mapping", context: "metadata.ttl_qc")
        )
    }

    private static func optionalString(
        _ object: [String: Any],
        key: String,
        context: String
    ) throws -> String? {
        guard let rawValue = object[key], !(rawValue is NSNull) else { return nil }
        guard let value = rawValue as? String else {
            throw TuningCurveError.invalidData("\(context).\(key) must be a string or null.")
        }
        return value
    }

    private static func optionalFiniteNumber(
        _ object: [String: Any],
        key: String,
        context: String
    ) throws -> Double? {
        guard let rawValue = object[key], !(rawValue is NSNull) else { return nil }
        guard let value = finiteNumber(rawValue) else {
            throw TuningCurveError.invalidData(
                "\(context).\(key) must be a finite number or null."
            )
        }
        return value
    }

    private static func optionalInteger(
        _ object: [String: Any],
        key: String,
        context: String
    ) throws -> Int? {
        guard let rawValue = object[key], !(rawValue is NSNull) else { return nil }
        guard let value = strictInteger(rawValue) else {
            throw TuningCurveError.invalidData("\(context).\(key) must be an integer or null.")
        }
        return value
    }

    private static func optionalBoolean(
        _ object: [String: Any],
        key: String,
        context: String
    ) throws -> Bool? {
        guard let rawValue = object[key], !(rawValue is NSNull) else { return nil }
        guard let number = rawValue as? NSNumber,
              CFGetTypeID(number) == CFBooleanGetTypeID() else {
            throw TuningCurveError.invalidData("\(context).\(key) must be a boolean or null.")
        }
        return number.boolValue
    }

    private static func optionalIntegerArray(
        _ object: [String: Any],
        key: String,
        context: String
    ) throws -> [Int]? {
        guard let rawValue = object[key], !(rawValue is NSNull) else { return nil }
        guard let values = rawValue as? [Any] else {
            throw TuningCurveError.invalidData(
                "\(context).\(key) must be an integer list or null."
            )
        }
        return try values.map { rawElement in
            guard let value = strictInteger(rawElement) else {
                throw TuningCurveError.invalidData(
                    "\(context).\(key) must be an integer list or null."
                )
            }
            return value
        }
    }

    private static func strictInteger(_ rawValue: Any) -> Int? {
        guard let number = rawValue as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        let type = String(cString: number.objCType)
        guard !["d", "f"].contains(type) else { return nil }
        if ["Q", "L", "I", "S", "C"].contains(type) {
            let value = number.uint64Value
            guard value <= UInt64(Int.max) else { return nil }
            return Int(value)
        }
        let value = number.int64Value
        return Int(exactly: value)
    }

    private static func finiteNumber(_ rawValue: Any) -> Double? {
        guard let number = rawValue as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        let value = number.doubleValue
        return value.isFinite ? value : nil
    }

    private static func numbersAreClose(
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

    private static func displayValue(_ rawValue: Any) -> String {
        if rawValue is NSNull { return "null" }
        return String(describing: rawValue)
    }
}

func normalizeHDTuningBinCount(_ value: Int) -> Int {
    let requested = max(1, min(hdRawBinCount, value))
    return hdDisplayBinDivisors.last(where: { $0 <= requested }) ?? 1
}

func hdTuningSmoothingSigmaBins(
    _ sigmaAtThirtyBins: Double,
    displayBins: Int
) throws -> Double {
    guard sigmaAtThirtyBins.isFinite, sigmaAtThirtyBins > 0.0 else {
        throw TuningCurveError.invalidData(
            "Tuning-curve smoothing sigma must be positive and finite."
        )
    }
    let normalizedBins = normalizeHDTuningBinCount(displayBins)
    let sigma = sigmaAtThirtyBins
        * Double(normalizedBins)
        / Double(defaultHDTuningDisplayBins)
    guard sigma.isFinite, sigma > 0.0 else {
        throw TuningCurveError.invalidData(
            "Tuning-curve smoothing sigma must be positive and finite."
        )
    }
    return sigma
}

func circularGaussianSmooth(_ values: [Double], sigma: Double) throws -> [Double] {
    guard !values.isEmpty else { return [] }
    guard sigma.isFinite, sigma > 0.0 else {
        throw TuningCurveError.invalidData(
            "Tuning-curve smoothing sigma must be positive and finite."
        )
    }
    let radiusValue = 4.0 * sigma + 0.5
    guard radiusValue.isFinite, radiusValue <= 1_000_000 else {
        throw TuningCurveError.invalidData("Tuning-curve smoothing sigma is too large.")
    }
    let radius = Int(radiusValue)
    guard radius > 0 else { return values }

    let scale = -0.5 / (sigma * sigma)
    var weights = (-radius...radius).map { offset in
        exp(scale * Double(offset * offset))
    }
    let weightTotal = compensatedSum(weights)
    weights = weights.map { $0 / weightTotal }

    let count = values.count
    return values.indices.map { index in
        var total = 0.0
        for offset in -radius...radius {
            let wrappedIndex = ((index + offset) % count + count) % count
            total += values[wrappedIndex] * weights[offset + radius]
        }
        return total
    }
}

func circularGaussianSmoothMissingAware(
    _ values: [Double?],
    sigma: Double
) throws -> [Double?] {
    let observed = values.map { value -> Double in
        guard let value, value.isFinite else { return 0.0 }
        return 1.0
    }
    let numerator = try circularGaussianSmooth(
        values.map { value in
            guard let value, value.isFinite else { return 0.0 }
            return value
        },
        sigma: sigma
    )
    let denominator = try circularGaussianSmooth(observed, sigma: sigma)
    return zip(numerator, denominator).map { value, weight in
        weight > 1e-12 ? value / weight : nil
    }
}

private struct AggregatedHDTuningObservations {
    let anglesDeg: [Double]
    let spikeCounts: [Double]
    let occupancyTimeSeconds: [Double]
}

private func aggregateHDTuningObservations(
    spikeCounts: [Double],
    occupancyTimeSeconds: [Double],
    displayBins: Int
) throws -> AggregatedHDTuningObservations {
    guard spikeCounts.count == hdRawBinCount else {
        throw TuningCurveError.invalidData(
            "Expected \(hdRawBinCount) spike-count bins; got \(spikeCounts.count)."
        )
    }
    guard occupancyTimeSeconds.count == hdRawBinCount else {
        throw TuningCurveError.invalidData(
            "Expected \(hdRawBinCount) occupancy-time bins; got \(occupancyTimeSeconds.count)."
        )
    }
    let bins = normalizeHDTuningBinCount(displayBins)
    let groupSize = hdRawBinCount / bins
    var counts: [Double] = []
    var occupancy: [Double] = []
    counts.reserveCapacity(bins)
    occupancy.reserveCapacity(bins)
    for start in stride(from: 0, to: hdRawBinCount, by: groupSize) {
        let end = start + groupSize
        counts.append(compensatedSum(Array(spikeCounts[start..<end])))
        occupancy.append(compensatedSum(Array(occupancyTimeSeconds[start..<end])))
    }
    let width = 360.0 / Double(bins)
    return AggregatedHDTuningObservations(
        anglesDeg: (0..<bins).map { (Double($0) + 0.5) * width },
        spikeCounts: counts,
        occupancyTimeSeconds: occupancy
    )
}

private func aggregateLegacyHDTuningRates(
    _ rates: [Double?],
    displayBins: Int
) throws -> ProcessedTuningCurve {
    guard rates.count == hdRawBinCount else {
        throw TuningCurveError.invalidData(
            "Expected \(hdRawBinCount) raw HD rates; got \(rates.count)."
        )
    }
    let bins = normalizeHDTuningBinCount(displayBins)
    let groupSize = hdRawBinCount / bins
    let values = stride(from: 0, to: hdRawBinCount, by: groupSize).map { start -> Double? in
        let group = rates[start..<(start + groupSize)].compactMap { $0 }
        guard !group.isEmpty else { return nil }
        return compensatedSum(group) / Double(group.count)
    }
    let width = 360.0 / Double(bins)
    return ProcessedTuningCurve(
        anglesDeg: (0..<bins).map { (Double($0) + 0.5) * width },
        firingRatesHz: values
    )
}
