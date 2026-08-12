import { describe, expect, it } from "vitest";
import {
  aggregateHdCounts,
  centerHdCurveOnZero,
  normalizeHdBinCount,
  processHdUnit,
  sharedHdPeak,
  smoothCircular,
  tuningSmoothingSigma,
} from "./hdMath";
import type { HdUnitArtifact } from "./types";

const raw = (value: number) => Array.from({ length: 180 }, () => value);

describe("HD tuning math", () => {
  it("uses the greatest valid 180-bin divisor", () => {
    expect(normalizeHdBinCount(31)).toBe(30);
    expect(normalizeHdBinCount(181)).toBe(180);
    expect(normalizeHdBinCount(0)).toBe(1);
  });

  it("aggregates counts and occupancy before computing firing rate", () => {
    const counts = raw(1);
    const occupancy = raw(2);
    occupancy[0] = 0;
    counts[0] = 0;
    const curve = aggregateHdCounts(counts, occupancy, 180);
    expect(curve.rates[0]).toBeNull();
    expect(curve.rates[1]).toBe(0.5);
  });

  it("processes every unit from counts and occupancy", () => {
    const unit: HdUnitArtifact = {
      unitId: 7,
      rates: raw(999),
      spikeCounts: raw(2),
      hdClass: 2,
    };
    const curve = processHdUnit(unit, raw(4), { displayBins: 30, smoothing: false, sigma: 1.5 });
    expect(curve.rates).toEqual(Array.from({ length: 30 }, () => 0.5));
  });

  it("computes a shared processed scale across units", () => {
    const units: HdUnitArtifact[] = [
      { unitId: 1, rates: raw(2), spikeCounts: raw(4), hdClass: null },
      { unitId: 2, rates: raw(7), spikeCounts: raw(14), hdClass: 1 },
    ];
    expect(sharedHdPeak(units, raw(2), { displayBins: 30, smoothing: false, sigma: 1.5 })).toBe(7);
  });

  it("keeps one fixed 18-degree smoothing width across display bins", () => {
    for (const displayBins of [6, 30, 60, 180]) {
      expect(tuningSmoothingSigma(1.5, displayBins) * 360 / displayBins).toBeCloseTo(18, 12);
    }
  });

  it("matches the Python/SciPy circular Gaussian boundary impulse golden", () => {
    const actual = smoothCircular([1, 0, 0, 0, 0, 0, 0, 0], 1);
    const expected = [
      0.39894346935609776,
      0.24197144565660073,
      0.05399112742070441,
      0.0044318616200312655,
      0.0002676612492294835,
      0.0044318616200312655,
      0.05399112742070441,
      0.24197144565660073,
    ];
    actual.forEach((value, index) => expect(value).toBeCloseTo(expected[index], 14));
    expect(actual.reduce((sum, value) => sum + value, 0)).toBeCloseTo(1, 14);
  });

  it("centers line plots on physical zero degrees", () => {
    const centered = centerHdCurveOnZero({ angles: [0, 90, 180, 270], rates: [10, 20, 30, 40] });
    expect(centered.angles).toEqual([-180, -90, 0, 90]);
    expect(centered.rates).toEqual([30, 20, 10, 40]);
  });
});
