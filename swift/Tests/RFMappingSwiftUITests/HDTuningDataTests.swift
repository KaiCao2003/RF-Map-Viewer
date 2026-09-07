import Foundation
import XCTest
@testable import RFMappingSwiftUI

func strictHDTuningPayload(
    unitIDs: [Int] = [17],
    zeroOccupancyBin: Int? = HDTuningData.rawBinCount - 1
) -> [String: Any] {
    var occupancySamples: [Any] = Array(
        repeating: 100,
        count: HDTuningData.rawBinCount
    )
    if let zeroOccupancyBin {
        occupancySamples[zeroOccupancyBin] = 0
    }
    let occupancyTime = occupancySamples.map { Double($0 as! Int) / 100 }
    var spikeCounts: [[Any]] = []
    var firingRates: [[Any]] = []
    var unitData: [String: [Any]] = [
        "hd_class": [],
        "rate_mvl": [],
        "spike_angle_mrl": [],
        "rayleigh_score": [],
        "rayleigh_p": [],
        "rayleigh_significant": [],
        "shuffle_p": [],
        "shuffle_significant": [],
    ]
    for (offset, _) in unitIDs.enumerated() {
        var counts = Array(
            repeating: offset + 2,
            count: HDTuningData.rawBinCount
        )
        var rates = zip(counts, occupancyTime).map { pair -> Any in
            let (count, occupied) = pair
            if occupied == 0 {
                return NSNull() as Any
            }
            return (Double(count) / occupied) as Any
        }
        if let zeroOccupancyBin {
            counts[zeroOccupancyBin] = 0
            rates[zeroOccupancyBin] = NSNull()
        }
        spikeCounts.append(counts.map { $0 as Any })
        firingRates.append(rates)
        unitData["hd_class", default: []].append(1)
        unitData["rate_mvl", default: []].append(0.25)
        unitData["spike_angle_mrl", default: []].append(0.2)
        unitData["rayleigh_score", default: []].append(8.0)
        unitData["rayleigh_p", default: []].append(0.01)
        unitData["rayleigh_significant", default: []].append(true)
        unitData["shuffle_p", default: []].append(0.5)
        unitData["shuffle_significant", default: []].append(false)
    }
    return [
        "metadata": [
            "session": "260630_1",
            "probe": "A",
            "timebase": "open_ephys_adc_t0_relative_seconds",
            "num_angle_bins": HDTuningData.rawBinCount,
            "feature_fs_hz": 100.0,
            "classification": [
                "method": "fixture",
                "rayleigh_alpha": 0.05,
                "shuffle_alpha": 0.01,
                "num_shuffle": 1_000,
            ],
            "ttl_qc": [
                "ttl_pulse_count": 100,
                "measured_rate_hz": 120.0,
                "camera_ttl_active_high": true,
            ],
        ],
        "angle_bin_edges_deg": (0...HDTuningData.rawBinCount).map { Double($0) * 2 },
        "occupancy_samples": occupancySamples,
        "occupancy_time_s": occupancyTime,
        "unit_id": unitIDs,
        "spike_counts": spikeCounts,
        "firing_rate_hz": firingRates,
        "unit_data": unitData,
    ]
}

final class HDTuningDataTests: XCTestCase {
    private let sourceURL = URL(fileURLWithPath: "/tmp/tuning_curves.tc")

    private func decode(_ payload: [String: Any]) throws -> HDTuningData {
        try HDTuningData(
            data: JSONSerialization.data(withJSONObject: payload),
            sourceURL: sourceURL
        )
    }

    private func assertRejected(
        _ payload: [String: Any],
        containing expected: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) throws {
        XCTAssertThrowsError(try decode(payload), file: file, line: line) { error in
            XCTAssertTrue(
                error.localizedDescription.contains(expected),
                "Expected error containing '\(expected)', got '\(error.localizedDescription)'.",
                file: file,
                line: line
            )
        }
    }

