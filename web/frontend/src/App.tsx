import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  exportDisplayedCsv,
  getHdDataset,
  getProbeGeometry,
  getUnitCounts,
  listRemoteFiles,
  openRemoteDataset,
} from "./api";
import HdPanel from "./components/HdPanel";
import FigureExportComposer from "./components/FigureExportComposer";
import { SpatialPlot, TimelinePlot } from "./components/Plots";
import ProbeLayout, { type ProbeSelection } from "./components/ProbeLayout";
import RemoteBrowser from "./components/RemoteBrowser";
import SaveArtifactDialog from "./components/SaveArtifactDialog";
import {
  jsonChoiceLabel,
  mergeJsonChoices,
  type JsonChoice,
  urlForJsonSource,
} from "./jsonChoices";
import {
  baseBinMs,
  cellFromMidpoint,
  clamp,
  formatNumber,
  formatResponse,
  groupResponseValue,
  groupResponseValues,
  groupTemporalMetrics,
  snapTimeRange,
  timeBounds,
  timeGroupForMs,
  timeGroups,
  unitMetrics,
  valueModeUnit,
} from "./math";
import { DEFAULT_HD_DISPLAY_BINS, DEFAULT_HD_SMOOTH_SIGMA } from "./hdMath";
import { nearestProbeUnitToRegionCenter } from "./probeSelection";
import { resolutionChangePatch, timelineSelectionPatch } from "./viewStateMath";
import { VIEWER_TABS } from "./viewTabs";
import type {
  CellRef,
  DatasetMeta,
  FsEntry,
  HdDatasetArtifact,
  HdViewSettings,
  Palette,
  PolarRadius,
  ProbeGeometry,
  ValueMode,
  ViewState,
} from "./types";
import { PALETTES, POLAR_RADIUS_MODES, VALUE_MODES } from "./types";

const RECENT_JSON_KEY = "rfmapping-recent-json-v1";
const HD_LAYOUT_KEY = "rfmapping-hd-layout-v1";

const INITIAL_HD_VIEW_SETTINGS: HdViewSettings = {
  plotMode: "auto",
  displayBins: DEFAULT_HD_DISPLAY_BINS,
  smoothing: true,
  sigmaDeg: DEFAULT_HD_SMOOTH_SIGMA * 360 / DEFAULT_HD_DISPLAY_BINS,
  compareScale: false,
};

type HdLayout = "side-by-side" | "stacked";

function loadHdLayout(): HdLayout {
  return window.localStorage.getItem(HD_LAYOUT_KEY) === "stacked" ? "stacked" : "side-by-side";
}

interface MessageDialogState {
  title: string;
  text: string;
}

interface ExportDialogState {
  path: string;
  busy: boolean;
  error: string;
  overwritePending: boolean;
}

function valueModeSlug(valueMode: ValueMode): string {
  if (valueMode === "Spike count") return "spike_count";
  if (valueMode === "Spikes / presentation") return "spikes_per_presentation";
  return "mean_firing_rate_hz";
}

function initialViewState(meta: DatasetMeta): ViewState {
  const rfRange = snapTimeRange(meta, 0, 200);
  const rfBounds = timeBounds(meta, rfRange);
  const resolution = baseBinMs(meta);
  const groups = timeGroups(meta, resolution);
  const activeBounds = timeBounds(meta, groups[0]);
  return {
    clusterId: meta.unitPool[0],
    valueMode: "Spike count",
    activeTimeCenterMs: (activeBounds[0] + activeBounds[1]) / 2,
    timelineStartMs: meta.timeBinEdges[0] * 1000,
    timelineEndMs: meta.timeBinEdges.at(-1)! * 1000,
    timelineAnchorMs: null,
    rfStartMs: rfBounds[0],
    rfEndMs: rfBounds[1],
    timeResolutionMs: resolution,
    xBins: meta.shape[2],
    yBins: meta.shape[1],
    smoothRadius: 0,
    flipY: false,
    palette: "Gray",
    polarRadius: "Display bottom inner",
    polarLayout: false,
    rgbMode: false,
    selectedCellYMidpoint: null,
    selectedCellXMidpoint: null,
    timelineScrollFraction: 0,
    selectedTab: "rf",
  };
}

