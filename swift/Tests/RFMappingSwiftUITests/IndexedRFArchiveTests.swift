import Foundation
import CryptoKit
import XCTest
import zlib
@testable import RFMappingSwiftUI

final class IndexedRFArchiveTests: XCTestCase {
    private let url = URL(fileURLWithPath: "/tmp/synthetic-indexed.rfmap")

    func testNumPyCompressedArchivePreservesSourceOrderAndFortranAxes() async throws {
        let fixtureURL = try XCTUnwrap(Bundle.module.url(
            forResource: "indexed-v2-numpy", withExtension: "base64", subdirectory: "Fixtures"
        ))
        let encoded = try String(contentsOf: fixtureURL, encoding: .utf8)
        let source = try XCTUnwrap(Data(base64Encoded: encoded, options: .ignoreUnknownCharacters))
        let data = try RFMappingData(data: source, url: url)
        XCTAssertTrue(data.isIndexed)
        XCTAssertEqual(data.unitPool, [41, 7, 902])
        XCTAssertEqual(data.loadedUnitIndices, [0])
        XCTAssertEqual(data.cachedUnitCount, 1)
        XCTAssertEqual(data.counts.count, 3)
        XCTAssertTrue(data.counts[1].isEmpty)
        XCTAssertEqual(data.unitIndex(forUnitID: 902), 2)
        XCTAssertThrowsError(try data.rfMap(byUnitID: 902))
        XCTAssertEqual(data.timeBinEdges, [-0.1, 0, 0.1, 0.2])
        let original = try data.rfMap(byOriginalIndex: 0)
        XCTAssertEqual(original.spikeCounts, [[[1, 2, 3], [4, 5, 6]], [[0, 0, 0], [10, 11, 12]]])

        // A priority request can load index 2 before index 1. Numeric methods
        // must keep using source indices, not positions in the partial cache.
        let third = try await data.loadUnit(at: 2)
        XCTAssertFalse(data.isUnitCached(2))
        try data.cacheUnit(third)
        XCTAssertEqual(data.rfMaps.originalIndices, [0, 2])
        XCTAssertEqual(data.countMatrix(unitIndex: 2, start: 0, end: 2), [[2, 2], [0, 6]])
        XCTAssertEqual(data.metrics(for: 2).totalSpikes, 10)

        let second = try await data.loadUnit(at: 1)
        try data.cacheUnit(second)
        XCTAssertEqual(second.spikeCounts, [[[4, 5, 6], [7, 8, 9]], [[0, 0, 0], [10, 11, 12]]])
        XCTAssertEqual(data.rfMaps.originalIndices, [0, 1, 2])
        XCTAssertEqual(data.cachedUnitCount, 3)
        XCTAssertEqual(try data.rfMap(byOriginalIndex: 0).spikeCounts, original.spikeCounts)
        XCTAssertFalse(data.supportsMeanFiringRate)
        XCTAssertThrowsError(try data.responseMatrix(unitIndex: 1, start: 0, end: 2, valueMode: .meanFiringRate))
        XCTAssertEqual(try data.responseMatrix(unitIndex: 1, start: 0, end: 2,
                                               valueMode: .spikeCount), [[15, 24], [nil, 33]])
        try data.cacheUnit(second)
        XCTAssertEqual(data.cachedUnitCount, 3)
    }

    func testStoredZIP64AndSingletonAxes() throws {
        let archive = try makeArchive(unitValues: [[9]], zip64: true)
        let data = try RFMappingData(data: archive, url: url)
        XCTAssertEqual(data.counts, [[[[9]]]])
        XCTAssertEqual(data.nUnits, 1)
        XCTAssertEqual(data.nY, 1)
        XCTAssertEqual(data.nX, 1)
        XCTAssertEqual(data.nBins, 1)
        XCTAssertEqual(data.timeBinEdges, [0, 0.1])
    }

