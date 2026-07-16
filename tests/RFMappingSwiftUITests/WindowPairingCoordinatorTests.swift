import Foundation
import XCTest
@testable import RFMappingSwiftUI

@MainActor
final class WindowPairingCoordinatorTests: XCTestCase {
    func testEligibilityRequiresTwoLoadedWindowsWithExactOrderedUnits() throws {
        let matchingA = try makeFixture(unitPool: [11, 22])
        let matchingB = try makeFixture(unitPool: [11, 22])
        let reordered = try makeFixture(unitPool: [22, 11])
        defer { removeFixtures([matchingA, matchingB, reordered]) }

        let coordinator = WindowPairingCoordinator()
        let firstID = UUID()
        let secondID = UUID()
        let mismatchID = UUID()
        let first = RFMappingStore(initialURL: matchingA)
        let second = RFMappingStore(initialURL: matchingB)
        let mismatch = RFMappingStore(initialURL: reordered)

        coordinator.register(first, id: firstID)
        XCTAssertEqual(coordinator.eligibility, .noSecondWindow(loadedWindowCount: 1))

        coordinator.register(second, id: secondID)
        XCTAssertEqual(coordinator.eligibility, .matching(loadedWindowCount: 2))

        coordinator.register(mismatch, id: mismatchID)
        XCTAssertEqual(coordinator.eligibility, .mismatch(loadedWindowCount: 3))

        coordinator.unregister(id: mismatchID)
        XCTAssertEqual(coordinator.eligibility, .matching(loadedWindowCount: 2))
        coordinator.unregister(id: secondID)
        XCTAssertEqual(coordinator.eligibility, .noSecondWindow(loadedWindowCount: 1))
    }

    func testEnablingUsesInitiatingWindowAndChangesFlowBothDirections() throws {
        let firstURL = try makeFixture(unitPool: [11, 22])
        let secondURL = try makeFixture(unitPool: [11, 22])
        defer { removeFixtures([firstURL, secondURL]) }

        let coordinator = WindowPairingCoordinator()
        let firstID = UUID()
        let secondID = UUID()
        let first = RFMappingStore(initialURL: firstURL)
        let second = RFMappingStore(initialURL: secondURL)
        coordinator.register(first, id: firstID)
        coordinator.register(second, id: secondID)

        first.unitIndex = 1
        first.setValueMode(.meanFiringRate)
        first.timeResolutionMS = 20
        first.normalizeControls()
        first.selectTimelineBin(1, extending: false)
        first.selectTimelineBin(2, extending: true)
        first.plotRangeStartMS = 10
        first.plotRangeEndMS = 30
        first.xBins = 2
        first.yBins = 1
        first.smoothRadius = 2
        first.flipY = true
        first.palette = .inferno
        first.polarRadiusMode = .matlabRowOneInner
        first.spatialPlotFormat = .polar
        first.delayRGBMode = .rgb
        first.responseFloor = 7
        first.selectedTab = .timeline
        first.selectedCell = CellRef(yStart: 0, yEnd: 1, xStart: 1, xEnd: 2)
        first.timelineScrollFraction = 0.625
        first.normalizeControls()

        coordinator.setPairingEnabled(true, sourceID: firstID)

        XCTAssertTrue(coordinator.isPairingEnabled)
        XCTAssertEqual(second.unitIndex, 1)
        XCTAssertEqual(second.valueMode, .meanFiringRate)
        XCTAssertEqual(second.selectedTab, .timeline)
        XCTAssertEqual(second.spatialPlotFormat, .polar)
        XCTAssertEqual(second.delayRGBMode, .rgb)
        XCTAssertEqual(second.palette, .inferno)
        XCTAssertEqual(second.polarRadiusMode, .matlabRowOneInner)
        XCTAssertEqual(second.xBins, 2)
        XCTAssertEqual(second.yBins, 1)
        XCTAssertEqual(second.smoothRadius, 2)
        XCTAssertTrue(second.flipY)
        XCTAssertEqual(second.responseFloor, 7)
        XCTAssertEqual(second.timelineScrollFraction, 0.625, accuracy: 1e-12)
        XCTAssertEqual(second.timeGroupCenterMS(second.binIndex), first.timeGroupCenterMS(first.binIndex), accuracy: 1e-12)
        XCTAssertEqual(second.selectedTimeBoundsMS().0, first.selectedTimeBoundsMS().0, accuracy: 1e-12)
        XCTAssertEqual(second.selectedTimeBoundsMS().1, first.selectedTimeBoundsMS().1, accuracy: 1e-12)
        XCTAssertEqual(second.plotTimeBoundsMS().0, first.plotTimeBoundsMS().0, accuracy: 1e-12)
        XCTAssertEqual(second.plotTimeBoundsMS().1, first.plotTimeBoundsMS().1, accuracy: 1e-12)

        second.unitIndex = 0
        second.selectedTab = .rf
        second.spatialPlotFormat = .rectangular
        second.delayRGBMode = .delay
        second.plotRangeStartMS = -20
        second.plotRangeEndMS = 20
        second.timelineScrollFraction = 0.2
        second.normalizeControls()
        coordinator.synchronizedStateDidChange(second.viewerSyncState, from: secondID)

        XCTAssertEqual(first.unitIndex, 0)
        XCTAssertEqual(first.selectedTab, .rf)
        XCTAssertEqual(first.spatialPlotFormat, .rectangular)
        XCTAssertEqual(first.delayRGBMode, .delay)
        XCTAssertEqual(first.timelineScrollFraction, 0.2, accuracy: 1e-12)
        XCTAssertEqual(first.plotTimeBoundsMS().0, second.plotTimeBoundsMS().0, accuracy: 1e-12)
        XCTAssertEqual(first.plotTimeBoundsMS().1, second.plotTimeBoundsMS().1, accuracy: 1e-12)

        // The observer callback caused by applying the state is consumed and
        // does not turn into a reverse broadcast loop.
        coordinator.synchronizedStateDidChange(first.viewerSyncState, from: firstID)
        XCTAssertTrue(coordinator.isPairingEnabled)
    }

