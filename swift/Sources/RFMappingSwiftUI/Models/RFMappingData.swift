import CryptoKit
import Foundation

private enum OccupancyTimePayload {
    case matrix([[Double]])
    case vector([Double])
    case scalar(Double)
}

/// Decode a large volume in cancellable spatial chunks. Replacing a document
/// must stop obsolete work before validating every remaining unit.
private struct CancellableSpikeCounts: Decodable {
    let values: [[[[Double]]]]

    init(from decoder: Decoder) throws {
        var units = try decoder.unkeyedContainer()
        var result: [[[[Double]]]] = []
        while !units.isAtEnd {
            try Task.checkCancellation()
            var rows = try units.nestedUnkeyedContainer()
            var unit: [[[Double]]] = []
            while !rows.isAtEnd {
                try Task.checkCancellation()
                unit.append(try rows.decode([[Double]].self))
            }
            result.append(unit)
        }
        values = result
    }
}

private struct RFMappingArbitraryCodingKey: CodingKey {
    let stringValue: String
    let intValue: Int?

    init?(stringValue: String) {
        self.stringValue = stringValue
        intValue = nil
    }

    init?(intValue: Int) {
        stringValue = String(intValue)
        self.intValue = intValue
    }
}

private struct RFMappingPayload: Decodable {
    let unitsSpikeCounts: [[[[Double]]]]
    let unitsSpikeCountsSize: [Int]
    let unitPool: [Int]
    let xPositions: [Double]
    let yPositions: [Double]
    let timeBinEdges: [Double]
    let occupancyTimeSec: OccupancyTimePayload
    let occupancyTimeSecSize: [Int]
    let responseUnits: String
    let responseNormalization: String
    let spikeCountDefinition: String
    let occupancyTimeDefinition: String
    let metadata: [String: RFMapJSONValue]

    private enum CodingKeys: String, CodingKey {
        case unitsSpikeCounts
        case unitsSpikeCountsSize
        case unitPool
        case xPositions
        case yPositions
        case timeBinEdges
        case occupancyTimeSec
        case occupancyTimeSecSize
        case responseUnits
        case responseNormalization
        case spikeCountDefinition
        case occupancyTimeDefinition
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let requiredKeys: [(CodingKeys, String)] = [
            (.unitsSpikeCounts, "unitsSpikeCounts"),
            (.unitsSpikeCountsSize, "unitsSpikeCountsSize"),
            (.unitPool, "unitPool"),
            (.xPositions, "xPositions"),
            (.yPositions, "yPositions"),
            (.timeBinEdges, "timeBinEdges"),
            (.occupancyTimeSec, "occupancyTimeSec"),
            (.responseUnits, "responseUnits"),
            (.responseNormalization, "responseNormalization"),
        ]
        let missingKeys = requiredKeys.compactMap { entry in
            container.contains(entry.0) ? nil : entry.1
        }
        guard missingKeys.isEmpty else {
            throw RFMappingError.invalidData(
                "Unsupported old RF map; missing current schema keys: "
                    + missingKeys.joined(separator: ", ")
            )
        }
        unitsSpikeCounts = try container.decode(
            CancellableSpikeCounts.self,
            forKey: .unitsSpikeCounts
        ).values
        unitsSpikeCountsSize = try container.decode([Int].self, forKey: .unitsSpikeCountsSize)
        unitPool = try Self.decodeUnitPool(
            from: container,
            declaredSize: unitsSpikeCountsSize.first
        )
        xPositions = try Self.decodeSpatialAxis(
            from: container,
            forKey: .xPositions,
            declaredSize: unitsSpikeCountsSize.indices.contains(2)
                ? unitsSpikeCountsSize[2]
                : nil,
            label: "xPositions"
        )
        yPositions = try Self.decodeSpatialAxis(
            from: container,
            forKey: .yPositions,
            declaredSize: unitsSpikeCountsSize.indices.contains(1)
                ? unitsSpikeCountsSize[1]
                : nil,
            label: "yPositions"
        )
        timeBinEdges = try container.decode([Double].self, forKey: .timeBinEdges)
        occupancyTimeSec = try Self.decodeOccupancyTime(from: container)
        // MATLAB JSON and indexed exports can omit these descriptive markers.
        // Explicit values (including null) must still satisfy the raw-count
        // contract; the actual occupancy dimensions are validated below.
        occupancyTimeSecSize = container.contains(.occupancyTimeSecSize)
            ? try container.decode([Int].self, forKey: .occupancyTimeSecSize)
            : [yPositions.count, xPositions.count]
        responseUnits = try container.decode(String.self, forKey: .responseUnits)
        responseNormalization = try container.decode(String.self, forKey: .responseNormalization)
        spikeCountDefinition = container.contains(.spikeCountDefinition)
            ? try container.decode(String.self, forKey: .spikeCountDefinition)
            : RFMappingData.expectedSpikeCountDefinition
        occupancyTimeDefinition = container.contains(.occupancyTimeDefinition)
            ? try container.decode(String.self, forKey: .occupancyTimeDefinition)
            : RFMappingData.expectedOccupancyTimeDefinition

