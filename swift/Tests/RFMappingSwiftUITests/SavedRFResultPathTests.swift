import Foundation
import XCTest
@testable import RFMappingSwiftUI

final class SavedRFResultPathTests: XCTestCase {
    func testKindDirectoriesUseAllFourShortNames() throws {
        for kind in ["regular", "bar_3deg", "bar_3.5deg", "rotation", "rotation_30", "egocentric",
                     "egocentric_30", "allocentric_pixelbins", "allocentric_pixelbins_30", "free_moving"] {
            for suffix in ["rfmap", "json", "JSON"] {
                let source = URL(fileURLWithPath: "/recording/\(kind)/\(kind)_261026_2.\(suffix)")
                for dimension in ["1d", "2d"] {
                    for polarity in ["excitatory", "inhibitory"] {
                        let path = try SavedRFResult.resultURL(source: source, dimension: dimension, polarity: polarity)
                        XCTAssertEqual(path.lastPathComponent, "rf_\(dimension)_\(polarity == "inhibitory" ? "inh" : "ext").npz")
                    }
                }
            }
        }
    }

    func testDefaultPatternRequiresExactParentAndStemMatch() throws {
        for filename in ["regular/regular_261026_2_extra.rfmap", "regular/bar_3deg_261026_2.rfmap",
                         "prefix_regular/regular_261026_2.rfmap", "regular/regular_20261026_2.rfmap",
                         "other/other_261026_2.rfmap", "regular/regular_261026_2.txt"] {
            let source = URL(fileURLWithPath: "/recording/" + filename)
            XCTAssertEqual(try SavedRFResult.resultURL(source: source), source.deletingPathExtension().appendingPathExtension("npz"))
        }
    }

    func testCustomPatternReplacesTheDefaultAndRequiresFullMatch() throws {
        let pattern = #"horizontal-test/scan-\d{8}-run\d+"#
        let custom = URL(fileURLWithPath: "/recording/horizontal-test/scan-20261026-run2.rfmap")
        XCTAssertEqual(try SavedRFResult.resultURL(source: custom, layoutPattern: pattern).lastPathComponent, "rf_2d_ext.npz")
        let duplicate = custom.deletingLastPathComponent().appendingPathComponent("scan-20261026-run2-copy.rfmap")
        XCTAssertEqual(try SavedRFResult.resultURL(source: duplicate, layoutPattern: pattern).lastPathComponent,
                       "scan-20261026-run2-copy.npz")
        let regular = URL(fileURLWithPath: "/recording/regular/regular_261026_2.rfmap")
        XCTAssertEqual(try SavedRFResult.resultURL(source: regular, layoutPattern: pattern).lastPathComponent, "regular_261026_2.npz")
    }

    func testInvalidLayoutAndResultInputsAreRejected() {
        for pattern in ["", "   ", "[", #"\k<missing>"#] {
            XCTAssertThrowsError(try SavedRFResult.validateLayoutPattern(pattern))
        }
        XCTAssertThrowsError(try SavedRFResult.resultURL(source: URL(fileURLWithPath: "/tmp/rf_2d_ext.npz")))
        XCTAssertThrowsError(try SavedRFResult.resultURL(source: URL(fileURLWithPath: "/tmp/input.rfmap"), dimension: "3d"))
    }
}

extension IndexedRFArchiveTests {
    func testKindDirectorySummaryUsesDimensionSpecificDropBins() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            .appendingPathComponent("regular")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory.deletingLastPathComponent()) }
        let source = try savedSource(directory, filename: "regular_261026_2.rfmap")
        let path = try SavedRFResult.resultURL(source: source.url)
        try savedArchive(shape: [1, 2, 2], mask: [1, 0, 0, 0], center: [1, 0, 0, 0], manifestOverrides: [
            "parameters": ["alternative": "greater", "cluster_forming_z": 2, "drop_bins": 3],
        ]).write(to: path)
        var summary: [String: Any] = [
            "source_path": "/original/regular_261026_2.rfmap",
            "parameters": ["rf_type": "excitatory", "time_range_s": [0, 0.2],
                           "cluster_forming_z_2d": 2, "drop_bins": 99, "drop_bins_2d": 3],
            "kept_unit_ids": [42], "output_paths": ["result_2d": path.path],
            "bin_qc": ["unit_ids": [17, 42], "zero_bins": [1, 0], "valid_bins": [3, 4], "keep": [false, true]],
        ]
        let summaryURL = directory.appendingPathComponent("analysis_ext.json")
        try JSONSerialization.data(withJSONObject: summary).write(to: summaryURL)
        let result = try SavedRFResult.load(source: source)
        XCTAssertNil(result.summaryWarning)
        XCTAssertEqual(result.unitQC(42)?["keep"], .bool(true))
        summary["parameters"] = ["rf_type": "excitatory", "time_range_s": [0, 0.2],
                                  "cluster_forming_z_2d": 2, "drop_bins_2d": 4]
        try JSONSerialization.data(withJSONObject: summary).write(to: summaryURL)
        XCTAssertNotNil(try SavedRFResult.load(source: source).summaryWarning)
        // A matching directory never substitutes an older source-named sidecar.
        try FileManager.default.moveItem(at: path, to: directory.appendingPathComponent("regular_261026_2.npz"))
        XCTAssertThrowsError(try SavedRFResult.load(source: source))
    }

    func testSavedFullGridCoordinatesRejectSameSizedReorderedOrShiftedAxes() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let source = try savedSource(directory)
        let path = try SavedRFResult.resultURL(source: source.url)
        let invalidCoordinates: [[String: Any]] = [
            ["x_positions": [10, -10]], ["y_positions": [15, 5]],
            ["x_positions": [-9, 11]], ["y_positions": [[5, 15]]],
            ["x_positions": [true, false]], ["y_positions": NSNull()],
        ]
        for coordinates in invalidCoordinates {
            try savedArchive(shape: [1, 2, 2], mask: [1, 0, 0, 0], center: [1, 0, 0, 0],
                             manifestOverrides: coordinates).write(to: path)
            XCTAssertThrowsError(try SavedRFResult.load(source: source))
        }
        let validCoordinates: [[String: Any]] = [[:], ["x_positions": [-10, 10], "y_positions": [5, 15]]]
        for coordinates in validCoordinates {
            try savedArchive(shape: [1, 2, 2], mask: [1, 0, 0, 0], center: [1, 0, 0, 0],
                             manifestOverrides: coordinates).write(to: path)
            XCTAssertNoThrow(try SavedRFResult.load(source: source))
        }
    }

    func testDirectOneDimensionalCoordinatesValidateOnlyRetainedAxis() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let source = try savedSource(directory)
        let path = try SavedRFResult.resultURL(source: source.url, dimension: "1d")
        for axis in ["x", "y"] {
            let shape = axis == "x" ? [1, 1, 2] : [1, 2, 1]
            let retained = axis == "x" ? [-10, 10] : [5, 15]
            var coordinates: [String: Any] = [axis + "_positions": retained,
                                              (axis == "x" ? "y" : "x") + "_positions": [0]]
            try savedArchive(shape: shape, mask: [1, 0], center: [1, 0], axis: axis,
                             manifestOverrides: coordinates).write(to: path)
            XCTAssertNoThrow(try SavedRFResult.load(source: source, dimension: "1d"))
            coordinates[axis + "_positions"] = Array(retained.reversed())
            try savedArchive(shape: shape, mask: [1, 0], center: [1, 0], axis: axis,
                             manifestOverrides: coordinates).write(to: path)
            XCTAssertThrowsError(try SavedRFResult.load(source: source, dimension: "1d"))
        }
    }
}
