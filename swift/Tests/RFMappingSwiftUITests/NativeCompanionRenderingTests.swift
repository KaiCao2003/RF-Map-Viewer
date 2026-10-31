import AppKit
import SwiftUI
import XCTest
@testable import RFMappingSwiftUI

@MainActor
private func withNativeHost<Content: View>(
    _ content: Content, size: CGSize,
    check: (NSHostingView<Content>) async throws -> Void
) async throws {
    _ = NSApplication.shared
    let window = NSWindow(
        contentRect: CGRect(origin: .zero, size: size), styleMask: [.titled, .closable],
        backing: .buffered, defer: false
    )
    window.isReleasedWhenClosed = false
    defer { window.close() }
    let host = NSHostingView(rootView: content)
    window.contentView = host
    window.orderFront(nil)
    host.layoutSubtreeIfNeeded()
    window.update()
    try await Task.sleep(for: .milliseconds(250))
    host.layoutSubtreeIfNeeded()
    XCTAssertEqual(host.bounds.width, size.width, accuracy: 1)
    XCTAssertEqual(host.bounds.height, size.height, accuracy: 1)
    try await check(host)
}

@MainActor
private func nativeBitmap(_ host: NSView, name: String) throws -> NSBitmapImageRep {
    host.displayIfNeeded()
    let bitmap = try XCTUnwrap(host.bitmapImageRepForCachingDisplay(in: host.bounds))
    host.cacheDisplay(in: host.bounds, to: bitmap)
    XCTAssertGreaterThan(bitmap.pixelsWide, 0)
    XCTAssertGreaterThan(bitmap.pixelsHigh, 0)
    if let directory = ProcessInfo.processInfo.environment["RFMAPPING_NATIVE_RENDER_DIR"] {
        let folder = URL(fileURLWithPath: directory, isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try XCTUnwrap(bitmap.representation(using: .png, properties: [:]))
            .write(to: folder.appendingPathComponent(name + ".png"))
    }
    return bitmap
}

@MainActor
private func checkWhiteCanvas(_ bitmap: NSBitmapImageRep, region: CGRect, requireColor: Bool = false) throws {
    var sampled = 0, white = 0, ink = 0, colored = 0
    var minimumAlpha: CGFloat = 1
    let xRange = Int(region.minX * CGFloat(bitmap.pixelsWide))..<Int(region.maxX * CGFloat(bitmap.pixelsWide))
    let yRange = Int(region.minY * CGFloat(bitmap.pixelsHigh))..<Int(region.maxY * CGFloat(bitmap.pixelsHigh))
    for y in stride(from: yRange.lowerBound, to: yRange.upperBound, by: 3) {
        for x in stride(from: xRange.lowerBound, to: xRange.upperBound, by: 3) {
            let color = try XCTUnwrap(bitmap.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB))
            minimumAlpha = min(minimumAlpha, color.alphaComponent)
            let low = min(color.redComponent, min(color.greenComponent, color.blueComponent))
            let high = max(color.redComponent, max(color.greenComponent, color.blueComponent))
            sampled += 1
            if low > 0.97 { white += 1 }
            if high < 0.65 { ink += 1 }
            if high - low > 0.2 { colored += 1 }
        }
    }
    XCTAssertGreaterThan(minimumAlpha, 0.99, "The canvas must be opaque")
    XCTAssertGreaterThan(Double(white) / Double(sampled), 0.6, "Expected an explicit white plotting background")
    XCTAssertGreaterThan(ink, 5, "The native view rendered blank instead of readable content")
    if requireColor { XCTAssertGreaterThan(colored, 5, "HD class marks must retain their colors") }
}

