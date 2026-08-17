import Foundation

struct ViewerSyncFields: OptionSet, Sendable {
    let rawValue: UInt32

    static let unit = Self(rawValue: 1 << 0)
    static let valueMode = Self(rawValue: 1 << 1)
    static let activeTime = Self(rawValue: 1 << 2)
    static let timelineSelection = Self(rawValue: 1 << 3)
    static let plotRange = Self(rawValue: 1 << 4)
    static let timeResolution = Self(rawValue: 1 << 5)
    static let xBins = Self(rawValue: 1 << 6)
    static let yBins = Self(rawValue: 1 << 7)
    static let smoothing = Self(rawValue: 1 << 8)
    static let flipY = Self(rawValue: 1 << 9)
    static let palette = Self(rawValue: 1 << 10)
    static let polarRadius = Self(rawValue: 1 << 11)
    static let spatialFormat = Self(rawValue: 1 << 12)
    static let delayRGBMode = Self(rawValue: 1 << 13)
    static let responseFloor = Self(rawValue: 1 << 14)
    static let selectedTab = Self(rawValue: 1 << 15)
    static let selectedCell = Self(rawValue: 1 << 16)
    static let timelineScroll = Self(rawValue: 1 << 17)

    static let all: Self = [
        .unit, .valueMode, .activeTime, .timelineSelection, .plotRange,
        .timeResolution, .xBins, .yBins, .smoothing, .flipY, .palette,
        .polarRadius, .spatialFormat, .delayRGBMode, .responseFloor,
        .selectedTab, .selectedCell, .timelineScroll
    ]
}

/// The durable viewer controls shared by paired windows. File identity,
/// transient hover state, dialogs, errors, and window geometry intentionally
/// remain local to each window.
struct ViewerSyncState: Equatable, Sendable {
    /// Unit identity, not a file-local array position. A paired target may not
    /// contain this ID and then deliberately enters its N/A state.
    let unitID: Int
    let valueMode: ResponseValueMode
    /// Physical time is authoritative because paired files may have different
    /// time axes, grouping, spatial grids, and unit lists.
    let activeTimeMS: Double
    let rangeStartMS: Double
    let rangeEndMS: Double
    let plotRangeStartMS: Double
    let plotRangeEndMS: Double
    let timeResolutionMS: Double
    let xBins: Int
    let yBins: Int
    let smoothRadius: Int
    let flipY: Bool
    let palette: RFPalette
    let polarRadiusMode: PolarRadiusMode
    let spatialPlotFormat: SpatialPlotFormat
    let delayRGBMode: DelayRGBMode
    let responseFloor: Double
    let selectedTab: PlotTab
    let selectedCell: CellRef?
    let timelineRangeAnchorMS: Double?
    let timelineScrollFraction: Double

    func changedFields(comparedTo baseline: ViewerSyncState) -> ViewerSyncFields {
        var fields: ViewerSyncFields = []
        if unitID != baseline.unitID { fields.insert(.unit) }
        if valueMode != baseline.valueMode { fields.insert(.valueMode) }
        if activeTimeMS != baseline.activeTimeMS { fields.insert(.activeTime) }
        if rangeStartMS != baseline.rangeStartMS
            || rangeEndMS != baseline.rangeEndMS
            || timelineRangeAnchorMS != baseline.timelineRangeAnchorMS {
            fields.insert(.timelineSelection)
        }
        if plotRangeStartMS != baseline.plotRangeStartMS
            || plotRangeEndMS != baseline.plotRangeEndMS {
            fields.insert(.plotRange)
        }
        if timeResolutionMS != baseline.timeResolutionMS { fields.insert(.timeResolution) }
        if xBins != baseline.xBins { fields.insert(.xBins) }
        if yBins != baseline.yBins { fields.insert(.yBins) }
        if smoothRadius != baseline.smoothRadius { fields.insert(.smoothing) }
        if flipY != baseline.flipY { fields.insert(.flipY) }
        if palette != baseline.palette { fields.insert(.palette) }
        if polarRadiusMode != baseline.polarRadiusMode { fields.insert(.polarRadius) }
        if spatialPlotFormat != baseline.spatialPlotFormat { fields.insert(.spatialFormat) }
        if delayRGBMode != baseline.delayRGBMode { fields.insert(.delayRGBMode) }
        if responseFloor != baseline.responseFloor { fields.insert(.responseFloor) }
        if selectedTab != baseline.selectedTab { fields.insert(.selectedTab) }
        if selectedCell != baseline.selectedCell { fields.insert(.selectedCell) }
        if abs(timelineScrollFraction - baseline.timelineScrollFraction) > 1e-6 {
            fields.insert(.timelineScroll)
        }
        return fields
    }

