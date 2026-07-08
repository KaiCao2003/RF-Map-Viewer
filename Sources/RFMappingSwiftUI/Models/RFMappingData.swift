import Foundation

private struct RFMappingPayload: Decodable {
    let unitsSpikeCounts: [[[[Double]]]]
    let unitsSpikeCountsSize: [Int]
    let unitPool: [Int]
    let xPositions: [Double]
    let yPositions: [Double]
    let timeBinEdges: [Double]
}

final class RFMappingData {
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

    private var metricsCache: [Int: UnitMetrics] = [:]

    init(url: URL) throws {
        self.url = url
        let data = try Data(contentsOf: url)
        let payload = try JSONDecoder().decode(RFMappingPayload.self, from: data)

        guard payload.unitsSpikeCountsSize.count == 4 else {
            throw RFMappingError.invalidData("unitsSpikeCountsSize must contain 4 values.")
        }

        counts = payload.unitsSpikeCounts
        size = (
            Int(payload.unitsSpikeCountsSize[0]),
            Int(payload.unitsSpikeCountsSize[1]),
            Int(payload.unitsSpikeCountsSize[2]),
            Int(payload.unitsSpikeCountsSize[3])
        )
        nUnits = size.0
        nY = size.1
        nX = size.2
        nBins = size.3
        unitPool = payload.unitPool
        xPositions = payload.xPositions
        yPositions = payload.yPositions
        timeBinEdges = payload.timeBinEdges

        try validate()
    }

    func displayYIndices(flipY: Bool) -> [Int] {
        flipY ? Array(stride(from: nY - 1, through: 0, by: -1)) : Array(0..<nY)
    }

    func clusterID(for unitIndex: Int) -> Int {
        unitPool[unitIndex]
    }

    func binLabel(_ binIndex: Int) -> String {
        let start = timeBinEdges[binIndex] * 1000.0
        let end = timeBinEdges[binIndex + 1] * 1000.0
        return "\(binIndex): \(formatMS(start))-\(formatMS(end)) ms"
    }

    func binCenterMS(_ binIndex: Int) -> Double {
        (timeBinEdges[binIndex] + timeBinEdges[binIndex + 1]) * 500.0
    }

    func inferTotalDeg() -> Double {
        guard nX > 1 else { return 360.0 }
        let diffs = (0..<(nX - 1)).map { xPositions[$0 + 1] - xPositions[$0] }
        let step = diffs.reduce(0.0, +) / Double(diffs.count)
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
                let cellTotal = hist.reduce(0.0, +)
                let cellPeak = hist.max() ?? 0.0
                let bestBin: Int?
                let delay: Double?
                let cellEntropy: Double

                if cellTotal > 0 {
                    let localBest = hist.indices.max { hist[$0] < hist[$1] } ?? 0
                    bestBin = localBest
                    delay = binCenterMS(localBest)
                    var entropyValue = 0.0
                    for count in hist where count > 0 {
                        let p = count / cellTotal
                        entropyValue -= p * log(p)
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

    func aggregateMatrix(
        unitIndex: Int,
        mode: RFMode,
        binIndex: Int,
        rangeStart: Int,
        rangeEnd: Int
    ) -> [[Double]] {
        let metrics = metrics(for: unitIndex)
        switch mode {
        case .total:
            return metrics.total
        case .peak:
            return metrics.peak
        case .bin:
            return (0..<nY).map { yIndex in
                (0..<nX).map { xIndex in
                    counts[unitIndex][yIndex][xIndex][binIndex]
                }
            }
        case .rangeSum:
            let start = max(0, min(rangeStart, rangeEnd))
            let end = min(nBins - 1, max(rangeStart, rangeEnd))
            return (0..<nY).map { yIndex in
                (0..<nX).map { xIndex in
                    counts[unitIndex][yIndex][xIndex][start...end].reduce(0.0, +)
                }
            }
        }
    }

    private func validate() throws {
        guard counts.count == nUnits else {
            throw RFMappingError.invalidData("unitsSpikeCounts first dimension does not match unitsSpikeCountsSize.")
        }
        guard unitPool.count == nUnits else {
            throw RFMappingError.invalidData("unitPool length does not match unit count.")
        }
        guard xPositions.count == nX else {
            throw RFMappingError.invalidData("xPositions length does not match x dimension.")
        }
        guard yPositions.count == nY else {
            throw RFMappingError.invalidData("yPositions length does not match y dimension.")
        }
        guard timeBinEdges.count == nBins + 1 else {
            throw RFMappingError.invalidData("timeBinEdges must contain nBins + 1 edges.")
        }

        for unitIndex in 0..<nUnits {
            guard counts[unitIndex].count == nY else {
                throw RFMappingError.invalidData("Unit \(unitIndex) has wrong y dimension.")
            }
            for yIndex in 0..<nY {
                guard counts[unitIndex][yIndex].count == nX else {
                    throw RFMappingError.invalidData("Unit \(unitIndex), y \(yIndex) has wrong x dimension.")
                }
                for xIndex in 0..<nX {
                    guard counts[unitIndex][yIndex][xIndex].count == nBins else {
                        throw RFMappingError.invalidData("Unit \(unitIndex), y \(yIndex), x \(xIndex) has wrong bin dimension.")
                    }
                }
            }
        }
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
