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
}

private struct HDTuningPayload: Decodable {
    let metadata: [String: RFMapJSONValue]
    let angleBinEdgesDegrees: [Double]
    let occupancyTimeSeconds: [Double]
    let unitIDs: [Int]
    let spikeCounts: [[Double]]
    let firingRatesHz: [[Double?]]
    let unitData: [String: [RFMapJSONValue]]

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

struct HDTuningData: Sendable {
    static let rawBinCount = 180
    static let defaultDisplayBins = 30
    static let defaultSmoothSigma = 1.5

    let sourceURL: URL
    let angleBinEdgesDegrees: [Double]
    let occupancyTimeSeconds: [Double]
    let unitIDs: [Int]
    let metadata: [String: RFMapJSONValue]
    private let unitsByID: [Int: HDTuningUnit]

    init(data: Data, sourceURL: URL) throws {
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
            var metrics: [String: RFMapJSONValue] = [:]
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
                groupedOccupancy > 1e-12 ? groupedCounts / groupedOccupancy : 0.0
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
