import Foundation
import CoreFoundation
import zlib

/// A read-only NPZ directory. Each request inflates just one NPY entry; no
/// extraction, Python runtime, or shared mutable ZIP cursor is involved.
struct IndexedRFArchive: Sendable {
    private struct Entry: Sendable {
        let method: UInt64
        let crc: UInt64
        let compressedSize: Int
        let size: Int
        let offset: Int
    }

    private let data: Data
    private let entries: [String: Entry]

    static func isArchive(_ data: Data) -> Bool {
        data.count >= 4 && data.prefix(4) == Data([0x50, 0x4b, 0x03, 0x04])
    }

    init(data: Data) throws {
        self.data = data
        entries = try Self.readDirectory(data)
    }

    /// The existing JSON header decoder remains the single schema authority.
    /// Counts are supplied separately, in source order, as units are loaded.
    func headerJSON() throws -> Data {
        let metadata = try array(named: "metadata")
        guard metadata.kind == "u", metadata.itemSize == 1,
              metadata.shape.count == 1 else {
            throw invalid("metadata must be a one-dimensional UTF-8 uint8 array.")
        }
        guard var header = try JSONSerialization.jsonObject(with: metadata.bytes)
                as? [String: Any],
              let version = header["formatVersion"] as? NSNumber,
              CFGetTypeID(version) != CFBooleanGetTypeID(), version == 2 else {
            throw invalid("Unsupported indexed .rfmap formatVersion; expected 2.")
        }
        let units = try array(named: "unitPool")
        guard units.shape.count == 1, units.kind == "i" || units.kind == "u" else {
            throw invalid("unitPool must be a one-dimensional integer array.")
        }
        let unitIDs = try units.integerValues()
        guard Set(unitIDs).count == unitIDs.count else {
            throw invalid("unitPool IDs must be unique.")
        }
        for id in unitIDs where entries["unit_\(id)"] == nil {
            throw invalid("Missing unit array unit_\(id).")
        }
        header["unitPool"] = unitIDs
        for name in ["xPositions", "yPositions", "timeBinEdges"] {
            let vector = try array(named: name)
            guard vector.shape.count == 1 else {
                throw invalid("\(name) must be a one-dimensional numeric array.")
            }
            header[name] = try vector.doubleValues()
        }
        let occupancy = try array(named: "occupancyTimeSec")
        guard occupancy.shape.count == 2 else {
            throw invalid("occupancyTimeSec must be a two-dimensional numeric array.")
        }
        let values = try occupancy.doubleValues()
        header["occupancyTimeSec"] = (0..<occupancy.shape[0]).map { y in
            Array(values[(y * occupancy.shape[1])..<((y + 1) * occupancy.shape[1])])
        }
        header["unitsSpikeCounts"] = [Any]()
        return try JSONSerialization.data(withJSONObject: header)
    }

    func counts(unitID: Int, shape: [Int]) throws -> [[[Double]]] {
        try Task.checkCancellation()
        let unit = try array(named: "unit_\(unitID)")
        guard unit.shape == shape else {
            throw invalid("Unit \(unitID) has shape \(unit.shape); expected \(shape).")
        }
        let values = try unit.doubleValues()
        // uint64 is the indexed format's maximum lossless count range.
        guard values.allSatisfy({ $0 >= 0 && $0 < 18_446_744_073_709_551_616.0
            && $0 == $0.rounded() }) else {
            throw invalid("Unit \(unitID) counts must be non-negative integers representable as uint64.")
        }
        return (0..<shape[0]).map { y in
            (0..<shape[1]).map { x in
                let start = (y * shape[1] + x) * shape[2]
                return Array(values[start..<(start + shape[2])])
            }
        }
    }

