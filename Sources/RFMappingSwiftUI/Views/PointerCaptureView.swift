import AppKit
import SwiftUI

struct PointerCaptureView: NSViewRepresentable {
    var onMove: (CGPoint) -> Void
    var onClick: (CGPoint, NSEvent.ModifierFlags) -> Void
    var onLeave: () -> Void

    func makeNSView(context: Context) -> TrackingView {
        let view = TrackingView()
        view.onMove = onMove
        view.onClick = onClick
        view.onLeave = onLeave
        return view
    }

    func updateNSView(_ nsView: TrackingView, context: Context) {
        nsView.onMove = onMove
        nsView.onClick = onClick
        nsView.onLeave = onLeave
    }

    final class TrackingView: NSView {
        var onMove: (CGPoint) -> Void = { _ in }
        var onClick: (CGPoint, NSEvent.ModifierFlags) -> Void = { _, _ in }
        var onLeave: () -> Void = {}
        private var tracking: NSTrackingArea?

        override var acceptsFirstResponder: Bool { true }

        override func updateTrackingAreas() {
            super.updateTrackingAreas()
            if let tracking {
                removeTrackingArea(tracking)
            }
            let options: NSTrackingArea.Options = [.mouseMoved, .mouseEnteredAndExited, .activeAlways, .inVisibleRect]
            let area = NSTrackingArea(rect: bounds, options: options, owner: self)
            addTrackingArea(area)
            tracking = area
        }

        override func mouseMoved(with event: NSEvent) {
            onMove(convertToTopLeft(event))
        }

        override func mouseDragged(with event: NSEvent) {
            onMove(convertToTopLeft(event))
        }

        override func mouseDown(with event: NSEvent) {
            window?.makeFirstResponder(self)
            onClick(convertToTopLeft(event), event.modifierFlags)
        }

        override func mouseExited(with event: NSEvent) {
            onLeave()
        }

        private func convertToTopLeft(_ event: NSEvent) -> CGPoint {
            let local = convert(event.locationInWindow, from: nil)
            return CGPoint(x: local.x, y: bounds.height - local.y)
        }
    }
}
