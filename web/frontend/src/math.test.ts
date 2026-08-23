import { describe, expect, it } from "vitest";
import { DEFAULT_VALUE_MODE, type DatasetMeta, type Matrix } from "./types";
import {
  allPositionsTimelineValues,
  axisGroupsForTarget,
  cellFromMidpoint,
  groupTemporalMetrics,
  halfOpenRangesOverlap,
  prepareMatrix,
  prepareResponseMatrix,
  prepareTemporalMetricMatrices,
  responseMatrix,
  responseValue,
  smoothMatrix,
  snapTimeRange,
  snappedResolutionMs,
  timeBounds,
  timeGroupForMs,
  timeGroupRangeForMs,
  timeGroups,
  unitMetrics,
} from "./math";

const meta: DatasetMeta = {
  id: "fixture",
  name: "fixture.json",
  sourcePath: "/mnt/senzailab/fixture.json",
  shape: [1, 2, 3, 4],
  unitPool: [17],
  xPositions: [-10, 0, 10],
  yPositions: [5, 15],
  timeBinEdges: [-0.1, -0.05, 0, 0.05, 0.1],
  occupancyTimeSec: [[2, 2, 2], [4, 4, 4]],
  responseUnits: "spike_count",
  responseNormalization: "none",
  capabilities: { probe: false, hd: false, occupancy: true },
};

const counts = Float64Array.from([
  0, 1, 2, 3,
  4, 0, 0, 0,
  1, 1, 1, 1,
  2, 2, 2, 2,
  0, 0, 5, 0,
  3, 1, 0, 0,
]);

