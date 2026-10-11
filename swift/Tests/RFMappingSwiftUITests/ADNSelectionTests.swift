import CoreGraphics
import Foundation
import XCTest
@testable import RFMappingSwiftUI

final class ADNSelectionTests: XCTestCase {
    private func selectionURL() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        addTeardownBlock { try FileManager.default.removeItem(at: root) }
        return root.appendingPathComponent("adn_neuron.npy")
    }

    private func numpyFixture(_ version: String) throws -> Data {
        let url = try XCTUnwrap(Bundle.module.url(
            forResource: "adn-numpy", withExtension: "json", subdirectory: "Fixtures"
        ))
        let fixtures = try JSONDecoder().decode([String: String].self, from: Data(contentsOf: url))
        return try XCTUnwrap(Data(base64Encoded: XCTUnwrap(fixtures[version])))
    }

    private func geometry(_ units: [(Int, Double?, Double?)]) -> ProbeGeometry {
        ProbeGeometry(
            probeName: "ProbeA", positionsURL: URL(fileURLWithPath: "/positions.csv"),
            channelsURL: nil, channels: [],
            units: units.map { ProbeUnitPosition(unitID: $0.0, xMicrometers: $0.1, yMicrometers: $0.2) }
        )
    }

    func testSelectionPathUsesNearestRFRecordingSession() {
        let source = URL(fileURLWithPath: "/recordings/m21/261006/261006_3/data/rfmapping/ProbeA/regular.rfmap")
        XCTAssertEqual(
            ADNSelection.selectionURL(forRFURL: source)?.path,
            "/recordings/m21/261006/261006_3/data/adn_neuron.npy"
        )
        XCTAssertNil(ADNSelection.selectionURL(forRFURL: URL(fileURLWithPath: "/exports/regular_261006_3_A.rfmap")))
    }

    func testPolygonIncludesBoundariesAndDeduplicatesIDsInEitherDirection() {
        let probe = geometry([(42, 5, 5), (8, 0, 5), (100, 10, 10), (17, 10.01, 5), (42, 5, 5)])
        let polygon = [CGPoint(x: 0, y: 0), CGPoint(x: 10, y: 0), CGPoint(x: 10, y: 10), CGPoint(x: 0, y: 10)]
        XCTAssertEqual(ADNSelection.unitIDs(in: polygon, geometry: probe), [42, 8, 100])
        XCTAssertEqual(ADNSelection.unitIDs(in: Array(polygon.reversed()), geometry: probe), [42, 8, 100])
        XCTAssertEqual(ADNSelection.unitIDs(in: polygon + Array(polygon.prefix(1)), geometry: probe), [42, 8, 100])
    }

    func testConcavePolygonSkipsMissingAndNonfinitePositions() {
        let probe = geometry([(1, 1, 1), (2, 3, 3), (3, 1, 3), (4, nil, nil), (5, .nan, .nan), (6, .infinity, 1)])
        let polygon = [(0, 0), (4, 0), (4, 2), (2, 2), (2, 4), (0, 4)].map {
            CGPoint(x: CGFloat($0.0), y: CGFloat($0.1))
        }
        XCTAssertEqual(ADNSelection.unitIDs(in: polygon, geometry: probe), [1, 3])
        XCTAssertTrue(ADNSelection.unitIDs(in: [], geometry: probe).isEmpty)
        XCTAssertTrue(ADNSelection.unitIDs(in: [CGPoint(x: CGFloat.nan, y: 0)] + polygon, geometry: probe).isEmpty)
    }

    func testSlantedBoundaryExcludesExtensionBeyondEdge() {
        let probe = geometry([(1, 5, 5), (2, 10, 0), (3, 10.01, -0.01), (4, 5, 5.01)])
        XCTAssertEqual(
            ADNSelection.unitIDs(in: [CGPoint(x: 0, y: 0), CGPoint(x: 10, y: 0), CGPoint(x: 0, y: 10)], geometry: probe),
            [1, 2]
        )
    }

    func testReadsPythonNumPyFilesAcrossHeaderVersions() throws {
        let url = try selectionURL()
        for version in ["1", "2", "3"] {
            try numpyFixture(version).write(to: url)
            XCTAssertEqual(try ADNSelection.load(from: url, probeName: "ProbeA"), [17, 42, 1_099_511_627_783])
            XCTAssertEqual(try ADNSelection.load(from: url, probeName: "ProbeB"), [42, -7])
        }
        try numpyFixture("empty").write(to: url)
        XCTAssertTrue(try ADNSelection.load(from: url, probeName: "ProbeA").isEmpty)
    }

    func testSavingReplacesOnlyCurrentProbeAndProducesNumPyBytes() throws {
        let url = try selectionURL()
        XCTAssertTrue(try ADNSelection.load(from: url, probeName: "ProbeA").isEmpty)
        let fixture = try numpyFixture("1")
        try fixture.write(to: url)
        try ADNSelection.save([42, 17, 1_099_511_627_783], probeName: "ProbeA", to: url)
        let saved = try Data(contentsOf: url)
        // NumPy's header and row bytes, reordered as other probes then sorted current IDs.
        var expected = fixture.prefix(128)
        for row in [0, 4, 1, 2, 3] {
            expected.append(fixture.subdata(in: (128 + row * 72)..<(128 + (row + 1) * 72)))
        }
        XCTAssertEqual(saved, expected)
        try ADNSelection.save([99], probeName: "ProbeA", to: url)
        XCTAssertEqual(try ADNSelection.load(from: url, probeName: "ProbeA"), [99])
        XCTAssertEqual(try ADNSelection.load(from: url, probeName: "ProbeB"), [42, -7])
        try ADNSelection.save([], probeName: "ProbeA", to: url)
        XCTAssertTrue(try ADNSelection.load(from: url, probeName: "ProbeA").isEmpty)
        XCTAssertEqual(try ADNSelection.load(from: url, probeName: "ProbeB"), [42, -7])
        try ADNSelection.save([], probeName: "ProbeB", to: url)
        XCTAssertEqual(try Data(contentsOf: url), try numpyFixture("empty"))
    }

    func testUTF32ProbeNamesAndNestedOutputDirectory() throws {
        let url = try selectionURL().deletingLastPathComponent().appendingPathComponent("261006_3/data/adn_neuron.npy")
        try ADNSelection.save([17], probeName: "Probe🐭", to: url)
        try ADNSelection.save([42], probeName: "ProbeB", to: url)
        XCTAssertEqual(try ADNSelection.load(from: url, probeName: "Probe🐭"), [17])
        XCTAssertEqual(try ADNSelection.load(from: url, probeName: "ProbeB"), [42])
    }

    func testInvalidOrTruncatedSchemaIsRejectedWithoutOverwriting() throws {
        let url = try selectionURL()
        let fixture = try numpyFixture("1")
        var wrongDtype = fixture
        let descriptor = try XCTUnwrap(wrongDtype.range(of: Data("<U16".utf8)))
        wrongDtype.replaceSubrange(descriptor, with: Data("<U15".utf8))
        for invalid in [Data("not numpy".utf8), fixture.prefix(11), fixture.dropLast(), wrongDtype] {
            try invalid.write(to: url)
            XCTAssertThrowsError(try ADNSelection.load(from: url, probeName: "ProbeA"))
            XCTAssertThrowsError(try ADNSelection.save([99], probeName: "ProbeA", to: url))
            XCTAssertEqual(try Data(contentsOf: url), invalid)
        }
    }

    func testFullGeometryIncludesUnitsAbsentFromRFData() throws {
        let positions = try selectionURL().deletingLastPathComponent().appendingPathComponent("positions.probe")
        try Data("unit_index,unit_id,x_um,y_um\n0,17,7.5,120\n1,99,20,130\n2,42,nan,nan\n".utf8).write(to: positions)
        let paths = ProbeGeometryPaths(probeName: "ProbeA", positionsURL: positions, channelsURL: nil)
        XCTAssertEqual(try ProbeGeometryDiscovery.load(paths).units.map(\.unitID), [17, 99, 42])
        XCTAssertEqual(try ProbeGeometryDiscovery.load(paths, rfUnitIDs: [17]).units.map(\.unitID), [17])
        XCTAssertThrowsError(try ProbeGeometryDiscovery.load(paths, rfUnitIDs: []))
    }
}