function loadRecentJsonPaths(): string[] {
  try {
    const value = JSON.parse(window.localStorage.getItem(RECENT_JSON_KEY) ?? "[]");
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function parentDirectory(path: string): string {
  const trimmed = path.replace(/\/+$/, "");
  const parent = trimmed.replace(/\/[^/]+$/, "");
  return parent.startsWith("/mnt/senzailab") ? parent : "/mnt/senzailab";
}

function SourceChooser({
  overlay,
  busy,
  error,
  initialPath,
  kind = "rf-json",
  title = "Open RF mapping JSON",
  busyLabel = "Opening JSON…",
  onClose,
  onRemote,
}: {
  overlay: boolean;
  busy: boolean;
  error: string;
  initialPath: string;
  kind?: "rf-json" | "tuning-json" | "positions-csv";
  title?: string;
  busyLabel?: string;
  onClose: () => void;
  onRemote: (path: string) => void;
}) {
  const content = (
    <div
      className={`source-chooser ${overlay ? "source-modal" : ""}`}
      role={overlay ? "dialog" : undefined}
      aria-modal={overlay || undefined}
      aria-label={title}
    >
      <header className="dialog-titlebar">
        <strong>{title}</strong>
        {overlay && <button type="button" aria-label="Close" onClick={onClose}>×</button>}
      </header>
      {error && <div className="dialog-error" role="alert">{error}</div>}
      <RemoteBrowser key={`${kind}:${initialPath}`} busy={busy} initialPath={initialPath} kind={kind} title={title} onOpen={onRemote} />
      {busy && <div className="dialog-status"><span className="spinner small" /> {busyLabel}</div>}
    </div>
  );
  return overlay ? <div className="modal-backdrop">{content}</div> : <main className="landing">{content}</main>;
}

export default function App() {
  const [meta, setMeta] = useState<DatasetMeta | null>(null);
  const [viewState, setViewState] = useState<ViewState | null>(null);
  const [counts, setCounts] = useState<Float64Array | null>(null);
  const countsCache = useRef(new Map<string, Map<number, Float64Array>>());
  const initialQueryHandled = useRef(false);
  const lastLocalCluster = useRef<number | null>(null);
  const [unitStatus, setUnitStatus] = useState<"loading" | "ready" | "unavailable" | "error">("loading");
  const [error, setError] = useState("");
  const [sourceOpen, setSourceOpen] = useState(false);
  const [sourceBusy, setSourceBusy] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [messageDialog, setMessageDialog] = useState<MessageDialogState | null>(null);
  const [exportDialog, setExportDialog] = useState<ExportDialogState | null>(null);
  const [figureComposerOpen, setFigureComposerOpen] = useState(false);
  const [recentPaths, setRecentPaths] = useState<string[]>(loadRecentJsonPaths);
  const [jsonChoices, setJsonChoices] = useState<JsonChoice[]>([]);
  const [jsonChoiceRefresh, setJsonChoiceRefresh] = useState(0);
  const [probe, setProbe] = useState<ProbeGeometry | null>(null);
  const [probeError, setProbeError] = useState("");
  const [probeBusy, setProbeBusy] = useState(false);
  const [probeChooserOpen, setProbeChooserOpen] = useState(false);
  const [probePositionsPath, setProbePositionsPath] = useState<string | null>(null);
  const [probeCollapsed, setProbeCollapsed] = useState(false);
  const [probeSelection, setProbeSelection] = useState<ProbeSelection | null>(null);
  const [probeFilter, setProbeFilter] = useState<number[] | null>(null);
  const [hdArtifact, setHdArtifact] = useState<HdDatasetArtifact | null>(null);
  const [hdError, setHdError] = useState("");
  const [hdLoading, setHdLoading] = useState(false);
  const [hdPath, setHdPath] = useState<string | null>(null);
  const [hdChooserOpen, setHdChooserOpen] = useState(false);
  const [hdCollapsed, setHdCollapsed] = useState(false);
  const [hdLayout, setHdLayout] = useState<HdLayout>(loadHdLayout);
  const [hdSettings, setHdSettings] = useState<HdViewSettings>(INITIAL_HD_VIEW_SETTINGS);
  const [hdRefresh, setHdRefresh] = useState(0);

  const updateState = useCallback((patch: Partial<ViewState> | ((current: ViewState) => Partial<ViewState>)) => {
    setViewState((current) => current ? { ...current, ...(typeof patch === "function" ? patch(current) : patch) } : current);
  }, []);

  const commitDataset = useCallback((next: DatasetMeta) => {
    countsCache.current.clear();
    setCounts(null);
    setMeta(next);
    setJsonChoices([{ path: next.sourcePath, mtime: null }]);
    lastLocalCluster.current = next.unitPool[0];
    setViewState((current) => {
      const initial = initialViewState(next);
      if (!current) return initial;
      return {
        ...initial,
        valueMode: current.valueMode !== "Spike count" && !next.presentationCounts ? "Spike count" : current.valueMode,
        smoothRadius: current.smoothRadius,
        flipY: current.flipY,
        palette: current.palette,
        polarRadius: current.polarRadius,
        polarLayout: current.polarLayout,
        rgbMode: current.rgbMode,
        selectedTab: current.selectedTab,
      };
    });
    setUnitStatus("loading");
    setSourceOpen(false);
    setProbe(null);
    setProbeError("");
    setProbePositionsPath(null);
    setProbeChooserOpen(false);
    setProbeSelection(null);
    setProbeFilter(null);
    setHdArtifact(null);
    setHdError("");
    setHdPath(null);
    setHdChooserOpen(false);
    setFigureComposerOpen(false);
    setError("");
    setRecentPaths((current) => {
      const updated = [next.sourcePath, ...current.filter((path) => path !== next.sourcePath)].slice(0, 24);
      window.localStorage.setItem(RECENT_JSON_KEY, JSON.stringify(updated));
      return updated;
    });
    setJsonChoiceRefresh((value) => value + 1);
    document.title = `${next.name} — RF Map Viewer`;
  }, []);

  const openRemote = useCallback(async (path: string) => {
    setSourceBusy(true);
    setError("");
    try {
      const next = await openRemoteDataset(path);
      commitDataset(next);
      window.history.replaceState(null, "", urlForJsonSource(window.location.href, next.sourcePath));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not open JSON.");
    } finally {
      setSourceBusy(false);
    }
  }, [commitDataset]);

  useEffect(() => {
    document.title = meta ? `${meta.name} — RF Map Viewer` : "RF Map Viewer";
  }, [meta]);

  useEffect(() => {
    if (!meta) return;
    const controller = new AbortController();
    const folder = parentDirectory(meta.sourcePath);
    void (async () => {
      const entries: FsEntry[] = [];
      const seenCursors = new Set<string>();
      let cursor: string | undefined;
      try {
        do {
          const page = await listRemoteFiles(folder, cursor, controller.signal);
          entries.push(...page.entries);
          const nextCursor = page.nextCursor ?? undefined;
          if (!nextCursor || seenCursors.has(nextCursor)) {
            cursor = undefined;
          } else {
            seenCursors.add(nextCursor);
            cursor = nextCursor;
          }
        } while (cursor && !controller.signal.aborted);
        if (!controller.signal.aborted) {
          setJsonChoices(mergeJsonChoices(entries, meta.sourcePath, recentPaths));
        }
      } catch {
        if (!controller.signal.aborted) {
          setJsonChoices(mergeJsonChoices([], meta.sourcePath, recentPaths));
        }
      }
    })();
    return () => controller.abort();
  }, [jsonChoiceRefresh, meta, recentPaths]);

  useEffect(() => {
    if (initialQueryHandled.current) return;
    initialQueryHandled.current = true;
    const initialPath = new URL(window.location.href).searchParams.get("json");
    if (initialPath) void openRemote(initialPath);
  }, [openRemote]);

  useEffect(() => {
    if (!meta || !viewState) return;
    if (meta.unitPool.includes(viewState.clusterId)) {
      lastLocalCluster.current = viewState.clusterId;
      return;
    }
    const fallback = lastLocalCluster.current != null && meta.unitPool.includes(lastLocalCluster.current)
      ? lastLocalCluster.current
      : meta.unitPool[0];
    updateState({ clusterId: fallback, selectedCellXMidpoint: null, selectedCellYMidpoint: null });
  }, [meta, updateState, viewState]);

  useEffect(() => {
    if (!meta || !viewState) return;
    const controller = new AbortController();
    const datasetId = meta.id;
    let datasetCache = countsCache.current.get(datasetId);
    if (!datasetCache) {
      datasetCache = new Map<number, Float64Array>();
      countsCache.current.set(datasetId, datasetCache);
    }
    const localIndex = meta.unitPool.indexOf(viewState.clusterId);
    if (localIndex < 0) {
      setCounts(null);
      setUnitStatus("unavailable");
      return () => controller.abort();
    }
    const cached = datasetCache.get(viewState.clusterId);
    if (cached) {
      setCounts(cached);
      setUnitStatus("ready");
    } else {
      setCounts(null);
      setUnitStatus("loading");
      getUnitCounts(meta, viewState.clusterId, controller.signal)
        .then((values) => {
          if (controller.signal.aborted || countsCache.current.get(datasetId) !== datasetCache) return;
          datasetCache.set(viewState.clusterId, values);
          setCounts(values);
          setUnitStatus("ready");
        })
        .catch((caught) => {
          if (!controller.signal.aborted) {
            setError(caught instanceof Error ? caught.message : "Could not load unit counts.");
            setUnitStatus("error");
          }
        });
    }
    const neighbors = [localIndex - 1, localIndex + 1]
      .filter((index) => 0 <= index && index < meta.unitPool.length)
      .map((index) => meta.unitPool[index]);
    neighbors.forEach((clusterId) => {
      if (!datasetCache.has(clusterId)) {
        void getUnitCounts(meta, clusterId, controller.signal).then((values) => {
          if (!controller.signal.aborted && countsCache.current.get(datasetId) === datasetCache) {
            datasetCache.set(clusterId, values);
          }
        }).catch(() => undefined);
      }
    });
    return () => controller.abort();
  }, [meta, viewState?.clusterId]);

  useEffect(() => {
    if (!meta?.capabilities.probe || probePositionsPath) return;
    const controller = new AbortController();
    setProbeError("");
    getProbeGeometry(meta.id, {}, controller.signal)
      .then(setProbe)
      .catch((caught) => {
        if (!controller.signal.aborted) setProbeError(caught instanceof Error ? caught.message : "Could not load probe layout.");
      });
    return () => controller.abort();
  }, [meta, probePositionsPath]);

  useEffect(() => {
    if (!meta || (!meta.capabilities.hd && !hdPath)) {
      setHdArtifact(null);
      setHdLoading(false);
      return;
    }
    const controller = new AbortController();
    setHdLoading(true);
    setHdError("");
    getHdDataset(meta.id, hdPath ?? undefined, controller.signal)
      .then((artifact) => { if (!controller.signal.aborted) setHdArtifact(artifact); })
      .catch((caught) => {
        if (!controller.signal.aborted) {
          setHdArtifact(null);
          setHdError(caught instanceof Error ? caught.message : "Could not load HD tuning data.");
        }
      })
      .finally(() => { if (!controller.signal.aborted) setHdLoading(false); });
    return () => controller.abort();
  }, [hdPath, hdRefresh, meta]);

  const navigationPool = useMemo(() => {
    const base = meta?.unitPool ?? [];
    if (!probeFilter) return base;
    const allowed = new Set(probeFilter);
    return base.filter((unit) => allowed.has(unit));
  }, [meta, probeFilter]);

  const stepUnit = useCallback((delta: number) => {
    if (!viewState || !navigationPool.length) return;
    const currentIndex = navigationPool.indexOf(viewState.clusterId);
    const start = currentIndex >= 0 ? currentIndex : navigationPool.findIndex((unit) => unit > viewState.clusterId);
    const index = ((start >= 0 ? start : 0) + delta + navigationPool.length) % navigationPool.length;
    updateState({ clusterId: navigationPool[index], selectedCellXMidpoint: null, selectedCellYMidpoint: null });
  }, [navigationPool, updateState, viewState]);

  const groups = useMemo(() => meta && viewState ? timeGroups(meta, viewState.timeResolutionMs) : [], [meta, viewState?.timeResolutionMs]);
  const activeGroup = useMemo(() => meta && viewState && groups.length
    ? timeGroupForMs(meta, groups, viewState.activeTimeCenterMs) : 0, [groups, meta, viewState?.activeTimeCenterMs]);

  const selectTimelineBin = useCallback((binIndex: number, extend: boolean) => {
    if (!meta || !groups[binIndex]) return;
    updateState((current) => timelineSelectionPatch(meta, current, groups, binIndex, extend));
  }, [groups, meta, updateState]);

  const stepTimeline = useCallback((delta: number) => {
    if (!groups.length) return;
    selectTimelineBin(clamp(activeGroup + delta, 0, groups.length - 1), false);
  }, [activeGroup, groups.length, selectTimelineBin]);

  const changeResolution = useCallback((requested: number) => {
    if (!meta) return;
    updateState((current) => resolutionChangePatch(meta, current, requested));
  }, [meta, updateState]);

  const stepResolution = useCallback((delta: number) => {
    if (!viewState) return;
    changeResolution(viewState.timeResolutionMs + delta);
  }, [changeResolution, viewState]);

  const showFullTimeline = useCallback(() => {
    if (!meta) return;
    updateState({
      activeTimeCenterMs: timeBounds(meta, timeGroups(meta, viewState?.timeResolutionMs ?? baseBinMs(meta))[0]).reduce((sum, value) => sum + value, 0) / 2,
      timelineStartMs: meta.timeBinEdges[0] * 1000,
      timelineEndMs: meta.timeBinEdges.at(-1)! * 1000,
      timelineAnchorMs: null,
    });
  }, [meta, updateState, viewState?.timeResolutionMs]);

  const updateTimelineScroll = useCallback((fraction: number) => {
    updateState({ timelineScrollFraction: fraction });
  }, [updateState]);

  const metrics = useMemo(() => counts && meta ? unitMetrics(counts, meta) : null, [counts, meta]);
  const bestTemporal = useMemo(() => counts && meta && metrics && groups.length
    ? groupTemporalMetrics(
      counts,
      meta,
      [metrics.bestY, metrics.bestY, metrics.bestX, metrics.bestX],
      groups,
    )
    : null, [counts, groups, meta, metrics]);
  const selectedCell = useMemo<CellRef | null>(() => {
    if (!meta || !viewState || !metrics) return null;
    return cellFromMidpoint(
      meta,
      viewState.xBins,
      viewState.yBins,
      viewState.flipY,
      viewState.selectedCellYMidpoint,
      viewState.selectedCellXMidpoint,
    ) ?? [metrics.bestY, metrics.bestY, metrics.bestX, metrics.bestX];
  }, [
    meta,
    metrics,
    viewState?.flipY,
    viewState?.selectedCellXMidpoint,
    viewState?.selectedCellYMidpoint,
    viewState?.xBins,
    viewState?.yBins,
  ]);

  const selectCell = useCallback((cell: CellRef) => updateState({
    selectedCellYMidpoint: (cell[0] + cell[1]) / 2,
    selectedCellXMidpoint: (cell[2] + cell[3]) / 2,
  }), [updateState]);

  const selectedDetails = useMemo(() => {
    if (!meta || !viewState || !counts || !selectedCell || !groups.length) return null;
    const rfRange = snapTimeRange(meta, viewState.rfStartMs, viewState.rfEndMs);
    const values = groupResponseValues(counts, meta, selectedCell, groups, viewState.valueMode);
    const temporal = groupTemporalMetrics(counts, meta, selectedCell, groups);
    const peakIndex = temporal.peakGroupIndex ?? -1;
    return {
      activeValue: values[activeGroup] ?? null,
      rfValue: groupResponseValue(counts, meta, selectedCell, rfRange, viewState.valueMode),
      totalValue: groupResponseValue(counts, meta, selectedCell, [0, meta.shape[3] - 1], viewState.valueMode),
      peakValue: peakIndex < 0 ? null : values[peakIndex] ?? null,
      peakIndex,
      delay: temporal.delayMs,
      entropy: temporal.entropy,
    };
  }, [activeGroup, counts, groups, meta, selectedCell, viewState]);

  const openExportDialog = useCallback(() => {
    if (!meta || !viewState || !counts) {
      setMessageDialog({ title: "Unit unavailable", text: `Cluster ${viewState?.clusterId ?? ""} is not available in this session.` });
      return;
    }
    const unitIndex = meta.unitPool.indexOf(viewState.clusterId);
    setExportDialog({
      path: `unit_${String(unitIndex).padStart(3, "0")}_cluster_${viewState.clusterId}_${valueModeSlug(viewState.valueMode)}_displayed.csv`,
      busy: false,
      error: "",
      overwritePending: false,
    });
  }, [counts, meta, viewState]);

  const openFigureComposer = useCallback(() => {
    if (!meta || !viewState) return;
    setFigureComposerOpen(true);
  }, [meta, viewState]);

  const exportCsv = useCallback(async (overwrite: boolean) => {
    if (!meta || !viewState || !counts || !exportDialog) return;
    const outputPath = exportDialog.path.trim();
    setExportDialog((current) => current ? { ...current, busy: true, error: "" } : current);
    try {
      const result = await exportDisplayedCsv(meta.id, {
        clusterId: viewState.clusterId,
        valueMode: viewState.valueMode,
        rfStartMs: viewState.rfStartMs,
        rfEndMs: viewState.rfEndMs,
        timeResolutionMs: viewState.timeResolutionMs,
        xBins: viewState.xBins,
        yBins: viewState.yBins,
        smoothRadius: viewState.smoothRadius,
        flipY: viewState.flipY,
        palette: viewState.palette,
        outputPath,
        overwrite,
      });
      setExportDialog(null);
      setMessageDialog({ title: "Export complete", text: `Wrote displayed matrix to ${result.path}` });
    } catch (caught) {
      const conflict = caught instanceof ApiError && caught.status === 409;
      setExportDialog((current) => current ? {
        ...current,
        busy: false,
        error: conflict ? "That file already exists." : caught instanceof Error ? caught.message : "Export failed.",
        overwritePending: conflict,
      } : current);
    }
  }, [counts, exportDialog, meta, viewState]);

  const normalizeRfRange = useCallback(() => {
    if (!meta) return;
    updateState((current) => {
      const range = snapTimeRange(meta, current.rfStartMs, current.rfEndMs);
      const bounds = timeBounds(meta, range);
      return { rfStartMs: bounds[0], rfEndMs: bounds[1] };
    });
  }, [meta, updateState]);

  const openChooser = useCallback(() => {
    setError("");
    setSourceOpen(true);
  }, []);

  const handleRemoteChoice = useCallback((path: string) => {
    void openRemote(path);
  }, [openRemote]);

  const handleProbePath = useCallback(async (path: string) => {
    if (!meta) return;
    setProbeBusy(true);
    setProbeError("");
    try {
      const geometry = await getProbeGeometry(meta.id, { positionsPath: path });
      setProbe(geometry);
      setProbePositionsPath(path);
      setProbeSelection(null);
      setProbeFilter(null);
      setProbeChooserOpen(false);
    } catch (caught) {
      setProbeError(caught instanceof Error ? caught.message : "Could not load probe geometry.");
    } finally {
      setProbeBusy(false);
    }
  }, [meta]);

  const handleHdPath = useCallback((path: string) => {
    setHdArtifact(null);
    setHdError("");
    setHdPath(path);
    setHdRefresh((value) => value + 1);
  }, []);

  useEffect(() => {
    if (hdChooserOpen && hdPath && !hdLoading && hdArtifact?.available && !hdError) {
      setHdChooserOpen(false);
    }
  }, [hdArtifact, hdChooserOpen, hdError, hdLoading, hdPath]);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (figureComposerOpen) return;
      const target = event.target as HTMLElement | null;
      const editing = target?.matches("input, select, textarea, [contenteditable='true']");
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "o") {
        event.preventDefault();
        openChooser();
        return;
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "e") {
        event.preventDefault();
        openFigureComposer();
        return;
      }
      if (editing || !meta || !viewState) return;
      if (event.key === "ArrowLeft" || event.key === "[") { event.preventDefault(); stepUnit(-1); }
      else if (event.key === "ArrowRight" || event.key === "]") { event.preventDefault(); stepUnit(1); }
      else if (event.key === "ArrowUp") { event.preventDefault(); stepTimeline(-1); }
      else if (event.key === "ArrowDown") { event.preventDefault(); stepTimeline(1); }
      else if (event.key === "<") stepResolution(-1);
      else if (event.key === ">") stepResolution(1);
      else if (event.key.toLowerCase() === "f") updateState({ flipY: !viewState.flipY });
      else if (event.key.toLowerCase() === "p") updateState({ palette: PALETTES[(PALETTES.indexOf(viewState.palette) + 1) % PALETTES.length] });
      else if (event.key === "Escape") {
        if (probeSelection) {
          setProbeSelection(null);
          setProbeFilter(null);
        } else {
          showFullTimeline();
        }
      }
      else if (event.key === "?") setHelpOpen(true);
      else if (/^[1-3]$/.test(event.key)) {
        const tab = VIEWER_TABS[Number(event.key) - 1].key;
        updateState({ selectedTab: tab });
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [figureComposerOpen, meta, openChooser, openFigureComposer, probeSelection, showFullTimeline, stepResolution, stepTimeline, stepUnit, updateState, viewState]);

  if (!meta || !viewState) {
    return (
      <SourceChooser
        overlay={false}
        busy={sourceBusy}
        error={error}
        initialPath="/mnt/senzailab"
        onClose={() => undefined}
        onRemote={handleRemoteChoice}
      />
    );
  }

  const localIndex = meta.unitPool.indexOf(viewState.clusterId);
  const axisStartMs = meta.timeBinEdges[0] * 1000;
  const axisEndMs = meta.timeBinEdges.at(-1)! * 1000;
  const bestDelay = bestTemporal?.delayMs ?? null;
  const visibleTabs = VIEWER_TABS;
  const noProbeMatches = probeFilter != null && navigationPool.length === 0;
  const unit = valueModeUnit(viewState.valueMode);
  const selectionRange = snapTimeRange(meta, viewState.rfStartMs, viewState.rfEndMs);
  const selectionBounds = timeBounds(meta, selectionRange);

  return (
    <div className={`app-shell ${probeCollapsed ? "probe-collapsed" : ""}`}>
      {probeCollapsed && <aside className="sidebar-rail"><button type="button" onClick={() => setProbeCollapsed(false)}>Show Probe & controls</button></aside>}
      {!probeCollapsed && <aside className="sidebar">
        <div className="sidebar-inner">
          <div className="sidebar-title-row">
            <h1 className="viewer-title">RF Map Viewer</h1>
            <button type="button" aria-label="Collapse Probe and controls sidebar" onClick={() => setProbeCollapsed(true)}>‹</button>
          </div>
          <p className="data-summary">
            <span title={meta.sourcePath}>{meta.sourcePath}</span>
            <span>{meta.shape[0]} units&nbsp;&nbsp;{meta.shape[1]} y x {meta.shape[2]} x&nbsp;&nbsp;{meta.shape[3]} bins</span>
            <span>Firing-rate metadata: {meta.presentationCounts ? "yes" : "no"}</span>
          </p>

          <hr />
          <section className="sidebar-block">
            <h2>Current JSON</h2>
            <div className="current-json-row">
              <select value={meta.sourcePath} title={meta.sourcePath} onChange={(event) => void openRemote(event.target.value)} aria-label="Current JSON">
                {(jsonChoices.length ? jsonChoices : mergeJsonChoices([], meta.sourcePath, recentPaths)).map((choice) => (
                  <option key={choice.path} value={choice.path}>{jsonChoiceLabel(choice, parentDirectory(meta.sourcePath))}</option>
                ))}
              </select>
              <button type="button" onClick={openChooser}>Open…</button>
            </div>
          </section>

          <hr />
          <section className="sidebar-block probe-sidebar-block">
            <div className="probe-sidebar-heading">
              <h2>Probe Layout</h2>
              <button type="button" onClick={() => setProbeChooserOpen(true)}>Choose positions.csv…</button>
            </div>
            {probe ? (
              <ProbeLayout
                geometry={probe}
                availableUnitIds={meta.unitPool}
                currentClusterId={viewState.clusterId}
                selection={probeSelection}
                onCluster={(clusterId) => {
                  if (probeFilter != null && !probeFilter.includes(clusterId)) return;
                  updateState({ clusterId, selectedCellXMidpoint: null, selectedCellYMidpoint: null });
                }}
                onSelection={(selection, units) => {
                  setProbeSelection(selection);
                  setProbeFilter(selection ? units : null);
                  if (selection && units.length && !units.includes(viewState.clusterId)) {
                    const target = nearestProbeUnitToRegionCenter(probe, selection, units) ?? units[0];
                    updateState({ clusterId: target, selectedCellXMidpoint: null, selectedCellYMidpoint: null });
                  }
                }}
              />
            ) : (
              <div className="probe-unavailable">
                {probeBusy || (meta.capabilities.probe && !probeError)
                  ? <><span className="spinner small" /> Loading probe geometry…</>
                  : <><strong>Probe layout unavailable</strong><span>{probeError || "Choose the matching remote positions.csv."}</span><button type="button" onClick={() => setProbeChooserOpen(true)}>Choose positions.csv…</button></>}
              </div>
            )}
            {probePositionsPath && <p className="companion-path" title={probePositionsPath}>{probePositionsPath}</p>}
          </section>

          <hr />
          <section className="sidebar-block">
            <h2>Unit</h2>
            <div className="unit-picker">
              <button type="button" aria-label="Previous unit" onClick={() => stepUnit(-1)} disabled={!navigationPool.length}>&lt;</button>
              <select
                value={navigationPool.includes(viewState.clusterId) ? viewState.clusterId : ""}
                onChange={(event) => updateState({ clusterId: Number(event.target.value), selectedCellXMidpoint: null, selectedCellYMidpoint: null })}
                aria-label="Current unit"
              >
                {!navigationPool.includes(viewState.clusterId) && <option value="">{noProbeMatches ? "No units in selected probe region" : `Cluster ${viewState.clusterId} excluded by probe region`}</option>}
                {navigationPool.map((clusterId) => {
                  const index = meta.unitPool.indexOf(clusterId);
                  return (
                    <option key={clusterId} value={clusterId}>
                      {`${String(index).padStart(3, "0")}  cluster ${clusterId}`}
                    </option>
                  );
                })}
              </select>
              <button type="button" aria-label="Next unit" onClick={() => stepUnit(1)} disabled={!navigationPool.length}>&gt;</button>
            </div>
            <div className="unit-stats">
              {unitStatus === "loading" && <span>Loading cluster…</span>}
              {noProbeMatches && <><strong>No units in region</strong><span>Clear the Probe filter to restore all units.</span></>}
              {unitStatus === "error" && <span>Unit data failed to load.</span>}
              {metrics && !noProbeMatches && <>
                <span>Total spikes: {metrics.totalSpikes.toFixed(0)}</span>
                <span>Best count cell: yIdx {metrics.bestY + 1}, xIdx {metrics.bestX + 1}</span>
                <span>Count-rate peak delay: {bestDelay == null ? "n/a" : `${formatNumber(bestDelay, 1)} ms`}</span>
              </>}
            </div>
          </section>

          <hr />
          <section className="sidebar-block display-block">
            <h2>Display</h2>
            <label className="check-row"><input type="checkbox" checked={viewState.flipY} onChange={(event) => updateState({ flipY: event.target.checked })} /><span>Invert Y (MATLAB flip)</span></label>
            <label className="display-row"><span>X bins</span><input type="number" min={1} max={meta.shape[2]} step={1} value={viewState.xBins} onChange={(event) => updateState({ xBins: clamp(Math.round(Number(event.target.value)), 1, meta.shape[2]) })} /></label>
            <label className="display-row"><span>Y bins</span><input type="number" min={1} max={meta.shape[1]} step={1} value={viewState.yBins} onChange={(event) => updateState({ yBins: clamp(Math.round(Number(event.target.value)), 1, meta.shape[1]) })} /></label>
            <label className="display-row"><span>Smooth</span><input type="number" min={0} max={3} step={1} value={viewState.smoothRadius} onChange={(event) => updateState({ smoothRadius: clamp(Math.round(Number(event.target.value)), 0, 3) })} /></label>
            <label className="display-row"><span>Palette</span><select value={viewState.palette} onChange={(event) => updateState({ palette: event.target.value as Palette })}>{PALETTES.map((palette) => <option key={palette}>{palette}</option>)}</select></label>
            <label className="display-row"><span>Polar radius</span><select value={viewState.polarRadius} onChange={(event) => updateState({ polarRadius: event.target.value as PolarRadius })}>{POLAR_RADIUS_MODES.map((mode) => <option key={mode}>{mode}</option>)}</select></label>
          </section>

          <hr />
          <section className="sidebar-block selected-block">
            <h2>Selected cell</h2>
            {!noProbeMatches && selectedCell && selectedDetails ? (
              <div className="selected-cell-text">
                <span>cluster {viewState.clusterId}</span>
                <span>yIdx {selectedCell[0] + 1}{selectedCell[1] !== selectedCell[0] ? `-${selectedCell[1] + 1}` : ""}; y {formatNumber(meta.yPositions[selectedCell[0]], 3)}{selectedCell[1] !== selectedCell[0] ? `..${formatNumber(meta.yPositions[selectedCell[1]], 3)}` : ""},</span>
                <span>xIdx {selectedCell[2] + 1}{selectedCell[3] !== selectedCell[2] ? `-${selectedCell[3] + 1}` : ""}; x {formatNumber(meta.xPositions[selectedCell[2]], 3)}{selectedCell[3] !== selectedCell[2] ? `..${formatNumber(meta.xPositions[selectedCell[3]], 3)}` : ""}</span>
                {(selectedCell[1] !== selectedCell[0] || selectedCell[3] !== selectedCell[2]) && <span>{viewState.valueMode === "Spike count" ? "mean" : "pooled"} over exposed source pixels</span>}
                <span>bin {formatResponse(selectedDetails.activeValue, viewState.valueMode)} {unit} ({formatNumber(timeBounds(meta, groups[activeGroup])[0])}–{formatNumber(timeBounds(meta, groups[activeGroup])[1])} ms)</span>
                <span>RF sum range {formatNumber(selectionBounds[0])}–{formatNumber(selectionBounds[1])} ms: {formatResponse(selectedDetails.rfValue, viewState.valueMode)} {unit}</span>
                <span>full window {formatResponse(selectedDetails.totalValue, viewState.valueMode)} {unit}</span>
                <span>peak {formatResponse(selectedDetails.peakValue, viewState.valueMode)} {unit}</span>
                <span>peak bin {selectedDetails.peakIndex < 0 ? "n/a" : `${selectedDetails.peakIndex + 1} (${formatNumber(timeBounds(meta, groups[selectedDetails.peakIndex])[0])}–${formatNumber(timeBounds(meta, groups[selectedDetails.peakIndex])[1])} ms)`}</span>
                <span>count-rate peak delay {selectedDetails.delay == null ? "n/a" : `${formatNumber(selectedDetails.delay, 1)} ms`}, count entropy {selectedDetails.entropy.toFixed(3)}</span>
              </div>
            ) : <span className="muted-copy">N/A for this session</span>}
            <div className="export-button-stack">
              <button className="export-button figure-button" type="button" onClick={openFigureComposer}>Compose figures…</button>
              <button className="export-button" type="button" onClick={openExportDialog} disabled={!counts || noProbeMatches}>Export displayed data…</button>
            </div>
          </section>

          <p className="shortcut-hint">←/→ unit&nbsp;&nbsp;&nbsp;↑/↓ timeline<br />⇧,/⇧. time resolution&nbsp;&nbsp;&nbsp;<button type="button" onClick={() => setHelpOpen(true)}>Keyboard shortcuts</button></p>
        </div>
      </aside>}

      <main className="workspace">
        <header className="workspace-heading">
          <h1>{noProbeMatches ? "No units in selected Probe region" : `Unit ${String(localIndex).padStart(3, "0")} / cluster ${viewState.clusterId}`}</h1>
          <p>
            {noProbeMatches
              ? "Clear or redraw the Probe region to continue unit navigation."
              : `x: ${formatNumber(meta.xPositions[0], 3)}..${formatNumber(meta.xPositions.at(-1)!, 3)}  y: ${formatNumber(meta.yPositions[0], 3)}..${formatNumber(meta.yPositions.at(-1)!, 3)}  time: ${formatNumber(axisStartMs)}..${formatNumber(axisEndMs)} ms  value: ${viewState.valueMode}`}
          </p>
        </header>

        <section className="plot-controls">
          <div className="plot-control-row top-row">
            <label><span>Value</span><select value={viewState.valueMode} onChange={(event) => updateState({ valueMode: event.target.value as ValueMode })}>{VALUE_MODES.map((mode) => <option key={mode} disabled={mode !== "Spike count" && !meta.presentationCounts}>{mode}</option>)}</select></label>
            <label><span>Time resolution (ms)</span><input type="number" min={baseBinMs(meta)} max={axisEndMs - axisStartMs} step={baseBinMs(meta)} value={formatNumber(viewState.timeResolutionMs, 6)} onChange={(event) => changeResolution(Number(event.target.value))} /></label>
          </div>
          <div className="plot-control-row bottom-row">
            <span className="range-title">RF sum range (ms)</span>
            <input type="number" min={axisStartMs} max={axisEndMs} step={baseBinMs(meta)} value={formatNumber(viewState.rfStartMs, 6)} onChange={(event) => updateState({ rfStartMs: Number(event.target.value) })} onBlur={normalizeRfRange} aria-label="RF range start" />
            <span>to</span>
            <input type="number" min={axisStartMs} max={axisEndMs} step={baseBinMs(meta)} value={formatNumber(viewState.rfEndMs, 6)} onChange={(event) => updateState({ rfEndMs: Number(event.target.value) })} onBlur={normalizeRfRange} aria-label="RF range end" />
            <label className="check-row"><input type="checkbox" checked={viewState.polarLayout} onChange={(event) => updateState({ polarLayout: event.target.checked })} /><span>Polar layout</span></label>
            <label className="check-row"><input type="checkbox" checked={viewState.rgbMode} disabled={viewState.selectedTab !== "delay"} onChange={(event) => updateState({ rgbMode: event.target.checked })} /><span>RGB composite</span></label>
            {viewState.selectedTab === "rf" && <label className="hd-layout-control"><span>RF + HD</span><select value={hdLayout} onChange={(event) => {
              const layout = event.target.value as HdLayout;
              setHdLayout(layout);
              window.localStorage.setItem(HD_LAYOUT_KEY, layout);
            }}><option value="side-by-side">Side by side</option><option value="stacked">Stacked</option></select></label>}
            <button className="reset-button" type="button" onClick={() => { const range = snapTimeRange(meta, 0, 200); const bounds = timeBounds(meta, range); updateState({ rfStartMs: bounds[0], rfEndMs: bounds[1] }); }}>Reset 0–200</button>
          </div>
        </section>

        <div className="notebook">
          <nav className="view-tabs" aria-label="Dataset views">
            {visibleTabs.map((tab) => (
              <button key={tab.key} type="button" className={viewState.selectedTab === tab.key ? "active" : ""} onClick={() => updateState({ selectedTab: tab.key })}>
                {tab.label}
              </button>
            ))}
          </nav>
          <section className="view-surface">
            <div className={`rf-hd-layout hd-layout-${hdLayout} ${hdCollapsed ? "hd-is-collapsed" : ""}`} hidden={viewState.selectedTab !== "rf"}>
              <div className="rf-primary-pane">
                {noProbeMatches && <div className="view-empty"><strong>No units in region</strong><span>Clear the Probe filter or select another region.</span></div>}
                {!noProbeMatches && unitStatus === "loading" && <div className="view-empty"><span className="spinner" /> Loading cluster {viewState.clusterId}…</div>}
                {!noProbeMatches && unitStatus === "error" && <div className="view-empty error-state"><strong>Unit data could not be loaded</strong><span>{error}</span></div>}
                {!noProbeMatches && counts && selectedCell && <SpatialPlot kind="rf" meta={meta} counts={counts} state={viewState} unitIndex={localIndex} selectedCell={selectedCell} onSelectCell={selectCell} />}
              </div>
              <HdPanel
                artifact={hdArtifact}
                clusterId={viewState.clusterId}
                loading={hdLoading}
                error={hdError}
                rfPolarLayout={viewState.polarLayout}
                blocked={noProbeMatches}
                collapsed={hdCollapsed}
                settings={hdSettings}
                onSettingsChange={setHdSettings}
                onToggleCollapsed={() => setHdCollapsed((value) => !value)}
                onChoosePath={() => setHdChooserOpen(true)}
              />
            </div>
            {viewState.selectedTab === "delay" && (
              noProbeMatches
                ? <div className="view-empty"><strong>No units in region</strong><span>Clear the Probe filter or select another region.</span></div>
                : unitStatus === "loading"
                  ? <div className="view-empty"><span className="spinner" /> Loading cluster {viewState.clusterId}…</div>
                  : unitStatus === "error"
                    ? <div className="view-empty error-state"><strong>Unit data could not be loaded</strong><span>{error}</span></div>
                    : counts && selectedCell && <SpatialPlot kind="delay" meta={meta} counts={counts} state={viewState} unitIndex={localIndex} selectedCell={selectedCell} onSelectCell={selectCell} />
            )}
            {viewState.selectedTab === "timeline" && (
              noProbeMatches
                ? <div className="view-empty"><strong>No units in region</strong><span>Clear the Probe filter or select another region.</span></div>
                : unitStatus === "loading"
                  ? <div className="view-empty"><span className="spinner" /> Loading cluster {viewState.clusterId}…</div>
                  : unitStatus === "error"
                    ? <div className="view-empty error-state"><strong>Unit data could not be loaded</strong><span>{error}</span></div>
                    : counts && selectedCell && <TimelinePlot meta={meta} counts={counts} state={viewState} unitIndex={localIndex} selectedCell={selectedCell} onSelectCell={selectCell} onSelectTime={selectTimelineBin} onScrollFraction={updateTimelineScroll} />
            )}
          </section>
        </div>
      </main>

      {sourceOpen && (
        <SourceChooser
          overlay
          busy={sourceBusy}
          error={error}
          initialPath={parentDirectory(meta.sourcePath)}
          onClose={() => setSourceOpen(false)}
          onRemote={handleRemoteChoice}
        />
      )}
      {probeChooserOpen && (
        <SourceChooser
          overlay
          kind="positions-csv"
          title="Attach Probe positions.csv"
          busyLabel="Loading Probe positions…"
          busy={probeBusy}
          error={probeError}
          initialPath={parentDirectory(probePositionsPath ?? meta.sourcePath)}
          onClose={() => { if (!probeBusy) setProbeChooserOpen(false); }}
          onRemote={(path) => void handleProbePath(path)}
        />
      )}
      {hdChooserOpen && (
        <SourceChooser
          overlay
          kind="tuning-json"
          title="Attach HD tuning_curves.json"
          busyLabel="Loading HD tuning data…"
          busy={hdLoading}
          error={hdError}
          initialPath={parentDirectory(hdPath ?? meta.sourcePath)}
          onClose={() => { if (!hdLoading) setHdChooserOpen(false); }}
          onRemote={handleHdPath}
        />
      )}
      {exportDialog && (
        <SaveArtifactDialog
          title="Export Displayed"
          value={exportDialog.path}
          extension=".csv"
          busy={exportDialog.busy}
          error={exportDialog.error}
          overwritePending={exportDialog.overwritePending}
          onChange={(path) => setExportDialog((current) => current ? {
            ...current,
            path,
            error: "",
            overwritePending: false,
          } : current)}
          onClose={() => { if (!exportDialog.busy) setExportDialog(null); }}
          onSubmit={(overwrite) => void exportCsv(overwrite)}
        />
      )}
      {figureComposerOpen && (
        <FigureExportComposer
          meta={meta}
          viewState={viewState}
          selectedCell={selectedCell}
          hdSettings={hdSettings}
          probeFilteredUnitIds={probeFilter}
          availableCapabilities={{
            hd: Boolean(hdArtifact?.available),
            probe: probe != null,
          }}
          hdPath={hdPath}
          probePositionsPath={probePositionsPath}
          onClose={() => setFigureComposerOpen(false)}
        />
      )}
      {helpOpen && (
        <div className="modal-backdrop" onMouseDown={(event) => { if (event.currentTarget === event.target) setHelpOpen(false); }}>
          <div className="info-dialog" role="dialog" aria-modal="true" aria-label="Keyboard Shortcuts">
            <header><strong>Keyboard Shortcuts</strong><button type="button" aria-label="Close" onClick={() => setHelpOpen(false)}>×</button></header>
            <pre>← / → or [ / ]   Previous / next unit{"\n"}↑ / ↓   Previous / next timeline bin{"\n"}Shift+, / Shift+.   Time resolution −/+ 1 ms{"\n"}1–3   Switch RF / Delay-RGB / Timeline{"\n"}F   Invert Y{"\n"}P   Cycle palette{"\n"}Esc   Clear Probe region; otherwise show full Timeline{"\n"}Command-O   Open JSON in this viewer{"\n"}Command-E   Open Figure Export Composer</pre>
            <footer><button type="button" onClick={() => setHelpOpen(false)}>OK</button></footer>
          </div>
        </div>
      )}
      {messageDialog && (
        <div className="modal-backdrop" onMouseDown={(event) => { if (event.currentTarget === event.target) setMessageDialog(null); }}>
          <div className="info-dialog message-dialog" role="alertdialog" aria-modal="true" aria-label={messageDialog.title}>
            <header><strong>{messageDialog.title}</strong><button type="button" aria-label="Close" onClick={() => setMessageDialog(null)}>×</button></header>
            <p>{messageDialog.text}</p>
            <footer><button type="button" onClick={() => setMessageDialog(null)}>OK</button></footer>
          </div>
        </div>
      )}
    </div>
  );
}
