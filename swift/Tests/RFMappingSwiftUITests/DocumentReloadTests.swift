import Foundation
import Observation
import XCTest
@testable import RFMappingSwiftUI

@MainActor
final class DocumentReloadTests: XCTestCase {
    private func directory() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        addTeardownBlock { try FileManager.default.removeItem(at: root) }
        return root
    }

    private func writeRF(_ url: URL, unitIDs: [Int] = [17, 42], count: Double = 2) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        var payload = currentRFSchemaPayload([
            "unitsSpikeCounts": unitIDs.map { _ in [[[count, count + 1, count + 2], [count + 3, count + 4, count + 5]]] },
            "unitsSpikeCountsSize": [unitIDs.count, 1, 2, 3],
            "unitPool": unitIDs, "xPositions": [-1, 1], "yPositions": [0],
            "timeBinEdges": [0, 0.08, 0.16, 0.24],
        ], occupancyTimeSec: [[1, 1]], occupancyTimeSecSize: [1, 2])
        payload["stimulusPresentationCounts"] = [[2, 2]]
        try JSONSerialization.data(withJSONObject: payload).write(to: url)
    }

    private func store(_ url: URL, preferences: UserDefaults? = nil) throws -> RFMappingStore {
        RFMappingStore(initialData: try RFMappingData(url: url), loadDefault: false,
                       discoverJSONChoices: false, discoverCompanions: false,
                       unitQualityFilterEnabled: false,
                       preferences: preferences ?? UserDefaults(suiteName: UUID().uuidString)!)
    }

    private func writeTuning(_ url: URL, rate: Double) throws {
        try JSONSerialization.data(withJSONObject: ["17": Array(repeating: rate, count: 180)])
            .write(to: url)
    }

    func testReloadReadsUpdatedCountsAndPreservesRecordedUnitControlsAndADN() async throws {
        let url = try directory().appendingPathComponent("261019_1/data/rfmapping/ProbeA/new.rfmap")
        try writeRF(url)
        let viewer = try store(url)
        let oldData = try XCTUnwrap(viewer.data)
        try ADNSelection.save([17, 42], probeName: "ProbeA", to: XCTUnwrap(viewer.adnSelectionURL))
        viewer.setOnlyADN(true)
        viewer.selectUnitID(42)
        viewer.valueMode = .spikeCount
        viewer.xBins = 1
        viewer.timeResolutionMS = 160
        viewer.setRFSubtractEnabled(true)
        viewer.plotRangeStartMS = 160
        viewer.plotRangeEndMS = 240
        viewer.subtractRangeStartMS = 0
        viewer.subtractRangeEndMS = 80
        viewer.palette = .viridis
        viewer.selectedTab = .timeline
        viewer.probeExpanded = false
        viewer.waveformExpanded = false
        viewer.tuningExpanded = false
        viewer.normalizeControls()
        let state = viewer.viewerSyncState
        try writeRF(url, unitIDs: [42, 17], count: 20)

        await viewer.reloadDocument()

        XCTAssertFalse(viewer.data === oldData)
        XCTAssertEqual(viewer.selectedUnitID, 42)
        XCTAssertEqual(viewer.unitIndex, 0)
        XCTAssertEqual(viewer.viewerSyncState, state)
        XCTAssertTrue(viewer.onlyADN)
        XCTAssertEqual(viewer.adnUnitIDs, [17, 42])
        XCTAssertEqual(viewer.probeExpanded, false)
        XCTAssertEqual(viewer.waveformExpanded, false)
        XCTAssertEqual(viewer.tuningExpanded, false)
        let reloaded = try XCTUnwrap(viewer.data)
        XCTAssertEqual(try reloaded.responseMatrix(unitIndex: 0, start: 0, end: 0, valueMode: .spikeCount), [[20, 23]])
        XCTAssertNil(viewer.errorMessage)
        XCTAssertNil(viewer.loadingStage)
        XCTAssertFalse(viewer.isLoadingData)
    }

    func testBrokenReplacementPreservesPreviousRFAndControls() async throws {
        let url = try directory().appendingPathComponent("old.rfmap")
        try writeRF(url)
        let viewer = try store(url)
        viewer.selectUnitID(42)
        viewer.plotRangeStartMS = 80
        viewer.plotRangeEndMS = 160
        viewer.normalizeControls()
        let previous = try XCTUnwrap(viewer.data)
        let state = viewer.viewerSyncState
        try Data("incomplete update".utf8).write(to: url)

        await viewer.reloadDocument()

        XCTAssertTrue(viewer.data === previous)
        XCTAssertEqual(viewer.viewerSyncState, state)
        XCTAssertTrue(viewer.errorMessage?.contains("previous document") == true)
        XCTAssertFalse(viewer.isLoadingData)
        XCTAssertNil(viewer.loadingStage)
    }

    func testReloadRefreshesExplicitCompanionsAndKeepsLastValidOnReadFailure() async throws {
        let root = try directory()
        let url = root.appendingPathComponent("new.rfmap")
        let tuning = root.appendingPathComponent("attached.tc")
        let probe = root.appendingPathComponent("positions.probe")
        try writeRF(url)
        try writeTuning(tuning, rate: 3)
        try Data("unit_index,unit_id,x_um,y_um\n0,17,10,120\n1,42,20,140\n".utf8).write(to: probe)
        let viewer = try store(url)
        viewer.setHDTuningURL(tuning)
        viewer.setProbePositionsURL(probe)
        try writeTuning(tuning, rate: 8)
        try Data("unit_index,unit_id,x_um,y_um\n0,17,10,125\n1,42,20,145\n".utf8).write(to: probe)

        await viewer.reloadDocument()

        XCTAssertEqual(try viewer.hdTuning?.processedCurve(unitID: 17, displayBins: 30, smoothing: false).ratesHz.first, 8)
        XCTAssertEqual(viewer.probeGeometry?.units.first?.yMicrometers, 125)
        let validGeometry = viewer.probeGeometry
        try Data("broken".utf8).write(to: tuning)
        try Data("broken".utf8).write(to: probe)
        await viewer.reloadDocument()
        XCTAssertEqual(try viewer.hdTuning?.processedCurve(unitID: 17, displayBins: 30, smoothing: false).ratesHz.first, 8)
        XCTAssertEqual(viewer.probeGeometry, validGeometry)
        XCTAssertNotNil(viewer.hdTuningError)
        XCTAssertNotNil(viewer.probeGeometryError)
    }

    func testCancelledReloadCannotReplaceCurrentDocument() async throws {
        let url = try directory().appendingPathComponent("new.rfmap")
        try writeRF(url)
        let viewer = try store(url)
        let previous = viewer.data
        try writeRF(url, count: 40)
        let reload = Task { await viewer.reloadDocument() }
        reload.cancel()
        await reload.value
        XCTAssertTrue(viewer.data === previous)
        XCTAssertFalse(viewer.isLoadingData)
        XCTAssertNil(viewer.loadingStage)
    }

    func testOpeningAnotherDocumentWinsOverPendingReload() async throws {
        let root = try directory()
        let first = root.appendingPathComponent("first.rfmap")
        let second = root.appendingPathComponent("second.rfmap")
        try writeRF(first)
        try writeRF(second, unitIDs: [90])
        let viewer = try store(first)
        let reload = Task { await viewer.reloadDocument() }
        await Task.yield()
        XCTAssertTrue(viewer.loadJSON(second))
        await reload.value
        XCTAssertEqual(viewer.data?.url, second)
        XCTAssertEqual(viewer.selectedUnitID, 90)
        XCTAssertFalse(viewer.isLoadingData)
        XCTAssertNil(viewer.loadingStage)
    }

    func testResultPathSavedDuringRFReloadAppliesToReplacementDocument() async throws {
        let url = try directory().appendingPathComponent("new.rfmap")
        try writeRF(url)
        let viewer = try store(url)
        withObservationTracking {
            _ = viewer.isLoadingData
        } onChange: {
            MainActor.assumeIsolated {
                viewer.displayDefaults.rfResultLayoutPattern = "[^/]+/new"
                viewer.saveDisplayDefaults()
            }
        }

        await viewer.reloadDocument()

        XCTAssertEqual(viewer.displayDefaults.rfResultLayoutPattern, "[^/]+/new")
        XCTAssertTrue(viewer.savedResultErrors["2d:excitatory"]?.contains("rf_2d_ext.npz") == true)
        XCTAssertNil(viewer.errorMessage)
    }
}
