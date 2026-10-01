import type { HdUnitArtifact } from "./types";

export const HD_RAW_BIN_COUNT = 180;
export const DEFAULT_HD_DISPLAY_BINS = 30;
export const DEFAULT_HD_SMOOTH_SIGMA = 1.5;
const GAUSSIAN_TRUNCATE = 4;

export interface ProcessedHdCurve {
  angles: number[];
  rates: Array<number | null>;
}

export interface HdProcessingOptions {
  displayBins: number;
  smoothing: boolean;
  sigma: number;
}

export function normalizeHdBinCount(value: number): number {
  const requested = Math.max(1, Math.min(HD_RAW_BIN_COUNT, Math.trunc(value)));
  for (let candidate = requested; candidate >= 1; candidate -= 1) {
    if (HD_RAW_BIN_COUNT % candidate === 0) return candidate;
  }
  return 1;
}

export function tuningSmoothingSigma(sigma: number, displayBins: number): number {
  if (!Number.isFinite(sigma) || sigma <= 0) throw new Error("HD smoothing sigma must be positive and finite.");
  return sigma * normalizeHdBinCount(displayBins) / DEFAULT_HD_DISPLAY_BINS;
}

function circularGaussianKernel(sigma: number): Array<readonly [number, number]> {
  if (!Number.isFinite(sigma) || sigma <= 0) throw new Error("HD smoothing sigma must be positive and finite.");
  const radius = Math.floor(GAUSSIAN_TRUNCATE * sigma + 0.5);
  const raw: Array<readonly [number, number]> = [];
  let total = 0;
  for (let offset = -radius; offset <= radius; offset += 1) {
    const weight = Math.exp(-0.5 * (offset / sigma) ** 2);
    raw.push([offset, weight]);
    total += weight;
  }
  return raw.map(([offset, weight]) => [offset, weight / total] as const);
}

export function smoothCircular(values: ReadonlyArray<number>, sigma: number): number[] {
  if (!values.length) return [];
  const kernel = circularGaussianKernel(sigma);
  return values.map((_value, index) => kernel.reduce((sum, [offset, weight]) => {
    const wrapped = (index + offset % values.length + values.length) % values.length;
    return sum + weight * values[wrapped];
  }, 0));
}

export function smoothCircularMissingAware(
  values: ReadonlyArray<number | null>,
  sigma: number,
): Array<number | null> {
  const finite = values.map((value) => value != null && Number.isFinite(value));
  const numerator = smoothCircular(values.map((value, index) => finite[index] ? value! : 0), sigma);
  const denominator = smoothCircular(finite.map((valid) => valid ? 1 : 0), sigma);
  return numerator.map((value, index) => denominator[index] > 1e-12 ? value / denominator[index] : null);
}

function anglesFor(displayBins: number): number[] {
  const width = 360 / displayBins;
  return Array.from({ length: displayBins }, (_unused, index) => (index + 0.5) * width);
}

function requireRawLength(values: ReadonlyArray<unknown>, label: string): void {
  if (values.length !== HD_RAW_BIN_COUNT) {
    throw new Error(`${label} must contain ${HD_RAW_BIN_COUNT} bins; got ${values.length}.`);
  }
}

function aggregateHdObservations(
  spikeCounts: ReadonlyArray<number>,
  occupancyTimeS: ReadonlyArray<number>,
  requestedDisplayBins: number,
): { angles: number[]; counts: number[]; occupancy: number[] } {
  requireRawLength(spikeCounts, "HD spike counts");
  requireRawLength(occupancyTimeS, "HD occupancy");
  const displayBins = normalizeHdBinCount(requestedDisplayBins);
  const groupSize = HD_RAW_BIN_COUNT / displayBins;
  const counts: number[] = [];
  const occupancy: number[] = [];
  for (let start = 0; start < HD_RAW_BIN_COUNT; start += groupSize) {
    counts.push(spikeCounts.slice(start, start + groupSize).reduce((sum, value) => sum + value, 0));
    occupancy.push(occupancyTimeS.slice(start, start + groupSize).reduce((sum, value) => sum + value, 0));
  }
  return { angles: anglesFor(displayBins), counts, occupancy };
}