        let arbitraryContainer = try decoder.container(
            keyedBy: RFMappingArbitraryCodingKey.self
        )
        var decodedMetadata: [String: RFMapJSONValue] = [:]
        for key in arbitraryContainer.allKeys
            where !RFMap.structuralJSONKeys.contains(key.stringValue) {
            decodedMetadata[key.stringValue] = try arbitraryContainer.decode(
                RFMapJSONValue.self,
                forKey: key
            )
        }
        metadata = decodedMetadata
    }

    private static func decodeOccupancyTime(
        from container: KeyedDecodingContainer<CodingKeys>
    ) throws -> OccupancyTimePayload {
        guard container.contains(.occupancyTimeSec) else {
            throw RFMappingError.invalidData(
                "occupancyTimeSec is required by the current RF map schema."
            )
        }
        let isNull = try container.decodeNil(forKey: .occupancyTimeSec)
        guard !isNull else {
            throw RFMappingError.invalidData(
                "occupancyTimeSec cannot be null in the current RF map schema."
            )
        }
        if let matrix = try? container.decode([[Double]].self, forKey: .occupancyTimeSec) {
            return .matrix(matrix)
        }
        if let vector = try? container.decode([Double].self, forKey: .occupancyTimeSec) {
            return .vector(vector)
        }
        if let scalar = try? container.decode(Double.self, forKey: .occupancyTimeSec) {
            return .scalar(scalar)
        }
        throw RFMappingError.invalidData(
            "occupancyTimeSec must be a y-by-x numeric array."
        )
    }

    private static func decodeSpatialAxis(
        from container: KeyedDecodingContainer<CodingKeys>,
        forKey key: CodingKeys,
        declaredSize: Int?,
        label: String
    ) throws -> [Double] {
        if let values = try? container.decode([Double].self, forKey: key) {
            return values
        }
        if let scalar = try? container.decode(Double.self, forKey: key) {
            guard declaredSize == 1 else {
                throw RFMappingError.invalidData(
                    "A scalar \(label) value is valid only when its declared spatial dimension is 1."
                )
            }
            return [scalar]
        }
        throw RFMappingError.invalidData(
            "\(label) must be a numeric array, or a numeric scalar for a declared singleton spatial dimension."
        )
    }

    private static func decodeUnitPool(
        from container: KeyedDecodingContainer<CodingKeys>,
        declaredSize: Int?
    ) throws -> [Int] {
        if let values = try? container.decode([Int].self, forKey: .unitPool) {
            return values
        }
        if let scalar = try? container.decode(Int.self, forKey: .unitPool) {
            guard declaredSize == 1 else {
                throw RFMappingError.invalidData(
                    "A scalar unitPool value is valid only when the declared unit dimension is 1."
                )
            }
            return [scalar]
        }
        throw RFMappingError.invalidData(
            "unitPool must be an integer array, or an integer scalar for a declared singleton unit dimension."
        )
    }
}

struct RFSpatialObservations: Sendable {
    let count: Double
    let occupancyTimeSeconds: Double
    let sourcePixelCount: Int
}

/// Header decoding and individual archive reads run on workers. Loaded RFMap
/// values are immutable; publishing units and mutable derived caches remain
/// main-actor confined by the store.
final class RFMappingData: @unchecked Sendable {
    static let expectedResponseUnits = "spike_count"
    static let expectedResponseNormalization = "none"
    static let expectedSpikeCountDefinition =
        "each_qualifying_trial_contributes_once_per_final_spatial_bin"
    static let expectedOccupancyTimeDefinition =
        "sum_of_qualifying_trial_durations_per_final_spatial_bin"

    /// Prefix subtraction is lossless only while non-negative integer counts
    /// stay within Double's exact-integer range. Other cells retain the
    /// original compensated slice summation path.
    private struct ExactPrefixValues {
        let values: ContiguousArray<Double>
        let safeCells: ContiguousArray<Bool>
    }

    private struct UnitPrefixCache {
        let unitIndex: Int
        let values: ExactPrefixValues
    }

    private struct CountWindowCache {
        let unitIndex: Int
        let groups: [AxisGroup]
        let matrices: [[[Double]]]
    }

    private struct SpatialExposure {
        let pixelIndices: [Int]
        let occupancyTimeSeconds: Double
    }

    private struct SpatialExposureCache {
        let yGroups: [AxisGroup]
        let xGroups: [AxisGroup]
        let exposures: [[SpatialExposure]]
    }

    let url: URL
    /// Source-indexed slots. An empty unit is unavailable, never a zero RF map.
    private(set) var counts: [[[[Double]]]]
    let size: (Int, Int, Int, Int)
    let nUnits: Int
    let nY: Int
    let nX: Int
    let nBins: Int
    let unitPool: [Int]
    private let unitIndexLookup: [Int: Int]
    let xPositions: [Double]
    let yPositions: [Double]
    let timeBinEdges: [Double]
    let occupancyTimeSeconds: [[Double]]
    let responseUnits: String
    let responseNormalization: String
    let spikeCountDefinition: String
    let occupancyTimeDefinition: String
    /// Cached maps in source order; explicit original-index lookup is required
    /// because priority loading can leave gaps between available units.
    private(set) var rfMaps: RFMapList
    private(set) var loadedUnitIndices: Set<Int>
    private let indexedArchive: IndexedRFArchive?
    var isIndexed: Bool { indexedArchive != nil }
    var cachedUnitCount: Int { loadedUnitIndices.count }
    /// All non-structural top-level JSON fields.
    let metadata: [String: RFMapJSONValue]
    /// JSON provenance is frozen during decoding. Indexed provenance is
    /// prepared for export after verifying that the source file is unchanged.
    private(set) var sourceSHA256: String
    let sourceByteCount: Int

