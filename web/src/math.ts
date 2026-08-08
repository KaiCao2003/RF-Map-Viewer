import type {
  AxisGroup,
  CellRef,
  DatasetMeta,
  Matrix,
  UnitMetrics,
  ValueMode,
} from "./types";

export function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(high, value));
}

export function halfOpenRangesOverlap(
  firstStart: number,
  firstEnd: number,
  secondStart: number,
  secondEnd: number,
): boolean {
  const firstLow = Math.min(firstStart, firstEnd);
  const firstHigh = Math.max(firstStart, firstEnd);
  const secondLow = Math.min(secondStart, secondEnd);
  const secondHigh = Math.max(secondStart, secondEnd);
  return firstLow < secondHigh && firstHigh > secondLow;
}

export function formatNumber(value: number, precision = 3): string {
  if (Math.abs(value - Math.round(value)) < 1e-9) return String(Math.round(value));
  return value.toFixed(precision).replace(/0+$/, "").replace(/\.$/, "");
}

export function axisGroupsForTarget(sourceCount: number, targetCount: number): AxisGroup[] {
  const target = clamp(Math.trunc(targetCount), 1, sourceCount);
  return Array.from({ length: target }, (_, groupIndex) => {
    const start = Math.floor((groupIndex * sourceCount) / target);
    const end = Math.floor(((groupIndex + 1) * sourceCount) / target) - 1;
    return [start, Math.max(start, end)] as const;
  });
}

export function groupIndexForSource(groups: AxisGroup[], sourceIndex: number): number {
  const found = groups.findIndex(([start, end]) => start <= sourceIndex && sourceIndex <= end);
  if (found >= 0) return found;
  return sourceIndex < (groups[0]?.[0] ?? 0) ? 0 : Math.max(0, groups.length - 1);
}

export function reduceMatrixXY(matrix: Matrix, yGroups: AxisGroup[], xGroups: AxisGroup[]): Matrix {
  return yGroups.map(([yStart, yEnd]) =>
    xGroups.map(([xStart, xEnd]) => {
      let total = 0;
      let count = 0;
      for (let y = yStart; y <= yEnd; y += 1) {
        for (let x = xStart; x <= xEnd; x += 1) {
          const value = matrix[y]?.[x];
          if (value != null && Number.isFinite(value)) {
            total += value;
            count += 1;
          }
        }
      }
      return count ? total / count : null;
    }),
  );
}

export function smoothMatrix(matrix: Matrix, requestedRadius: number): Matrix {
  const radius = Math.max(0, Math.trunc(requestedRadius));
  let current = matrix.map((row) => [...row]);
  for (let pass = 0; pass < radius; pass += 1) {
    current = current.map((row, y) =>
      row.map((center, x) => {
        if (center == null || !Number.isFinite(center)) return null;
        let total = 0;
        let weightTotal = 0;
        for (let dy = -1; dy <= 1; dy += 1) {
          for (let dx = -1; dx <= 1; dx += 1) {
            const value = current[y + dy]?.[x + dx];
            if (value == null || !Number.isFinite(value)) continue;
            const weight = dx === 0 && dy === 0 ? 4 : dx === 0 || dy === 0 ? 2 : 1;
            total += value * weight;
            weightTotal += weight;
          }
        }
        return weightTotal ? total / weightTotal : null;
      }),
    );
  }
  return current;
}

export function finiteMinMax(matrix: Matrix): readonly [number, number] {
  const values = matrix.flat().filter((value): value is number => value != null && Number.isFinite(value));
  if (!values.length) return [0, 1];
  const low = Math.min(...values);
  const rawHigh = Math.max(...values);
  return [low, Math.abs(rawHigh - low) < 1e-12 ? low + 1 : rawHigh];
}

export function baseBinMs(meta: DatasetMeta): number {
  const positive = meta.timeBinEdges
    .slice(1)
    .map((edge, index) => (edge - meta.timeBinEdges[index]) * 1000)
    .filter((difference) => difference > 1e-9);
  return positive.length ? Math.min(...positive) : 1;
}

function roundHalfToEven(value: number): number {
  const lower = Math.floor(value);
  const fraction = value - lower;
  if (fraction === 0.5) {
    return lower % 2 === 0 ? lower : lower + 1;
  }
  return Math.round(value);
}

