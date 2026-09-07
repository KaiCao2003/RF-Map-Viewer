import Foundation

public enum HDTuningError: LocalizedError, Equatable, Sendable {
    case invalidData(String)
    case missingUnit(Int, available: [Int])

    public var errorDescription: String? {
        switch self {
        case .invalidData(let message):
            return message
        case .missingUnit(let unitID, let available):
            return "HD unit \(unitID) is unavailable. Available unit IDs: \(available)."
        }
    }
}

/// A lossless representation of metadata values carried by a tuning-curve file.
public indirect enum TuningCurveJSONValue: Codable, Equatable, Sendable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([TuningCurveJSONValue])
    case object([String: TuningCurveJSONValue])

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([TuningCurveJSONValue].self) {
            self = .array(value)
        } else if let value = try? container.decode([String: TuningCurveJSONValue].self) {
            self = .object(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unsupported tuning-curve metadata value."
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .null:
            try container.encodeNil()
        case .bool(let value):
            try container.encode(value)
        case .number(let value):
            try container.encode(value)
        case .string(let value):
            try container.encode(value)
        case .array(let value):
            try container.encode(value)
        case .object(let value):
            try container.encode(value)
        }
    }
}

public struct HDTuningUnit: Equatable, Sendable {
    public let unitID: Int
    public let spikeCounts: [Double]
    /// `nil` is valid only for a zero-occupancy bin, matching JSON `null`.
    public let rawRatesHz: [Double?]
    public let hdClass: Int?
    public let metrics: [String: TuningCurveJSONValue]
}

public struct ProcessedHDCurve: Equatable, Sendable {
    public let anglesDegrees: [Double]
    public let ratesHz: [Double]

    public init(anglesDegrees: [Double], ratesHz: [Double]) {
        self.anglesDegrees = anglesDegrees
        self.ratesHz = ratesHz
    }

    /// Returns samples shifted into calibrated display coordinates. Positive
    /// offsets rotate clockwise/right when zero degrees is drawn at the top.
    public func applyingAngleOffset(_ offsetDegrees: Double) -> ProcessedHDCurve {
        let offset = offsetDegrees.isFinite ? offsetDegrees : 0
        let shifted = zip(anglesDegrees, ratesHz)
            .map { angle, rate in
                (HDTuningData.normalizedAngleDegrees(angle + offset), rate)
            }
            .sorted { left, right in left.0 < right.0 }
        return ProcessedHDCurve(
            anglesDegrees: shifted.map(\.0),
            ratesHz: shifted.map(\.1)
        )
    }
}

private struct HDTuningPayload: Decodable {
    let metadata: [String: TuningCurveJSONValue]
    let angleBinEdgesDegrees: [Double]
    let occupancyTimeSeconds: [Double]
    let unitIDs: [Int]
    let spikeCounts: [[Double]]
    let firingRatesHz: [[Double?]]
    let unitData: [String: [TuningCurveJSONValue]]

    private enum CodingKeys: String, CodingKey {
        case metadata
        case angleBinEdgesDegrees = "angle_bin_edges_deg"
        case occupancyTimeSeconds = "occupancy_time_s"
        case unitIDs = "unit_id"
        case spikeCounts = "spike_counts"
        case firingRatesHz = "firing_rate_hz"
        case unitData = "unit_data"
    }
}

public struct HDTuningData: Sendable {
    public static let rawBinCount = 180
    public static let defaultDisplayBins = 30
    public static let defaultSmoothSigma = 1.5

    public static let validDisplayBinCounts: [Int] = (1...rawBinCount).filter {
        rawBinCount.isMultiple(of: $0)
    }

    public let sourceURL: URL
    public let angleBinEdgesDegrees: [Double]
    public let occupancyTimeSeconds: [Double]
    public let unitIDs: [Int]
    public let metadata: [String: TuningCurveJSONValue]
    private let unitsByID: [Int: HDTuningUnit]

