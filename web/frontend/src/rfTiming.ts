import { snapTimeRange, timeBounds } from "./math";
import type { DatasetMeta, ViewState } from "./types";

export const RF_TIMING_KEY = "rfmapping-rf-timing-v1";
export interface RfTiming {
  mode: "sum" | "difference";
  sum: [number, number];
  a: [number, number];
  b: [number, number];
}
export const DEFAULT_RF_TIMING: RfTiming = { mode: "sum", sum: [0, 200], a: [80, 160], b: [0, 80] };

export function readRfTiming(raw: string | null): RfTiming {
  try {
    const value = JSON.parse(raw ?? "null");
    if (!value || !["sum", "difference"].includes(value.mode)) return DEFAULT_RF_TIMING;
    if (![value.sum, value.a, value.b].every((range) => Array.isArray(range) && range.length === 2 && range.every(Number.isFinite) && range[0] < range[1])) return DEFAULT_RF_TIMING;
    return value;
  } catch { return DEFAULT_RF_TIMING; }
}

export function timingPatch(meta: DatasetMeta, timing: RfTiming): Partial<ViewState> {
  const bounds = (range: [number, number]) => timeBounds(meta, snapTimeRange(meta, ...range));
  const sum = bounds(timing.sum), a = bounds(timing.a), b = bounds(timing.b);
  const active = timing.mode === "difference" ? a : sum;
  return { rfWindowMode: timing.mode, rfStartMs: active[0], rfEndMs: active[1],
    rfSumStartMs: sum[0], rfSumEndMs: sum[1], rfAStartMs: a[0], rfAEndMs: a[1],
    rfBStartMs: b[0], rfBEndMs: b[1] };
}

export function timingFromState(state: ViewState): RfTiming {
  const difference = state.rfWindowMode === "difference";
  return { mode: difference ? "difference" : "sum",
    sum: difference ? [state.rfSumStartMs ?? 0, state.rfSumEndMs ?? 200] : [state.rfStartMs, state.rfEndMs],
    a: difference ? [state.rfStartMs, state.rfEndMs] : [state.rfAStartMs ?? 80, state.rfAEndMs ?? 160],
    b: [state.rfBStartMs ?? 0, state.rfBEndMs ?? 80] };
}

export function toggleRfMode(meta: DatasetMeta, state: ViewState): Partial<ViewState> {
  const timing = timingFromState(state);
  timing.mode = timing.mode === "sum" ? "difference" : "sum";
  return timingPatch(meta, timing);
}