    private func assertRawRejected(
        _ json: String,
        containing expected: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertThrowsError(
            try HDTuningData(data: Data(json.utf8), sourceURL: sourceURL),
            file: file,
            line: line
        ) { error in
            XCTAssertTrue(
                error.localizedDescription.contains(expected),
                "Expected error containing '\(expected)', got '\(error.localizedDescription)'.",
                file: file,
                line: line
            )
        }
    }

    func testCurrentColumnarContractDecodesAndPreservesIntegerOccupancy() throws {
        let decoded = try decode(strictHDTuningPayload(unitIDs: [17, 42]))

        XCTAssertEqual(decoded.unitIDs, [17, 42])
        XCTAssertEqual(decoded.occupancySamples.count, HDTuningData.rawBinCount)
        XCTAssertEqual(decoded.occupancySamples.first, 100)
        XCTAssertEqual(decoded.occupancySamples.last, 0)
        XCTAssertEqual(try decoded.unit(byID: 17).hdClass, 1)
        XCTAssertNil(try decoded.unit(byID: 17).rawRatesHz.last!)
    }

    func testRejectsObsoleteSchemaVersionAndNonExactTopLevelKeys() throws {
        var obsolete = strictHDTuningPayload()
        obsolete["schema_version"] = 2
        try assertRejected(obsolete, containing: "schema_version is obsolete")

        var missing = strictHDTuningPayload()
        missing.removeValue(forKey: "occupancy_samples")
        try assertRejected(missing, containing: "Missing tuning-curve keys")

        var unexpected = strictHDTuningPayload()
        unexpected["unexpected"] = NSNull()
        try assertRejected(unexpected, containing: "Unexpected tuning-curve keys")
    }

    func testRejectsDuplicateTopLevelJSONKeysBeforeFoundationFoldsThem() {
        assertRawRejected(
            #"{"metadata":{},"metadata":{}}"#,
            containing: "Duplicate tuning-curve JSON key: metadata"
        )
    }

    func testRejectsDuplicateMetadataKeysIncludingEscapedSpellings() {
        assertRawRejected(
            #"{"metadata":{"a":1,"\u0061":2}}"#,
            containing: "Duplicate tuning-curve JSON key: a"
        )
        assertRawRejected(
            #"{"metadata":{"escaped\"key":1,"escaped\"key":2}}"#,
            containing: "Duplicate tuning-curve JSON key: escaped\"key"
        )
    }

    func testRejectsDuplicateUnitDataAndClassificationKeysRecursively() {
        assertRawRejected(
            #"{"unit_data":{"hd_class":[],"hd_class":[]}}"#,
            containing: "Duplicate tuning-curve JSON key: hd_class"
        )
        assertRawRejected(
            #"{"metadata":{"classification":{"method":"a","method":"b"}}}"#,
            containing: "Duplicate tuning-curve JSON key: method"
        )
    }

    func testRejectsInvalidOccupancyAndSamplingFrequencyContracts() throws {
        var fractionalSample = strictHDTuningPayload()
        var samples = fractionalSample["occupancy_samples"] as! [Any]
        samples[0] = 1.5
        fractionalSample["occupancy_samples"] = samples
        try assertRejected(fractionalSample, containing: "non-negative integer")

        var mismatchedMask = strictHDTuningPayload()
        samples = mismatchedMask["occupancy_samples"] as! [Any]
        samples[0] = 0
        mismatchedMask["occupancy_samples"] = samples
        try assertRejected(mismatchedMask, containing: "inconsistent samples and time")

        var inconsistentRates = strictHDTuningPayload()
        samples = inconsistentRates["occupancy_samples"] as! [Any]
        samples[0] = 200
        inconsistentRates["occupancy_samples"] = samples
        try assertRejected(inconsistentRates, containing: "inconsistent sampling rates")

        var featureMismatch = strictHDTuningPayload()
        var metadata = featureMismatch["metadata"] as! [String: Any]
        metadata["feature_fs_hz"] = 99.0
        featureMismatch["metadata"] = metadata
        try assertRejected(featureMismatch, containing: "does not match occupancy samples/time")

        var allZero = strictHDTuningPayload()
        allZero["occupancy_samples"] = Array(repeating: 0, count: HDTuningData.rawBinCount)
        allZero["occupancy_time_s"] = Array(repeating: 0.0, count: HDTuningData.rawBinCount)
        try assertRejected(allZero, containing: "positive occupancy")
    }

