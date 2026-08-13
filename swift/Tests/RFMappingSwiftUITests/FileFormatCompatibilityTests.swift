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

    func testExtensionAliasesAndLegacyExtensionsRemainAccepted() {
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

    func testRFDiscoveryIncludesRFMapAndLegacyJSONOnly() throws {
        let root = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let rfmapURL = root.appendingPathComponent("new.rfmap")
        let jsonURL = root.appendingPathComponent("legacy.json")
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
        let payload: [String: Any] = [
            "unitsSpikeCounts": [[[[1.0]]]],
            "unitsSpikeCountsSize": [1, 1, 1, 1],
            "unitPool": [17],
            "xPositions": [10.0],
            "yPositions": [20.0],
            "timeBinEdges": [0.0, 0.1],
            "stimulusPresentationCounts": [[1.0]],
        ]
        try write(try JSONSerialization.data(withJSONObject: payload), to: url)

        let decoded = try RFMappingData(url: url)
        XCTAssertEqual(decoded.url.pathExtension, "rfmap")
        XCTAssertEqual(decoded.unitPool, [17])
        XCTAssertEqual(decoded.counts, [[[[1.0]]]])
    }

    func testHDTuningJSONSchemaDecodesFromTCFile() throws {
        let root = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("tuning_curves.tc")
        let payload: [String: Any] = [
            "metadata": [String: Any](),
            "angle_bin_edges_deg": (0...HDTuningData.rawBinCount).map { Double($0) * 2 },
            "occupancy_time_s": Array(repeating: 1.0, count: HDTuningData.rawBinCount),
            "unit_id": [17],
            "spike_counts": [Array(repeating: 2.0, count: HDTuningData.rawBinCount)],
            "firing_rate_hz": [Array(repeating: 2.0, count: HDTuningData.rawBinCount)],
            "unit_data": [String: Any](),
        ]
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
}