    private var metricsCache: [Int: UnitMetrics] = [:]
    private var prefixCaches: [UnitPrefixCache] = []
    private var countWindowCaches: [CountWindowCache] = []
    private var spatialExposureCaches: [SpatialExposureCache] = []

    convenience init(url: URL) throws {
        let file = try FileHandle(forReadingFrom: url)
        let magic: Data
        do {
            magic = try file.read(upToCount: 4) ?? Data()
            try file.close()
        } catch {
            try? file.close()
            throw error
        }
        if IndexedRFArchive.isArchive(magic) {
            try self.init(jsonData: Data(), archive: IndexedRFArchive(url: url), url: url)
        } else {
            try self.init(data: Data(contentsOf: url, options: .mappedIfSafe), url: url)
        }
    }

    static func makeDecodeTask(url: URL) -> Task<RFMappingData, Error> {
        Task.detached(priority: .userInitiated) {
            try Task.checkCancellation()
            let decoded = try RFMappingData(url: url)
            try Task.checkCancellation()
            _ = decoded.metrics(for: 0)
            try Task.checkCancellation()
            _ = decoded.prefixValues(for: 0)
            try Task.checkCancellation()
            return decoded
        }
    }

    static func decodeOffMain(url: URL) async throws -> RFMappingData {
        let task = makeDecodeTask(url: url)
        return try await withTaskCancellationHandler {
            try await task.value
        } onCancel: {
            task.cancel()
        }
    }

    convenience init(data jsonData: Data, url: URL) throws {
        let archive = try IndexedRFArchive.isArchive(jsonData)
            ? IndexedRFArchive(data: jsonData) : nil
        try self.init(jsonData: jsonData, archive: archive, url: url)
    }

    private init(jsonData: Data, archive: IndexedRFArchive?, url: URL) throws {
        self.url = url.standardizedFileURL
        if archive == nil {
            var digest = SHA256()
            for offset in stride(from: 0, to: jsonData.count, by: 1_048_576) {
                try Task.checkCancellation()
                digest.update(data: jsonData[offset..<min(jsonData.count, offset + 1_048_576)])
            }
            sourceSHA256 = digest.finalize().map { String(format: "%02x", $0) }.joined()
        } else {
            // Indexed startup touches only the directory, header, and first
            // unit. Export explicitly freezes a digest before provenance.
            sourceSHA256 = ""
        }
        sourceByteCount = archive?.byteCount ?? jsonData.count
        indexedArchive = archive
        let payload: RFMappingPayload
        do {
            payload = try JSONDecoder().decode(
                RFMappingPayload.self,
                from: archive?.headerJSON() ?? jsonData
            )
        } catch is CancellationError {
            throw CancellationError()
        } catch let error as RFMappingError {
            throw error
        } catch {
            throw RFMappingError.invalidData("Could not decode RF mapping data: \(error.localizedDescription)")
        }
        try Task.checkCancellation()

        guard payload.unitsSpikeCountsSize.count == 4 else {
            throw RFMappingError.invalidData("unitsSpikeCountsSize must contain 4 values.")
        }

        size = (
            payload.unitsSpikeCountsSize[0],
            payload.unitsSpikeCountsSize[1],
            payload.unitsSpikeCountsSize[2],
            payload.unitsSpikeCountsSize[3]
        )
        nUnits = size.0
        nY = size.1
        nX = size.2
        nBins = size.3
        guard [nUnits, nY, nX, nBins].allSatisfy({ $0 > 0 }) else {
            throw RFMappingError.invalidData("unitsSpikeCountsSize values must all be positive.")
        }
        guard payload.unitPool.count == nUnits,
              Set(payload.unitPool).count == nUnits else {
            throw RFMappingError.invalidData("unitPool must contain one unique ID per declared unit.")
        }
        guard archive != nil || payload.unitsSpikeCounts.count == payload.unitPool.count else {
            throw RFMappingError.invalidData(
                "unitPool length does not match the decoded unit count."
            )
        }
        let normalizedOccupancyTime = try Self.normalizeOccupancyTime(
            payload.occupancyTimeSec,
            nY: nY,
            nX: nX
        )
        let sourceMetadata = payload.metadata
        let initialCounts: [[[[Double]]]]
        if let archive {
            initialCounts = [try archive.counts(unitID: payload.unitPool[0], shape: [nY, nX, nBins])]
        } else {
            initialCounts = payload.unitsSpikeCounts
        }
        let perUnitMaps = try initialCounts.enumerated().map { unitIndex, unitCounts in
            try Task.checkCancellation()
            return try RFMap(
                unitIndex: unitIndex,
                unitID: payload.unitPool[unitIndex],
                spikeCounts: unitCounts,
                xPositions: payload.xPositions,
                yPositions: payload.yPositions,
                timeBinEdgesSeconds: payload.timeBinEdges,
                occupancyTimeSeconds: normalizedOccupancyTime,
                metadata: sourceMetadata,
                sourceURL: url
            )
        }
        counts = archive == nil ? initialCounts : initialCounts + Array(repeating: [], count: nUnits - 1)
        unitPool = payload.unitPool
        unitIndexLookup = Dictionary(uniqueKeysWithValues: payload.unitPool.enumerated().map { ($0.element, $0.offset) })
        xPositions = payload.xPositions
        yPositions = payload.yPositions
        timeBinEdges = payload.timeBinEdges
        occupancyTimeSeconds = normalizedOccupancyTime
        responseUnits = payload.responseUnits
        responseNormalization = payload.responseNormalization
        spikeCountDefinition = payload.spikeCountDefinition
        occupancyTimeDefinition = payload.occupancyTimeDefinition
        rfMaps = try RFMapList(perUnitMaps)
        loadedUnitIndices = Set(perUnitMaps.map(\.unitIndex))
        metadata = sourceMetadata

        try validate(occupancyTimeSecSize: payload.occupancyTimeSecSize)
        try archive?.verifySource()
    }