    public init(data: Data, sourceURL: URL) throws {
        let payload: HDTuningPayload
        do {
            payload = try JSONDecoder().decode(HDTuningPayload.self, from: data)
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
            let counts = payload.spikeCounts[index]
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
            var metrics: [String: TuningCurveJSONValue] = [:]
            for (key, column) in payload.unitData {
                metrics[key] = column[index]
            }
            let hdClass: Int?
            switch metrics["hd_class"] {
            case nil, .some(.null):
                hdClass = nil
            case .some(.number(let value)):
                guard let exact = Int(exactly: value) else {
                    throw HDTuningError.invalidData(
                        "HD unit \(unitID) hd_class must be an in-range integer or null."
                    )
                }
                hdClass = exact
            default:
                throw HDTuningError.invalidData(
                    "HD unit \(unitID) hd_class must be an in-range integer or null."
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
        occupancyTimeSeconds = payload.occupancyTimeSeconds
        unitIDs = payload.unitIDs
        metadata = payload.metadata
        self.unitsByID = unitsByID
    }

    public init(url: URL) throws {
        try self.init(
            data: Data(contentsOf: url, options: .mappedIfSafe),
            sourceURL: url
        )
    }

    public func unit(byID unitID: Int) throws -> HDTuningUnit {
        guard let unit = unitsByID[unitID] else {
            throw HDTuningError.missingUnit(unitID, available: unitIDs)
        }
        return unit
    }

    public func processedCurve(
        unitID: Int,
        displayBins: Int = HDTuningData.defaultDisplayBins,
        smoothing: Bool = true,
        sigma: Double = HDTuningData.defaultSmoothSigma
    ) throws -> ProcessedHDCurve {
        let unit = try unit(byID: unitID)
        let bins = Self.normalizedDisplayBinCount(displayBins)
        var counts = unit.spikeCounts
        var occupancy = occupancyTimeSeconds
        if smoothing {
            guard sigma.isFinite, sigma > 0 else {
                throw HDTuningError.invalidData(
                    "HD smoothing sigma must be positive and finite."
                )
            }
            let rawSigma = sigma * Double(Self.rawBinCount) / Double(Self.defaultDisplayBins)
            guard rawSigma.isFinite,
                  rawSigma <= Double(Int.max - 1) / 4 else {
                throw HDTuningError.invalidData("HD smoothing sigma is too large.")
            }
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
                groupedOccupancy > 1e-12 ? groupedCounts / groupedOccupancy : 0.0
            )
        }
        let width = 360.0 / Double(bins)
        let angles = (0..<bins).map { (Double($0) + 0.5) * width }
        return ProcessedHDCurve(anglesDegrees: angles, ratesHz: groupedRates)
    }

    public static func normalizedDisplayBinCount(_ requested: Int) -> Int {
        let clamped = max(1, min(rawBinCount, requested))
        for candidate in stride(from: clamped, through: 1, by: -1)
            where rawBinCount.isMultiple(of: candidate) {
            return candidate
        }
        return 1
    }

    public static func normalizedAngleDegrees(_ degrees: Double) -> Double {
        guard degrees.isFinite else { return 0 }
        let remainder = degrees.truncatingRemainder(dividingBy: 360)
        return remainder >= 0 ? remainder : remainder + 360
    }

    public static func smoothCircular(_ values: [Double], sigma: Double) -> [Double] {
        guard !values.isEmpty,
              sigma.isFinite,
              sigma > 0,
              sigma <= Double(Int.max - 1) / 4 else { return values }
        let radius = Int(floor(4.0 * sigma + 0.5))
        let offsets = Array(-radius...radius)
        var weights = offsets.map { exp(-0.5 * pow(Double($0) / sigma, 2)) }
        let weightSum = compensatedSum(weights)
        weights = weights.map { $0 / weightSum }
        var result = Array(repeating: 0.0, count: values.count)
        for index in values.indices {
            for (offset, weight) in zip(offsets, weights) {
                let source = ((index + offset) % values.count + values.count) % values.count
                result[index] += weight * values[source]
            }
        }
        return result
    }
}

private func compensatedSum<S: Sequence>(_ values: S) -> Double where S.Element == Double {
    var high = 0.0
    var low = 0.0
    for value in values {
        let next = high + value
        if !next.isFinite {
            high = next
            low = 0
            continue
        }
        if abs(high) >= abs(value) {
            low += (high - next) + value
        } else {
            low += (value - next) + high
        }
        high = next
    }
    return high + low
}
