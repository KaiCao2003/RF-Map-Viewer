import CoreGraphics
import Foundation
import SwiftUI
import XCTest
@testable import RFMappingSwiftUI

@MainActor
final class FigureExportTests: XCTestCase {
    private func makeData(unitIDs: [Int] = [22, 11]) throws -> RFMappingData {
        let counts = unitIDs.enumerated().map { index, _ in
            [[[Double(index + 1), Double(index + 2)]]]
        }
        let payload = currentRFSchemaPayload([
            "unitsSpikeCounts": counts,
            "unitsSpikeCountsSize": [unitIDs.count, 1, 1, 2],
            "unitPool": unitIDs,
            "xPositions": [0.0],
            "yPositions": [0.0],
            "timeBinEdges": [0.0, 0.1, 0.2],
        ], occupancyTimeSec: 0.2, occupancyTimeSecSize: [1, 1])
        let jsonData = try JSONSerialization.data(withJSONObject: payload)
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
            .appendingPathComponent("260101_1", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let sourceURL = root.appendingPathComponent("ProbeA-rf.json")
        try jsonData.write(to: sourceURL, options: .atomic)
        addTeardownBlock {
            try? FileManager.default.removeItem(at: root.deletingLastPathComponent())
        }
        return try RFMappingData(data: jsonData, url: sourceURL)
    }

    private func snapshot(
        unitID: Int = 22,
        timeResolutionMS: Double = 100
    ) -> ViewerSyncState {
        ViewerSyncState(
            unitID: unitID,
            valueMode: .spikeCount,
            activeTimeMS: 50,
            rangeStartMS: 0,
            rangeEndMS: 100,
            plotRangeStartMS: 0,
            plotRangeEndMS: 200,
            timeResolutionMS: timeResolutionMS,
            xBins: 1,
            yBins: 1,
            smoothRadius: 0,
            flipY: false,
            palette: .gray,
            polarRadiusMode: .displayBottomInner,
            spatialPlotFormat: .rectangular,
            delayRGBMode: .delay,
            responseFloor: 0,
            selectedTab: .rf,
            selectedCell: nil,
            timelineRangeAnchorMS: nil,
            timelineScrollFraction: 0
        )
    }

    private func makeDenseTimelineData(binCount: Int = 80) throws -> RFMappingData {
        let histogram = (0..<binCount).map { Double(($0 % 7) + 1) }
        let payload = currentRFSchemaPayload([
            "unitsSpikeCounts": [[[histogram]]],
            "unitsSpikeCountsSize": [1, 1, 1, binCount],
            "unitPool": [22],
            "xPositions": [0.0],
            "yPositions": [0.0],
            "timeBinEdges": (0...binCount).map { Double($0) / 1_000 },
        ], occupancyTimeSec: Double(binCount) / 1_000, occupancyTimeSecSize: [1, 1])
        let jsonData = try JSONSerialization.data(withJSONObject: payload)
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
            .appendingPathComponent("260101_1", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let sourceURL = root.appendingPathComponent("ProbeA-dense-rf.json")
        try jsonData.write(to: sourceURL, options: .atomic)
        addTeardownBlock {
            try? FileManager.default.removeItem(at: root.deletingLastPathComponent())
        }
        return try RFMappingData(data: jsonData, url: sourceURL)
    }

    private func configuration(
        unitIDs: [Int] = [22, 11],
        pages: [FigurePageTemplate]
    ) -> FigureExportConfiguration {
        FigureExportConfiguration(
            destinationDirectory: URL(fileURLWithPath: "/tmp", isDirectory: true),
            selectedUnitIDs: unitIDs,
            pages: pages,
            viewerSnapshot: snapshot()
        )
    }

    private func makeHDTuningData(
        unitID: Int = 22,
        sourceURL: URL? = nil
    ) throws -> HDTuningData {
        let payload = strictHDTuningPayload(
            unitIDs: [unitID],
            zeroOccupancyBin: nil
        )
        let data = try JSONSerialization.data(withJSONObject: payload)
        let resolvedSource = sourceURL
            ?? URL(fileURLWithPath: "/tmp/tuning_curves.json")
        if sourceURL != nil {
            try FileManager.default.createDirectory(
                at: resolvedSource.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try data.write(to: resolvedSource, options: .atomic)
        }
        return try HDTuningData(data: data, sourceURL: resolvedSource)
    }

    private func makeProbeGeometry(
        unitIDs: [Int] = [22, 11],
        sourceDirectory: URL? = nil
    ) throws -> ProbeGeometry {
        let directory = sourceDirectory
            ?? URL(fileURLWithPath: "/tmp/260101_1/data/spike_position/ProbeA")
        let positionsURL = directory.appendingPathComponent("positions.csv")
        let channelsURL = directory.appendingPathComponent("channels.csv")
        if sourceDirectory != nil {
            try FileManager.default.createDirectory(
                at: directory,
                withIntermediateDirectories: true
            )
            let positionRows = unitIDs.enumerated().map { index, unitID in
                "\(index),\(unitID),\(Double(index) * 7.5 + 5),\(Double(index) * 120 + 100)"
            }
            try (["unit_index,unit_id,x_um,y_um"] + positionRows)
                .joined(separator: "\n")
                .appending("\n")
                .write(to: positionsURL, atomically: true, encoding: .utf8)
            try """
            channel_index,raw_channel_index,channel_id,x_um,y_um,shank_id
            0,0,10,0,0,0
            1,1,11,20,20,0
            """.write(to: channelsURL, atomically: true, encoding: .utf8)
        }
        return ProbeGeometry(
            probeName: "ProbeA",
            positionsURL: positionsURL,
            channelsURL: channelsURL,
            channels: [
                ProbeChannel(channelID: 10, xMicrometers: 0, yMicrometers: 0, shankID: 0),
                ProbeChannel(channelID: 11, xMicrometers: 20, yMicrometers: 20, shankID: 0),
            ],
            units: unitIDs.enumerated().map { index, unitID in
                ProbeUnitPosition(
                    unitID: unitID,
                    xMicrometers: Double(index) * 7.5 + 5,
                    yMicrometers: Double(index) * 120 + 100
                )
            }
        )
    }

    private func makeWaveformArtifact(
        unitIDs: [Int] = [22, 11]
    ) throws -> (root: URL, store: WaveformArtifactStore) {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        let manifest: [String: Any] = [
            "schema_name": WaveformArtifactStore.schemaName,
            "schema_version": WaveformArtifactStore.schemaVersion,
            "recording": [
                "sampling_frequency_hz": 30_000.0,
                "num_frames": 1_800_000,
                "duration_minutes": 1.0,
            ],
            "units": ["scope": "good", "count": unitIDs.count],
            "waveform": ["nbefore": 2, "num_samples": 4],
            "files": ["units": "units.csv"],
        ]
        try JSONSerialization.data(withJSONObject: manifest).write(
            to: root.appendingPathComponent("manifest.json")
        )
        try """
        channel_index,channel_id,raw_channel_index,x_um,y_um,shank_id
        0,100,0,0,20,0
        1,101,1,0,40,0
        2,102,2,0,60,0
        """.write(
            to: root.appendingPathComponent("channels.csv"),
            atomically: true,
            encoding: .utf8
        )
        try """
        sample_index,sample_offset,time_ms
        0,-2,-0.5
        1,-1,-0.25
        2,0,0
        3,1,0.25
        """.write(
            to: root.appendingPathComponent("waveform_time.csv"),
            atomically: true,
            encoding: .utf8
        )
        var unitRows = [
            "unit_index,unit_id,quality,total_spike_count,selected_spike_count,time_coverage_percent,best_channel_index,best_channel_id,best_channel_x_um,best_channel_y_um,max_ptp_uv,unit_data_dir"
        ]
        for (index, unitID) in unitIDs.enumerated() {
            let directoryName = "Unit\(unitID)"
            let directory = root.appendingPathComponent(directoryName, isDirectory: true)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
            let scale = Double(index + 1)
            let templateLines = [
                "sample_index,chidx_000_uv,chidx_001_uv,chidx_002_uv",
                "0,\(1 * scale),\(2 * scale),\(3 * scale)",
                "1,\(2 * scale),\(4 * scale),\(6 * scale)",
                "2,\(3 * scale),\(6 * scale),\(9 * scale)",
                "3,\(4 * scale),\(8 * scale),\(12 * scale)",
            ].joined(separator: "\n") + "\n"
            try gzip(
                Data(templateLines.utf8),
                to: directory.appendingPathComponent("template_uv.csv.gz")
            )
            unitRows.append(
                "\(index),\(unitID),good,1000,500,90,1,101,0,40,\(40 * scale),\(directoryName)"
            )
        }
        try (unitRows.joined(separator: "\n") + "\n").write(
            to: root.appendingPathComponent("units.csv"),
            atomically: true,
            encoding: .utf8
        )
        return (root, try WaveformArtifactStore(directory: root))
    }

    private func gzip(_ data: Data, to destination: URL) throws {
        let fileManager = FileManager.default
        let inputURL = fileManager.temporaryDirectory
            .appendingPathComponent("rfmapping-test-gzip-input-\(UUID().uuidString)")
        try data.write(to: inputURL)
        defer { try? fileManager.removeItem(at: inputURL) }
        let compressed = try POSIXGzipRunner.run(
            arguments: ["-c", "--", inputURL.path]
        )
        try compressed.write(to: destination)
    }

    func testPlotKindStableIDsCoverEveryRequestedCapability() {
        XCTAssertEqual(
            Set(FigureExportPlotKind.allCases.map(\.rawValue)),
            Set([
                "rf.cartesian", "rf.polar",
                "delay.cartesian", "delay.polar",
                "rgb.cartesian", "rgb.polar",
                "timeline.current", "hd.line", "hd.polar", "probe",
                "waveform.local_average",
            ])
        )
    }

    func testWaveformReaderMatchesBaselineAndLocalChannelContract() throws {
        let waveform = try makeWaveformArtifact(unitIDs: [22])
        defer { try? FileManager.default.removeItem(at: waveform.root) }

        let payload = try waveform.store.payload(for: 22, mode: .sameXColumn)

        XCTAssertEqual(payload.channels.map(\.channelID), [102, 101, 100])
        XCTAssertEqual(payload.bestChannelRow, 1)
        XCTAssertEqual(payload.baselineEndMilliseconds, -0.25)
        XCTAssertEqual(payload.valuesMicrovolts[0], [-1.5, 1.5, 4.5, 7.5])
        XCTAssertEqual(payload.amplitudeLimitMicrovolts, 7.5)
        XCTAssertEqual(try waveform.store.sourceURLs(for: 22).count, 5)
    }

    func testWaveformReaderReturnsFromCorruptGzipWithDecompressionError() throws {
        let waveform = try makeWaveformArtifact(unitIDs: [22])
        defer { try? FileManager.default.removeItem(at: waveform.root) }
        let templateURL = waveform.root
            .appendingPathComponent("Unit22", isDirectory: true)
            .appendingPathComponent("template_uv.csv.gz")
        try Data("not a gzip stream".utf8).write(to: templateURL, options: .atomic)

        XCTAssertThrowsError(try waveform.store.payload(for: 22, mode: .sameXColumn)) { error in
            guard let waveformError = error as? WaveformArtifactError,
                  case .decompression(let message) = waveformError else {
                return XCTFail("Unexpected error: \(error)")
            }
            XCTAssertTrue(message.contains("Could not decompress"))
        }
    }

    func testWaveformPreviewAndFinalUseOneSelectionScopedScale() throws {
        let data = try makeData(unitIDs: [22, 11])
        let waveform = try makeWaveformArtifact(unitIDs: [22, 11])
        defer { try? FileManager.default.removeItem(at: waveform.root) }
        let page = FigurePageTemplate(
            name: "Waveform",
            plots: [FigurePlotPlacement(kind: .waveformLocalAverage)]
        )
        let value = configuration(unitIDs: [22, 11], pages: [page])
        var companions = FigureExportCompanions()
        companions.waveformArtifact = waveform.store
        let renderer = FigureExportRenderer()

        let descriptors = renderer.descriptors(
            configuration: value,
            data: data,
            companions: companions
        )
        XCTAssertEqual(
            descriptors.compactMap { $0.plots.first?.waveformAmplitudeLimitMicrovolts },
            [15, 15]
        )
        let preview = try XCTUnwrap(renderer.previewDescriptor(
            unitID: 22,
            pageIndex: 0,
            configuration: value,
            data: data,
            companions: companions
        ))
        XCTAssertEqual(preview, descriptors[0])
        XCTAssertEqual(preview.plots.first?.waveformAmplitudeLimitMicrovolts, 15)
    }

    func testUnitSelectionIsExplicitAndPreservesOriginalUnitPoolOrder() {
        let pool = [22, 11, 90]
        XCTAssertEqual(
            FigureUnitSelection(mode: .current).resolve(unitPool: pool, currentUnitID: 11),
            [11]
        )
        XCTAssertEqual(
            FigureUnitSelection(mode: .all).resolve(unitPool: pool, currentUnitID: 11),
            pool
        )
        XCTAssertEqual(
            FigureUnitSelection(mode: .custom, customUnitIDs: [90, 22])
                .resolve(unitPool: pool, currentUnitID: 11),
            [22, 90]
        )
    }

    func testValidationReportsZeroPagesUnitsAndEmptyPage() {
        let noPages = configuration(unitIDs: [], pages: [])
        let noPageCodes = Set(FigureExportValidation.issues(
            for: noPages,
            checkOutputCollision: false
        ).map(\.code))
        XCTAssertTrue(noPageCodes.contains(.noUnits))
        XCTAssertTrue(noPageCodes.contains(.noPages))

        let empty = FigurePageTemplate(name: "  ", plots: [])
        let emptyCodes = Set(FigureExportValidation.issues(
            for: configuration(pages: [empty]),
            checkOutputCollision: false
        ).map(\.code))
        XCTAssertTrue(emptyCodes.contains(.blankPageName))
        XCTAssertTrue(emptyCodes.contains(.emptyPage))

        let duplicatedPage = FigurePageTemplate(
            name: "RF",
            plots: [FigurePlotPlacement(kind: .rfCartesian)]
        )
        let duplicateCodes = Set(FigureExportValidation.issues(
            for: configuration(unitIDs: [22, 22], pages: [duplicatedPage, duplicatedPage]),
            checkOutputCollision: false
        ).map(\.code))
        XCTAssertTrue(duplicateCodes.contains(.duplicateUnits))
        XCTAssertTrue(duplicateCodes.contains(.duplicatePageName))
    }

    func testNoOverwriteIsDefaultAndExistingOutputFailsValidation() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: directory) }
        let page = FigurePageTemplate(
            name: "RF",
            plots: [FigurePlotPlacement(kind: .rfCartesian)]
        )
        var value = configuration(pages: [page])
        value.destinationDirectory = directory
        XCTAssertFalse(value.overwriteExisting)
        guard let outputURL = value.outputURL else { return XCTFail("Expected output URL") }
        try Data().write(to: outputURL)

        let codes = Set(FigureExportValidation.issues(for: value).map(\.code))
        XCTAssertTrue(codes.contains(.outputExists))
    }

