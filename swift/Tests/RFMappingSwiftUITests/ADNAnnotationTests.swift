import CoreGraphics
import Foundation
import XCTest
@testable import RFMappingSwiftUI

final class ADNAnnotationTests: XCTestCase {
    private var geometry: ProbeGeometry {
        ProbeGeometry(
            probeName: "ProbeA", positionsURL: URL(fileURLWithPath: "/positions.probe"),
            channelsURL: nil, channels: [],
            units: [
                ProbeUnitPosition(unitID: 10, xMicrometers: 1, yMicrometers: 1),
                ProbeUnitPosition(unitID: 11, xMicrometers: 2, yMicrometers: 2),
                ProbeUnitPosition(unitID: 12, xMicrometers: 3, yMicrometers: 3),
                ProbeUnitPosition(unitID: 13, xMicrometers: 4, yMicrometers: 4),
                ProbeUnitPosition(unitID: 99, xMicrometers: 5, yMicrometers: 5),
                ProbeUnitPosition(unitID: 100, xMicrometers: nil, yMicrometers: nil),
            ]
        )
    }

    private var polygon: [CGPoint] {
        [CGPoint(x: 0, y: 0), CGPoint(x: 6, y: 0), CGPoint(x: 6, y: 6), CGPoint(x: 0, y: 6)]
    }

    func testClassesIncludeUnknownAndKeepProbeUnitsOutsideRFPopulation() {
        let filter = ADNAnnotationFilter(hdClasses: [10: 0, 11: 1, 12: 2, 13: 3])
        XCTAssertEqual(ADNHDClass.allCases.map(\.title), ["Class 0", "Class 1", "Class 2", "Class 3", "Unknown"])
        XCTAssertEqual(ADNHDClass(nil), .unknown)
        XCTAssertEqual(filter.visibleUnitIDs(in: geometry), [10, 11, 12, 13, 99])
    }

    func testHiddenClassesCannotBeAddedByLassoAndExistingSelectionsRemain() {
        var filter = ADNAnnotationFilter(hdClasses: [10: 0, 11: 1, 12: 2, 13: 3])
        filter.visibleClasses.remove(.one)
        filter.visibleClasses.remove(.unknown)
        XCTAssertEqual(filter.visibleUnitIDs(in: geometry), [10, 12, 13])
        XCTAssertEqual(filter.addingUnits(in: polygon, geometry: geometry, to: [11, 200]), [10, 11, 12, 13, 200])
        XCTAssertFalse(filter.addingUnits(in: polygon, geometry: geometry, to: []).contains(99))
        filter.visibleClasses.insert(.unknown)
        XCTAssertTrue(filter.addingUnits(in: polygon, geometry: geometry, to: []).contains(99))
    }

    func testHidingEveryClassPreservesSavedSelectionAndAddsNothing() {
        var filter = ADNAnnotationFilter(hdClasses: [10: 0, 11: 1, 12: 2, 13: 3])
        filter.visibleClasses = []
        XCTAssertTrue(filter.visibleUnitIDs(in: geometry).isEmpty)
        XCTAssertEqual(filter.addingUnits(in: polygon, geometry: geometry, to: [11, 99]), [11, 99])
    }
}

@MainActor
final class ADNAnnotationCompanionTests: XCTestCase {
    func testUtilityDiscoversFullGeometryAndHDClassesWithCompanionsHidden() throws {
        let store = try companionStore()
        store.displayDefaults.showProbe = false
        store.displayDefaults.showTuning = false
        store.displayDefaults.autoLoadProbe = false
        store.displayDefaults.autoLoadTuning = false
        XCTAssertNil(store.probeGeometry)
        XCTAssertNil(store.hdTuning)
        XCTAssertEqual(try store.loadADNGeometry().units.map(\.unitID), [7, 99])
        XCTAssertEqual(store.annotationHDClasses(), [7: 1, 99: 3])
        XCTAssertEqual(store.data?.unitPool, [7])
        XCTAssertFalse(store.displayDefaults.showProbe)
        XCTAssertFalse(store.displayDefaults.showTuning)
    }

    func testPreparedDocumentPublishesRFBeforeDeferredCompanions() async throws {
        let store = try companionStore(deferCompanionLoading: true)
        defer { store.cancelPendingLoads() }
        XCTAssertEqual(store.data?.unitPool, [7])
        XCTAssertNil(store.hdTuning)
        XCTAssertNil(store.probeGeometry)
        let deadline = ContinuousClock.now.advanced(by: .seconds(3))
        while (store.hdTuning == nil || store.isLoadingData), ContinuousClock.now < deadline {
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTAssertEqual(store.hdTuning?.unitIDs, [7, 99])
        XCTAssertEqual(store.probeGeometry?.units.map(\.unitID), [7])
        XCTAssertFalse(store.isLoadingData)
        XCTAssertNil(store.loadingStage)
    }

    private func companionStore(deferCompanionLoading: Bool = false) throws -> RFMappingStore {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let dataRoot = root.appendingPathComponent("261006_1/data")
        let rfDirectory = dataRoot.appendingPathComponent("rfmapping/ProbeA")
        try FileManager.default.createDirectory(at: rfDirectory, withIntermediateDirectories: true)
        addTeardownBlock { try FileManager.default.removeItem(at: root) }
        let rfURL = rfDirectory.appendingPathComponent("regular.rfmap")
        let rf = currentRFSchemaPayload([
            "unitsSpikeCounts": [[[[2.0, 3.0]]]],
            "unitsSpikeCountsSize": [1, 1, 1, 2], "unitPool": [7],
            "xPositions": [0.0], "yPositions": [0.0], "timeBinEdges": [0.0, 0.1, 0.2],
        ], occupancyTimeSec: [0.2], occupancyTimeSecSize: [1, 1])
        try JSONSerialization.data(withJSONObject: rf).write(to: rfURL)
        let positionsURL = dataRoot.appendingPathComponent("spike_position/ProbeA/positions.probe")
        try FileManager.default.createDirectory(at: positionsURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try "unit_index,unit_id,x_um,y_um\n0,7,0,100\n1,99,20,120\n"
            .write(to: positionsURL, atomically: true, encoding: .utf8)
        let tuningURL = dataRoot.appendingPathComponent("tuning_curves/ProbeA/tuning_curves.tc")
        try FileManager.default.createDirectory(at: tuningURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        var hd = strictHDTuningPayload(unitIDs: [7, 99], zeroOccupancyBin: nil)
        var unitData = try XCTUnwrap(hd["unit_data"] as? [String: [Any]])
        unitData["hd_class"] = [1, 3]
        hd["unit_data"] = unitData
        try JSONSerialization.data(withJSONObject: hd).write(to: tuningURL)
        let suite = "ADNAnnotationCompanionTests.\(UUID().uuidString)"
        let preferences = try XCTUnwrap(UserDefaults(suiteName: suite))
        addTeardownBlock { preferences.removePersistentDomain(forName: suite) }
        return RFMappingStore(
            initialData: try RFMappingData(url: rfURL), loadDefault: false,
            discoverJSONChoices: false, discoverCompanions: deferCompanionLoading,
            deferCompanionLoading: deferCompanionLoading,
            unitQualityFilterEnabled: false, preferences: preferences
        )
    }
}
