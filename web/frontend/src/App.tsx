import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  getCacheProgress,
  retryCache,
  closeDataset,
  exportDisplayedCsv,
  getHdDataset,
  getProbeGeometry,
  getUnitFilter,
  getUnitCounts,
  getWaveformArtifact,
  listRemoteFiles,
  openRemoteDataset,
} from "./api";
import RfWindowInput from "./components/RfWindowInput";
import HdPanel from "./components/HdPanel";
import FigureExportComposer from "./components/FigureExportComposer";
import { SpatialPlot, TimelinePlot } from "./components/Plots";
import ProbeLayout, { type ProbeSelection } from "./components/ProbeLayout";
import RemoteBrowser from "./components/RemoteBrowser";
import SaveArtifactDialog from "./components/SaveArtifactDialog";
import WaveformPanel from "./components/WaveformPanel";
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
  prepareRfResponse,
  rfWindowLabel,
  snapTimeRange,
  timeBounds,
  timeGroupForMs,
  timeGroups,
  unitMetrics,
  valueModeUnit,
} from "./math";
import { DEFAULT_HD_DISPLAY_BINS, DEFAULT_HD_SMOOTH_SIGMA } from "./hdMath";
import { exportShortcutAction, steppedTimeResolutionMs } from "./appShortcuts";
import { nearestProbeUnitToRegionCenter, probeUnitsInRegion } from "./probeSelection";
import { resolutionChangePatch, timelineSelectionPatch } from "./viewStateMath";
import { VIEWER_TABS } from "./viewTabs";
import { usePairedWindows } from "./pairedWindows";
import { RF_TIMING_KEY, readRfTiming, resetRfTiming, timingPatch, timingFromState, toggleRfMode } from "./rfTiming";
import { LatestRequest, LatestSerialRead, UnitCountsCache } from "./requestLifecycle";
import {
  navigationUnitIds,
  orderedQualityVisibleUnitIds,
  reconciledClusterId,
  userEnteredZeroSpikeSpatialBinThreshold,
} from "./unitFilter";
import type {
  CellRef,
  CacheProgress,
  DatasetMeta,
  FsEntry,
  HdDatasetArtifact,
  HdViewSettings,
  Palette,
  PolarRadius,
  ProbeGeometry,
  ValueMode,
  ViewState,
  WaveformArtifact,
  WaveformChannelMode,
} from "./types";
import { DEFAULT_VALUE_MODE, PALETTES, POLAR_RADIUS_MODES, VALUE_MODES } from "./types";

const RECENT_JSON_KEY = "rfmapping-recent-json-v1";
const HD_LAYOUT_KEY = "rfmapping-hd-layout-v1";
const COMPANION_PREFERENCES_KEY = "rfmapping-companion-preferences-v1";
const UNIT_FILTER_PREFERENCES_KEY = "rfmapping-zero-bin-unit-filter-v1";

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

interface CompanionPreferences {
  tuningSession: number;
  showWaveform: boolean;
  waveformChannelMode: WaveformChannelMode;
}

function loadCompanionPreferences(): CompanionPreferences {
  const fallback: CompanionPreferences = {
    tuningSession: 1,
    showWaveform: true,
    waveformChannelMode: "same_x_column",
  };
  try {
    const parsed = JSON.parse(window.localStorage.getItem(COMPANION_PREFERENCES_KEY) ?? "null");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return fallback;
    const source = parsed as Record<string, unknown>;
    const rawSession = Number(source.tuningSession);
    return {
      tuningSession: Number.isInteger(rawSession) && rawSession >= 1 ? rawSession : 1,
      showWaveform: typeof source.showWaveform === "boolean" ? source.showWaveform : true,
      waveformChannelMode: source.waveformChannelMode === "same_shank"
        ? "same_shank"
        : "same_x_column",
    };
  } catch {
    return fallback;
  }
}

interface UnitFilterPreferences {
  enabled: boolean;
  zeroSpikeSpatialBinThreshold: number;
}

function loadUnitFilterPreferences(): UnitFilterPreferences {
  const fallback = { enabled: true, zeroSpikeSpatialBinThreshold: 1 };
  try {
    const parsed = JSON.parse(window.localStorage.getItem(UNIT_FILTER_PREFERENCES_KEY) ?? "null");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return fallback;
    const source = parsed as Record<string, unknown>;
    const threshold = Number(source.zeroSpikeSpatialBinThreshold);
    return {
      enabled: typeof source.enabled === "boolean" ? source.enabled : true,
      zeroSpikeSpatialBinThreshold: Number.isInteger(threshold) && threshold >= 1
        ? threshold
        : 1,
    };
  } catch {
    return fallback;
  }
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
    valueMode: DEFAULT_VALUE_MODE,
    activeTimeCenterMs: (activeBounds[0] + activeBounds[1]) / 2,
    timelineStartMs: meta.timeBinEdges[0] * 1000,
    timelineEndMs: meta.timeBinEdges.at(-1)! * 1000,
    timelineAnchorMs: null,
    rfStartMs: rfBounds[0],
    rfEndMs: rfBounds[1],
    ...timingPatch(meta, readRfTiming(window.localStorage.getItem(RF_TIMING_KEY))),
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
  return parent.startsWith("/data/rfmapping") ? parent : "/data/rfmapping";
}

function SourceChooser({
  overlay,
  busy,
  error,
  initialPath,
  kind = "rf-json",
  title = "Open RF mapping file (.rfmap or .json)",
  busyLabel = "Opening RF mapping file…",
  onCancel,
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
  onCancel?: () => void;
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
      {busy && <div className="dialog-status"><span className="spinner small" /> {busyLabel}{onCancel && <button type="button" onClick={onCancel}>Cancel</button>}</div>}
    </div>
  );
  return overlay ? <div className="modal-backdrop">{content}</div> : <main className="landing">{content}</main>;
}

