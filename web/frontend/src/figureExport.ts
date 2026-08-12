import type { CellRef, HdViewSettings, ViewState } from "./types";

export const FIGURE_TYPE_IDS = [
  "rf.cartesian",
  "rf.polar",
  "delay.cartesian",
  "delay.polar",
  "rgb.cartesian",
  "rgb.polar",
  "timeline.current",
  "hd.line",
  "hd.polar",
  "probe",
] as const;

export type FigureTypeId = typeof FIGURE_TYPE_IDS[number];
export type FigureOutputFormat = "pdf" | "png";
export type FigurePageOrder = "unit-major" | "page-major";

export interface FigureSettingDefinition {
  type: "number" | "integer" | "boolean" | "string" | "object";
  default: unknown;
  minimum?: number;
  maximum?: number;
  choices?: unknown[];
  description: string;
}

export interface FigureTypeDefinition {
  id: FigureTypeId;
  label: string;
  family: string;
  projection: string;
  settings: Record<string, FigureSettingDefinition>;
  capability?: "hd" | "probe";
}

export interface FigureExportSpec {
  specVersion: number;
  figureTypes: FigureTypeDefinition[];
  pageOrders: FigurePageOrder[];
  formats: FigureOutputFormat[];
  page: {
    minPlots: number;
    maxPlots: number;
    default: FigurePagePayload;
  };
}

export interface FigurePlotPayload {
  type: FigureTypeId;
  settings: Record<string, unknown>;
}

export interface FigurePagePayload {
  title: string;
  plots: FigurePlotPayload[];
}

export interface FigurePreviewRequest {
  specVersion: number;
  clusterId: number;
  pageIndex: number;
  pages: FigurePagePayload[];
  hdPath?: string;
  probePositionsPath?: string;
}

export interface FigureDestinationPayload {
  directory: string;
  baseName: string;
  overwrite: boolean;
}

export interface FigureExportRequest {
  specVersion: number;
  clusterIds: number[];
  order: FigurePageOrder;
  format: FigureOutputFormat;
  pages: FigurePagePayload[];
  destination: FigureDestinationPayload;
  hdPath?: string;
  probePositionsPath?: string;
}

export interface FigureManifestPage {
  outputIndex: number;
  clusterId: number;
  unitIndex: number;
  pageIndex: number;
  title: string;
  file: string | null;
  sha256: string | null;
  placeholders: string[];
}

export interface FigureExportResult {
  format: FigureOutputFormat;
  path: string;
  pageCount: number;
  bytes: number;
  overwritten: boolean;
  manifest: {
    specVersion: number;
    format: FigureOutputFormat;
    order: FigurePageOrder;
    source: string;
    pages: FigureManifestPage[];
  };
}

export interface FigureDirectoryEntry {
  name: string;
  path: string;
  writable: boolean;
}

export interface FigureDirectoryListing {
  path: string;
  writable: boolean;
  entries: FigureDirectoryEntry[];
}

export interface FigurePlotDraft extends FigurePlotPayload {
  id: string;
}

export interface FigurePageDraft {
  id: string;
  title: string;
  plots: FigurePlotDraft[];
}

export interface FigureComposerState {
  unitPool: number[];
  selectedUnitIds: number[];
  unitSearch: string;
  pages: FigurePageDraft[];
  activePageId: string;
  previewPageId: string;
  previewClusterId: number;
  addPlotType: FigureTypeId;
  format: FigureOutputFormat;
  order: FigurePageOrder;
  destinationDirectory: string;
  baseName: string;
  overwrite: boolean;
}

export type FigureComposerAction =
  | { type: "set-units"; unitIds: ReadonlyArray<number> }
  | { type: "toggle-unit"; unitId: number }
  | { type: "set-unit-search"; value: string }
  | { type: "add-page"; page: FigurePageDraft }
  | { type: "remove-page"; pageId: string }
  | { type: "move-page"; pageId: string; delta: -1 | 1 }
  | { type: "select-page"; pageId: string }
  | { type: "set-preview-page"; pageId: string }
  | { type: "rename-page"; pageId: string; title: string }
  | { type: "add-plot"; pageId: string; plot: FigurePlotDraft; maximum: number }
  | { type: "remove-plot"; pageId: string; plotId: string }
  | { type: "move-plot"; pageId: string; plotId: string; delta: -1 | 1 }
  | { type: "replace-plot-settings"; pageId: string; plotId: string; settings: Record<string, unknown> }
  | { type: "set-add-plot-type"; plotType: FigureTypeId }
  | { type: "set-preview-unit"; unitId: number }
  | { type: "set-format"; format: FigureOutputFormat }
  | { type: "set-order"; order: FigurePageOrder }
  | { type: "set-destination"; directory: string }
  | { type: "set-base-name"; value: string }
  | { type: "set-overwrite"; value: boolean };

