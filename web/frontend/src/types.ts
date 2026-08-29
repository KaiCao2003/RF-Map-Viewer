export type Matrix = Array<Array<number | null>>;
export type AxisGroup = readonly [number, number];
export type CellRef = readonly [number, number, number, number];

export type ValueMode = "Spike count" | "Mean firing rate (Hz)";
export type Palette = "Gray" | "Viridis" | "Inferno";
export type PolarRadius = "MATLAB row 1 inner" | "Display bottom inner";
export type ViewTab = "rf" | "delay" | "timeline";

export const VALUE_MODES: ValueMode[] = [
  "Mean firing rate (Hz)",
  "Spike count",
];
export const DEFAULT_VALUE_MODE: ValueMode = "Mean firing rate (Hz)";
export const PALETTES: Palette[] = ["Gray", "Viridis", "Inferno"];
export const POLAR_RADIUS_MODES: PolarRadius[] = [
  "MATLAB row 1 inner",
  "Display bottom inner",
];

export interface DatasetMeta {
  id: string;
  name: string;
  sourcePath: string;
  shape: readonly [number, number, number, number];
  unitPool: number[];
  xPositions: number[];
  yPositions: number[];
  timeBinEdges: number[];
  occupancyTimeSec: number[][];
  isVerticalBar?: boolean;
  responseUnits: "spike_count";
  responseNormalization: "none";
  capabilities: {
    probe: boolean;
    hd: boolean;
    waveform: boolean;
    occupancy: boolean;
  };
}

export interface FsEntry {
  name: string;
  path: string;
  type: "directory" | "file";
  size: number | null;
  mtime: number | null;
}

export interface FsPage {
  root: string;
  path: string;
  entries: FsEntry[];
  nextCursor: string | null;
}

export interface ProbeChannel {
  channelId: number;
  x: number;
  y: number;
  shank: number;
}

export type ProbeUnit =
  | { unitId: number; x: number; y: number }
  | { unitId: number; x: null; y: null };

export interface ProbeGeometry {
  probe: string;
  channels: ProbeChannel[];
  units: ProbeUnit[];
}

export interface HdUnitArtifact {
  unitId: number;
  rates: Array<number | null>;
  spikeCounts: number[];
  hdClass: number | null;
}

export interface HdDatasetArtifact {
  available: boolean;
  sourcePath: string | null;
  occupancyTimeS: number[] | null;
  units: HdUnitArtifact[];
  metadata: Record<string, unknown> | null;
}

export type HdPlotMode = "auto" | "polar" | "line";

export type WaveformChannelMode = "same_x_column" | "same_shank";

export interface WaveformChannel {
  channelIndex: number;
  channelId: number;
  rawChannelIndex: number;
  xUm: number;
  yUm: number;
  shankId: number;
}

export type WaveformArtifact =
  | {
    available: false;
    detail: string;
  }
  | {
    available: true;
    sourcePath: string;
    unitId: number;
    quality: string;
    totalSpikeCount: number;
    selectedSpikeCount: number;
    timeCoveragePercent: number;
    maxPtpUv: number;
    mode: WaveformChannelMode;
    localChannelCount: number;
    baselineEndMs: number;
    timesMs: number[];
    timeEdgesMs: number[];
    valuesUv: number[][];
    channels: WaveformChannel[];
    channelLabels: string[];
    bestChannelIndex: number;
    bestChannelRow: number;
    amplitudeLimitUv: number;
  };

export interface HdViewSettings {
  plotMode: HdPlotMode;
  displayBins: number;
  smoothing: boolean;
  sigmaDeg: number;
  compareScale: boolean;
}

export interface ViewState {
  clusterId: number;
  valueMode: ValueMode;
  activeTimeCenterMs: number;
  timelineStartMs: number;
  timelineEndMs: number;
  timelineAnchorMs: number | null;
  rfStartMs: number;
  rfEndMs: number;
  timeResolutionMs: number;
  xBins: number;
  yBins: number;
  smoothRadius: number;
  flipY: boolean;
  palette: Palette;
  polarRadius: PolarRadius;
  polarLayout: boolean;
  rgbMode: boolean;
  selectedCellYMidpoint: number | null;
  selectedCellXMidpoint: number | null;
  timelineScrollFraction: number;
  selectedTab: ViewTab;
}

export interface UnitMetrics {
  total: Matrix;
  peak: Matrix;
  delayMs: Matrix;
  entropy: Matrix;
  binTotals: number[];
  totalSpikes: number;
  bestY: number;
  bestX: number;
  bestRateHz: number;
}

export interface HoverInfo {
  cell: CellRef;
  displayBin?: number;
  clientX: number;
  clientY: number;
}
