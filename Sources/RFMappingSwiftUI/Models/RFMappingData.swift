import Foundation

private enum PresentationCountsPayload {
    case matrix([[Double]])
    case vector([Double])
    case scalar(Double)
}

private struct RFMappingPayload: Decodable {
    let unitsSpikeCounts: [[[[Double]]]]
    let unitsSpikeCountsSize: [Int]
    let unitPool: [Int]
    let xPositions: [Double]
    let yPositions: [Double]
    let timeBinEdges: [Double]
    let stimulusPresentationCounts: PresentationCountsPayload?

    private enum CodingKeys: String, CodingKey {
        case unitsSpikeCounts
        case unitsSpikeCountsSize
        case unitPool
        case xPositions
        case yPositions
        case timeBinEdges
        case stimulusPresentationCounts
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        unitsSpikeCounts = try container.decode([[[[Double]]]].self, forKey: .unitsSpikeCounts)
        unitsSpikeCountsSize = try container.decode([Int].self, forKey: .unitsSpikeCountsSize)
        unitPool = try container.decode([Int].self, forKey: .unitPool)
        xPositions = try container.decode([Double].self, forKey: .xPositions)
        yPositions = try container.decode([Double].self, forKey: .yPositions)
        timeBinEdges = try container.decode([Double].self, forKey: .timeBinEdges)

        if !container.contains(.stimulusPresentationCounts) {
            stimulusPresentationCounts = nil
        } else if try container.decodeNil(forKey: .stimulusPresentationCounts) {
            stimulusPresentationCounts = nil
        } else if let matrix = try? container.decode([[Double]].self, forKey: .stimulusPresentationCounts) {
            stimulusPresentationCounts = .matrix(matrix)
        } else if let vector = try? container.decode([Double].self, forKey: .stimulusPresentationCounts) {
            stimulusPresentationCounts = .vector(vector)
        } else if let scalar = try? container.decode(Double.self, forKey: .stimulusPresentationCounts) {
            stimulusPresentationCounts = .scalar(scalar)
        } else {
            throw RFMappingError.invalidData("stimulusPresentationCounts must be a y-by-x numeric array.")
        }
    }
}

/// Instances are constructed completely on one worker and then transferred to
/// the main actor. Mutable derived caches remain main-actor confined by the
/// store; no instance is read concurrently while decoding.
final class RFMappingData: @unchecked Sendable {
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

    let url: URL
    let counts: [[[[Double]]]]
    let size: (Int, Int, Int, Int)
    let nUnits: Int
    let nY: Int
    let nX: Int
    let nBins: Int
    let unitPool: [Int]
    let xPositions: [Double]
    let yPositions: [Double]
    let timeBinEdges: [Double]
    let presentationCounts: [[Double]]?

    private var metricsCache: [Int: UnitMetrics] = [:]
    private var prefixCaches: [UnitPrefixCache] = []