    func testDifferentAxesAndDimensionsClampByTimeAndDisplayGroup() throws {
        let sourceURL = try makeFixture(
            unitPool: [11, 22],
            nY: 4,
            nX: 5,
            edges: [-0.10, 0.0, 0.10, 0.20, 0.30]
        )
        let targetURL = try makeFixture(
            unitPool: [11, 22],
            nY: 2,
            nX: 2,
            edges: [-0.05, 0.05, 0.15],
            includesPresentationCounts: false
        )
        defer { removeFixtures([sourceURL, targetURL]) }

        let coordinator = WindowPairingCoordinator()
        let sourceID = UUID()
        let targetID = UUID()
        let source = RFMappingStore(initialURL: sourceURL)
        let target = RFMappingStore(initialURL: targetURL)
        coordinator.register(source, id: sourceID)
        coordinator.register(target, id: targetID)

        source.timeResolutionMS = 100
        source.setValueMode(.meanFiringRate)
        source.xBins = 5
        source.yBins = 4
        source.smoothRadius = 3
        source.normalizeControls()
        source.selectTimelineBin(3, extending: false)
        source.plotRangeStartMS = 200
        source.plotRangeEndMS = 300
        source.normalizePlotTimeRange()
        source.selectedCell = CellRef(yStart: 3, yEnd: 3, xStart: 4, xEnd: 4)
        source.timelineScrollFraction = 0.9

        coordinator.setPairingEnabled(true, sourceID: sourceID)

        XCTAssertEqual(target.xBins, 2)
        XCTAssertEqual(target.yBins, 2)
        XCTAssertEqual(target.smoothRadius, 3)
        XCTAssertEqual(target.valueMode, .spikeCount)
        XCTAssertEqual(target.binIndex, 1)
        XCTAssertEqual(target.timeGroupCenterMS(target.binIndex), 100, accuracy: 1e-12)
        XCTAssertEqual(target.selectedTimeBoundsMS().0, 50, accuracy: 1e-12)
        XCTAssertEqual(target.selectedTimeBoundsMS().1, 150, accuracy: 1e-12)
        XCTAssertEqual(target.plotTimeBoundsMS().0, 50, accuracy: 1e-12)
        XCTAssertEqual(target.plotTimeBoundsMS().1, 150, accuracy: 1e-12)
        XCTAssertEqual(
            target.selectedCell,
            CellRef(yStart: 1, yEnd: 1, xStart: 1, xEnd: 1)
        )
        XCTAssertEqual(target.timelineScrollFraction, 0.9, accuracy: 1e-12)

        // A change originating in the clamped target must patch only what the
        // user changed; it must not feed the target's smaller axes back into
        // the source window.
        target.selectedTab = .timeline
        target.timelineScrollFraction = 0.4
        coordinator.synchronizedStateDidChange(target.viewerSyncState, from: targetID)

        XCTAssertEqual(source.selectedTab, .timeline)
        XCTAssertEqual(source.timelineScrollFraction, 0.4, accuracy: 1e-12)
        XCTAssertEqual(source.xBins, 5)
        XCTAssertEqual(source.yBins, 4)
        XCTAssertEqual(source.selectedTimeBoundsMS().0, 200, accuracy: 1e-12)
        XCTAssertEqual(source.selectedTimeBoundsMS().1, 300, accuracy: 1e-12)
        XCTAssertEqual(source.plotTimeBoundsMS().0, 200, accuracy: 1e-12)
        XCTAssertEqual(source.plotTimeBoundsMS().1, 300, accuracy: 1e-12)
        XCTAssertEqual(
            source.selectedCell,
            CellRef(yStart: 3, yEnd: 3, xStart: 4, xEnd: 4)
        )
    }

