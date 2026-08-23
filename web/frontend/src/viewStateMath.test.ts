import { describe, expect, it } from "vitest";
import { timeGroups } from "./math";
import type { DatasetMeta, ViewState } from "./types";
import { resolutionChangePatch, timelineSelectionPatch } from "./viewStateMath";

const meta: DatasetMeta = {
  id: "fixture",
  name: "fixture.json",
  sourcePath: "/mnt/senzailab/fixture.json",
  shape: [1, 1, 1, 4],
  unitPool: [17],
  xPositions: [0],
  yPositions: [0],
  timeBinEdges: [-0.1, -0.05, 0, 0.05, 0.1],
  occupancyTimeSec: [[1]],
  responseUnits: "spike_count",
  responseNormalization: "none",
  capabilities: { probe: false, hd: false, occupancy: true },
};

const state: ViewState = {
  clusterId: 17,
  valueMode: "Spike count",
  activeTimeCenterMs: -75,
  timelineStartMs: -100,
  timelineEndMs: 100,
  timelineAnchorMs: null,
  rfStartMs: 0,
  rfEndMs: 100,
  timeResolutionMs: 50,
  xBins: 1,
  yBins: 1,
  smoothRadius: 0,
  flipY: false,
  palette: "Gray",
  polarRadius: "Display bottom inner",
  polarLayout: false,
  rgbMode: false,
  selectedCellYMidpoint: null,
  selectedCellXMidpoint: null,
  timelineScrollFraction: 0,
  selectedTab: "timeline",
};

describe("viewer timeline state parity", () => {
  it("anchors the first modifier click at the current range start and advances the anchor", () => {
    const groups = timeGroups(meta, 50);
    const first = { ...state, ...timelineSelectionPatch(meta, state, groups, 2, true) };
    expect([first.timelineStartMs, first.timelineEndMs, first.timelineAnchorMs]).toEqual([-100, 50, 25]);
    const second = timelineSelectionPatch(meta, first, groups, 3, true);
    expect([second.timelineStartMs, second.timelineEndMs, second.timelineAnchorMs]).toEqual([0, 100, 75]);
  });

  it("remaps active and selection source bins to exact new resolution bounds", () => {
    const selected = { ...state, activeTimeCenterMs: -25, timelineStartMs: -50, timelineEndMs: 0 };
    expect(resolutionChangePatch(meta, selected, 100)).toMatchObject({
      timeResolutionMs: 100,
      activeTimeCenterMs: -50,
      timelineStartMs: -100,
      timelineEndMs: 0,
      timelineAnchorMs: null,
    });
  });
});
