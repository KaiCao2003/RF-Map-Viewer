import CoreGraphics
import Foundation

enum ADNSelection {
    private struct Row {
        let probe: String
        let unitID: Int
    }

    private static let magic = Data([0x93, 0x4e, 0x55, 0x4d, 0x50, 0x59])
    private static let rowSize = 16 * 4 + 8

    static func selectionURL(forRFURL sourceURL: URL) -> URL? {
        var directory = sourceURL.standardizedFileURL.deletingLastPathComponent()
        while true {
            if directory.lastPathComponent.range(of: #"^\d{6}_\d+$"#, options: .regularExpression) != nil {
                return directory.appendingPathComponent("data/adn_neuron.npy")
            }
            let parent = directory.deletingLastPathComponent()
            if parent.path == directory.path { return nil }
            directory = parent
        }
    }

    static func load(from url: URL, probeName: String) throws -> Set<Int> {
        Set(try rows(from: url).filter { $0.probe == probeName }.map(\.unitID))
    }

    static func save(_ unitIDs: Set<Int>, probeName: String, to url: URL) throws {
        guard probeName.unicodeScalars.count <= 16 else {
            throw RFMappingError.invalidData("ADN probe names must fit the U16 field.")
        }
        let preserved = try rows(from: url).filter { $0.probe != probeName }
        let selected = unitIDs.sorted().map { Row(probe: probeName, unitID: $0) }
        let rows = preserved + selected
        var header = "{'descr': [('probe', '<U16'), ('unit_id', '<i8')], 'fortran_order': False, 'shape': (\(rows.count),), }"
        // NPY v1 headers include the ten-byte prefix in their 64-byte alignment.
        header += String(repeating: " ", count: 64 - (10 + header.utf8.count + 1) % 64) + "\n"
        var data = magic
        data.append(contentsOf: [1, 0])
        append(UInt64(header.utf8.count), width: 2, to: &data)
        data.append(contentsOf: header.utf8)
        for row in rows {
            let scalars = Array(row.probe.unicodeScalars)
            for index in 0..<16 {
                append(index < scalars.count ? UInt64(scalars[index].value) : 0, width: 4, to: &data)
            }
            append(UInt64(bitPattern: Int64(row.unitID)), width: 8, to: &data)
        }
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: url, options: .atomic)
    }

    static func unitIDs(in polygon: [CGPoint], geometry: ProbeGeometry) -> Set<Int> {
        guard polygon.count >= 3, polygon.allSatisfy({ $0.x.isFinite && $0.y.isFinite }) else {
            return []
        }
        return Set(geometry.positionedUnits.compactMap { unit in
            guard let position = unit.position, position.x.isFinite, position.y.isFinite else { return nil }
            let x = CGFloat(position.x)
            let y = CGFloat(position.y)
            var inside = false
            for index in polygon.indices {
                let start = polygon[index]
                let end = polygon[(index + 1) % polygon.count]
                let dx = end.x - start.x
                let dy = end.y - start.y
                let length = hypot(dx, dy)
                if length == 0 { continue }
                let cross = (x - start.x) * dy - (y - start.y) * dx
                let along = ((x - start.x) * dx + (y - start.y) * dy) / length
                // Match Python's boundary tolerance in physical micrometres.
                if abs(cross) <= 1e-9 * length, along >= -1e-9, along <= length + 1e-9 {
                    return unit.unitID
                }
                if (start.y > y) != (end.y > y), x < start.x + (y - start.y) * dx / dy {
                    inside.toggle()
                }
            }
            return inside ? unit.unitID : nil
        })
    }

    private static func rows(from url: URL) throws -> [Row] {
        guard FileManager.default.fileExists(atPath: url.path) else { return [] }
        let data = try Data(contentsOf: url)
        let invalid = RFMappingError.invalidData(
            "ADN selection must be a one-dimensional NumPy array with probe (U16) and unit_id (int64) fields."
        )
        guard data.count >= 10, data.prefix(6) == magic,
              (1...3).contains(data[6]), data[7] == 0 else { throw invalid }
        let headerStart = data[6] == 1 ? 10 : 12
        guard data.count >= headerStart else { throw invalid }
        let headerLength = Int(integer(in: data, at: 8, width: headerStart - 8))
        guard headerLength <= data.count - headerStart else { throw invalid }
        let body = headerStart + headerLength
        guard let header = String(data: data.subdata(in: headerStart..<body), encoding: .utf8) else {
            throw invalid
        }
        // This codec deliberately handles only the shared ADN schema, not RF arrays.
        let pattern = #"^\s*\{\s*['"]descr['"]\s*:\s*\["#
            + #"\s*\(\s*['"]probe['"]\s*,\s*['"]<U16['"]\s*\)\s*,"#
            + #"\s*\(\s*['"]unit_id['"]\s*,\s*['"]<i8['"]\s*\)\s*\]\s*,"#
            + #"\s*['"]fortran_order['"]\s*:\s*(?:False|True)\s*,"#
            + #"\s*['"]shape['"]\s*:\s*\(\s*(\d+)\s*,\s*\)\s*,?\s*\}\s*$"#
        let expression = try NSRegularExpression(pattern: pattern)
        guard let match = expression.firstMatch(in: header, range: NSRange(header.startIndex..., in: header)),
              let countRange = Range(match.range(at: 1), in: header),
              let count = Int(header[countRange]),
              (data.count - body) % rowSize == 0,
              count == (data.count - body) / rowSize else { throw invalid }
        return try (0..<count).map { index in
            let offset = body + index * rowSize
            var scalars = (0..<16).map { UInt32(integer(in: data, at: offset + $0 * 4, width: 4)) }
            while scalars.last == 0 { scalars.removeLast() }
            var probe = String.UnicodeScalarView()
            for value in scalars {
                guard let scalar = UnicodeScalar(value) else { throw invalid }
                probe.append(scalar)
            }
            let unitID = Int(Int64(bitPattern: integer(in: data, at: offset + 64, width: 8)))
            return Row(probe: String(probe), unitID: unitID)
        }
    }

    private static func integer(in data: Data, at offset: Int, width: Int) -> UInt64 {
        (0..<width).reduce(0) { $0 | UInt64(data[offset + $1]) << ($1 * 8) }
    }

    private static func append(_ value: UInt64, width: Int, to data: inout Data) {
        for byte in 0..<width {
            data.append(UInt8(truncatingIfNeeded: value >> (byte * 8)))
        }
    }
}