    func displayYIndices(flipY: Bool) -> [Int] {
        flipY ? Array(stride(from: nY - 1, through: 0, by: -1)) : Array(0..<nY)
    }

    func clusterID(for unitIndex: Int) -> Int {
        unitPool[unitIndex]
    }

    func unitIndex(forUnitID unitID: Int) -> Int? {
        unitIndexLookup[unitID]
    }

    func isUnitCached(_ index: Int) -> Bool {
        loadedUnitIndices.contains(index)
    }

    /// Freeze source provenance only when needed for an export. Cached RF
    /// units remain usable if the source changes, but a changed source cannot
    /// produce a new export claiming the original file identity.
    func prepareSourceHash() throws {
        guard let archive = indexedArchive else { return }
        try archive.verifySource()
        if sourceSHA256.isEmpty { sourceSHA256 = try archive.sourceHash() }
    }

    /// Read on a worker, then let the store publish this value on the main
    /// actor. Cancellation prevents obsolete document loads from continuing.
    func loadUnit(at index: Int) async throws -> RFMap {
        guard unitPool.indices.contains(index) else {
            throw RFMapError.missingOriginalIndex(index, available: Array(unitPool.indices))
        }
        if isUnitCached(index) { return try rfMap(byOriginalIndex: index) }
        guard let archive = indexedArchive else {
            throw RFMapError.missingOriginalIndex(index, available: loadedUnitIndices.sorted())
        }
        let unitID = unitPool[index]
        let shape = [nY, nX, nBins]
        let x = xPositions
        let y = yPositions
        let edges = timeBinEdges
        let occupancy = occupancyTimeSeconds
        let sourceMetadata = metadata
        let sourceURL = url
        let task = Task.detached(priority: .utility) {
            try Task.checkCancellation()
            let counts = try archive.counts(unitID: unitID, shape: shape)
            let map = try RFMap(
                unitIndex: index,
                unitID: unitID,
                spikeCounts: counts,
                xPositions: x,
                yPositions: y,
                timeBinEdgesSeconds: edges,
                occupancyTimeSeconds: occupancy,
                metadata: sourceMetadata,
                sourceURL: sourceURL
            )
            try archive.verifySource()
            try Task.checkCancellation()
            return map
        }
        return try await withTaskCancellationHandler {
            try await task.value
        } onCancel: {
            task.cancel()
        }
    }

    /// Called only by the main-actor store. A unit is published after all
    /// counts and occupancy constraints pass, so partial units stay invisible.
    func cacheUnit(_ map: RFMap) throws {
        guard unitPool.indices.contains(map.unitIndex),
              unitPool[map.unitIndex] == map.unitID,
              map.sourceURL == url,
              map.nY == nY, map.nX == nX, map.nTimeBins == nBins else {
            throw RFMappingError.invalidData("Cached RF unit does not belong to this dataset.")
        }
        guard !isUnitCached(map.unitIndex) else { return }
        try rfMaps.insertInOriginalOrder(map)
        counts[map.unitIndex] = map.spikeCounts
        loadedUnitIndices.insert(map.unitIndex)
    }

    private func cachedCounts(for unitIndex: Int) -> [[[Double]]] {
        precondition(isUnitCached(unitIndex), "RF unit must be cached before plotting.")
        return counts[unitIndex]
    }

    func rfMap(byOriginalIndex unitIndex: Int) throws -> RFMap {
        try rfMaps.byOriginalIndex(unitIndex)
    }

    func rfMap(byUnitID unitID: Int) throws -> RFMap {
        try rfMaps.byUnitID(unitID)
    }

    func binCenterMS(_ binIndex: Int) -> Double {
        (timeBinEdges[binIndex] + timeBinEdges[binIndex + 1]) * 500.0
    }