export interface PlotSettingsContext {
  view: ViewState;
  selectedCell: CellRef | null;
  hd: HdViewSettings;
}

export interface FigureCompanionPaths {
  hdPath?: string | null;
  probePositionsPath?: string | null;
}

export function isFigureTypeId(value: string): value is FigureTypeId {
  return (FIGURE_TYPE_IDS as readonly string[]).includes(value);
}

export function orderedUnitSelection(
  unitPool: ReadonlyArray<number>,
  requested: Iterable<number>,
): number[] {
  const selected = new Set(requested);
  return unitPool.filter((unitId) => selected.has(unitId));
}

export function matchingUnitIds(
  unitPool: ReadonlyArray<number>,
  query: string,
): number[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return [...unitPool];
  return unitPool.filter((clusterId, index) => {
    const indexText = String(index).padStart(3, "0");
    return String(clusterId).toLocaleLowerCase().includes(needle)
      || indexText.includes(needle)
      || `cluster ${clusterId}`.includes(needle);
  });
}

export interface FigureUnitSelectionGesture {
  additive: boolean;
  range: boolean;
  checkbox: boolean;
}

export interface FigureUnitSelectionResult {
  unitIds: number[];
  anchorUnitId: number | null;
}

/**
 * Apply Finder-style selection semantics while preserving the source unitPool
 * order used by preview and export payloads.
 */
export function figureUnitSelectionAfterGesture(
  unitPool: ReadonlyArray<number>,
  visibleUnitIds: ReadonlyArray<number>,
  selectedUnitIds: ReadonlyArray<number>,
  clickedUnitId: number,
  anchorUnitId: number | null,
  gesture: FigureUnitSelectionGesture,
): FigureUnitSelectionResult {
  if (!unitPool.includes(clickedUnitId) || !visibleUnitIds.includes(clickedUnitId)) {
    return {
      unitIds: orderedUnitSelection(unitPool, selectedUnitIds),
      anchorUnitId,
    };
  }

  if (gesture.range) {
    const clickedIndex = visibleUnitIds.indexOf(clickedUnitId);
    const anchorIndex = anchorUnitId == null ? -1 : visibleUnitIds.indexOf(anchorUnitId);
    if (anchorIndex >= 0) {
      const low = Math.min(anchorIndex, clickedIndex);
      const high = Math.max(anchorIndex, clickedIndex);
      const range = visibleUnitIds.slice(low, high + 1);
      return {
        unitIds: orderedUnitSelection(
          unitPool,
          gesture.additive ? [...selectedUnitIds, ...range] : range,
        ),
        anchorUnitId,
      };
    }
  }

  if (gesture.additive || gesture.checkbox) {
    const selected = new Set(selectedUnitIds);
    if (selected.has(clickedUnitId)) selected.delete(clickedUnitId);
    else selected.add(clickedUnitId);
    return {
      unitIds: orderedUnitSelection(unitPool, selected),
      anchorUnitId: clickedUnitId,
    };
  }

  return { unitIds: [clickedUnitId], anchorUnitId: clickedUnitId };
}

export function currentFigureType(view: ViewState): FigureTypeId {
  if (view.selectedTab === "timeline") return "timeline.current";
  if (view.selectedTab === "rf") return view.polarLayout ? "rf.polar" : "rf.cartesian";
  if (view.rgbMode) return view.polarLayout ? "rgb.polar" : "rgb.cartesian";
  return view.polarLayout ? "delay.polar" : "delay.cartesian";
}

function spatialSettings(view: ViewState): Record<string, unknown> {
  return {
    rfStartMs: view.rfStartMs,
    rfEndMs: view.rfEndMs,
    valueMode: view.valueMode,
    xBins: view.xBins,
    yBins: view.yBins,
    smoothRadius: view.smoothRadius,
    flipY: view.flipY,
    palette: view.palette,
    polarRadius: view.polarRadius,
  };
}

function temporalSettings(view: ViewState): Record<string, unknown> {
  return {
    timeResolutionMs: view.timeResolutionMs,
    valueMode: view.valueMode,
    xBins: view.xBins,
    yBins: view.yBins,
    smoothRadius: view.smoothRadius,
    flipY: view.flipY,
    palette: view.palette,
    polarRadius: view.polarRadius,
    responseFloor: 0,
  };
}

