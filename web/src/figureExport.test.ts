import { describe, expect, it } from "vitest";
import {
  FIGURE_TYPE_IDS,
  buildFigureExportRequest,
  buildFigurePreviewRequest,
  composerValidationError,
  createFigureComposerState,
  currentFigureType,
  figureComposerReducer,
  matchingUnitIds,
  snapshotPlotSettings,
  type FigurePageDraft,
} from "./figureExport";
import type { HdViewSettings, ViewState } from "./types";

const view: ViewState = {
  clusterId: 7,
  valueMode: "Spike count",
  activeTimeCenterMs: 25,
  timelineStartMs: -50,
  timelineEndMs: 250,
  timelineAnchorMs: null,
  rfStartMs: 0,
  rfEndMs: 200,
  timeResolutionMs: 10,
  xBins: 30,
  yBins: 7,
  smoothRadius: 1,
  flipY: true,
  palette: "Inferno",
  polarRadius: "Display bottom inner",
  polarLayout: false,
  rgbMode: false,
  selectedCellYMidpoint: 2,
  selectedCellXMidpoint: 3,
  timelineScrollFraction: 0.25,
  selectedTab: "rf",
};

const hd: HdViewSettings = {
  plotMode: "line",
  displayBins: 60,
  smoothing: true,
  sigmaDeg: 12.5,
  compareScale: true,
};

function state() {
  return createFigureComposerState({
    unitPool: [41, 7, 88, 3],
    currentClusterId: 7,
    initialType: "rf.cartesian",
    initialSettings: snapshotPlotSettings("rf.cartesian", {
      view,
      selectedCell: [1, 2, 3, 4],
      hd,
    }),
    baseName: "session_figures",
  });
}

