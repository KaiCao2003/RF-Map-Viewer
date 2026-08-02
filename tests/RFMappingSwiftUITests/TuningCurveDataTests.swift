import Foundation
import XCTest
@testable import RFMappingSwiftUI

final class TuningCurveDataTests: XCTestCase {
    func testCompareScaleNeverFallsBackToPerCellWhileSharedScaleIsPending() {
        XCTAssertNil(hdTuningDisplayScaleHigh(
            curvePeakHz: 4,
            compareScale: true,
            sharedScaleHigh: nil
        ))
        XCTAssertEqual(hdTuningDisplayScaleHigh(
            curvePeakHz: 4,
            compareScale: true,
            sharedScaleHigh: 10
        ), 10)
        XCTAssertEqual(hdTuningDisplayScaleHigh(
            curvePeakHz: 4,
            compareScale: false,
            sharedScaleHigh: nil
        ), 4)
    }

    func testSchemaV2LoadsScientificProvenanceAndUsesCountsOverOccupancy() throws {
        var occupancy = Array(repeating: 1.0, count: hdRawBinCount)
        occupancy[5] = 5.0
        var counts = Array(repeating: 0, count: hdRawBinCount)
        counts[0] = 10
        let payload = version2Payload(
            occupancy: occupancy,
            units: [version2Unit(unitID: 7, counts: counts, occupancy: occupancy, hdClass: 1)],
            metadata: [
                "session": "260729_1",
                "probe": "A",
                "timebase": "open_ephys_adc_t0_relative_seconds",
                "timestamp_reference": "motive_exposure_ttl_midpoint",
                "angle_convention_note": "0 degrees up, positive counter-clockwise",
                "num_angle_bins": 180,
                "feature_fs_hz": 120.006,
                "classification": [
                    "method": "occupancy_adjusted_rayleigh_or_circular_shift_v1",
                    "class_1": "exactly one significant",
                    "class_2": "rayleigh and shuffle significant",
                    "rayleigh_alpha": 0.05,
                    "shuffle_alpha": 0.01,
                    "num_shuffle": 1000,
                    "shuffle_seed": 0
                ],
                "ttl_qc": [
                    "ttl_pulse_count": 478_692,
                    "measured_rate_hz": 120.006,
                    "camera_ttl_active_high": true
                ]
            ]
        )

        let subject = try load(payload)

        XCTAssertEqual(subject.schema, .version2)
        XCTAssertEqual(subject.unitIDs, [7])
        XCTAssertEqual(subject.hdClass(for: 7), .oneTestSignificant)
        XCTAssertEqual(subject.metadata?.timestampReference, "motive_exposure_ttl_midpoint")
        XCTAssertEqual(
            subject.metadata?.classification?.method,
            "occupancy_adjusted_rayleigh_or_circular_shift_v1"
        )
        XCTAssertEqual(subject.metadata?.classification?.numberOfShuffles, 1000)
        XCTAssertEqual(subject.metadata?.ttlQC?.pulseCount, 478_692)
        XCTAssertEqual(subject.metadata?.ttlQC?.cameraTTLActiveHigh, true)

        let processed = try XCTUnwrap(subject.processedCurve(
            for: 7,
            displayBins: 30,
            smoothing: false
        ))
        XCTAssertEqual(processed.anglesDeg.first, 6.0)
        XCTAssertEqual(try XCTUnwrap(processed.firingRatesHz.first ?? nil), 1.0, accuracy: 1e-12)

        let rawRates = try XCTUnwrap(subject.rates(for: 7))
        let incorrectAverage = rawRates.prefix(6).compactMap { $0 }.reduce(0, +) / 6.0
        XCTAssertNotEqual(incorrectAverage, processed.firingRatesHz[0])
    }

    func testZeroOccupancyStaysMissingUntilCountsAndOccupancyAreSmoothed() throws {
        let occupancy = Array(repeating: 0.0, count: 6)
            + Array(repeating: 1.0, count: hdRawBinCount - 6)
        let counts = Array(repeating: 0, count: 6)
            + Array(repeating: 2, count: hdRawBinCount - 6)
        let subject = try load(version2Payload(
            occupancy: occupancy,
            units: [version2Unit(unitID: 7, counts: counts, occupancy: occupancy, hdClass: 0)]
        ))

        XCTAssertNil(subject.rates(for: 7)?[0])
        XCTAssertEqual(subject.hdClass(for: 7), .notSignificant)

        let unsmoothed = try XCTUnwrap(subject.processedCurve(
            for: 7,
            displayBins: 30,
            smoothing: false
        ))
        XCTAssertNil(unsmoothed.firingRatesHz[0])
        for rate in unsmoothed.firingRatesHz.dropFirst() {
            XCTAssertEqual(try XCTUnwrap(rate), 2.0, accuracy: 1e-12)
        }

        let smoothed = try XCTUnwrap(subject.processedCurve(
            for: 7,
            displayBins: 30,
            smoothing: true,
            sigmaAtThirtyBins: 1.5
        ))
        for rate in smoothed.firingRatesHz {
            XCTAssertEqual(try XCTUnwrap(rate), 2.0, accuracy: 1e-12)
        }
    }