    private func array(named name: String) throws -> RFNPYArray {
        guard let entry = entries[name] else { throw invalid("Missing NPZ entry \(name).") }
        let local = entry.offset
        guard try data.integer(at: local, width: 4) == 0x04034b50,
              try data.integer(at: local + 8, width: 2) == entry.method,
              try data.integer(at: local + 6, width: 2) & 1 == 0 else {
            throw invalid("Invalid or encrypted ZIP header for \(name).")
        }
        let nameLength = try data.int(at: local + 26, width: 2)
        let extraLength = try data.int(at: local + 28, width: 2)
        let start = try checkedSum(local, 30, nameLength, extraLength)
        let end = try checkedSum(start, entry.compressedSize)
        guard end <= data.count else { throw invalid("Truncated ZIP entry \(name).") }
        let compressed = data.subdata(in: start..<end)
        let unpacked: Data
        switch entry.method {
        case 0:
            guard compressed.count == entry.size else { throw invalid("Invalid stored ZIP size.") }
            unpacked = compressed
        case 8:
            unpacked = try Self.inflate(compressed, size: entry.size)
        default:
            throw invalid("Unsupported ZIP compression method \(entry.method).")
        }
        let crc = try unpacked.withUnsafeBytes { (bytes: UnsafeRawBufferPointer) -> UInt64 in
            var checksum = crc32(0, nil, 0)
            for start in stride(from: 0, to: bytes.count, by: 1_048_576) {
                try Task.checkCancellation()
                let length = min(1_048_576, bytes.count - start)
                checksum = crc32(checksum, bytes.baseAddress!.advanced(by: start)
                    .assumingMemoryBound(to: Bytef.self), uInt(length))
            }
            return UInt64(checksum)
        }
        guard crc == entry.crc else { throw invalid("ZIP checksum mismatch for \(name).") }
        return try RFNPYArray(data: unpacked)
    }

    private static func inflate(_ source: Data, size: Int) throws -> Data {
        var stream = z_stream()
        guard inflateInit2_(&stream, -MAX_WBITS, ZLIB_VERSION,
                            Int32(MemoryLayout<z_stream>.size)) == Z_OK else {
            throw invalid("Could not initialize ZIP decompression.")
        }
        defer { inflateEnd(&stream) }
        // The extra byte distinguishes an exact-size stream from a truncated
        // output buffer without trusting the ZIP header's declared size.
        var output = Data(count: try checkedSum(size, 1))
        try source.withUnsafeBytes { (input: UnsafeRawBufferPointer) in
            try output.withUnsafeMutableBytes { (destination: UnsafeMutableRawBufferPointer) in
                var inputOffset = 0
                var outputOffset = 0
                while true {
                    try Task.checkCancellation()
                    if stream.avail_in == 0 && inputOffset < input.count {
                        let length = min(1_048_576, input.count - inputOffset)
                        stream.next_in = UnsafeMutablePointer(mutating: input.baseAddress!
                            .advanced(by: inputOffset).assumingMemoryBound(to: Bytef.self))
                        stream.avail_in = uInt(length)
                        inputOffset += length
                    }
                    let length = min(1_048_576, destination.count - outputOffset)
                    guard length > 0 else { throw invalid("ZIP entry exceeds its declared size.") }
                    stream.next_out = destination.baseAddress!.advanced(by: outputOffset)
                        .assumingMemoryBound(to: Bytef.self)
                    stream.avail_out = uInt(length)
                    let status = zlib.inflate(&stream, Z_NO_FLUSH)
                    outputOffset += length - Int(stream.avail_out)
                    if status == Z_STREAM_END {
                        guard outputOffset == size,
                              inputOffset - Int(stream.avail_in) == input.count else {
                            throw invalid("ZIP entry size does not match its compressed stream.")
                        }
                        break
                    }
                    guard status == Z_OK,
                          stream.avail_in > 0 || inputOffset < input.count
                            || stream.avail_out == 0 else {
                        throw invalid("Corrupt or truncated ZIP deflate stream.")
                    }
                }
            }
        }
        output.removeLast()
        return output
    }

