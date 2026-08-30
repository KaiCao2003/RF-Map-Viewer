import Foundation
import XCTest
@testable import RFMappingSwiftUI

final class FileFormatCompatibilityTests: XCTestCase {
    private func temporaryRoot() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: false
        )
        return root
    }

    private func write(_ data: Data, to url: URL) throws {
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try data.write(to: url)
    }

    private func write(_ text: String, to url: URL) throws {
        try write(Data(text.utf8), to: url)
    }

    private func assertSameFile(
        _ actual: URL?,
        _ expected: URL,
        file: StaticString = #filePath,
        line: UInt = #line
    ) throws {
        let actual = try XCTUnwrap(actual, file: file, line: line)
        let actualAttributes = try FileManager.default.attributesOfItem(atPath: actual.path)
        let expectedAttributes = try FileManager.default.attributesOfItem(atPath: expected.path)
        XCTAssertEqual(
            actualAttributes[.systemNumber] as? NSNumber,
            expectedAttributes[.systemNumber] as? NSNumber,
            file: file,
            line: line
        )
        XCTAssertEqual(
            actualAttributes[.systemFileNumber] as? NSNumber,
            expectedAttributes[.systemFileNumber] as? NSNumber,
            file: file,
            line: line
        )
    }

    func testCurrentSchemaFilenameAliasesRemainAccepted() {
        XCTAssertTrue(RFMappingFileTypes.isRFMappingURL(
            URL(fileURLWithPath: "/tmp/map.RFMAP")
        ))
        XCTAssertTrue(RFMappingFileTypes.isRFMappingURL(
            URL(fileURLWithPath: "/tmp/map.json")
        ))
        XCTAssertTrue(RFMappingFileTypes.isTuningCurveURL(
            URL(fileURLWithPath: "/tmp/tuning.tc")
        ))
        XCTAssertTrue(RFMappingFileTypes.isTuningCurveURL(
            URL(fileURLWithPath: "/tmp/tuning.JSON")
        ))
        XCTAssertTrue(RFMappingFileTypes.isProbeURL(
            URL(fileURLWithPath: "/tmp/positions.probe")
        ))
        XCTAssertTrue(RFMappingFileTypes.isProbeURL(
            URL(fileURLWithPath: "/tmp/positions.csv")
        ))
        XCTAssertFalse(RFMappingFileTypes.isRFMappingURL(
            URL(fileURLWithPath: "/tmp/tuning.tc")
        ))
    }

    func testRFDiscoveryIncludesRFMapAndJSONFilenameAliasesOnly() throws {
        let root = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let rfmapURL = root.appendingPathComponent("new.rfmap")
        let jsonURL = root.appendingPathComponent("current.json")
        try write("{}", to: rfmapURL)
        try write("{}", to: jsonURL)
        try write("{}", to: root.appendingPathComponent("tuning.tc"))
        try write("{}", to: root.appendingPathComponent("TUNING_CURVES.JSON"))

        let paths = Set(JSONDiscovery.discoverJSONFiles(root: root).map(\.path))
        XCTAssertEqual(paths, Set([rfmapURL.path, jsonURL.path]))
    }

    func testRFMappingJSONSchemaDecodesFromRFMapFile() throws {
        let root = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("example.rfmap")
        let payload = currentRFSchemaPayload([
            "unitsSpikeCounts": [[[[1.0]]]],
            "unitsSpikeCountsSize": [1, 1, 1, 1],
            "unitPool": [17],
            "xPositions": [10.0],
            "yPositions": [20.0],
            "timeBinEdges": [0.0, 0.1],
        ], occupancyTimeSec: 0.1, occupancyTimeSecSize: [1, 1])
        try write(try JSONSerialization.data(withJSONObject: payload), to: url)

        let decoded = try RFMappingData(url: url)
        XCTAssertEqual(decoded.url.pathExtension, "rfmap")
        XCTAssertEqual(decoded.unitPool, [17])
        XCTAssertEqual(decoded.counts, [[[[1.0]]]])
        XCTAssertEqual(decoded.occupancyTimeSeconds, [[0.1]])
    }

    func testLegacyPresentationCountSchemaIsRejected() throws {
        let payload: [String: Any] = [
            "unitsSpikeCounts": [[[[1.0]]]],
            "unitsSpikeCountsSize": [1, 1, 1, 1],
            "unitPool": [17],
            "xPositions": [10.0],
            "yPositions": [20.0],
            "timeBinEdges": [0.0, 0.1],
            "stimulusPresentationCounts": [[1.0]],
        ]

        XCTAssertThrowsError(try RFMappingData(
            data: JSONSerialization.data(withJSONObject: payload),
            url: URL(fileURLWithPath: "/tmp/old-schema.rfmap")
        )) { error in
            XCTAssertTrue(error.localizedDescription.contains("occupancyTimeSec"))
        }
    }

    func testMATLABSingletonYScalarAxisDecodesFromRFMapFile() throws {
        let root = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("matlab-singleton-y.rfmap")
        let payload = Data(#"""
        {
          "unitsSpikeCounts": [[[[1.0], [2.0]]]],
          "unitsSpikeCountsSize": [1, 1, 2, 1],
          "unitPool": 17,
          "xPositions": [-3.0, 3.0],
          "yPositions": 0.0,
          "timeBinEdges": [0.0, 0.1],
          "occupancyTimeSec": [0.1, 0.2],
          "occupancyTimeSecSize": [1, 2],
          "responseUnits": "spike_count",
          "responseNormalization": "none",
          "spikeCountDefinition": "each_qualifying_trial_contributes_once_per_final_spatial_bin",
          "occupancyTimeDefinition": "sum_of_qualifying_trial_durations_per_final_spatial_bin"
        }
        """#.utf8)
        try write(payload, to: url)

        let decoded = try RFMappingData(url: url)

        XCTAssertEqual(decoded.url.pathExtension, "rfmap")
        XCTAssertEqual(decoded.size.1, 1)
        XCTAssertEqual(decoded.size.2, 2)
        XCTAssertEqual(decoded.xPositions, [-3, 3])
        XCTAssertEqual(decoded.yPositions, [0])
        XCTAssertEqual(decoded.unitPool, [17])
        XCTAssertEqual(decoded.occupancyTimeSeconds, [[0.1, 0.2]])
    }

    func testMATLABSingletonXScalarAxisDecodesSymmetrically() throws {
        let payload = Data(#"""
        {
          "unitsSpikeCounts": [[[[1.0]], [[2.0]]]],
          "unitsSpikeCountsSize": [1, 2, 1, 1],
          "unitPool": [17],
          "xPositions": 0.0,
          "yPositions": [-3.0, 3.0],
          "timeBinEdges": [0.0, 0.1],
          "occupancyTimeSec": [0.1, 0.2],
          "occupancyTimeSecSize": [2, 1],
          "responseUnits": "spike_count",
          "responseNormalization": "none",
          "spikeCountDefinition": "each_qualifying_trial_contributes_once_per_final_spatial_bin",
          "occupancyTimeDefinition": "sum_of_qualifying_trial_durations_per_final_spatial_bin"
        }
        """#.utf8)

        let decoded = try RFMappingData(
            data: payload,
            url: URL(fileURLWithPath: "/tmp/matlab-singleton-x.rfmap")
        )

        XCTAssertEqual(decoded.xPositions, [0])
        XCTAssertEqual(decoded.yPositions, [-3, 3])
        XCTAssertEqual(decoded.occupancyTimeSeconds, [[0.1], [0.2]])
    }

    func testScalarSpatialAxisRequiresDeclaredSingletonDimension() {
        let payload = Data(#"""
        {
          "unitsSpikeCounts": [[[[1.0]], [[2.0]]]],
          "unitsSpikeCountsSize": [1, 2, 1, 1],
          "unitPool": [17],
          "xPositions": [0.0],
          "yPositions": 0.0,
          "timeBinEdges": [0.0, 0.1],
          "occupancyTimeSec": [0.1, 0.1],
          "occupancyTimeSecSize": [2, 1],
          "responseUnits": "spike_count",
          "responseNormalization": "none",
          "spikeCountDefinition": "each_qualifying_trial_contributes_once_per_final_spatial_bin",
          "occupancyTimeDefinition": "sum_of_qualifying_trial_durations_per_final_spatial_bin"
        }
        """#.utf8)

        XCTAssertThrowsError(try RFMappingData(
            data: payload,
            url: URL(fileURLWithPath: "/tmp/invalid-scalar-y.rfmap")
        )) { error in
            XCTAssertTrue(error.localizedDescription.contains("declared spatial dimension is 1"))
        }
    }

    func testBooleanSpatialAxisIsNotAcceptedAsNumericScalar() {
        let payload = Data(#"""
        {
          "unitsSpikeCounts": [[[[1.0]]]],
          "unitsSpikeCountsSize": [1, 1, 1, 1],
          "unitPool": [17],
          "xPositions": [0.0],
          "yPositions": true,
          "timeBinEdges": [0.0, 0.1],
          "occupancyTimeSec": 0.1,
          "occupancyTimeSecSize": [1, 1],
          "responseUnits": "spike_count",
          "responseNormalization": "none",
          "spikeCountDefinition": "each_qualifying_trial_contributes_once_per_final_spatial_bin",
          "occupancyTimeDefinition": "sum_of_qualifying_trial_durations_per_final_spatial_bin"
        }
        """#.utf8)

        XCTAssertThrowsError(try RFMappingData(
            data: payload,
            url: URL(fileURLWithPath: "/tmp/invalid-boolean-y.rfmap")
        )) { error in
            XCTAssertTrue(error.localizedDescription.contains("numeric scalar"))
        }
    }

    func testHDTuningJSONSchemaDecodesFromTCFile() throws {
        let root = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("tuning_curves.tc")
        let payload = strictHDTuningPayload(
            unitIDs: [17],
            zeroOccupancyBin: nil
        )
        try write(try JSONSerialization.data(withJSONObject: payload), to: url)

        let decoded = try HDTuningData(url: url)
        XCTAssertEqual(decoded.sourceURL.pathExtension, "tc")
        XCTAssertEqual(decoded.unitIDs, [17])
    }

    func testHDTuningDiscoveryPrefersTCAndFallsBackToJSON() throws {
        let root = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let session = root.appendingPathComponent("260630_3", isDirectory: true)
        let rfURL = session
            .appendingPathComponent("data/rfmapping/ProbeA", isDirectory: true)
            .appendingPathComponent("map.rfmap")
        try write("{}", to: rfURL)
        let tuningDirectory = session
            .appendingPathComponent("data/tuning_curves/ProbeA", isDirectory: true)
        let aliasURL = tuningDirectory.appendingPathComponent("tuning_curves.tc")
        let legacyURL = tuningDirectory.appendingPathComponent("tuning_curves.json")
        try write("{}", to: aliasURL)
        try write("{}", to: legacyURL)

        try assertSameFile(HDTuningDiscovery.discover(forRFURL: rfURL), aliasURL)
        try FileManager.default.removeItem(at: aliasURL)
        try assertSameFile(HDTuningDiscovery.discover(forRFURL: rfURL), legacyURL)
    }

    func testHDTuningDiscoveryUsesExplicitSessionWithoutFallback() throws {
        let root = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let currentSession = root.appendingPathComponent("260630_3", isDirectory: true)
        let rfURL = currentSession
            .appendingPathComponent("data/rfmapping/ProbeA", isDirectory: true)
            .appendingPathComponent("map.rfmap")
        try write("{}", to: rfURL)
        let firstSessionCurve = root
            .appendingPathComponent("260630_1/data/tuning_curves/ProbeA", isDirectory: true)
            .appendingPathComponent("tuning_curves.tc")
        let thirdSessionCurve = currentSession
            .appendingPathComponent("data/tuning_curves/ProbeA", isDirectory: true)
            .appendingPathComponent("tuning_curves.tc")
        try write("{}", to: firstSessionCurve)
        try write("{}", to: thirdSessionCurve)

        try assertSameFile(
            HDTuningDiscovery.discover(forRFURL: rfURL, sessionIndex: 1),
            firstSessionCurve
        )
        try assertSameFile(
            HDTuningDiscovery.discover(forRFURL: rfURL, sessionIndex: 3),
            thirdSessionCurve
        )
        XCTAssertNil(HDTuningDiscovery.discover(forRFURL: rfURL, sessionIndex: 2))
        XCTAssertNil(HDTuningDiscovery.discover(forRFURL: rfURL, sessionIndex: 0))
    }
}
