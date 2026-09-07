import Foundation
import XCTest
@testable import RFMappingSwiftUI

@MainActor
final class PerformancePathsTests: XCTestCase {
    private let sourceURL = URL(fileURLWithPath: "/tmp/performance-paths.rfmap")

    private func makeData(largeCounts: Bool = false) throws -> RFMappingData {
        let first: [[[Double]]] = [
            [[0, 4, 0, 8], [0, 0, 0, 0], [10, 0, 20, 0]],
            [[0, 8, 0, 4], [2, 0, 2, 0], [0, 0, 0, 0]],
        ]
        var second = first.map { row in row.map { $0.map { $0 * 3 } } }
        if largeCounts { second[0][0] = [9_007_199_254_740_992, 1, 7, 1] }
        let payload = currentRFSchemaPayload([
            "unitsSpikeCounts": [first, second],
            "unitsSpikeCountsSize": [2, 2, 3, 4],
            "unitPool": [17, 29],
            "xPositions": [-1.0, 0.0, 1.0],
            "yPositions": [0.0, 1.0],
            "timeBinEdges": [-0.1, 0.0, 0.1, 0.2, 0.3],
        ], occupancyTimeSec: [[1.0, 0.0, 10.0], [2.0, 1.0, 0.0]],
           occupancyTimeSecSize: [2, 3])
        return try RFMappingData(
            data: JSONSerialization.data(withJSONObject: payload),
            url: sourceURL
        )
    }

    func testBatchedWindowsNormalizeBoundsAndSurviveCacheReplacement() throws {
        let data = try makeData()
        let groups = [AxisGroup(start: 1, end: -5), AxisGroup(start: 99, end: 2)]
        let expected: [[[Double]]] = [
            [[4, 0, 10], [8, 2, 0]],
            [[8, 0, 20], [4, 2, 0]],
        ]
        XCTAssertEqual(data.countWindows(unitIndex: 0, timeGroups: groups), expected)
        XCTAssertEqual(data.countWindows(unitIndex: 0, timeGroups: []), [])
        XCTAssertEqual(
            data.countWindows(unitIndex: 1, timeGroups: groups),
            expected.map { frame in frame.map { row in row.map { $0 * 3 } } }
        )
        XCTAssertEqual(
            data.countWindows(unitIndex: 0, timeGroups: [AxisGroup(start: 1, end: 1)])[0],
            [[4, 0, 0], [8, 0, 0]]
        )
        XCTAssertEqual(data.countWindows(unitIndex: 0, timeGroups: groups), expected)
    }

    func testBatchedWindowsDoNotLoseSmallCountsAfterLargePrefix() throws {
        let data = try makeData(largeCounts: true)
        let frames = data.countWindows(unitIndex: 1, timeGroups: [
            AxisGroup(start: 1, end: 1),
            AxisGroup(start: 2, end: 3),
        ])
        XCTAssertEqual(frames[0][0][0], 1)
        XCTAssertEqual(frames[1][0][0], 8)
    }

    func testBatchedPoolingKeepsOccupancyAndMissingCellsAcrossLayouts() throws {
        let data = try makeData()
        let groups = [AxisGroup(start: 0, end: 1), AxisGroup(start: 2, end: 3)]
        let frames = data.spatialObservationFrames(
            unitIndex: 0,
            timeGroups: groups,
            yGroups: [AxisGroup(start: 1, end: 0)],
            xGroups: [AxisGroup(start: 0, end: 2)]
        )
        XCTAssertEqual(frames[0][0][0].count, 24)
        XCTAssertEqual(frames[1][0][0].count, 34)
        XCTAssertEqual(frames[0][0][0].occupancyTimeSeconds, 14)
        XCTAssertEqual(frames[0][0][0].sourcePixelCount, 4)

        let native = data.spatialObservationFrames(
            unitIndex: 1,
            timeGroups: groups,
            yGroups: [AxisGroup(start: 1, end: 1), AxisGroup(start: 0, end: 0)],
            xGroups: (0..<3).map { AxisGroup(start: $0, end: $0) }
        )
        XCTAssertEqual(native[0][0][0].count, 24)
        XCTAssertEqual(native[0][0][0].occupancyTimeSeconds, 2)
        XCTAssertEqual(native[0][0][2].sourcePixelCount, 0)
        XCTAssertEqual(native[0][1][1].occupancyTimeSeconds, 0)
    }