    private static func readDirectory(_ data: Data) throws -> [String: Entry] {
        guard data.count >= 22 else { throw invalid("Truncated ZIP archive.") }
        var end: Int?
        for offset in stride(from: data.count - 22, through: max(0, data.count - 65_557), by: -1) {
            if try data.integer(at: offset, width: 4) == 0x06054b50,
               try checkedSum(offset, 22, data.int(at: offset + 20, width: 2)) == data.count {
                end = offset
                break
            }
        }
        guard let end else { throw invalid("ZIP directory was not found.") }
        guard try data.int(at: end + 4, width: 2) == 0,
              try data.int(at: end + 6, width: 2) == 0,
              try data.int(at: end + 8, width: 2) == data.int(at: end + 10, width: 2) else {
            throw invalid("Multi-disk ZIP archives are unsupported.")
        }
        var count = try data.int(at: end + 10, width: 2)
        var size = try data.int(at: end + 12, width: 4)
        var offset = try data.int(at: end + 16, width: 4)
        if count == 0xffff || size == 0xffffffff || offset == 0xffffffff {
            guard end >= 20, try data.integer(at: end - 20, width: 4) == 0x07064b50,
                  try data.integer(at: end - 16, width: 4) == 0,
                  try data.integer(at: end - 4, width: 4) == 1 else {
                throw invalid("Missing ZIP64 directory locator.")
            }
            let zip64 = try data.int(at: end - 12, width: 8)
            guard try data.integer(at: zip64, width: 4) == 0x06064b50,
                  try data.integer(at: zip64 + 16, width: 4) == 0,
                  try data.integer(at: zip64 + 20, width: 4) == 0,
                  try data.integer(at: zip64 + 24, width: 8)
                    == data.integer(at: zip64 + 32, width: 8) else {
                throw invalid("Invalid ZIP64 directory.")
            }
            count = try data.int(at: zip64 + 32, width: 8)
            size = try data.int(at: zip64 + 40, width: 8)
            offset = try data.int(at: zip64 + 48, width: 8)
        }
        let directoryEnd = try checkedSum(offset, size)
        guard directoryEnd <= end, count <= size / 46 else {
            throw invalid("Invalid ZIP directory bounds.")
        }
        var result: [String: Entry] = [:]
        for _ in 0..<count {
            try Task.checkCancellation()
            guard try data.integer(at: offset, width: 4) == 0x02014b50,
                  try data.integer(at: offset + 8, width: 2) & 1 == 0 else {
                throw invalid("Invalid or encrypted ZIP directory entry.")
            }
            let nameLength = try data.int(at: offset + 28, width: 2)
            let extraLength = try data.int(at: offset + 30, width: 2)
            let commentLength = try data.int(at: offset + 32, width: 2)
            let nameStart = try checkedSum(offset, 46)
            let extraStart = try checkedSum(nameStart, nameLength)
            let extraEnd = try checkedSum(extraStart, extraLength)
            let next = try checkedSum(extraEnd, commentLength)
            guard next <= directoryEnd,
                  let name = String(data: data.subdata(in: nameStart..<extraStart), encoding: .utf8) else {
                throw invalid("Invalid ZIP entry name or bounds.")
            }
            var unpacked = try data.int(at: offset + 24, width: 4)
            var compressed = try data.int(at: offset + 20, width: 4)
            var local = try data.int(at: offset + 42, width: 4)
            var disk = try data.int(at: offset + 34, width: 2)
            if [unpacked, compressed, local].contains(0xffffffff) || disk == 0xffff {
                var extra = extraStart
                var found = false
                while extra + 4 <= extraEnd {
                    let tag = try data.int(at: extra, width: 2)
                    let length = try data.int(at: extra + 2, width: 2)
                    let fieldEnd = try checkedSum(extra, 4, length)
                    guard fieldEnd <= extraEnd else { throw invalid("Truncated ZIP extra field.") }
                    if tag == 1 {
                        var cursor = extra + 4
                        func nextValue(_ width: Int) throws -> Int {
                            guard cursor + width <= fieldEnd else { throw invalid("Truncated ZIP64 size.") }
                            defer { cursor += width }
                            return try data.int(at: cursor, width: width)
                        }
                        if unpacked == 0xffffffff { unpacked = try nextValue(8) }
                        if compressed == 0xffffffff { compressed = try nextValue(8) }
                        if local == 0xffffffff { local = try nextValue(8) }
                        if disk == 0xffff { disk = try nextValue(4) }
                        found = true
                        break
                    }
                    extra = fieldEnd
                }
                guard found else { throw invalid("Missing ZIP64 entry sizes.") }
            }
            guard disk == 0, local < offset else { throw invalid("Invalid ZIP entry location.") }
            let key = name.hasSuffix(".npy") ? String(name.dropLast(4)) : name
            guard result[key] == nil else { throw invalid("Duplicate NPZ entry \(key).") }
            result[key] = Entry(
                method: try data.integer(at: offset + 10, width: 2),
                crc: try data.integer(at: offset + 16, width: 4),
                compressedSize: compressed,
                size: unpacked,
                offset: local
            )
            offset = next
        }
        guard offset == directoryEnd else { throw invalid("Unexpected data in ZIP directory.") }
        return result
    }
}