    func testOmittedMetadataDescriptionsAndOccupancySizeAreAccepted() async throws {
        let keys = ["spikeCountDefinition", "occupancyTimeDefinition", "occupancyTimeSecSize"]
        for omitted in keys.map({ [$0] }) + [keys] {
            let archive = zip(try makeEntries(
                unitValues: [[2, 0], [4, 0]], shape: [1, 2, 1], occupancy: [0.5, 0],
                omittingMetadata: Set(omitted)
            ))
            let data = try RFMappingData(data: archive, url: url)
            XCTAssertEqual(data.spikeCountDefinition, RFMappingData.expectedSpikeCountDefinition)
            XCTAssertEqual(data.occupancyTimeDefinition, RFMappingData.expectedOccupancyTimeDefinition)
            XCTAssertEqual(try data.responseMatrix(
                unitIndex: 0, start: 0, end: 0, valueMode: .meanFiringRate
            ), [[4, nil]])
            let second = try await data.loadUnit(at: 1)
            try data.cacheUnit(second)
            XCTAssertEqual(try data.responseMatrix(
                unitIndex: 1, start: 0, end: 0, valueMode: .meanFiringRate
            ), [[8, nil]])
        }
    }

    func testMissingRequiredOrConflictingIndexedMetadataIsRejected() throws {
        for key in ["responseUnits", "responseNormalization"] {
            XCTAssertThrowsError(try RFMappingData(data: zip(makeEntries(
                unitValues: [[1]], omittingMetadata: [key]
            )), url: url))
        }
        let conflicts: [(String, Any)] = [
            ("responseUnits", "mean_spikes"),
            ("responseNormalization", "legacy"),
            ("spikeCountDefinition", "legacy"),
            ("occupancyTimeDefinition", "legacy"),
            ("occupancyTimeSecSize", [2, 1]),
            ("spikeCountDefinition", NSNull()),
            ("occupancyTimeDefinition", NSNull()),
            ("occupancyTimeSecSize", NSNull()),
        ]
        for (key, value) in conflicts {
            XCTAssertThrowsError(try RFMappingData(data: zip(makeEntries(
                unitValues: [[1]], metadataOverrides: [key: value]
            )), url: url))
        }
    }

    func testMissingSizeMarkerDoesNotBypassActualIndexedOccupancyShape() throws {
        var entries = try makeEntries(
            unitValues: [[1, 2]], shape: [1, 2, 1], omittingMetadata: ["occupancyTimeSecSize"]
        )
        let occupancyIndex = try XCTUnwrap(entries.firstIndex { $0.0 == "occupancyTimeSec" })
        entries[occupancyIndex].1 = npy(dtype: "<f8", shape: [2, 1], bytes: doubles([1, 1]))
        XCTAssertThrowsError(try RFMappingData(data: zip(entries), url: url))
    }

    func testInvalidLaterCountsFailWithoutPublishingPartialUnitAndCanBeRetried() async throws {
        for invalid in [-1.0, 0.5, .nan, .infinity, 18_446_744_073_709_551_616.0] {
            let data = try RFMappingData(data: makeArchive(unitValues: [[1], [invalid]]), url: url)
            for _ in 0..<2 {
                do {
                    _ = try await data.loadUnit(at: 1)
                    XCTFail("Invalid counts unexpectedly loaded: \(invalid)")
                } catch {
                    XCTAssertFalse(data.isUnitCached(1))
                    XCTAssertTrue(data.counts[1].isEmpty)
                    XCTAssertEqual(data.cachedUnitCount, 1)
                }
            }
        }
    }

