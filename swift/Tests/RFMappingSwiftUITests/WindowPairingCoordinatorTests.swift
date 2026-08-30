import Foundation
import XCTest
@testable import RFMappingSwiftUI

@MainActor
final class WindowPairingCoordinatorTests: XCTestCase {
    private func makeData(unitIDs: [Int], name: String) throws -> RFMappingData {
        let counts = unitIDs.enumerated().map { index, _ in
            [[[Double(index + 1), Double(index + 2)]]]
        }
        let object = currentRFSchemaPayload([
            "unitsSpikeCounts": counts,
            "unitsSpikeCountsSize": [unitIDs.count, 1, 1, 2],
            "unitPool": unitIDs,
            "xPositions": [0.0],
            "yPositions": [0.0],
            "timeBinEdges": [0.0, 0.1, 0.2],
        ], occupancyTimeSec: 0.2, occupancyTimeSecSize: [1, 1])
        return try RFMappingData(
            data: JSONSerialization.data(withJSONObject: object),
            url: URL(fileURLWithPath: "/tmp/\(name).json")
        )
    }

    private func makeStore(unitIDs: [Int], name: String) throws -> RFMappingStore {
        RFMappingStore(
            initialData: try makeData(unitIDs: unitIDs, name: name),
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false,
            unitQualityFilterEnabled: true,
            zeroSpikeBinThreshold: 1
        )
    }

    private func makeQualityData(
        unitIDs: [Int],
        spatialCounts: [[Double]],
        name: String
    ) throws -> RFMappingData {
        let counts = spatialCounts.map { unit in
            [unit.map { [$0] }]
        }
        let object = currentRFSchemaPayload([
            "unitsSpikeCounts": counts,
            "unitsSpikeCountsSize": [unitIDs.count, 1, 2, 1],
            "unitPool": unitIDs,
            "xPositions": [-1.0, 1.0],
            "yPositions": [0.0],
            "timeBinEdges": [0.0, 0.1],
        ], occupancyTimeSec: [0.1, 0.1], occupancyTimeSecSize: [1, 2])
        return try RFMappingData(
            data: JSONSerialization.data(withJSONObject: object),
            url: URL(fileURLWithPath: "/tmp/\(name).json")
        )
    }

    private func makeWindowQualityData(name: String) throws -> RFMappingData {
        let object = currentRFSchemaPayload([
            "unitsSpikeCounts": [
                [[[1.0, 0.0]]],
                [[[0.0, 1.0]]],
            ],
            "unitsSpikeCountsSize": [2, 1, 1, 2],
            "unitPool": [7, 8],
            "xPositions": [0.0],
            "yPositions": [0.0],
            "timeBinEdges": [0.0, 0.1, 0.2],
        ], occupancyTimeSec: 0.2, occupancyTimeSecSize: [1, 1])
        return try RFMappingData(
            data: JSONSerialization.data(withJSONObject: object),
            url: URL(fileURLWithPath: "/tmp/\(name).json")
        )
    }

    func testPairingNavigatesSortedUnionAndUsesExplicitNAState() throws {
        let first = try makeStore(unitIDs: [5, 9], name: "first")
        let second = try makeStore(unitIDs: [3, 9, 12], name: "second")
        let coordinator = WindowPairingCoordinator()
        let firstID = UUID()
        let secondID = UUID()
        coordinator.register(first, id: firstID)
        coordinator.register(second, id: secondID)

        XCTAssertTrue(coordinator.eligibility.canEnable)
        coordinator.setPairingEnabled(true, sourceID: firstID)
        XCTAssertEqual(first.navigationUnitIDs, [3, 5, 9, 12])
        XCTAssertEqual(second.navigationUnitIDs, [3, 5, 9, 12])
        XCTAssertEqual(first.selectedUnitID, 5)
        XCTAssertEqual(second.selectedUnitID, 5)
        XCTAssertEqual(second.unitIndex, -1)
        XCTAssertFalse(second.hasSelectedUnit)
        XCTAssertTrue(second.headerTitle.contains("N/A"))
        XCTAssertNil(second.selectedRFMap)
        XCTAssertTrue(second.currentMatrix().isEmpty)
        XCTAssertTrue(second.currentHeatmapPlot().matrix.isEmpty)
        XCTAssertTrue(second.timelineSnapshot().matrices.isEmpty)
        XCTAssertEqual(second.exportCSV(), "")

        first.stepUnit(1)
        coordinator.synchronizedStateDidChange(first.viewerSyncState, from: firstID)
        XCTAssertEqual(first.selectedUnitID, 9)
        XCTAssertEqual(first.unitIndex, 1)
        XCTAssertEqual(second.selectedUnitID, 9)
        XCTAssertEqual(second.unitIndex, 1)
        XCTAssertTrue(second.hasSelectedUnit)

        first.stepUnit(-2)
        coordinator.synchronizedStateDidChange(first.viewerSyncState, from: firstID)
        XCTAssertEqual(first.selectedUnitID, 3)
        XCTAssertEqual(first.unitIndex, -1)
        XCTAssertEqual(second.selectedUnitID, 3)
        XCTAssertEqual(second.unitIndex, 0)

        coordinator.setPairingEnabled(false, sourceID: firstID)
        XCTAssertNil(first.pairedUnitIDs)
        XCTAssertEqual(first.selectedUnitID, 5)
        XCTAssertEqual(first.unitIndex, 0)
        XCTAssertTrue(first.hasSelectedUnit)
    }