    func testDescriptorsAreUnitMajorThenPageMajor() throws {
        let data = try makeData()
        let first = FigurePageTemplate(
            name: "First",
            plots: [FigurePlotPlacement(kind: .rfCartesian)]
        )
        let second = FigurePageTemplate(
            name: "Second",
            plots: [FigurePlotPlacement(kind: .timelineCurrent)]
        )
        let renderer = FigureExportRenderer()
        let descriptors = renderer.descriptors(
            configuration: configuration(pages: [first, second]),
            data: data,
            companions: FigureExportCompanions()
        )

        XCTAssertEqual(descriptors.map(\.outputOrdinal), [0, 1, 2, 3])
        XCTAssertEqual(descriptors.map(\.unitID), [22, 22, 11, 11])
        XCTAssertEqual(descriptors.map(\.pageName), ["First", "Second", "First", "Second"])
        XCTAssertEqual(descriptors.map(\.originalUnitIndex), [0, 0, 1, 1])
    }

    func testRFPlotsAndPreviewShareOneScaleAcrossSelectedUnits() throws {
        let data = try makeData(unitIDs: [22, 11])
        let page = FigurePageTemplate(
            name: "Shared RF",
            plots: [
                FigurePlotPlacement(kind: .rfCartesian),
                FigurePlotPlacement(kind: .rfPolar),
            ]
        )
        let value = configuration(unitIDs: [22, 11], pages: [page])
        let renderer = FigureExportRenderer()

        let descriptors = renderer.descriptors(
            configuration: value,
            data: data,
            companions: FigureExportCompanions()
        )
        let ranges = descriptors.flatMap(\.plots).compactMap(\.rfValueRange)
        XCTAssertEqual(ranges.count, 4)
        XCTAssertTrue(ranges.allSatisfy { $0 == FigureScalarRange(vmin: 3, vmax: 5) })

        let preview = try XCTUnwrap(renderer.previewDescriptor(
            unitID: 11,
            pageIndex: 0,
            configuration: value,
            data: data,
            companions: FigureExportCompanions()
        ))
        XCTAssertEqual(
            preview.plots.compactMap(\.rfValueRange),
            [FigureScalarRange(vmin: 3, vmax: 5), FigureScalarRange(vmin: 3, vmax: 5)]
        )
    }

    func testConstantRFPlotsPreserveAnExactDegenerateSharedScale() throws {
        let data = try makeData(unitIDs: [22])
        let page = FigurePageTemplate(
            name: "Constant RF",
            plots: [
                FigurePlotPlacement(kind: .rfCartesian),
                FigurePlotPlacement(kind: .rfPolar),
            ]
        )
        let value = configuration(unitIDs: [22], pages: [page])
        let renderer = FigureExportRenderer()

        let descriptor = try XCTUnwrap(renderer.descriptors(
            configuration: value,
            data: data,
            companions: FigureExportCompanions()
        ).first)
        XCTAssertEqual(
            descriptor.plots.compactMap(\.rfValueRange),
            [FigureScalarRange(vmin: 3, vmax: 3), FigureScalarRange(vmin: 3, vmax: 3)]
        )
        let preview = try XCTUnwrap(renderer.previewDescriptor(
            unitID: 22,
            pageIndex: 0,
            configuration: value,
            data: data,
            companions: FigureExportCompanions()
        ))
        XCTAssertEqual(preview, descriptor)
    }

    func testMissingCompanionCapabilitiesBecomeExplicitPlaceholders() throws {
        let data = try makeData()
        let page = FigurePageTemplate(
            name: "Companions",
            plots: [
                FigurePlotPlacement(kind: .hdLine),
                FigurePlotPlacement(kind: .hdPolar),
                FigurePlotPlacement(kind: .probe),
                FigurePlotPlacement(kind: .waveformLocalAverage),
                FigurePlotPlacement(kind: .rfCartesian),
            ]
        )
        var companions = FigureExportCompanions()
        companions.hdError = "fixture missing"
        let descriptor = try XCTUnwrap(FigureExportRenderer().descriptors(
            configuration: configuration(unitIDs: [22], pages: [page]),
            data: data,
            companions: companions
        ).first)

        XCTAssertTrue(descriptor.plots[0].placeholder?.contains("fixture missing") == true)
        XCTAssertTrue(descriptor.plots[1].placeholder?.contains("fixture missing") == true)
        XCTAssertTrue(descriptor.plots[2].placeholder?.contains("positions.csv") == true)
        XCTAssertTrue(descriptor.plots[3].placeholder?.contains("waveform") == true)
        XCTAssertNil(descriptor.plots[4].placeholder)
    }

