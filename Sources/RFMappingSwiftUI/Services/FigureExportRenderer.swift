import AppKit
import CoreGraphics
import CryptoKit
import Darwin
import Foundation
import SwiftUI

struct FigureExportCompanions: Sendable {
    var hdTuning: HDTuningData?
    var hdError: String?
    var probeGeometry: ProbeGeometry?
    var probeError: String?

    var probeUnavailableReason: String {
        if let probeError {
            return "Probe geometry could not be loaded: \(probeError)"
        }
        return "No companion positions.csv was found for this RF dataset."
    }
}

struct FigurePlotRenderDescriptor: Identifiable, Equatable, Sendable {
    let id: UUID
    let kind: FigureExportPlotKind
    let placeholder: String?
    let hdCurve: ProcessedHDCurve?
    let probePayload: ProbePlotPayload?

    init(
        id: UUID,
        kind: FigureExportPlotKind,
        placeholder: String? = nil,
        hdCurve: ProcessedHDCurve? = nil,
        probePayload: ProbePlotPayload? = nil
    ) {
        self.id = id
        self.kind = kind
        self.placeholder = placeholder
        self.hdCurve = hdCurve
        self.probePayload = probePayload
    }
}

struct FigurePageRenderDescriptor: Identifiable, Equatable, Sendable {
    let id: UUID
    let outputOrdinal: Int
    let unitID: Int
    let originalUnitIndex: Int
    let pageIndex: Int
    let pageID: UUID
    let pageName: String
    let plots: [FigurePlotRenderDescriptor]
    let viewerSnapshot: ViewerSyncState

    var unitLabel: String {
        originalUnitIndex >= 0
            ? "Original index \(String(format: "%03d", originalUnitIndex)) / unit ID \(unitID)"
            : "Original index N/A / unit ID \(unitID)"
    }
}

struct FigureExportResult: Sendable {
    let outputURL: URL
    let pageCount: Int
    let generatedFiles: [URL]
}

struct FigureExportProgress: Equatable, Sendable {
    let completedPages: Int
    let totalPages: Int
}

enum FigureExportRendererError: LocalizedError {
    case invalidConfiguration([FigureExportValidationIssue])
    case couldNotCreateRenderer(String)
    case couldNotWrite(String)
    case outputAlreadyExists(URL)

    var errorDescription: String? {
        switch self {
        case .invalidConfiguration(let issues):
            return issues.map(\.message).joined(separator: "\n")
        case .couldNotCreateRenderer(let message), .couldNotWrite(let message):
            return message
        case .outputAlreadyExists(let url):
            return "Output already exists: \(url.path)."
        }
    }
}

@MainActor
struct FigureExportRenderer {
    func descriptors(
        configuration: FigureExportConfiguration,
        data: RFMappingData,
        companions: FigureExportCompanions
    ) -> [FigurePageRenderDescriptor] {
        var descriptors: [FigurePageRenderDescriptor] = []
        descriptors.reserveCapacity(configuration.selectedUnitIDs.count * configuration.pages.count)
        var ordinal = 0
        // The loop order is an explicit invariant: unit-major, then page-major.
        for unitID in configuration.selectedUnitIDs {
            let originalIndex = data.unitIndex(forUnitID: unitID) ?? -1
            for (pageIndex, page) in configuration.pages.enumerated() {
                descriptors.append(makeDescriptor(
                    outputOrdinal: ordinal,
                    unitID: unitID,
                    originalUnitIndex: originalIndex,
                    pageIndex: pageIndex,
                    page: page,
                    viewerSnapshot: configuration.viewerSnapshot,
                    data: data,
                    companions: companions
                ))
                ordinal += 1
            }
        }
        return descriptors
    }

    /// Live preview calls this exact function; final export calls `descriptors`,
    /// which delegates to it for every page.
    func previewDescriptor(
        unitID: Int,
        pageIndex: Int,
        configuration: FigureExportConfiguration,
        data: RFMappingData,
        companions: FigureExportCompanions
    ) -> FigurePageRenderDescriptor? {
        guard let unitOffset = configuration.selectedUnitIDs.firstIndex(of: unitID),
              configuration.pages.indices.contains(pageIndex) else { return nil }
        let outputOrdinal = unitOffset * configuration.pages.count + pageIndex
        return makeDescriptor(
            outputOrdinal: outputOrdinal,
            unitID: unitID,
            originalUnitIndex: data.unitIndex(forUnitID: unitID) ?? -1,
            pageIndex: pageIndex,
            page: configuration.pages[pageIndex],
            viewerSnapshot: configuration.viewerSnapshot,
            data: data,
            companions: companions
        )
    }

    func export(
        configuration: FigureExportConfiguration,
        data: RFMappingData,
        companions: FigureExportCompanions,
        fileManager: FileManager = .default,
        progress: ((FigureExportProgress) -> Void)? = nil
    ) async throws -> FigureExportResult {
        try Task.checkCancellation()
        let issues = FigureExportValidation.issues(
            for: configuration,
            fileManager: fileManager,
            checkOutputCollision: true
        )
        guard issues.isEmpty else {
            throw FigureExportRendererError.invalidConfiguration(issues)
        }
        guard let destination = configuration.destinationDirectory,
              let outputURL = configuration.outputURL else {
            throw FigureExportRendererError.invalidConfiguration([
                .init(code: .missingDestination, message: "Choose an export destination.", pageID: nil)
            ])
        }
        let pages = descriptors(configuration: configuration, data: data, companions: companions)
        guard !pages.isEmpty else {
            throw FigureExportRendererError.invalidConfiguration([
                .init(code: .noPages, message: "The export contains no rendered pages.", pageID: nil)
            ])
        }

        let accessing = destination.startAccessingSecurityScopedResource()
        defer { if accessing { destination.stopAccessingSecurityScopedResource() } }
        switch configuration.format {
        case .pdf:
            return try await exportPDF(
                pages: pages,
                configuration: configuration,
                data: data,
                outputURL: outputURL,
                fileManager: fileManager,
                progress: progress
            )
        case .png, .svg:
            return try await exportPageDirectory(
                pages: pages,
                configuration: configuration,
                data: data,
                outputURL: outputURL,
                fileManager: fileManager,
                progress: progress
            )
        }
    }

