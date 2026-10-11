import Foundation
import XCTest
@testable import RFMappingSwiftUI

@MainActor
final class ADNFilterTests: XCTestCase {
    private func makeStore(session: URL? = nil, probe: String = "ProbeA") throws -> RFMappingStore {
        let session = session ?? FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
            .appendingPathComponent("261006_3")
        let directory = session.appendingPathComponent("data/rfmapping/\(probe)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let url = directory.appendingPathComponent("regular.rfmap")
        let payload = currentRFSchemaPayload([
            "unitsSpikeCounts": [
                [[[2.0, 2.0], [0.0, 0.0]]],
                [[[3.0, 3.0], [4.0, 4.0]]],
            ],
            "unitsSpikeCountsSize": [2, 1, 2, 2],
            "unitPool": [8, 7],
            "xPositions": [-1.0, 1.0],
            "yPositions": [0.0],
            "timeBinEdges": [0.0, 0.1, 0.2],
        ], occupancyTimeSec: [0.2, 0.2], occupancyTimeSecSize: [1, 2])
        try JSONSerialization.data(withJSONObject: payload).write(to: url)
        let suite = "ADNFilterTests.\(UUID().uuidString)"
        let preferences = UserDefaults(suiteName: suite)!
        addTeardownBlock {
            preferences.removePersistentDomain(forName: suite)
            try? FileManager.default.removeItem(at: session.deletingLastPathComponent())
        }
        return RFMappingStore(
            initialData: try RFMappingData(url: url),
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false,
            unitQualityFilterEnabled: false,
            preferences: preferences
        )
    }

    func testADNIntersectsQualityAndFreezesExportSelection() throws {
        let store = try makeStore()
        try store.saveADNSelection([8, 7, 99])
        store.setOnlyADN(true)
        XCTAssertEqual(store.localVisibleUnitIDs, [8, 7])
        store.setRFUnitQualityFilterEnabled(true)
        XCTAssertEqual(store.localVisibleUnitIDs, [7])
        XCTAssertEqual(store.navigationUnitIDs, [7])
        XCTAssertEqual(store.selectedUnitID, 7)
        XCTAssertEqual(store.adnUnitIDs, [8, 7, 99])

        let registry = FigureExportWindowRegistry()
        let request = try XCTUnwrap(registry.prepare(from: store))
        let seed = try XCTUnwrap(registry.seed(for: request))
        XCTAssertEqual(seed.unitPool, [7])
        XCTAssertEqual(seed.unitQualityFilter?.visibleUnitIDs, [7])
        XCTAssertEqual(seed.unitQualityFilter?.excludedUnitIDs, [8])
        XCTAssertEqual(seed.adnFilter, ADNFilterSnapshot(enabled: true, probe: "ProbeA", unitIDs: [7, 8, 99]))
        let workspace = FigureExportWorkspace(seed: seed)
        XCTAssertEqual(workspace.configuration.adnFilter, seed.adnFilter)
        try store.saveADNSelection([8])
        XCTAssertEqual(seed.unitPool, [7])
        XCTAssertEqual(seed.adnFilter?.unitIDs, [7, 8, 99])
        XCTAssertTrue(store.localVisibleUnitIDs.isEmpty)
        XCTAssertNil(registry.prepare(from: store))
    }

    func testMissingSelectionIsEmptyAndSourceChangeResetsADN() throws {
        let store = try makeStore()
        store.setOnlyADN(true)
        XCTAssertTrue(store.onlyADN)
        XCTAssertTrue(store.navigationUnitIDs.isEmpty)
        XCTAssertNil(store.selectedUnitID)
        XCTAssertEqual(store.unitUnavailableReason, .noADNVisibleUnits)
        let replacement = try makeStore()
        XCTAssertTrue(store.loadJSON(try XCTUnwrap(replacement.data?.url)))
        XCTAssertFalse(store.onlyADN)
        XCTAssertTrue(store.adnUnitIDs.isEmpty)
        XCTAssertEqual(store.navigationUnitIDs, [8, 7])
    }

    func testAnnotationLoadsFullGeometryIncludingUnitsWithoutRF() throws {
        let store = try makeStore()
        let positions = try XCTUnwrap(store.adnSelectionURL).deletingLastPathComponent()
            .appendingPathComponent("spike_position/ProbeA/positions.csv")
        try FileManager.default.createDirectory(
            at: positions.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        try "unit_index,unit_id,x_um,y_um\n0,8,300,300\n1,7,7.5,120\n2,99,20,130\n"
            .write(to: positions, atomically: true, encoding: .utf8)
        store.setProbePositionsURL(positions)
        XCTAssertEqual(store.probeGeometry?.units.map(\.unitID), [8, 7])
        XCTAssertEqual(try store.loadADNGeometry().units.map(\.unitID), [8, 7, 99])
        try store.saveADNSelection([99])
        XCTAssertEqual(try ADNSelection.load(
            from: XCTUnwrap(store.adnSelectionURL), probeName: "ProbeA"
        ), [99])
    }

    func testPairedNonADNSelectionStaysUnavailableAndLocalNavigationStaysADN() throws {
        let first = try makeStore()
        let second = try makeStore()
        try first.saveADNSelection([7])
        first.setOnlyADN(true)
        let coordinator = WindowPairingCoordinator()
        let firstID = UUID(), secondID = UUID()
        coordinator.register(first, id: firstID)
        coordinator.register(second, id: secondID)
        coordinator.setPairingEnabled(true, sourceID: firstID)
        second.selectUnitID(8)
        coordinator.synchronizedStateDidChange(second.viewerSyncState, from: secondID)
        XCTAssertEqual(first.selectedUnitID, 8)
        XCTAssertEqual(first.unitIndex, -1)
        XCTAssertEqual(first.unitUnavailableReason, .adnFiltered(unitID: 8))
        XCTAssertEqual(first.navigationUnitIDs, [7])
        XCTAssertTrue(first.onlyADN)
        XCTAssertFalse(second.onlyADN)
        XCTAssertEqual(second.navigationUnitIDs, [7, 8])
        first.normalizeControls()
        XCTAssertEqual(first.selectedUnitID, 8)
        XCTAssertFalse(first.hasSelectedUnit)
    }

    func testSaveUpdatesOnlyMatchingSessionAndProbeWindows() throws {
        let source = try makeStore()
        let session = try XCTUnwrap(source.adnSelectionURL)
            .deletingLastPathComponent().deletingLastPathComponent()
        let same = try makeStore(session: session)
        let otherProbe = try makeStore(session: session, probe: "ProbeB")
        let otherSession = try makeStore()
        let coordinator = WindowPairingCoordinator()
        for store in [source, same, otherProbe, otherSession] {
            coordinator.register(store, id: UUID())
        }
        same.setOnlyADN(true)
        otherSession.setOnlyADN(true)
        try otherProbe.saveADNSelection([8])
        otherProbe.setOnlyADN(true)
        try source.saveADNSelection([7, 99])
        XCTAssertEqual(same.adnUnitIDs, [7, 99])
        XCTAssertEqual(otherProbe.adnUnitIDs, [8])
        XCTAssertEqual(otherSession.adnUnitIDs, [])
        XCTAssertTrue(same.onlyADN)
        XCTAssertEqual(same.navigationUnitIDs, [7])
        XCTAssertEqual(same.selectedUnitID, 7)
        XCTAssertTrue(otherProbe.onlyADN)
        XCTAssertEqual(otherProbe.navigationUnitIDs, [8])
        XCTAssertTrue(otherSession.onlyADN)
        XCTAssertTrue(otherSession.navigationUnitIDs.isEmpty)
        XCTAssertEqual(try ADNSelection.load(
            from: XCTUnwrap(source.adnSelectionURL), probeName: "ProbeB"
        ), [8])
    }

    func testOnlyADNHidesPairedHDCompanionAndDisablingRestoresCurve() throws {
        let store = try makeStore()
        let hdURL = try XCTUnwrap(store.adnSelectionURL).deletingLastPathComponent()
            .appendingPathComponent("tuning_curves.json")
        try JSONSerialization.data(withJSONObject: strictHDTuningPayload(
            unitIDs: [7, 8], zeroOccupancyBin: nil
        )).write(to: hdURL)
        store.setHDTuningURL(hdURL)
        XCTAssertNotNil(store.selectedHDTuningCurve)
        try store.saveADNSelection([7])
        store.setOnlyADN(true)
        store.setPairedUnitIDs([7, 8])
        store.selectUnitID(8)
        XCTAssertEqual(store.selectedUnitID, 8)
        XCTAssertNil(store.selectedHDTuningCurve)
        store.setOnlyADN(false)
        XCTAssertEqual(store.selectedUnitID, 8)
        XCTAssertNotNil(store.selectedHDTuningCurve)
    }

    func testExportManifestKeepsADNAndQualityFiltersSeparate() async throws {
        let store = try makeStore()
        try store.saveADNSelection([7, 99])
        store.setOnlyADN(true)
        let registry = FigureExportWindowRegistry()
        let request = try XCTUnwrap(registry.prepare(from: store))
        let workspace = FigureExportWorkspace(seed: try XCTUnwrap(registry.seed(for: request)))
        workspace.format = .png
        workspace.outputScale = 1
        workspace.destinationDirectory = try XCTUnwrap(store.adnSelectionURL).deletingLastPathComponent()
        let result = try await FigureExportRenderer().export(
            configuration: workspace.configuration,
            data: try XCTUnwrap(store.data),
            companions: workspace.companions
        )
        let manifest = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(
            contentsOf: result.outputURL.appendingPathComponent("manifest.json")
        )) as? [String: Any])
        let provenance = try XCTUnwrap(manifest["provenance"] as? [String: Any])
        let display = try XCTUnwrap(provenance["display"] as? [String: Any])
        let adn = try XCTUnwrap(display["adnFilter"] as? [String: Any])
        let quality = try XCTUnwrap(display["unitQualityFilter"] as? [String: Any])
        XCTAssertEqual(adn["enabled"] as? Bool, true)
        XCTAssertEqual(adn["probe"] as? String, "ProbeA")
        XCTAssertEqual(adn["unitIDs"] as? [Int], [7, 99])
        XCTAssertEqual(quality["visibleUnitIDs"] as? [Int], [8, 7])
        let selection = try XCTUnwrap(provenance["selection"] as? [String: Any])
        XCTAssertEqual(selection["selectedUnitIDs"] as? [Int], [7])
    }
}