    func inferTotalDeg() -> Double {
        guard nX > 1 else { return 360.0 }
        let diffs = (0..<(nX - 1)).map { xPositions[$0 + 1] - xPositions[$0] }
        let step = compensatedSum(diffs) / Double(diffs.count)
        if diffs.allSatisfy({ abs($0 - step) < 1e-6 }) && abs(step) > 1e-9 {
            return abs(step) * Double(nX)
        }
        return abs((xPositions.last ?? 0.0) - (xPositions.first ?? 0.0))
    }

    func metrics(for unitIndex: Int) -> UnitMetrics {
        if let cached = metricsCache[unitIndex] {
            return cached
        }

        let unit = cachedCounts(for: unitIndex)
        var total: [[Double]] = []
        var peak: [[Double]] = []
        var peakBin: [[Int?]] = []
        var delayMS: [[Double?]] = []
        var entropy: [[Double]] = []
        var binTotals = Array(repeating: 0.0, count: nBins)

        var maxTotal = 0.0
        var maxPeak = 0.0
        var maxBinCount = 0.0
        var maxFiringRate: Double?
        var totalSpikes = 0.0
        var bestY = 0
        var bestX = 0

        for yIndex in 0..<nY {
            var totalRow: [Double] = []
            var peakRow: [Double] = []
            var peakBinRow: [Int?] = []
            var delayRow: [Double?] = []
            var entropyRow: [Double] = []

            for xIndex in 0..<nX {
                let hist = unit[yIndex][xIndex]
                let cellTotal = compensatedSum(hist)
                let cellPeak = hist.max() ?? 0.0
                let bestBin: Int?
                let delay: Double?
                let cellEntropy: Double

                if cellTotal > 0 {
                    var earliestBest = 0
                    for index in 1..<hist.count where hist[index] > hist[earliestBest] {
                        earliestBest = index
                    }
                    bestBin = earliestBest
                    delay = binCenterMS(earliestBest)
                    var entropyValue = 0.0
                    for count in hist where count > 0 {
                        let probability = count / cellTotal
                        entropyValue -= probability * log(probability)
                    }
                    cellEntropy = nBins > 1 ? entropyValue / log(Double(nBins)) : 0.0
                } else {
                    bestBin = nil
                    delay = nil
                    cellEntropy = 0.0
                }

                for (binIndex, count) in hist.enumerated() {
                    binTotals[binIndex] += count
                    maxBinCount = max(maxBinCount, count)
                }
                maxTotal = max(maxTotal, cellTotal)
                let occupancy = occupancyTimeSeconds[yIndex][xIndex]
                if occupancy > 0 {
                    let firingRate = cellTotal / occupancy
                    if maxFiringRate == nil || firingRate > (maxFiringRate ?? -.infinity) {
                        maxFiringRate = firingRate
                        bestY = yIndex
                        bestX = xIndex
                    }
                }
                maxPeak = max(maxPeak, cellPeak)
                totalSpikes += cellTotal

                totalRow.append(cellTotal)
                peakRow.append(cellPeak)
                peakBinRow.append(bestBin)
                delayRow.append(delay)
                entropyRow.append(cellEntropy)
            }

            total.append(totalRow)
            peak.append(peakRow)
            peakBin.append(peakBinRow)
            delayMS.append(delayRow)
            entropy.append(entropyRow)
        }

        let metrics = UnitMetrics(
            total: total,
            peak: peak,
            peakBin: peakBin,
            delayMS: delayMS,
            entropy: entropy,
            binTotals: binTotals,
            maxTotal: maxTotal,
            maxPeak: maxPeak,
            maxBinCount: maxBinCount,
            maxFiringRate: maxFiringRate,
            totalSpikes: totalSpikes,
            bestY: bestY,
            bestX: bestX
        )
        metricsCache[unitIndex] = metrics
        return metrics
    }

    func timeSpanSeconds(start: Int, end: Int) -> Double {
        let low = max(0, min(nBins - 1, min(start, end)))
        let high = max(0, min(nBins - 1, max(start, end)))
        return timeBinEdges[high + 1] - timeBinEdges[low]
    }

    func countMatrix(unitIndex: Int, start: Int, end: Int) -> [[Double]] {
        let low = max(0, min(nBins - 1, min(start, end)))
        let high = max(0, min(nBins - 1, max(start, end)))
        let prefix = prefixValues(for: unitIndex)
        let stride = nBins + 1
        let unit = cachedCounts(for: unitIndex)
        return (0..<nY).map { yIndex in
            (0..<nX).map { xIndex in
                let base = (yIndex * nX + xIndex) * stride
                return prefixRangeCount(
                    prefix,
                    base: base,
                    low: low,
                    high: high,
                    hist: unit[yIndex][xIndex]
                )
            }
        }
    }