    private func makeDescriptor(
        outputOrdinal: Int,
        unitID: Int,
        originalUnitIndex: Int,
        pageIndex: Int,
        page: FigurePageTemplate,
        viewerSnapshot: ViewerSyncState,
        data: RFMappingData,
        companions: FigureExportCompanions
    ) -> FigurePageRenderDescriptor {
        let plots = page.plots.map { placement in
            makePlotDescriptor(
                placement: placement,
                unitID: unitID,
                originalUnitIndex: originalUnitIndex,
                companions: companions
            )
        }
        return FigurePageRenderDescriptor(
            id: stablePageID(unitID: unitID, pageID: page.id),
            outputOrdinal: outputOrdinal,
            unitID: unitID,
            originalUnitIndex: originalUnitIndex,
            pageIndex: pageIndex,
            pageID: page.id,
            pageName: page.name,
            plots: plots,
            viewerSnapshot: viewerSnapshot
        )
    }

    private func makePlotDescriptor(
        placement: FigurePlotPlacement,
        unitID: Int,
        originalUnitIndex: Int,
        companions: FigureExportCompanions
    ) -> FigurePlotRenderDescriptor {
        if originalUnitIndex < 0 {
            return .init(
                id: placement.id,
                kind: placement.kind,
                placeholder: "RF mapping unit \(unitID) is unavailable in this dataset.",
                hdCurve: nil,
                probePayload: nil
            )
        }
        if placement.kind.requiresHDTuning {
            guard let hdTuning = companions.hdTuning else {
                let reason = companions.hdError
                    ?? "No companion HD tuning_curves.json was found for this RF dataset."
                return .init(
                    id: placement.id,
                    kind: placement.kind,
                    placeholder: "HD tuning unavailable: \(reason)",
                    hdCurve: nil,
                    probePayload: nil
                )
            }
            do {
                return .init(
                    id: placement.id,
                    kind: placement.kind,
                    placeholder: nil,
                    hdCurve: try hdTuning.processedCurve(unitID: unitID),
                    probePayload: nil
                )
            } catch {
                return .init(
                    id: placement.id,
                    kind: placement.kind,
                    placeholder: "HD tuning unavailable for unit ID \(unitID): \(error.localizedDescription)",
                    hdCurve: nil,
                    probePayload: nil
                )
            }
        }
        if placement.kind.requiresProbe {
            guard let geometry = companions.probeGeometry else {
                return .init(
                    id: placement.id,
                    kind: placement.kind,
                    placeholder: companions.probeUnavailableReason
                )
            }
            guard geometry.units.contains(where: { $0.unitID == unitID }) else {
                return .init(
                    id: placement.id,
                    kind: placement.kind,
                    placeholder: "Probe position is unavailable for RF unit \(unitID); "
                        + "the selected unit is absent from positions.csv."
                )
            }
            return .init(
                id: placement.id,
                kind: placement.kind,
                probePayload: ProbePlotPayload(
                    probeName: geometry.probeName,
                    positionsURL: geometry.positionsURL,
                    channelsURL: geometry.channelsURL,
                    channels: geometry.channels,
                    units: geometry.units,
                    selectedUnitID: unitID
                )
            )
        }
        return .init(id: placement.id, kind: placement.kind)
    }

    private func stablePageID(unitID: Int, pageID: UUID) -> UUID {
        // Identity only needs to remain stable while the composer is open.
        var bytes = pageID.uuid
        withUnsafeMutableBytes(of: &bytes) { buffer in
            var value = UInt64(bitPattern: Int64(unitID)).littleEndian
            withUnsafeBytes(of: &value) { unitBytes in
                for index in 0..<min(8, buffer.count) {
                    buffer[index] ^= unitBytes[index]
                }
            }
        }
        return UUID(uuid: bytes)
    }

    private func exportPDF(
        pages: [FigurePageRenderDescriptor],
        configuration: FigureExportConfiguration,
        data: RFMappingData,
        outputURL: URL,
        fileManager: FileManager,
        progress: ((FigureExportProgress) -> Void)?
    ) async throws -> FigureExportResult {
        let temporaryURL = outputURL.deletingLastPathComponent()
            .appendingPathComponent(".\(configuration.baseName)-\(UUID().uuidString).tmp")
            .appendingPathExtension("pdf")
        var mediaBox = CGRect(origin: .zero, size: configuration.pageSize.size)
        let metadata = pdfMetadata(configuration: configuration, data: data)
        guard let context = CGContext(
            temporaryURL as CFURL,
            mediaBox: &mediaBox,
            metadata as CFDictionary
        ) else {
            throw FigureExportRendererError.couldNotCreateRenderer(
                "Could not create the PDF context."
            )
        }
        do {
            for (pageOffset, page) in pages.enumerated() {
                try Task.checkCancellation()
                context.beginPDFPage(nil)
                let imageRenderer = makeImageRenderer(
                    descriptor: page,
                    data: data,
                    configuration: configuration,
                    scale: 1
                )
                imageRenderer.render { _, draw in draw(context) }
                context.endPDFPage()
                progress?(FigureExportProgress(
                    completedPages: pageOffset + 1,
                    totalPages: pages.count
                ))
                try Task.checkCancellation()
                await Task.yield()
            }
            try Task.checkCancellation()
        } catch {
            context.closePDF()
            try? fileManager.removeItem(at: temporaryURL)
            throw error
        }
        // Close exactly once before publication. Commit failures must never
        // call closePDF again on the same Core Graphics context.
        context.closePDF()
        do {
            try commitTemporaryItem(
                temporaryURL,
                to: outputURL,
                overwrite: configuration.overwriteExisting,
                expectedDirectory: false,
                expectedFormat: nil,
                fileManager: fileManager
            )
        } catch {
            try? fileManager.removeItem(at: temporaryURL)
            throw error
        }
        return FigureExportResult(
            outputURL: outputURL,
            pageCount: pages.count,
            generatedFiles: [outputURL]
        )
    }