export function timeGroups(meta: DatasetMeta, requestedResolutionMs: number): AxisGroup[] {
  const base = baseBinMs(meta);
  const total = Math.max(
    (meta.timeBinEdges.at(-1)! - meta.timeBinEdges[0]) * 1000,
    base,
  );
  const requested = clamp(requestedResolutionMs, base, total);
  const groupSize = clamp(roundHalfToEven(requested / base), 1, meta.shape[3]);
  const targetDurationMs = groupSize * base;
  const edgesMs = meta.timeBinEdges.map((edge) => edge * 1000);
  const groups: AxisGroup[] = [];
  let start = 0;
  while (start < meta.shape[3]) {
    const targetEdge = edgesMs[start] + targetDurationMs;
    let upper = start + 1;
    while (upper < meta.shape[3] && edgesMs[upper] < targetEdge) upper += 1;
    const lower = Math.max(start + 1, upper - 1);
    const endExclusive = Math.abs(edgesMs[lower] - targetEdge) <= Math.abs(edgesMs[upper] - targetEdge)
      ? lower
      : upper;
    groups.push([start, endExclusive - 1]);
    start = endExclusive;
  }
  return groups;
}

export function snappedResolutionMs(meta: DatasetMeta, requestedResolutionMs: number): number {
  const base = baseBinMs(meta);
  const total = Math.max(
    (meta.timeBinEdges.at(-1)! - meta.timeBinEdges[0]) * 1000,
    base,
  );
  const requested = clamp(requestedResolutionMs, base, total);
  const groupSize = clamp(roundHalfToEven(requested / base), 1, meta.shape[3]);
  return groupSize * base;
}

export function snapTimeRange(meta: DatasetMeta, requestedStart: number, requestedEnd: number): AxisGroup {
  const edges = meta.timeBinEdges.map((value) => value * 1000);
  const axisStart = edges[0];
  const axisEnd = edges.at(-1)!;
  let lower = clamp(requestedStart, axisStart, axisEnd);
  let upper = clamp(requestedEnd, axisStart, axisEnd);
  if (lower > upper) [lower, upper] = [upper, lower];
  let startEdge = 0;
  for (let index = 1; index < meta.shape[3]; index += 1) {
    if (Math.abs(edges[index] - lower) < Math.abs(edges[startEdge] - lower)) startEdge = index;
  }
  let endEdge = 1;
  for (let index = 2; index <= meta.shape[3]; index += 1) {
    if (Math.abs(edges[index] - upper) < Math.abs(edges[endEdge] - upper)) endEdge = index;
  }
  if (endEdge <= startEdge) {
    if (lower >= axisEnd) [startEdge, endEdge] = [meta.shape[3] - 1, meta.shape[3]];
    else if (upper <= axisStart) [startEdge, endEdge] = [0, 1];
    else endEdge = Math.min(meta.shape[3], startEdge + 1);
  }
  return [startEdge, endEdge - 1];
}

export function timeBounds(meta: DatasetMeta, group: AxisGroup): readonly [number, number] {
  return [meta.timeBinEdges[group[0]] * 1000, meta.timeBinEdges[group[1] + 1] * 1000];
}

export function timeGroupForMs(meta: DatasetMeta, groups: AxisGroup[], milliseconds: number): number {
  if (!Number.isFinite(milliseconds)) return 0;
  for (let index = 0; index < groups.length; index += 1) {
    const [start, end] = timeBounds(meta, groups[index]);
    if (start <= milliseconds && (milliseconds < end || (index === groups.length - 1 && milliseconds <= end))) {
      return index;
    }
  }
  let best = 0;
  let distance = Number.POSITIVE_INFINITY;
  groups.forEach((group, index) => {
    const [start, end] = timeBounds(meta, group);
    const current = Math.abs((start + end) / 2 - milliseconds);
    if (current < distance) {
      best = index;
      distance = current;
    }
  });
  return best;
}

