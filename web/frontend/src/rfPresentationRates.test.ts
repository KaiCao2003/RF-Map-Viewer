import { describe, expect, it } from "vitest";
import { allPositionsTimelineValues, groupResponseValue, prepareResponseMatrix, prepareRfResponse, responseValue, supportsFiringRate, unitMetrics } from "./math";
import { DEFAULT_VIEWER_SETTINGS, viewerSettingsPatch } from "./viewerSettings";
import type { DatasetMeta, ViewState } from "./types";

const meta: DatasetMeta = {
  id: "rate", name: "rate", sourcePath: "/rate.rfmap", shape: [1, 1, 2, 2], unitPool: [7],
  xPositions: [0, 1], yPositions: [0], timeBinEdges: [0, 0.01, 0.03],
  occupancyTimeSec: [[20, 90]], stimulusPresentationCounts: [[10, 30]],
  responseUnits: "spike_count", responseNormalization: "none",
  capabilities: { probe: false, hd: false, waveform: false, occupancy: true },
};
const counts = Float64Array.from([10, 20, 30, 60]);

describe("RF presentation exposure", () => {
  it("uses the actual response duration for native and unequal timeline bins", () => {
    expect(responseValue(counts, meta, 0, 0, 0, 0, "Mean firing rate (Hz)")).toBeCloseTo(100);
    expect(responseValue(counts, meta, 0, 0, 1, 1, "Mean firing rate (Hz)")).toBeCloseTo(100);
    expect(responseValue(counts, meta, 0, 0, 0, 1, "Mean firing rate (Hz)")).toBeCloseTo(100);
    expect(groupResponseValue(counts, meta, [0, 0, 0, 1], [0, 1], "Mean firing rate (Hz)")).toBeCloseTo(100);
    expect(allPositionsTimelineValues(counts, meta, [[0, 0], [1, 1]], "Mean firing rate (Hz)")[1]).toBeCloseTo(100);
  });

  it("normalizes A and B separately before subtracting unequal windows", () => {
    const state = { rfStartMs: 10, rfEndMs: 30, rfBStartMs: 0, rfBEndMs: 10, rfWindowMode: "difference",
      valueMode: "Mean firing rate (Hz)", xBins: 2, yBins: 1, flipY: false, smoothRadius: 0 } as ViewState;
    for (const value of prepareRfResponse(counts, meta, state).matrix[0]) expect(value).toBeCloseTo(0);
  });

  it("keeps legacy input in count mode and chooses its strongest count cell", () => {
    const legacy = { ...meta, stimulusPresentationCounts: null };
    expect(supportsFiringRate(legacy)).toBe(false);
    expect(viewerSettingsPatch(legacy, DEFAULT_VIEWER_SETTINGS).valueMode).toBe("Spike count");
    expect(responseValue(counts, legacy, 0, 0, 0, 1, "Mean firing rate (Hz)")).toBeNull();
    expect(unitMetrics(counts, legacy)).toMatchObject({ bestX: 1, bestRateHz: null });
    expect(responseValue(counts, legacy, 0, 1, 0, 1, "Spike count")).toBe(90);
  });
});

it("does not invent response exposure in zero-presentation cells when smoothing", () => {
  const missing = { ...meta, stimulusPresentationCounts: [[0, 30]] };
  const observed = Float64Array.from([0, 0, 30, 60]);
  const display = prepareResponseMatrix(observed, missing, [0, 1], "Mean firing rate (Hz)", 2, 1, false, 1);
  expect(display.matrix[0][0]).toBeNull();
  expect(display.matrix[0][1]).toBeCloseTo(100);
  const grouped = prepareResponseMatrix(observed, missing, [0, 1], "Mean firing rate (Hz)", 1, 1, false, 1);
  expect(grouped.matrix[0][0]).toBeCloseTo(100);
});