    private func exportPageDirectory(
        pages: [FigurePageRenderDescriptor],
        configuration: FigureExportConfiguration,
        data: RFMappingData,
        outputURL: URL,
        fileManager: FileManager,
        progress: ((FigureExportProgress) -> Void)?
    ) async throws -> FigureExportResult {
        let temporaryURL = outputURL.deletingLastPathComponent()
            .appendingPathComponent(".\(configuration.baseName)-\(UUID().uuidString).tmp", isDirectory: true)
        try fileManager.createDirectory(at: temporaryURL, withIntermediateDirectories: false)
        var generated: [URL] = []
        var entries: [FigureManifestPage] = []
        do {
            for (pageOffset, page) in pages.enumerated() {
                try Task.checkCancellation()
                let filename = pageFilename(page, format: configuration.format)
                let fileURL = temporaryURL.appendingPathComponent(filename)
                let png = try pngData(
                    descriptor: page,
                    data: data,
                    configuration: configuration
                )
                let outputData: Data
                switch configuration.format {
                case .png:
                    outputData = png
                case .svg:
                    let svg = embeddedSVG(
                        pngData: png,
                        size: configuration.pageSize.size,
                        scale: configuration.outputScale,
                        title: "\(page.unitLabel) — \(page.pageName)"
                    )
                    outputData = Data(svg.utf8)
                case .pdf:
                    preconditionFailure("PDF is handled by exportPDF")
                }
                try outputData.write(to: fileURL, options: .atomic)
                generated.append(outputURL.appendingPathComponent(filename))
                entries.append(FigureManifestPage(
                    ordinal: page.outputOrdinal,
                    unitID: page.unitID,
                    originalUnitIndex: page.originalUnitIndex,
                    pageIndex: page.pageIndex,
                    pageName: page.pageName,
                    filename: filename,
                    sha256: sha256Hex(outputData),
                    plots: page.plots.map(\.kind.rawValue),
                    placeholders: page.plots.compactMap(\.placeholder)
                ))
                progress?(FigureExportProgress(
                    completedPages: pageOffset + 1,
                    totalPages: pages.count
                ))
                try Task.checkCancellation()
                await Task.yield()
            }
            try Task.checkCancellation()
            let manifest = FigureExportManifest(
                schemaVersion: 1,
                generator: generatorName,
                generatedAtUTC: ISO8601DateFormatter().string(from: Date()),
                order: "unit-major/page-major",
                format: configuration.format.rawValue,
                sourceJSON: data.url.path,
                sourceSHA256: data.sourceSHA256,
                sourceByteCount: data.sourceByteCount,
                pageSize: configuration.pageSize.rawValue,
                rasterEmbeddedInSVG: configuration.format == .svg,
                pages: entries
            )
            let manifestURL = temporaryURL.appendingPathComponent("manifest.json")
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            try encoder.encode(manifest).write(to: manifestURL, options: .atomic)
            generated.append(outputURL.appendingPathComponent("manifest.json"))
            try Task.checkCancellation()
            try commitTemporaryItem(
                temporaryURL,
                to: outputURL,
                overwrite: configuration.overwriteExisting,
                expectedDirectory: true,
                expectedFormat: configuration.format,
                fileManager: fileManager
            )
        } catch {
            try? fileManager.removeItem(at: temporaryURL)
            throw error
        }
        return FigureExportResult(
            outputURL: outputURL,
            pageCount: pages.count,
            generatedFiles: generated
        )
    }