export function timeGroupRangeForMs(
  meta: DatasetMeta,
  groups: AxisGroup[],
  requestedStartMs: number,
  requestedEndMs: number,
): AxisGroup {
  const boundaryToleranceMs = 1e-9;
  let startMs = requestedStartMs;
  let endMs = requestedEndMs;
  if (startMs > endMs) [startMs, endMs] = [endMs, startMs];
  if (Math.abs(startMs - endMs) < boundaryToleranceMs) {
    const index = timeGroupForMs(meta, groups, startMs);
    return [index, index];
  }
  const overlapping: number[] = [];
  groups.forEach((group, index) => {
    const [groupStart, groupEnd] = timeBounds(meta, group);
    if (
      groupEnd > startMs + boundaryToleranceMs
      && groupStart < endMs - boundaryToleranceMs
    ) overlapping.push(index);
  });
  if (overlapping.length) return [overlapping[0], overlapping.at(-1)!];
  return [timeGroupForMs(meta, groups, startMs), timeGroupForMs(meta, groups, endMs)];
}

export function countIndex(meta: DatasetMeta, y: number, x: number, bin: number): number {
  return (y * meta.shape[2] + x) * meta.shape[3] + bin;
}

export function countAt(counts: Float64Array, meta: DatasetMeta, y: number, x: number, bin: number): number {
  return counts[countIndex(meta, y, x, bin)] ?? 0;
}

export function countInRange(
  counts: Float64Array,
  meta: DatasetMeta,
  y: number,
  x: number,
  start: number,
  end: number,
): number {
  let total = 0;
  for (let bin = Math.max(0, start); bin <= Math.min(meta.shape[3] - 1, end); bin += 1) {
    total += countAt(counts, meta, y, x, bin);
  }
  return total;
}

export function responseValue(
  counts: Float64Array,
  meta: DatasetMeta,
  y: number,
  x: number,
  start: number,
  end: number,
  valueMode: ValueMode,
): number | null {
  const lower = Math.min(start, end);
  const upper = Math.max(start, end);
  const count = countInRange(counts, meta, y, x, lower, upper);
  const presentations = meta.presentationCounts?.[y]?.[x] ?? null;
  if (presentations != null && presentations <= 0) return null;
  if (valueMode === "Spike count") return count;
  if (presentations == null || presentations <= 0) return null;
  if (valueMode === "Spikes / presentation") return count / presentations;
  const duration = meta.timeBinEdges[upper + 1] - meta.timeBinEdges[lower];
  return duration > 0 ? count / (presentations * duration) : null;
}

export function responseMatrix(
  counts: Float64Array,
  meta: DatasetMeta,
  start: number,
  end: number,
  valueMode: ValueMode,
): Matrix {
  return Array.from({ length: meta.shape[1] }, (_, y) =>
    Array.from({ length: meta.shape[2] }, (_, x) =>
      responseValue(counts, meta, y, x, start, end, valueMode),
    ),
  );
}

export function prepareMatrix(
  matrix: Matrix,
  meta: DatasetMeta,
  xTarget: number,
  yTarget: number,
  flipY: boolean,
  smoothRadius: number,
): { matrix: Matrix; xGroups: AxisGroup[]; yGroups: AxisGroup[] } {
  const xGroups = axisGroupsForTarget(meta.shape[2], xTarget);
  const naturalYGroups = axisGroupsForTarget(meta.shape[1], yTarget);
  const yGroups = flipY ? [...naturalYGroups].reverse() : naturalYGroups;
  return {
    matrix: smoothMatrix(reduceMatrixXY(matrix, yGroups, xGroups), smoothRadius),
    xGroups,
    yGroups,
  };
}

export interface SpatialGroupObservations {
  count: number;
  presentations: number | null;
  sourcePixelCount: number;
}

export interface PreparedSpatialResponse {
  matrix: Matrix;
  xGroups: AxisGroup[];
  yGroups: AxisGroup[];
}

export interface GroupTemporalMetrics {
  meanTotalCount: number;
  peakGroupIndex: number | null;
  delayMs: number | null;
  entropy: number;
}

export interface PreparedTemporalMetrics {
  delay: Matrix;
  entropy: Matrix;
  xGroups: AxisGroup[];
  yGroups: AxisGroup[];
}

