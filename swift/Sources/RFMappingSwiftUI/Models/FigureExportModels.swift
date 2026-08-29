import CoreGraphics
import Foundation

enum FigureExportPlotKind: String, CaseIterable, Codable, Identifiable, Hashable, Sendable {
    case rfCartesian = "rf.cartesian"
    case rfPolar = "rf.polar"
    case delayCartesian = "delay.cartesian"
    case delayPolar = "delay.polar"
    case rgbCartesian = "rgb.cartesian"
    case rgbPolar = "rgb.polar"
    case timelineCurrent = "timeline.current"
    case hdLine = "hd.line"
    case hdPolar = "hd.polar"
    case probe = "probe"
    case waveformLocalAverage = "waveform.local_average"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .rfCartesian: "RF map"
        case .rfPolar: "RF map (polar)"
        case .delayCartesian: "Delay map"
        case .delayPolar: "Delay map (polar)"
        case .rgbCartesian: "RGB map"
        case .rgbPolar: "RGB map (polar)"
        case .timelineCurrent: "Timeline (current settings)"
        case .hdLine: "HD tuning curve"
        case .hdPolar: "HD tuning curve (polar)"
        case .probe: "Probe"
        case .waveformLocalAverage: "Local average waveform"
        }
    }

    var shortSlug: String {
        rawValue.replacingOccurrences(of: ".", with: "-")
    }

    var requiresHDTuning: Bool {
        self == .hdLine || self == .hdPolar
    }

    var requiresProbe: Bool { self == .probe }

    var requiresWaveform: Bool { self == .waveformLocalAverage }
}

struct FigurePlotPlacement: Identifiable, Equatable, Codable, Sendable {
    let id: UUID
    var kind: FigureExportPlotKind

    init(id: UUID = UUID(), kind: FigureExportPlotKind) {
        self.id = id
        self.kind = kind
    }
}

struct FigurePageTemplate: Identifiable, Equatable, Codable, Sendable {
    let id: UUID
    var name: String
    var plots: [FigurePlotPlacement]

    init(
        id: UUID = UUID(),
        name: String,
        plots: [FigurePlotPlacement]
    ) {
        self.id = id
        self.name = name
        self.plots = plots
    }
}

enum FigureExportFormat: String, CaseIterable, Codable, Identifiable, Hashable, Sendable {
    case pdf
    case png
    case svg

    var id: String { rawValue }
    var label: String { rawValue.uppercased() }
}

enum FigurePageSizePreset: String, CaseIterable, Codable, Identifiable, Hashable, Sendable {
    case letterLandscape
    case a4Landscape
    case widescreen
    case square

    var id: String { rawValue }

    var label: String {
        switch self {
        case .letterLandscape: "Letter landscape"
        case .a4Landscape: "A4 landscape"
        case .widescreen: "16:9 widescreen"
        case .square: "Square"
        }
    }

    /// PDF points. PNG/SVG use the same aspect ratio and are rendered at the
    /// configured output scale.
    var size: CGSize {
        switch self {
        case .letterLandscape: CGSize(width: 792, height: 612)
        case .a4Landscape: CGSize(width: 841.89, height: 595.28)
        case .widescreen: CGSize(width: 960, height: 540)
        case .square: CGSize(width: 720, height: 720)
        }
    }
}

enum FigureUnitSelectionMode: String, CaseIterable, Identifiable, Hashable, Sendable {
    case current
    case all
    case custom

    var id: String { rawValue }
    var label: String { rawValue.capitalized }
}

struct FigureUnitSelectionModifiers: OptionSet, Equatable, Sendable {
    let rawValue: Int

    static let command = FigureUnitSelectionModifiers(rawValue: 1 << 0)
    static let shift = FigureUnitSelectionModifiers(rawValue: 1 << 1)
}

struct FigureUnitSelection: Equatable, Sendable {
    var mode: FigureUnitSelectionMode
    var customUnitIDs: Set<Int>

    init(mode: FigureUnitSelectionMode = .current, customUnitIDs: Set<Int> = []) {
        self.mode = mode
        self.customUnitIDs = customUnitIDs
    }

    /// Always returns source `unitPool` order. This makes output order stable
    /// and keeps original index and unit ID semantics explicit.
    func resolve(unitPool: [Int], currentUnitID: Int?) -> [Int] {
        switch mode {
        case .current:
            guard let currentUnitID, unitPool.contains(currentUnitID) else { return [] }
            return [currentUnitID]
        case .all:
            return unitPool
        case .custom:
            return unitPool.filter(customUnitIDs.contains)
        }
    }
}

struct FigureExportConfiguration: Sendable {
    var format: FigureExportFormat
    var pageSize: FigurePageSizePreset
    var baseName: String
    var destinationDirectory: URL?
    var overwriteExisting: Bool
    var selectedUnitIDs: [Int]
    var pages: [FigurePageTemplate]
    var viewerSnapshot: ViewerSyncState
    var outputScale: CGFloat

    init(
        format: FigureExportFormat = .pdf,
        pageSize: FigurePageSizePreset = .letterLandscape,
        baseName: String = "rfmapping-figures",
        destinationDirectory: URL? = nil,
        overwriteExisting: Bool = false,
        selectedUnitIDs: [Int],
        pages: [FigurePageTemplate],
        viewerSnapshot: ViewerSyncState,
        outputScale: CGFloat = 2
    ) {
        self.format = format
        self.pageSize = pageSize
        self.baseName = baseName
        self.destinationDirectory = destinationDirectory
        self.overwriteExisting = overwriteExisting
        self.selectedUnitIDs = selectedUnitIDs
        self.pages = pages
        self.viewerSnapshot = viewerSnapshot
        self.outputScale = outputScale
    }

