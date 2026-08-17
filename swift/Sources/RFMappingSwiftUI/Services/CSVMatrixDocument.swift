import SwiftUI
import UniformTypeIdentifiers

extension UTType {
    static let rfCSV = UTType(filenameExtension: "csv")!
}

struct CSVMatrixDocument: FileDocument {
    static var readableContentTypes: [UTType] { [.rfCSV] }

    var text: String

    init(text: String = "") {
        self.text = text
    }

    init(configuration: ReadConfiguration) throws {
        text = ""
    }

    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper {
        FileWrapper(regularFileWithContents: Data(text.utf8))
    }
}