    private func pngData(
        descriptor: FigurePageRenderDescriptor,
        data: RFMappingData,
        configuration: FigureExportConfiguration
    ) throws -> Data {
        let renderer = makeImageRenderer(
            descriptor: descriptor,
            data: data,
            configuration: configuration,
            scale: configuration.outputScale
        )
        guard let image = renderer.nsImage,
              let tiff = image.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: tiff),
              let png = bitmap.representation(using: .png, properties: [:]) else {
            throw FigureExportRendererError.couldNotCreateRenderer(
                "Could not rasterize \(descriptor.unitLabel), page \(descriptor.pageName)."
            )
        }
        return png
    }

    private func makeImageRenderer(
        descriptor: FigurePageRenderDescriptor,
        data: RFMappingData,
        configuration: FigureExportConfiguration,
        scale: CGFloat
    ) -> ImageRenderer<FigureRenderedPageView> {
        let view = FigureRenderedPageView(
            descriptor: descriptor,
            data: data
        )
        let renderer = ImageRenderer(content: view)
        renderer.proposedSize = ProposedViewSize(configuration.pageSize.size)
        renderer.scale = scale
        return renderer
    }

    private var generatorName: String {
        let version = Bundle.main.object(
            forInfoDictionaryKey: "CFBundleShortVersionString"
        ) as? String ?? "development"
        return "RFMappingSwiftUI/\(version)"
    }

    func pdfMetadata(
        configuration: FigureExportConfiguration,
        data: RFMappingData
    ) -> [CFString: Any] {
        [
            kCGPDFContextTitle: configuration.baseName,
            kCGPDFContextCreator: generatorName,
            kCGPDFContextSubject: [
                "Source JSON: \(data.url.path)",
                "Source SHA-256: \(data.sourceSHA256)",
                "Source bytes: \(data.sourceByteCount)",
                "Order: unit-major/page-major",
            ].joined(separator: "\n"),
        ]
    }

    private func sha256Hex(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    /// Publishes a complete sibling staging item using one Darwin rename.
    /// New outputs use an exclusive rename; explicit overwrite atomically swaps
    /// old and new entries, after which the old complete output is cleaned from
    /// the private staging name. Directory overwrite additionally requires the
    /// old entry to be a fully validated RFMappingSwiftUI export bundle. There
    /// is never a state where overwrite has deleted the old output but not yet
    /// published the new one.
    func commitTemporaryItem(
        _ temporaryURL: URL,
        to outputURL: URL,
        overwrite: Bool,
        expectedDirectory: Bool,
        expectedFormat: FigureExportFormat?,
        fileManager: FileManager
    ) throws {
        let renameExclusive: UInt32 = 0x00000004
        let renameSwap: UInt32 = 0x00000002

        for _ in 0..<4 {
            do {
                try atomicRename(
                    temporaryURL,
                    outputURL,
                    flags: renameExclusive
                )
                return
            } catch let error as AtomicRenameError
                where error.code == EEXIST || error.code == ENOTEMPTY {
                guard overwrite else {
                    throw FigureExportRendererError.outputAlreadyExists(outputURL)
                }
            } catch let error as AtomicRenameError {
                throw renameFailure(error)
            }

            do {
                try validateExistingOutput(
                    outputURL,
                    expectedDirectory: expectedDirectory,
                    expectedFormat: expectedFormat,
                    fileManager: fileManager
                )
                try atomicRename(temporaryURL, outputURL, flags: renameSwap)
                // `temporaryURL` now contains the previous complete output.
                do {
                    try validateExistingOutput(
                        temporaryURL,
                        expectedDirectory: expectedDirectory,
                        expectedFormat: expectedFormat,
                        fileManager: fileManager
                    )
                } catch {
                    // The destination changed type between validation and the
                    // atomic exchange. Exchange again to restore the original
                    // destination before reporting the race.
                    do {
                        try atomicRename(temporaryURL, outputURL, flags: renameSwap)
                    } catch let rollbackError as AtomicRenameError {
                        throw FigureExportRendererError.couldNotWrite(
                            "Export replacement validation failed and rollback also failed: "
                                + "\(renameFailure(rollbackError).localizedDescription)"
                        )
                    }
                    throw error
                }
                // Failure to clean it cannot invalidate the newly published
                // destination, so cleanup is deliberately best-effort.
                try? fileManager.removeItem(at: temporaryURL)
                return
            } catch let error as AtomicRenameError where error.code == ENOENT {
                // Destination disappeared after the exclusive attempt. Retry
                // the complete decision without using a clobbering rename.
                continue
            } catch let error as AtomicRenameError {
                throw renameFailure(error)
            }
        }
        throw FigureExportRendererError.couldNotWrite(
            "Output changed repeatedly while publishing \(outputURL.path)."
        )
    }

    private struct AtomicRenameError: Error {
        let code: Int32
        let source: URL
        let destination: URL
    }

    private func atomicRename(_ source: URL, _ destination: URL, flags: UInt32) throws {
        let result = source.path.withCString { sourcePath in
            destination.path.withCString { destinationPath in
                renamex_np(sourcePath, destinationPath, flags)
            }
        }
        guard result == 0 else {
            throw AtomicRenameError(
                code: errno,
                source: source,
                destination: destination
            )
        }
    }

    private func renameFailure(_ error: AtomicRenameError) -> FigureExportRendererError {
        let detail = String(cString: strerror(error.code))
        return .couldNotWrite(
            "Could not atomically publish \(error.source.path) to "
                + "\(error.destination.path): \(detail)."
        )
    }

    private func validateExistingOutput(
        _ outputURL: URL,
        expectedDirectory: Bool,
        expectedFormat: FigureExportFormat?,
        fileManager: FileManager
    ) throws {
        let attributes: [FileAttributeKey: Any]
        do {
            attributes = try fileManager.attributesOfItem(atPath: outputURL.path)
        } catch let error as NSError where error.domain == NSCocoaErrorDomain
            && error.code == NSFileNoSuchFileError {
            throw AtomicRenameError(code: ENOENT, source: outputURL, destination: outputURL)
        }
        let type = attributes[.type] as? FileAttributeType
        guard type != .typeSymbolicLink else {
            throw FigureExportRendererError.couldNotWrite(
                "Export output must not replace a symbolic link: \(outputURL.path)."
            )
        }
        let isDirectory = type == .typeDirectory
        guard isDirectory == expectedDirectory else {
            let expected = expectedDirectory ? "a directory" : "a regular file"
            throw FigureExportRendererError.couldNotWrite(
                "Existing export output must be \(expected): \(outputURL.path)."
            )
        }
        if expectedDirectory {
            guard let expectedFormat, expectedFormat != .pdf else {
                throw FigureExportRendererError.couldNotWrite(
                    "Directory export replacement requires an explicit PNG or SVG format."
                )
            }
            try validateExportDirectoryBundle(
                outputURL,
                expectedFormat: expectedFormat,
                fileManager: fileManager
            )
        }
    }

    private func validateExportDirectoryBundle(
        _ directory: URL,
        expectedFormat: FigureExportFormat,
        fileManager: FileManager
    ) throws {
        let manifestURL = directory.appendingPathComponent("manifest.json")
        let manifestAttributes: [FileAttributeKey: Any]
        do {
            manifestAttributes = try regularFileAttributes(
                manifestURL,
                label: "Figure export manifest",
                fileManager: fileManager
            )
        } catch {
            throw invalidExportBundle(directory, error.localizedDescription)
        }
        let manifestSize = (manifestAttributes[.size] as? NSNumber)?.uint64Value ?? 0
        guard manifestSize > 0, manifestSize <= 16 * 1_024 * 1_024 else {
            throw invalidExportBundle(directory, "manifest.json has an invalid size")
        }

        let manifest: FigureExportManifest
        do {
            manifest = try JSONDecoder().decode(
                FigureExportManifest.self,
                from: Data(contentsOf: manifestURL, options: .mappedIfSafe)
            )
        } catch {
            throw invalidExportBundle(
                directory,
                "manifest.json could not be decoded: \(error.localizedDescription)"
            )
        }
        let generatorParts = manifest.generator.split(
            separator: "/",
            omittingEmptySubsequences: false
        )
        guard manifest.schemaVersion == 1,
              generatorParts.count == 2,
              generatorParts[0] == "RFMappingSwiftUI",
              !generatorParts[1].isEmpty,
              manifest.format == expectedFormat.rawValue,
              manifest.order == "unit-major/page-major",
              manifest.rasterEmbeddedInSVG == (expectedFormat == .svg),
              ISO8601DateFormatter().date(from: manifest.generatedAtUTC) != nil,
              manifest.sourceJSON.hasPrefix("/"),
              manifest.pages.allSatisfy({ !$0.plots.isEmpty }),
              manifest.pages.count <= 100_000,
              !manifest.pages.isEmpty else {
            throw invalidExportBundle(
                directory,
                "manifest identity, format, order, or page list is invalid"
            )
        }
        guard manifest.sourceSHA256.count == 64,
              manifest.sourceSHA256.allSatisfy(\.isHexDigit),
              manifest.sourceByteCount > 0 else {
            throw invalidExportBundle(directory, "source provenance is invalid")
        }

        let filenames = manifest.pages.map(\.filename)
        guard Set(filenames).count == filenames.count,
              manifest.pages.map(\.ordinal) == Array(manifest.pages.indices) else {
            throw invalidExportBundle(directory, "page filenames or ordinals are duplicated")
        }
        let requiredExtension = expectedFormat.rawValue
        for page in manifest.pages {
            guard page.pageIndex >= 0, page.pageIndex < Int.max else {
                throw invalidExportBundle(directory, "manifest page index is invalid")
            }
            let canonicalFilename = String(
                format: "unit_%03d_id_%d_page_%02d_%@.%@",
                page.originalUnitIndex,
                page.unitID,
                page.pageIndex + 1,
                slug(page.pageName),
                requiredExtension
            )
            guard safeManifestFilename(page.filename),
                  page.filename == canonicalFilename,
                  URL(fileURLWithPath: page.filename).pathExtension.lowercased()
                    == requiredExtension,
                  page.sha256.count == 64,
                  page.sha256.allSatisfy(\.isHexDigit) else {
                throw invalidExportBundle(
                    directory,
                    "manifest contains an unsafe filename, extension, or digest"
                )
            }
        }

        let expectedEntries = Set(filenames).union(["manifest.json"])
        let actualEntries: Set<String>
        do {
            actualEntries = Set(try fileManager.contentsOfDirectory(atPath: directory.path))
        } catch {
            throw invalidExportBundle(
                directory,
                "directory contents could not be enumerated: \(error.localizedDescription)"
            )
        }
        guard actualEntries == expectedEntries else {
            let unexpected = actualEntries.subtracting(expectedEntries).sorted()
            let missing = expectedEntries.subtracting(actualEntries).sorted()
            throw invalidExportBundle(
                directory,
                "file set differs from manifest; unexpected=\(unexpected), missing=\(missing)"
            )
        }

        for page in manifest.pages {
            let pageURL = directory.appendingPathComponent(page.filename)
            do {
                _ = try regularFileAttributes(
                    pageURL,
                    label: "Manifest page \(page.filename)",
                    fileManager: fileManager
                )
            } catch {
                throw invalidExportBundle(directory, error.localizedDescription)
            }
            let actualDigest: String
            do {
                actualDigest = sha256Hex(try Data(
                    contentsOf: pageURL,
                    options: .mappedIfSafe
                ))
            } catch {
                throw invalidExportBundle(
                    directory,
                    "page \(page.filename) could not be read: \(error.localizedDescription)"
                )
            }
            guard actualDigest == page.sha256.lowercased() else {
                throw invalidExportBundle(
                    directory,
                    "page \(page.filename) does not match its SHA-256"
                )
            }
        }
    }

    private func regularFileAttributes(
        _ url: URL,
        label: String,
        fileManager: FileManager
    ) throws -> [FileAttributeKey: Any] {
        let attributes: [FileAttributeKey: Any]
        do {
            attributes = try fileManager.attributesOfItem(atPath: url.path)
        } catch {
            throw FigureExportRendererError.couldNotWrite(
                "\(label) is unavailable: \(error.localizedDescription)"
            )
        }
        guard attributes[.type] as? FileAttributeType == .typeRegular else {
            throw FigureExportRendererError.couldNotWrite(
                "\(label) must be a regular file and not a directory or symbolic link."
            )
        }
        return attributes
    }

    private func safeManifestFilename(_ filename: String) -> Bool {
        !filename.isEmpty
            && filename != "."
            && filename != ".."
            && URL(fileURLWithPath: filename).lastPathComponent == filename
            && !filename.contains("/")
            && !filename.contains(":")
            && filename.rangeOfCharacter(from: .controlCharacters) == nil
    }

    private func invalidExportBundle(
        _ directory: URL,
        _ reason: String
    ) -> FigureExportRendererError {
        .couldNotWrite(
            "Refusing to overwrite a directory that is not a validated "
                + "RFMappingSwiftUI export bundle: \(directory.path) (\(reason))."
        )
    }

    private func pageFilename(
        _ page: FigurePageRenderDescriptor,
        format: FigureExportFormat
    ) -> String {
        let safePageName = slug(page.pageName)
        return String(
            format: "unit_%03d_id_%d_page_%02d_%@.%@",
            page.originalUnitIndex,
            page.unitID,
            page.pageIndex + 1,
            safePageName,
            format.rawValue
        )
    }

    private func slug(_ text: String) -> String {
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_"))
        let sanitized = text.lowercased().unicodeScalars.map {
            allowed.contains($0) ? String($0) : "-"
        }.joined()
        let collapsed = sanitized.replacingOccurrences(
            of: "-+",
            with: "-",
            options: .regularExpression
        ).trimmingCharacters(in: CharacterSet(charactersIn: "-"))
        return collapsed.isEmpty ? "page" : collapsed
    }

    private func embeddedSVG(
        pngData: Data,
        size: CGSize,
        scale: CGFloat,
        title: String
    ) -> String {
        let escapedTitle = title
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
        let pixelWidth = size.width * scale
        let pixelHeight = size.height * scale
        return """
        <?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="\(pixelWidth)" height="\(pixelHeight)" viewBox="0 0 \(size.width) \(size.height)">
          <title>\(escapedTitle)</title>
          <image width="\(size.width)" height="\(size.height)" href="data:image/png;base64,\(pngData.base64EncodedString())" />
        </svg>
        """
    }
}

