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
            "head_direction_is_clockwise": true,
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

    func testCenteredCurveAlignsClockwiseHDWithSignedRFAnglesWithoutMirroringRates() {
        let curve = ProcessedHDCurve(
            anglesDegrees: [0, 90, 180, 270],
            ratesHz: [10, 20, 30, 40]
        )
        let centered = curve.centeredOnZero()

        XCTAssertEqual(centered.anglesDegrees, [-180, -90, 0, 90])
        XCTAssertEqual(centered.ratesHz, [30, 40, 10, 20])
        XCTAssertEqual(curve.anglesDegrees, [0, 90, 180, 270])
        XCTAssertEqual(curve.ratesHz, [10, 20, 30, 40])
    }

    func testHDPolarScreenCoordinatesIncreaseClockwiseFromNorth() {
        for (angle, x, y) in [
            (0.0, 0.0, -1.0),
            (90.0, 1.0, 0.0),
            (180.0, 0.0, 1.0),
            (270.0, -1.0, 0.0),
            (-90.0, -1.0, 0.0),
        ] {
            let vector = hdScreenVector(angleDegrees: angle)
            XCTAssertEqual(vector.x, x, accuracy: 1e-12)
            XCTAssertEqual(vector.y, y, accuracy: 1e-12)
        }
    }

    func testAcceptsOptionalSampleCountsAndAdditionalColumnarMetadata() throws {
        var payload = strictHDTuningPayload()
        payload.removeValue(forKey: "occupancy_samples")
        payload["additional_provenance"] = "saved upstream"
        XCTAssertEqual(try decode(payload).unitIDs, [17])
        payload.removeValue(forKey: "unit_id")
        try assertRejected(payload, containing: "Missing tuning-curve keys")
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

    func testRejectsInvalidOccupancyWithoutInferringItFromSampleMetadata() throws {
        var payload = strictHDTuningPayload()
        payload["occupancy_time_s"] = Array(repeating: 0.0, count: HDTuningData.rawBinCount)
        try assertRejected(payload, containing: "positive occupancy")
        payload["occupancy_time_s"] = Array(repeating: -1.0, count: HDTuningData.rawBinCount)
        try assertRejected(payload, containing: "non-negative")
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

    func testPreservesSavedClassThreeAndFlexibleMetricColumns() throws {
        var payload = strictHDTuningPayload()
        payload["unit_data"] = ["hd_class": [3], "kappa": [2.7]]
        let unit = try decode(payload).unit(byID: 17)
        XCTAssertEqual(unit.hdClass, 3)
        XCTAssertEqual(unit.metrics["kappa"], .number(2.7))
        payload["unit_data"] = ["hd_class": [4]]
        try assertRejected(payload, containing: "0, 1, 2, 3, or null")
        payload["unit_data"] = ["kappa": [1.0, 2.0]]
        try assertRejected(payload, containing: "must contain 1 values")
        payload["unit_data"] = [String: [Any]]()
        XCTAssertNil(try decode(payload).unit(byID: 17).hdClass)
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

    func testSavedMetricsAndClassAreNotRecalculated() throws {
        var payload = strictHDTuningPayload()
        let saved = 1.0000000000000002
        payload["unit_data"] = ["hd_class": [2], "rate_mvl": [saved], "rayleigh_p": [0.7]]
        let unit = try decode(payload).unit(byID: 17)
        XCTAssertEqual(unit.hdClass, 2)
        XCTAssertEqual(unit.metrics["rate_mvl"], .number(saved))
        XCTAssertEqual(unit.metrics["rayleigh_p"], .number(0.7))
    }

    func testNestedV2AndLegacyRatesRetainTheirScientificInputs() throws {
        let columnar = strictHDTuningPayload()
        let nested: [String: Any] = [
            "schema_version": 2,
            "angle_bin_edges_deg": columnar["angle_bin_edges_deg"]!,
            "occupancy_time_s": columnar["occupancy_time_s"]!,
            "metadata": ["classification": ["class_3": "high kappa", "kappa_cutoff": 2.0]],
            "units": [["unit_id": 17, "spike_counts": (columnar["spike_counts"] as! [[Any]])[0],
                       "firing_rate_hz": (columnar["firing_rate_hz"] as! [[Any]])[0], "hd_class": 3]],
        ]
        let decoded = try decode(nested)
        XCTAssertEqual(try decoded.unit(byID: 17).hdClass, 3)
        XCTAssertTrue(try decoded.processedCurve(unitID: 17, displayBins: 180, smoothing: false).ratesHz[179].isNaN)
        let legacy = try decode(["17": Array(repeating: 4.0, count: 180)])
        XCTAssertTrue(try legacy.unit(byID: 17).spikeCounts.isEmpty)
        XCTAssertTrue(legacy.occupancyTimeSeconds.isEmpty)
        XCTAssertEqual(try legacy.processedCurve(unitID: 17, displayBins: 30, smoothing: false).ratesHz,
                       Array(repeating: 4.0, count: 30))
        XCTAssertEqual(try legacy.processedCurve(unitID: 17, displayBins: 30).ratesHz[0], 4.0, accuracy: 1e-12)
    }

}