    /// Reuse native counts across temporal views, independent of display mode
    /// or spatial rebinning. Only two layouts are retained, matching the small
    /// per-unit prefix cache rather than accumulating a volume per UI edit.
    func countWindows(unitIndex: Int, timeGroups: [AxisGroup]) -> [[[Double]]] {
        let groups = timeGroups.map { group in
            AxisGroup(
                start: max(0, min(nBins - 1, min(group.start, group.end))),
                end: max(0, min(nBins - 1, max(group.start, group.end)))
            )
        }
        guard !groups.isEmpty else { return [] }
        if let index = countWindowCaches.firstIndex(where: {
            $0.unitIndex == unitIndex && $0.groups == groups
        }) {
            let cached = countWindowCaches.remove(at: index)
            countWindowCaches.insert(cached, at: 0)
            return cached.matrices
        }

        // A single RF window needs no full-volume prefix allocation. Temporal
        // queries capture the prefix once instead of looking it up per pixel.
        let prefix = groups.count > 1 ? prefixValues(for: unitIndex) : nil
        let unit = cachedCounts(for: unitIndex)
        let stride = nBins + 1
        let matrices = groups.map { group in
            (0..<nY).map { yIndex in
                (0..<nX).map { xIndex -> Double in
                    let hist = unit[yIndex][xIndex]
                    guard let prefix else {
                        return compensatedSum(hist[group.start...group.end])
                    }
                    return prefixRangeCount(
                        prefix,
                        base: (yIndex * nX + xIndex) * stride,
                        low: group.start,
                        high: group.end,
                        hist: hist
                    )
                }
            }
        }
        countWindowCaches.insert(
            CountWindowCache(unitIndex: unitIndex, groups: groups, matrices: matrices),
            at: 0
        )
        if countWindowCaches.count > 2 { countWindowCaches.removeLast() }
        return matrices
    }

    /// Pool all requested frames with one occupancy layout. Source order and
    /// compensated summation match the scalar observation API exactly.
    func spatialObservationFrames(
        unitIndex: Int,
        timeGroups: [AxisGroup],
        yGroups: [AxisGroup],
        xGroups: [AxisGroup]
    ) -> [[[RFSpatialObservations]]] {
        guard !timeGroups.isEmpty, !yGroups.isEmpty, !xGroups.isEmpty else { return [] }
        let frames = countWindows(unitIndex: unitIndex, timeGroups: timeGroups)
        let exposures = spatialExposures(yGroups: yGroups, xGroups: xGroups)
        return frames.map { frame in
            exposures.map { row in
                row.map { exposure in
                    RFSpatialObservations(
                        count: compensatedSum(exposure.pixelIndices.lazy.map {
                            frame[$0 / self.nX][$0 % self.nX]
                        }),
                        occupancyTimeSeconds: exposure.occupancyTimeSeconds,
                        sourcePixelCount: exposure.pixelIndices.count
                    )
                }
            }
        }
    }

    private func spatialExposures(
        yGroups: [AxisGroup],
        xGroups: [AxisGroup]
    ) -> [[SpatialExposure]] {
        if let index = spatialExposureCaches.firstIndex(where: {
            $0.yGroups == yGroups && $0.xGroups == xGroups
        }) {
            let cached = spatialExposureCaches.remove(at: index)
            spatialExposureCaches.insert(cached, at: 0)
            return cached.exposures
        }
        let exposures = yGroups.map { yGroup in
            xGroups.map { xGroup in
                let yStart = max(0, min(nY - 1, min(yGroup.start, yGroup.end)))
                let yEnd = max(0, min(nY - 1, max(yGroup.start, yGroup.end)))
                let xStart = max(0, min(nX - 1, min(xGroup.start, xGroup.end)))
                let xEnd = max(0, min(nX - 1, max(xGroup.start, xGroup.end)))
                var indices: [Int] = []
                var occupancy: [Double] = []
                for yIndex in yStart...yEnd {
                    for xIndex in xStart...xEnd {
                        let value = occupancyTimeSeconds[yIndex][xIndex]
                        guard value > 0 else { continue }
                        indices.append(yIndex * nX + xIndex)
                        occupancy.append(value)
                    }
                }
                return SpatialExposure(
                    pixelIndices: indices,
                    occupancyTimeSeconds: compensatedSum(occupancy)
                )
            }
        }
        spatialExposureCaches.insert(
            SpatialExposureCache(yGroups: yGroups, xGroups: xGroups, exposures: exposures),
            at: 0
        )
        if spatialExposureCaches.count > 8 { spatialExposureCaches.removeLast() }
        return exposures
    }

    /// Counts native `(y, x)` RF bins that have no spikes in the inclusive
    /// source-bin window, or have no occupancy. This deliberately precedes all
    /// display rebinning and smoothing so it remains a data-quality test.
    func zeroSpikeSpatialBinCount(
        unitIndex: Int,
        start: Int,
        end: Int
    ) -> Int {
        let counts = countMatrix(unitIndex: unitIndex, start: start, end: end)
        var zeroCount = 0
        for yIndex in 0..<nY {
            for xIndex in 0..<nX {
                if counts[yIndex][xIndex] == 0
                    || occupancyTimeSeconds[yIndex][xIndex] <= 0 {
                    zeroCount += 1
                }
            }
        }
        return zeroCount
    }