export function spatialGroupObservations(
  counts: Float64Array,
  meta: DatasetMeta,
  yGroup: AxisGroup,
  xGroup: AxisGroup,
  start: number,
  end: number,
): SpatialGroupObservations {
  const yStart = clamp(Math.min(...yGroup), 0, meta.shape[1] - 1);
  const yEnd = clamp(Math.max(...yGroup), 0, meta.shape[1] - 1);
  const xStart = clamp(Math.min(...xGroup), 0, meta.shape[2] - 1);
  const xEnd = clamp(Math.max(...xGroup), 0, meta.shape[2] - 1);
  const rangeStart = clamp(Math.min(start, end), 0, meta.shape[3] - 1);
  const rangeEnd = clamp(Math.max(start, end), 0, meta.shape[3] - 1);
  let count = 0;
  let presentations = 0;
  let sourcePixelCount = 0;
  for (let y = yStart; y <= yEnd; y += 1) {
    for (let x = xStart; x <= xEnd; x += 1) {
      const exposure = meta.presentationCounts?.[y]?.[x] ?? null;
      if (exposure != null && exposure <= 0) continue;
      count += countInRange(counts, meta, y, x, rangeStart, rangeEnd);
      if (exposure != null) presentations += exposure;
      sourcePixelCount += 1;
    }
  }
  return {
    count,
    presentations: meta.presentationCounts == null ? null : presentations,
    sourcePixelCount,
  };
}

export function prepareResponseMatrix(
  counts: Float64Array,
  meta: DatasetMeta,
  sourceRange: AxisGroup,
  valueMode: ValueMode,
  xTarget: number,
  yTarget: number,
  flipY: boolean,
  smoothRadius: number,
): PreparedSpatialResponse {
  const xGroups = axisGroupsForTarget(meta.shape[2], xTarget);
  const naturalYGroups = axisGroupsForTarget(meta.shape[1], yTarget);
  const yGroups = flipY ? [...naturalYGroups].reverse() : naturalYGroups;
  const observations = yGroups.map((yGroup) => xGroups.map((xGroup) =>
    spatialGroupObservations(
      counts,
      meta,
      yGroup,
      xGroup,
      sourceRange[0],
      sourceRange[1],
    ),
  ));
  const valid = observations.map((row) => row.map((value) => value.sourcePixelCount > 0));

  if (valueMode === "Spike count") {
    let matrix: Matrix = observations.map((row) => row.map((value) =>
      value.sourcePixelCount > 0 ? value.count / value.sourcePixelCount : null,
    ));
    if (smoothRadius > 0) {
      matrix = smoothMatrix(matrix, smoothRadius).map((row, y) =>
        row.map((value, x) => valid[y][x] ? value : null),
      );
    }
    return { matrix, xGroups, yGroups };
  }

  let pooledCounts: Matrix = observations.map((row) => row.map((value) =>
    value.sourcePixelCount > 0 ? value.count : null,
  ));
  let pooledPresentations: Matrix = observations.map((row) => row.map((value) =>
    value.sourcePixelCount > 0 ? (value.presentations ?? 0) : null,
  ));
  if (smoothRadius > 0) {
    pooledCounts = smoothMatrix(pooledCounts, smoothRadius);
    pooledPresentations = smoothMatrix(pooledPresentations, smoothRadius);
  }
  const rangeStart = clamp(Math.min(...sourceRange), 0, meta.shape[3] - 1);
  const rangeEnd = clamp(Math.max(...sourceRange), 0, meta.shape[3] - 1);
  const duration = meta.timeBinEdges[rangeEnd + 1] - meta.timeBinEdges[rangeStart];
  const matrix: Matrix = pooledCounts.map((row, y) => row.map((count, x) => {
    const exposure = pooledPresentations[y][x];
    if (!valid[y][x] || count == null || exposure == null || exposure <= 0) return null;
    const normalized = count / exposure;
    return valueMode === "Mean firing rate (Hz)"
      ? (duration > 0 ? normalized / duration : null)
      : normalized;
  }));
  return { matrix, xGroups, yGroups };
}