@MainActor
final class NativeCompanionRenderingTests: XCTestCase {
    func testADNClassFiltersAndProbeCanvasRenderNatively() async throws {
        let geometry = ProbeGeometry(
            probeName: "ProbeA", positionsURL: URL(fileURLWithPath: "/synthetic/positions.probe"),
            channelsURL: nil, channels: [],
            units: (0..<5).map { index in
                ProbeUnitPosition(unitID: index + 7, xMicrometers: Double(index % 2) * 20,
                                  yMicrometers: Double(index) * 100)
            }
        )
        let view = ADNAnnotationView(
            geometry: geometry, savedUnitIDs: [7, 10],
            selectionURL: URL(fileURLWithPath: "/synthetic/adn_neuron.npy"),
            hdClasses: [7: 0, 8: 1, 9: 2, 10: 3], save: { _ in XCTFail("Rendering must not save data") }
        )
        .frame(width: 800, height: 640)
        .background(Color.white)
        .preferredColorScheme(.light)
        try await withNativeHost(view, size: CGSize(width: 800, height: 640)) { host in
            let bitmap = try nativeBitmap(host, name: "adn-class-filters")
            try checkWhiteCanvas(bitmap, region: CGRect(x: 0, y: 0, width: 1, height: 1), requireColor: true)
        }
    }
}

extension IndexedRFArchiveTests {
    @MainActor
    func testSavedResultInspectorRendersAndScrollsAllFourLayers() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let source = try savedSource(directory)
        var results: [String: SavedRFResult] = [:]
        for layer in RFOverlayLayer.allCases {
            let oneDimensional = layer.dimension == "1d"
            let shape = oneDimensional ? [1, 1, 2] : [1, 2, 2]
            let mask: [UInt8] = oneDimensional ? [1, 0] : [1, 0, 0, 0]
            let manifest: [String: Any] = [
                "x_positions": source.xPositions,
                "y_positions": oneDimensional ? [0.0] : source.yPositions,
                "response_units": "Hz", "response_normalization": "presentation_count_time",
                "detector_algorithm": "pooled-spatial-z-v3-zero-filled",
                "baseline": [
                    "unit_ids": [42], "mean_hz": [10.0], "sd_hz": [2.0],
                    "mean_scope": "global", "sd_scope": "global", "sd_ddof": 0,
                    "response_stage": "after_gui_smoothing1",
                ],
            ]
            try savedArchive(shape: shape, mask: mask, center: mask, axis: oneDimensional ? "x" : nil,
                             polarity: layer.polarity.rawValue, manifestOverrides: manifest)
                .write(to: SavedRFResult.resultURL(source: source.url, dimension: layer.dimension,
                                                  polarity: layer.polarity.rawValue))
            results[layer.resultKey] = try SavedRFResult.load(source: source, dimension: layer.dimension,
                                                            polarity: layer.polarity.rawValue)
        }
        let view = ScrollView {
            SavedRFResultInspectorView(source: source, unitID: 42, results: results, errors: [:],
                                       layers: RFOverlayLayer.allCases)
        }
        .background(Color.white)
        .preferredColorScheme(.light)
        .frame(width: 460, height: 240)
        try await withNativeHost(view, size: CGSize(width: 460, height: 240)) { host in
            let bitmap = try nativeBitmap(host, name: "saved-result-inspector-top")
            try checkWhiteCanvas(bitmap, region: CGRect(x: 0.03, y: 0.03, width: 0.9, height: 0.94))
            @MainActor func scrollViews(in view: NSView) -> [NSScrollView] {
                if let scroll = view as? NSScrollView { return [scroll] }
                return view.subviews.flatMap { scrollViews(in: $0) }
            }
            let scroll = try XCTUnwrap(scrollViews(in: host).first)
            let document = try XCTUnwrap(scroll.documentView)
            XCTAssertGreaterThan(document.bounds.height, scroll.contentView.bounds.height,
                                 "All four layers must remain reachable in a compact Inspector")
            scroll.contentView.scroll(to: NSPoint(x: 0, y: document.bounds.maxY - scroll.contentView.bounds.height))
            scroll.reflectScrolledClipView(scroll.contentView)
            try await Task.sleep(for: .milliseconds(100))
            XCTAssertGreaterThan(scroll.contentView.bounds.minY, 0)
            _ = try nativeBitmap(host, name: "saved-result-inspector-bottom")
        }
    }
}