    func testAvailableCompanionsResolveAllElevenKindsToRealRendererPayloads() throws {
        figureExportHangTrace("test available enter")
        let data = try makeData()
        figureExportHangTrace("test available data end")
        let waveform = try makeWaveformArtifact()
        figureExportHangTrace("test available waveform fixture end")
        defer { try? FileManager.default.removeItem(at: waveform.root) }
        let page = FigurePageTemplate(
            name: "Every view",
            plots: FigureExportPlotKind.allCases.map { FigurePlotPlacement(kind: $0) }
        )
        figureExportHangTrace("test available page end")
        var companions = FigureExportCompanions()
        figureExportHangTrace("test available hd begin")
        companions.hdTuning = try makeHDTuningData(unitID: 22)
        figureExportHangTrace("test available hd end")
        companions.probeGeometry = try makeProbeGeometry()
        figureExportHangTrace("test available probe end")
        companions.waveformArtifact = waveform.store

        figureExportHangTrace("test available descriptors begin")
        let descriptor = try XCTUnwrap(FigureExportRenderer().descriptors(
            configuration: configuration(unitIDs: [22], pages: [page]),
            data: data,
            companions: companions
        ).first)
        figureExportHangTrace("test available descriptors end")

        XCTAssertEqual(descriptor.plots.map(\.kind), FigureExportPlotKind.allCases)
        XCTAssertTrue(descriptor.plots.allSatisfy { $0.placeholder == nil })
        for plot in descriptor.plots {
            switch plot.kind {
            case .hdLine, .hdPolar:
                XCTAssertNotNil(plot.hdCurve)
                XCTAssertNil(plot.probePayload)
            case .probe:
                let payload = try XCTUnwrap(plot.probePayload)
                XCTAssertEqual(payload.unit.unitID, 22)
                XCTAssertEqual(payload.channels.count, 2)
                XCTAssertNil(plot.hdCurve)
            case .waveformLocalAverage:
                let payload = try XCTUnwrap(plot.waveformPayload)
                XCTAssertEqual(payload.summary.unitID, 22)
                XCTAssertEqual(payload.mode, .sameXColumn)
                XCTAssertEqual(plot.waveformAmplitudeLimitMicrovolts, 7.5)
            default:
                XCTAssertNil(plot.hdCurve)
                XCTAssertNil(plot.probePayload)
                XCTAssertNil(plot.waveformPayload)
            }
        }
        figureExportHangTrace("test available assertions end")
    }

    func testProbePayloadRequiresSelectedUnitPosition() throws {
        let data = try makeData()
        let page = FigurePageTemplate(
            name: "Probe",
            plots: [FigurePlotPlacement(kind: .probe)]
        )
        var companions = FigureExportCompanions()
        companions.probeGeometry = try makeProbeGeometry(unitIDs: [22])

        let descriptors = FigureExportRenderer().descriptors(
            configuration: configuration(unitIDs: [22, 11], pages: [page]),
            data: data,
            companions: companions
        )

        XCTAssertNil(descriptors[0].plots[0].placeholder)
        XCTAssertNotNil(descriptors[0].plots[0].probePayload)
        XCTAssertTrue(
            descriptors[1].plots[0].placeholder?.contains("absent from positions.csv") == true
        )
        XCTAssertNil(descriptors[1].plots[0].probePayload)
    }

    func testMultiUnitProbePayloadContainsOnlyThatPagesUnitMarker() throws {
        let data = try makeData(unitIDs: [22, 11])
        let page = FigurePageTemplate(
            name: "Probe",
            plots: [FigurePlotPlacement(kind: .probe)]
        )
        var companions = FigureExportCompanions()
        companions.probeGeometry = try makeProbeGeometry(unitIDs: [22, 11])

        let descriptors = FigureExportRenderer().descriptors(
            configuration: configuration(unitIDs: [22, 11], pages: [page]),
            data: data,
            companions: companions
        )

        XCTAssertEqual(descriptors.count, 2)
        XCTAssertEqual(descriptors[0].plots[0].probePayload?.unit.unitID, 22)
        XCTAssertEqual(descriptors[1].plots[0].probePayload?.unit.unitID, 11)
        XCTAssertEqual(descriptors[0].plots[0].probePayload?.channels.count, 2)
        XCTAssertEqual(descriptors[1].plots[0].probePayload?.channels.count, 2)
    }

    func testHDCompanionRendersAvailableUnitAndMarksMissingUnit() throws {
        let data = try makeData()
        let page = FigurePageTemplate(
            name: "HD",
            plots: [FigurePlotPlacement(kind: .hdPolar)]
        )
        var companions = FigureExportCompanions()
        companions.hdTuning = try makeHDTuningData(unitID: 22)
        let descriptors = FigureExportRenderer().descriptors(
            configuration: configuration(pages: [page]),
            data: data,
            companions: companions
        )

        XCTAssertNil(descriptors[0].plots[0].placeholder)
        XCTAssertEqual(descriptors[0].plots[0].hdCurve?.ratesHz.count, 30)
        XCTAssertEqual(descriptors[0].plots[0].hdCurve?.ratesHz.first, 2)
        XCTAssertTrue(descriptors[1].plots[0].placeholder?.contains("unit ID 11") == true)
    }

    func testHDTuningAcceptsNullRateOnlyForEmptyOccupancyBin() throws {
        let payload = strictHDTuningPayload(unitIDs: [22])
        let tuning = try HDTuningData(
            data: JSONSerialization.data(withJSONObject: payload),
            sourceURL: URL(fileURLWithPath: "/tmp/tuning_curves.json")
        )

        let unit = try tuning.unit(byID: 22)
        XCTAssertEqual(unit.rawRatesHz.count, HDTuningData.rawBinCount)
        XCTAssertNil(unit.rawRatesHz[HDTuningData.rawBinCount - 1])

        var rates = payload["firing_rate_hz"] as! [[Any]]
        rates[0][0] = NSNull()
        var invalid = payload
        invalid["firing_rate_hz"] = rates
        XCTAssertThrowsError(try HDTuningData(
            data: JSONSerialization.data(withJSONObject: invalid),
            sourceURL: URL(fileURLWithPath: "/tmp/invalid-tuning.json")
        ))
    }

    func testHDTuningRejectsOutOfRangeHDClassWithoutTrapping() throws {
        var payload = strictHDTuningPayload(unitIDs: [22], zeroOccupancyBin: nil)
        var unitData = payload["unit_data"] as! [String: [Any]]
        unitData["hd_class"] = [1e100]
        payload["unit_data"] = unitData

        XCTAssertThrowsError(try HDTuningData(
            data: JSONSerialization.data(withJSONObject: payload),
            sourceURL: URL(fileURLWithPath: "/tmp/out-of-range-tuning.json")
        )) { error in
            XCTAssertTrue(error.localizedDescription.contains("hd_class"))
        }
    }

    func testPreviewDescriptorExactlyMatchesFinalDescriptor() throws {
        let data = try makeData()
        let pages = [
            FigurePageTemplate(name: "One", plots: [FigurePlotPlacement(kind: .rfPolar)]),
            FigurePageTemplate(name: "Two", plots: [FigurePlotPlacement(kind: .rgbCartesian)]),
        ]
        let value = configuration(pages: pages)
        let renderer = FigureExportRenderer()
        let finalDescriptors = renderer.descriptors(
            configuration: value,
            data: data,
            companions: FigureExportCompanions()
        )
        let preview = renderer.previewDescriptor(
            unitID: 11,
            pageIndex: 1,
            configuration: value,
            data: data,
            companions: FigureExportCompanions()
        )

        XCTAssertEqual(preview, finalDescriptors[3])
    }

    func testWorkspaceSnapshotsViewerAndSupportsPageAndPlotEditing() throws {
        let data = try makeData()
        let originalSnapshot = snapshot()
        let workspace = FigureExportWorkspace(seed: FigureExportSeed(
            data: data,
            viewerSnapshot: originalSnapshot,
            currentUnitID: 22
        ))

        XCTAssertEqual(workspace.configuration.viewerSnapshot, originalSnapshot)
        XCTAssertFalse(workspace.configuration.overwriteExisting)
        workspace.addPage()
        XCTAssertEqual(workspace.pages.count, 2)
        workspace.addPlot(.hdLine)
        XCTAssertEqual(workspace.pages[1].plots.last?.kind, .hdLine)
        let placement = try XCTUnwrap(workspace.pages[1].plots.last)
        workspace.movePlot(placement.id, offset: -1)
        XCTAssertEqual(workspace.pages[1].plots.first?.kind, .hdLine)
        workspace.removePlot(placement.id)
        XCTAssertFalse(workspace.pages[1].plots.contains { $0.id == placement.id })
        workspace.removeSelectedPage()
        XCTAssertEqual(workspace.pages.count, 1)
        let retainedPageID = workspace.pages[0].id
        workspace.removeSelectedPage()
        XCTAssertEqual(workspace.pages.count, 1)
        XCTAssertEqual(workspace.selectedPageID, retainedPageID)
        XCTAssertEqual(workspace.configuration.viewerSnapshot, originalSnapshot)
    }