describe("figure export plan reducer", () => {
  it("uses all ten backend-stable figure IDs", () => {
    expect(FIGURE_TYPE_IDS).toEqual([
      "rf.cartesian",
      "rf.polar",
      "delay.cartesian",
      "delay.polar",
      "rgb.cartesian",
      "rgb.polar",
      "timeline.current",
      "hd.line",
      "hd.polar",
      "probe",
    ]);
  });

  it("preserves original unitPool order for presets, checkboxes, and payloads", () => {
    let draft = state();
    draft = figureComposerReducer(draft, { type: "set-units", unitIds: [3, 41, 88] });
    expect(draft.selectedUnitIds).toEqual([41, 88, 3]);
    draft = figureComposerReducer(draft, { type: "toggle-unit", unitId: 7 });
    expect(draft.selectedUnitIds).toEqual([41, 7, 88, 3]);
    draft = figureComposerReducer(draft, { type: "toggle-unit", unitId: 88 });
    expect(buildFigureExportRequest(draft, 1).clusterIds).toEqual([41, 7, 3]);
  });

  it("adds, renames, removes, and reorders page plots without allowing empty pages", () => {
    let draft = state();
    const second: FigurePageDraft = {
      id: "page-2",
      title: "Delay",
      plots: [{ id: "plot-2", type: "delay.polar", settings: { timeResolutionMs: 20 } }],
    };
    draft = figureComposerReducer(draft, { type: "add-page", page: second });
    draft = figureComposerReducer(draft, { type: "rename-page", pageId: "page-2", title: "Temporal" });
    draft = figureComposerReducer(draft, {
      type: "add-plot",
      pageId: "page-2",
      maximum: 12,
      plot: { id: "plot-3", type: "hd.line", settings: { displayBins: 60 } },
    });
    draft = figureComposerReducer(draft, { type: "move-plot", pageId: "page-2", plotId: "plot-3", delta: -1 });
    expect(draft.pages[1].title).toBe("Temporal");
    expect(draft.pages[1].plots.map((plot) => plot.type)).toEqual(["hd.line", "delay.polar"]);
    draft = figureComposerReducer(draft, { type: "remove-plot", pageId: "page-2", plotId: "plot-3" });
    draft = figureComposerReducer(draft, { type: "remove-plot", pageId: "page-2", plotId: "plot-2" });
    expect(draft.pages[1].plots).toHaveLength(1);
    draft = figureComposerReducer(draft, { type: "remove-page", pageId: "page-2" });
    draft = figureComposerReducer(draft, { type: "remove-page", pageId: "page-1" });
    expect(draft.pages).toHaveLength(1);
  });

  it("reorders page templates while keeping active and preview page IDs stable", () => {
    let draft = state();
    draft = figureComposerReducer(draft, {
      type: "add-page",
      page: {
        id: "page-2",
        title: "Temporal",
        plots: [{ id: "plot-2", type: "delay.cartesian", settings: {} }],
      },
    });
    draft = figureComposerReducer(draft, {
      type: "add-page",
      page: {
        id: "page-3",
        title: "Timeline",
        plots: [{ id: "plot-3", type: "timeline.current", settings: {} }],
      },
    });
    draft = figureComposerReducer(draft, { type: "set-preview-page", pageId: "page-2" });

    draft = figureComposerReducer(draft, { type: "move-page", pageId: "page-3", delta: -1 });
    expect(draft.pages.map((page) => page.id)).toEqual(["page-1", "page-3", "page-2"]);
    expect(draft.activePageId).toBe("page-3");
    expect(draft.previewPageId).toBe("page-2");
    expect(buildFigurePreviewRequest(draft, 1).pageIndex).toBe(2);
    expect(buildFigureExportRequest(draft, 1).pages.map((page) => page.title)).toEqual([
      "Page 1",
      "Timeline",
      "Temporal",
    ]);

    const unchanged = figureComposerReducer(draft, { type: "move-page", pageId: "page-1", delta: -1 });
    expect(unchanged).toBe(draft);
  });

  it("freezes timeline selection and lifted HD settings into plot snapshots", () => {
    const context = { view, selectedCell: [1, 2, 3, 4] as const, hd };
    expect(snapshotPlotSettings("timeline.current", context)).toMatchObject({
      timelineStartMs: -50,
      timelineEndMs: 250,
      activeTimeCenterMs: 25,
      timeResolutionMs: 10,
      polarLayout: false,
      spatialProjection: { yStart: 1, yEnd: 2, xStart: 3, xEnd: 4 },
    });
    expect(snapshotPlotSettings("hd.polar", context)).toEqual({
      displayBins: 60,
      smoothing: true,
      sigmaDeg: 12.5,
    });
  });

  it("serializes the same manual companion paths for preview and final export", () => {
    const draft = state();
    const companions = {
      hdPath: "/mnt/senzailab/session/tuning_curves.json",
      probePositionsPath: "/mnt/senzailab/session/positions.csv",
    };
    expect(buildFigurePreviewRequest(draft, 1, companions)).toMatchObject(companions);
    expect(buildFigureExportRequest(draft, 1, companions)).toMatchObject(companions);
    expect(buildFigurePreviewRequest(draft, 1)).not.toHaveProperty("hdPath");
  });

  it("validates selection, names, and writable destinations", () => {
    let draft = state();
    expect(composerValidationError(draft, true)).toBeNull();
    draft = figureComposerReducer(draft, { type: "set-units", unitIds: [] });
    expect(composerValidationError(draft, true)).toBe("Select at least one unit.");
    draft = figureComposerReducer(draft, { type: "set-units", unitIds: [7] });
    draft = figureComposerReducer(draft, { type: "set-base-name", value: "../bad" });
    expect(composerValidationError(draft, true)).toContain("File name");
    draft = figureComposerReducer(draft, { type: "set-base-name", value: "safe" });
    expect(composerValidationError(draft, false)).toContain("writable");
  });

  it("searches original indices and cluster IDs and derives the current view type", () => {
    expect(matchingUnitIds([41, 700, 88], "001")).toEqual([700]);
    expect(matchingUnitIds([41, 700, 88], "cluster 88")).toEqual([88]);
    expect(currentFigureType(view)).toBe("rf.cartesian");
    expect(currentFigureType({ ...view, selectedTab: "delay", rgbMode: true, polarLayout: true })).toBe("rgb.polar");
    expect(currentFigureType({ ...view, selectedTab: "timeline" })).toBe("timeline.current");
  });
});