export function aggregateHdCounts(
  spikeCounts: ReadonlyArray<number>,
  occupancyTimeS: ReadonlyArray<number>,
  displayBins: number,
): ProcessedHdCurve {
  const grouped = aggregateHdObservations(spikeCounts, occupancyTimeS, displayBins);
  return {
    angles: grouped.angles,
    rates: grouped.counts.map((count, index) => grouped.occupancy[index] > 1e-12
      ? count / grouped.occupancy[index]
      : null),
  };
}

export function smoothHdCounts(
  spikeCounts: ReadonlyArray<number>,
  occupancyTimeS: ReadonlyArray<number>,
  displayBins: number,
  sigma: number,
): ProcessedHdCurve {
  requireRawLength(spikeCounts, "HD spike counts");
  requireRawLength(occupancyTimeS, "HD occupancy");
  const rawSigma = tuningSmoothingSigma(sigma, HD_RAW_BIN_COUNT);
  const grouped = aggregateHdObservations(
    smoothCircular(spikeCounts, rawSigma),
    smoothCircular(occupancyTimeS, rawSigma),
    displayBins,
  );
  return {
    angles: grouped.angles,
    rates: grouped.counts.map((count, index) => grouped.occupancy[index] > 1e-12
      ? count / grouped.occupancy[index]
      : null),
  };
}

export function aggregateHdRates(
  rates: ReadonlyArray<number | null>,
  requestedDisplayBins: number,
): ProcessedHdCurve {
  requireRawLength(rates, "HD rates");
  const displayBins = normalizeHdBinCount(requestedDisplayBins);
  const groupSize = HD_RAW_BIN_COUNT / displayBins;
  const groupedRates: Array<number | null> = [];
  for (let start = 0; start < HD_RAW_BIN_COUNT; start += groupSize) {
    const group = rates.slice(start, start + groupSize).filter((rate): rate is number => rate != null && Number.isFinite(rate));
    groupedRates.push(group.length ? group.reduce((sum, rate) => sum + rate, 0) / group.length : null);
  }
  return { angles: anglesFor(displayBins), rates: groupedRates };
}

export function processHdUnit(
  unit: HdUnitArtifact,
  occupancyTimeS: ReadonlyArray<number> | null,
  options: HdProcessingOptions,
): ProcessedHdCurve {
  const displayBins = normalizeHdBinCount(options.displayBins);
  if (unit.spikeCounts == null || occupancyTimeS == null || !occupancyTimeS.some((value) => value > 0)) {
    // Legacy files have rates only: smooth and average those rates without inventing exposure.
    requireRawLength(unit.rates, "HD rates");
    const rates = options.smoothing
      ? smoothCircularMissingAware(unit.rates, tuningSmoothingSigma(options.sigma, HD_RAW_BIN_COUNT))
      : unit.rates;
    return aggregateHdRates(rates, displayBins);
  }
  requireRawLength(unit.spikeCounts, "HD spike counts");
  requireRawLength(occupancyTimeS, "HD occupancy");
  return options.smoothing
    ? smoothHdCounts(unit.spikeCounts, occupancyTimeS, displayBins, options.sigma)
    : aggregateHdCounts(unit.spikeCounts, occupancyTimeS, displayBins);
}

export function hdRatePeak(rates: ReadonlyArray<number | null>): number {
  return rates.reduce<number>((high, value) => value == null || !Number.isFinite(value)
    ? high
    : Math.max(high, value), 0);
}

export function sharedHdPeak(
  units: ReadonlyArray<HdUnitArtifact>,
  occupancyTimeS: ReadonlyArray<number> | null,
  options: HdProcessingOptions,
): number {
  return Math.max(0, ...units.map((unit) => hdRatePeak(processHdUnit(unit, occupancyTimeS, options).rates)));
}

export function centerHdCurveOnZero(curve: ProcessedHdCurve): ProcessedHdCurve {
  const pairs = curve.angles.map((angle, index) => ({
    angle: ((angle + 180) % 360 + 360) % 360 - 180,
    rate: curve.rates[index],
  })).sort((left, right) => left.angle - right.angle);
  return { angles: pairs.map((pair) => pair.angle), rates: pairs.map((pair) => pair.rate) };
}

export function headDirectionUnitVector(angleDeg: number): readonly [number, number] {
  const radians = angleDeg * Math.PI / 180;
  return [Math.sin(radians), -Math.cos(radians)];
}