describe("RF display math", () => {
  it("defaults the viewer to occupancy-normalized firing rate", () => {
    expect(DEFAULT_VALUE_MODE).toBe("Mean firing rate (Hz)");
  });

  it("uses Tk-compatible uneven target groups", () => {
    expect(axisGroupsForTarget(7, 3)).toEqual([[0, 1], [2, 3], [4, 6]]);
  });

  it("groups time bins from the minimum native resolution", () => {
    expect(timeGroups(meta, 100)).toEqual([[0, 1], [2, 3]]);
    expect(timeGroups(meta, 50)).toEqual([[0, 0], [1, 1], [2, 2], [3, 3]]);
  });

  it("uses physical edges, keeps the residual, and chooses the earlier edge on ties", () => {
    const irregular: DatasetMeta = {
      ...meta,
      shape: [1, 1, 1, 2],
      xPositions: [0],
      yPositions: [0],
      timeBinEdges: [0, 0.004, 0.012],
      occupancyTimeSec: [[1]],
    };
    expect(snappedResolutionMs(irregular, 8)).toBe(8);
    expect(timeGroups(irregular, 8)).toEqual([[0, 0], [1, 1]]);
    expect(snappedResolutionMs(irregular, 10)).toBe(8);
    const exactTie: DatasetMeta = {
      ...irregular,
      shape: [1, 1, 1, 5],
      timeBinEdges: [0, 0.125, 0.25, 0.375, 0.5, 0.625],
    };
    expect(snappedResolutionMs(exactTie, 312.5)).toBe(250);
    expect(timeGroups(exactTie, 312.5)).toEqual([[0, 1], [2, 3], [4, 4]]);
  });

  it("snaps RF ranges to nearest source edges", () => {
    expect(snapTimeRange(meta, -38, 74)).toEqual([1, 2]);
    expect(snapTimeRange(meta, 74, -38)).toEqual([1, 2]);
    expect(snapTimeRange(meta, 1000, 1000)).toEqual([3, 3]);
  });

  it("keeps source bounds intact when stepping a 1 ms resolution", () => {
    const millisecondMeta: DatasetMeta = {
      ...meta,
      shape: [1, 1, 1, 4],
      xPositions: [0],
      yPositions: [0],
      timeBinEdges: [-0.002, -0.001, 0, 0.001, 0.002],
      occupancyTimeSec: [[1]],
    };
    expect(snappedResolutionMs(millisecondMeta, 2)).toBe(2);
    expect(timeBounds(millisecondMeta, timeGroups(millisecondMeta, 2)[0])).toEqual([-2, 0]);
    expect(snappedResolutionMs(millisecondMeta, 3)).toBe(3);
    expect(timeBounds(millisecondMeta, timeGroups(millisecondMeta, 3).at(-1)!)).toEqual([1, 2]);
  });

  it("treats timeline ranges as half-open at shared edges", () => {
    expect(halfOpenRangesOverlap(0, 10, 10, 20)).toBe(false);
    expect(halfOpenRangesOverlap(0, 10, 9, 20)).toBe(true);
    expect(halfOpenRangesOverlap(20, 10, 0, 11)).toBe(true);
  });

  it("maps timeline selections onto exact destination groups", () => {
    const destination = timeGroups(meta, 100);
    expect(timeGroupRangeForMs(meta, destination, -50, 50)).toEqual([0, 1]);
    expect(timeGroupRangeForMs(meta, destination, 50, -50)).toEqual([0, 1]);
    expect(timeGroupRangeForMs(meta, destination, 75, 75)).toEqual([1, 1]);
    expect(timeGroupForMs(meta, destination, 0)).toBe(1);
    expect(timeGroupForMs(meta, destination, 100)).toBe(1);
  });

  it("keeps noisy floating-point shared edges half-open", () => {
    const noisyMeta: DatasetMeta = {
      ...meta,
      shape: [1, 1, 1, 4],
      xPositions: [0],
      yPositions: [0],
      timeBinEdges: [-0.01, 0, 0.01, 0.019999999999999997, 0.03],
      occupancyTimeSec: [[1]],
    };
    const destination = timeGroups(noisyMeta, 10);
    expect(destination).toEqual([[0, 0], [1, 1], [2, 2], [3, 3]]);
    expect(timeGroupRangeForMs(noisyMeta, destination, 0, 20)).toEqual([1, 2]);
  });

  it("normalizes raw counts by occupancy seconds", () => {
    expect(responseMatrix(counts, meta, 2, 3, "Spike count")[0]).toEqual([5, 0, 2]);
    expect(responseMatrix(counts, meta, 2, 3, "Mean firing rate (Hz)")[0]).toEqual([2.5, 0, 1]);
  });

  it("excludes unexposed source pixels and pools observations before normalization", () => {
    const pooledMeta: DatasetMeta = {
      ...meta,
      shape: [1, 1, 3, 1],
      xPositions: [0, 1, 2],
      yPositions: [0],
      timeBinEdges: [0, 1],
      occupancyTimeSec: [[1, 9, 0]],
    };
    const pooledCounts = Float64Array.from([10, 0, 999]);
    expect(responseValue(pooledCounts, pooledMeta, 0, 2, 0, 0, "Spike count")).toBeNull();
    expect(prepareResponseMatrix(pooledCounts, pooledMeta, [0, 0], "Spike count", 1, 1, false, 0).matrix).toEqual([[5]]);
    expect(prepareResponseMatrix(pooledCounts, pooledMeta, [0, 0], "Mean firing rate (Hz)", 1, 1, false, 0).matrix).toEqual([[1]]);
  });

  it("smooths pooled counts and exposures separately", () => {
    const pooledMeta: DatasetMeta = {
      ...meta,
      shape: [1, 1, 2, 1],
      xPositions: [0, 1],
      yPositions: [0],
      timeBinEdges: [0, 0.1],
      occupancyTimeSec: [[100, 1]],
    };
    const prepared = prepareResponseMatrix(
      Float64Array.from([100, 9]),
      pooledMeta,
      [0, 0],
      "Mean firing rate (Hz)",
      2,
      1,
      false,
      1,
    );
    expect(prepared.matrix[0][0]).toBeCloseTo(1.0398009950248756);
    expect(prepared.matrix[0][1]).toBeCloseTo(1.1568627450980392);
    expect(smoothMatrix([[60, null, 30]], 2)).toEqual([[60, null, 30]]);
  });

  it("selects delay from count rate and keeps entropy independent of display resolution", () => {
    const temporalMeta: DatasetMeta = {
      ...meta,
      shape: [1, 1, 1, 5],
      xPositions: [0],
      yPositions: [0],
      timeBinEdges: [0, 0.01, 0.02, 0.03, 0.04, 0.05],
      occupancyTimeSec: [[1]],
    };
    const temporalCounts = Float64Array.from([2, 2, 2, 2.5, 2.5]);
    const partialGroups = [[0, 2], [3, 4]] as const;
    const metricsByRate = groupTemporalMetrics(temporalCounts, temporalMeta, [0, 0, 0, 0], [...partialGroups]);
    const nativeMetrics = groupTemporalMetrics(temporalCounts, temporalMeta, [0, 0, 0, 0], [[0, 0], [1, 1], [2, 2], [3, 3], [4, 4]]);
    expect(metricsByRate.peakGroupIndex).toBe(1);
    expect(metricsByRate.delayMs).toBeCloseTo(40);
    expect(metricsByRate.entropy).toBeCloseTo(nativeMetrics.entropy);
  });

  it("pools temporal histograms before deriving a grouped Delay map", () => {
    const temporalMeta: DatasetMeta = {
      ...meta,
      shape: [1, 1, 2, 2],
      xPositions: [0, 1],
      yPositions: [0],
      timeBinEdges: [0, 0.01, 0.02],
      occupancyTimeSec: [[1, 1]],
    };
    const temporalCounts = Float64Array.from([10, 0, 0, 100]);
    const prepared = prepareTemporalMetricMatrices(
      temporalCounts,
      temporalMeta,
      [[0, 0], [1, 1]],
      1,
      1,
      false,
      0,
    );
    expect(prepared.delay).toEqual([[15]]);
  });

  it("reduces before applying the weighted smoothing kernel", () => {
    const matrix: Matrix = [[1, 3, 5], [7, 9, 11]];
    const prepared = prepareMatrix(matrix, meta, 2, 1, false, 0);
    expect(prepared.matrix).toEqual([[4, 7]]);
    expect(smoothMatrix([[0, 8]], 1)[0]).toEqual([16 / 6, 32 / 6]);
  });

  it("computes unit peak/delay and all-position timeline totals", () => {
    const metrics = unitMetrics(counts, meta);
    expect(metrics.totalSpikes).toBe(31);
    expect(metrics.bestY).toBe(0);
    expect(metrics.bestX).toBe(0);
    expect(metrics.bestRateHz).toBe(3);
    expect(metrics.delayMs[0][0]).toBeCloseTo(75);
    expect(allPositionsTimelineValues(counts, meta, [[0, 1], [2, 3]], "Spike count")).toEqual([15, 16]);
    expect(allPositionsTimelineValues(counts, meta, [[0, 1], [2, 3]], "Mean firing rate (Hz)"))
      .toEqual([15 / 18, 16 / 18]);
  });

  it("counts every spatial cell exactly once in the raw all-position total", () => {
    expect(allPositionsTimelineValues(counts, meta, [[0, 3]], "Spike count")).toEqual([31]);
  });

  it("remaps a selected-cell midpoint after spatial regrouping and flipping", () => {
    expect(cellFromMidpoint(meta, 2, 1, false, 1, 2)).toEqual([0, 1, 1, 2]);
    expect(cellFromMidpoint(meta, 3, 2, true, 1, 2)).toEqual([1, 1, 2, 2]);
    expect(cellFromMidpoint(meta, 3, 2, true, 99, -99)).toEqual([1, 1, 0, 0]);
  });
});
