import Foundation
import UniformTypeIdentifiers

extension UTType {
    /// RF mapping payload. `.rfmap` contains the same JSON schema accepted by
    /// the legacy `.json` representation.
    static let rfMapping = UTType(
        exportedAs: "org.local.rfmapping.rfmap",
        conformingTo: .json
    )

    /// Head-direction tuning curve payload. `.tc` contains the same JSON
    /// schema accepted by the legacy `.json` representation.
    static let rfTuningCurve = UTType(
        exportedAs: "org.local.rfmapping.tc",
        conformingTo: .json
    )

    /// Probe unit-position payload. `.probe` contains the same CSV schema
    /// accepted by the legacy `.csv` representation.
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
