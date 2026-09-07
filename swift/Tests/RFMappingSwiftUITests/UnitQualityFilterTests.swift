import Foundation
import XCTest
@testable import RFMappingSwiftUI

@MainActor
final class UnitQualityFilterTests: XCTestCase {
    private func makeWindowSensitiveData() throws -> RFMappingData {
        let payload = currentRFSchemaPayload([
            "unitsSpikeCounts": [
                [[[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]]],
                [[[0.0, 1.0], [0.0, 1.0], [0.0, 1.0]]],
            ],
            "unitsSpikeCountsSize": [2, 1, 3, 2],
            "unitPool": [10, 20],
            "xPositions": [-1.0, 0.0, 1.0],
            "yPositions": [0.0],
            "timeBinEdges": [0.0, 0.1, 0.2],
        ], occupancyTimeSec: [0.2, 0.2, 0.2], occupancyTimeSecSize: [1, 3])
        return try RFMappingData(
            data: JSONSerialization.data(withJSONObject: payload),
            url: URL(fileURLWithPath: "/tmp/unit-quality-window.rfmap")
        )
    }

    private func makeAllZeroData() throws -> RFMappingData {
        let payload = currentRFSchemaPayload([
            "unitsSpikeCounts": [[[[0.0], [0.0]]], [[[0.0], [0.0]]]],
            "unitsSpikeCountsSize": [2, 1, 2, 1],
            "unitPool": [10, 20],
            "xPositions": [-1.0, 1.0],
            "yPositions": [0.0],
            "timeBinEdges": [0.0, 0.1],
        ], occupancyTimeSec: [0.1, 0.1], occupancyTimeSecSize: [1, 2])
        return try RFMappingData(
            data: JSONSerialization.data(withJSONObject: payload),
            url: URL(fileURLWithPath: "/tmp/unit-quality-empty.rfmap")
        )
    }

    func testDefaultsAndUpdatesPersist() {
        let suiteName = "UnitQualityFilterTests.\(UUID().uuidString)"
        let preferences = UserDefaults(suiteName: suiteName)!
        defer { preferences.removePersistentDomain(forName: suiteName) }

        let defaults = RFMappingStore(
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false,
            preferences: preferences
        )
        XCTAssertTrue(defaults.rfFilterUnitsWithZeroBins)
        XCTAssertEqual(defaults.rfZeroBinThreshold, 1)
        XCTAssertEqual(defaults.rfZeroBinThresholdEditMaximum, 100_000)

        defaults.setRFUnitQualityFilterEnabled(false)
        defaults.setRFZeroBinThreshold(7)
        let restored = RFMappingStore(
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false,
            preferences: preferences
        )
        XCTAssertFalse(restored.rfFilterUnitsWithZeroBins)
        XCTAssertEqual(restored.rfZeroBinThreshold, 7)

        restored.setRFZeroBinThreshold(0)
        XCTAssertEqual(restored.rfZeroBinThreshold, 1)
        restored.setRFZeroBinThreshold(100_001)
        XCTAssertEqual(restored.rfZeroBinThreshold, 100_000)

        preferences.set(500_000, forKey: "rfmapping.rfZeroBinThreshold")
        let clampedStored = RFMappingStore(
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false,
            preferences: preferences
        )
        XCTAssertEqual(clampedStored.rfZeroBinThreshold, 100_000)

        let clampedInjected = RFMappingStore(
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false,
            unitQualityFilterEnabled: true,
            zeroSpikeBinThreshold: 500_000,
            preferences: preferences
        )
        XCTAssertEqual(clampedInjected.rfZeroBinThreshold, 100_000)
    }

    func testFilterUsesNativeBinsCurrentRFWindowAndStrictThreshold() throws {
        let data = try makeWindowSensitiveData()
        let store = RFMappingStore(
            initialData: data,
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false,
            unitQualityFilterEnabled: true,
            zeroSpikeBinThreshold: 1
        )

        XCTAssertEqual(store.qualityFilteredUnitIDs, [10, 20])
        XCTAssertEqual(
            data.zeroSpikeSpatialBinCount(unitIndex: 0, start: 1, end: 1),
            3
        )

        // Display pooling and smoothing never change the native-grid quality
        // result.
        store.xBins = 1
        store.smoothRadius = 3
        store.normalizeControls()
        XCTAssertEqual(store.qualityFilteredUnitIDs, [10, 20])

        store.selectUnitID(10)
        store.plotRangeStartMS = 100
        store.plotRangeEndMS = 200
        store.normalizePlotTimeRange()
        XCTAssertEqual(store.qualityFilteredUnitIDs, [20])
        XCTAssertEqual(store.navigationUnitIDs, [20])
        XCTAssertEqual(store.selectedUnitID, 20)

        // A probe-region filter can only narrow the quality-filtered pool; it
        // cannot reintroduce a zero-spike unit.
        store.setProbeFilteredUnitIDs([10, 20])
        XCTAssertEqual(store.navigationUnitIDs, [20])
        store.setProbeFilteredUnitIDs([10])
        XCTAssertTrue(store.navigationUnitIDs.isEmpty)
        XCTAssertNil(store.selectedUnitID)
        store.setProbeFilteredUnitIDs(nil)
        XCTAssertEqual(store.navigationUnitIDs, [20])
        XCTAssertEqual(store.selectedUnitID, 20)

        // Timeline selection remains independent from the 2-D RF window and
        // the timeline still retains every source group.
        store.rangeStartMS = 0
        store.rangeEndMS = 100
        store.normalizeControls()
        XCTAssertEqual(store.qualityFilteredUnitIDs, [20])
        XCTAssertEqual(store.timelineSnapshot().timeGroups.count, 2)

        store.setRFZeroBinThreshold(3)
        XCTAssertEqual(store.qualityFilteredUnitIDs, [20])
        store.setRFZeroBinThreshold(4)
        XCTAssertEqual(store.rfZeroBinThreshold, 3)
        XCTAssertEqual(store.rfZeroBinThresholdEditMaximum, 3)
        XCTAssertEqual(store.qualityFilteredUnitIDs, [20])
    }

