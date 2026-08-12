import Foundation

/// A lossless-enough, Sendable representation of arbitrary JSON metadata.
/// Structural RF fields are modeled explicitly and therefore excluded from
/// `RFMap.metadata`.
indirect enum RFMapJSONValue: Codable, Equatable, Sendable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([RFMapJSONValue])
    case object([String: RFMapJSONValue])

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([RFMapJSONValue].self) {
            self = .array(value)
        } else if let value = try? container.decode([String: RFMapJSONValue].self) {
            self = .object(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unsupported JSON metadata value."
            )
        }
    }

    func encode(to encoder: Encoder) throws {
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

enum RFMapError: LocalizedError, Equatable {
    case invalidData(String)
    case invalidTimeRange(String)
    case missingTimeEdge(String)
    case ambiguousTimeEdge(String)
    case missingOriginalIndex(Int, available: [Int])
    case missingUnitID(Int, available: [Int])

    var errorDescription: String? {
        switch self {
        case .invalidData(let message),
             .invalidTimeRange(let message),
             .missingTimeEdge(let message),
             .ambiguousTimeEdge(let message):
            return message
        case .missingOriginalIndex(let index, let available):
            return "Original unit index \(index) is unavailable. Available original indices: \(available)."
        case .missingUnitID(let unitID, let available):
            return "Unit ID \(unitID) is unavailable. Available unit IDs: \(available)."
        }
    }
}

struct RFMapBumpDetection: Equatable, Sendable {
    /// Full `(y, x, time)` mask. Values are exactly zero or one.
    let mask: [[[UInt8]]]
    let baselineMean: Double
    let threshold: Double
    /// Non-nil when the threshold also marks one or more baseline bins.
    let warning: String?
}

/// RF mapping data for exactly one unit.
///
/// `spikeCounts` always has `(y, x, time)` axes. A time-summed map keeps a
/// singleton time axis so it remains an `RFMap`.
struct RFMap: Sendable {
    static let edgeToleranceSeconds = 1e-12
    static let structuralJSONKeys: Set<String> = [
        "unitsSpikeCounts",
        "unitsSpikeCountsSize",
        "unitPool",
        "xPositions",
        "yPositions",
        "timeBinEdges",
        "stimulusPresentationCounts",
    ]

    let unitIndex: Int
    let unitID: Int
    let spikeCounts: [[[Double]]]
    let xPositions: [Double]
    let yPositions: [Double]
    let timeBinEdgesSeconds: [Double]
    let presentationCounts: [[Double]]?
    let metadata: [String: RFMapJSONValue]
    let sourceURL: URL

    var nY: Int { spikeCounts.count }
    var nX: Int { spikeCounts.first?.count ?? 0 }
    var nTimeBins: Int { spikeCounts.first?.first?.count ?? 0 }

    var timeBinCentersSeconds: [Double] {
        zip(timeBinEdgesSeconds, timeBinEdgesSeconds.dropFirst()).map { pair in
            (pair.0 + pair.1) / 2.0
        }
    }

    var timeBinWidthsSeconds: [Double] {
        zip(timeBinEdgesSeconds, timeBinEdgesSeconds.dropFirst()).map { pair in
            pair.1 - pair.0
        }
    }

    init(
        unitIndex: Int,
        unitID: Int,
        spikeCounts: [[[Double]]],
        xPositions: [Double],
        yPositions: [Double],
        timeBinEdgesSeconds: [Double],
        presentationCounts: [[Double]]?,
        metadata: [String: RFMapJSONValue] = [:],
        sourceURL: URL
    ) throws {
        try Self.validate(
            spikeCounts: spikeCounts,
            xPositions: xPositions,
            yPositions: yPositions,
            timeBinEdgesSeconds: timeBinEdgesSeconds,
            presentationCounts: presentationCounts,
            allowZeroWidthSingleBin: false
        )
        self.unitIndex = unitIndex
        self.unitID = unitID
        self.spikeCounts = spikeCounts
        self.xPositions = xPositions
        self.yPositions = yPositions
        self.timeBinEdgesSeconds = timeBinEdgesSeconds
        self.presentationCounts = presentationCounts
        self.metadata = metadata
        self.sourceURL = sourceURL.standardizedFileURL
    }

    private init(
        summedFrom source: RFMap,
        spikeCounts: [[[Double]]],
        timeBinEdgesSeconds: [Double],
        metadata: [String: RFMapJSONValue]
    ) {
        unitIndex = source.unitIndex
        unitID = source.unitID
        self.spikeCounts = spikeCounts
        xPositions = source.xPositions
        yPositions = source.yPositions
        self.timeBinEdgesSeconds = timeBinEdgesSeconds
        presentationCounts = source.presentationCounts
        self.metadata = metadata
        sourceURL = source.sourceURL
    }

    /// Returns a new one-bin RF map summed over the strict half-open interval
    /// `[earlierSeconds, laterSeconds)`. Both endpoints must match source edges
    /// within an absolute `1e-12` seconds and are never snapped or clamped.
    func sumBetweenSeconds(_ earlierSeconds: Double, _ laterSeconds: Double) throws -> RFMap {
        let resolved = try timeIndices(
            earlierSeconds: earlierSeconds,
            laterSeconds: laterSeconds,
            allowEmpty: true
        )

        var summed = Array(
            repeating: Array(repeating: [0.0], count: nX),
            count: nY
        )
        if resolved.start < resolved.stop {
            for yIndex in 0..<nY {
                for xIndex in 0..<nX {
                    summed[yIndex][xIndex][0] = compensatedSum(
                        spikeCounts[yIndex][xIndex][resolved.start..<resolved.stop]
                    )
                }
            }
        }

        var summedMetadata = metadata
        if summedMetadata["VSTimeWindow"] != nil {
            summedMetadata["VSTimeWindow"] = .array([
                .number(resolved.canonicalStart),
                .number(resolved.canonicalStop),
            ])
        }
        if summedMetadata["timeWindowMs"] != nil {
            summedMetadata["timeWindowMs"] = .array([
                .number(resolved.canonicalStart * 1_000.0),
                .number(resolved.canonicalStop * 1_000.0),
            ])
        }
        if summedMetadata["timeBinWidthMs"] != nil {
            summedMetadata["timeBinWidthMs"] = .number(
                (resolved.canonicalStop - resolved.canonicalStart) * 1_000.0
            )
        }

        // The equal-endpoint result deliberately has [t, t] edges. It is the
        // sole valid zero-width RFMap representation.
        return RFMap(
            summedFrom: self,
            spikeCounts: summed,
            timeBinEdgesSeconds: [resolved.canonicalStart, resolved.canonicalStop],
            metadata: summedMetadata
        )
    }

    /// Thresholds every bin using `mean(baseline) * thresholdRatio`.
    /// Comparison is strict (`>`), and the returned mask retains all bins.
    func detectBumps(
        thresholdRatio: Double = 1.2,
        baselineStartSeconds: Double = -0.1,
        baselineEndSeconds: Double = 0.0
    ) throws -> RFMapBumpDetection {
        guard thresholdRatio.isFinite, thresholdRatio > 1.0 else {
            throw RFMapError.invalidData("thresholdRatio must be finite and greater than 1.")
        }
        let resolved = try timeIndices(
            earlierSeconds: baselineStartSeconds,
            laterSeconds: baselineEndSeconds,
            allowEmpty: false
        )

        var baselineValues: [Double] = []
        baselineValues.reserveCapacity(nY * nX * (resolved.stop - resolved.start))
        for yIndex in 0..<nY {
            for xIndex in 0..<nX {
                baselineValues.append(contentsOf: spikeCounts[yIndex][xIndex][resolved.start..<resolved.stop])
            }
        }
        let baselineMean = compensatedSum(baselineValues) / Double(baselineValues.count)
        let threshold = baselineMean * thresholdRatio
        var baselineContainsBump = false
        let mask: [[[UInt8]]] = spikeCounts.map { row in
            row.map { histogram in
                histogram.enumerated().map { binIndex, value in
                    let detected: UInt8 = value > threshold ? 1 : 0
                    if detected == 1,
                       resolved.start <= binIndex,
                       binIndex < resolved.stop {
                        baselineContainsBump = true
                    }
                    return detected
                }
            }
        }
        let warning = baselineContainsBump
            ? "RFMap unit \(unitID) has detected bumps inside the baseline window; "
                + "thresholdRatio=\(thresholdRatio) may be too low. Increase the threshold ratio "
                + "if baseline bins should all be zero."
            : nil
        return RFMapBumpDetection(
            mask: mask,
            baselineMean: baselineMean,
            threshold: threshold,
            warning: warning
        )
    }

    /// Returns thresholded 2-D local maxima for every time bin.
    ///
    /// This is the Swift equivalent of applying SciPy's `maximum_filter` with
    /// `size=(spatialSize, spatialSize, 1)` and `mode="nearest"`, then
    /// intersecting it with `detectBumps`. Time bins never compete with one
    /// another, and every point in a flat maximum plateau is retained.
    func detectSpatialBumps(
        thresholdRatio: Double = 1.2,
        spatialSize: Int = 3,
        baselineStartSeconds: Double = -0.1,
        baselineEndSeconds: Double = 0.0
    ) throws -> RFMapBumpDetection {
        try detectSpatialBumps(
            thresholdRatio: thresholdRatio,
            spatialSize: (y: spatialSize, x: spatialSize),
            baselineStartSeconds: baselineStartSeconds,
            baselineEndSeconds: baselineEndSeconds
        )
    }

    /// An anisotropic spatial-window overload. Both dimensions must be
    /// positive odd integers and are ordered explicitly as `(y, x)`.
    func detectSpatialBumps(
        thresholdRatio: Double = 1.2,
        spatialSize: (y: Int, x: Int),
        baselineStartSeconds: Double = -0.1,
        baselineEndSeconds: Double = 0.0
    ) throws -> RFMapBumpDetection {
        try Self.validateSpatialSize(spatialSize.y, label: "spatialSize y")
        try Self.validateSpatialSize(spatialSize.x, label: "spatialSize x")

        let thresholdDetection = try detectBumps(
            thresholdRatio: thresholdRatio,
            baselineStartSeconds: baselineStartSeconds,
            baselineEndSeconds: baselineEndSeconds
        )
        let yRadius = spatialSize.y / 2
        let xRadius = spatialSize.x / 2
        var mask = Array(
            repeating: Array(
                repeating: Array(repeating: UInt8(0), count: nTimeBins),
                count: nX
            ),
            count: nY
        )

        for timeIndex in 0..<nTimeBins {
            for yIndex in 0..<nY {
                let lowerY = yRadius > yIndex ? 0 : yIndex - yRadius
                let upperY = min(nY - 1, yIndex + yRadius)
                for xIndex in 0..<nX {
                    guard thresholdDetection.mask[yIndex][xIndex][timeIndex] != 0 else {
                        continue
                    }
                    let lowerX = xRadius > xIndex ? 0 : xIndex - xRadius
                    let upperX = min(nX - 1, xIndex + xRadius)
                    var localMaximum = -Double.infinity
                    for neighborY in lowerY...upperY {
                        for neighborX in lowerX...upperX {
                            localMaximum = max(
                                localMaximum,
                                spikeCounts[neighborY][neighborX][timeIndex]
                            )
                        }
                    }
                    if spikeCounts[yIndex][xIndex][timeIndex] == localMaximum {
                        mask[yIndex][xIndex][timeIndex] = 1
                    }
                }
            }
        }

        return RFMapBumpDetection(
            mask: mask,
            baselineMean: thresholdDetection.baselineMean,
            threshold: thresholdDetection.threshold,
            warning: thresholdDetection.warning
        )
    }

    private static func validateSpatialSize(_ value: Int, label: String) throws {
        guard value > 0, value.isMultiple(of: 2) == false else {
            throw RFMapError.invalidData("\(label) must be a positive odd integer.")
        }
    }

    private func timeIndices(
        earlierSeconds: Double,
        laterSeconds: Double,
        allowEmpty: Bool
    ) throws -> (
        start: Int,
        stop: Int,
        canonicalStart: Double,
        canonicalStop: Double
    ) {
        guard earlierSeconds.isFinite, laterSeconds.isFinite else {
            throw RFMapError.invalidTimeRange(
                "Time endpoints must be finite. \(availableEdgesMessage)"
            )
        }
        if laterSeconds < earlierSeconds || (!allowEmpty && laterSeconds == earlierSeconds) {
            let relation = allowEmpty ? ">=" : ">"
            throw RFMapError.invalidTimeRange(
                "laterSeconds must be \(relation) earlierSeconds; received "
                    + "\(earlierSeconds), \(laterSeconds). \(availableEdgesMessage)"
            )
        }

        let start = try edgeIndex(for: earlierSeconds, label: "earlierSeconds")
        let stop = try edgeIndex(for: laterSeconds, label: "laterSeconds")
        if stop < start || (!allowEmpty && stop == start) {
            let relation = allowEmpty ? ">=" : ">"
            throw RFMapError.invalidTimeRange(
                "laterSeconds must resolve to an edge \(relation) the earlierSeconds edge. "
                    + availableEdgesMessage
            )
        }
        return (start, stop, timeBinEdgesSeconds[start], timeBinEdgesSeconds[stop])
    }

    private func edgeIndex(for value: Double, label: String) throws -> Int {
        let exact = timeBinEdgesSeconds.indices.filter { timeBinEdgesSeconds[$0] == value }
        if let first = exact.first {
            // A zero-width summed RFMap contains [t, t]. The first edge makes
            // [t, t) resolve to the intended empty slice.
            return first
        }
        let matches = timeBinEdgesSeconds.indices.filter {
            abs(timeBinEdgesSeconds[$0] - value) <= Self.edgeToleranceSeconds
        }
        guard !matches.isEmpty else {
            throw RFMapError.missingTimeEdge(
                "\(label)=\(value) is not in timeBinEdges. \(availableEdgesMessage)"
            )
        }
        guard matches.count == 1 else {
            let values = matches.map { timeBinEdgesSeconds[$0] }
            throw RFMapError.ambiguousTimeEdge(
                "\(label)=\(value) is within \(Self.edgeToleranceSeconds) seconds of multiple "
                    + "timeBinEdges \(values); use an exact edge value. \(availableEdgesMessage)"
            )
        }
        return matches[0]
    }

    private var availableEdgesMessage: String {
        "Available time bin edges (s): \(timeBinEdgesSeconds)"
    }

    private static func validate(
        spikeCounts: [[[Double]]],
        xPositions: [Double],
        yPositions: [Double],
        timeBinEdgesSeconds: [Double],
        presentationCounts: [[Double]]?,
        allowZeroWidthSingleBin: Bool
    ) throws {
        let nY = spikeCounts.count
        guard nY > 0 else { throw RFMapError.invalidData("spikeCounts y dimension must be positive.") }
        let nX = spikeCounts[0].count
        guard nX > 0 else { throw RFMapError.invalidData("spikeCounts x dimension must be positive.") }
        let nBins = spikeCounts[0][0].count
        guard nBins > 0 else { throw RFMapError.invalidData("spikeCounts time dimension must be positive.") }
        guard yPositions.count == nY, yPositions.allSatisfy(\.isFinite) else {
            throw RFMapError.invalidData("yPositions must match the y dimension and be finite.")
        }
        guard xPositions.count == nX, xPositions.allSatisfy(\.isFinite) else {
            throw RFMapError.invalidData("xPositions must match the x dimension and be finite.")
        }
        guard timeBinEdgesSeconds.count == nBins + 1,
              timeBinEdgesSeconds.allSatisfy(\.isFinite) else {
            throw RFMapError.invalidData("timeBinEdges must contain nTimeBins + 1 finite edges.")
        }
        let strictlyIncreasing = zip(timeBinEdgesSeconds, timeBinEdgesSeconds.dropFirst())
            .allSatisfy { pair in pair.0 < pair.1 }
        let validZeroWidth = allowZeroWidthSingleBin
            && nBins == 1
            && timeBinEdgesSeconds[0] == timeBinEdgesSeconds[1]
        guard strictlyIncreasing || validZeroWidth else {
            throw RFMapError.invalidData("timeBinEdges must be strictly increasing.")
        }
        for (yIndex, row) in spikeCounts.enumerated() {
            guard row.count == nX else {
                throw RFMapError.invalidData("spikeCounts row \(yIndex) has the wrong x dimension.")
            }
            for (xIndex, histogram) in row.enumerated() {
                guard histogram.count == nBins else {
                    throw RFMapError.invalidData(
                        "spikeCounts cell (\(yIndex), \(xIndex)) has the wrong time dimension."
                    )
                }
                guard histogram.allSatisfy({ $0.isFinite && $0 >= 0 }) else {
                    throw RFMapError.invalidData("spikeCounts values must be finite and non-negative.")
                }
            }
        }
        if let presentationCounts {
            guard presentationCounts.count == nY,
                  presentationCounts.allSatisfy({ $0.count == nX }) else {
                throw RFMapError.invalidData("presentationCounts must match the y-by-x dimensions.")
            }
            for yIndex in 0..<nY {
                for xIndex in 0..<nX {
                    let presentations = presentationCounts[yIndex][xIndex]
                    guard presentations.isFinite,
                          presentations >= 0,
                          presentations == presentations.rounded() else {
                        throw RFMapError.invalidData(
                            "presentationCounts values must be finite, non-negative integers."
                        )
                    }
                    if presentations == 0,
                       spikeCounts[yIndex][xIndex].contains(where: { $0 != 0 }) {
                        throw RFMapError.invalidData(
                            "presentationCounts is zero where spikeCounts is nonzero."
                        )
                    }
                }
            }
        }
    }
}

/// Ordered per-unit maps from one source JSON. Integer subscripting is always
/// positional; explicit lookups prevent an original index from being mistaken
/// for a unit ID.
struct RFMapList: RandomAccessCollection, Sendable {
    typealias Index = Int
    typealias Element = RFMap

    private let maps: [RFMap]
    private let originalIndexLookup: [Int: Int]
    private let unitIDLookup: [Int: Int]

    init(_ maps: [RFMap]) throws {
        var originalIndexLookup: [Int: Int] = [:]
        var unitIDLookup: [Int: Int] = [:]
        for (position, map) in maps.enumerated() {
            guard originalIndexLookup.updateValue(position, forKey: map.unitIndex) == nil else {
                throw RFMapError.invalidData("RFMap original unit indices must be unique.")
            }
            guard unitIDLookup.updateValue(position, forKey: map.unitID) == nil else {
                throw RFMapError.invalidData("RFMap unit IDs must be unique.")
            }
        }
        self.maps = maps
        self.originalIndexLookup = originalIndexLookup
        self.unitIDLookup = unitIDLookup
    }

    var startIndex: Int { maps.startIndex }
    var endIndex: Int { maps.endIndex }
    subscript(position: Int) -> RFMap { maps[position] }

    var unitIDs: [Int] { maps.map(\.unitID) }
    var originalIndices: [Int] { maps.map(\.unitIndex) }
    var sourceURL: URL? { maps.first?.sourceURL }

    func byOriginalIndex(_ originalIndex: Int) throws -> RFMap {
        guard let position = originalIndexLookup[originalIndex] else {
            throw RFMapError.missingOriginalIndex(
                originalIndex,
                available: originalIndices.sorted()
            )
        }
        return maps[position]
    }

    func byUnitID(_ unitID: Int) throws -> RFMap {
        guard let position = unitIDLookup[unitID] else {
            throw RFMapError.missingUnitID(unitID, available: unitIDs)
        }
        return maps[position]
    }

    func originalIndex(forUnitID unitID: Int) -> Int? {
        guard let position = unitIDLookup[unitID] else { return nil }
        return maps[position].unitIndex
    }
}