    func rangeCount(
        unitIndex: Int,
        yIndex: Int,
        xIndex: Int,
        start: Int,
        end: Int
    ) -> Double {
        let low = max(0, min(nBins - 1, min(start, end)))
        let high = max(0, min(nBins - 1, max(start, end)))
        let prefix = prefixValues(for: unitIndex)
        let stride = nBins + 1
        let base = (yIndex * nX + xIndex) * stride
        return prefixRangeCount(
            prefix,
            base: base,
            low: low,
            high: high,
            hist: cachedCounts(for: unitIndex)[yIndex][xIndex]
        )
    }

    func responseValue(
        unitIndex: Int,
        yIndex: Int,
        xIndex: Int,
        start: Int,
        end: Int,
        valueMode: ResponseValueMode
    ) throws -> Double? {
        let low = max(0, min(nBins - 1, min(start, end)))
        let high = max(0, min(nBins - 1, max(start, end)))
        let count = rangeCount(
            unitIndex: unitIndex,
            yIndex: yIndex,
            xIndex: xIndex,
            start: low,
            end: high
        )
        let occupancy = occupancyTimeSeconds[yIndex][xIndex]
        guard occupancy > 0 else { return nil }
        switch valueMode {
        case .spikeCount:
            return count
        case .meanFiringRate:
            return count / occupancy
        }
    }

    func responseMatrix(
        unitIndex: Int,
        start: Int,
        end: Int,
        valueMode: ResponseValueMode
    ) throws -> OptionalMatrix {
        let low = max(0, min(nBins - 1, min(start, end)))
        let high = max(0, min(nBins - 1, max(start, end)))
        let prefix = prefixValues(for: unitIndex)
        let stride = nBins + 1
        let unit = cachedCounts(for: unitIndex)
        return (0..<nY).map { yIndex in
            (0..<nX).map { xIndex -> Double? in
                let base = (yIndex * nX + xIndex) * stride
                let count = prefixRangeCount(
                    prefix,
                    base: base,
                    low: low,
                    high: high,
                    hist: unit[yIndex][xIndex]
                )
                let occupancy = occupancyTimeSeconds[yIndex][xIndex]
                guard occupancy > 0 else { return nil }
                switch valueMode {
                case .spikeCount:
                    return count
                case .meanFiringRate:
                    return count / occupancy
                }
            }
        }
    }

    func spatialObservations(
        unitIndex: Int,
        yGroup: AxisGroup,
        xGroup: AxisGroup,
        start: Int,
        end: Int
    ) -> RFSpatialObservations {
        let yStart = max(0, min(nY - 1, min(yGroup.start, yGroup.end)))
        let yEnd = max(0, min(nY - 1, max(yGroup.start, yGroup.end)))
        let xStart = max(0, min(nX - 1, min(xGroup.start, xGroup.end)))
        let xEnd = max(0, min(nX - 1, max(xGroup.start, xGroup.end)))
        var counts: [Double] = []
        var occupancies: [Double] = []
        counts.reserveCapacity((yEnd - yStart + 1) * (xEnd - xStart + 1))
        occupancies.reserveCapacity(counts.capacity)
        for yIndex in yStart...yEnd {
            for xIndex in xStart...xEnd {
                let occupancy = occupancyTimeSeconds[yIndex][xIndex]
                guard occupancy > 0 else { continue }
                counts.append(rangeCount(
                    unitIndex: unitIndex,
                    yIndex: yIndex,
                    xIndex: xIndex,
                    start: start,
                    end: end
                ))
                occupancies.append(occupancy)
            }
        }
        return RFSpatialObservations(
            count: compensatedSum(counts),
            occupancyTimeSeconds: compensatedSum(occupancies),
            sourcePixelCount: counts.count
        )
    }

    private func prefixRangeCount(
        _ prefix: ExactPrefixValues,
        base: Int,
        low: Int,
        high: Int,
        hist: [Double]
    ) -> Double {
        let stride = nBins + 1
        let cellIndex = base / stride
        guard prefix.safeCells[cellIndex] else {
            return compensatedSum(hist[low...high])
        }
        let start = base + low
        let end = base + high + 1
        return prefix.values[end] - prefix.values[start]
    }

    private func prefixValues(for unitIndex: Int) -> ExactPrefixValues {
        if let cached = prefixCaches.first, cached.unitIndex == unitIndex {
            return cached.values
        }
        if let index = prefixCaches.firstIndex(where: { $0.unitIndex == unitIndex }) {
            let cached = prefixCaches.remove(at: index)
            prefixCaches.insert(cached, at: 0)
            return cached.values
        }

        let stride = nBins + 1
        let valueCount = nY * nX * stride
        let maximumExactInteger = 9_007_199_254_740_992.0
        var prefixValues = ContiguousArray(repeating: 0.0, count: valueCount)
        var safeCells = ContiguousArray(repeating: true, count: nY * nX)
        let unit = cachedCounts(for: unitIndex)
        for yIndex in 0..<nY {
            for xIndex in 0..<nX {
                let base = (yIndex * nX + xIndex) * stride
                let cellIndex = yIndex * nX + xIndex
                var running = 0.0
                var isExactIntegerPrefix = true
                for bin in 0..<nBins {
                    let value = unit[yIndex][xIndex][bin]
                    if isExactIntegerPrefix,
                       value == value.rounded(),
                       value <= maximumExactInteger - running {
                        running += value
                        prefixValues[base + bin + 1] = running
                    } else {
                        isExactIntegerPrefix = false
                    }
                }
                safeCells[cellIndex] = isExactIntegerPrefix
            }
        }

        let prefix = ExactPrefixValues(values: prefixValues, safeCells: safeCells)
        prefixCaches.insert(UnitPrefixCache(unitIndex: unitIndex, values: prefix), at: 0)
        if prefixCaches.count > 2 { prefixCaches.removeLast() }
        return prefix
    }