    func testCompatibleOpenAndReloadAdoptCanonicalStateWhileMismatchDisables() throws {
        let firstURL = try makeFixture(unitPool: [11, 22])
        let secondURL = try makeFixture(unitPool: [11, 22])
        let thirdURL = try makeFixture(
            unitPool: [11, 22],
            nY: 1,
            nX: 1,
            edges: [-0.05, 0.05, 0.15]
        )
        let mismatchURL = try makeFixture(unitPool: [11, 99])
        defer { removeFixtures([firstURL, secondURL, thirdURL, mismatchURL]) }

        let coordinator = WindowPairingCoordinator()
        let firstID = UUID()
        let secondID = UUID()
        let thirdID = UUID()
        let first = RFMappingStore(initialURL: firstURL)
        let second = RFMappingStore(initialURL: secondURL)
        coordinator.register(first, id: firstID)
        coordinator.register(second, id: secondID)

        first.unitIndex = 1
        first.selectedTab = .timeline
        first.timelineScrollFraction = 0.75
        coordinator.setPairingEnabled(true, sourceID: firstID)

        let third = RFMappingStore(initialURL: thirdURL)
        coordinator.register(third, id: thirdID)
        XCTAssertTrue(coordinator.isPairingEnabled)
        XCTAssertEqual(third.unitIndex, 1)
        XCTAssertEqual(third.selectedTab, .timeline)
        XCTAssertEqual(third.timelineScrollFraction, 0.75, accuracy: 1e-12)

        XCTAssertTrue(second.loadJSON(thirdURL))
        XCTAssertTrue(coordinator.isPairingEnabled)
        XCTAssertEqual(second.unitIndex, 1)
        XCTAssertEqual(second.selectedTab, .timeline)

        coordinator.unregister(id: thirdID)
        XCTAssertTrue(coordinator.isPairingEnabled)

        XCTAssertTrue(second.loadJSON(mismatchURL))
        XCTAssertFalse(coordinator.isPairingEnabled)
        XCTAssertEqual(coordinator.eligibility, .mismatch(loadedWindowCount: 2))
    }

    func testPairingDisablesWhenFewerThanTwoLoadedWindowsRemain() throws {
        let firstURL = try makeFixture(unitPool: [11, 22])
        let secondURL = try makeFixture(unitPool: [11, 22])
        defer { removeFixtures([firstURL, secondURL]) }

        let coordinator = WindowPairingCoordinator()
        let firstID = UUID()
        let secondID = UUID()
        let first = RFMappingStore(initialURL: firstURL)
        let second = RFMappingStore(initialURL: secondURL)
        coordinator.register(first, id: firstID)
        coordinator.register(second, id: secondID)
        coordinator.setPairingEnabled(true, sourceID: firstID)

        coordinator.unregister(id: secondID)

        XCTAssertFalse(coordinator.isPairingEnabled)
        XCTAssertEqual(coordinator.eligibility, .noSecondWindow(loadedWindowCount: 1))
    }

    private func makeFixture(
        unitPool: [Int],
        nY: Int = 2,
        nX: Int = 3,
        edges: [Double] = [-0.02, 0.0, 0.01, 0.02, 0.03, 0.04],
        includesPresentationCounts: Bool = true
    ) throws -> URL {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("RFMappingPairingTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let url = directory.appendingPathComponent("fixture.json")
        let binCount = edges.count - 1
        let counts: [[[[Double]]]] = unitPool.indices.map { unit in
            (0..<nY).map { y in
                (0..<nX).map { x in
                    (0..<binCount).map { bin in
                        Double((unit + 1) * 100 + y * 10 + x + bin)
                    }
                }
            }
        }
        let presentations = (0..<nY).map { _ in
            (0..<nX).map { _ in 5.0 }
        }
        var payload: [String: Any] = [
            "unitsSpikeCounts": counts,
            "unitsSpikeCountsSize": [unitPool.count, nY, nX, binCount],
            "unitPool": unitPool,
            "xPositions": (0..<nX).map(Double.init),
            "yPositions": (0..<nY).map(Double.init),
            "timeBinEdges": edges
        ]
        if includesPresentationCounts {
            payload["stimulusPresentationCounts"] = presentations
        }
        try JSONSerialization.data(withJSONObject: payload).write(to: url)
        return url
    }

    private func removeFixtures(_ urls: [URL]) {
        for url in urls {
            try? FileManager.default.removeItem(at: url.deletingLastPathComponent())
        }
    }
}