export function unitMetrics(counts: Float64Array, meta: DatasetMeta): UnitMetrics {
  const total: Matrix = [];
  const peak: Matrix = [];
  const delayMs: Matrix = [];
  const entropy: Matrix = [];
  const binTotals = Array.from({ length: meta.shape[3] }, () => 0);
  let totalSpikes = 0;
  let bestY = 0;
  let bestX = 0;
  let bestTotal = -1;
  for (let y = 0; y < meta.shape[1]; y += 1) {
    const totalRow: Array<number | null> = [];
    const peakRow: Array<number | null> = [];
    const delayRow: Array<number | null> = [];
    const entropyRow: Array<number | null> = [];
    for (let x = 0; x < meta.shape[2]; x += 1) {
      let cellTotal = 0;
      let cellPeak = 0;
      let peakBin = 0;
      for (let bin = 0; bin < meta.shape[3]; bin += 1) {
        const value = countAt(counts, meta, y, x, bin);
        cellTotal += value;
        binTotals[bin] += value;
        if (value > cellPeak) {
          cellPeak = value;
          peakBin = bin;
        }
      }
      let normalizedEntropy = 0;
      if (cellTotal > 0) {
        for (let bin = 0; bin < meta.shape[3]; bin += 1) {
          const value = countAt(counts, meta, y, x, bin);
          if (value > 0) {
            const probability = value / cellTotal;
            normalizedEntropy -= probability * Math.log(probability);
          }
        }
        if (meta.shape[3] > 1) normalizedEntropy /= Math.log(meta.shape[3]);
      }
      totalRow.push(cellTotal);
      peakRow.push(cellPeak);
      delayRow.push(
        cellTotal > 0
          ? (meta.timeBinEdges[peakBin] + meta.timeBinEdges[peakBin + 1]) * 500
          : null,
      );
      entropyRow.push(normalizedEntropy);
      totalSpikes += cellTotal;
      if (cellTotal > bestTotal) {
        bestTotal = cellTotal;
        bestY = y;
        bestX = x;
      }
    }
    total.push(totalRow);
    peak.push(peakRow);
    delayMs.push(delayRow);
    entropy.push(entropyRow);
  }
  return { total, peak, delayMs, entropy, binTotals, totalSpikes, bestY, bestX };
}

export function delayMatrixForGroups(
  counts: Float64Array,
  meta: DatasetMeta,
  groups: AxisGroup[],
): Matrix {
  return Array.from({ length: meta.shape[1] }, (_, y) =>
    Array.from({ length: meta.shape[2] }, (_, x) =>
      groupTemporalMetrics(counts, meta, [y, y, x, x], groups).delayMs,
    ),
  );
}

export function spatialGroupCountHistogram(
  counts: Float64Array,
  meta: DatasetMeta,
  yGroup: AxisGroup,
  xGroup: AxisGroup,
): number[] {
  const yStart = clamp(Math.min(...yGroup), 0, meta.shape[1] - 1);
  const yEnd = clamp(Math.max(...yGroup), 0, meta.shape[1] - 1);
  const xStart = clamp(Math.min(...xGroup), 0, meta.shape[2] - 1);
  const xEnd = clamp(Math.max(...xGroup), 0, meta.shape[2] - 1);
  return Array.from({ length: meta.shape[3] }, (_, bin) => {
    let total = 0;
    for (let y = yStart; y <= yEnd; y += 1) {
      for (let x = xStart; x <= xEnd; x += 1) {
        const exposure = meta.presentationCounts?.[y]?.[x] ?? null;
        if (exposure != null && exposure <= 0) continue;
        total += countAt(counts, meta, y, x, bin);
      }
    }
    return total;
  });
}

export function spatialGroupSourcePixelCount(
  meta: DatasetMeta,
  yGroup: AxisGroup,
  xGroup: AxisGroup,
): number {
  const yStart = clamp(Math.min(...yGroup), 0, meta.shape[1] - 1);
  const yEnd = clamp(Math.max(...yGroup), 0, meta.shape[1] - 1);
  const xStart = clamp(Math.min(...xGroup), 0, meta.shape[2] - 1);
  const xEnd = clamp(Math.max(...xGroup), 0, meta.shape[2] - 1);
  let sourcePixelCount = 0;
  for (let y = yStart; y <= yEnd; y += 1) {
    for (let x = xStart; x <= xEnd; x += 1) {
      const exposure = meta.presentationCounts?.[y]?.[x] ?? null;
      if (exposure == null || exposure > 0) sourcePixelCount += 1;
    }
  }
  return sourcePixelCount;
}