/// NPY's axis order is independent of its C/Fortran byte order. Decode the
/// strides explicitly, retaining singleton axes and the full time dimension.
private struct RFNPYArray {
    let shape: [Int]
    let kind: Character
    let itemSize: Int
    let bytes: Data
    private let littleEndian: Bool
    private let fortranOrder: Bool

    init(data: Data) throws {
        guard data.count >= 10, data.prefix(6) == Data([0x93, 0x4e, 0x55, 0x4d, 0x50, 0x59]) else {
            throw invalid("Invalid NPY magic bytes.")
        }
        let major = data[6]
        guard (1...3).contains(major), data[7] == 0 else { throw invalid("Unsupported NPY version.") }
        let start = major == 1 ? 10 : 12
        let headerLength = try data.int(at: 8, width: major == 1 ? 2 : 4)
        let body = try checkedSum(start, headerLength)
        guard body <= data.count,
              let header = String(data: data.subdata(in: start..<body),
                                  encoding: major == 3 ? .utf8 : .isoLatin1) else {
            throw invalid("Truncated NPY header.")
        }
        func field(_ pattern: String) throws -> String {
            let regex = try NSRegularExpression(pattern: pattern)
            let range = NSRange(header.startIndex..<header.endIndex, in: header)
            let matches = regex.matches(in: header, range: range)
            guard matches.count == 1,
                  let match = matches.first,
                  let value = Range(match.range(at: 1), in: header) else {
                throw invalid("Missing or invalid NPY header field.")
            }
            return String(header[value])
        }
        let descriptor = Array(try field("['\"]descr['\"]\\s*:\\s*['\"]([^'\"]+)['\"]"))
        guard descriptor.count >= 3, "<>=|".contains(descriptor[0]),
              "uif".contains(descriptor[1]),
              let width = Int(String(descriptor.dropFirst(2))),
              [1, 2, 4, 8].contains(width),
              descriptor[1] != "f" || [4, 8].contains(width),
              descriptor[0] != "|" || width == 1 else {
            throw invalid("Unsupported NPY dtype; expected real integer or float data.")
        }
        kind = descriptor[1]
        itemSize = width
        littleEndian = descriptor[0] != ">"
        fortranOrder = try field("['\"]fortran_order['\"]\\s*:\\s*(True|False)") == "True"
        let dimensions = try field("['\"]shape['\"]\\s*:\\s*\\(([^)]*)\\)")
        let parts = dimensions.split(separator: ",", omittingEmptySubsequences: false)
        var parsed: [Int] = []
        for (index, part) in parts.enumerated() {
            let token = part.trimmingCharacters(in: .whitespacesAndNewlines)
            if token.isEmpty && index == parts.count - 1 { continue }
            guard let value = Int(token), value > 0 else { throw invalid("Invalid NPY dimensions.") }
            parsed.append(value)
        }
        guard !parsed.isEmpty else { throw invalid("NPY scalar arrays are unsupported.") }
        shape = parsed
        var count = 1
        for dimension in shape {
            let product = count.multipliedReportingOverflow(by: dimension)
            guard !product.overflow else { throw invalid("NPY dimensions overflow.") }
            count = product.partialValue
        }
        let byteCount = count.multipliedReportingOverflow(by: itemSize)
        guard !byteCount.overflow, byteCount.partialValue == data.count - body else {
            throw invalid("NPY data size does not match its declared dimensions.")
        }
        bytes = data.subdata(in: body..<data.count)
    }