private struct FigureManifestPage: Codable {
    let ordinal: Int
    let unitID: Int
    let originalUnitIndex: Int
    let pageIndex: Int
    let pageName: String
    let filename: String
    let sha256: String
    let plots: [String]
    let placeholders: [String]
}

private struct FigureExportManifest: Codable {
    let schemaVersion: Int
    let generator: String
    let generatedAtUTC: String
    let order: String
    let format: String
    let sourceJSON: String
    let sourceSHA256: String
    let sourceByteCount: Int
    let pageSize: String
    let rasterEmbeddedInSVG: Bool
    let pages: [FigureManifestPage]
}

struct FigureRenderedPageView: View {
    let descriptor: FigurePageRenderDescriptor
    let data: RFMappingData

    var body: some View {
        VStack(spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(descriptor.pageName)
                        .font(.system(size: 18, weight: .bold))
                    Text(descriptor.unitLabel)
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Text("Page \(descriptor.pageIndex + 1)")
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 14)
            .padding(.top, 10)

            GeometryReader { proxy in
                let columns = gridColumnCount(descriptor.plots.count)
                let rows = max(1, Int(ceil(Double(max(1, descriptor.plots.count)) / Double(columns))))
                let gap: CGFloat = 8
                let slotWidth = max(1, (proxy.size.width - CGFloat(columns - 1) * gap) / CGFloat(columns))
                let slotHeight = max(1, (proxy.size.height - CGFloat(rows - 1) * gap) / CGFloat(rows))
                ZStack(alignment: .topLeading) {
                    if descriptor.plots.isEmpty {
                        FigureExportPlaceholderView(
                            title: "Empty page",
                            message: "Add at least one plot before exporting."
                        )
                    } else {
                        ForEach(Array(descriptor.plots.enumerated()), id: \.element.id) { index, plot in
                            FigureExportPlotView(
                                plot: plot,
                                descriptor: descriptor,
                                data: data
                            )
                            .frame(width: slotWidth, height: slotHeight)
                            .position(
                                x: CGFloat(index % columns) * (slotWidth + gap) + slotWidth / 2,
                                y: CGFloat(index / columns) * (slotHeight + gap) + slotHeight / 2
                            )
                        }
                    }
                }
            }
            .padding(.horizontal, 10)
            .padding(.bottom, 10)
        }
        .background(Color(nsColor: .textBackgroundColor))
        .environment(\.colorScheme, .light)
    }

    private func gridColumnCount(_ plotCount: Int) -> Int {
        Int(ceil(sqrt(Double(max(1, plotCount)))))
    }
}

