import { describe, expect, it } from "vitest";
import { savedRfOverlay, savedResultDetails } from "./rfResults";
import { savedNativeCell } from "./rfResultDrawing";
import type { DatasetMeta, SavedRfResult } from "./types";
import type { RectSpatialLayout, PolarSpatialLayout } from "./spatialLayout";

const meta: DatasetMeta = { id: "saved", name: "saved", sourcePath: "/source.rfmap", shape: [1, 2, 3, 1],
  unitPool: [42], xPositions: [0, 1, 2], yPositions: [0, 1], timeBinEdges: [0, 0.1],
  occupancyTimeSec: [[1, 1, 1], [1, 1, 1]], responseUnits: "spike_count", responseNormalization: "none",
  capabilities: { probe: false, hd: false, waveform: false, occupancy: true } };
const result: SavedRfResult = { available: true, sourcePath: "/source.rfmap", resultPath: "/source.npz", dimension: "2d", polarity: "excitatory",
  unitIds: [42], masks: [[[1, 0, 0], [0, 1, 0]]], centers: [[[0, 0, 0], [0, 1, 0]]], axis: null,
  manifest: { time_range_s: [0.08, 0.16], parameters: { wrap_x: true } }, summary: null, summaryWarning: null, projectedFrom2d: false };

describe("saved RF results", () => {
  it("matches recorded IDs, projects the saved axis, and colors only native intersections", () => {
    const oneD: SavedRfResult = { ...result, dimension: "1d", axis: "x", masks: [[[0, 1, 0], [0, 0, 0]]] };
    const overlay = savedRfOverlay(meta, [result, oneD], 42);
    expect(overlay.flags).toEqual([[1, 2, 0], [0, 3, 0]]);
    expect(overlay.centers2d).toEqual([[1, 1]]);
    expect(overlay.centers1d).toEqual([{ axis: "x", index: 1 }]);
    expect(savedRfOverlay(meta, [result, oneD], 1)).toMatchObject({ flags: null, unavailable: ["excitatory 2D", "excitatory 1D"] });
  });

  it("keeps partial availability and reports provenance without recomputing results", () => {
    const missing: SavedRfResult = { available: false, dimension: "1d", polarity: "excitatory", resultPath: "/source_1d.npz", detail: "File missing" };
    expect(savedRfOverlay(meta, [result, missing], 42)).toMatchObject({ flags: [[1, 0, 0], [0, 1, 0]], unavailable: ["excitatory 1D"] });
    expect(savedResultDetails(result, 42)).toContain("80–160 ms");
    expect(savedResultDetails(result, 42)).toContain("/source.rfmap");
    expect(savedResultDetails(missing, 42)).toContain("File missing");
  });

  it("preserves native subcells under grouping and Y flipping", () => {
    const layout: RectSpatialLayout = { kind: "rect", x: 10, y: 20, width: 90, height: 60, cellWidth: 90, cellHeight: 60, xGroups: [[0, 2]], yGroups: [[0, 1]] };
    expect(savedNativeCell(layout, { flipY: false, polarRadius: "Display bottom inner" }, 0, 1)).toMatchObject({ x: 40, y: 20, width: 30, height: 30 });
    expect(savedNativeCell(layout, { flipY: true, polarRadius: "Display bottom inner" }, 0, 1)).toMatchObject({ x: 40, y: 50 });
  });

  it("keeps the native radial order in both polar conventions", () => {
    const layout: PolarSpatialLayout = { kind: "polar", cx: 100, cy: 100, scale: 10, totalDegrees: 360, ringSpan: 1, ringRows: [0], xGroups: [[0, 2]], yGroups: [[0, 1]] };
    expect(savedNativeCell(layout, { flipY: false, polarRadius: "MATLAB row 1 inner" }, 0, 0)).toMatchObject({ inner: 40, outer: 45 });
    expect(savedNativeCell(layout, { flipY: false, polarRadius: "Display bottom inner" }, 0, 0)).toMatchObject({ inner: 45, outer: 50 });
  });
});

it("broadcasts native collapsed 1D sidecars along only their recorded axis", () => {
  const x: SavedRfResult = { ...result, dimension: "1d", axis: "x", masks: [[[0, 1, 0]]], centers: [[[0, 1, 0]]] };
  const y: SavedRfResult = { ...result, dimension: "1d", axis: "y", masks: [[[0], [1]]], centers: [[[0], [1]]] };
  expect(savedRfOverlay(meta, [x], 42).flags).toEqual([[0, 2, 0], [0, 2, 0]]);
  expect(savedRfOverlay(meta, [y], 42).flags).toEqual([[0, 0, 0], [2, 2, 2]]);
  expect(savedRfOverlay(meta, [y], 42).centers1d).toEqual([{ axis: "y", index: 1 }]);
});

it("retains independent polarity bits when four saved layers overlap", () => {
  const oneD: SavedRfResult = { ...result, dimension: "1d", axis: "x", masks: [[[0, 1, 0]]] };
  const inhibitory: SavedRfResult = { ...result, polarity: "inhibitory" };
  expect(savedRfOverlay(meta, [result, oneD, inhibitory, { ...oneD, polarity: "inhibitory" }], 42).flags)
    .toEqual([[5, 10, 0], [0, 15, 0]]);
});