    func testZeroOccupancyCountsAreValidatedPerUnit() async throws {
        let data = try RFMappingData(data: makeArchive(
            unitValues: [[0, 1], [1, 1]], shape: [1, 2, 1], occupancy: [0, 1]
        ), url: url)
        do {
            _ = try await data.loadUnit(at: 1)
            XCTFail("Counts in zero-occupancy cells must be rejected.")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("occupancy"))
            XCTAssertEqual(data.loadedUnitIndices, [0])
        }
    }

    func testWrongUnitShapeIsRejectedBeforePublishing() async throws {
        var entries = try makeEntries(unitValues: [[1], [2]])
        entries[entries.count - 1].1 = npy(dtype: "<f8", shape: [1, 1, 2], bytes: doubles([2, 3]))
        let data = try RFMappingData(data: zip(entries), url: url)
        do {
            _ = try await data.loadUnit(at: 1)
            XCTFail("Wrong shape must be rejected.")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("shape"))
        }
    }

    func testMissingDuplicateAndTruncatedEntriesAreRejected() throws {
        let entries = try makeEntries(unitValues: [[1]])
        XCTAssertThrowsError(try RFMappingData(data: zip(Array(entries.dropLast())), url: url))
        XCTAssertThrowsError(try RFMappingData(data: zip(entries + [entries[0]]), url: url))
        let complete = zip(entries)
        for length in [0, 3, 20, complete.count - 1, complete.count - 22] {
            XCTAssertThrowsError(try RFMappingData(data: Data(complete.prefix(length)), url: url))
        }
    }

    func testBadCRCAndTruncatedNPYAreRejected() throws {
        var entries = try makeEntries(unitValues: [[1]])
        XCTAssertThrowsError(try RFMappingData(data: zip(entries, corruptCRC: true), url: url))
        entries[entries.count - 1].1.removeLast()
        XCTAssertThrowsError(try RFMappingData(data: zip(entries), url: url))
    }

    func testObjectAndBooleanArraysAreRejected() throws {
        for dtype in ["|O8", "|b1"] {
            var entries = try makeEntries(unitValues: [[1]])
            entries[entries.count - 1].1 = npy(dtype: dtype, shape: [1, 1, 1], bytes: Data([1]))
            XCTAssertThrowsError(try RFMappingData(data: zip(entries), url: url))
        }
    }

    func testUnknownVersionAndNonIntegerUnitPoolAreRejected() throws {
        var entries = try makeEntries(unitValues: [[1]])
        let wrongMetadata = try JSONSerialization.data(withJSONObject: ["formatVersion": 3])
        entries[0].1 = npy(dtype: "|u1", shape: [wrongMetadata.count], bytes: wrongMetadata)
        XCTAssertThrowsError(try RFMappingData(data: zip(entries), url: url))
        entries = try makeEntries(unitValues: [[1]])
        entries[1].1 = npy(dtype: "<f8", shape: [1], bytes: doubles([41]))
        XCTAssertThrowsError(try RFMappingData(data: zip(entries), url: url))
    }

    func testSourceReplacementStopsProgressiveReadsAndExportButKeepsCachedUnit() async throws {
        let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".rfmap")
        defer { try? FileManager.default.removeItem(at: file) }
        let original = try makeArchive(unitValues: [[1], [2]])
        try original.write(to: file)
        let data = try RFMappingData(url: file)
        XCTAssertTrue(data.sourceSHA256.isEmpty)
        XCTAssertEqual(data.sourceByteCount, original.count)
        try data.prepareSourceHash()
        XCTAssertEqual(data.sourceSHA256, SHA256.hash(data: original).map { String(format: "%02x", $0) }.joined())
        try makeArchive(unitValues: [[9], [8]]).write(to: file, options: .atomic)
        do {
            _ = try await data.loadUnit(at: 1)
            XCTFail("Replaced source must stop progressive reads.")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("changed"))
        }
        XCTAssertThrowsError(try data.prepareSourceHash())
        XCTAssertEqual(data.counts[0], [[[1]]])
        XCTAssertEqual(data.loadedUnitIndices, [0])
    }

    private func makeArchive(
        unitValues: [[Double]], shape: [Int] = [1, 1, 1],
        occupancy: [Double]? = nil, zip64: Bool = false
    ) throws -> Data {
        zip(try makeEntries(unitValues: unitValues, shape: shape, occupancy: occupancy), zip64: zip64)
    }

    private func makeEntries(
        unitValues: [[Double]], shape: [Int] = [1, 1, 1], occupancy: [Double]? = nil,
        omittingMetadata: Set<String> = [], metadataOverrides: [String: Any] = [:]
    ) throws -> [(String, Data)] {
        var metadata: [String: Any] = [
            "formatVersion": 2,
            "unitsSpikeCountsSize": [unitValues.count] + shape,
            "occupancyTimeSecSize": Array(shape.prefix(2)),
            "responseUnits": RFMappingData.expectedResponseUnits,
            "responseNormalization": RFMappingData.expectedResponseNormalization,
            "spikeCountDefinition": RFMappingData.expectedSpikeCountDefinition,
            "occupancyTimeDefinition": RFMappingData.expectedOccupancyTimeDefinition,
        ]
        for key in omittingMetadata { metadata.removeValue(forKey: key) }
        metadata.merge(metadataOverrides) { _, replacement in replacement }
        let encoded = try JSONSerialization.data(withJSONObject: metadata)
        var ids = Data()
        for index in unitValues.indices { ids.appendLE(UInt64(41 + index)) }
        var entries = [
            ("metadata", npy(dtype: "|u1", shape: [encoded.count], bytes: encoded)),
            ("unitPool", npy(dtype: "<i8", shape: [unitValues.count], bytes: ids)),
            ("xPositions", npy(dtype: "<f8", shape: [shape[1]], bytes: doubles((0..<shape[1]).map(Double.init)))),
            ("yPositions", npy(dtype: "<f8", shape: [shape[0]], bytes: doubles((0..<shape[0]).map(Double.init)))),
            ("timeBinEdges", npy(dtype: "<f8", shape: [shape[2] + 1],
                bytes: doubles((0...shape[2]).map { Double($0) * 0.1 }))),
            ("occupancyTimeSec", npy(dtype: "<f8", shape: Array(shape.prefix(2)),
                bytes: doubles(occupancy ?? Array(repeating: 1, count: shape[0] * shape[1])))),
        ]
        // Synthetic fixtures use 10 presentations per occupied second and 100-ms bins.
        entries.append(("stimulusPresentationCounts", npy(dtype: "<f8", shape: Array(shape.prefix(2)),
            bytes: doubles((occupancy ?? Array(repeating: 1, count: shape[0] * shape[1])).map { $0 * 10 }))))
        entries += unitValues.enumerated().map { index, values in
            ("unit_\(41 + index)", npy(dtype: "<f8", shape: shape, bytes: doubles(values)))
        }
        return entries
    }

    private func doubles(_ values: [Double]) -> Data {
        var data = Data()
        for value in values { data.appendLE(value.bitPattern) }
        return data
    }

    private func npy(dtype: String, shape: [Int], bytes: Data) -> Data {
        var header = "{'descr': '\(dtype)', 'fortran_order': False, 'shape': ("
            + shape.map(String.init).joined(separator: ", ") + (shape.isEmpty ? "), }" : ",), }")
        header += String(repeating: " ", count: (64 - (10 + header.utf8.count + 1) % 64) % 64) + "\n"
        var result = Data([0x93, 0x4e, 0x55, 0x4d, 0x50, 0x59, 1, 0])
        result.appendLE(UInt16(header.utf8.count))
        result.append(Data(header.utf8))
        result.append(bytes)
        return result
    }

    private func zip(_ entries: [(String, Data)], zip64: Bool = false, corruptCRC: Bool = false) -> Data {
        var result = Data()
        var directory = Data()
        for (key, body) in entries {
            let name = Data((key + ".npy").utf8)
            let checksum = body.withUnsafeBytes { (bytes: UnsafeRawBufferPointer) in
                UInt32(crc32(0, bytes.baseAddress!.assumingMemoryBound(to: Bytef.self), uInt(bytes.count)))
            } ^ (corruptCRC ? 1 : 0)
            let offset = result.count
            result.appendLE(UInt32(0x04034b50))
            result.appendLE(UInt16(20))
            result.append(Data(repeating: 0, count: 8))
            result.appendLE(checksum)
            result.appendLE(UInt32(body.count))
            result.appendLE(UInt32(body.count))
            result.appendLE(UInt16(name.count))
            result.appendLE(UInt16(0))
            result.append(name)
            result.append(body)

            directory.appendLE(UInt32(0x02014b50))
            directory.appendLE(UInt16(20))
            directory.appendLE(UInt16(20))
            directory.append(Data(repeating: 0, count: 8))
            directory.appendLE(checksum)
            directory.appendLE(zip64 ? UInt32.max : UInt32(body.count))
            directory.appendLE(zip64 ? UInt32.max : UInt32(body.count))
            directory.appendLE(UInt16(name.count))
            directory.appendLE(UInt16(zip64 ? 28 : 0))
            directory.append(Data(repeating: 0, count: 10))
            directory.appendLE(zip64 ? UInt32.max : UInt32(offset))
            directory.append(name)
            if zip64 {
                directory.appendLE(UInt16(1))
                directory.appendLE(UInt16(24))
                directory.appendLE(UInt64(body.count))
                directory.appendLE(UInt64(body.count))
                directory.appendLE(UInt64(offset))
            }
        }
        let directoryOffset = result.count
        result.append(directory)
        if zip64 {
            let zip64Offset = result.count
            result.appendLE(UInt32(0x06064b50))
            result.appendLE(UInt64(44))
            result.appendLE(UInt16(45))
            result.appendLE(UInt16(45))
            result.append(Data(repeating: 0, count: 8))
            result.appendLE(UInt64(entries.count))
            result.appendLE(UInt64(entries.count))
            result.appendLE(UInt64(directory.count))
            result.appendLE(UInt64(directoryOffset))
            result.appendLE(UInt32(0x07064b50))
            result.appendLE(UInt32(0))
            result.appendLE(UInt64(zip64Offset))
            result.appendLE(UInt32(1))
        }
        result.appendLE(UInt32(0x06054b50))
        result.appendLE(UInt32(0))
        result.appendLE(zip64 ? UInt16.max : UInt16(entries.count))
        result.appendLE(zip64 ? UInt16.max : UInt16(entries.count))
        result.appendLE(zip64 ? UInt32.max : UInt32(directory.count))
        result.appendLE(zip64 ? UInt32.max : UInt32(directoryOffset))
        result.appendLE(UInt16(0))
        return result
    }
}

