import { describe, expect, it } from "vitest";
import { prepareRfResponse } from "./math";
import { readRfTiming, resetRfTiming, timingFromState, timingPatch, toggleRfMode } from "./rfTiming";
import type { DatasetMeta, ViewState } from "./types";

const meta: DatasetMeta = {
  id: "rf", name: "synthetic.rfmap", sourcePath: "/fixture", shape: [1, 1, 2, 3],
  unitPool: [1], xPositions: [0, 1], yPositions: [0], timeBinEdges: [-0.1, 0, 0.1, 0.2],
  occupancyTimeSec: [[1, 3]], responseUnits: "spike_count", responseNormalization: "none",
  capabilities: { probe: false, hd: false, waveform: false, occupancy: true },
};
const state = { valueMode: "Mean firing rate (Hz)", xBins: 1, yBins: 1, flipY: false, smoothRadius: 1,
  ...timingPatch(meta, { mode: "sum", sum: [-100, 200], a: [100, 200], b: [0, 100] }) } as ViewState;

describe("RF timing parity", () => {
  it("preserves independent sum and difference ranges when switching", () => {
    const difference = { ...state, ...toggleRfMode(meta, state) };
    expect([difference.rfStartMs, difference.rfEndMs]).toEqual([100, 200]);
    const edited = { ...difference, rfStartMs: 0 };
    const sum = { ...edited, ...toggleRfMode(meta, edited) };
    expect([sum.rfStartMs, sum.rfEndMs]).toEqual([-100, 200]);
    expect(timingFromState(sum).a).toEqual([0, 200]);
    expect(readRfTiming(JSON.stringify(timingFromState(sum)))).toEqual(timingFromState(sum));
  });
  it("subtracts independently pooled and smoothed rates; preserves zero and masks negatives", () => {
    const counts = Float64Array.from([0, 1, 5, 0, 3, 7]);
    const difference = { ...state, ...toggleRfMode(meta, state) };
    expect(prepareRfResponse(counts, meta, difference).matrix).toEqual([[2]]);
    expect(prepareRfResponse(counts, meta, { ...difference, rfStartMs: 0, rfEndMs: 100, rfBStartMs: 100, rfBEndMs: 200 }).matrix).toEqual([[null]]);
    expect(prepareRfResponse(counts, meta, { ...difference, rfBStartMs: 100, rfBEndMs: 200 }).matrix).toEqual([[0]]);
  });
  it("resets only the active mode while preserving the other mode's last window", () => {
    const difference = { ...state, ...toggleRfMode(meta, state), rfStartMs: 0 };
    const reset = { ...difference, ...resetRfTiming(meta, difference, { mode: "sum", sum: [0, 100], a: [100, 200], b: [0, 100] }) };
    expect(timingFromState(reset)).toEqual({ mode: "difference", sum: [-100, 200], a: [100, 200], b: [0, 100] });
  });
});