export function temporalMetricsFromHistogram(
  histogram: readonly number[],
  meta: DatasetMeta,
  groups: AxisGroup[],
  sourcePixelCount = 1,
): GroupTemporalMetrics {
  const hist = Array.from({ length: meta.shape[3] }, (_, index) => Number(histogram[index] ?? 0));
  const total = hist.reduce((sum, value) => sum + value, 0);
  let peakGroupIndex: number | null = null;
  let peakRate = Number.NEGATIVE_INFINITY;
  if (total > 0) {
    groups.forEach(([rawStart, rawEnd], index) => {
      const start = clamp(Math.min(rawStart, rawEnd), 0, meta.shape[3] - 1);
      const end = clamp(Math.max(rawStart, rawEnd), 0, meta.shape[3] - 1);
      let groupCount = 0;
      for (let bin = start; bin <= end; bin += 1) groupCount += hist[bin];
      const duration = meta.timeBinEdges[end + 1] - meta.timeBinEdges[start];
      const rate = duration > 0 ? groupCount / duration : 0;
      if (rate > peakRate) {
        peakRate = rate;
        peakGroupIndex = index;
      }
    });
  }
  let entropy = 0;
  if (total > 0) {
    hist.forEach((count) => {
      if (count <= 0) return;
      const probability = count / total;
      entropy -= probability * Math.log(probability);
    });
    if (meta.shape[3] > 1) entropy /= Math.log(meta.shape[3]);
  }
  const delayMs = peakGroupIndex == null
    ? null
    : timeBounds(meta, groups[peakGroupIndex]).reduce((sum, value) => sum + value, 0) / 2;
  return {
    meanTotalCount: total / Math.max(1, Math.trunc(sourcePixelCount)),
    peakGroupIndex,
    delayMs,
    entropy,
  };
}

export function groupTemporalMetrics(
  counts: Float64Array,
  meta: DatasetMeta,
  cell: CellRef,
  groups: AxisGroup[],
): GroupTemporalMetrics {
  const yGroup: AxisGroup = [cell[0], cell[1]];
  const xGroup: AxisGroup = [cell[2], cell[3]];
  return temporalMetricsFromHistogram(
    spatialGroupCountHistogram(counts, meta, yGroup, xGroup),
    meta,
    groups,
    spatialGroupSourcePixelCount(meta, yGroup, xGroup),
  );
}

export function prepareTemporalMetricMatrices(
  counts: Float64Array,
  meta: DatasetMeta,
  groups: AxisGroup[],
  xTarget: number,
  yTarget: number,
  flipY: boolean,
  smoothRadius: number,
  floor = 0,
): PreparedTemporalMetrics {
  const xGroups = axisGroupsForTarget(meta.shape[2], xTarget);
  const naturalYGroups = axisGroupsForTarget(meta.shape[1], yTarget);
  const yGroups = flipY ? [...naturalYGroups].reverse() : naturalYGroups;
  let histograms = yGroups.map((yGroup) => xGroups.map((xGroup) => {
    const divisor = Math.max(1, spatialGroupSourcePixelCount(meta, yGroup, xGroup));
    return spatialGroupCountHistogram(counts, meta, yGroup, xGroup).map((value) => value / divisor);
  }));
  if (smoothRadius > 0 && histograms.length && histograms[0].length) {
    const output = histograms.map((row) => row.map(() => Array.from({ length: meta.shape[3] }, () => 0)));
    for (let bin = 0; bin < meta.shape[3]; bin += 1) {
      const temporalSlice: Matrix = histograms.map((row) => row.map((histogram) => histogram[bin]));
      const smoothed = smoothMatrix(temporalSlice, smoothRadius);
      smoothed.forEach((row, y) => row.forEach((value, x) => {
        output[y][x][bin] = value ?? 0;
      }));
    }
    histograms = output;
  }
  const safeFloor = Math.max(0, floor);
  const delay: Matrix = [];
  const entropy: Matrix = [];
  histograms.forEach((row) => {
    const delayRow: Array<number | null> = [];
    const entropyRow: Array<number | null> = [];
    row.forEach((histogram) => {
      const metrics = temporalMetricsFromHistogram(histogram, meta, groups);
      delayRow.push(metrics.meanTotalCount > safeFloor ? metrics.delayMs : null);
      entropyRow.push(metrics.entropy);
    });
    delay.push(delayRow);
    entropy.push(entropyRow);
  });
  return { delay, entropy, xGroups, yGroups };
}