private extension Data {
    mutating func appendLE<T: FixedWidthInteger>(_ value: T) {
        for index in 0..<MemoryLayout<T>.size {
            append(UInt8(truncatingIfNeeded: value >> (index * 8)))
        }
    }
}

extension IndexedRFArchiveTests {
    @MainActor
    func testStoreOverlayIntersectionsStayOnOriginalDetectionBins() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let source = try savedSource(directory)
        try savedArchive(shape: [1, 2, 2], mask: [1, 1, 0, 0], center: [0, 1, 0, 0])
            .write(to: SavedRFResult.resultURL(source: source.url))
        try savedArchive(shape: [1, 1, 2], mask: [1, 0], center: [1, 0], axis: "x")
            .write(to: SavedRFResult.resultURL(source: source.url, dimension: "1d"))
        let store = RFMappingStore(initialData: source, loadDefault: false, discoverJSONChoices: false,
            discoverCompanions: false, unitQualityFilterEnabled: false,
            preferences: UserDefaults(suiteName: UUID().uuidString)!)
        store.selectUnitID(42)
        store.reloadSavedResults()
        store.selectedTab = .results
        store.resultsMode = .both
        store.xBins = 1
        store.yBins = 1
        store.normalizeControls()
        let cells = store.savedOverlayCells
        XCTAssertEqual(cells.count, 3)
        XCTAssertEqual(cells.first { $0.y == 0 && $0.x == 0 }?.membership, 3)
        XCTAssertEqual(cells.first { $0.y == 0 && $0.x == 1 }?.membership, 1)
        XCTAssertEqual(cells.first { $0.y == 1 && $0.x == 0 }?.membership, 2)
        XCTAssertNil(cells.first { $0.y == 1 && $0.x == 1 })
        XCTAssertEqual(cells.filter(\.center1D).count, 1)
        store.selectUnitID(17)
        XCTAssertTrue(store.savedOverlayCells.isEmpty)
    }

    func testSavedResultsMatchRecordedIDsAndNativeAxes() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let source = try savedSource(directory)
        let path = SavedRFResult.resultURL(source: source.url)
        try savedArchive(shape: [1, 2, 2], mask: [1, 1, 0, 0], center: [0, 1, 0, 0]).write(to: path)
        let result = try SavedRFResult.load(source: source)
        XCTAssertNil(result.forUnit(17))
        XCTAssertEqual(result.forUnit(42)?.mask, [[true, true], [false, false]])
        XCTAssertEqual(result.nativeCenter(for: 42), [[false, true], [false, false]])
        XCTAssertEqual(result.timeRangeSeconds, [0, 0.2])
        XCTAssertNil(result.summaryWarning)
        XCTAssertTrue(result.diagnostics(for: 42).contains("fixture-key"))
    }

    func testNativeOneDimensionalResultsExpandAlongTheirDeclaredAxis() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let source = try savedSource(directory)
        let path = SavedRFResult.resultURL(source: source.url, dimension: "1d", polarity: "inhibitory")
        try savedArchive(shape: [1, 1, 2], mask: [1, 0], center: [1, 0], axis: "x", polarity: "inhibitory").write(to: path)
        let result = try SavedRFResult.load(source: source, dimension: "1d", polarity: "inhibitory")
        XCTAssertEqual(result.nativeMask(for: 42), [[true, false], [true, false]])
        XCTAssertEqual(result.forUnit(42)?.mask, [[true, false]])
        XCTAssertFalse(result.projectedFrom2D)
        try savedArchive(shape: [1, 2, 1], mask: [0, 1], center: [0, 1], axis: "y", polarity: "inhibitory").write(to: path)
        let vertical = try SavedRFResult.load(source: source, dimension: "1d", polarity: "inhibitory")
        XCTAssertEqual(vertical.nativeMask(for: 42), [[false, false], [true, true]])
    }

    func testSavedResultRejectsMisalignedIDsAxesCentersAndTiming() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let source = try savedSource(directory)
        let path = SavedRFResult.resultURL(source: source.url)
        for candidate in [
            try savedArchive(shape: [1, 2, 2], mask: [1, 0, 0, 0], center: [0, 1, 0, 0]),
            try savedArchive(shape: [1, 1, 4], mask: [1, 0, 0, 0], center: [1, 0, 0, 0]),
            try savedArchive(shape: [1, 2, 2], mask: [1, 0, 0, 0], center: [1, 0, 0, 0], unitID: 99),
            try savedArchive(shape: [1, 2, 2], mask: [1, 0, 0, 0], center: [1, 0, 0, 0], timeRange: [0.01, 0.2]),
        ] {
            try candidate.write(to: path)
            XCTAssertThrowsError(try SavedRFResult.load(source: source))
        }
    }

    func testLegacyOneDimensionalProjectionRequiresMatchingSummary() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let source = try savedSource(directory)
        let path = SavedRFResult.resultURL(source: source.url, dimension: "1d")
        try savedArchive(shape: [1, 2, 2], mask: [0, 0, 1, 0], center: [0, 0, 1, 0]).write(to: path)
        XCTAssertThrowsError(try SavedRFResult.load(source: source, dimension: "1d"))
        var summary: [String: Any] = [
            "source_path": "/original/location/fixture.rfmap",
            "parameters": ["rf_type": "excitatory", "time_range_s": [0, 0.2], "collapse_from_2d": true,
                           "cluster_forming_z_1d": 2.0],
            "kept_unit_ids": [42], "output_paths": ["result_1d": path.path],
            "bin_qc": ["unit_ids": [17, 42], "zero_bins": [1, 0], "valid_bins": [3, 4], "keep": [false, true]],
        ]
        let summaryURL = directory.appendingPathComponent("fixture_analysis.json")
        try JSONSerialization.data(withJSONObject: summary).write(to: summaryURL)
        let result = try SavedRFResult.load(source: source, dimension: "1d")
        XCTAssertTrue(result.projectedFrom2D)
        XCTAssertEqual(result.forUnit(42)?.mask, [[true, false]])
        XCTAssertTrue(result.diagnostics(for: 42).contains("Saved QC"))
        summary["kept_unit_ids"] = [17]
        try JSONSerialization.data(withJSONObject: summary).write(to: summaryURL)
        XCTAssertThrowsError(try SavedRFResult.load(source: source, dimension: "1d"))
    }

    func testIndexedPresentationExposureAndInvalidPresentationCounts() throws {
        let source = try RFMappingData(data: makeArchive(unitValues: [[2, 6]], shape: [1, 1, 2]), url: url)
        XCTAssertEqual(source.stimulusPresentationCounts, [[10]])
        XCTAssertEqual(try source.responseValue(unitIndex: 0, yIndex: 0, xIndex: 0, start: 0, end: 1,
                                                valueMode: .meanFiringRate), 4)
        var entries = try makeEntries(unitValues: [[2]])
        let index = try XCTUnwrap(entries.firstIndex { $0.0 == "stimulusPresentationCounts" })
        entries[index].1 = npy(dtype: "<f8", shape: [1, 1], bytes: doubles([0.5]))
        XCTAssertThrowsError(try RFMappingData(data: zip(entries), url: url))
    }

    func testStaleAnalysisSummaryCannotOverrideSavedTwoDimensionalResult() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let source = try savedSource(directory)
        try savedArchive(shape: [1, 2, 2], mask: [1, 0, 0, 0], center: [1, 0, 0, 0])
            .write(to: SavedRFResult.resultURL(source: source.url))
        try Data(#"{"source_path":"different.rfmap"}"#.utf8)
            .write(to: directory.appendingPathComponent("fixture_analysis.json"))
        let result = try SavedRFResult.load(source: source)
        XCTAssertEqual(result.forUnit(42)?.mask, [[true, false], [false, false]])
        XCTAssertNotNil(result.summaryWarning)
    }

    private func savedSource(_ directory: URL) throws -> RFMappingData {
        let payload = currentRFSchemaPayload([
            "unitsSpikeCounts": [[[[1, 2], [2, 3]], [[3, 4], [4, 5]]], [[[2, 1], [3, 2]], [[4, 3], [5, 4]]]],
            "unitsSpikeCountsSize": [2, 2, 2, 2], "unitPool": [17, 42],
            "xPositions": [-10, 10], "yPositions": [5, 15], "timeBinEdges": [0, 0.1, 0.2],
            "stimulusPresentationCounts": [[2, 4], [6, 8]],
        ], occupancyTimeSec: [[1, 1], [1, 1]], occupancyTimeSecSize: [2, 2])
        return try RFMappingData(data: JSONSerialization.data(withJSONObject: payload), url: directory.appendingPathComponent("fixture.rfmap"))
    }

    private func savedArchive(shape: [Int], mask: [UInt8], center: [UInt8], axis: String? = nil,
                              polarity: String = "excitatory", unitID: Int = 42, timeRange: [Double] = [0, 0.2]) throws -> Data {
        var manifest: [String: Any] = ["schema_name": "rfmapping-rf-result", "grid_shape": Array(shape.dropFirst()),
            "storage_shape": shape, "time_range_s": timeRange,
            "parameters": ["alternative": polarity == "inhibitory" ? "less" : "greater", "cluster_forming_z": 2.0]]
        if let axis { manifest["collapse_axis"] = axis }
        let manifestData = try JSONSerialization.data(withJSONObject: manifest, options: [.sortedKeys, .withoutEscapingSlashes])
        func textArray(_ string: String) -> Data {
            var bytes = Data()
            for scalar in string.unicodeScalars { bytes.appendLE(scalar.value) }
            return npy(dtype: "<U\(string.unicodeScalars.count)", shape: [], bytes: bytes)
        }
        var id = Data(); id.appendLE(Int64(unitID))
        return zip([
            ("schema_version", npy(dtype: "|u1", shape: [], bytes: Data([1]))),
            ("mask_2d", npy(dtype: "|b1", shape: shape, bytes: Data(mask))),
            ("center_2d", npy(dtype: "|u1", shape: shape, bytes: Data(center))),
            ("unit_ids", npy(dtype: "<i8", shape: [1], bytes: id)),
            ("manifest_json", textArray(String(data: manifestData, encoding: .utf8)!)),
            ("cache_key", textArray("fixture-key")),
        ])
    }
}
