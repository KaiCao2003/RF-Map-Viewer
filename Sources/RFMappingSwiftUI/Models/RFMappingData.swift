import Foundation

private enum PresentationCountsPayload {
    case matrix([[Double]])
    case vector([Double])
    case scalar(Double)
}

private struct RFMappingPayload: Decodable {
    let unitsSpikeCounts: ContiguousArray<Double>
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
        unitsSpikeCountsSize = try container.decode([Int].self, forKey: .unitsSpikeCountsSize)
        let expectedCount = try Self.expectedCount(for: unitsSpikeCountsSize)
        unitPool = try container.decode([Int].self, forKey: .unitPool)
        xPositions = try container.decode([Double].self, forKey: .xPositions)
        yPositions = try container.decode([Double].self, forKey: .yPositions)
        timeBinEdges = try container.decode([Double].self, forKey: .timeBinEdges)

        let nUnits = unitsSpikeCountsSize[0]
        let nY = unitsSpikeCountsSize[1]
        let nX = unitsSpikeCountsSize[2]
        let nBins = unitsSpikeCountsSize[3]
        var values = ContiguousArray<Double>()
        values.reserveCapacity(expectedCount)

        var units: any UnkeyedDecodingContainer
        do {
            units = try container.nestedUnkeyedContainer(forKey: .unitsSpikeCounts)
        } catch {
            throw RFMappingError.invalidData("unitsSpikeCounts must be a four-dimensional numeric array.")
        }
        guard units.count == nUnits else {
            throw RFMappingError.invalidData("unitsSpikeCounts first dimension does not match unitsSpikeCountsSize.")
        }

        for unitIndex in 0..<nUnits {
            try Task.checkCancellation()
            var yValues: any UnkeyedDecodingContainer
            do {
                yValues = try units.nestedUnkeyedContainer()
            } catch {
                throw RFMappingError.invalidData("Unit \(unitIndex) has wrong y dimension.")
            }
            guard yValues.count == nY else {
                throw RFMappingError.invalidData("Unit \(unitIndex) has wrong y dimension.")
            }

            for yIndex in 0..<nY {
                try Task.checkCancellation()
                var xValues: any UnkeyedDecodingContainer
                do {
                    xValues = try yValues.nestedUnkeyedContainer()
                } catch {
                    throw RFMappingError.invalidData("Unit \(unitIndex), y \(yIndex) has wrong x dimension.")
                }
                guard xValues.count == nX else {
                    throw RFMappingError.invalidData("Unit \(unitIndex), y \(yIndex) has wrong x dimension.")
                }

                for xIndex in 0..<nX {
                    var bins: any UnkeyedDecodingContainer
                    do {
                        bins = try xValues.nestedUnkeyedContainer()
                    } catch {
                        throw RFMappingError.invalidData(
                            "Unit \(unitIndex), y \(yIndex), x \(xIndex) has wrong bin dimension."
                        )
                    }
                    guard bins.count == nBins else {
                        throw RFMappingError.invalidData(
                            "Unit \(unitIndex), y \(yIndex), x \(xIndex) has wrong bin dimension."
                        )
                    }

                    for _ in 0..<nBins {
                        let value: Double
                        do {
                            value = try bins.decode(Double.self)
                        } catch {
                            throw RFMappingError.invalidData(
                                "Unit \(unitIndex), y \(yIndex), x \(xIndex) counts must be finite and non-negative."
                            )
                        }
                        guard value.isFinite, value >= 0 else {
                            throw RFMappingError.invalidData(
                                "Unit \(unitIndex), y \(yIndex), x \(xIndex) counts must be finite and non-negative."
                            )
                        }
                        values.append(value)
                    }
                }
            }
        }
        guard values.count == expectedCount else {
            throw RFMappingError.invalidData("unitsSpikeCounts does not match unitsSpikeCountsSize.")
        }
        unitsSpikeCounts = values

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