    func testRejectsFractionalCountsAndRateOccupancyMismatches() throws {
        var fractional = strictHDTuningPayload()
        var countRows = fractional["spike_counts"] as! [[Any]]
        countRows[0][0] = 1.5
        fractional["spike_counts"] = countRows
        try assertRejected(fractional, containing: "non-negative integer")

        var wrongRate = strictHDTuningPayload()
        var rateRows = wrongRate["firing_rate_hz"] as! [[Any]]
        rateRows[0][0] = 999.0
        wrongRate["firing_rate_hz"] = rateRows
        try assertRejected(wrongRate, containing: "does not match count / occupancy")

        var zeroOccupancyCount = strictHDTuningPayload()
        countRows = zeroOccupancyCount["spike_counts"] as! [[Any]]
        countRows[0][HDTuningData.rawBinCount - 1] = 1
        zeroOccupancyCount["spike_counts"] = countRows
        try assertRejected(zeroOccupancyCount, containing: "count 0 / rate null")

        var zeroOccupancyRate = strictHDTuningPayload()
        rateRows = zeroOccupancyRate["firing_rate_hz"] as! [[Any]]
        rateRows[0][HDTuningData.rawBinCount - 1] = 0.0
        zeroOccupancyRate["firing_rate_hz"] = rateRows
        try assertRejected(zeroOccupancyRate, containing: "count 0 / rate null")

        var occupiedNullRate = strictHDTuningPayload()
        rateRows = occupiedNullRate["firing_rate_hz"] as! [[Any]]
        rateRows[0][0] = NSNull()
        occupiedNullRate["firing_rate_hz"] = rateRows
        try assertRejected(occupiedNullRate, containing: "must be numeric")
    }

    func testRequiresExactTypedUnitDataColumnsAndValidHDClass() throws {
        var missing = strictHDTuningPayload()
        var unitData = missing["unit_data"] as! [String: [Any]]
        unitData.removeValue(forKey: "rate_mvl")
        missing["unit_data"] = unitData
        try assertRejected(missing, containing: "Missing tuning-curve unit_data keys")

        var unexpected = strictHDTuningPayload()
        unitData = unexpected["unit_data"] as! [String: [Any]]
        unitData["unexpected"] = [NSNull()]
        unexpected["unit_data"] = unitData
        try assertRejected(unexpected, containing: "Unexpected tuning-curve unit_data keys")

        var badMetric = strictHDTuningPayload()
        unitData = badMetric["unit_data"] as! [String: [Any]]
        unitData["rate_mvl"] = [1.5]
        badMetric["unit_data"] = unitData
        try assertRejected(badMetric, containing: "must not exceed")

        var nonBoolean = strictHDTuningPayload()
        unitData = nonBoolean["unit_data"] as! [String: [Any]]
        unitData["rayleigh_significant"] = [1]
        nonBoolean["unit_data"] = unitData
        try assertRejected(nonBoolean, containing: "boolean or null")

        var badClass = strictHDTuningPayload()
        unitData = badClass["unit_data"] as! [String: [Any]]
        unitData["hd_class"] = [3]
        badClass["unit_data"] = unitData
        try assertRejected(badClass, containing: "0, 1, 2, or null")

        var inconsistentClass = strictHDTuningPayload()
        unitData = inconsistentClass["unit_data"] as! [String: [Any]]
        unitData["hd_class"] = [2]
        inconsistentClass["unit_data"] = unitData
        try assertRejected(inconsistentClass, containing: "significance results")
    }