    func testPersistedThresholdAboveSpatialRemainsEffectiveUntilUserEdits() throws {
        let suiteName = "UnitQualityFilterTests.\(UUID().uuidString)"
        let preferences = UserDefaults(suiteName: suiteName)!
        defer { preferences.removePersistentDomain(forName: suiteName) }
        preferences.set(4, forKey: "rfmapping.rfZeroBinThreshold")

        // A value restored before this smaller data set is adopted retains the
        // persisted 1...100,000 contract. Runtime comparison and the frozen
        // export snapshot use that unmodified value, even above spatial count.
        let restoredAboveSpatial = RFMappingStore(
            initialData: try makeWindowSensitiveData(),
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false,
            unitQualityFilterEnabled: true,
            preferences: preferences
        )
        restoredAboveSpatial.plotRangeStartMS = 100
        restoredAboveSpatial.plotRangeEndMS = 200
        restoredAboveSpatial.normalizePlotTimeRange()
        XCTAssertEqual(restoredAboveSpatial.rfZeroBinThreshold, 4)
        XCTAssertEqual(restoredAboveSpatial.rfZeroBinThresholdEditMaximum, 3)
        XCTAssertEqual(restoredAboveSpatial.qualityFilteredUnitIDs, [10, 20])
        let snapshot = try XCTUnwrap(restoredAboveSpatial.unitQualityFilterSnapshot)
        XCTAssertEqual(snapshot.zeroSpikeSpatialBinThreshold, 4)
        XCTAssertEqual(snapshot.spatialBinCount, 3)
        XCTAssertEqual(snapshot.visibleUnitIDs, [10, 20])
        XCTAssertEqual(snapshot.excludedUnitIDs, [])

        // A live control edit is the equivalent of saving Python Settings, so
        // the active file's native spatial count becomes the edit-time limit.
        restoredAboveSpatial.setRFZeroBinThreshold(4)
        XCTAssertEqual(restoredAboveSpatial.rfZeroBinThreshold, 3)
        XCTAssertEqual(restoredAboveSpatial.qualityFilteredUnitIDs, [20])
        XCTAssertEqual(
            preferences.integer(forKey: "rfmapping.rfZeroBinThreshold"),
            3
        )
    }

    func testAllFilteredIsNonfatalAndSnapshotDescribesContract() throws {
        let data = try makeAllZeroData()
        let store = RFMappingStore(
            initialData: data,
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false,
            unitQualityFilterEnabled: true,
            zeroSpikeBinThreshold: 1
        )

        XCTAssertTrue(store.qualityFilteredUnitIDs.isEmpty)
        XCTAssertTrue(store.navigationUnitIDs.isEmpty)
        XCTAssertNil(store.selectedUnitID)
        XCTAssertEqual(store.unitIndex, -1)
        XCTAssertTrue(store.statusText.contains("0/2 units visible"))
        XCTAssertEqual(
            store.unitUnavailableReason,
            .noQualityVisibleUnits(total: 2)
        )

        let snapshot = try XCTUnwrap(store.unitQualityFilterSnapshot)
        XCTAssertTrue(snapshot.enabled)
        XCTAssertEqual(snapshot.zeroSpikeSpatialBinThreshold, 1)
        XCTAssertEqual(snapshot.sourceStartBin, 0)
        XCTAssertEqual(snapshot.sourceEndBin, 0)
        XCTAssertEqual(snapshot.spatialBinCount, 2)
        XCTAssertEqual(snapshot.visibleUnitIDs, [])
        XCTAssertEqual(snapshot.excludedUnitIDs, [10, 20])

        store.setRFUnitQualityFilterEnabled(false)
        XCTAssertEqual(store.navigationUnitIDs, [10, 20])
        XCTAssertEqual(store.selectedUnitID, 10)
        XCTAssertEqual(store.unitIndex, 0)
    }

    func testUnavailableReasonDistinguishesQualityHiddenFromPairedMissing() throws {
        let store = RFMappingStore(
            initialData: try makeWindowSensitiveData(),
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false,
            unitQualityFilterEnabled: true,
            zeroSpikeBinThreshold: 1
        )
        store.plotRangeStartMS = 100
        store.plotRangeEndMS = 200
        store.normalizePlotTimeRange()
        XCTAssertEqual(store.qualityFilteredUnitIDs, [20])

        store.setPairedUnitIDs([10, 20, 99])
        store.selectUnitID(10)
        XCTAssertFalse(store.hasSelectedUnit)
        XCTAssertEqual(
            store.unitUnavailableReason,
            .qualityFiltered(unitID: 10)
        )

        store.selectUnitID(99)
        XCTAssertFalse(store.hasSelectedUnit)
        XCTAssertEqual(
            store.unitUnavailableReason,
            .pairedMissing(unitID: 99)
        )
    }
}
