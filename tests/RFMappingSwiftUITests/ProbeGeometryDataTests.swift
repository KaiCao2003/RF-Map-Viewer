import Foundation
import XCTest
@testable import RFMappingSwiftUI

final class ProbeGeometryDataTests: XCTestCase {
    private func temporaryRoot() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: false
        )
        return root
    }

    private func write(_ text: String, to url: URL) throws {
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try Data(text.utf8).write(to: url)
    }

    func testDiscoversSessionBoundGeometryAndLoadsOnlyRFUnits() throws {
        let root = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let session = root.appendingPathComponent("260630_3", isDirectory: true)
        let dataRoot = session.appendingPathComponent("data", isDirectory: true)
        let rfURL = dataRoot
            .appendingPathComponent("rfmapping/good/-100_400_1ms/ProbeA", isDirectory: true)
            .appendingPathComponent("unitsSpikeCounts.json")
        try write("{}", to: rfURL)
        let positionsURL = dataRoot
            .appendingPathComponent("spike_position/ProbeA", isDirectory: true)
            .appendingPathComponent("positions.csv")
        try write(
            "unit_index,unit_id,x_um,y_um\r\n"
                + "0,17,7.5,120.0\r\n"
                + "1,999,999.0,999.0\r\n"
                + "2,42,15.0,240.0\r\n",
            to: positionsURL
        )
        let channelsURL = dataRoot
            .appendingPathComponent("waveform/ProbeA", isDirectory: true)
            .appendingPathComponent("channels.csv")
        try write(
            "channel_index,channel_id,raw_channel_index,x_um,y_um,shank_id\n"
                + "0,10,0,0.0,0.0,0\n"
                + "1,11,1,20.0,20.0,0\n",
            to: channelsURL
        )

        let paths = try XCTUnwrap(ProbeGeometryDiscovery.discover(forRFURL: rfURL))
        XCTAssertEqual(paths.probeName, "ProbeA")
        XCTAssertEqual(paths.positionsURL, positionsURL.resolvingSymlinksInPath())
        XCTAssertEqual(paths.channelsURL, channelsURL.resolvingSymlinksInPath())
        let geometry = try ProbeGeometryDiscovery.load(paths, rfUnitIDs: [17, 42])

        XCTAssertEqual(geometry.units.map(\.unitID), [17, 42])
        XCTAssertEqual(geometry.channels.map(\.channelID), [10, 11])
        XCTAssertEqual(geometry.channelsURL, channelsURL.resolvingSymlinksInPath())
    }

    func testSessionDiscoveryDoesNotCrossDataBoundary() throws {
        let root = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let dateRoot = root.appendingPathComponent("260630", isDirectory: true)
        let rfURL = dateRoot
            .appendingPathComponent(
                "260630_3/data/rfmapping/good/-100_400_1ms/ProbeA",
                isDirectory: true
            )
            .appendingPathComponent("unitsSpikeCounts.json")
        try write("{}", to: rfURL)
        try write(
            "unit_index,unit_id,x_um,y_um\n0,17,999.0,999.0\n",
            to: dateRoot.appendingPathComponent("positions.csv")
        )

        XCTAssertNil(ProbeGeometryDiscovery.discover(forRFURL: rfURL))
    }

    func testResolvedCSVTargetCannotEscapeSessionBoundary() throws {
        let root = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let dataRoot = root
            .appendingPathComponent("260630_3/data", isDirectory: true)
        let rfURL = dataRoot
            .appendingPathComponent("rfmapping/ProbeA", isDirectory: true)
            .appendingPathComponent("unitsSpikeCounts.json")
        try write("{}", to: rfURL)
        let outside = root.appendingPathComponent("outside/positions.csv")
        try write(
            "unit_index,unit_id,x_um,y_um\n0,17,7.5,120.0\n",
            to: outside
        )
        let linked = dataRoot
            .appendingPathComponent("spike_position/ProbeA", isDirectory: true)
            .appendingPathComponent("positions.csv")
        try FileManager.default.createDirectory(
            at: linked.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try FileManager.default.createSymbolicLink(
            at: linked,
            withDestinationURL: outside
        )

        XCTAssertNil(ProbeGeometryDiscovery.discover(forRFURL: rfURL))
    }

    func testLegacyDiscoveryIsAdjacentOnly() throws {
        let root = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let rfDirectory = root.appendingPathComponent("legacy/ProbeA", isDirectory: true)
        let rfURL = rfDirectory.appendingPathComponent("rf.json")
        try write("{}", to: rfURL)
        let adjacent = rfDirectory.appendingPathComponent("positions.csv")
        try write(
            "unit_index,unit_id,x_um,y_um\n0,17,7.5,120.0\n",
            to: adjacent
        )
        // This plausible but unrelated file must never win over the adjacent
        // bounded legacy layout or be discovered after it is removed.
        try write(
            "unit_index,unit_id,x_um,y_um\n0,999,9,9\n",
            to: root.appendingPathComponent("positions.csv")
        )

        let found = try XCTUnwrap(ProbeGeometryDiscovery.discover(forRFURL: rfURL))
        XCTAssertEqual(found.positionsURL, adjacent.resolvingSymlinksInPath())
        try FileManager.default.removeItem(at: adjacent)
        XCTAssertNil(ProbeGeometryDiscovery.discover(forRFURL: rfURL))
    }

    func testNoUnitOverlapFailsAndMalformedOptionalChannelsAreIgnored() throws {
        let root = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let positionsURL = root.appendingPathComponent("positions.csv")
        let channelsURL = root.appendingPathComponent("channels.csv")
        try write(
            "unit_index,unit_id,x_um,y_um\n0,17,7.5,120.0\n",
            to: positionsURL
        )
        try write("not,a,channel,file\n", to: channelsURL)
        let paths = ProbeGeometryPaths(
            probeName: "ProbeA",
            positionsURL: positionsURL,
            channelsURL: channelsURL
        )

        XCTAssertThrowsError(try ProbeGeometryDiscovery.load(paths, rfUnitIDs: [42])) {
            XCTAssertEqual($0 as? ProbeGeometryError, .noRFUnits)
        }
        let geometry = try ProbeGeometryDiscovery.load(paths, rfUnitIDs: [17])
        XCTAssertEqual(geometry.units.map(\.unitID), [17])
        XCTAssertTrue(geometry.channels.isEmpty)
        XCTAssertNil(geometry.channelsURL)
    }
}