    func testCircularGaussianMatchesPythonAndKeepsFixedAngularWidth() throws {
        let rates = (0..<hdRawBinCount).map(Double.init)
        let subject = try load(["42": rates])
        let processed = try XCTUnwrap(subject.processedCurve(
            for: 42,
            displayBins: 30,
            smoothing: true,
            sigmaAtThirtyBins: 1.5
        ))
        let pythonGolden = [
            68.56321685550363,
            36.2290617168899,
            22.547650225074413,
            22.068667571806824,
            26.701135176419378,
            32.51605979235437
        ]
        for (actual, expected) in zip(processed.firingRatesHz.prefix(6), pythonGolden) {
            XCTAssertEqual(try XCTUnwrap(actual), expected, accuracy: 1e-11)
        }

        for bins in [6, 30, 60, 180] {
            let sigmaBins = try hdTuningSmoothingSigmaBins(1.5, displayBins: bins)
            XCTAssertEqual(sigmaBins * 360.0 / Double(bins), 18.0, accuracy: 1e-12)
        }
        XCTAssertEqual(normalizeHDTuningBinCount(8), 6)
        XCTAssertEqual(normalizeHDTuningBinCount(181), 180)
        XCTAssertEqual(normalizeHDTuningBinCount(0), 1)
    }

    func testLegacySchemaNormalizesIDsAndAveragesOnlyBecauseOccupancyIsUnavailable() throws {
        let rates = (0..<hdRawBinCount).map(Double.init)
        let subject = try load([
            "42": rates,
            "007": rates.map { $0 + 1.5 }
        ])

        XCTAssertEqual(subject.schema, .legacy)
        XCTAssertNil(subject.occupancyTimeSeconds)
        XCTAssertNil(subject.metadata)
        XCTAssertEqual(subject.unitIDs, [7, 42])
        XCTAssertNil(subject.hdClass(for: 7))
        let processed = try XCTUnwrap(subject.processedCurve(
            for: 42,
            displayBins: 30,
            smoothing: false
        ))
        XCTAssertEqual(processed.anglesDeg.prefix(2), [6.0, 18.0])
        XCTAssertEqual(try XCTUnwrap(processed.firingRatesHz[0]), 2.5, accuracy: 1e-12)
        XCTAssertEqual(try XCTUnwrap(processed.firingRatesHz[1]), 8.5, accuracy: 1e-12)
        XCTAssertNil(try subject.processedCurve(for: 99, displayBins: 30, smoothing: false))
    }

    func testSchemaV2RejectsInvalidEdgesOccupancyRatesClassesAndDuplicateUnits() throws {
        let occupancy = Array(repeating: 1.0, count: hdRawBinCount)
        let unit = version2Unit(unitID: 7, counts: Array(repeating: 1, count: hdRawBinCount), occupancy: occupancy, hdClass: 1)

        var wrongEdges = version2Payload(occupancy: occupancy, units: [unit])
        var edges = wrongEdges["angle_bin_edges_deg"] as! [Double]
        edges[10] += 0.25
        wrongEdges["angle_bin_edges_deg"] = edges
        assertInvalid(wrongEdges, contains: "0–360°")

        var allZeroOccupancy = version2Payload(
            occupancy: Array(repeating: 0.0, count: hdRawBinCount),
            units: [version2Unit(
                unitID: 7,
                counts: Array(repeating: 0, count: hdRawBinCount),
                occupancy: Array(repeating: 0.0, count: hdRawBinCount),
                hdClass: 1
            )]
        )
        assertInvalid(allZeroOccupancy, contains: "positive occupancy")

        var rateMismatch = version2Payload(occupancy: occupancy, units: [unit])
        var mismatchUnit = (rateMismatch["units"] as! [[String: Any]])[0]
        var mismatchRates = mismatchUnit["firing_rate_hz"] as! [Any]
        mismatchRates[0] = 99.0
        mismatchUnit["firing_rate_hz"] = mismatchRates
        rateMismatch["units"] = [mismatchUnit]
        assertInvalid(rateMismatch, contains: "does not match count / occupancy")

        var invalidClass = version2Payload(occupancy: occupancy, units: [unit])
        var classUnit = (invalidClass["units"] as! [[String: Any]])[0]
        classUnit["hd_class"] = 3
        invalidClass["units"] = [classUnit]
        assertInvalid(invalidClass, contains: "hd_class")

        let duplicate = version2Payload(occupancy: occupancy, units: [unit, unit])
        assertInvalid(duplicate, contains: "Duplicate schema v2 unit_id")

        allZeroOccupancy["schema_version"] = 3
        assertInvalid(allZeroOccupancy, contains: "Unsupported tuning-curve schema version")
    }