private struct FigureExportPlotView: View {
    let plot: FigurePlotRenderDescriptor
    let descriptor: FigurePageRenderDescriptor
    let data: RFMappingData

    @ViewBuilder
    var body: some View {
        Group {
            if let placeholder = plot.placeholder {
                FigureExportPlaceholderView(title: plot.kind.label, message: placeholder)
            } else if let curve = plot.hdCurve {
                HDCurveExportView(
                    title: plot.kind.label,
                    curve: curve,
                    polar: plot.kind == .hdPolar,
                    unitID: descriptor.unitID
                )
            } else if let probePayload = plot.probePayload {
                ProbeGeometryExportView(payload: probePayload)
            } else {
                ExistingRFExportPlotView(
                    data: data,
                    snapshot: descriptor.viewerSnapshot,
                    unitID: descriptor.unitID,
                    kind: plot.kind
                )
                // `@State` owns an isolated store. Force a fresh identity when
                // previewing another unit so SwiftUI cannot retain the prior
                // unit's store under the same reusable plot placement ID.
                .id("\(descriptor.unitID):\(plot.kind.rawValue):\(plot.id.uuidString)")
            }
        }
        // Preview and final rendering stay deterministic; interacting with a
        // preview plot must not create state that is absent from the export.
        .allowsHitTesting(false)
    }
}

private struct ExistingRFExportPlotView: View {
    @State private var store: RFMappingStore
    let kind: FigureExportPlotKind

    init(
        data: RFMappingData,
        snapshot: ViewerSyncState,
        unitID: Int,
        kind: FigureExportPlotKind
    ) {
        let isolated = RFMappingStore(
            initialData: data,
            loadDefault: false,
            discoverJSONChoices: false
        )
        isolated.applyViewerSyncState(snapshot)
        isolated.selectUnitID(unitID, resetInteraction: false)
        isolated.clearHover()
        if kind == .rgbCartesian { isolated.spatialPlotFormat = .rectangular }
        if kind == .rgbPolar { isolated.spatialPlotFormat = .polar }
        _store = State(initialValue: isolated)
        self.kind = kind
    }

    @ViewBuilder
    var body: some View {
        switch kind {
        case .rfCartesian:
            HeatmapView(store: store, kind: .rf)
        case .rfPolar:
            PolarMapView(store: store, kind: .rf)
        case .delayCartesian:
            HeatmapView(store: store, kind: .delay)
        case .delayPolar:
            PolarMapView(store: store, kind: .delay)
        case .rgbCartesian, .rgbPolar:
            RGBMapView(store: store)
        case .timelineCurrent:
            TimelineExportView(store: store)
        case .hdLine, .hdPolar:
            FigureExportPlaceholderView(
                title: kind.label,
                message: "HD renderer payload was not resolved."
            )
        case .probe:
            FigureExportPlaceholderView(
                title: kind.label,
                message: "Probe renderer payload was not resolved."
            )
        }
    }
}