export function groupResponseValue(
  counts: Float64Array,
  meta: DatasetMeta,
  cell: CellRef,
  sourceRange: AxisGroup,
  valueMode: ValueMode,
): number | null {
  const observations = spatialGroupObservations(
    counts,
    meta,
    [cell[0], cell[1]],
    [cell[2], cell[3]],
    sourceRange[0],
    sourceRange[1],
  );
  if (valueMode === "Spike count") {
    return observations.sourcePixelCount > 0
      ? observations.count / observations.sourcePixelCount
      : null;
  }
  if (observations.presentations == null || observations.presentations <= 0) return null;
  const normalized = observations.count / observations.presentations;
  if (valueMode === "Spikes / presentation") return normalized;
  const lower = clamp(Math.min(...sourceRange), 0, meta.shape[3] - 1);
  const upper = clamp(Math.max(...sourceRange), 0, meta.shape[3] - 1);
  const duration = meta.timeBinEdges[upper + 1] - meta.timeBinEdges[lower];
  return duration > 0 ? normalized / duration : null;
}

export function groupResponseValues(
  counts: Float64Array,
  meta: DatasetMeta,
  cell: CellRef,
  groups: AxisGroup[],
  valueMode: ValueMode,
): Array<number | null> {
  return groups.map((group) => groupResponseValue(counts, meta, cell, group, valueMode));
}

export function allPositionsTimelineValues(
  counts: Float64Array,
  meta: DatasetMeta,
  groups: AxisGroup[],
  valueMode: ValueMode,
): number[] {
  if (valueMode === "Spike count") {
    return groups.map(([start, end]) => {
      let total = 0;
      for (let y = 0; y < meta.shape[1]; y += 1) {
        for (let x = 0; x < meta.shape[2]; x += 1) total += countInRange(counts, meta, y, x, start, end);
      }
      return total;
    });
  }
  const presentations = (meta.presentationCounts ?? []).flat().reduce(
    (sum, value) => sum + (value > 0 ? value : 0),
    0,
  );
  if (presentations <= 0) return groups.map(() => 0);
  return groups.map(([start, end]) => {
    let count = 0;
    for (let y = 0; y < meta.shape[1]; y += 1) {
      for (let x = 0; x < meta.shape[2]; x += 1) count += countInRange(counts, meta, y, x, start, end);
    }
    const normalized = count / presentations;
    if (valueMode === "Mean firing rate (Hz)") {
      const duration = meta.timeBinEdges[end + 1] - meta.timeBinEdges[start];
      return duration > 0 ? normalized / duration : 0;
    }
    return normalized;
  });
}

export function inferTotalDegrees(xPositions: number[]): number {
  if (xPositions.length <= 1) return 360;
  const differences = xPositions.slice(1).map((value, index) => value - xPositions[index]);
  const step = differences.reduce((sum, value) => sum + value, 0) / differences.length;
  if (Math.abs(step) > 1e-9 && differences.every((difference) => Math.abs(difference - step) < 1e-6)) {
    return Math.abs(step) * xPositions.length;
  }
  return Math.abs(xPositions.at(-1)! - xPositions[0]);
}

export function cellFromMidpoint(
  meta: DatasetMeta,
  xTarget: number,
  yTarget: number,
  flipY: boolean,
  yMidpoint: number | null,
  xMidpoint: number | null,
): CellRef | null {
  if (yMidpoint == null || xMidpoint == null) return null;
  const xGroups = axisGroupsForTarget(meta.shape[2], xTarget);
  let yGroups = axisGroupsForTarget(meta.shape[1], yTarget);
  if (flipY) yGroups = [...yGroups].reverse();
  const sourceX = Math.floor(clamp(xMidpoint, 0, meta.shape[2] - 1) + 0.5);
  const sourceY = Math.floor(clamp(yMidpoint, 0, meta.shape[1] - 1) + 0.5);
  const x = groupIndexForSource(xGroups, sourceX);
  const y = groupIndexForSource(yGroups, sourceY);
  return [yGroups[y][0], yGroups[y][1], xGroups[x][0], xGroups[x][1]];
}

export function valueModeUnit(mode: ValueMode): string {
  if (mode === "Spike count") return "spikes";
  if (mode === "Spikes / presentation") return "spikes/presentation";
  return "Hz";
}

export function formatResponse(value: number | null, mode: ValueMode): string {
  if (value == null) return "n/a";
  return mode === "Spike count" ? value.toFixed(0) : value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}
