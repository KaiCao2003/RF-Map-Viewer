import AppKit
import Foundation
import SwiftUI
import XCTest
@testable import RFMappingSwiftUI

@MainActor
final class StartupBehaviorTests: XCTestCase {
    func testHostedWelcomeDoesNotOpenAPicker() async throws {
        _ = NSApplication.shared
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 480, height: 632),
            styleMask: [.titled, .closable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.isReleasedWhenClosed = false
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        WindowRouter.shared.registerWelcomeWindow(window)
        defer { window.close() }
        var openCount = 0
        let host = NSHostingView(rootView:
            WelcomeWindowContent {
                WelcomeView(openDocument: { openCount += 1 }, openRecent: { _ in openCount += 1 })
            }
        )
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        window.update()
        try await Task.sleep(for: .milliseconds(250))

        XCTAssertEqual(host.fittingSize.width, 480, accuracy: 0.1)
        XCTAssertEqual(host.fittingSize.height, 632, accuracy: 0.1)
        XCTAssertTrue(window.canBecomeKey)
        XCTAssertEqual(openCount, 0)
        XCTAssertNil(window.attachedSheet)
        XCTAssertNil(NSApplication.shared.modalWindow)
    }

    func testFileReferenceURLCompanionDiscoveryStopsAtFilesystemRoot() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("#Recording/260918_10/data/rfmapping/ProbeA/map.rfmap")
        try FileManager.default.createDirectory(
            at: file.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        try Data().write(to: file)
        let reference = try XCTUnwrap((file as NSURL).fileReferenceURL())
        XCTAssertNil(try WaveformArtifactStore.discover(forRFURL: reference))
        XCTAssertNil(ProbeGeometryDiscovery.discover(forRFURL: reference))
    }

    func testURLLaunchQueuedBeforeWelcomeOpensOnePreparedDocument() async throws {
        let url = try makeRFDocument()
        defer { try? FileManager.default.removeItem(at: url) }
        let router = WindowRouter()
        let delegate = AppDelegate(windowRouter: router)
        let opened = expectation(description: "Queued Finder document is opened")
        var requests: [DocumentWindowRequest] = []

        delegate.application(NSApplication.shared, open: [url])
        router.install { request in
            requests.append(request)
            opened.fulfill()
        }
        await fulfillment(of: [opened], timeout: 2)

        XCTAssertEqual(requests.count, 1)
        let request = try XCTUnwrap(requests.first)
        XCTAssertEqual(router.takePreparedDocument(for: request.id)?.unitPool, [22])
        XCTAssertNil(router.takePreparedDocument(for: request.id))
    }

    func testExternalOpensUseSeparatePreparedDocumentWindows() async throws {
        let url = try makeRFDocument()
        defer { try? FileManager.default.removeItem(at: url) }
        let router = WindowRouter()
        var requests: [DocumentWindowRequest] = []
        router.install { requests.append($0) }
        let completed = expectation(description: "All external documents opened")
        router.openExternal([url, url, url]) { succeeded in
            XCTAssertTrue(succeeded)
            completed.fulfill()
        }
        await fulfillment(of: [completed], timeout: 2)

        XCTAssertEqual(requests.count, 3)
        XCTAssertEqual(Set(requests.map(\.id)).count, 3)
        for request in requests {
            XCTAssertEqual(request.path, url.standardizedFileURL.path)
            XCTAssertEqual(router.takePreparedDocument(for: request.id)?.unitPool, [22])
        }
    }

    func testNativeRecentOpenUsesTheDocumentRoute() async throws {
        let url = try makeRFDocument()
        defer { try? FileManager.default.removeItem(at: url) }
        let router = WindowRouter()
        let delegate = AppDelegate(windowRouter: router)
        let opened = expectation(description: "Native recent document is routed")
        router.install { request in
            XCTAssertEqual(request.path, url.standardizedFileURL.path)
            XCTAssertNotNil(router.takePreparedDocument(for: request.id))
            opened.fulfill()
        }

        XCTAssertTrue(delegate.application(NSApplication.shared, openFile: url.path))
        await fulfillment(of: [opened], timeout: 2)
    }

    func testWelcomeStaysVisibleUntilAReadyDocumentAppearsAndCanBeReused() {
        _ = NSApplication.shared
        let welcome = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 480, height: 632),
            styleMask: [.titled, .closable], backing: .buffered, defer: false
        )
        welcome.isReleasedWhenClosed = false
        defer { welcome.close() }
        let router = WindowRouter()
        var isImporting = true
        router.registerWelcomeWindow(welcome, dismissImporter: { isImporting = false })
        welcome.orderFront(nil)
        router.install { _ in }
        XCTAssertTrue(welcome.isVisible)

        router.documentDidAppear()
        XCTAssertFalse(isImporting)
        XCTAssertFalse(welcome.isVisible)
        var openedNewWindow = false
        router.showWelcome { openedNewWindow = true }
        XCTAssertFalse(openedNewWindow, "The ordered-out welcome window must be reused")
        XCTAssertTrue(welcome.isVisible)
        router.showWelcome { XCTFail("The visible welcome window must be reused") }
    }

    private func makeRFDocument() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".rfmap")
        let payload = currentRFSchemaPayload([
            "unitsSpikeCounts": [[[[1.0]]]],
            "unitsSpikeCountsSize": [1, 1, 1, 1],
            "unitPool": [22], "xPositions": [0.0], "yPositions": [0.0],
            "timeBinEdges": [0.0, 0.1],
        ], occupancyTimeSec: 0.1, occupancyTimeSecSize: [1, 1])
        try JSONSerialization.data(withJSONObject: payload).write(to: url)
        return url
    }
}