    convenience init(url: URL) throws {
        try self.init(data: Data(contentsOf: url, options: .mappedIfSafe), url: url)
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

    init(data jsonData: Data, url: URL) throws {
        self.url = url.standardizedFileURL
        let payload: RFMappingPayload
        do {
            payload = try JSONDecoder().decode(RFMappingPayload.self, from: jsonData)
        } catch let error as RFMappingError {
            throw error
        } catch {
            throw RFMappingError.invalidData("Could not decode RF mapping JSON: \(error.localizedDescription)")
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
        counts = payload.unitsSpikeCounts
        unitPool = payload.unitPool
        xPositions = payload.xPositions
        yPositions = payload.yPositions
        timeBinEdges = payload.timeBinEdges
        presentationCounts = try Self.normalizePresentationCounts(
            payload.stimulusPresentationCounts,
            nY: nY,
            nX: nX
        )

        try validate()
    }

    var hasPresentationCounts: Bool {
        presentationCounts != nil
    }

    func supports(_ valueMode: ResponseValueMode) -> Bool {
        !valueMode.requiresPresentationCounts || presentationCounts != nil
    }

    func displayYIndices(flipY: Bool) -> [Int] {
        flipY ? Array(stride(from: nY - 1, through: 0, by: -1)) : Array(0..<nY)
    }

    func clusterID(for unitIndex: Int) -> Int {
        unitPool[unitIndex]
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

        let unit = counts[unitIndex]
        var total: [[Double]] = []
        var peak: [[Double]] = []
        var peakBin: [[Int?]] = []
        var delayMS: [[Double?]] = []
        var entropy: [[Double]] = []
        var binTotals = Array(repeating: 0.0, count: nBins)

        var maxTotal = 0.0
        var maxPeak = 0.0
        var maxBinCount = 0.0
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
                if cellTotal > maxTotal {
                    maxTotal = cellTotal
                    bestY = yIndex
                    bestX = xIndex
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
        let unit = counts[unitIndex]
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
            hist: counts[unitIndex][yIndex][xIndex]
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
        if valueMode == .spikeCount {
            return count
        }
        guard let presentationCounts else {
            throw RFMappingError.presentationCountsRequired(valueMode)
        }
        let presentations = presentationCounts[yIndex][xIndex]
        guard presentations > 0 else { return nil }
        switch valueMode {
        case .spikeCount:
            return count
        case .spikesPerPresentation:
            return count / presentations
        case .meanFiringRate:
            return count / (presentations * timeSpanSeconds(start: low, end: high))
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
        if valueMode != .spikeCount, presentationCounts == nil {
            throw RFMappingError.presentationCountsRequired(valueMode)
        }
        let duration = timeSpanSeconds(start: low, end: high)
        let prefix = prefixValues(for: unitIndex)
        let stride = nBins + 1
        let unit = counts[unitIndex]
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
                if valueMode == .spikeCount { return count }
                guard let presentationCounts else { return nil }
                let presentations = presentationCounts[yIndex][xIndex]
                guard presentations > 0 else { return nil }
                let divisor = presentations * (valueMode == .meanFiringRate ? duration : 1.0)
                return count / divisor
            }
        }
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
        let unit = counts[unitIndex]
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

    private static func normalizePresentationCounts(
        _ payload: PresentationCountsPayload?,
        nY: Int,
        nX: Int
    ) throws -> [[Double]]? {
        guard let payload else { return nil }
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
            throw RFMappingError.invalidData("stimulusPresentationCounts singleton dimensions do not match the y-by-x shape.")
        case .scalar(let value):
            guard nY == 1, nX == 1 else {
                throw RFMappingError.invalidData("A scalar stimulusPresentationCounts value is valid only for a 1-by-1 map.")
            }
            return [[value]]
        }
    }

    private func validate() throws {
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

        if let presentationCounts {
            guard presentationCounts.count == nY else {
                throw RFMappingError.invalidData("stimulusPresentationCounts y dimension does not match unitsSpikeCountsSize.")
            }
            for (yIndex, row) in presentationCounts.enumerated() {
                guard row.count == nX else {
                    throw RFMappingError.invalidData("stimulusPresentationCounts row \(yIndex) x dimension does not match unitsSpikeCountsSize.")
                }
                for (xIndex, value) in row.enumerated() {
                    guard value.isFinite, value >= 0, abs(value - value.rounded()) <= 1e-9 else {
                        throw RFMappingError.invalidData("stimulusPresentationCounts values must be finite, non-negative integers (y \(yIndex), x \(xIndex)).")
                    }
                }
            }
        }

        for unitIndex in 0..<nUnits {
            try Task.checkCancellation()
            guard counts[unitIndex].count == nY else {
                throw RFMappingError.invalidData("Unit \(unitIndex) has wrong y dimension.")
            }
            for yIndex in 0..<nY {
                guard counts[unitIndex][yIndex].count == nX else {
                    throw RFMappingError.invalidData("Unit \(unitIndex), y \(yIndex) has wrong x dimension.")
                }
                for xIndex in 0..<nX {
                    let hist = counts[unitIndex][yIndex][xIndex]
                    guard hist.count == nBins else {
                        throw RFMappingError.invalidData("Unit \(unitIndex), y \(yIndex), x \(xIndex) has wrong bin dimension.")
                    }
                    guard hist.allSatisfy({ $0.isFinite && $0 >= 0 }) else {
                        throw RFMappingError.invalidData("Unit \(unitIndex), y \(yIndex), x \(xIndex) counts must be finite and non-negative.")
                    }
                }
            }
        }

        if let presentationCounts {
            for yIndex in 0..<nY {
                for xIndex in 0..<nX where presentationCounts[yIndex][xIndex] == 0 {
                    let hasCounts = (0..<nUnits).contains { unitIndex in
                        counts[unitIndex][yIndex][xIndex].contains { $0 != 0 }
                    }
                    if hasCounts {
                        throw RFMappingError.invalidData("stimulusPresentationCounts is zero where spike counts are nonzero (y \(yIndex), x \(xIndex)).")
                    }
                }
            }
        }
    }
}

enum RFMappingError: LocalizedError {
    case invalidData(String)
    case presentationCountsRequired(ResponseValueMode)

    var errorDescription: String? {
        switch self {
        case .invalidData(let message):
            message
        case .presentationCountsRequired(let mode):
            "\(mode.rawValue) requires stimulusPresentationCounts metadata. Regenerate this legacy JSON with presentation-count metadata."
        }
    }
}
