import { DEFAULT_HD_DISPLAY_BINS, DEFAULT_HD_SMOOTH_SIGMA, normalizeHdBinCount } from "./hdMath";
import { clamp, snappedResolutionMs } from "./math";
import { DEFAULT_RF_TIMING, readRfTiming, RF_TIMING_KEY, timingPatch, type RfTiming } from "./rfTiming";
import { DEFAULT_VALUE_MODE, PALETTES, POLAR_RADIUS_MODES, VALUE_MODES } from "./types";
import type { DatasetMeta, HdViewSettings, ViewState, WaveformChannelMode } from "./types";

export const VIEWER_SETTINGS_KEY = "rfmapping-viewer-settings-v1";
export type HdLayout = "side-by-side" | "stacked";

export interface ViewerSettings {
  showHd: boolean;
  autoLoadHd: boolean;
  showProbe: boolean;
  autoLoadProbe: boolean;
  showWaveform: boolean;
  tuningSession: number;
  waveformChannelMode: WaveformChannelMode;
  hdLayout: HdLayout;
  hd: HdViewSettings;
  timing: RfTiming;
  filterUnits: boolean;
  zeroBinThreshold: number;
  timeResolutionMs: number;
  valueMode: ViewState["valueMode"];
  xBins: number;
  yBins: number;
  smoothRadius: number;
  flipY: boolean;
  palette: ViewState["palette"];
  polarRadius: ViewState["polarRadius"];
  polarLayout: boolean;
  rgbMode: boolean;
  initialTab: ViewState["selectedTab"];
}

export const DEFAULT_VIEWER_SETTINGS: ViewerSettings = {
  showHd: true, autoLoadHd: true, showProbe: true, autoLoadProbe: true,
  showWaveform: true, tuningSession: 1, waveformChannelMode: "same_x_column",
  hdLayout: "side-by-side",
  hd: { plotMode: "auto", displayBins: DEFAULT_HD_DISPLAY_BINS, smoothing: true,
    sigmaDeg: DEFAULT_HD_SMOOTH_SIGMA * 360 / DEFAULT_HD_DISPLAY_BINS, compareScale: false },
  timing: DEFAULT_RF_TIMING, filterUnits: true, zeroBinThreshold: 1,
  timeResolutionMs: 1, valueMode: DEFAULT_VALUE_MODE, xBins: 0, yBins: 0,
  smoothRadius: 0, flipY: false, palette: "Gray", polarRadius: "Display bottom inner",
  polarLayout: false, rgbMode: false, initialTab: "rf",
};