    func testTimelineRemainsFullAxisAndRefreshesAfterDisplayChanges() throws {
        let data = try makeData()
        let store = RFMappingStore(
            initialData: data,
            loadDefault: false,
            discoverJSONChoices: false,
            discoverCompanions: false,
            unitQualityFilterEnabled: false
        )
        store.timeResolutionMS = 200
        store.xBins = 1
        store.yBins = 1
        store.normalizeControls()
        let initial = store.timelineSnapshot()
        XCTAssertEqual(initial.timeGroups, [
            AxisGroup(start: 0, end: 1), AxisGroup(start: 2, end: 3),
        ])
        XCTAssertEqual(initial.matrices[0][0][0] ?? .nan, 24.0 / 14.0, accuracy: 1e-12)
        XCTAssertEqual(initial.matrices[1][0][0] ?? .nan, 34.0 / 14.0, accuracy: 1e-12)
        XCTAssertEqual(initial.totals, [24.0 / 14.0, 34.0 / 14.0])

        store.plotRangeStartMS = 0
        store.plotRangeEndMS = 100
        store.normalizeControls()
        XCTAssertEqual(store.timelineSnapshot().matrices, initial.matrices)

        store.valueMode = .spikeCount
        XCTAssertEqual(store.timelineSnapshot().matrices, [[[6.0]], [[8.5]]])
        store.selectUnitID(29)
        XCTAssertEqual(store.timelineSnapshot().matrices, [[[18.0]], [[25.5]]])

        store.selectUnitID(17)
        let delays = store.delayMatrixForTimeGroups()
        XCTAssertEqual(delays[0][0] ?? .nan, 200, accuracy: 1e-12)
        XCTAssertEqual(delays[1][0] ?? .nan, 0, accuracy: 1e-12)
        XCTAssertEqual(delays[1][1] ?? .nan, 0, accuracy: 1e-12)
        XCTAssertNil(delays[0][1])
        XCTAssertEqual(store.windowTitle, "performance-paths.rfmap — RF Map Viewer")

        store.valueMode = .meanFiringRate
        store.xBins = 3
        store.yBins = 2
        store.flipY = true
        store.smoothRadius = 0
        store.normalizeControls()
        XCTAssertEqual(store.timelineSnapshot().matrices[0], [
            [4.0, 2.0, nil], [4.0, nil, 1.0],
        ])
        store.smoothRadius = 1
        let smoothed = store.timelineSnapshot().matrices[0]
        XCTAssertEqual(smoothed[0][0] ?? .nan, 44.0 / 12.0, accuracy: 1e-12)
        XCTAssertNil(smoothed[1][1], "Smoothing must preserve the missing-occupancy mask")
    }

    func testCancelledDecodePreservesCancellationError() async throws {
        let payload = currentRFSchemaPayload([
            "unitsSpikeCounts": [[[[1.0]]]],
            "unitsSpikeCountsSize": [1, 1, 1, 1],
            "unitPool": [17],
            "xPositions": [0.0],
            "yPositions": [0.0],
            "timeBinEdges": [0.0, 0.1],
        ], occupancyTimeSec: [[1.0]], occupancyTimeSecSize: [1, 1])
        let json = try JSONSerialization.data(withJSONObject: payload)
        let url = sourceURL
        let task = Task.detached {
            withUnsafeCurrentTask { $0?.cancel() }
            return try RFMappingData(data: json, url: url)
        }
        do {
            _ = try await task.value
            XCTFail("A cancelled RF decode must not publish a model")
        } catch is CancellationError {
            // Cancellation is expected, rather than a user-visible format error.
        }
    }
}
