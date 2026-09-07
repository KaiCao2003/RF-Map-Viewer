// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "RFMappingSwiftUI",
    platforms: [
        .macOS("15.0")
    ],
    products: [
        .executable(name: "RFMappingSwiftUI", targets: ["RFMappingSwiftUI"]),
        .executable(name: "TCComparisonApp", targets: ["TCComparisonApp"])
    ],
    targets: [
        .target(name: "TuningCurveCore"),
        .executableTarget(
            name: "RFMappingSwiftUI",
            dependencies: ["TuningCurveCore"]
        ),
        .executableTarget(
            name: "TCComparisonApp",
            dependencies: ["TuningCurveCore"]
        ),
        .testTarget(
            name: "RFMappingSwiftUITests",
            dependencies: ["RFMappingSwiftUI"],
            path: "Tests/RFMappingSwiftUITests"
        ),
        .testTarget(
            name: "TuningCurveCoreTests",
            dependencies: ["TuningCurveCore"]
        )
    ]
)