function readObject(raw: string | null): Record<string, unknown> {
  try {
    const value = JSON.parse(raw ?? "null");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch { return {}; }
}

export function loadViewerSettings(storage: Pick<Storage, "getItem">): ViewerSettings {
  // Preserve preferences saved by the earlier separate controls.
  const companions = readObject(storage.getItem("rfmapping-companion-preferences-v1"));
  const filter = readObject(storage.getItem("rfmapping-zero-bin-unit-filter-v1"));
  const raw = readObject(storage.getItem(VIEWER_SETTINGS_KEY));
  const source: Record<string, unknown> = { ...raw, ...companions,
    ...(filter.enabled !== undefined ? { filterUnits: filter.enabled } : {}),
    ...(filter.zeroSpikeSpatialBinThreshold !== undefined ? { zeroBinThreshold: filter.zeroSpikeSpatialBinThreshold } : {}),
    hdLayout: storage.getItem("rfmapping-hd-layout-v1") ?? raw.hdLayout };
  const hd = readObject(JSON.stringify(raw.hd));
  const defaults = DEFAULT_VIEWER_SETTINGS;
  const bool = (key: keyof ViewerSettings) => typeof source[key] === "boolean" ? source[key] as boolean : defaults[key] as boolean;
  const number = (key: keyof ViewerSettings, low: number, high: number, integer = false) => {
    const value = source[key];
    return typeof value === "number" && Number.isFinite(value) && (!integer || Number.isInteger(value)) && value >= low
      ? clamp(value, low, high) : defaults[key] as number;
  };
  const choice = <T extends string>(value: unknown, choices: readonly T[], fallback: T): T =>
    choices.includes(value as T) ? value as T : fallback;
  return {
    showHd: bool("showHd"), autoLoadHd: bool("autoLoadHd"), showProbe: bool("showProbe"), autoLoadProbe: bool("autoLoadProbe"),
    showWaveform: bool("showWaveform"), tuningSession: number("tuningSession", 1, Number.MAX_SAFE_INTEGER, true),
    waveformChannelMode: choice(source.waveformChannelMode, ["same_x_column", "same_shank"], defaults.waveformChannelMode),
    hdLayout: choice(source.hdLayout, ["side-by-side", "stacked"], defaults.hdLayout),
    hd: {
      plotMode: choice(hd.plotMode, ["auto", "line", "polar"], defaults.hd.plotMode),
      displayBins: typeof hd.displayBins === "number" && Number.isInteger(hd.displayBins) ? normalizeHdBinCount(hd.displayBins) : defaults.hd.displayBins,
      smoothing: typeof hd.smoothing === "boolean" ? hd.smoothing : defaults.hd.smoothing,
      sigmaDeg: typeof hd.sigmaDeg === "number" && Number.isFinite(hd.sigmaDeg) && hd.sigmaDeg > 0 ? hd.sigmaDeg : defaults.hd.sigmaDeg,
      compareScale: typeof hd.compareScale === "boolean" ? hd.compareScale : defaults.hd.compareScale,
    },
    timing: readRfTiming(storage.getItem(RF_TIMING_KEY) ?? (raw.timing ? JSON.stringify(raw.timing) : null)),
    filterUnits: bool("filterUnits"), zeroBinThreshold: number("zeroBinThreshold", 1, 100_000, true),
    timeResolutionMs: number("timeResolutionMs", Number.MIN_VALUE, Number.MAX_VALUE),
    valueMode: choice(source.valueMode, VALUE_MODES, defaults.valueMode),
    xBins: number("xBins", 0, 100_000, true), yBins: number("yBins", 0, 100_000, true),
    smoothRadius: number("smoothRadius", 0, 3, true), flipY: bool("flipY"),
    palette: choice(source.palette, PALETTES, defaults.palette), polarRadius: choice(source.polarRadius, POLAR_RADIUS_MODES, defaults.polarRadius),
    polarLayout: bool("polarLayout"), rgbMode: bool("rgbMode"), initialTab: choice(source.initialTab, ["rf", "delay", "timeline"], defaults.initialTab),
  };
}

export function saveViewerSettings(storage: Pick<Storage, "setItem">, settings: ViewerSettings): void {
  storage.setItem(VIEWER_SETTINGS_KEY, JSON.stringify(settings));
  storage.setItem(RF_TIMING_KEY, JSON.stringify(settings.timing));
  storage.setItem("rfmapping-companion-preferences-v1", JSON.stringify({ tuningSession: settings.tuningSession,
    showWaveform: settings.showWaveform, waveformChannelMode: settings.waveformChannelMode }));
  storage.setItem("rfmapping-zero-bin-unit-filter-v1", JSON.stringify({ enabled: settings.filterUnits,
    zeroSpikeSpatialBinThreshold: settings.zeroBinThreshold }));
  storage.setItem("rfmapping-hd-layout-v1", settings.hdLayout);
}

export function invalidSettingsSection(settings: ViewerSettings): "RF Map" | "Tuning Curve" | null {
  const integerInRange = (value: number, low: number, high: number) => Number.isInteger(value) && value >= low && value <= high;
  if (!Number.isFinite(settings.timeResolutionMs) || settings.timeResolutionMs <= 0
    || !integerInRange(settings.xBins, 0, 100_000) || !integerInRange(settings.yBins, 0, 100_000)
    || !integerInRange(settings.smoothRadius, 0, 3) || !integerInRange(settings.zeroBinThreshold, 1, 100_000)
    || ![settings.timing.sum, settings.timing.a, settings.timing.b].every(([start, end]) => Number.isFinite(start) && Number.isFinite(end) && start < end)) return "RF Map";
  if (!integerInRange(settings.tuningSession, 1, Number.MAX_SAFE_INTEGER)
    || !integerInRange(settings.hd.displayBins, 1, 180) || !Number.isFinite(settings.hd.sigmaDeg) || settings.hd.sigmaDeg <= 0) return "Tuning Curve";
  return null;
}

export function viewerSettingsPatch(meta: DatasetMeta, settings: ViewerSettings): Partial<ViewState> {
  return {
    ...timingPatch(meta, settings.timing), timeResolutionMs: snappedResolutionMs(meta, settings.timeResolutionMs),
    valueMode: settings.valueMode, xBins: Math.min(settings.xBins || meta.shape[2], meta.shape[2]),
    yBins: Math.min(settings.yBins || meta.shape[1], meta.shape[1]), smoothRadius: settings.smoothRadius,
    flipY: settings.flipY, palette: settings.palette, polarRadius: settings.polarRadius,
    polarLayout: settings.polarLayout, rgbMode: settings.rgbMode,
  };
}
