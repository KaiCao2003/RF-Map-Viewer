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
        XCTAssertEqual(try data.responseMatrix(unitIndex: 1, start: 0, end: 2,
                                               valueMode: .meanFiringRate), [[7.5, 24], [nil, 8.25]])
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
        unitValues: [[Double]], shape: [Int] = [1, 1, 1], occupancy: [Double]? = nil
    ) throws -> [(String, Data)] {
        let metadata: [String: Any] = [
            "formatVersion": 2,
            "unitsSpikeCountsSize": [unitValues.count] + shape,
            "occupancyTimeSecSize": Array(shape.prefix(2)),
            "responseUnits": RFMappingData.expectedResponseUnits,
            "responseNormalization": RFMappingData.expectedResponseNormalization,
            "spikeCountDefinition": RFMappingData.expectedSpikeCountDefinition,
            "occupancyTimeDefinition": RFMappingData.expectedOccupancyTimeDefinition,
        ]
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
            + shape.map(String.init).joined(separator: ", ") + ",), }"
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