    func testPairingWorksWithoutIntersectionAndMapsIDNotArrayPosition() throws {
        let first = try makeStore(unitIDs: [1, 0], name: "collision-a")
        let second = try makeStore(unitIDs: [0, 2], name: "collision-b")
        let coordinator = WindowPairingCoordinator()
        let firstID = UUID()
        let secondID = UUID()
        coordinator.register(first, id: firstID)
        coordinator.register(second, id: secondID)
        coordinator.setPairingEnabled(true, sourceID: firstID)

        first.selectUnitID(0)
        coordinator.synchronizedStateDidChange(first.viewerSyncState, from: firstID)
        XCTAssertEqual(first.unitIndex, 1)
        XCTAssertEqual(second.unitIndex, 0)
        XCTAssertEqual(first.selectedUnitID, second.selectedUnitID)

        coordinator.setPairingEnabled(false, sourceID: firstID)

        let disjointFirst = try makeStore(unitIDs: [10], name: "disjoint-a")
        let disjointSecond = try makeStore(unitIDs: [20], name: "disjoint-b")
        let disjoint = WindowPairingCoordinator()
        let disjointFirstID = UUID()
        let disjointSecondID = UUID()
        disjoint.register(disjointFirst, id: disjointFirstID)
        disjoint.register(disjointSecond, id: disjointSecondID)
        XCTAssertTrue(disjoint.eligibility.canEnable)
        disjoint.setPairingEnabled(true, sourceID: disjointFirstID)
        XCTAssertEqual(disjointFirst.navigationUnitIDs, [10, 20])
        XCTAssertEqual(disjointSecond.navigationUnitIDs, [10, 20])
        XCTAssertEqual(disjointSecond.unitIndex, -1)
    }

    func testStoreDefaultsRFPlotToZeroThroughTwoHundredMilliseconds() throws {
        let object = currentRFSchemaPayload([
            "unitsSpikeCounts": [[[[1.0, 2.0, 3.0, 4.0]]]],
            "unitsSpikeCountsSize": [1, 1, 1, 4],
            "unitPool": [7],
            "xPositions": [0.0],
            "yPositions": [0.0],
            "timeBinEdges": [-0.1, 0.0, 0.1, 0.2, 0.3],
        ], occupancyTimeSec: 0.4, occupancyTimeSecSize: [1, 1])
        let data = try RFMappingData(
            data: JSONSerialization.data(withJSONObject: object),
            url: URL(fileURLWithPath: "/tmp/default-range.json")
        )
        let store = RFMappingStore(
            initialData: data,
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false
        )

        XCTAssertEqual(store.plotRangeStartMS, 0.0, accuracy: 1e-9)
        XCTAssertEqual(store.plotRangeEndMS, 200.0, accuracy: 1e-9)
        XCTAssertEqual(store.valueMode, .meanFiringRate)
        XCTAssertEqual(store.currentMatrix(), [[12.5]])
    }

