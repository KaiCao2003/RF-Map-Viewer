import Foundation
import XCTest
@testable import RFMappingSwiftUI

final class ViewerPreferencesTests: XCTestCase {
    @MainActor
    func testTuningPreferencesPersistPhysicalSmoothingWidthAndScaleMode() {
        let suiteName = "ViewerPreferencesTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defer { defaults.removePersistentDomain(forName: suiteName) }

        let preferences = ViewerPreferences(defaults: defaults)
        XCTAssertTrue(preferences.showTuningCurve)
        XCTAssertEqual(preferences.tuningDisplayBins, 30)
        XCTAssertEqual(preferences.tuningSmoothingDegrees, 18.0)
        XCTAssertFalse(preferences.tuningCompareScale)

        preferences.tuningPlotMode = .polar
        preferences.tuningLayout = .stacked
        preferences.tuningDisplayBins = 60
        preferences.tuningSmoothingDegrees = 24.0
        preferences.tuningCompareScale = true

        let restored = ViewerPreferences(defaults: defaults)
        XCTAssertEqual(restored.tuningPlotMode, .polar)
        XCTAssertEqual(restored.tuningLayout, .stacked)
        XCTAssertEqual(restored.tuningDisplayBins, 60)
        XCTAssertEqual(restored.tuningSmoothingDegrees, 24.0)
        XCTAssertTrue(restored.tuningCompareScale)
    }

    @MainActor
    func testInvalidStoredTuningValuesReturnToScientificDefaults() {
        let suiteName = "ViewerPreferencesTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defer { defaults.removePersistentDomain(forName: suiteName) }

        defaults.set("Unknown", forKey: "viewer.tuningPlotMode")
        defaults.set("Unknown", forKey: "viewer.tuningLayout")
        defaults.set(17, forKey: "viewer.tuningDisplayBins")
        defaults.set(Double.nan, forKey: "viewer.tuningSmoothingDegrees")

        let preferences = ViewerPreferences(defaults: defaults)
        XCTAssertEqual(preferences.tuningPlotMode, .automatic)
        XCTAssertEqual(preferences.tuningLayout, .sideBySide)
        XCTAssertEqual(preferences.tuningDisplayBins, 30)
        XCTAssertEqual(preferences.tuningSmoothingDegrees, 18.0)
    }
}
