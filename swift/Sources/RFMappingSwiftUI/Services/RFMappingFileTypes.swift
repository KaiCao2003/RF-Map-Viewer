import Foundation
import UniformTypeIdentifiers

extension UTType {
    /// Current-schema RF mapping payload. `.rfmap` and `.json` are filename
    /// aliases; the extension does not relax schema validation.
    static let rfMapping = UTType(
        exportedAs: "org.local.rfmapping.rfmap",
        conformingTo: .json
    )

    /// Head-direction tuning curve payload. `.tc` and `.json` are filename
    /// aliases for the same schema.
    static let rfTuningCurve = UTType(
        exportedAs: "org.local.rfmapping.tc",
        conformingTo: .json
    )

    /// Probe unit-position payload. `.probe` and `.csv` are filename aliases
    /// for the same schema.
    static let rfProbe = UTType(
        exportedAs: "org.local.rfmapping.probe",
        conformingTo: .commaSeparatedText
    )

    static var rfMappingReadableTypes: [UTType] { [.rfMapping, .json] }
    static var rfTuningCurveReadableTypes: [UTType] { [.rfTuningCurve, .json] }
    static var rfProbeReadableTypes: [UTType] { [.rfProbe, .commaSeparatedText] }
}

enum RFMappingFileTypes {
    static let rfMappingExtensions: Set<String> = ["rfmap", "json"]
    static let tuningCurveExtensions: Set<String> = ["tc", "json"]
    static let probeExtensions: Set<String> = ["probe", "csv"]

    static func isRFMappingURL(_ url: URL) -> Bool {
        rfMappingExtensions.contains(url.pathExtension.lowercased())
    }

    static func isDiscoverableRFMappingURL(_ url: URL) -> Bool {
        isRFMappingURL(url)
            && url.lastPathComponent.lowercased() != "tuning_curves.json"
    }

    static func isTuningCurveURL(_ url: URL) -> Bool {
        tuningCurveExtensions.contains(url.pathExtension.lowercased())
    }

    static func isProbeURL(_ url: URL) -> Bool {
        probeExtensions.contains(url.pathExtension.lowercased())
    }
}