    func integerValues() throws -> [Int] {
        guard kind == "i" || kind == "u" else { throw invalid("Expected integer NPY data.") }
        return try decode { raw in
            if kind == "i" {
                let signed = signedValue(raw)
                guard let result = Int(exactly: signed) else { throw invalid("Unit ID exceeds Int range.") }
                return result
            }
            guard let result = Int(exactly: raw) else { throw invalid("Unit ID exceeds Int range.") }
            return result
        }
    }

    func doubleValues() throws -> [Double] {
        try decode { raw in
            let value: Double
            switch kind {
            case "f":
                value = itemSize == 8 ? Double(bitPattern: raw) : Double(Float(bitPattern: UInt32(raw)))
            case "i":
                let integer = signedValue(raw)
                value = Double(integer)
                guard Int64(exactly: value) == integer else {
                    throw invalid("Integer NPY value cannot be represented losslessly as a viewer count.")
                }
            default:
                value = Double(raw)
                guard UInt64(exactly: value) == raw else {
                    throw invalid("Integer NPY value cannot be represented losslessly as a viewer count.")
                }
            }
            guard value.isFinite else { throw invalid("NPY values must be finite.") }
            return value
        }
    }

    private func signedValue(_ raw: UInt64) -> Int64 {
        let shift = 64 - itemSize * 8
        return Int64(bitPattern: raw << shift) >> shift
    }

    private func decode<T>(_ convert: (UInt64) throws -> T) throws -> [T] {
        let count = bytes.count / itemSize
        return try bytes.withUnsafeBytes { (buffer: UnsafeRawBufferPointer) in
            var result: [T] = []
            result.reserveCapacity(count)
            for index in 0..<count {
                if index.isMultiple(of: 65_536) { try Task.checkCancellation() }
                var storageIndex = index
                if fortranOrder && shape.count > 1 {
                    var remainder = index
                    var stride = count
                    storageIndex = 0
                    for axis in shape.indices.reversed() {
                        stride /= shape[axis]
                        storageIndex += (remainder % shape[axis]) * stride
                        remainder /= shape[axis]
                    }
                }
                let base = storageIndex * itemSize
                var raw: UInt64 = 0
                for byte in 0..<itemSize {
                    let shift = (littleEndian ? byte : itemSize - 1 - byte) * 8
                    raw |= UInt64(buffer[base + byte]) << shift
                }
                result.append(try convert(raw))
            }
            return result
        }
    }
}

private func invalid(_ message: String) -> RFMappingError {
    RFMappingError.invalidData("Indexed RF map: \(message)")
}

private func checkedSum(_ values: Int...) throws -> Int {
    try values.reduce(0) { partial, value in
        let result = partial.addingReportingOverflow(value)
        guard value >= 0, !result.overflow else { throw invalid("Archive offset overflow.") }
        return result.partialValue
    }
}

private extension Data {
    func integer(at offset: Int, width: Int) throws -> UInt64 {
        guard offset >= 0, width <= count, offset <= count - width else {
            throw invalid("Truncated archive header.")
        }
        var result: UInt64 = 0
        for byte in 0..<width { result |= UInt64(self[offset + byte]) << (byte * 8) }
        return result
    }

    func int(at offset: Int, width: Int) throws -> Int {
        guard let value = Int(exactly: try integer(at: offset, width: width)) else {
            throw invalid("Archive size exceeds platform limits.")
        }
        return value
    }
}