    private static func normalizeOccupancyTime(
        _ payload: OccupancyTimePayload,
        nY: Int,
        nX: Int
    ) throws -> [[Double]] {
        switch payload {
        case .matrix(let matrix):
            return matrix
        case .vector(let vector):
            if nY == 1, vector.count == nX {
                return [vector]
            }
            if nX == 1, vector.count == nY {
                return vector.map { [$0] }
            }
            throw RFMappingError.invalidData(
                "occupancyTimeSec singleton dimensions do not match the y-by-x shape."
            )
        case .scalar(let value):
            guard nY == 1, nX == 1 else {
                throw RFMappingError.invalidData(
                    "A scalar occupancyTimeSec value is valid only for a 1-by-1 map."
                )
            }
            return [[value]]
        }
    }

    private func validate(occupancyTimeSecSize: [Int]) throws {
        guard [nUnits, nY, nX, nBins].allSatisfy({ $0 > 0 }) else {
            throw RFMappingError.invalidData("unitsSpikeCountsSize values must all be positive.")
        }
        guard counts.count == nUnits else {
            throw RFMappingError.invalidData("unitsSpikeCounts first dimension does not match unitsSpikeCountsSize.")
        }
        guard unitPool.count == nUnits else {
            throw RFMappingError.invalidData("unitPool length does not match unit count.")
        }
        guard xPositions.count == nX, xPositions.allSatisfy(\.isFinite) else {
            throw RFMappingError.invalidData("xPositions must match the x dimension and contain finite values.")
        }
        guard yPositions.count == nY, yPositions.allSatisfy(\.isFinite) else {
            throw RFMappingError.invalidData("yPositions must match the y dimension and contain finite values.")
        }
        guard timeBinEdges.count == nBins + 1, timeBinEdges.allSatisfy(\.isFinite) else {
            throw RFMappingError.invalidData("timeBinEdges must contain nBins + 1 finite edges.")
        }
        guard zip(timeBinEdges, timeBinEdges.dropFirst()).allSatisfy({ pair in pair.0 < pair.1 }) else {
            throw RFMappingError.invalidData("timeBinEdges must be strictly increasing.")
        }

        guard occupancyTimeSecSize == [nY, nX] else {
            throw RFMappingError.invalidData(
                "occupancyTimeSecSize must equal the y-by-x dimensions [\(nY), \(nX)]."
            )
        }
        guard occupancyTimeSeconds.count == nY else {
            throw RFMappingError.invalidData(
                "occupancyTimeSec y dimension does not match unitsSpikeCountsSize."
            )
        }
        for (yIndex, row) in occupancyTimeSeconds.enumerated() {
            guard row.count == nX else {
                throw RFMappingError.invalidData(
                    "occupancyTimeSec row \(yIndex) x dimension does not match unitsSpikeCountsSize."
                )
            }
            for (xIndex, value) in row.enumerated() {
                guard value.isFinite, value >= 0 else {
                    throw RFMappingError.invalidData(
                        "occupancyTimeSec values must be finite and non-negative "
                            + "(y \(yIndex), x \(xIndex))."
                    )
                }
            }
        }
        guard occupancyTimeSeconds.joined().contains(where: { $0 > 0 }) else {
            throw RFMappingError.invalidData(
                "occupancyTimeSec must contain at least one positive value."
            )
        }
        guard responseUnits == Self.expectedResponseUnits else {
            throw RFMappingError.invalidData(
                "responseUnits must be '\(Self.expectedResponseUnits)'."
            )
        }
        guard responseNormalization == Self.expectedResponseNormalization else {
            throw RFMappingError.invalidData(
                "responseNormalization must be '\(Self.expectedResponseNormalization)'."
            )
        }
        guard spikeCountDefinition == Self.expectedSpikeCountDefinition else {
            throw RFMappingError.invalidData(
                "spikeCountDefinition must be '\(Self.expectedSpikeCountDefinition)'."
            )
        }
        guard occupancyTimeDefinition == Self.expectedOccupancyTimeDefinition else {
            throw RFMappingError.invalidData(
                "occupancyTimeDefinition must be '\(Self.expectedOccupancyTimeDefinition)'."
            )
        }

        // Per-unit rectangularity, count values, and zero-occupancy cells
        // were validated once while constructing `rfMaps` above. Repeating
        // those full-volume scans here would double load time for large files.
    }
}

enum RFMappingError: LocalizedError {
    case invalidData(String)

    var errorDescription: String? {
        switch self {
        case .invalidData(let message):
            message
        }
    }
}