export default function App() {
  const [meta, setMeta] = useState<DatasetMeta | null>(null);
  const [viewState, setViewState] = useState<ViewState | null>(null);
  const [counts, setCounts] = useState<Float64Array | null>(null);
  const [cacheProgress, setCacheProgress] = useState<CacheProgress | null>(null);
  const [displayOptions, setDisplayOptions] = useState(true);
  const waveformReads = useRef(new LatestSerialRead());
  const unitFilterSignature = useRef("");
  const countsCache = useRef(new Map<string, UnitCountsCache>());
  const sourceRequest = useRef(new LatestRequest());
  const probeRequest = useRef(new LatestRequest());
  const lastLocalCluster = useRef<number | null>(null);
  const [unitStatus, setUnitStatus] = useState<"loading" | "ready" | "unavailable" | "error">("loading");
  const [unitFilterEnabled, setUnitFilterEnabled] = useState(
    () => loadUnitFilterPreferences().enabled,
  );
  const [zeroSpikeSpatialBinThreshold, setZeroSpikeSpatialBinThreshold] = useState(
    () => loadUnitFilterPreferences().zeroSpikeSpatialBinThreshold,
  );
  const [qualityVisibleUnitIds, setQualityVisibleUnitIds] = useState<number[]>([]);
  const [unitFilterStatus, setUnitFilterStatus] = useState<"loading" | "ready" | "error">("loading");
  const [unitFilterError, setUnitFilterError] = useState("");
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
  const [hdArtifact, setHdArtifact] = useState<HdDatasetArtifact | null>(null);
  const [hdError, setHdError] = useState("");
  const [hdLoading, setHdLoading] = useState(false);
  const [hdPath, setHdPath] = useState<string | null>(null);
  const [hdChooserOpen, setHdChooserOpen] = useState(false);
  const [hdCollapsed, setHdCollapsed] = useState(false);
  const [hdLayout, setHdLayout] = useState<HdLayout>(loadHdLayout);
  const [hdSettings, setHdSettings] = useState<HdViewSettings>(INITIAL_HD_VIEW_SETTINGS);
  const [hdRefresh, setHdRefresh] = useState(0);
  const [tuningSession, setTuningSession] = useState(() => loadCompanionPreferences().tuningSession);
  const [showWaveform, setShowWaveform] = useState(() => loadCompanionPreferences().showWaveform);
  const [waveformChannelMode, setWaveformChannelMode] = useState<WaveformChannelMode>(
    () => loadCompanionPreferences().waveformChannelMode,
  );
  const [waveformArtifact, setWaveformArtifact] = useState<WaveformArtifact | null>(null);
  const [waveformLoading, setWaveformLoading] = useState(false);
  const [waveformError, setWaveformError] = useState("");

  const updateState = useCallback((patch: Partial<ViewState> | ((current: ViewState) => Partial<ViewState>)) => {
    setViewState((current) => current ? { ...current, ...(typeof patch === "function" ? patch(current) : patch) } : current);
  }, []);

  const applyPairedPatch = useCallback((patch: Partial<ViewState>) => {
    if (!meta) return;
    updateState((current) => {
      const next = { ...current, ...patch };
      const a = timeBounds(meta, snapTimeRange(meta, next.rfStartMs, next.rfEndMs));
      const b = timeBounds(meta, snapTimeRange(meta, next.rfBStartMs ?? 0, next.rfBEndMs ?? 80));
      return { ...patch, rfStartMs: a[0], rfEndMs: a[1], rfBStartMs: b[0], rfBEndMs: b[1],
        xBins: clamp(next.xBins, 1, meta.shape[2]), yBins: clamp(next.yBins, 1, meta.shape[1]) };
    });
  }, [meta, updateState]);
  const paired = usePairedWindows(meta, viewState, qualityVisibleUnitIds, applyPairedPatch);

  const commitDataset = useCallback((next: DatasetMeta) => {
    probeRequest.current.cancel();
    setProbeBusy(false);
    countsCache.current.clear();
    setCounts(null);
    setMeta(next);
    setCacheProgress(next.cacheProgress ?? null);
    setJsonChoices([{ path: next.sourcePath, mtime: null }]);
    lastLocalCluster.current = next.unitPool[0];
    setViewState((current) => {
      const initial = initialViewState(next);
      if (!current) return initial;
      return {
        ...initial,
        valueMode: VALUE_MODES.includes(current.valueMode) ? current.valueMode : DEFAULT_VALUE_MODE,
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
    setQualityVisibleUnitIds([]);
    setUnitFilterStatus("loading");
    setUnitFilterError("");
    setSourceOpen(false);
    setProbe(null);
    setProbeError("");
    setProbePositionsPath(null);
    setProbeChooserOpen(false);
    setProbeSelection(null);
    setHdArtifact(null);
    setHdError("");
    setHdPath(null);
    setHdChooserOpen(false);
    setWaveformArtifact(null);
    setWaveformError("");
    setWaveformLoading(false);
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
    const signal = sourceRequest.current.begin();
    setSourceBusy(true);
    setError("");
    try {
      const next = await openRemoteDataset(path, signal);
      if (!sourceRequest.current.isCurrent(signal)) { void closeDataset(next.id).catch(() => undefined); return; }
      commitDataset(next);
      window.history.replaceState(null, "", urlForJsonSource(window.location.href, next.sourcePath));
    } catch (caught) {
      if (!sourceRequest.current.isCurrent(signal)) return;
      setError(caught instanceof Error ? caught.message : "Could not open RF mapping file.");
    } finally {
      if (sourceRequest.current.isCurrent(signal)) setSourceBusy(false);
    }
  }, [commitDataset]);

  const cancelSourceLoad = useCallback(() => {
    sourceRequest.current.cancel();
    setSourceBusy(false);
  }, []);

  useEffect(() => () => {
    sourceRequest.current.cancel();
    probeRequest.current.cancel();
  }, []);

  useEffect(() => {
    if (!meta) return;
    const close = () => { void closeDataset(meta.id).catch(() => undefined); };
    window.addEventListener("pagehide", close);
    return () => { window.removeEventListener("pagehide", close); close(); };
  }, [meta?.id]);

  useEffect(() => {
    if (!meta || !cacheProgress?.indexed || cacheProgress.complete) return;
    const controller = new AbortController();
    let timer: number;
    const poll = async () => {
      try {
        const progress = await getCacheProgress(meta.id, controller.signal);
        if (controller.signal.aborted) return;
        setCacheProgress(progress);
        if (!progress.complete) timer = window.setTimeout(poll, 500);
      } catch (caught) {
        if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Could not read cache progress.");
      }
    };
    timer = window.setTimeout(poll, 200);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [meta, cacheProgress?.indexed, cacheProgress?.complete]);

  useEffect(() => {
    document.title = meta ? `${meta.name} — RF Map Viewer` : "RF Map Viewer";
  }, [meta]);

  useEffect(() => {
    window.localStorage.setItem(COMPANION_PREFERENCES_KEY, JSON.stringify({
      tuningSession,
      showWaveform,
      waveformChannelMode,
    } satisfies CompanionPreferences));
  }, [showWaveform, tuningSession, waveformChannelMode]);

  useEffect(() => {
    window.localStorage.setItem(UNIT_FILTER_PREFERENCES_KEY, JSON.stringify({
      enabled: unitFilterEnabled,
      zeroSpikeSpatialBinThreshold,
    } satisfies UnitFilterPreferences));
  }, [unitFilterEnabled, zeroSpikeSpatialBinThreshold]);

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
    const initialPath = new URL(window.location.href).searchParams.get("json");
    if (initialPath) void openRemote(initialPath);
  }, [openRemote]);

  useEffect(() => {
    if (!meta || !viewState) return;
    const threshold = clamp(
      Math.round(zeroSpikeSpatialBinThreshold),
      1,
      100_000,
    );
    if (threshold !== zeroSpikeSpatialBinThreshold) {
      setZeroSpikeSpatialBinThreshold(threshold);
      return;
    }
    const signature = [meta.id, unitFilterEnabled, viewState.rfStartMs, viewState.rfEndMs, threshold].join(":");
    if (signature !== unitFilterSignature.current) {
      unitFilterSignature.current = signature;
      setQualityVisibleUnitIds([]);
      setUnitFilterStatus("loading");
    }
    if (!unitFilterEnabled) {
      setQualityVisibleUnitIds([...meta.unitPool]);
      setUnitFilterStatus("ready");
      setUnitFilterError("");
      return;
    }
    if (!Number.isFinite(viewState.rfStartMs) || !Number.isFinite(viewState.rfEndMs)) {
      setQualityVisibleUnitIds([]);
      setUnitFilterStatus("error");
      setUnitFilterError("RF filter range must contain finite times.");
      return;
    }
    const controller = new AbortController();
    setUnitFilterError("");
    const timer = window.setTimeout(() => {
      getUnitFilter(
        meta.id,
        viewState.rfStartMs,
        viewState.rfEndMs,
        threshold,
        controller.signal,
      )
        .then((result) => {
          if (controller.signal.aborted) return;
          setQualityVisibleUnitIds(orderedQualityVisibleUnitIds(
            meta.unitPool,
            result.visibleUnitIds,
            true,
          ));
          setUnitFilterStatus("ready");
        })
        .catch((caught) => {
          if (controller.signal.aborted) return;
          setQualityVisibleUnitIds([]);
          setUnitFilterStatus("error");
          setUnitFilterError(
            caught instanceof Error ? caught.message : "Could not filter RF units.",
          );
        });
    }, 120);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [
    meta,
    unitFilterEnabled,
    viewState?.rfEndMs,
    viewState?.rfStartMs,
    zeroSpikeSpatialBinThreshold,
    cacheProgress?.cachedUnits,
  ]);

  useEffect(() => {
    if (!meta || !viewState) return;
    const controller = new AbortController();
    const datasetId = meta.id;
    let datasetCache = countsCache.current.get(datasetId);
    if (!datasetCache) {
      datasetCache = new UnitCountsCache();
      countsCache.current.set(datasetId, datasetCache);
    }
    const localIndex = meta.unitPool.indexOf(viewState.clusterId);
    if (
      localIndex < 0
      || unitFilterStatus !== "ready"
      || !qualityVisibleUnitIds.includes(viewState.clusterId)
    ) {
      setCounts(null);
      setUnitStatus(unitFilterStatus === "loading" ? "loading" : "unavailable");
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
    const qualityIndex = qualityVisibleUnitIds.indexOf(viewState.clusterId);
    const neighbors = [qualityIndex - 1, qualityIndex + 1]
      .filter((index) => 0 <= index && index < qualityVisibleUnitIds.length)
      .map((index) => qualityVisibleUnitIds[index]);
    if (!cacheProgress?.indexed || cacheProgress.complete) neighbors.forEach((clusterId) => {
      if (!datasetCache.has(clusterId)) {
        void getUnitCounts(meta, clusterId, controller.signal).then((values) => {
          if (!controller.signal.aborted && countsCache.current.get(datasetId) === datasetCache) {
            datasetCache.set(clusterId, values);
          }
        }).catch(() => undefined);
      }
    });
    return () => controller.abort();
  }, [meta, qualityVisibleUnitIds, unitFilterStatus, viewState?.clusterId, cacheProgress?.indexed, cacheProgress?.complete]);

  useEffect(() => {
    if (!meta?.capabilities.probe || probePositionsPath) return;
    const signal = probeRequest.current.begin();
    setProbeError("");
    getProbeGeometry(meta.id, {}, signal)
      .then((geometry) => { if (probeRequest.current.isCurrent(signal)) setProbe(geometry); })
      .catch((caught) => {
        if (probeRequest.current.isCurrent(signal)) setProbeError(caught instanceof Error ? caught.message : "Could not load probe layout.");
      });
    return () => {
      if (probeRequest.current.isCurrent(signal)) probeRequest.current.cancel();
    };
  }, [meta, probePositionsPath]);

  useEffect(() => {
    if (!meta) {
      setHdArtifact(null);
      setHdLoading(false);
      return;
    }
    const controller = new AbortController();
    setHdLoading(true);
    setHdError("");
    getHdDataset(meta.id, hdPath ?? undefined, tuningSession, controller.signal)
      .then((artifact) => { if (!controller.signal.aborted) setHdArtifact(artifact); })
      .catch((caught) => {
        if (!controller.signal.aborted) {
          setHdArtifact(null);
          setHdError(caught instanceof Error ? caught.message : "Could not load HD tuning data.");
        }
      })
      .finally(() => { if (!controller.signal.aborted) setHdLoading(false); });
    return () => controller.abort();
  }, [hdPath, hdRefresh, meta, tuningSession]);

  useEffect(() => {
    waveformReads.current.cancel();
    if (!meta || !viewState || !showWaveform) {
      setWaveformArtifact(null);
      setWaveformError("");
      setWaveformLoading(false);
      return;
    }
    if (!meta.capabilities.waveform) {
      setWaveformArtifact({
        available: false,
        detail: "No companion data/waveform/Probe* artifact was found for this RF dataset.",
      });
      setWaveformError("");
      setWaveformLoading(false);
      return;
    }
    setWaveformArtifact(null);
    setWaveformError("");
    setWaveformLoading(true);
    waveformReads.current.submit(
      () => getWaveformArtifact(meta.id, viewState.clusterId, waveformChannelMode),
      setWaveformArtifact,
      (caught) => setWaveformError(caught instanceof Error ? caught.message : "Could not load the local average waveform."),
      () => setWaveformLoading(false),
    );
    return () => waveformReads.current.cancel();
  }, [meta, showWaveform, viewState?.clusterId, waveformChannelMode]);

  const probeFilter = useMemo(() => {
    if (probe == null || probeSelection == null) return null;
    return probeUnitsInRegion(probe, probeSelection, qualityVisibleUnitIds);
  }, [probe, probeSelection, qualityVisibleUnitIds]);

  const navigationPool = useMemo(() => {
    return navigationUnitIds(paired.unitIDs ?? qualityVisibleUnitIds, probeFilter);
  }, [probeFilter, qualityVisibleUnitIds, paired.unitIDs]);

  useEffect(() => {
    if (!viewState || unitFilterStatus !== "ready") return;
    const nextClusterId = reconciledClusterId(viewState.clusterId, navigationPool);
    if (nextClusterId == null) {
      setCounts(null);
      setUnitStatus("unavailable");
      return;
    }
    lastLocalCluster.current = nextClusterId;
    if (nextClusterId !== viewState.clusterId) {
      updateState({
        clusterId: nextClusterId,
        selectedCellXMidpoint: null,
        selectedCellYMidpoint: null,
      });
    }
  }, [navigationPool, unitFilterStatus, updateState, viewState]);

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

  const stepResolution = useCallback((direction: -1 | 1) => {
    if (!meta || !viewState) return;
    changeResolution(steppedTimeResolutionMs(meta, viewState.timeResolutionMs, direction));
  }, [changeResolution, meta, viewState]);

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

  const selectedSeries = useMemo(() => {
    if (!meta || !viewState || !counts || !selectedCell || !groups.length) return null;
    return {
      values: groupResponseValues(counts, meta, selectedCell, groups, viewState.valueMode),
      temporal: groupTemporalMetrics(counts, meta, selectedCell, groups),
      totalValue: groupResponseValue(counts, meta, selectedCell, [0, meta.shape[3] - 1], viewState.valueMode),
    };
  }, [counts, groups, meta, selectedCell, viewState?.valueMode]);

  const selectedRfValue = useMemo(() => {
    if (!meta || !viewState || !counts || !selectedCell) return null;
    const prepared = prepareRfResponse(counts, meta, viewState);
    const y = prepared.yGroups.findIndex(([start, end]) => start <= selectedCell[0] && end >= selectedCell[1]);
    const x = prepared.xGroups.findIndex(([start, end]) => start <= selectedCell[2] && end >= selectedCell[3]);
    return prepared.matrix[y]?.[x] ?? null;
  }, [counts, meta, selectedCell, viewState?.rfStartMs, viewState?.rfEndMs, viewState?.rfWindowMode, viewState?.rfBStartMs, viewState?.rfBEndMs, viewState?.valueMode, viewState?.xBins, viewState?.yBins, viewState?.flipY, viewState?.smoothRadius]);

  const selectedDetails = useMemo(() => {
    if (!selectedSeries) return null;
    const { values, temporal, totalValue } = selectedSeries;
    const peakIndex = temporal.peakGroupIndex ?? -1;
    return {
      activeValue: values[activeGroup] ?? null,
      rfValue: selectedRfValue,
      totalValue,
      peakValue: peakIndex < 0 ? null : values[peakIndex] ?? null,
      peakIndex,
      delay: temporal.delayMs,
      entropy: temporal.entropy,
    };
  }, [activeGroup, selectedRfValue, selectedSeries]);

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
    if (cacheProgress && !cacheProgress.complete) {
      setMessageDialog({ title: "RF cache is loading", text: "Figure Composer is available after all units have been cached. Retry any cache error first." });
      return;
    }
    if (unitFilterStatus !== "ready" || !qualityVisibleUnitIds.length) {
      setMessageDialog({
        title: "No visible units",
        text: unitFilterStatus === "error"
          ? unitFilterError
          : unitFilterStatus === "loading"
            ? "The native RF-bin unit filter is still updating."
            : "No units pass the native RF-bin filter for the current RF window.",
      });
      return;
    }
    setFigureComposerOpen(true);
  }, [meta, qualityVisibleUnitIds, unitFilterError, unitFilterStatus, viewState, cacheProgress]);

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
        rfWindowMode: viewState.rfWindowMode ?? "sum",
        rfBStartMs: viewState.rfBStartMs ?? 0,
        rfBEndMs: viewState.rfBEndMs ?? 80,
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

  const openChooser = useCallback(() => {
    setError("");
    setSourceOpen(true);
  }, []);

  const handleRemoteChoice = useCallback((path: string) => {
    void openRemote(path);
  }, [openRemote]);

  const handleProbePath = useCallback(async (path: string) => {
    if (!meta) return;
    const signal = probeRequest.current.begin();
    setProbeBusy(true);
    setProbeError("");
    try {
      const geometry = await getProbeGeometry(meta.id, { positionsPath: path }, signal);
      if (!probeRequest.current.isCurrent(signal)) return;
      setProbe(geometry);
      setProbePositionsPath(path);
      setProbeSelection(null);
      setProbeChooserOpen(false);
    } catch (caught) {
      if (!probeRequest.current.isCurrent(signal)) return;
      setProbeError(caught instanceof Error ? caught.message : "Could not load probe geometry.");
    } finally {
      if (probeRequest.current.isCurrent(signal)) setProbeBusy(false);
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
      const editing = target?.closest("input, textarea, [contenteditable='true']");
      const picker = target?.tagName === "SELECT";
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "o") {
        event.preventDefault();
        openChooser();
        return;
      }
      const exportAction = exportShortcutAction(event);
      if (exportAction) {
        event.preventDefault();
        if (exportAction === "displayed-csv") openExportDialog();
        else openFigureComposer();
        return;
      }
      if ((event.metaKey || event.ctrlKey) && event.shiftKey && (event.key === "." || event.key === ">") && !event.altKey) {
        if (editing) return;
        event.preventDefault(); setUnitFilterEnabled((enabled) => !enabled); return;
      }
      if (editing || event.metaKey || event.ctrlKey || event.altKey || !meta || !viewState) return;
      if (picker && (target?.matches(":open") || event.key.toLowerCase() !== "p")) return;
      if (event.key === "-") { event.preventDefault(); updateState(toggleRfMode(meta, viewState)); return; }
      if (event.key.toLowerCase() === "d") { event.preventDefault(); setDisplayOptions((value) => !value); return; }
      if (event.key === "ArrowLeft" || event.key === "[") { event.preventDefault(); stepUnit(-1); }
      else if (event.key === "ArrowRight" || event.key === "]") { event.preventDefault(); stepUnit(1); }
      else if (event.key === "ArrowUp") { event.preventDefault(); stepTimeline(-1); }
      else if (event.key === "ArrowDown") { event.preventDefault(); stepTimeline(1); }
      else if (event.shiftKey && (event.key === "<" || event.key === ",")) { event.preventDefault(); stepResolution(1); }
      else if (event.shiftKey && (event.key === ">" || event.key === ".")) { event.preventDefault(); stepResolution(-1); }
      else if (event.key.toLowerCase() === "f") updateState({ flipY: !viewState.flipY });
      else if (event.key.toLowerCase() === "p" && event.shiftKey) {
        event.preventDefault();
        updateState({ palette: PALETTES[(PALETTES.indexOf(viewState.palette) + 1) % PALETTES.length] });
      }
      else if (event.key.toLowerCase() === "p") { event.preventDefault(); updateState({ polarLayout: !viewState.polarLayout }); }
      else if (event.key === "Escape") {
        if (probeSelection) {
          setProbeSelection(null);
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
  }, [figureComposerOpen, meta, openChooser, openExportDialog, openFigureComposer, probeSelection, showFullTimeline, stepResolution, stepTimeline, stepUnit, updateState, viewState]);

  if (!meta || !viewState) {
    return (
      <SourceChooser
        overlay={false}
        busy={sourceBusy}
        error={error}
        initialPath="/data/rfmapping"
        onCancel={cancelSourceLoad}
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
  const noQualityMatches = unitFilterStatus === "ready"
    && unitFilterEnabled
    && qualityVisibleUnitIds.length === 0;
  const noNavigationUnits = unitFilterStatus !== "ready" || navigationPool.length === 0;
  const emptyUnitTitle = unitFilterStatus === "loading"
    ? "Updating native RF-bin unit filter"
    : unitFilterStatus === "error"
      ? "Unit filter unavailable"
      : noQualityMatches
        ? "No units pass the current RF window"
        : "No units in selected Probe region";
  const emptyUnitDetail = unitFilterStatus === "loading"
    ? "Counting zero-spike native spatial bins for the current RF selection window."
    : unitFilterStatus === "error"
      ? unitFilterError
      : noQualityMatches
        ? `Increase the zero-bin threshold, change the RF sum range, or disable the filter. Visible requires zero-bin count < ${zeroSpikeSpatialBinThreshold}.`
        : "Clear or redraw the Probe region to continue unit navigation.";
  const unavailableUnit = !noNavigationUnits && (localIndex < 0 || (unitFilterEnabled && !qualityVisibleUnitIds.includes(viewState.clusterId)));
  const unitIsLoading = !unavailableUnit && (unitStatus === "loading" || unitStatus === "unavailable");
  const unavailableView = <div className="view-empty"><strong>Cluster {viewState.clusterId}: N/A in this dataset</strong><span>{localIndex < 0 ? "This recorded unit is present in a paired viewer, but absent from this RF file." : "This unit is hidden by this dataset's native RF-bin filter."}</span></div>;
  const unit = valueModeUnit(viewState.valueMode);

  return (
    <div className={`app-shell ${probeCollapsed ? "probe-collapsed" : ""}`}>
      {cacheProgress?.indexed && !cacheProgress.complete && <div className="cache-progress" role="status">
        <progress value={cacheProgress.cachedUnits} max={cacheProgress.totalUnits} />
        <span>Caching {cacheProgress.cachedUnits} / {cacheProgress.totalUnits} units</span>
        {cacheProgress.error && <><span>{cacheProgress.error}</span><button type="button" onClick={() => { void retryCache(meta.id).then(setCacheProgress).catch((caught) => setError(String(caught))); }}>Retry</button></>}
      </div>}
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
            <span>Occupancy map: {meta.capabilities.occupancy ? "yes" : "no"}</span>
          </p>

          <hr />
          <section className="sidebar-block">
            <h2>Current RF map</h2>
            <div className="current-json-row">
              <select value={meta.sourcePath} title={meta.sourcePath} onChange={(event) => void openRemote(event.target.value)} aria-label="Current RF map">
                {(jsonChoices.length ? jsonChoices : mergeJsonChoices([], meta.sourcePath, recentPaths)).map((choice) => (
                  <option key={choice.path} value={choice.path}>{jsonChoiceLabel(choice, parentDirectory(meta.sourcePath))}</option>
                ))}
              </select>
              <button type="button" onClick={openChooser}>Open…</button>
            </div>
            {sourceBusy && !sourceOpen && (
              <div className="dialog-status" role="status">
                <span className="spinner small" /> Opening RF mapping file…
                <button type="button" onClick={cancelSourceLoad}>Cancel</button>
              </div>
            )}
            <label className="display-row tuning-session-row">
              <span>Tuning session</span>
              <input
                type="number"
                min={1}
                step={1}
                value={tuningSession}
                onChange={(event) => {
                  const requested = Number(event.target.value);
                  if (Number.isInteger(requested) && requested >= 1) setTuningSession(requested);
                }}
                title="Load only the exact DATE_SESSION tuning-curve artifact"
              />
            </label>
          </section>

          <hr />
          <section className="sidebar-block probe-sidebar-block">
            <div className="probe-sidebar-heading">
              <h2>Probe Layout</h2>
              <button type="button" onClick={() => setProbeChooserOpen(true)}>Choose .probe / positions.csv…</button>
            </div>
            {probe ? (
              <ProbeLayout
                geometry={probe}
                availableUnitIds={qualityVisibleUnitIds}
                currentClusterId={viewState.clusterId}
                selection={probeSelection}
                onCluster={(clusterId) => {
                  if (!qualityVisibleUnitIds.includes(clusterId)) return;
                  if (probeFilter != null && !probeFilter.includes(clusterId)) return;
                  updateState({ clusterId, selectedCellXMidpoint: null, selectedCellYMidpoint: null });
                }}
                onSelection={(selection, units) => {
                  setProbeSelection(selection);
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
                  : <><strong>Probe layout unavailable</strong><span>{probeError || "Choose the matching remote .probe or positions.csv file."}</span><button type="button" onClick={() => setProbeChooserOpen(true)}>Choose .probe / positions.csv…</button></>}
              </div>
            )}
            {probePositionsPath && <p className="companion-path" title={probePositionsPath}>{probePositionsPath}</p>}
          </section>

          <hr />
          <section className="sidebar-block">
            <h2>Unit</h2>
            <label className="check-row"><input type="checkbox" checked={paired.enabled} disabled={!paired.supported} onChange={(event) => paired.setEnabled(event.target.checked)} /><span>Pair Windows</span></label>
            {paired.enabled && <p className="muted-copy">{paired.peerCount ? `Paired with ${paired.peerCount} other tab${paired.peerCount === 1 ? "" : "s"}` : "Enable Pair Windows in another viewer tab."}</p>}
            <label className="check-row">
              <input
                type="checkbox"
                checked={unitFilterEnabled}
                onChange={(event) => setUnitFilterEnabled(event.target.checked)}
              />
              <span>Hide units with zero-spike RF bins</span>
            </label>
            <label className="display-row">
              <span>Zero-bin threshold</span>
              <input
                type="number"
                min={1}
                max={Math.min(100_000, meta.shape[1] * meta.shape[2])}
                step={1}
                disabled={!unitFilterEnabled}
                value={zeroSpikeSpatialBinThreshold}
                onChange={(event) => {
                  const value = userEnteredZeroSpikeSpatialBinThreshold(
                    Number(event.target.value),
                    meta.shape[1] * meta.shape[2],
                  );
                  if (value != null) setZeroSpikeSpatialBinThreshold(value);
                }}
                aria-label="Zero-spike spatial-bin threshold"
              />
            </label>
            <div className="unit-stats" aria-live="polite">
              {unitFilterStatus === "loading" && <span>Checking native y × x bins…</span>}
              {unitFilterStatus === "error" && <span>{unitFilterError}</span>}
              {unitFilterStatus === "ready" && (
                <span>
                  {unitFilterEnabled
                    ? `${qualityVisibleUnitIds.length} / ${meta.unitPool.length} units pass the current RF window`
                    : `Filter disabled; all ${meta.unitPool.length} units are available`}
                </span>
              )}
              <span>Filter uses source bins before display rebinning or smoothing.</span>
            </div>
            <div className="unit-picker">
              <button type="button" aria-label="Previous unit" onClick={() => stepUnit(-1)} disabled={!navigationPool.length}>&lt;</button>
              <select
                value={navigationPool.includes(viewState.clusterId) ? viewState.clusterId : ""}
                onChange={(event) => updateState({ clusterId: Number(event.target.value), selectedCellXMidpoint: null, selectedCellYMidpoint: null })}
                aria-label="Current unit"
              >
                {!navigationPool.includes(viewState.clusterId) && <option value="">{emptyUnitTitle}</option>}
                {navigationPool.map((clusterId) => {
                  const index = meta.unitPool.indexOf(clusterId);
                  return (
                    <option key={clusterId} value={clusterId}>
                      {index < 0 ? `N/A · cluster ${clusterId}` : `${String(index).padStart(3, "0")}  cluster ${clusterId}`}
                    </option>
                  );
                })}
              </select>
              <button type="button" aria-label="Next unit" onClick={() => stepUnit(1)} disabled={!navigationPool.length}>&gt;</button>
            </div>
            <div className="unit-stats">
              {unitIsLoading && <span>Loading cluster…</span>}
              {noNavigationUnits && <><strong>{emptyUnitTitle}</strong><span>{emptyUnitDetail}</span></>}
              {unitStatus === "error" && <span>Unit data failed to load.</span>}
              {metrics && !noNavigationUnits && <>
                <span>Summed RF counts: {metrics.totalSpikes.toFixed(0)}</span>
                <span>Strongest rate cell: yIdx {metrics.bestY + 1}, xIdx {metrics.bestX + 1} ({formatResponse(metrics.bestRateHz, "Mean firing rate (Hz)")} Hz)</span>
                <span>Count-rate peak delay: {bestDelay == null ? "n/a" : `${formatNumber(bestDelay, 1)} ms`}</span>
              </>}
            </div>
          </section>

          <hr />
          <WaveformPanel
            artifact={waveformArtifact}
            clusterId={viewState.clusterId}
            loading={waveformLoading}
            error={waveformError}
            visible={showWaveform}
            mode={waveformChannelMode}
            blocked={noNavigationUnits}
            onVisibleChange={setShowWaveform}
            onModeChange={setWaveformChannelMode}
          />

          <hr />
          <section className="sidebar-block display-block">
            <h2><button type="button" onClick={() => setDisplayOptions((value) => !value)}>{displayOptions ? "Hide (D)" : "Display Options (D)"}</button></h2>
            <div hidden={!displayOptions}>
            <label className="check-row"><input type="checkbox" checked={viewState.flipY} onChange={(event) => updateState({ flipY: event.target.checked })} /><span>Invert Y (MATLAB flip)</span></label>
            <label className="display-row"><span>X bins</span><input type="number" min={1} max={meta.shape[2]} step={1} value={viewState.xBins} onChange={(event) => updateState({ xBins: clamp(Math.round(Number(event.target.value)), 1, meta.shape[2]) })} /></label>
            <label className="display-row"><span>Y bins</span><input type="number" min={1} max={meta.shape[1]} step={1} value={viewState.yBins} onChange={(event) => updateState({ yBins: clamp(Math.round(Number(event.target.value)), 1, meta.shape[1]) })} /></label>
            <label className="display-row"><span>Smooth</span><input type="number" min={0} max={3} step={1} value={viewState.smoothRadius} onChange={(event) => updateState({ smoothRadius: clamp(Math.round(Number(event.target.value)), 0, 3) })} /></label>
            <label className="display-row"><span>Palette</span><select value={viewState.palette} onChange={(event) => updateState({ palette: event.target.value as Palette })}>{PALETTES.map((palette) => <option key={palette}>{palette}</option>)}</select></label>
            <label className="display-row"><span>Polar radius</span><select value={viewState.polarRadius} onChange={(event) => updateState({ polarRadius: event.target.value as PolarRadius })}>{POLAR_RADIUS_MODES.map((mode) => <option key={mode}>{mode}</option>)}</select></label>
            </div>
          </section>

          <hr />
          <section className="sidebar-block selected-block">
            <h2>Selected cell</h2>
            {!noNavigationUnits && selectedCell && selectedDetails ? (
              <div className="selected-cell-text">
                <span>cluster {viewState.clusterId}</span>
                <span>yIdx {selectedCell[0] + 1}{selectedCell[1] !== selectedCell[0] ? `-${selectedCell[1] + 1}` : ""}; y {formatNumber(meta.yPositions[selectedCell[0]], 3)}{selectedCell[1] !== selectedCell[0] ? `..${formatNumber(meta.yPositions[selectedCell[1]], 3)}` : ""},</span>
                <span>xIdx {selectedCell[2] + 1}{selectedCell[3] !== selectedCell[2] ? `-${selectedCell[3] + 1}` : ""}; x {formatNumber(meta.xPositions[selectedCell[2]], 3)}{selectedCell[3] !== selectedCell[2] ? `..${formatNumber(meta.xPositions[selectedCell[3]], 3)}` : ""}</span>
                {(selectedCell[1] !== selectedCell[0] || selectedCell[3] !== selectedCell[2]) && <span>{viewState.valueMode === "Spike count" ? "mean" : "pooled"} over exposed source pixels</span>}
                <span>bin {formatResponse(selectedDetails.activeValue, viewState.valueMode)} {unit} ({formatNumber(timeBounds(meta, groups[activeGroup])[0])}–{formatNumber(timeBounds(meta, groups[activeGroup])[1])} ms)</span>
                <span>RF {rfWindowLabel(viewState)}: {formatResponse(selectedDetails.rfValue, viewState.valueMode)} {unit}</span>
                <span>full window {formatResponse(selectedDetails.totalValue, viewState.valueMode)} {unit}</span>
                <span>peak {formatResponse(selectedDetails.peakValue, viewState.valueMode)} {unit}</span>
                <span>peak bin {selectedDetails.peakIndex < 0 ? "n/a" : `${selectedDetails.peakIndex + 1} (${formatNumber(timeBounds(meta, groups[selectedDetails.peakIndex])[0])}–${formatNumber(timeBounds(meta, groups[selectedDetails.peakIndex])[1])} ms)`}</span>
                <span>count-rate peak delay {selectedDetails.delay == null ? "n/a" : `${formatNumber(selectedDetails.delay, 1)} ms`}, count entropy {selectedDetails.entropy.toFixed(3)}</span>
              </div>
            ) : <span className="muted-copy">N/A for this session</span>}
            <div className="export-button-stack">
              <button className="export-button figure-button" type="button" onClick={openFigureComposer} disabled={unitFilterStatus !== "ready" || !qualityVisibleUnitIds.length || Boolean(cacheProgress && !cacheProgress.complete)}>Compose figures…</button>
              <button className="export-button" type="button" onClick={openExportDialog} disabled={!counts || noNavigationUnits}>Export displayed data…</button>
            </div>
          </section>

          <p className="shortcut-hint">←/→ unit&nbsp;&nbsp;&nbsp;↑/↓ timeline<br />⇧,/⇧. time resolution&nbsp;&nbsp;&nbsp;<button type="button" onClick={() => setHelpOpen(true)}>Keyboard shortcuts</button></p>
        </div>
      </aside>}

      <main className="workspace">
        <header className="workspace-heading">
          <h1>{noNavigationUnits ? emptyUnitTitle : unavailableUnit ? `Cluster ${viewState.clusterId} / N/A in this dataset` : `Unit ${String(localIndex).padStart(3, "0")} / cluster ${viewState.clusterId}`}</h1>
          <p>
            {noNavigationUnits
              ? emptyUnitDetail
              : `x: ${formatNumber(meta.xPositions[0], 3)}..${formatNumber(meta.xPositions.at(-1)!, 3)}  y: ${formatNumber(meta.yPositions[0], 3)}..${formatNumber(meta.yPositions.at(-1)!, 3)}  time: ${formatNumber(axisStartMs)}..${formatNumber(axisEndMs)} ms  value: ${viewState.valueMode}`}
          </p>
        </header>

        <section className="plot-controls">
          <div className="plot-control-row top-row">
            <label><span>Value</span><select value={viewState.valueMode} onChange={(event) => updateState({ valueMode: VALUE_MODES.includes(event.target.value as ValueMode) ? event.target.value as ValueMode : DEFAULT_VALUE_MODE })}>{VALUE_MODES.map((mode) => <option key={mode}>{mode}</option>)}</select></label>
            <label><span>Time resolution (ms)</span><input type="number" min={baseBinMs(meta)} max={axisEndMs - axisStartMs} step={baseBinMs(meta)} value={formatNumber(viewState.timeResolutionMs, 6)} onChange={(event) => changeResolution(Number(event.target.value))} /></label>
          </div>
          <div className="plot-control-row bottom-row">
            <label className="range-title"><select aria-label="RF window mode" value={viewState.rfWindowMode ?? "sum"} onChange={() => updateState(toggleRfMode(meta, viewState))}><option value="sum">RF sum (ms)</option><option value="difference">RF A − B (ms)</option></select></label>
            <RfWindowInput value={viewState.rfStartMs} label="RF range start" onCommit={(value) => { const bounds = timeBounds(meta, snapTimeRange(meta, value, viewState.rfEndMs)); updateState({ rfStartMs: bounds[0], rfEndMs: bounds[1] }); }} />
            <span>to</span>
            <RfWindowInput value={viewState.rfEndMs} label="RF range end" onCommit={(value) => { const bounds = timeBounds(meta, snapTimeRange(meta, viewState.rfStartMs, value)); updateState({ rfStartMs: bounds[0], rfEndMs: bounds[1] }); }} />
            {viewState.rfWindowMode === "difference" && <><span>− (</span>
              <RfWindowInput value={viewState.rfBStartMs ?? 0} label="RF window B start" onCommit={(value) => { const bounds = timeBounds(meta, snapTimeRange(meta, value, viewState.rfBEndMs ?? 80)); updateState({ rfBStartMs: bounds[0], rfBEndMs: bounds[1] }); }} />
              <span>to</span><RfWindowInput value={viewState.rfBEndMs ?? 80} label="RF window B end" onCommit={(value) => { const bounds = timeBounds(meta, snapTimeRange(meta, viewState.rfBStartMs ?? 0, value)); updateState({ rfBStartMs: bounds[0], rfBEndMs: bounds[1] }); }} /><span>)</span></>}
            <label className="check-row"><input type="checkbox" checked={viewState.polarLayout} onChange={(event) => updateState({ polarLayout: event.target.checked })} /><span>Polar layout</span></label>
            <label className="check-row"><input type="checkbox" checked={viewState.rgbMode} disabled={viewState.selectedTab !== "delay"} onChange={(event) => updateState({ rgbMode: event.target.checked })} /><span>RGB composite</span></label>
            {viewState.selectedTab === "rf" && <label className="hd-layout-control"><span>RF + HD</span><select value={hdLayout} onChange={(event) => {
              const layout = event.target.value as HdLayout;
              setHdLayout(layout);
              window.localStorage.setItem(HD_LAYOUT_KEY, layout);
            }}><option value="side-by-side">Side by side</option><option value="stacked">Stacked</option></select></label>}
            <button className="reset-button" type="button" onClick={() => { const saved = readRfTiming(window.localStorage.getItem(RF_TIMING_KEY)); updateState(resetRfTiming(meta, viewState, saved)); }}>Reset windows</button>
            <button type="button" onClick={() => { window.localStorage.setItem(RF_TIMING_KEY, JSON.stringify(timingFromState(viewState))); setMessageDialog({ title: "RF timing defaults saved", text: "New datasets use these Sum and A − B windows. Reset restores the saved windows for the active mode." }); }}>Save timing defaults</button>
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
                {noNavigationUnits && <div className={`view-empty${unitFilterStatus === "error" ? " error-state" : ""}`}><strong>{emptyUnitTitle}</strong><span>{emptyUnitDetail}</span></div>}
                {unavailableUnit && unavailableView}
                {!noNavigationUnits && unitIsLoading && <div className="view-empty"><span className="spinner" /> Loading cluster {viewState.clusterId}…</div>}
                {!noNavigationUnits && unitStatus === "error" && <div className="view-empty error-state"><strong>Unit data could not be loaded</strong><span>{error}</span></div>}
                {viewState.selectedTab === "rf" && !noNavigationUnits && !unavailableUnit && counts && selectedCell && <SpatialPlot kind="rf" meta={meta} counts={counts} state={viewState} unitIndex={localIndex} selectedCell={selectedCell} onSelectCell={selectCell} />}
              </div>
              <HdPanel
                artifact={hdArtifact}
                clusterId={viewState.clusterId}
                loading={hdLoading}
                error={hdError}
                rfPolarLayout={viewState.polarLayout}
                blocked={noNavigationUnits || unavailableUnit}
                collapsed={hdCollapsed}
                settings={hdSettings}
                onSettingsChange={setHdSettings}
                onToggleCollapsed={() => setHdCollapsed((value) => !value)}
                onChoosePath={() => setHdChooserOpen(true)}
              />
            </div>
            {viewState.selectedTab === "delay" && (
              noNavigationUnits
                ? <div className="view-empty"><strong>{emptyUnitTitle}</strong><span>{emptyUnitDetail}</span></div>
                : unavailableUnit ? unavailableView : unitIsLoading
                  ? <div className="view-empty"><span className="spinner" /> Loading cluster {viewState.clusterId}…</div>
                  : unitStatus === "error"
                    ? <div className="view-empty error-state"><strong>Unit data could not be loaded</strong><span>{error}</span></div>
                    : counts && selectedCell && <SpatialPlot kind="delay" meta={meta} counts={counts} state={viewState} unitIndex={localIndex} selectedCell={selectedCell} onSelectCell={selectCell} />
            )}
            {viewState.selectedTab === "timeline" && (
              noNavigationUnits
                ? <div className="view-empty"><strong>{emptyUnitTitle}</strong><span>{emptyUnitDetail}</span></div>
                : unavailableUnit ? unavailableView : unitIsLoading
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
          onCancel={cancelSourceLoad}
          onClose={() => { cancelSourceLoad(); setSourceOpen(false); }}
          onRemote={handleRemoteChoice}
        />
      )}
      {probeChooserOpen && (
        <SourceChooser
          overlay
          kind="positions-csv"
          title="Attach Probe .probe or positions.csv"
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
          title="Attach HD .tc or tuning_curves.json"
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
          visibleUnitIds={qualityVisibleUnitIds}
          unitFilter={{
            enabled: unitFilterEnabled,
            rfStartMs: viewState.rfStartMs,
            rfEndMs: viewState.rfEndMs,
            zeroSpikeSpatialBinThreshold,
            visibleUnitIds: [...qualityVisibleUnitIds],
          }}
          viewState={viewState}
          selectedCell={selectedCell}
          hdSettings={hdSettings}
          probeFilteredUnitIds={probeFilter}
          availableCapabilities={{
            hd: Boolean(hdArtifact?.available),
            probe: probe != null,
            waveform: meta.capabilities.waveform,
          }}
          hdPath={hdPath}
          probePositionsPath={probePositionsPath}
          tuningSession={tuningSession}
          waveformChannelMode={waveformChannelMode}
          onClose={() => setFigureComposerOpen(false)}
        />
      )}
      {helpOpen && (
        <div className="modal-backdrop" onMouseDown={(event) => { if (event.currentTarget === event.target) setHelpOpen(false); }}>
          <div className="info-dialog" role="dialog" aria-modal="true" aria-label="Keyboard Shortcuts">
            <header><strong>Keyboard Shortcuts</strong><button type="button" aria-label="Close" onClick={() => setHelpOpen(false)}>×</button></header>
            <pre>← / → or [ / ]   Previous / next unit{"\n"}↑ / ↓   Previous / next timeline bin{"\n"}Shift+, / Shift+.   Coarser / finer by one source bin{"\n"}−   Toggle RF Sum / A − B windows{"\n"}D   Show / hide Display Options{"\n"}Command/Ctrl-Shift+.   Show / hide filtered units{"\n"}1–3   Switch RF / Delay-RGB / Timeline{"\n"}F   Invert Y{"\n"}P   Toggle rectangular / polar layout{"\n"}Shift+P   Cycle palette{"\n"}Double-click waveform   Enlarge local waveform{"\n"}Esc   Close waveform zoom; clear Probe region; otherwise show full Timeline{"\n"}Command/Ctrl-O   Open an RF mapping file in this viewer{"\n"}Command/Ctrl-E   Open Figure Export Composer{"\n"}Command/Ctrl-Shift-E   Export displayed data CSV</pre>
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