    func testValidatesKnownMetadataTypesAndAllowsForwardCompatibleExtras() throws {
        var payload = strictHDTuningPayload()
        var metadata = payload["metadata"] as! [String: Any]
        metadata["epoch"] = "arena"
        metadata["headplate"] = ["animal": "m15"]
        payload["metadata"] = metadata
        XCTAssertEqual(try decode(payload).unitIDs, [17])

        var invalidBins = payload
        metadata["num_angle_bins"] = 179
        invalidBins["metadata"] = metadata
        try assertRejected(invalidBins, containing: "must equal 180")

        var invalidAlpha = payload
        metadata = payload["metadata"] as! [String: Any]
        var classification = metadata["classification"] as! [String: Any]
        classification["rayleigh_alpha"] = 1.5
        metadata["classification"] = classification
        invalidAlpha["metadata"] = metadata
        try assertRejected(invalidAlpha, containing: "between 0 and 1")

        var invalidTTL = payload
        metadata = payload["metadata"] as! [String: Any]
        var ttl = metadata["ttl_qc"] as! [String: Any]
        ttl["camera_ttl_active_high"] = 1
        metadata["ttl_qc"] = ttl
        invalidTTL["metadata"] = metadata
        try assertRejected(invalidTTL, containing: "boolean or null")
    }

    func testNullableMetricsAndRoundoffAtUnitMetricUpperBoundAreAccepted() throws {
        var payload = strictHDTuningPayload()
        var unitData = payload["unit_data"] as! [String: [Any]]
        for key in [
            "rate_mvl",
            "spike_angle_mrl",
            "rayleigh_score",
            "rayleigh_p",
            "rayleigh_significant",
            "shuffle_p",
            "shuffle_significant",
            "hd_class",
        ] {
            unitData[key] = [NSNull()]
        }
        payload["unit_data"] = unitData
        XCTAssertNil(try decode(payload).unit(byID: 17).hdClass)

        payload = strictHDTuningPayload()
        unitData = payload["unit_data"] as! [String: [Any]]
        unitData["rate_mvl"] = [1.0000000000000002]
        unitData["spike_angle_mrl"] = [1.0000000000000002]
        payload["unit_data"] = unitData
        let rounded = try decode(payload)
        XCTAssertEqual(rounded.unitIDs, [17])
        let roundedMetrics = try rounded.unit(byID: 17).metrics
        XCTAssertEqual(try XCTUnwrap(roundedMetrics["rate_mvl"]), .number(1))
        XCTAssertEqual(try XCTUnwrap(roundedMetrics["spike_angle_mrl"]), .number(1))

        unitData["rate_mvl"] = [1.0 + 2e-12]
        payload["unit_data"] = unitData
        try assertRejected(payload, containing: "rate_mvl must not exceed")
    }

    func testBoundaryNormalizationDrivesSignificanceAndPersistedMetrics() throws {
        var payload = strictHDTuningPayload()
        var metadata = payload["metadata"] as! [String: Any]
        var classification = metadata["classification"] as! [String: Any]
        classification["rayleigh_alpha"] = 1.0
        classification["shuffle_alpha"] = 1.0
        metadata["classification"] = classification
        payload["metadata"] = metadata

        var unitData = payload["unit_data"] as! [String: [Any]]
        let roundoff = 1.0 + 5e-13
        unitData["rate_mvl"] = [roundoff]
        unitData["spike_angle_mrl"] = [roundoff]
        unitData["rayleigh_p"] = [roundoff]
        unitData["rayleigh_significant"] = [false]
        unitData["shuffle_p"] = [roundoff]
        unitData["shuffle_significant"] = [true]
        unitData["hd_class"] = [1]
        payload["unit_data"] = unitData

        let unit = try decode(payload).unit(byID: 17)
        XCTAssertEqual(unit.hdClass, 1)
        for key in ["rate_mvl", "spike_angle_mrl", "rayleigh_p", "shuffle_p"] {
            XCTAssertEqual(
                try XCTUnwrap(unit.metrics[key]),
                .number(1),
                "Expected normalized \(key)"
            )
        }
    }
}