private struct FigureExportPlaceholderView: View {
    let title: String
    let message: String

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 26))
                .foregroundStyle(.orange)
            Text(title).font(.headline)
            Text(message)
                .font(.caption)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .textSelection(.enabled)
        }
        .padding(16)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(nsColor: .controlBackgroundColor))
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(.secondary.opacity(0.45)))
    }
}

private struct HDCurveExportView: View {
    let title: String
    let curve: ProcessedHDCurve
    let polar: Bool
    let unitID: Int

    var body: some View {
        Canvas { context, size in
            var context = context
            drawTitle(
                context: &context,
                title: title,
                subtitle: "Unit ID \(unitID); 30 display bins; circular Gaussian smoothing"
            )
            if polar {
                drawPolarCurve(context: &context, size: size)
            } else {
                drawLineCurve(context: &context, size: size)
            }
        }
        .background(Color(nsColor: .textBackgroundColor))
    }

    private func drawLineCurve(context: inout GraphicsContext, size: CGSize) {
        let rect = CGRect(x: 52, y: 66, width: max(10, size.width - 76), height: max(10, size.height - 102))
        context.stroke(Path(rect), with: .color(.secondary.opacity(0.6)), lineWidth: 1)
        let high = max(curve.ratesHz.max() ?? 0, 1e-12)
        var path = Path()
        for index in curve.ratesHz.indices {
            let point = CGPoint(
                x: rect.minX + rect.width * CGFloat(index) / CGFloat(max(1, curve.ratesHz.count - 1)),
                y: rect.maxY - rect.height * CGFloat(curve.ratesHz[index] / high)
            )
            if index == 0 {
                path.move(to: point)
            } else {
                path.addLine(to: point)
            }
        }
        context.stroke(path, with: .color(.blue), lineWidth: 2)
        for angle in stride(from: 0, through: 360, by: 90) {
            let x = rect.minX + rect.width * CGFloat(angle) / 360
            context.draw(
                Text("\(angle)°").font(.system(size: 9)).foregroundStyle(.secondary),
                at: CGPoint(x: x, y: rect.maxY + 16),
                anchor: .center
            )
        }
        context.draw(
            Text(String(format: "%.2f Hz", high)).font(.system(size: 9)).foregroundStyle(.secondary),
            at: CGPoint(x: rect.minX - 6, y: rect.minY),
            anchor: .trailing
        )
    }

    private func drawPolarCurve(context: inout GraphicsContext, size: CGSize) {
        let center = CGPoint(x: size.width / 2, y: size.height / 2 + 18)
        let radius = max(10, min(size.width, size.height) * 0.34)
        let high = max(curve.ratesHz.max() ?? 0, 1e-12)
        for fraction in [0.25, 0.5, 0.75, 1.0] {
            let r = radius * CGFloat(fraction)
            context.stroke(
                Path(ellipseIn: CGRect(x: center.x - r, y: center.y - r, width: r * 2, height: r * 2)),
                with: .color(.secondary.opacity(0.25)),
                lineWidth: 1
            )
        }
        var path = Path()
        for index in 0...curve.ratesHz.count {
            let sourceIndex = index % curve.ratesHz.count
            let angle = curve.anglesDegrees[sourceIndex] * Double.pi / 180 - Double.pi / 2
            let r = radius * CGFloat(curve.ratesHz[sourceIndex] / high)
            let point = CGPoint(
                x: center.x + r * CGFloat(cos(angle)),
                y: center.y + r * CGFloat(sin(angle))
            )
            if index == 0 {
                path.move(to: point)
            } else {
                path.addLine(to: point)
            }
        }
        context.stroke(path, with: .color(.blue), lineWidth: 2)
        for (label, angle) in [("0°", -Double.pi / 2), ("90°", 0.0), ("180°", Double.pi / 2), ("270°", Double.pi)] {
            context.draw(
                Text(label).font(.system(size: 9)).foregroundStyle(.secondary),
                at: CGPoint(
                    x: center.x + (radius + 15) * CGFloat(cos(angle)),
                    y: center.y + (radius + 15) * CGFloat(sin(angle))
                ),
                anchor: .center
            )
        }
    }
}

private struct ProbeGeometryExportView: View {
    let payload: ProbePlotPayload

    var body: some View {
        Canvas { context, size in
            var context = context
            drawTitle(
                context: &context,
                title: "\(payload.probeName) layout",
                subtitle: "Unit ID \(payload.selectedUnitID); "
                    + "\(payload.channels.count) channels; \(payload.units.count) RF units"
            )
            drawGeometry(context: &context, size: size)
        }
        .background(Color(nsColor: .textBackgroundColor))
    }