export function snapshotPlotSettings(
  type: FigureTypeId,
  context: PlotSettingsContext,
): Record<string, unknown> {
  if (type === "rf.cartesian" || type === "rf.polar") {
    return spatialSettings(context.view);
  }
  if (
    type === "delay.cartesian"
    || type === "delay.polar"
    || type === "rgb.cartesian"
    || type === "rgb.polar"
  ) {
    return temporalSettings(context.view);
  }
  if (type === "timeline.current") {
    return {
      timelineStartMs: context.view.timelineStartMs,
      timelineEndMs: context.view.timelineEndMs,
      activeTimeCenterMs: context.view.activeTimeCenterMs,
      timeResolutionMs: context.view.timeResolutionMs,
      valueMode: context.view.valueMode,
      xBins: context.view.xBins,
      yBins: context.view.yBins,
      smoothRadius: context.view.smoothRadius,
      flipY: context.view.flipY,
      palette: context.view.palette,
      polarLayout: context.view.polarLayout,
      polarRadius: context.view.polarRadius,
      spatialProjection: context.selectedCell == null ? null : {
        yStart: context.selectedCell[0],
        yEnd: context.selectedCell[1],
        xStart: context.selectedCell[2],
        xEnd: context.selectedCell[3],
      },
    };
  }
  if (type === "hd.line" || type === "hd.polar") {
    return {
      displayBins: context.hd.displayBins,
      smoothing: context.hd.smoothing,
      sigmaDeg: context.hd.sigmaDeg,
    };
  }
  return {};
}

export function safeExportBaseName(datasetName: string): string {
  const stem = datasetName.replace(/\.[^.]+$/, "");
  const safe = stem.replace(/[^A-Za-z0-9 _.-]+/g, "_").replace(/^[. ]+/, "").trim();
  return `${safe || "rfmapping"}_figures`.slice(0, 128);
}

export function createFigureComposerState(options: {
  unitPool: ReadonlyArray<number>;
  currentClusterId: number;
  initialType: FigureTypeId;
  initialSettings: Record<string, unknown>;
  baseName: string;
  pageId?: string;
  plotId?: string;
}): FigureComposerState {
  const pageId = options.pageId ?? "page-1";
  const plotId = options.plotId ?? "plot-1";
  const previewClusterId = options.unitPool.includes(options.currentClusterId)
    ? options.currentClusterId
    : options.unitPool[0];
  return {
    unitPool: [...options.unitPool],
    selectedUnitIds: [previewClusterId],
    unitSearch: "",
    pages: [{
      id: pageId,
      title: "Page 1",
      plots: [{ id: plotId, type: options.initialType, settings: { ...options.initialSettings } }],
    }],
    activePageId: pageId,
    previewPageId: pageId,
    previewClusterId,
    addPlotType: options.initialType,
    format: "pdf",
    order: "unit-major",
    destinationDirectory: "",
    baseName: options.baseName,
    overwrite: false,
  };
}

function withPages(
  state: FigureComposerState,
  pages: FigurePageDraft[],
): FigureComposerState {
  const activePageId = pages.some((page) => page.id === state.activePageId)
    ? state.activePageId
    : pages[0].id;
  const previewPageId = pages.some((page) => page.id === state.previewPageId)
    ? state.previewPageId
    : activePageId;
  return { ...state, pages, activePageId, previewPageId };
}