    private static func expectedCount(for dimensions: [Int]) throws -> Int {
        guard dimensions.count == 4 else {
            throw RFMappingError.invalidData("unitsSpikeCountsSize must contain 4 values.")
        }
        guard dimensions.allSatisfy({ $0 > 0 }) else {
            throw RFMappingError.invalidData("unitsSpikeCountsSize values must all be positive.")
        }
        var result = 1
        for dimension in dimensions {
            let product = result.multipliedReportingOverflow(by: dimension)
            guard !product.overflow else {
                throw RFMappingError.invalidData("unitsSpikeCountsSize is too large.")
            }
            result = product.partialValue
        }
        return result
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
    private let flatCounts: ContiguousArray<Double>
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
        } catch is CancellationError {
            throw CancellationError()
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
        flatCounts = payload.unitsSpikeCounts
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

    @inline(__always)
    func count(unitIndex: Int, yIndex: Int, xIndex: Int, binIndex: Int) -> Double {
        flatCounts[cellCountOffset(unitIndex: unitIndex, yIndex: yIndex, xIndex: xIndex) + binIndex]
    }

    @inline(__always)
    private func cellCountOffset(unitIndex: Int, yIndex: Int, xIndex: Int) -> Int {
        ((unitIndex * nY + yIndex) * nX + xIndex) * nBins
    }

    @inline(__always)
    private func compensatedCountSum(start: Int, end: Int) -> Double {
        var high = 0.0
        var low = 0.0
        for index in start...end {
            let value = flatCounts[index]
            let next = high + value
            if !next.isFinite {
                high = next
                low = 0.0
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
                let countBase = cellCountOffset(
                    unitIndex: unitIndex,
                    yIndex: yIndex,
                    xIndex: xIndex
                )
                let cellTotal = compensatedCountSum(
                    start: countBase,
                    end: countBase + nBins - 1
                )
                var cellPeak = 0.0
                var earliestBest = 0
                for binIndex in 0..<nBins {
                    let count = flatCounts[countBase + binIndex]
                    if count > cellPeak {
                        cellPeak = count
                        earliestBest = binIndex
                    }
                    binTotals[binIndex] += count
                    maxBinCount = max(maxBinCount, count)
                }
                let bestBin: Int?
                let delay: Double?
                let cellEntropy: Double

                if cellTotal > 0 {
                    bestBin = earliestBest
                    delay = binCenterMS(earliestBest)
                    var entropyValue = 0.0
                    for binIndex in 0..<nBins {
                        let count = flatCounts[countBase + binIndex]
                        guard count > 0 else { continue }
                        let probability = count / cellTotal
                        entropyValue -= probability * log(probability)
                    }
                    cellEntropy = nBins > 1 ? entropyValue / log(Double(nBins)) : 0.0
                } else {
                    bestBin = nil
                    delay = nil
                    cellEntropy = 0.0
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
        return (0..<nY).map { yIndex in
            (0..<nX).map { xIndex in
                let base = (yIndex * nX + xIndex) * stride
                return prefixRangeCount(
                    prefix,
                    base: base,
                    countBase: cellCountOffset(
                        unitIndex: unitIndex,
                        yIndex: yIndex,
                        xIndex: xIndex
                    ),
                    low: low,
                    high: high
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
            countBase: cellCountOffset(unitIndex: unitIndex, yIndex: yIndex, xIndex: xIndex),
            low: low,
            high: high
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
        if let presentationCounts, presentationCounts[yIndex][xIndex] <= 0 {
            return nil
        }
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
        return (0..<nY).map { yIndex in
            (0..<nX).map { xIndex -> Double? in
                let base = (yIndex * nX + xIndex) * stride
                let count = prefixRangeCount(
                    prefix,
                    base: base,
                    countBase: cellCountOffset(
                        unitIndex: unitIndex,
                        yIndex: yIndex,
                        xIndex: xIndex
                    ),
                    low: low,
                    high: high
                )
                if let presentationCounts,
                   presentationCounts[yIndex][xIndex] <= 0 {
                    return nil
                }
                if valueMode == .spikeCount { return count }
                guard let presentationCounts else { return nil }
                let presentations = presentationCounts[yIndex][xIndex]
                guard presentations > 0 else { return nil }
                let divisor = presentations * (valueMode == .meanFiringRate ? duration : 1.0)
                return count / divisor
            }
        }
    }

    /// Pools raw observations before normalizing a displayed spatial cell.
    /// `stimulusPresentationCounts` is the exposure for each source position,
    /// so averaging already-normalized source rates would overweight positions
    /// with fewer presentations.
    func spatialGroupObservations(
        unitIndex: Int,
        yGroup: AxisGroup,
        xGroup: AxisGroup,
        start: Int,
        end: Int
    ) -> SpatialGroupObservations {
        let yLow = max(0, min(nY - 1, min(yGroup.start, yGroup.end)))
        let yHigh = max(0, min(nY - 1, max(yGroup.start, yGroup.end)))
        let xLow = max(0, min(nX - 1, min(xGroup.start, xGroup.end)))
        let xHigh = max(0, min(nX - 1, max(xGroup.start, xGroup.end)))
        var counts: [Double] = []
        var presentations: [Double] = []
        counts.reserveCapacity((yHigh - yLow + 1) * (xHigh - xLow + 1))
        presentations.reserveCapacity(counts.capacity)
        for yIndex in yLow...yHigh {
            for xIndex in xLow...xHigh {
                if let presentationCounts {
                    let exposure = presentationCounts[yIndex][xIndex]
                    guard exposure > 0 else { continue }
                    presentations.append(exposure)
                }
                counts.append(rangeCount(
                    unitIndex: unitIndex,
                    yIndex: yIndex,
                    xIndex: xIndex,
                    start: start,
                    end: end
                ))
            }
        }
        return SpatialGroupObservations(
            count: compensatedSum(counts),
            presentations: presentationCounts == nil ? nil : compensatedSum(presentations),
            sourcePixelCount: counts.count
        )
    }

    func spatialGroupResponseValue(
        unitIndex: Int,
        yGroup: AxisGroup,
        xGroup: AxisGroup,
        start: Int,
        end: Int,
        valueMode: ResponseValueMode
    ) throws -> Double? {
        let observations = spatialGroupObservations(
            unitIndex: unitIndex,
            yGroup: yGroup,
            xGroup: xGroup,
            start: start,
            end: end
        )
        if valueMode == .spikeCount {
            guard observations.sourcePixelCount > 0 else { return nil }
            return observations.count / Double(observations.sourcePixelCount)
        }
        guard let presentations = observations.presentations else {
            throw RFMappingError.presentationCountsRequired(valueMode)
        }
        guard presentations > 0 else { return nil }
        var value = observations.count / presentations
        if valueMode == .meanFiringRate {
            value /= timeSpanSeconds(start: start, end: end)
        }
        return value
    }

    func spatialGroupResponseMatrix(
        unitIndex: Int,
        start: Int,
        end: Int,
        valueMode: ResponseValueMode,
        yGroups: [AxisGroup],
        xGroups: [AxisGroup]
    ) throws -> OptionalMatrix {
        try yGroups.map { yGroup in
            try xGroups.map { xGroup in
                try spatialGroupResponseValue(
                    unitIndex: unitIndex,
                    yGroup: yGroup,
                    xGroup: xGroup,
                    start: start,
                    end: end,
                    valueMode: valueMode
                )
            }
        }
    }

    func spatialGroupCountHistogram(
        unitIndex: Int,
        yGroup: AxisGroup,
        xGroup: AxisGroup
    ) -> [Double] {
        let yLow = max(0, min(nY - 1, min(yGroup.start, yGroup.end)))
        let yHigh = max(0, min(nY - 1, max(yGroup.start, yGroup.end)))
        let xLow = max(0, min(nX - 1, min(xGroup.start, xGroup.end)))
        let xHigh = max(0, min(nX - 1, max(xGroup.start, xGroup.end)))
        return (0..<nBins).map { binIndex in
            var values: [Double] = []
            values.reserveCapacity((yHigh - yLow + 1) * (xHigh - xLow + 1))
            for yIndex in yLow...yHigh {
                for xIndex in xLow...xHigh {
                    if let presentationCounts,
                       presentationCounts[yIndex][xIndex] <= 0 {
                        continue
                    }
                    values.append(count(
                        unitIndex: unitIndex,
                        yIndex: yIndex,
                        xIndex: xIndex,
                        binIndex: binIndex
                    ))
                }
            }
            return compensatedSum(values)
        }
    }

    /// Number of source positions that contribute measured observations to a
    /// displayed spatial group. Legacy files without exposure metadata retain
    /// their historical behavior and count every source position.
    func spatialGroupSourcePixelCount(
        yGroup: AxisGroup,
        xGroup: AxisGroup
    ) -> Int {
        let yLow = max(0, min(nY - 1, min(yGroup.start, yGroup.end)))
        let yHigh = max(0, min(nY - 1, max(yGroup.start, yGroup.end)))
        let xLow = max(0, min(nX - 1, min(xGroup.start, xGroup.end)))
        let xHigh = max(0, min(nX - 1, max(xGroup.start, xGroup.end)))
        guard let presentationCounts else {
            return (yHigh - yLow + 1) * (xHigh - xLow + 1)
        }
        var count = 0
        for yIndex in yLow...yHigh {
            for xIndex in xLow...xHigh where presentationCounts[yIndex][xIndex] > 0 {
                count += 1
            }
        }
        return count
    }

    /// Derives temporal metrics only after pooling every source histogram in
    /// the displayed spatial cell. Entropy retains native source-bin support;
    /// delay retains the viewer's current time-group support.
    func spatialGroupTemporalMetrics(
        unitIndex: Int,
        yGroup: AxisGroup,
        xGroup: AxisGroup,
        timeGroups: [AxisGroup]
    ) -> SpatialGroupTemporalMetrics {
        let histogram = spatialGroupCountHistogram(
            unitIndex: unitIndex,
            yGroup: yGroup,
            xGroup: xGroup
        )
        let pixelCount = spatialGroupSourcePixelCount(yGroup: yGroup, xGroup: xGroup)
        return temporalMetrics(
            histogram: histogram,
            timeGroups: timeGroups,
            sourcePixelCount: pixelCount
        )
    }

    func temporalMetrics(
        histogram: [Double],
        timeGroups: [AxisGroup],
        sourcePixelCount: Int = 1
    ) -> SpatialGroupTemporalMetrics {
        precondition(histogram.count == nBins, "Temporal histogram must match the source-bin count.")
        let total = compensatedSum(histogram)
        let grouped = timeGroups.map { group -> (start: Int, end: Int, count: Double, rate: Double) in
            let start = max(0, min(nBins - 1, min(group.start, group.end)))
            let end = max(0, min(nBins - 1, max(group.start, group.end)))
            let count = compensatedSum(histogram[start...end])
            let durationSeconds = timeBinEdges[end + 1] - timeBinEdges[start]
            return (start, end, count, count / durationSeconds)
        }

        let peakGroupIndex: Int?
        let delayMS: Double?
        var entropy = 0.0
        if total > 0, !grouped.isEmpty {
            var earliestBest = 0
            var peakRate = grouped[0].rate
            for index in 1..<grouped.count where grouped[index].rate > peakRate {
                peakRate = grouped[index].rate
                earliestBest = index
            }
            peakGroupIndex = earliestBest
            let peakGroup = grouped[earliestBest]
            delayMS = (timeBinEdges[peakGroup.start] + timeBinEdges[peakGroup.end + 1]) * 500.0
            for count in histogram where count > 0 {
                let probability = count / total
                entropy -= probability * log(probability)
            }
            if nBins > 1 { entropy /= log(Double(nBins)) }
        } else {
            peakGroupIndex = nil
            delayMS = nil
        }
        return SpatialGroupTemporalMetrics(
            meanTotalCount: total / Double(max(1, sourcePixelCount)),
            peakGroupIndex: peakGroupIndex,
            delayMS: delayMS,
            entropy: entropy
        )
    }

    private func prefixRangeCount(
        _ prefix: ExactPrefixValues,
        base: Int,
        countBase: Int,
        low: Int,
        high: Int
    ) -> Double {
        let stride = nBins + 1
        let cellIndex = base / stride
        guard prefix.safeCells[cellIndex] else {
            return compensatedCountSum(start: countBase + low, end: countBase + high)
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
        for yIndex in 0..<nY {
            for xIndex in 0..<nX {
                let base = (yIndex * nX + xIndex) * stride
                let countBase = cellCountOffset(
                    unitIndex: unitIndex,
                    yIndex: yIndex,
                    xIndex: xIndex
                )
                let cellIndex = yIndex * nX + xIndex
                var running = 0.0
                var isExactIntegerPrefix = true
                for bin in 0..<nBins {
                    let value = flatCounts[countBase + bin]
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
        guard flatCounts.count == nUnits * nY * nX * nBins else {
            throw RFMappingError.invalidData("unitsSpikeCounts does not match unitsSpikeCountsSize.")
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

        if let presentationCounts {
            for yIndex in 0..<nY {
                for xIndex in 0..<nX where presentationCounts[yIndex][xIndex] == 0 {
                    let hasCounts = (0..<nUnits).contains { unitIndex in
                        let base = cellCountOffset(
                            unitIndex: unitIndex,
                            yIndex: yIndex,
                            xIndex: xIndex
                        )
                        return (0..<nBins).contains { flatCounts[base + $0] != 0 }
                    }
                    if hasCounts {
                        throw RFMappingError.invalidData("stimulusPresentationCounts is zero where spike counts are nonzero (y \(yIndex), x \(xIndex)).")
                    }
                }
            }
        }
    }
}

/// Full Foundation JSON decoding has a high transient memory cost for real RF
/// documents. Keep every asynchronous document decode in one FIFO lane so
/// separate windows and external-open requests cannot multiply that peak.
actor AsyncSerialGate {
    private struct Waiter {
        let id: UUID
        let continuation: CheckedContinuation<Void, Error>
    }

    private var isHeld = false
    private var waiters: [Waiter] = []

    var waitingCount: Int { waiters.count }

    func enter() async throws {
        try Task.checkCancellation()
        if !isHeld {
            isHeld = true
            return
        }
        let waiterID = UUID()
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                waiters.append(Waiter(id: waiterID, continuation: continuation))
            }
        } onCancel: {
            Task { await self.cancelWaiter(waiterID) }
        }
    }

    func leave() {
        if waiters.isEmpty {
            isHeld = false
        } else {
            waiters.removeFirst().continuation.resume()
        }
    }

    private func cancelWaiter(_ id: UUID) {
        guard let index = waiters.firstIndex(where: { $0.id == id }) else { return }
        waiters.remove(at: index).continuation.resume(throwing: CancellationError())
    }
}

enum RFMappingDecodeCoordinator {
    private static let gate = AsyncSerialGate()

    static func decode(url: URL) async throws -> RFMappingData {
        try await gate.enter()
        do {
            try Task.checkCancellation()
            let decoded = try await RFMappingData.decodeOffMain(url: url)
            await gate.leave()
            return decoded
        } catch {
            await gate.leave()
            throw error
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