    private func drawGeometry(context: inout GraphicsContext, size: CGSize) {
        let plotRect = CGRect(
            x: 54,
            y: 68,
            width: max(10, size.width - 86),
            height: max(10, size.height - 112)
        )
        let allX = payload.channels.map(\.xMicrometers)
            + payload.units.map(\.xMicrometers)
        let allY = payload.channels.map(\.yMicrometers)
            + payload.units.map(\.yMicrometers)
        guard let rawXLow = allX.min(), let rawXHigh = allX.max(),
              let rawYLow = allY.min(), let rawYHigh = allY.max() else { return }
        let xRange = paddedRange(low: rawXLow, high: rawXHigh)
        let yRange = paddedRange(low: rawYLow, high: rawYHigh)

        context.stroke(
            Path(plotRect),
            with: .color(.secondary.opacity(0.55)),
            lineWidth: 1
        )
        drawAxisLabels(
            context: &context,
            plotRect: plotRect,
            xRange: xRange,
            yRange: yRange
        )

        for channel in payload.channels {
            let point = displayPoint(
                x: channel.xMicrometers,
                y: channel.yMicrometers,
                plotRect: plotRect,
                xRange: xRange,
                yRange: yRange
            )
            context.fill(
                Path(ellipseIn: CGRect(
                    x: point.x - 2.25,
                    y: point.y - 2.25,
                    width: 4.5,
                    height: 4.5
                )),
                with: .color(Color(red: 0.58, green: 0.64, blue: 0.72))
            )
        }

        // Draw the selected unit last so coincident coordinates cannot hide
        // its red marker beneath another RF unit.
        let orderedUnits = payload.units.filter { $0.unitID != payload.selectedUnitID }
            + payload.units.filter { $0.unitID == payload.selectedUnitID }
        for unit in orderedUnits {
            let selected = unit.unitID == payload.selectedUnitID
            let point = displayPoint(
                x: unit.xMicrometers,
                y: unit.yMicrometers,
                plotRect: plotRect,
                xRange: xRange,
                yRange: yRange
            )
            let radius: CGFloat = selected ? 6 : 4.5
            let circle = Path(ellipseIn: CGRect(
                x: point.x - radius,
                y: point.y - radius,
                width: radius * 2,
                height: radius * 2
            ))
            context.fill(
                circle,
                with: .color(selected
                    ? Color(red: 0.86, green: 0.15, blue: 0.15)
                    : Color(red: 0.15, green: 0.39, blue: 0.72))
            )
            context.stroke(circle, with: .color(.white), lineWidth: 1)
            context.draw(
                Text("\(unit.unitID)")
                    .font(.system(size: selected ? 10 : 8, weight: selected ? .bold : .regular))
                    .foregroundStyle(.primary),
                at: CGPoint(x: point.x + radius + 3, y: point.y),
                anchor: .leading
            )
        }

        drawLegend(context: &context, plotRect: plotRect)
    }

    private func paddedRange(low: Double, high: Double) -> ClosedRange<Double> {
        let span = high - low
        let padding = span > 1e-12 ? span * 0.06 : max(abs(low) * 0.06, 1)
        return (low - padding)...(high + padding)
    }

    private func displayPoint(
        x: Double,
        y: Double,
        plotRect: CGRect,
        xRange: ClosedRange<Double>,
        yRange: ClosedRange<Double>
    ) -> CGPoint {
        let xFraction = (x - xRange.lowerBound)
            / max(xRange.upperBound - xRange.lowerBound, 1e-12)
        let yFraction = (y - yRange.lowerBound)
            / max(yRange.upperBound - yRange.lowerBound, 1e-12)
        return CGPoint(
            x: plotRect.minX + plotRect.width * CGFloat(xFraction),
            y: plotRect.maxY - plotRect.height * CGFloat(yFraction)
        )
    }

    private func drawAxisLabels(
        context: inout GraphicsContext,
        plotRect: CGRect,
        xRange: ClosedRange<Double>,
        yRange: ClosedRange<Double>
    ) {
        for fraction in [0.0, 0.5, 1.0] {
            let x = plotRect.minX + plotRect.width * CGFloat(fraction)
            let value = xRange.lowerBound
                + (xRange.upperBound - xRange.lowerBound) * fraction
            context.draw(
                Text(String(format: "%.1f", value))
                    .font(.system(size: 8))
                    .foregroundStyle(.secondary),
                at: CGPoint(x: x, y: plotRect.maxY + 13),
                anchor: .center
            )
            let y = plotRect.maxY - plotRect.height * CGFloat(fraction)
            let yValue = yRange.lowerBound
                + (yRange.upperBound - yRange.lowerBound) * fraction
            context.draw(
                Text(String(format: "%.1f", yValue))
                    .font(.system(size: 8))
                    .foregroundStyle(.secondary),
                at: CGPoint(x: plotRect.minX - 6, y: y),
                anchor: .trailing
            )
        }
        context.draw(
            Text("x (µm)").font(.system(size: 9)).foregroundStyle(.secondary),
            at: CGPoint(x: plotRect.midX, y: plotRect.maxY + 29),
            anchor: .center
        )
        context.drawLayer { layer in
            layer.translateBy(x: plotRect.minX - 37, y: plotRect.midY)
            layer.rotate(by: .degrees(-90))
            layer.draw(
                Text("y (µm)").font(.system(size: 9)).foregroundStyle(.secondary),
                at: .zero,
                anchor: .center
            )
        }
    }

    private func drawLegend(context: inout GraphicsContext, plotRect: CGRect) {
        let origin = CGPoint(x: plotRect.maxX - 124, y: plotRect.minY + 10)
        let entries: [(String, Color, CGFloat)] = [
            ("channel", Color(red: 0.58, green: 0.64, blue: 0.72), 2.5),
            ("RF unit", Color(red: 0.15, green: 0.39, blue: 0.72), 4),
            ("selected", Color(red: 0.86, green: 0.15, blue: 0.15), 5),
        ]
        let box = CGRect(x: origin.x - 8, y: origin.y - 8, width: 128, height: 55)
        context.fill(Path(box), with: .color(.white.opacity(0.84)))
        context.stroke(Path(box), with: .color(.secondary.opacity(0.35)), lineWidth: 1)
        for (index, entry) in entries.enumerated() {
            let y = origin.y + CGFloat(index) * 16
            context.fill(
                Path(ellipseIn: CGRect(
                    x: origin.x - entry.2,
                    y: y - entry.2,
                    width: entry.2 * 2,
                    height: entry.2 * 2
                )),
                with: .color(entry.1)
            )
            context.draw(
                Text(entry.0).font(.system(size: 8)).foregroundStyle(.primary),
                at: CGPoint(x: origin.x + 10, y: y),
                anchor: .leading
            )
        }
    }
}