export function figureComposerReducer(
  state: FigureComposerState,
  action: FigureComposerAction,
): FigureComposerState {
  switch (action.type) {
    case "set-units": {
      const selectedUnitIds = orderedUnitSelection(state.unitPool, action.unitIds);
      const previewClusterId = selectedUnitIds.includes(state.previewClusterId)
        ? state.previewClusterId
        : selectedUnitIds[0] ?? state.previewClusterId;
      return { ...state, selectedUnitIds, previewClusterId };
    }
    case "toggle-unit": {
      const requested = new Set(state.selectedUnitIds);
      if (requested.has(action.unitId)) requested.delete(action.unitId);
      else requested.add(action.unitId);
      return figureComposerReducer(state, { type: "set-units", unitIds: [...requested] });
    }
    case "set-unit-search":
      return { ...state, unitSearch: action.value };
    case "add-page":
      return {
        ...state,
        pages: [...state.pages, action.page],
        activePageId: action.page.id,
        previewPageId: action.page.id,
      };
    case "remove-page":
      if (state.pages.length <= 1) return state;
      return withPages(state, state.pages.filter((page) => page.id !== action.pageId));
    case "move-page": {
      const index = state.pages.findIndex((page) => page.id === action.pageId);
      const destination = index + action.delta;
      if (index < 0 || destination < 0 || destination >= state.pages.length) return state;
      const pages = [...state.pages];
      [pages[index], pages[destination]] = [pages[destination], pages[index]];
      return withPages(state, pages);
    }
    case "select-page":
      return state.pages.some((page) => page.id === action.pageId)
        ? { ...state, activePageId: action.pageId }
        : state;
    case "set-preview-page":
      return state.pages.some((page) => page.id === action.pageId)
        ? { ...state, previewPageId: action.pageId }
        : state;
    case "rename-page":
      return withPages(state, state.pages.map((page) => page.id === action.pageId
        ? { ...page, title: action.title }
        : page));
    case "add-plot":
      return withPages(state, state.pages.map((page) => {
        if (page.id !== action.pageId || page.plots.length >= action.maximum) return page;
        return { ...page, plots: [...page.plots, action.plot] };
      }));
    case "remove-plot":
      return withPages(state, state.pages.map((page) => {
        if (page.id !== action.pageId || page.plots.length <= 1) return page;
        return { ...page, plots: page.plots.filter((plot) => plot.id !== action.plotId) };
      }));
    case "move-plot":
      return withPages(state, state.pages.map((page) => {
        if (page.id !== action.pageId) return page;
        const index = page.plots.findIndex((plot) => plot.id === action.plotId);
        const destination = index + action.delta;
        if (index < 0 || destination < 0 || destination >= page.plots.length) return page;
        const plots = [...page.plots];
        [plots[index], plots[destination]] = [plots[destination], plots[index]];
        return { ...page, plots };
      }));
    case "replace-plot-settings":
      return withPages(state, state.pages.map((page) => page.id !== action.pageId
        ? page
        : {
          ...page,
          plots: page.plots.map((plot) => plot.id === action.plotId
            ? { ...plot, settings: { ...action.settings } }
            : plot),
        }));
    case "set-add-plot-type":
      return { ...state, addPlotType: action.plotType };
    case "set-preview-unit":
      return state.selectedUnitIds.includes(action.unitId)
        ? { ...state, previewClusterId: action.unitId }
        : state;
    case "set-format":
      return { ...state, format: action.format, overwrite: false };
    case "set-order":
      return { ...state, order: action.order };
    case "set-destination":
      return { ...state, destinationDirectory: action.directory, overwrite: false };
    case "set-base-name":
      return { ...state, baseName: action.value, overwrite: false };
    case "set-overwrite":
      return { ...state, overwrite: action.value };
  }
}

export function serializeFigurePages(pages: ReadonlyArray<FigurePageDraft>): FigurePagePayload[] {
  return pages.map((page) => ({
    title: page.title,
    plots: page.plots.map((plot) => ({
      type: plot.type,
      settings: { ...plot.settings },
    })),
  }));
}

export function buildFigurePreviewRequest(
  state: FigureComposerState,
  specVersion: number,
  companions: FigureCompanionPaths = {},
): FigurePreviewRequest {
  const pageIndex = Math.max(0, state.pages.findIndex((page) => page.id === state.previewPageId));
  return {
    specVersion,
    clusterId: state.previewClusterId,
    pageIndex,
    pages: serializeFigurePages(state.pages),
    ...(companions.hdPath ? { hdPath: companions.hdPath } : {}),
    ...(companions.probePositionsPath ? { probePositionsPath: companions.probePositionsPath } : {}),
  };
}

export function buildFigureExportRequest(
  state: FigureComposerState,
  specVersion: number,
  companions: FigureCompanionPaths = {},
): FigureExportRequest {
  return {
    specVersion,
    clusterIds: [...state.selectedUnitIds],
    order: state.order,
    format: state.format,
    pages: serializeFigurePages(state.pages),
    destination: {
      directory: state.destinationDirectory,
      baseName: state.baseName.trim(),
      overwrite: state.overwrite,
    },
    ...(companions.hdPath ? { hdPath: companions.hdPath } : {}),
    ...(companions.probePositionsPath ? { probePositionsPath: companions.probePositionsPath } : {}),
  };
}

const SAFE_BASE_NAME = /^[A-Za-z0-9][A-Za-z0-9 _.-]{0,127}$/;

export function composerValidationError(
  state: FigureComposerState,
  destinationWritable: boolean,
): string | null {
  if (!state.selectedUnitIds.length) return "Select at least one unit.";
  if (!state.pages.length) return "Add at least one page.";
  if (state.pages.some((page) => !page.title.trim())) return "Every page needs a name.";
  if (state.pages.some((page) => !page.plots.length)) return "Every page needs at least one plot.";
  if (!SAFE_BASE_NAME.test(state.baseName.trim())) {
    return "File name must use 1–128 letters, numbers, spaces, dots, dashes, or underscores.";
  }
  if (!destinationWritable) return "Choose a writable destination directory.";
  return null;
}

export function parentFigureDirectory(path: string): string {
  const parts = path.split("/").filter(Boolean);
  parts.pop();
  return parts.join("/");
}