    func testMountedComposerSurvivesRapidPageRemovalAndReordering() async throws {
        let data = try makeData(unitIDs: [22, 11])
        let workspace = FigureExportWorkspace(seed: FigureExportSeed(
            data: data,
            viewerSnapshot: snapshot(),
            currentUnitID: 22
        ))
        let hosting = NSHostingView(rootView:
            FigureExportComposerView(workspace: workspace)
                .frame(width: 1_280, height: 860)
        )
        hosting.frame = CGRect(x: 0, y: 0, width: 1_280, height: 860)

        func refreshMountedView() async {
            await Task.yield()
            hosting.layoutSubtreeIfNeeded()
            _ = hosting.fittingSize
        }

        await refreshMountedView()
        workspace.addPage()
        workspace.addPlot(.hdLine)
        await refreshMountedView()
        workspace.removeSelectedPage()
        await refreshMountedView()
        workspace.addPage()
        await refreshMountedView()
        workspace.moveSelectedPage(-1)
        await refreshMountedView()
        workspace.moveSelectedPage(1)
        await refreshMountedView()

        XCTAssertEqual(workspace.pages.map(\.name), ["Page 1", "Page 2"])
        XCTAssertEqual(workspace.selectedPageIndex, 1)
    }

    func testFinderStyleCustomUnitRowsUseAnchorModifiersAndSourceOrder() throws {
        let data = try makeData(unitIDs: [22, 11, 90, 7, 55, 31])
        let workspace = FigureExportWorkspace(seed: FigureExportSeed(
            data: data,
            viewerSnapshot: snapshot(),
            currentUnitID: 22
        ))

        workspace.setUnitSelectionMode(.custom)
        XCTAssertEqual(workspace.resolvedUnitIDs, [22])
        XCTAssertEqual(workspace.customSelectionAnchorIndex, 0)

        // A plain row click is a single selection.
        workspace.selectCustomUnit(at: 3)
        XCTAssertEqual(workspace.resolvedUnitIDs, [7])
        XCTAssertEqual(workspace.customSelectionAnchorIndex, 3)

        // Command-click preserves the old choice and toggles the clicked row.
        workspace.selectCustomUnit(at: 1, modifiers: .command)
        XCTAssertEqual(workspace.resolvedUnitIDs, [11, 7])
        XCTAssertEqual(workspace.customSelectionAnchorIndex, 1)

        // Shift-click replaces the selection with the inclusive anchor range.
        workspace.selectCustomUnit(at: 4, modifiers: .shift)
        XCTAssertEqual(workspace.resolvedUnitIDs, [11, 90, 7, 55])
        XCTAssertEqual(workspace.customSelectionAnchorIndex, 1)

        // Command-Shift adds a range while retaining selections elsewhere.
        workspace.selectCustomUnit(at: 0)
        workspace.selectCustomUnit(at: 5, modifiers: .command)
        workspace.selectCustomUnit(at: 3, modifiers: [.command, .shift])
        XCTAssertEqual(workspace.resolvedUnitIDs, [22, 7, 55, 31])

        // The checkbox is an additive toggle and becomes the next range anchor.
        workspace.setCustomUnitSelected(true, at: 1)
        XCTAssertEqual(workspace.resolvedUnitIDs, [22, 11, 7, 55, 31])
        XCTAssertEqual(workspace.customSelectionAnchorIndex, 1)
        workspace.setCustomUnitSelected(false, at: 4)
        XCTAssertEqual(workspace.resolvedUnitIDs, [22, 11, 7, 31])
        XCTAssertEqual(workspace.customSelectionAnchorIndex, 4)

        // Row gestures are inert outside Custom mode.
        workspace.setUnitSelectionMode(.all)
        workspace.selectCustomUnit(at: 2)
        XCTAssertEqual(workspace.resolvedUnitIDs, data.unitPool)
    }

    func testEverySelectedUnitUsesTheSameOrderedPageTemplates() throws {
        let data = try makeData(unitIDs: [22, 11, 90])
        let pages = [
            FigurePageTemplate(
                name: "Spatial",
                plots: [
                    FigurePlotPlacement(kind: .rfCartesian),
                    FigurePlotPlacement(kind: .delayPolar),
                ]
            ),
            FigurePageTemplate(
                name: "Companions",
                plots: [
                    FigurePlotPlacement(kind: .timelineCurrent),
                    FigurePlotPlacement(kind: .hdLine),
                    FigurePlotPlacement(kind: .probe),
                ]
            ),
        ]
        let descriptors = FigureExportRenderer().descriptors(
            configuration: configuration(unitIDs: [22, 11, 90], pages: pages),
            data: data,
            companions: FigureExportCompanions()
        )

        XCTAssertEqual(descriptors.count, 6)
        XCTAssertEqual(descriptors.map(\.unitID), [22, 22, 11, 11, 90, 90])
        XCTAssertEqual(
            descriptors.map { $0.plots.map(\.kind) },
            [
                [.rfCartesian, .delayPolar],
                [.timelineCurrent, .hdLine, .probe],
                [.rfCartesian, .delayPolar],
                [.timelineCurrent, .hdLine, .probe],
                [.rfCartesian, .delayPolar],
                [.timelineCurrent, .hdLine, .probe],
            ]
        )
    }

    func testPDFPNGAndSVGActuallyRenderAllElevenViewsWithoutPlaceholders() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let data = try makeData(unitIDs: [22])
        let waveform = try makeWaveformArtifact(unitIDs: [22])
        defer { try? FileManager.default.removeItem(at: waveform.root) }
        let page = FigurePageTemplate(
            name: "All eleven views",
            plots: FigureExportPlotKind.allCases.map { FigurePlotPlacement(kind: $0) }
        )
        var companions = FigureExportCompanions()
        companions.hdTuning = try makeHDTuningData(
            unitID: 22,
            sourceURL: root.appendingPathComponent("tuning_curves.json")
        )
        companions.probeGeometry = try makeProbeGeometry(
            unitIDs: [22],
            sourceDirectory: root.appendingPathComponent("probe", isDirectory: true)
        )
        companions.waveformArtifact = waveform.store