    var outputURL: URL? {
        guard let destinationDirectory else { return nil }
        switch format {
        case .pdf:
            return destinationDirectory.appendingPathComponent(baseName).appendingPathExtension("pdf")
        case .png:
            return destinationDirectory.appendingPathComponent("\(baseName)_png", isDirectory: true)
        case .svg:
            return destinationDirectory.appendingPathComponent("\(baseName)_svg", isDirectory: true)
        }
    }
}

enum FigureExportValidationCode: String, Codable, Hashable, Sendable {
    case noUnits
    case duplicateUnits
    case noPages
    case emptyPage
    case blankPageName
    case duplicatePageName
    case invalidBaseName
    case missingDestination
    case invalidScale
    case outputExists
}

struct FigureExportValidationIssue: Equatable, Codable, Sendable {
    let code: FigureExportValidationCode
    let message: String
    let pageID: UUID?
}

enum FigureExportValidation {
    static func issues(
        for configuration: FigureExportConfiguration,
        fileManager: FileManager = .default,
        checkOutputCollision: Bool = true
    ) -> [FigureExportValidationIssue] {
        var issues: [FigureExportValidationIssue] = []
        if configuration.selectedUnitIDs.isEmpty {
            issues.append(.init(
                code: .noUnits,
                message: "Select at least one unit.",
                pageID: nil
            ))
        }
        if Set(configuration.selectedUnitIDs).count != configuration.selectedUnitIDs.count {
            issues.append(.init(
                code: .duplicateUnits,
                message: "Selected unit IDs must be unique.",
                pageID: nil
            ))
        }
        if configuration.pages.isEmpty {
            issues.append(.init(
                code: .noPages,
                message: "Add at least one page.",
                pageID: nil
            ))
        }
        for page in configuration.pages {
            if page.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                issues.append(.init(
                    code: .blankPageName,
                    message: "Every page needs a name.",
                    pageID: page.id
                ))
            }
            if page.plots.isEmpty {
                issues.append(.init(
                    code: .emptyPage,
                    message: "Page ‘\(page.name)’ has no plots.",
                    pageID: page.id
                ))
            }
        }
        let normalizedPageNames = configuration.pages.map {
            $0.name.trimmingCharacters(in: .whitespacesAndNewlines)
        }.filter { !$0.isEmpty }
        if Set(normalizedPageNames).count != normalizedPageNames.count {
            issues.append(.init(
                code: .duplicatePageName,
                message: "Page names must be unique.",
                pageID: nil
            ))
        }
        let trimmedName = configuration.baseName.trimmingCharacters(in: .whitespacesAndNewlines)
        let invalidCharacters = CharacterSet(charactersIn: "/:")
            .union(CharacterSet.controlCharacters)
        if trimmedName.isEmpty
            || trimmedName == "."
            || trimmedName == ".."
            || trimmedName.rangeOfCharacter(from: invalidCharacters) != nil {
            issues.append(.init(
                code: .invalidBaseName,
                message: "Enter a safe output name without / or : characters.",
                pageID: nil
            ))
        }
        if !configuration.outputScale.isFinite || configuration.outputScale <= 0 {
            issues.append(.init(
                code: .invalidScale,
                message: "Output scale must be positive and finite.",
                pageID: nil
            ))
        }
        guard let destination = configuration.destinationDirectory else {
            issues.append(.init(
                code: .missingDestination,
                message: "Choose an export destination.",
                pageID: nil
            ))
            return issues
        }
        if checkOutputCollision,
           !configuration.overwriteExisting,
           let outputURL = configuration.outputURL,
           fileManager.fileExists(atPath: outputURL.path) {
            issues.append(.init(
                code: .outputExists,
                message: "Output already exists: \(outputURL.path). Enable overwrite or choose another name.",
                pageID: nil
            ))
        }
        var isDirectory: ObjCBool = false
        let destinationExists = fileManager.fileExists(
            atPath: destination.path,
            isDirectory: &isDirectory
        )
        if !destinationExists {
            issues.append(.init(
                code: .missingDestination,
                message: "The export destination no longer exists.",
                pageID: nil
            ))
        } else if !isDirectory.boolValue {
            issues.append(.init(
                code: .missingDestination,
                message: "The export destination must be a directory.",
                pageID: nil
            ))
        }
        return issues
    }
}

struct FigureExportSeed: Sendable {
    let data: RFMappingData
    let viewerSnapshot: ViewerSyncState
    let currentUnitID: Int
    let tuningSessionIndex: Int
    let waveformChannelMode: WaveformChannelMode
    let companions: FigureExportCompanions

    init(
        data: RFMappingData,
        viewerSnapshot: ViewerSyncState,
        currentUnitID: Int,
        tuningSessionIndex: Int = 1,
        waveformChannelMode: WaveformChannelMode = .sameXColumn,
        companions: FigureExportCompanions = FigureExportCompanions()
    ) {
        self.data = data
        self.viewerSnapshot = viewerSnapshot
        self.currentUnitID = currentUnitID
        self.tuningSessionIndex = max(1, tuningSessionIndex)
        self.waveformChannelMode = waveformChannelMode
        self.companions = companions
    }
}

extension FigureExportPlotKind {
    static func currentViewerDefault(from state: ViewerSyncState) -> FigureExportPlotKind {
        switch state.selectedTab {
        case .rf:
            return state.spatialPlotFormat == .polar ? .rfPolar : .rfCartesian
        case .delayRGB:
            if state.delayRGBMode == .rgb {
                return state.spatialPlotFormat == .polar ? .rgbPolar : .rgbCartesian
            }
            return state.spatialPlotFormat == .polar ? .delayPolar : .delayCartesian
        case .timeline:
            return .timelineCurrent
        }
    }
}
