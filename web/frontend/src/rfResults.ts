import type { DatasetMeta, RfOverlayMode, RfOverlayLayer, RfResultPolarity, RfResultDimension, SavedRfResult } from "./types";

export function resultDimensions(mode: RfOverlayMode): RfResultDimension[] {
  return mode === "Both" ? ["2d", "1d"] : mode === "None" ? [] : [mode === "2D" ? "2d" : "1d"];
}

export function resultLayers(mode: RfOverlayMode, polarity: RfResultPolarity): RfOverlayLayer[] {
  return resultDimensions(mode).map((dimension): RfOverlayLayer => `${polarity}_${dimension}`);
}

export function layerResult(layer: RfOverlayLayer): { dimension: RfResultDimension; polarity: RfResultPolarity } {
  const [polarity, dimension] = layer.split("_") as [RfResultPolarity, RfResultDimension];
  return { dimension, polarity };
}

export interface SavedRfOverlay {
  flags: number[][] | null;
  centers2d: Array<readonly [number, number]>;
  centers1d: Array<{ axis: "x" | "y"; index: number }>;
  unavailable: string[];
}

export function savedRfOverlay(meta: DatasetMeta, results: SavedRfResult[], unitId: number): SavedRfOverlay {
  const overlay: SavedRfOverlay = { flags: null, centers2d: [], centers1d: [], unavailable: [] };
  for (const result of results) {
    const index = result.available ? result.unitIds.indexOf(unitId) : -1;
    if (!result.available || index < 0) {
      overlay.unavailable.push(`${result.polarity} ${result.dimension.toUpperCase()}`);
      continue;
    }
    overlay.flags ??= Array.from({ length: meta.shape[1] }, () => Array(meta.shape[2]).fill(0));
    const mask = result.masks[index], center = result.centers[index];
    const axis = result.axis;
    for (let y = 0; y < meta.shape[1]; y += 1) {
      for (let x = 0; x < meta.shape[2]; x += 1) {
        // A saved 1D result is the OR projection of its stored detection,
        // broadcast over the collapsed axis only for display.
        const detected = result.dimension === "2d" ? Boolean(mask[y][x])
          : axis === "x" ? mask.some((row) => Boolean(row[x])) : mask[y].some(Boolean);
        if (detected) overlay.flags[y][x] |= (result.dimension === "2d" ? 1 : 2) << (result.polarity === "inhibitory" ? 2 : 0);
        if (result.dimension === "2d" && center[y][x]) overlay.centers2d.push([y, x]);
      }
    }
    if (result.dimension === "1d" && axis) {
      for (let position = 0; position < (axis === "x" ? meta.shape[2] : meta.shape[1]); position += 1) {
        if (axis === "x" ? center.some((row) => Boolean(row[position])) : center[position].some(Boolean)) {
          overlay.centers1d.push({ axis, index: position });
        }
      }
    }
  }
  return overlay;
}

export function savedResultDetails(result: SavedRfResult, unitId: number): string {
  const heading = `${result.dimension.toUpperCase()} ${result.polarity}\n${result.resultPath}`;
  if (!result.available) return `${heading}\n${result.detail}`;
  const index = result.unitIds.indexOf(unitId);
  const summary = result.summary;
  const qc = summary?.bin_qc as { unit_ids: number[]; zero_bins: number[]; valid_bins: number[]; keep: boolean[] } | undefined;
  const qcIndex = qc?.unit_ids.indexOf(unitId) ?? -1;
  const interval = result.manifest.time_range_s as number[] | undefined;
  const lines = [heading, `Source: ${result.sourcePath}`, `Unit ${unitId}: ${index < 0 ? "not in saved results" : "saved detection available"}`];
  if (interval) lines.push(`Detection window: ${interval.map((value) => value * 1000).join("–")} ms`);
  if (qc && qcIndex >= 0) lines.push(`QC: ${qc.zero_bins[qcIndex]} zero bins; ${qc.valid_bins[qcIndex]} valid bins; ${qc.keep[qcIndex] ? "kept" : "excluded"}`);
  if (result.projectedFrom2d) lines.push(`1D projection along ${result.axis}; stored 2D detection.`);
  if (result.summaryWarning) lines.push(result.summaryWarning);
  lines.push(`Detection parameters and provenance:\n${JSON.stringify(result.manifest, null, 2)}`);
  return lines.join("\n");
}
