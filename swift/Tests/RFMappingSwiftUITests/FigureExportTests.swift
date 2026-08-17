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
        let payload: [String: Any] = [
            "unitsSpikeCounts": counts,
            "unitsSpikeCountsSize": [unitIDs.count, 1, 1, 2],
            "unitPool": unitIDs,
            "xPositions": [0.0],
            "yPositions": [0.0],
            "timeBinEdges": [0.0, 0.1, 0.2],
        ]
        return try RFMappingData(
            data: JSONSerialization.data(withJSONObject: payload),
            url: URL(fileURLWithPath: "/tmp/260101_1/ProbeA-rf.json")
        )
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
        let payload: [String: Any] = [
            "unitsSpikeCounts": [[[histogram]]],
            "unitsSpikeCountsSize": [1, 1, 1, binCount],
            "unitPool": [22],
            "xPositions": [0.0],
            "yPositions": [0.0],
            "timeBinEdges": (0...binCount).map { Double($0) / 1_000 },
        ]
        return try RFMappingData(
            data: JSONSerialization.data(withJSONObject: payload),
            url: URL(fileURLWithPath: "/tmp/260101_1/ProbeA-dense-rf.json")
        )
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

    private func makeHDTuningData(unitID: Int = 22) throws -> HDTuningData {
        let payload: [String: Any] = [
            "metadata": [String: Any](),
            "angle_bin_edges_deg": (0...HDTuningData.rawBinCount).map { Double($0) * 2 },
            "occupancy_time_s": Array(repeating: 1.0, count: HDTuningData.rawBinCount),
            "unit_id": [unitID],
            "spike_counts": [Array(repeating: 2.0, count: HDTuningData.rawBinCount)],
            "firing_rate_hz": [Array(repeating: 2.0, count: HDTuningData.rawBinCount)],
            "unit_data": ["hd_class": [1]],
        ]
        return try HDTuningData(
            data: JSONSerialization.data(withJSONObject: payload),
            sourceURL: URL(fileURLWithPath: "/tmp/tuning_curves.json")
        )
    }

    private func makeProbeGeometry(unitIDs: [Int] = [22, 11]) -> ProbeGeometry {
        ProbeGeometry(
            probeName: "ProbeA",
            positionsURL: URL(fileURLWithPath: "/tmp/260101_1/data/spike_position/ProbeA/positions.csv"),
            channelsURL: URL(fileURLWithPath: "/tmp/260101_1/data/waveform/ProbeA/channels.csv"),
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

    func testPlotKindStableIDsCoverEveryRequestedCapability() {
        XCTAssertEqual(
            Set(FigureExportPlotKind.allCases.map(\.rawValue)),
            Set([
                "rf.cartesian", "rf.polar",
                "delay.cartesian", "delay.polar",
                "rgb.cartesian", "rgb.polar",
                "timeline.current", "hd.line", "hd.polar", "probe",
            ])
        )
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

    func testMissingHDAndProbeCapabilitiesBecomeExplicitPlaceholders() throws {
        let data = try makeData()
        let page = FigurePageTemplate(
            name: "Companions",
            plots: [
                FigurePlotPlacement(kind: .hdLine),
                FigurePlotPlacement(kind: .hdPolar),
                FigurePlotPlacement(kind: .probe),
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
        XCTAssertNil(descriptor.plots[3].placeholder)
    }

    func testAvailableCompanionsResolveAllTenKindsToRealRendererPayloads() throws {
        let data = try makeData()
        let page = FigurePageTemplate(
            name: "Every view",
            plots: FigureExportPlotKind.allCases.map { FigurePlotPlacement(kind: $0) }
        )
        var companions = FigureExportCompanions()
        companions.hdTuning = try makeHDTuningData(unitID: 22)
        companions.probeGeometry = makeProbeGeometry()

        let descriptor = try XCTUnwrap(FigureExportRenderer().descriptors(
            configuration: configuration(unitIDs: [22], pages: [page]),
            data: data,
            companions: companions
        ).first)

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
            default:
                XCTAssertNil(plot.hdCurve)
                XCTAssertNil(plot.probePayload)
            }
        }
    }

    func testProbePayloadRequiresSelectedUnitPosition() throws {
        let data = try makeData()
        let page = FigurePageTemplate(
            name: "Probe",
            plots: [FigurePlotPlacement(kind: .probe)]
        )
        var companions = FigureExportCompanions()
        companions.probeGeometry = makeProbeGeometry(unitIDs: [22])

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
        companions.probeGeometry = makeProbeGeometry(unitIDs: [22, 11])

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
        var occupancy = Array(repeating: 1.0, count: HDTuningData.rawBinCount)
        occupancy[HDTuningData.rawBinCount - 1] = 0
        var counts = Array(repeating: 2.0, count: HDTuningData.rawBinCount)
        counts[HDTuningData.rawBinCount - 1] = 0
        var rates: [Any] = Array(repeating: 2.0, count: HDTuningData.rawBinCount)
        rates[HDTuningData.rawBinCount - 1] = NSNull()
        let payload: [String: Any] = [
            "metadata": [String: Any](),
            "angle_bin_edges_deg": (0...HDTuningData.rawBinCount).map { Double($0) * 2 },
            "occupancy_time_s": occupancy,
            "unit_id": [22],
            "spike_counts": [counts],
            "firing_rate_hz": [rates],
            "unit_data": ["hd_class": [1]],
        ]
        let tuning = try HDTuningData(
            data: JSONSerialization.data(withJSONObject: payload),
            sourceURL: URL(fileURLWithPath: "/tmp/tuning_curves.json")
        )

        let unit = try tuning.unit(byID: 22)
        XCTAssertEqual(unit.rawRatesHz.count, HDTuningData.rawBinCount)
        XCTAssertNil(unit.rawRatesHz[HDTuningData.rawBinCount - 1])

        occupancy[0] = 1
        rates[0] = NSNull()
        var invalid = payload
        invalid["occupancy_time_s"] = occupancy
        invalid["firing_rate_hz"] = [rates]
        XCTAssertThrowsError(try HDTuningData(
            data: JSONSerialization.data(withJSONObject: invalid),
            sourceURL: URL(fileURLWithPath: "/tmp/invalid-tuning.json")
        ))
    }

    func testHDTuningRejectsOutOfRangeHDClassWithoutTrapping() throws {
        let payload: [String: Any] = [
            "metadata": [String: Any](),
            "angle_bin_edges_deg": (0...HDTuningData.rawBinCount).map { Double($0) * 2 },
            "occupancy_time_s": Array(repeating: 1.0, count: HDTuningData.rawBinCount),
            "unit_id": [22],
            "spike_counts": [Array(repeating: 2.0, count: HDTuningData.rawBinCount)],
            "firing_rate_hz": [Array(repeating: 2.0, count: HDTuningData.rawBinCount)],
            "unit_data": ["hd_class": [1e100]],
        ]

        XCTAssertThrowsError(try HDTuningData(
            data: JSONSerialization.data(withJSONObject: payload),
            sourceURL: URL(fileURLWithPath: "/tmp/out-of-range-tuning.json")
        )) { error in
            XCTAssertTrue(error.localizedDescription.contains("in-range integer"))
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

    func testPDFPNGAndSVGActuallyRenderAllTenViewsWithoutPlaceholders() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let data = try makeData(unitIDs: [22])
        let page = FigurePageTemplate(
            name: "All ten views",
            plots: FigureExportPlotKind.allCases.map { FigurePlotPlacement(kind: $0) }
        )
        var companions = FigureExportCompanions()
        companions.hdTuning = try makeHDTuningData(unitID: 22)
        companions.probeGeometry = makeProbeGeometry(unitIDs: [22])

        for format in [FigureExportFormat.pdf, .png, .svg] {
            var value = configuration(unitIDs: [22], pages: [page])
            value.format = format
            value.pageSize = .widescreen
            value.destinationDirectory = root
            value.baseName = "all-ten-\(format.rawValue)"
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
        let data = try makeData(unitIDs: [22])
        let page = FigurePageTemplate(
            name: "RF",
            plots: [FigurePlotPlacement(kind: .rfCartesian)]
        )
        var value = configuration(unitIDs: [22], pages: [page])
        value.format = .png
        value.destinationDirectory = root
        value.baseName = "provenance"
        value.outputScale = 1

        let result = try await FigureExportRenderer().export(
            configuration: value,
            data: data,
            companions: FigureExportCompanions()
        )
        let manifestData = try Data(contentsOf: result.outputURL.appendingPathComponent("manifest.json"))
        let manifest = try XCTUnwrap(
            JSONSerialization.jsonObject(with: manifestData) as? [String: Any]
        )
        XCTAssertEqual(manifest["sourceJSON"] as? String, data.url.path)
        XCTAssertEqual(manifest["sourceSHA256"] as? String, data.sourceSHA256)
        XCTAssertEqual(manifest["sourceByteCount"] as? Int, data.sourceByteCount)
        XCTAssertEqual(manifest["order"] as? String, "unit-major/page-major")
        let pages = try XCTUnwrap(manifest["pages"] as? [[String: Any]])
        let digest = try XCTUnwrap(pages.first?["sha256"] as? String)
        XCTAssertEqual(digest.count, 64)
        XCTAssertTrue(digest.allSatisfy { $0.isHexDigit })
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
        let metadata = FigureExportRenderer().pdfMetadata(
            configuration: value,
            data: data
        )
        XCTAssertEqual(metadata[kCGPDFContextTitle] as? String, value.baseName)
        let subject = try XCTUnwrap(metadata[kCGPDFContextSubject] as? String)
        XCTAssertTrue(subject.contains(data.url.path))
        XCTAssertTrue(subject.contains(data.sourceSHA256))
        XCTAssertTrue(subject.contains("Source bytes: \(data.sourceByteCount)"))
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
}