        for format in [FigureExportFormat.pdf, .png, .svg] {
            var value = configuration(unitIDs: [22], pages: [page])
            value.format = format
            value.pageSize = .widescreen
            value.destinationDirectory = root
            value.baseName = "all-eleven-\(format.rawValue)"
            value.outputScale = 1

            let result = try await FigureExportRenderer().export(
                configuration: value,
                data: data,
                companions: companions
            )
            XCTAssertEqual(result.pageCount, 1)
            if format == .pdf {
                XCTAssertGreaterThan(try Data(contentsOf: result.outputURL).count, 1_000)
                let document = try XCTUnwrap(CGPDFDocument(result.outputURL as CFURL))
                XCTAssertEqual(document.numberOfPages, 1)
                continue
            }
            let manifestURL = result.outputURL.appendingPathComponent("manifest.json")
            let manifest = try XCTUnwrap(
                JSONSerialization.jsonObject(with: Data(contentsOf: manifestURL))
                    as? [String: Any]
            )
            let provenance = try XCTUnwrap(manifest["provenance"] as? [String: Any])
            let inputs = try XCTUnwrap(provenance["companions"] as? [[String: Any]])
            XCTAssertEqual(inputs.count, 8)
            XCTAssertEqual(Set(inputs.compactMap { $0["kind"] as? String }), Set([
                "headDirection", "probeGeometry", "waveform",
            ]))
            XCTAssertTrue(inputs.allSatisfy { input in
                (input["path"] as? String)?.hasPrefix("/") == true
                    && (input["byteCount"] as? Int).map { $0 > 0 } == true
                    && (input["sha256"] as? String)?.count == 64
            })
            let companionStatus = try XCTUnwrap(
                provenance["companionStatus"] as? [String: Any]
            )
            XCTAssertEqual(companionStatus["headDirection"] as? String, "available")
            XCTAssertEqual(companionStatus["probeGeometry"] as? String, "available")
            XCTAssertEqual(companionStatus["waveform"] as? String, "available")
            let waveformScale = try XCTUnwrap(
                provenance["sharedWaveformScale"] as? [String: Any]
            )
            XCTAssertEqual(waveformScale["vmin"] as? Double, -7.5)
            XCTAssertEqual(waveformScale["vmax"] as? Double, 7.5)
            XCTAssertEqual(waveformScale["unit"] as? String, "µV")
            XCTAssertEqual(waveformScale["unitIds"] as? [Int], [22])
            XCTAssertEqual(waveformScale["baselineEndMs"] as? Double, -0.25)
            XCTAssertEqual(
                waveformScale["channelMode"] as? String,
                WaveformChannelMode.sameXColumn.rawValue
            )
            let display = try XCTUnwrap(provenance["display"] as? [String: Any])
            XCTAssertEqual(
                display["sharedWaveformAmplitudeLimitMicrovolts"] as? Double,
                waveformScale["vmax"] as? Double
            )
            let pages = try XCTUnwrap(manifest["pages"] as? [[String: Any]])
            XCTAssertEqual(pages.count, 1)
            XCTAssertEqual(
                pages[0]["plots"] as? [String],
                FigureExportPlotKind.allCases.map(\.rawValue)
            )
            XCTAssertEqual(pages[0]["placeholders"] as? [String], [])
            let filename = try XCTUnwrap(pages[0]["filename"] as? String)
            let pageData = try Data(contentsOf: result.outputURL.appendingPathComponent(filename))
            XCTAssertGreaterThan(pageData.count, 1_000)
            if format == .svg {
                let svg = try XCTUnwrap(String(data: pageData, encoding: .utf8))
                XCTAssertTrue(svg.contains("<svg"))
                XCTAssertTrue(svg.contains("data:image/png;base64,"))
                XCTAssertEqual(manifest["rasterEmbeddedInSVG"] as? Bool, true)
            }
        }
    }

    func testTimelineExportLayoutAndPNGContainEveryCurrentResolutionFrameWithoutScrollViewport() async throws {
        let data = try makeDenseTimelineData(binCount: 80)
        let store = RFMappingStore(
            initialData: data,
            loadDefault: false,
            discoverJSONChoices: false
        )
        store.applyViewerSyncState(snapshot(unitID: 22, timeResolutionMS: 1))
        store.selectUnitID(22, resetInteraction: false)
        let size = CGSize(width: 480, height: 260)

        let layout = makeTimelineExportLayout(
            store: store,
            width: size.width,
            height: size.height
        )

        XCTAssertEqual(store.timeGroupCount(), 80)
        XCTAssertEqual(layout.displayBins, 80)
        XCTAssertEqual(layout.miniLayouts.count, 80)
        XCTAssertEqual(layout.miniMatrices.count, 80)
        XCTAssertEqual(layout.contentHeight, size.height, accuracy: 0.001)
        let maximumFrameBottom = layout.miniLayouts.map {
            $0.y0 + $0.gridHeight + layout.labelGap + layout.labelHeight
        }.max() ?? 0
        XCTAssertLessThanOrEqual(maximumFrameBottom, size.height + 0.001)

        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let page = FigurePageTemplate(
            name: "Full timeline",
            plots: [FigurePlotPlacement(kind: .timelineCurrent)]
        )
        var value = FigureExportConfiguration(
            format: .png,
            pageSize: .widescreen,
            baseName: "full-timeline",
            destinationDirectory: root,
            selectedUnitIDs: [22],
            pages: [page],
            viewerSnapshot: snapshot(unitID: 22, timeResolutionMS: 1),
            outputScale: 1
        )
        value.overwriteExisting = false
        let result = try await FigureExportRenderer().export(
            configuration: value,
            data: data,
            companions: FigureExportCompanions()
        )
        let manifest = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(
                contentsOf: result.outputURL.appendingPathComponent("manifest.json")
            )) as? [String: Any]
        )
        let exportedPages = try XCTUnwrap(manifest["pages"] as? [[String: Any]])
        XCTAssertEqual(exportedPages.first?["placeholders"] as? [String], [])
        let filename = try XCTUnwrap(exportedPages.first?["filename"] as? String)
        XCTAssertGreaterThan(
            try Data(contentsOf: result.outputURL.appendingPathComponent(filename)).count,
            1_000
        )
    }

    func testAtomicCommitNeverClobbersWithoutOverwrite() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let staged = root.appendingPathComponent(".staged", isDirectory: true)
        let destination = root.appendingPathComponent("figures", isDirectory: true)
        try FileManager.default.createDirectory(at: staged, withIntermediateDirectories: false)
        try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: false)
        try Data("new".utf8).write(to: staged.appendingPathComponent("new.txt"))
        try Data("old".utf8).write(to: destination.appendingPathComponent("old.txt"))

        XCTAssertThrowsError(try FigureExportRenderer().commitTemporaryItem(
            staged,
            to: destination,
            overwrite: false,
            expectedDirectory: true,
            expectedFormat: .png,
            fileManager: .default
        )) { error in
            guard case FigureExportRendererError.outputAlreadyExists = error else {
                return XCTFail("Expected outputAlreadyExists, received \(error)")
            }
        }
        XCTAssertTrue(FileManager.default.fileExists(
            atPath: destination.appendingPathComponent("old.txt").path
        ))
        XCTAssertTrue(FileManager.default.fileExists(
            atPath: staged.appendingPathComponent("new.txt").path
        ))
    }

    func testAtomicOverwritePublishesCompleteReplacement() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let staged = root.appendingPathComponent(".staged.pdf")
        let destination = root.appendingPathComponent("figures.pdf")
        try Data("new".utf8).write(to: staged)
        try Data("old".utf8).write(to: destination)

        try FigureExportRenderer().commitTemporaryItem(
            staged,
            to: destination,
            overwrite: true,
            expectedDirectory: false,
            expectedFormat: nil,
            fileManager: .default
        )

        XCTAssertFalse(FileManager.default.fileExists(atPath: staged.path))
        XCTAssertEqual(try String(contentsOf: destination, encoding: .utf8), "new")
    }

    func testAtomicOverwriteRejectsWrongDestinationTypeWithoutMutation() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let staged = root.appendingPathComponent(".staged", isDirectory: true)
        let destination = root.appendingPathComponent("figures")
        try FileManager.default.createDirectory(at: staged, withIntermediateDirectories: false)
        try Data("new".utf8).write(to: staged.appendingPathComponent("new.txt"))
        try Data("old".utf8).write(to: destination)

        XCTAssertThrowsError(try FigureExportRenderer().commitTemporaryItem(
            staged,
            to: destination,
            overwrite: true,
            expectedDirectory: true,
            expectedFormat: .png,
            fileManager: .default
        ))
        XCTAssertEqual(try String(contentsOf: destination, encoding: .utf8), "old")
        XCTAssertTrue(FileManager.default.fileExists(
            atPath: staged.appendingPathComponent("new.txt").path
        ))
    }

    func testDirectoryOverwriteRefusesUnprovenancedUserDirectory() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let staged = root.appendingPathComponent(".staged", isDirectory: true)
        let destination = root.appendingPathComponent("raw-session", isDirectory: true)
        try FileManager.default.createDirectory(at: staged, withIntermediateDirectories: false)
        try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: false)
        try Data("new".utf8).write(to: staged.appendingPathComponent("new.txt"))
        try Data("irreplaceable".utf8).write(
            to: destination.appendingPathComponent("raw-data.bin")
        )

        XCTAssertThrowsError(try FigureExportRenderer().commitTemporaryItem(
            staged,
            to: destination,
            overwrite: true,
            expectedDirectory: true,
            expectedFormat: .png,
            fileManager: .default
        )) { error in
            XCTAssertTrue(error.localizedDescription.contains("validated RFMappingSwiftUI"))
        }
        XCTAssertEqual(
            try String(
                contentsOf: destination.appendingPathComponent("raw-data.bin"),
                encoding: .utf8
            ),
            "irreplaceable"
        )
        XCTAssertTrue(FileManager.default.fileExists(
            atPath: staged.appendingPathComponent("new.txt").path
        ))
    }

    func testPNGManifestCarriesExactSourceAndPageProvenance() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let data = try makeData(unitIDs: [22, 11])
        let page = FigurePageTemplate(
            name: "RF",
            plots: [FigurePlotPlacement(kind: .rfCartesian)]
        )
        var value = configuration(unitIDs: [22], pages: [page])
        value.format = .png
        value.destinationDirectory = root
        value.baseName = "provenance"
        value.outputScale = 1
        value.unitQualityFilter = RFUnitQualityFilterSnapshot(
            enabled: true,
            zeroSpikeSpatialBinThreshold: 1,
            sourceStartBin: 0,
            sourceEndBin: 1,
            spatialBinCount: 1,
            visibleUnitIDs: [22],
            excludedUnitIDs: [11]
        )

        let result = try await FigureExportRenderer().export(
            configuration: value,
            data: data,
            companions: FigureExportCompanions()
        )
        let manifestData = try Data(contentsOf: result.outputURL.appendingPathComponent("manifest.json"))
        let manifest = try XCTUnwrap(
            JSONSerialization.jsonObject(with: manifestData) as? [String: Any]
        )
        XCTAssertEqual(manifest["schemaVersion"] as? Int, 2)
        XCTAssertEqual(manifest["sourceJSON"] as? String, data.url.path)
        XCTAssertEqual(manifest["sourceSHA256"] as? String, data.sourceSHA256)
        XCTAssertEqual(manifest["sourceByteCount"] as? Int, data.sourceByteCount)
        XCTAssertEqual(manifest["order"] as? String, "unit-major/page-major")
        let provenance = try XCTUnwrap(manifest["provenance"] as? [String: Any])
        XCTAssertEqual(provenance["provenanceVersion"] as? Int, 1)
        let application = try XCTUnwrap(provenance["application"] as? [String: Any])
        XCTAssertEqual(application["name"] as? String, "RF Map Viewer")
        XCTAssertEqual(application["version"] as? String, "1.9.6")
        XCTAssertEqual(application["edition"] as? String, "SwiftUI")
        let source = try XCTUnwrap(provenance["source"] as? [String: Any])
        XCTAssertEqual(source["path"] as? String, data.url.path)
        XCTAssertEqual(source["sha256"] as? String, data.sourceSHA256)
        XCTAssertEqual(source["byteCount"] as? Int, data.sourceByteCount)
        let selection = try XCTUnwrap(provenance["selection"] as? [String: Any])
        XCTAssertEqual(selection["selectedUnitIDs"] as? [Int], [22])
        let templates = try XCTUnwrap(selection["pageTemplates"] as? [[String: Any]])
        XCTAssertEqual(templates.first?["pageName"] as? String, "RF")
        XCTAssertEqual(templates.first?["plots"] as? [String], ["rf.cartesian"])
        let display = try XCTUnwrap(provenance["display"] as? [String: Any])
        XCTAssertEqual(display["valueMode"] as? String, ResponseValueMode.spikeCount.rawValue)
        XCTAssertEqual(display["plotRangeMS"] as? [Double], [0, 200])
        XCTAssertEqual(display["smoothRadius"] as? Int, 0)
        let unitFilter = try XCTUnwrap(
            display["unitQualityFilter"] as? [String: Any]
        )
        XCTAssertEqual(unitFilter["enabled"] as? Bool, true)
        XCTAssertEqual(unitFilter["zeroSpikeSpatialBinThreshold"] as? Int, 1)
        XCTAssertEqual(unitFilter["sourceStartBin"] as? Int, 0)
        XCTAssertEqual(unitFilter["sourceEndBin"] as? Int, 1)
        XCTAssertEqual(unitFilter["visibleUnitIDs"] as? [Int], [22])
        XCTAssertEqual(unitFilter["excludedUnitIDs"] as? [Int], [11])
        XCTAssertEqual(
            unitFilter["comparison"] as? String,
            "hide when zero-bin count is greater than or equal to threshold"
        )
        let exportSettings = try XCTUnwrap(provenance["export"] as? [String: Any])
        XCTAssertEqual(exportSettings["format"] as? String, "png")
        XCTAssertEqual(exportSettings["outputScale"] as? Double, 1)
        XCTAssertEqual(exportSettings["outputPath"] as? String, result.outputURL.path)
        let pages = try XCTUnwrap(manifest["pages"] as? [[String: Any]])
        let digest = try XCTUnwrap(pages.first?["sha256"] as? String)
        XCTAssertEqual(digest.count, 64)
        XCTAssertTrue(digest.allSatisfy { $0.isHexDigit })
        let filename = try XCTUnwrap(pages.first?["filename"] as? String)
        XCTAssertEqual(
            pages.first?["byteCount"] as? Int,
            try Data(contentsOf: result.outputURL.appendingPathComponent(filename)).count
        )
        let sharedRFScale = try XCTUnwrap(provenance["sharedRFScale"] as? [String: Any])
        XCTAssertEqual(sharedRFScale["vmin"] as? Double, 3)
        XCTAssertEqual(sharedRFScale["vmax"] as? Double, 3)
        XCTAssertEqual(sharedRFScale["unit"] as? String, "spikes")
        XCTAssertEqual(sharedRFScale["unitIds"] as? [Int], [22])
        XCTAssertNil(provenance["sharedWaveformScale"])
    }

    func testExportRejectsRFSourceChangedAfterViewerLoad() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let data = try makeData(unitIDs: [22])
        try Data("replaced source".utf8).write(to: data.url, options: .atomic)
        var value = configuration(unitIDs: [22], pages: [FigurePageTemplate(
            name: "RF",
            plots: [FigurePlotPlacement(kind: .rfCartesian)]
        )])
        value.format = .png
        value.destinationDirectory = root
        value.baseName = "changed-source"

        do {
            _ = try await FigureExportRenderer().export(
                configuration: value,
                data: data,
                companions: FigureExportCompanions()
            )
            XCTFail("Expected changed source verification to fail")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("changed after it was loaded"))
        }
        XCTAssertFalse(FileManager.default.fileExists(
            atPath: root.appendingPathComponent("changed-source_png").path
        ))
    }

    func testCompanionMutationDuringRenderingPublishesNoOutput() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let data = try makeData(unitIDs: [22])
        let tuningURL = root.appendingPathComponent("tuning_curves.json")
        var companions = FigureExportCompanions()
        companions.hdTuning = try makeHDTuningData(unitID: 22, sourceURL: tuningURL)
        var value = configuration(unitIDs: [22], pages: [
            FigurePageTemplate(
                name: "HD one",
                plots: [FigurePlotPlacement(kind: .hdLine)]
            ),
            FigurePageTemplate(
                name: "HD two",
                plots: [FigurePlotPlacement(kind: .hdPolar)]
            ),
        ])
        value.format = .png
        value.destinationDirectory = root
        value.baseName = "changed-companion"
        var mutated = false

        do {
            _ = try await FigureExportRenderer().export(
                configuration: value,
                data: data,
                companions: companions,
                progress: { progress in
                    guard progress.completedPages == 1, !mutated else { return }
                    mutated = true
                    try? Data("replaced companion".utf8).write(
                        to: tuningURL,
                        options: .atomic
                    )
                }
            )
            XCTFail("Expected companion verification to fail")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("changed during figure export"))
        }
        XCTAssertTrue(mutated)
        XCTAssertFalse(FileManager.default.fileExists(
            atPath: root.appendingPathComponent("changed-companion_png").path
        ))
    }

    func testComposerSnapshotRejectsEachCompanionMutationWithoutChangingPreview() async throws {
        for mutation in ["headDirection", "probeGeometry", "waveform"] {
            let root = FileManager.default.temporaryDirectory
                .appendingPathComponent(UUID().uuidString, isDirectory: true)
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
            defer { try? FileManager.default.removeItem(at: root) }

            let data = try makeData(unitIDs: [22])
            let tuningURL = root.appendingPathComponent("tuning_curves.json")
            let probeDirectory = root.appendingPathComponent("probe", isDirectory: true)
            let waveform = try makeWaveformArtifact(unitIDs: [22])
            defer { try? FileManager.default.removeItem(at: waveform.root) }
            var companions = FigureExportCompanions()
            companions.hdTuning = try makeHDTuningData(
                unitID: 22,
                sourceURL: tuningURL
            )
            companions.probeGeometry = try makeProbeGeometry(
                unitIDs: [22],
                sourceDirectory: probeDirectory
            )
            companions.waveformArtifact = waveform.store

            let workspace = FigureExportWorkspace(seed: FigureExportSeed(
                data: data,
                unitPool: [22],
                viewerSnapshot: snapshot(),
                currentUnitID: 22,
                companions: companions
            ))
            let page = FigurePageTemplate(
                name: "Frozen companions",
                plots: [
                    FigurePlotPlacement(kind: .hdLine),
                    FigurePlotPlacement(kind: .probe),
                    FigurePlotPlacement(kind: .waveformLocalAverage),
                ]
            )
            workspace.pages = [page]
            workspace.selectedPageID = page.id
            workspace.format = .png
            workspace.destinationDirectory = root
            workspace.baseName = "changed-\(mutation)"
            workspace.outputScale = 1

            XCTAssertTrue(workspace.companions.isFrozen)
            XCTAssertEqual(workspace.companions.frozenUnitIDs, [22])
            XCTAssertEqual(workspace.companions.frozenWaveformPayloads[22]?.summary.unitID, 22)
            let frozenInputs = try XCTUnwrap(workspace.companions.frozenInputs)
            XCTAssertEqual(frozenInputs.count, 8)
            XCTAssertEqual(Set(frozenInputs.map(\.kind)), Set([
                "headDirection", "probeGeometry", "waveform",
            ]))
            let previewBefore = try XCTUnwrap(workspace.previewDescriptor)
            let finalBefore = try XCTUnwrap(FigureExportRenderer().descriptors(
                configuration: workspace.configuration,
                data: data,
                companions: workspace.companions
            ).first)
            XCTAssertEqual(previewBefore, finalBefore)

            switch mutation {
            case "headDirection":
                try Data("changed HD companion".utf8).write(
                    to: tuningURL,
                    options: .atomic
                )
            case "probeGeometry":
                try Data("changed probe companion".utf8).write(
                    to: probeDirectory.appendingPathComponent("positions.csv"),
                    options: .atomic
                )
            default:
                try Data("changed waveform companion".utf8).write(
                    to: waveform.root
                        .appendingPathComponent("Unit22", isDirectory: true)
                        .appendingPathComponent("template_uv.csv.gz"),
                    options: .atomic
                )
            }

            XCTAssertEqual(try XCTUnwrap(workspace.previewDescriptor), previewBefore)
            do {
                _ = try await FigureExportRenderer().export(
                    configuration: workspace.configuration,
                    data: data,
                    companions: workspace.companions
                )
                XCTFail("Expected frozen \(mutation) fingerprint verification to fail")
            } catch {
                XCTAssertTrue(
                    error.localizedDescription.contains(
                        "Scientific input changed during figure export"
                    ),
                    error.localizedDescription
                )
            }
            XCTAssertFalse(FileManager.default.fileExists(
                atPath: root.appendingPathComponent("changed-\(mutation)_png").path
            ))
        }
    }

    func testProbeNanPositionKeepsChannelPlotAndRecordsMissingPosition() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let data = try makeData(unitIDs: [22])
        let positionsURL = root.appendingPathComponent("positions.csv")
        let channelsURL = root.appendingPathComponent("channels.csv")
        try """
        unit_index,unit_id,x_um,y_um
        0,22,nan,nan
        """.write(to: positionsURL, atomically: true, encoding: .utf8)
        try """
        channel_index,raw_channel_index,channel_id,x_um,y_um,shank_id
        0,0,10,0,0,0
        1,1,11,20,20,0
        """.write(to: channelsURL, atomically: true, encoding: .utf8)
        var companions = FigureExportCompanions()
        companions.probeGeometry = ProbeGeometry(
            probeName: "ProbeA",
            positionsURL: positionsURL,
            channelsURL: channelsURL,
            channels: [
                ProbeChannel(channelID: 10, xMicrometers: 0, yMicrometers: 0, shankID: 0),
                ProbeChannel(channelID: 11, xMicrometers: 20, yMicrometers: 20, shankID: 0),
            ],
            units: [ProbeUnitPosition(unitID: 22, xMicrometers: nil, yMicrometers: nil)]
        )
        var value = configuration(unitIDs: [22], pages: [FigurePageTemplate(
            name: "Probe",
            plots: [FigurePlotPlacement(kind: .probe)]
        )])
        value.format = .png
        value.destinationDirectory = root
        value.baseName = "missing-position"
        value.outputScale = 1

        let descriptor = try XCTUnwrap(FigureExportRenderer().descriptors(
            configuration: value,
            data: data,
            companions: companions
        ).first)
        XCTAssertNil(descriptor.plots.first?.placeholder)
        XCTAssertEqual(descriptor.plots.first?.probePayload?.channels.count, 2)
        XCTAssertNil(descriptor.plots.first?.probePayload?.unit.position)

        let result = try await FigureExportRenderer().export(
            configuration: value,
            data: data,
            companions: companions
        )
        let manifest = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(
                contentsOf: result.outputURL.appendingPathComponent("manifest.json")
            )) as? [String: Any]
        )
        let pages = try XCTUnwrap(manifest["pages"] as? [[String: Any]])
        XCTAssertEqual(pages.first?["placeholders"] as? [String], [])
        let annotations = try XCTUnwrap(pages.first?["annotations"] as? [String])
        XCTAssertEqual(annotations.count, 1)
        XCTAssertTrue(annotations[0].contains("missingPosition"))
    }

    func testDirectoryOverwriteRefusesTamperedExporterManifest() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let data = try makeData(unitIDs: [22])
        let firstPage = FigurePageTemplate(
            name: "RF",
            plots: [FigurePlotPlacement(kind: .rfCartesian)]
        )
        var first = configuration(unitIDs: [22], pages: [firstPage])
        first.format = .png
        first.destinationDirectory = root
        first.baseName = "tampered"
        first.outputScale = 1
        let firstResult = try await FigureExportRenderer().export(
            configuration: first,
            data: data,
            companions: FigureExportCompanions()
        )
        let manifestURL = firstResult.outputURL.appendingPathComponent("manifest.json")
        var manifest = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(contentsOf: manifestURL))
                as? [String: Any]
        )
        manifest["generator"] = "UntrustedTool/1.0"
        try JSONSerialization.data(
            withJSONObject: manifest,
            options: [.prettyPrinted, .sortedKeys]
        ).write(to: manifestURL, options: .atomic)

        var replacement = first
        replacement.overwriteExisting = true
        replacement.pages = [FigurePageTemplate(
            name: "Changed",
            plots: [FigurePlotPlacement(kind: .delayCartesian)]
        )]
        do {
            _ = try await FigureExportRenderer().export(
                configuration: replacement,
                data: data,
                companions: FigureExportCompanions()
            )
            XCTFail("Expected tampered bundle replacement to fail closed")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("validated RFMappingSwiftUI"))
        }

        let preserved = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(contentsOf: manifestURL))
                as? [String: Any]
        )
        XCTAssertEqual(preserved["generator"] as? String, "UntrustedTool/1.0")
        let leftovers = try FileManager.default.contentsOfDirectory(atPath: root.path)
            .filter { $0.hasPrefix(".tampered-") }
        XCTAssertTrue(leftovers.isEmpty, "Unexpected staging outputs: \(leftovers)")
    }

    func testDirectoryOverwriteRejectsUnitFilterThresholdAbovePersistentLimit() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let data = try makeData(unitIDs: [22])
        var value = configuration(unitIDs: [22], pages: [FigurePageTemplate(
            name: "RF",
            plots: [FigurePlotPlacement(kind: .rfCartesian)]
        )])
        value.format = .png
        value.destinationDirectory = root
        value.baseName = "invalid-filter-limit"
        value.outputScale = 1
        let result = try await FigureExportRenderer().export(
            configuration: value,
            data: data,
            companions: FigureExportCompanions()
        )
        let manifestURL = result.outputURL.appendingPathComponent("manifest.json")
        var manifest = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(contentsOf: manifestURL))
                as? [String: Any]
        )
        var provenance = try XCTUnwrap(manifest["provenance"] as? [String: Any])
        var display = try XCTUnwrap(provenance["display"] as? [String: Any])
        var filter = try XCTUnwrap(display["unitQualityFilter"] as? [String: Any])
        filter["zeroSpikeSpatialBinThreshold"] = 100_001
        display["unitQualityFilter"] = filter
        provenance["display"] = display
        manifest["provenance"] = provenance
        try JSONSerialization.data(
            withJSONObject: manifest,
            options: [.prettyPrinted, .sortedKeys]
        ).write(to: manifestURL, options: .atomic)

        value.overwriteExisting = true
        do {
            _ = try await FigureExportRenderer().export(
                configuration: value,
                data: data,
                companions: FigureExportCompanions()
            )
            XCTFail("Expected out-of-range filter provenance to fail closed")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("validated RFMappingSwiftUI"))
        }
        let preserved = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(contentsOf: manifestURL))
                as? [String: Any]
        )
        let preservedProvenance = try XCTUnwrap(
            preserved["provenance"] as? [String: Any]
        )
        let preservedDisplay = try XCTUnwrap(
            preservedProvenance["display"] as? [String: Any]
        )
        let preservedFilter = try XCTUnwrap(
            preservedDisplay["unitQualityFilter"] as? [String: Any]
        )
        XCTAssertEqual(
            preservedFilter["zeroSpikeSpatialBinThreshold"] as? Int,
            100_001
        )
    }

    func testDirectoryOverwriteRejectsOverflowingSourceDimensions() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let data = try makeData(unitIDs: [22])
        var value = configuration(unitIDs: [22], pages: [FigurePageTemplate(
            name: "RF",
            plots: [FigurePlotPlacement(kind: .rfCartesian)]
        )])
        value.format = .png
        value.destinationDirectory = root
        value.baseName = "overflowing-source-dimensions"
        value.outputScale = 1
        let result = try await FigureExportRenderer().export(
            configuration: value,
            data: data,
            companions: FigureExportCompanions()
        )
        let manifestURL = result.outputURL.appendingPathComponent("manifest.json")
        var manifest = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(contentsOf: manifestURL))
                as? [String: Any]
        )
        var provenance = try XCTUnwrap(manifest["provenance"] as? [String: Any])
        var sourceContract = try XCTUnwrap(provenance["sourceContract"] as? [String: Any])
        sourceContract["dimensions"] = [1, Int.max, 2, 1]
        provenance["sourceContract"] = sourceContract
        manifest["provenance"] = provenance
        try JSONSerialization.data(
            withJSONObject: manifest,
            options: [.prettyPrinted, .sortedKeys]
        ).write(to: manifestURL, options: .atomic)

        value.overwriteExisting = true
        do {
            _ = try await FigureExportRenderer().export(
                configuration: value,
                data: data,
                companions: FigureExportCompanions()
            )
            XCTFail("Expected overflowing source dimensions to fail closed")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("validated RFMappingSwiftUI"))
        }

        let preserved = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(contentsOf: manifestURL))
                as? [String: Any]
        )
        let preservedProvenance = try XCTUnwrap(
            preserved["provenance"] as? [String: Any]
        )
        let preservedSourceContract = try XCTUnwrap(
            preservedProvenance["sourceContract"] as? [String: Any]
        )
        let dimensions = try XCTUnwrap(
            preservedSourceContract["dimensions"] as? [NSNumber]
        )
        XCTAssertEqual(dimensions.map(\.int64Value), [1, Int64.max, 2, 1])
    }

    func testConstantWaveformScaleIsStructuredAndValidatedOnOverwrite() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let data = try makeData(unitIDs: [22])
        let waveform = try makeWaveformArtifact(unitIDs: [22])
        defer { try? FileManager.default.removeItem(at: waveform.root) }
        let constantTemplate = """
        sample_index,chidx_000_uv,chidx_001_uv,chidx_002_uv
        0,5,5,5
        1,5,5,5
        2,5,5,5
        3,5,5,5
        """.appending("\n")
        try gzip(
            Data(constantTemplate.utf8),
            to: waveform.root
                .appendingPathComponent("Unit22", isDirectory: true)
                .appendingPathComponent("template_uv.csv.gz")
        )
        var companions = FigureExportCompanions()
        companions.waveformArtifact = try WaveformArtifactStore(directory: waveform.root)
        var value = configuration(unitIDs: [22], pages: [FigurePageTemplate(
            name: "Constant waveform",
            plots: [FigurePlotPlacement(kind: .waveformLocalAverage)]
        )])
        value.format = .png
        value.destinationDirectory = root
        value.baseName = "constant-waveform"
        value.outputScale = 1

        var result = try await FigureExportRenderer().export(
            configuration: value,
            data: data,
            companions: companions
        )
        var manifestURL = result.outputURL.appendingPathComponent("manifest.json")
        var manifest = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(contentsOf: manifestURL))
                as? [String: Any]
        )
        var provenance = try XCTUnwrap(manifest["provenance"] as? [String: Any])
        var scale = try XCTUnwrap(provenance["sharedWaveformScale"] as? [String: Any])
        let vmin = try XCTUnwrap(scale["vmin"] as? Double)
        let vmax = try XCTUnwrap(scale["vmax"] as? Double)
        XCTAssertEqual(vmax, Double.ulpOfOne)
        XCTAssertEqual(vmin, -vmax)
        XCTAssertEqual(scale["unit"] as? String, "µV")
        XCTAssertEqual(scale["unitIds"] as? [Int], [22])
        XCTAssertEqual(scale["baselineEndMs"] as? Double, -0.25)
        XCTAssertEqual(scale["channelMode"] as? String, "same_x_column")

        value.overwriteExisting = true
        result = try await FigureExportRenderer().export(
            configuration: value,
            data: data,
            companions: companions
        )
        manifestURL = result.outputURL.appendingPathComponent("manifest.json")
        manifest = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(contentsOf: manifestURL))
                as? [String: Any]
        )
        provenance = try XCTUnwrap(manifest["provenance"] as? [String: Any])
        scale = try XCTUnwrap(provenance["sharedWaveformScale"] as? [String: Any])
        scale["channelMode"] = "unsupported"
        provenance["sharedWaveformScale"] = scale
        manifest["provenance"] = provenance
        try JSONSerialization.data(
            withJSONObject: manifest,
            options: [.prettyPrinted, .sortedKeys]
        ).write(to: manifestURL, options: .atomic)

        do {
            _ = try await FigureExportRenderer().export(
                configuration: value,
                data: data,
                companions: companions
            )
            XCTFail("Expected invalid shared waveform scale provenance to fail closed")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("validated RFMappingSwiftUI"))
        }
    }

    func testUnavailableWaveformOmitsStructuredScale() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let data = try makeData(unitIDs: [22])
        var value = configuration(unitIDs: [22], pages: [FigurePageTemplate(
            name: "Missing waveform",
            plots: [FigurePlotPlacement(kind: .waveformLocalAverage)]
        )])
        value.format = .png
        value.destinationDirectory = root
        value.baseName = "missing-waveform"
        value.outputScale = 1

        let result = try await FigureExportRenderer().export(
            configuration: value,
            data: data,
            companions: FigureExportCompanions()
        )
        let manifest = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(
                contentsOf: result.outputURL.appendingPathComponent("manifest.json")
            )) as? [String: Any]
        )
        let provenance = try XCTUnwrap(manifest["provenance"] as? [String: Any])
        XCTAssertNil(provenance["sharedWaveformScale"])
        let display = try XCTUnwrap(provenance["display"] as? [String: Any])
        XCTAssertNil(display["sharedWaveformAmplitudeLimitMicrovolts"])
        let pages = try XCTUnwrap(manifest["pages"] as? [[String: Any]])
        let placeholders = try XCTUnwrap(pages.first?["placeholders"] as? [String])
        XCTAssertEqual(placeholders.count, 1)
        XCTAssertTrue(placeholders[0].contains("waveform"))
    }

    func testDirectoryOverwriteAllowsDifferentRecipeForValidatedBundle() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let data = try makeData(unitIDs: [22])
        var first = configuration(unitIDs: [22], pages: [FigurePageTemplate(
            name: "Original RF",
            plots: [FigurePlotPlacement(kind: .rfCartesian)]
        )])
        first.format = .png
        first.destinationDirectory = root
        first.baseName = "recipe"
        first.outputScale = 1
        _ = try await FigureExportRenderer().export(
            configuration: first,
            data: data,
            companions: FigureExportCompanions()
        )

        var replacement = first
        replacement.overwriteExisting = true
        replacement.pages = [FigurePageTemplate(
            name: "New delay recipe",
            plots: [
                FigurePlotPlacement(kind: .delayCartesian),
                FigurePlotPlacement(kind: .timelineCurrent),
            ]
        )]
        let result = try await FigureExportRenderer().export(
            configuration: replacement,
            data: data,
            companions: FigureExportCompanions()
        )
        let manifest = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(
                contentsOf: result.outputURL.appendingPathComponent("manifest.json")
            )) as? [String: Any]
        )
        let pages = try XCTUnwrap(manifest["pages"] as? [[String: Any]])
        XCTAssertEqual(pages.count, 1)
        XCTAssertEqual(
            pages[0]["plots"] as? [String],
            ["delay.cartesian", "timeline.current"]
        )
        XCTAssertEqual(pages[0]["pageName"] as? String, "New delay recipe")
    }

    func testPDFMetadataContainsExactDecodedSourceProvenance() throws {
        let data = try makeData(unitIDs: [22])
        let page = FigurePageTemplate(
            name: "RF",
            plots: [FigurePlotPlacement(kind: .rfCartesian)]
        )
        let value = configuration(unitIDs: [22], pages: [page])
        let embeddedManifest = Data("{\"manifestVersion\":2}".utf8)
        let metadata = FigureExportRenderer().pdfMetadata(
            configuration: value,
            data: data,
            manifestData: embeddedManifest
        )
        XCTAssertEqual(metadata[kCGPDFContextTitle] as? String, value.baseName)
        let subject = try XCTUnwrap(metadata[kCGPDFContextSubject] as? String)
        XCTAssertTrue(subject.contains(data.url.path))
        XCTAssertTrue(subject.contains(data.sourceSHA256))
        XCTAssertTrue(subject.contains("Source bytes: \(data.sourceByteCount)"))
        XCTAssertTrue(subject.contains("RFMExportManifest: {\"manifestVersion\":2}"))
        let digestLine = try XCTUnwrap(subject.split(separator: "\n").first(where: {
            $0.hasPrefix("RFMExportManifestSHA256: ")
        }))
        XCTAssertEqual(digestLine.split(separator: " ").last?.count, 64)
        let keywords = try XCTUnwrap(metadata[kCGPDFContextKeywords] as? String)
        XCTAssertTrue(keywords.hasPrefix("RFMExportManifestSHA256="))
        XCTAssertEqual(keywords.split(separator: "=").last?.count, 64)
    }

    func testPDFExportPublishesOneReadablePageWithoutStagingResidue() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let data = try makeData(unitIDs: [22])
        let page = FigurePageTemplate(
            name: "RF",
            plots: [FigurePlotPlacement(kind: .rfCartesian)]
        )
        var value = configuration(unitIDs: [22], pages: [page])
        value.destinationDirectory = root
        value.baseName = "single-page"

        let result = try await FigureExportRenderer().export(
            configuration: value,
            data: data,
            companions: FigureExportCompanions()
        )
        let document = try XCTUnwrap(CGPDFDocument(result.outputURL as CFURL))
        XCTAssertEqual(document.numberOfPages, 1)
        let leftovers = try FileManager.default.contentsOfDirectory(atPath: root.path)
            .filter { $0.hasPrefix(".single-page-") }
        XCTAssertTrue(leftovers.isEmpty, "Unexpected staging outputs: \(leftovers)")
    }

    func testCancellationBetweenPagesPublishesNoPartialOutput() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let data = try makeData()
        let pages = [
            FigurePageTemplate(name: "One", plots: [FigurePlotPlacement(kind: .rfCartesian)]),
            FigurePageTemplate(name: "Two", plots: [FigurePlotPlacement(kind: .delayCartesian)]),
        ]
        var value = configuration(pages: pages)
        value.format = .png
        value.destinationDirectory = root
        value.baseName = "cancelled"
        value.outputScale = 1
        var observed: [FigureExportProgress] = []
        var exportTask: Task<FigureExportResult, Error>?
        exportTask = Task { @MainActor in
            try await FigureExportRenderer().export(
                configuration: value,
                data: data,
                companions: FigureExportCompanions(),
                progress: { progress in
                    observed.append(progress)
                    if progress.completedPages == 1 { exportTask?.cancel() }
                }
            )
        }

        do {
            _ = try await XCTUnwrap(exportTask).value
            XCTFail("Expected cancellation")
        } catch is CancellationError {
            // Expected.
        }
        XCTAssertEqual(observed.first, FigureExportProgress(completedPages: 1, totalPages: 4))
        XCTAssertFalse(FileManager.default.fileExists(
            atPath: root.appendingPathComponent("cancelled_png").path
        ))
        let leftovers = try FileManager.default.contentsOfDirectory(atPath: root.path)
            .filter { $0.contains("cancelled") }
        XCTAssertTrue(leftovers.isEmpty, "Unexpected staging outputs: \(leftovers)")
    }

    func testRegistryAndWorkspaceFreezeQualityFilteredUnitPool() throws {
        let payload = currentRFSchemaPayload([
            "unitsSpikeCounts": [
                [[[1.0], [1.0]]],
                [[[0.0], [1.0]]],
                [[[2.0], [3.0]]],
            ],
            "unitsSpikeCountsSize": [3, 1, 2, 1],
            "unitPool": [22, 11, 90],
            "xPositions": [-1.0, 1.0],
            "yPositions": [0.0],
            "timeBinEdges": [0.0, 0.1],
        ], occupancyTimeSec: [0.1, 0.1], occupancyTimeSecSize: [1, 2])
        let jsonData = try JSONSerialization.data(withJSONObject: payload)
        let data = try RFMappingData(
            data: jsonData,
            url: URL(fileURLWithPath: "/tmp/quality-filtered-figure.rfmap")
        )
        let store = RFMappingStore(
            initialData: data,
            loadDefault: false,
            discoverJSONChoices: false,
            unitQualityFilterEnabled: true,
            zeroSpikeBinThreshold: 1
        )
        XCTAssertEqual(store.qualityFilteredUnitIDs, [22, 90])

        let registry = FigureExportWindowRegistry.shared
        let request = try XCTUnwrap(registry.prepare(from: store))
        defer { registry.release(request) }
        let seed = try XCTUnwrap(registry.seed(for: request))
        XCTAssertEqual(seed.unitPool, [22, 90])
        XCTAssertEqual(seed.unitQualityFilter?.visibleUnitIDs, [22, 90])
        XCTAssertEqual(seed.unitQualityFilter?.excludedUnitIDs, [11])

        let workspace = FigureExportWorkspace(seed: seed)
        XCTAssertEqual(workspace.unitPool, [22, 90])
        workspace.setUnitSelectionMode(.all)
        XCTAssertEqual(workspace.resolvedUnitIDs, [22, 90])
        XCTAssertEqual(workspace.configuration.selectedUnitIDs, [22, 90])
        XCTAssertEqual(
            workspace.configuration.unitQualityFilter,
            store.unitQualityFilterSnapshot
        )

        workspace.setUnitSelectionMode(.custom)
        workspace.selectCustomUnit(at: 1)
        XCTAssertEqual(workspace.resolvedUnitIDs, [90])
        workspace.selectCustomUnit(at: 2)
        XCTAssertEqual(workspace.resolvedUnitIDs, [90])
    }
}
