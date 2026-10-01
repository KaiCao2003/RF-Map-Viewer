import { describe, expect, it } from "vitest";
import {
  aggregateHdCounts,
  aggregateHdRates,
  centerHdCurveOnZero,
  headDirectionUnitVector,
  normalizeHdBinCount,
  processHdUnit,
  sharedHdPeak,
  smoothCircular,
  smoothCircularMissingAware,
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

  it("rebins rate-only curves using only observed rates", () => {
    const rates: Array<number | null> = raw(2);
    rates.splice(0, 12, null, 2, null, 4, null, null, ...Array.from({ length: 6 }, () => null));
    const curve = aggregateHdRates(rates, 30);
    expect(curve.angles.slice(0, 2)).toEqual([6, 18]);
    expect(curve.rates.slice(0, 3)).toEqual([3, null, 2]);
  });

  it("processes legacy rates without fabricating counts or exposure", () => {
    const unit: HdUnitArtifact = {
      unitId: 7,
      rates: Array.from({ length: 180 }, (_unused, index) => index % 6),
      spikeCounts: null,
      hdClass: null,
    };
    const curve = processHdUnit(unit, null, { displayBins: 30, smoothing: false, sigma: 1.5 });
    expect(curve.rates).toEqual(Array.from({ length: 30 }, () => 2.5));
    expect(unit.spikeCounts).toBeNull();
  });

  it("smooths missing rate bins with observed support instead of zero Hz", () => {
    const rates: Array<number | null> = raw(4);
    rates.splice(0, 6, ...Array.from({ length: 6 }, () => null));
    const unit: HdUnitArtifact = { unitId: 7, rates, spikeCounts: null, hdClass: null };
    const unsmoothed = processHdUnit(unit, null, { displayBins: 30, smoothing: false, sigma: 1.5 });
    expect(unsmoothed.rates[0]).toBeNull();
    const smoothed = processHdUnit(unit, null, { displayBins: 30, smoothing: true, sigma: 1.5 });
    smoothed.rates.forEach((rate) => expect(rate).toBeCloseTo(4, 12));
    expect(smoothCircularMissingAware([null, null, null, null], 1)).toEqual([null, null, null, null]);
  });

  it("shares a processed scale for rate-only files", () => {
    const units: HdUnitArtifact[] = [
      { unitId: 1, rates: raw(2), spikeCounts: null, hdClass: null },
      { unitId: 2, rates: raw(7), spikeCounts: null, hdClass: null },
    ];
    expect(sharedHdPeak(units, null, { displayBins: 30, smoothing: true, sigma: 1.5 })).toBeCloseTo(7, 12);
  });

  it("smooths counts and occupancy together across an unoccupied bin", () => {
    const counts = raw(2);
    const occupancy = raw(1);
    counts[0] = 0;
    occupancy[0] = 0;
    const unit: HdUnitArtifact = { unitId: 7, rates: counts, spikeCounts: counts, hdClass: 3 };
    const curve = processHdUnit(unit, occupancy, { displayBins: 180, smoothing: true, sigma: 1.5 });
    curve.rates.forEach((rate) => expect(rate).toBeCloseTo(2, 12));
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
    expect(centered.rates).toEqual([30, 40, 10, 20]);
  });

  it("aligns clockwise HD angles with signed RF azimuths", () => {
    for (const [hdAngle, rfAzimuth] of [[270, -90], [90, 90], [0, 0]]) {
      const centered = centerHdCurveOnZero({ angles: [hdAngle], rates: [7] });
      expect(centered.angles).toEqual([rfAzimuth]);
      expect(centered.rates).toEqual([7]);
    }
  });

  it("places zero north and increases clockwise on the polar canvas", () => {
    for (const [angle, x, y] of [[0, 0, -1], [90, 1, 0], [180, 0, 1], [270, -1, 0]]) {
      const vector = headDirectionUnitVector(angle);
      expect(vector[0]).toBeCloseTo(x, 14);
      expect(vector[1]).toBeCloseTo(y, 14);
    }
  });
});
