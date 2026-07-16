// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "RFMappingSwiftUI",
    platforms: [
        .macOS("15.0")
    ],
    products: [
        .executable(name: "RFMappingSwiftUI", targets: ["RFMappingSwiftUI"])
    ],
    targets: [
        .executableTarget(name: "RFMappingSwiftUI"),
        .testTarget(
            name: "RFMappingSwiftUITests",
            dependencies: ["RFMappingSwiftUI"],
            path: "tests/RFMappingSwiftUITests"
        )
    ]
)