    func testZeroOccupancyRequiresNullRateAndZeroCount() throws {
        var occupancy = Array(repeating: 1.0, count: hdRawBinCount)
        occupancy[0] = 0.0
        var counts = Array(repeating: 1, count: hdRawBinCount)
        counts[0] = 0
        var unit = version2Unit(unitID: 7, counts: counts, occupancy: occupancy, hdClass: nil)
        var rates = unit["firing_rate_hz"] as! [Any]
        rates[0] = 0.0
        unit["firing_rate_hz"] = rates
        assertInvalid(
            version2Payload(occupancy: occupancy, units: [unit]),
            contains: "zero occupancy"
        )

        counts[0] = 1
        unit = version2Unit(unitID: 7, counts: counts, occupancy: occupancy, hdClass: nil)
        assertInvalid(
            version2Payload(occupancy: occupancy, units: [unit]),
            contains: "zero occupancy"
        )
    }

    func testLegacyRejectsDuplicateNormalizedIDAndBadRates() throws {
        let rates = (0..<hdRawBinCount).map(Double.init)
        assertInvalid(["1": rates, "01": rates], contains: "Duplicate cluster ID")
        assertInvalid(["42": Array(rates.dropLast())], contains: "exactly 180 rates")
        var negative = rates
        negative[4] = -1
        assertInvalid(["42": negative], contains: "finite and non-negative")
    }

    func testProbeInferenceAndDiscoveryUseEarliestNumericSessionForSameProbe() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("TuningCurveDiscoveryTests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let rfURL = root
            .appendingPathComponent("260730_10/data/rfmapping/good/ProbeA", isDirectory: true)
            .appendingPathComponent("regular_unitsSpikeCounts_260730_10.json")
        try FileManager.default.createDirectory(
            at: rfURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try Data("{}".utf8).write(to: rfURL)

        let firstA = root
            .appendingPathComponent("260730_2/data/tuning_curves/ProbeA", isDirectory: true)
            .appendingPathComponent("tuning_curves.json")
        let laterA = root
            .appendingPathComponent("260730_10/data/tuning_curves/ProbeA", isDirectory: true)
            .appendingPathComponent("tuning_curves.json")
        let otherProbe = root
            .appendingPathComponent("260730_1/data/tuning_curves/ProbeB", isDirectory: true)
            .appendingPathComponent("tuning_curves.json")
        let otherDay = root
            .appendingPathComponent("260731_1/data/tuning_curves/ProbeA", isDirectory: true)
            .appendingPathComponent("tuning_curves.json")
        for url in [firstA, laterA, otherProbe, otherDay] {
            try FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try Data("{}".utf8).write(to: url)
        }

        XCTAssertEqual(TuningCurveDiscovery.probeName(for: URL(fileURLWithPath: "session-B.json")), "ProbeB")
        XCTAssertEqual(
            TuningCurveDiscovery.probeName(for: URL(fileURLWithPath: "/tmp/session ProbeA/rf.json")),
            "ProbeA"
        )
        XCTAssertEqual(TuningCurveDiscovery.discoverURL(for: rfURL), firstA.standardizedFileURL)

        try FileManager.default.removeItem(at: firstA)
        XCTAssertEqual(TuningCurveDiscovery.discoverURL(for: rfURL), laterA.standardizedFileURL)
        try FileManager.default.removeItem(at: laterA)
        XCTAssertNil(TuningCurveDiscovery.discoverURL(for: rfURL))
    }

    private func version2Payload(
        occupancy: [Double],
        units: [[String: Any]],
        metadata: [String: Any]? = nil
    ) -> [String: Any] {
        var payload: [String: Any] = [
            "schema_version": 2,
            "angle_bin_edges_deg": (0...hdRawBinCount).map { Double($0) * 2.0 },
            "occupancy_time_s": occupancy,
            "units": units
        ]
        if let metadata { payload["metadata"] = metadata }
        return payload
    }

    private func version2Unit(
        unitID: Int,
        counts: [Int],
        occupancy: [Double],
        hdClass: Int?
    ) -> [String: Any] {
        var rates: [Any] = []
        rates.reserveCapacity(min(counts.count, occupancy.count))
        for (count, occupiedSeconds) in zip(counts, occupancy) {
            if occupiedSeconds > 0.0 {
                rates.append(Double(count) / occupiedSeconds)
            } else {
                rates.append(NSNull())
            }
        }
        return [
            "unit_id": unitID,
            "spike_counts": counts,
            "firing_rate_hz": rates,
            "hd_class": hdClass.map { $0 as Any } ?? NSNull()
        ]
    }

    private func load(_ payload: Any) throws -> TuningCurveData {
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("TuningCurveDataTests-\(UUID().uuidString)")
            .appendingPathExtension("json")
        try data.write(to: url, options: .atomic)
        defer { try? FileManager.default.removeItem(at: url) }
        return try TuningCurveData(url: url)
    }

    private func assertInvalid(
        _ payload: Any,
        contains expectedText: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertThrowsError(try load(payload), file: file, line: line) { error in
            guard
                let tuningError = error as? TuningCurveError,
                case .invalidData(let message) = tuningError
            else {
                return XCTFail("Expected invalidData, got \(error)", file: file, line: line)
            }
            XCTAssertTrue(
                message.contains(expectedText),
                "Expected error containing '\(expectedText)', got '\(message)'",
                file: file,
                line: line
            )
        }
    }
}