    func testPairedUnionUsesEachWindowsQualityFilteredIDsAndReconcilesSelection() throws {
        let first = RFMappingStore(
            initialData: try makeQualityData(
                unitIDs: [7, 8],
                spatialCounts: [[1, 1], [0, 1]],
                name: "quality-first"
            ),
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false,
            unitQualityFilterEnabled: true,
            zeroSpikeBinThreshold: 1
        )
        let second = RFMappingStore(
            initialData: try makeQualityData(
                unitIDs: [8],
                spatialCounts: [[0, 1]],
                name: "quality-second"
            ),
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false,
            unitQualityFilterEnabled: false,
            zeroSpikeBinThreshold: 1
        )
        XCTAssertEqual(first.qualityFilteredUnitIDs, [7])
        XCTAssertEqual(second.qualityFilteredUnitIDs, [8])

        let coordinator = WindowPairingCoordinator()
        let firstID = UUID()
        let secondID = UUID()
        coordinator.register(first, id: firstID)
        coordinator.register(second, id: secondID)
        coordinator.setPairingEnabled(true, sourceID: secondID)

        XCTAssertEqual(first.navigationUnitIDs, [7, 8])
        XCTAssertEqual(second.navigationUnitIDs, [7, 8])
        XCTAssertEqual(first.selectedUnitID, 8)
        XCTAssertEqual(first.unitIndex, -1)
        XCTAssertEqual(second.selectedUnitID, 8)
        XCTAssertEqual(second.unitIndex, 0)

        second.setRFUnitQualityFilterEnabled(true)

        XCTAssertEqual(first.navigationUnitIDs, [7])
        XCTAssertEqual(second.navigationUnitIDs, [7])
        XCTAssertEqual(first.selectedUnitID, 7)
        XCTAssertEqual(first.unitIndex, 0)
        XCTAssertEqual(second.selectedUnitID, 7)
        XCTAssertEqual(second.unitIndex, -1)
        XCTAssertTrue(coordinator.statusText().contains("1 unit IDs"))
    }

    func testPlotRangeBroadcastRebuildsUnionAfterEveryTargetAppliesRange() throws {
        let first = RFMappingStore(
            initialData: try makeWindowQualityData(name: "range-quality-first"),
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false,
            unitQualityFilterEnabled: true,
            zeroSpikeBinThreshold: 1
        )
        let second = RFMappingStore(
            initialData: try makeWindowQualityData(name: "range-quality-second"),
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false,
            unitQualityFilterEnabled: true,
            zeroSpikeBinThreshold: 1
        )
        let coordinator = WindowPairingCoordinator()
        let firstID = UUID()
        let secondID = UUID()
        coordinator.register(first, id: firstID)
        coordinator.register(second, id: secondID)
        coordinator.setPairingEnabled(true, sourceID: firstID)

        XCTAssertEqual(first.qualityFilteredUnitIDs, [7, 8])
        XCTAssertEqual(second.qualityFilteredUnitIDs, [7, 8])
        XCTAssertEqual(first.selectedUnitID, 7)
        XCTAssertEqual(second.selectedUnitID, 7)

        first.plotRangeStartMS = 100
        first.plotRangeEndMS = 200
        first.normalizePlotTimeRange()

        // The source callback deliberately waits: the target still evaluates
        // its old range until the synchronized viewer state is broadcast.
        XCTAssertEqual(first.qualityFilteredUnitIDs, [8])
        XCTAssertEqual(second.qualityFilteredUnitIDs, [7, 8])
        XCTAssertEqual(first.navigationUnitIDs, [7, 8])

        coordinator.synchronizedStateDidChange(first.viewerSyncState, from: firstID)

        XCTAssertEqual(second.plotRangeStartMS, 100, accuracy: 1e-9)
        XCTAssertEqual(second.plotRangeEndMS, 200, accuracy: 1e-9)
        XCTAssertEqual(first.qualityFilteredUnitIDs, [8])
        XCTAssertEqual(second.qualityFilteredUnitIDs, [8])
        XCTAssertEqual(first.navigationUnitIDs, [8])
        XCTAssertEqual(second.navigationUnitIDs, [8])
        XCTAssertEqual(first.selectedUnitID, 8)
        XCTAssertEqual(second.selectedUnitID, 8)
        XCTAssertEqual(first.unitIndex, 1)
        XCTAssertEqual(second.unitIndex, 1)
    }
}