    func merging(_ incoming: ViewerSyncState, fields: ViewerSyncFields) -> ViewerSyncState {
        ViewerSyncState(
            unitID: fields.contains(.unit) ? incoming.unitID : unitID,
            valueMode: fields.contains(.valueMode) ? incoming.valueMode : valueMode,
            activeTimeMS: fields.contains(.activeTime) ? incoming.activeTimeMS : activeTimeMS,
            rangeStartMS: fields.contains(.timelineSelection) ? incoming.rangeStartMS : rangeStartMS,
            rangeEndMS: fields.contains(.timelineSelection) ? incoming.rangeEndMS : rangeEndMS,
            plotRangeStartMS: fields.contains(.plotRange) ? incoming.plotRangeStartMS : plotRangeStartMS,
            plotRangeEndMS: fields.contains(.plotRange) ? incoming.plotRangeEndMS : plotRangeEndMS,
            timeResolutionMS: fields.contains(.timeResolution) ? incoming.timeResolutionMS : timeResolutionMS,
            xBins: fields.contains(.xBins) ? incoming.xBins : xBins,
            yBins: fields.contains(.yBins) ? incoming.yBins : yBins,
            smoothRadius: fields.contains(.smoothing) ? incoming.smoothRadius : smoothRadius,
            flipY: fields.contains(.flipY) ? incoming.flipY : flipY,
            palette: fields.contains(.palette) ? incoming.palette : palette,
            polarRadiusMode: fields.contains(.polarRadius) ? incoming.polarRadiusMode : polarRadiusMode,
            spatialPlotFormat: fields.contains(.spatialFormat) ? incoming.spatialPlotFormat : spatialPlotFormat,
            delayRGBMode: fields.contains(.delayRGBMode) ? incoming.delayRGBMode : delayRGBMode,
            responseFloor: fields.contains(.responseFloor) ? incoming.responseFloor : responseFloor,
            selectedTab: fields.contains(.selectedTab) ? incoming.selectedTab : selectedTab,
            selectedCell: fields.contains(.selectedCell) ? incoming.selectedCell : selectedCell,
            timelineRangeAnchorMS: fields.contains(.timelineSelection)
                ? incoming.timelineRangeAnchorMS
                : timelineRangeAnchorMS,
            timelineScrollFraction: fields.contains(.timelineScroll)
                ? incoming.timelineScrollFraction
                : timelineScrollFraction
        )
    }

    func replacingUnitID(_ unitID: Int) -> ViewerSyncState {
        ViewerSyncState(
            unitID: unitID,
            valueMode: valueMode,
            activeTimeMS: activeTimeMS,
            rangeStartMS: rangeStartMS,
            rangeEndMS: rangeEndMS,
            plotRangeStartMS: plotRangeStartMS,
            plotRangeEndMS: plotRangeEndMS,
            timeResolutionMS: timeResolutionMS,
            xBins: xBins,
            yBins: yBins,
            smoothRadius: smoothRadius,
            flipY: flipY,
            palette: palette,
            polarRadiusMode: polarRadiusMode,
            spatialPlotFormat: spatialPlotFormat,
            delayRGBMode: delayRGBMode,
            responseFloor: responseFloor,
            selectedTab: selectedTab,
            selectedCell: selectedCell,
            timelineRangeAnchorMS: timelineRangeAnchorMS,
            timelineScrollFraction: timelineScrollFraction
        )
    }

    func matchesAppliedState(_ other: ViewerSyncState) -> Bool {
        changedFields(comparedTo: other).isEmpty
    }
}
