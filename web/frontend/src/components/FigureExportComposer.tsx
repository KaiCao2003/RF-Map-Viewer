import {
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";
import {
  ApiError,
  exportFigurePlan,
  getFigureExportSpec,
  listFigureExportDirectories,
  previewFigureExport,
} from "../api";
import {
  buildFigureExportRequest,
  buildFigurePreviewRequest,
  composerValidationError,
  createFigureComposerState,
  currentFigureType,
  figureUnitSelectionAfterGesture,
  figureComposerReducer,
  matchingUnitIds,
  orderedUnitSelection,
  parentFigureDirectory,
  safeExportBaseName,
  snapshotPlotSettings,
  type FigureComposerState,
  type FigureExportSpec,
  type FigureExportResult,
  type FigureTypeDefinition,
  type FigureTypeId,
} from "../figureExport";
import type {
  CellRef,
  DatasetMeta,
  HdViewSettings,
  ViewState,
} from "../types";

interface FigureExportComposerProps {
  meta: DatasetMeta;
  viewState: ViewState;
  selectedCell: CellRef | null;
  hdSettings: HdViewSettings;
  probeFilteredUnitIds: ReadonlyArray<number> | null;
  availableCapabilities: { hd: boolean; probe: boolean };
  hdPath: string | null;
  probePositionsPath: string | null;
  initialSpec?: FigureExportSpec;
  onClose: () => void;
}

type PreviewStatus = "idle" | "waiting" | "rendering" | "ready" | "error";
type ExportStatus = "idle" | "exporting" | "complete" | "error";

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "n/a";
  if (value < 1024) return `${Math.round(value)} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
}

function settingsSummary(settings: Record<string, unknown>): string {
  const entries = Object.entries(settings);
  if (!entries.length) return "No configurable settings";
  return entries.map(([name, value]) => {
    if (value && typeof value === "object") return `${name}=${JSON.stringify(value)}`;
    return `${name}=${String(value)}`;
  }).join(" · ");
}

function capabilityAvailable(
  definition: FigureTypeDefinition,
  capabilities: FigureExportComposerProps["availableCapabilities"],
): boolean {
  return definition.capability == null || capabilities[definition.capability];
}

function figureTypeById(
  definitions: ReadonlyArray<FigureTypeDefinition>,
  type: FigureTypeId,
): FigureTypeDefinition {
  return definitions.find((definition) => definition.id === type) ?? {
    id: type,
    label: type,
    family: "unknown",
    projection: "unknown",
    settings: {},
  };
}

function UnitPicker({
  draft,
  currentClusterId,
  probeFilteredUnitIds,
  dispatch,
}: {
  draft: FigureComposerState;
  currentClusterId: number;
  probeFilteredUnitIds: ReadonlyArray<number> | null;
  dispatch: React.Dispatch<Parameters<typeof figureComposerReducer>[1]>;
}) {
  const visible = useMemo(
    () => matchingUnitIds(draft.unitPool, draft.unitSearch),
    [draft.unitPool, draft.unitSearch],
  );
  const selected = new Set(draft.selectedUnitIds);
  const probeOrdered = probeFilteredUnitIds == null
    ? null
    : orderedUnitSelection(draft.unitPool, probeFilteredUnitIds);
  const selectionAnchor = useRef<number | null>(currentClusterId);
  useEffect(() => {
    if (selectionAnchor.current != null && draft.unitPool.includes(selectionAnchor.current)) return;
    selectionAnchor.current = draft.selectedUnitIds[0] ?? currentClusterId;
  }, [currentClusterId, draft.selectedUnitIds, draft.unitPool]);
  const setUnits = (unitIds: ReadonlyArray<number>, anchorUnitId = unitIds[0] ?? null) => {
    selectionAnchor.current = anchorUnitId;
    dispatch({ type: "set-units", unitIds });
  };
  const selectUnit = (
    clusterId: number,
    gesture: { additive: boolean; range: boolean; checkbox: boolean },
  ) => {
    const result = figureUnitSelectionAfterGesture(
      draft.unitPool,
      visible,
      draft.selectedUnitIds,
      clusterId,
      selectionAnchor.current,
      gesture,
    );
    selectionAnchor.current = result.anchorUnitId;
    dispatch({ type: "set-units", unitIds: result.unitIds });
  };
  return (
    <aside className="figure-units-panel" aria-label="Units to export">
      <div className="figure-section-heading">
        <div><strong>Units</strong><span>{draft.selectedUnitIds.length} selected</span></div>
      </div>
      <div className="figure-unit-presets" role="group" aria-label="Unit selection presets">
        <button type="button" onClick={() => setUnits([currentClusterId], currentClusterId)}>Current</button>
        <button type="button" onClick={() => setUnits(draft.unitPool)}>All</button>
        <button
          type="button"
          disabled={probeOrdered == null || probeOrdered.length === 0}
          title={probeOrdered == null ? "Draw a Probe region first" : undefined}
          onClick={() => setUnits(probeOrdered ?? [])}
        >
          Probe filtered{probeOrdered == null ? "" : ` (${probeOrdered.length})`}
        </button>
        <button type="button" onClick={() => setUnits([])}>Clear</button>
      </div>
      <label className="figure-unit-search">
        <span>Search index or cluster ID</span>
        <input
          type="search"
          value={draft.unitSearch}
          onChange={(event) => dispatch({ type: "set-unit-search", value: event.target.value })}
          placeholder="e.g. 023 or 145"
        />
      </label>
      <div className="figure-visible-actions">
        <button
          type="button"
          disabled={!visible.length}
          onClick={() => setUnits([...draft.selectedUnitIds, ...visible])}
        >Select matches</button>
        <button
          type="button"
          disabled={!visible.some((unitId) => selected.has(unitId))}
          onClick={() => setUnits(
            draft.selectedUnitIds.filter((unitId) => !visible.includes(unitId)),
          )}
        >Clear matches</button>
      </div>
      <div className="figure-unit-list" role="listbox" aria-label="Export units" aria-multiselectable="true">
        {visible.map((clusterId) => {
          const index = draft.unitPool.indexOf(clusterId);
          return (
            <div
              key={clusterId}
              className={`figure-unit-row${selected.has(clusterId) ? " selected" : ""}`}
              role="option"
              aria-selected={selected.has(clusterId)}
              tabIndex={0}
              onClick={(event) => selectUnit(clusterId, {
                additive: event.metaKey || event.ctrlKey,
                range: event.shiftKey,
                checkbox: false,
              })}
              onKeyDown={(event) => {
                if (event.key !== "Enter" && event.key !== " ") return;
                event.preventDefault();
                selectUnit(clusterId, {
                  additive: event.metaKey || event.ctrlKey,
                  range: event.shiftKey,
                  checkbox: false,
                });
              }}
            >
              <input
                type="checkbox"
                checked={selected.has(clusterId)}
                readOnly
                aria-label={`${String(index).padStart(3, "0")} cluster ${clusterId}`}
                onKeyDown={(event) => event.stopPropagation()}
                onClick={(event) => {
                  event.stopPropagation();
                  selectUnit(clusterId, {
                    additive: event.metaKey || event.ctrlKey,
                    range: event.shiftKey,
                    checkbox: true,
                  });
                }}
              />
              <span className="unit-index">{String(index).padStart(3, "0")}</span>
              <span>cluster {clusterId}</span>
              {clusterId === currentClusterId && <small>current</small>}
            </div>
          );
        })}
        {!visible.length && <div className="figure-empty-list">No matching units</div>}
      </div>
      <p className="figure-order-note">Click a row for one unit; Command/Ctrl-click toggles; Shift-click selects a range. Checkboxes toggle units. Export order always follows the original JSON unitPool.</p>
    </aside>
  );
}

function PageComposer({
  draft,
  definitions,
  maxPlots,
  settingsFor,
  capabilities,
  nextId,
  dispatch,
}: {
  draft: FigureComposerState;
  definitions: FigureTypeDefinition[];
  maxPlots: number;
  settingsFor: (type: FigureTypeId) => Record<string, unknown>;
  capabilities: FigureExportComposerProps["availableCapabilities"];
  nextId: (prefix: "page" | "plot") => string;
  dispatch: React.Dispatch<Parameters<typeof figureComposerReducer>[1]>;
}) {
  const activePage = draft.pages.find((page) => page.id === draft.activePageId) ?? draft.pages[0];
  const activePageIndex = draft.pages.findIndex((page) => page.id === activePage.id);
  const addPage = () => {
    const type = draft.addPlotType;
    const pageNumber = draft.pages.length + 1;
    dispatch({
      type: "add-page",
      page: {
        id: nextId("page"),
        title: `Page ${pageNumber}`,
        plots: [{ id: nextId("plot"), type, settings: settingsFor(type) }],
      },
    });
  };
  const addPlot = () => {
    dispatch({
      type: "add-plot",
      pageId: activePage.id,
      maximum: maxPlots,
      plot: {
        id: nextId("plot"),
        type: draft.addPlotType,
        settings: settingsFor(draft.addPlotType),
      },
    });
  };
  return (
    <section className="figure-pages-panel" aria-label="Page templates">
      <div className="figure-section-heading">
        <div><strong>Page templates</strong><span>Repeated for every selected unit</span></div>
        <button type="button" onClick={addPage}>+ Add page</button>
      </div>
      <div className="figure-page-tabs" role="tablist" aria-label="Export pages">
        {draft.pages.map((page, index) => (
          <button
            key={page.id}
            type="button"
            role="tab"
            aria-selected={page.id === activePage.id}
            className={page.id === activePage.id ? "active" : ""}
            onClick={() => dispatch({ type: "select-page", pageId: page.id })}
          >{index + 1}. {page.title || "Untitled"}</button>
        ))}
      </div>
      <div className="figure-page-editor">
        <div className="figure-page-title-row">
          <label>
            <span>Page name</span>
            <input
              type="text"
              maxLength={200}
              value={activePage.title}
              onChange={(event) => dispatch({
                type: "rename-page",
                pageId: activePage.id,
                title: event.target.value,
              })}
            />
          </label>
          <div className="figure-page-actions" role="group" aria-label={`Reorder and remove ${activePage.title || "untitled page"}`}>
            <button
              type="button"
              aria-label={`Move ${activePage.title || "untitled page"} earlier`}
              disabled={activePageIndex <= 0}
              onClick={() => dispatch({ type: "move-page", pageId: activePage.id, delta: -1 })}
            >← Earlier</button>
            <button
              type="button"
              aria-label={`Move ${activePage.title || "untitled page"} later`}
              disabled={activePageIndex >= draft.pages.length - 1}
              onClick={() => dispatch({ type: "move-page", pageId: activePage.id, delta: 1 })}
            >Later →</button>
            <button
              type="button"
              disabled={draft.pages.length <= 1}
              onClick={() => dispatch({ type: "remove-page", pageId: activePage.id })}
            >Remove page</button>
          </div>
        </div>
        <div className="figure-add-plot-row">
          <label>
            <span>Figure type</span>
            <select
              aria-label="Figure type to add"
              value={draft.addPlotType}
              onChange={(event) => dispatch({
                type: "set-add-plot-type",
                plotType: event.target.value as FigureTypeId,
              })}
            >
              {definitions.map((definition) => (
                <option key={definition.id} value={definition.id}>
                  {definition.label}{capabilityAvailable(definition, capabilities) ? "" : " — unavailable"}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={activePage.plots.length >= maxPlots}
            onClick={addPlot}
          >+ Add plot</button>
          <span>{activePage.plots.length}/{maxPlots}</span>
        </div>
        <div className="figure-plot-list">
          {activePage.plots.map((plot, index) => {
            const definition = figureTypeById(definitions, plot.type);
            const available = capabilityAvailable(definition, capabilities);
            return (
              <article key={plot.id} className={`figure-plot-card ${available ? "" : "unavailable"}`}>
                <div className="figure-plot-order">{index + 1}</div>
                <div className="figure-plot-body">
                  <div className="figure-plot-heading">
                    <strong>{definition.label}</strong>
                    <code>{definition.id}</code>
                  </div>
                  {!available && (
                    <p className="figure-capability-warning" role="status">
                      {definition.capability?.toUpperCase()} data unavailable. The server will render a labeled placeholder; this plot is not being ignored.
                    </p>
                  )}
                  <details>
                    <summary>Frozen viewer settings</summary>
                    <p>{settingsSummary(plot.settings)}</p>
                  </details>
                </div>
                <div className="figure-plot-actions">
                  <button type="button" aria-label={`Move ${definition.label} up`} disabled={index === 0} onClick={() => dispatch({ type: "move-plot", pageId: activePage.id, plotId: plot.id, delta: -1 })}>↑</button>
                  <button type="button" aria-label={`Move ${definition.label} down`} disabled={index === activePage.plots.length - 1} onClick={() => dispatch({ type: "move-plot", pageId: activePage.id, plotId: plot.id, delta: 1 })}>↓</button>
                  <button type="button" onClick={() => dispatch({ type: "replace-plot-settings", pageId: activePage.id, plotId: plot.id, settings: settingsFor(plot.type) })}>Refresh snapshot</button>
                  <button type="button" disabled={activePage.plots.length <= 1} onClick={() => dispatch({ type: "remove-plot", pageId: activePage.id, plotId: plot.id })}>Remove</button>
                </div>
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function DestinationBrowser({
  draft,
  listing,
  busy,
  error,
  dispatch,
}: {
  draft: FigureComposerState;
  listing: Awaited<ReturnType<typeof listFigureExportDirectories>> | null;
  busy: boolean;
  error: string;
  dispatch: React.Dispatch<Parameters<typeof figureComposerReducer>[1]>;
}) {
  const current = draft.destinationDirectory;
  return (
    <section className="figure-destination-browser" aria-label="Export destination directory">
      <div className="figure-directory-path">
        <button
          type="button"
          disabled={!current || busy}
          aria-label="Parent export directory"
          onClick={() => dispatch({ type: "set-destination", directory: parentFigureDirectory(current) })}
        >↑</button>
        <code title={`/mnt/senzailab${current ? `/${current}` : ""}`}>
          /mnt/senzailab{current ? `/${current}` : ""}
        </code>
        {listing?.path === current && (
          <span className={listing.writable ? "writable" : "not-writable"}>
            {listing.writable ? "writable" : "read only"}
          </span>
        )}
      </div>
      {error && <p className="figure-inline-error" role="alert">{error}</p>}
      <div className="figure-directory-list">
        {busy && <div className="figure-directory-loading"><span className="spinner small" /> Loading folders…</div>}
        {!busy && listing?.entries.map((entry) => (
          <button
            type="button"
            key={entry.path}
            onClick={() => dispatch({ type: "set-destination", directory: entry.path })}
          >
            <span>▸</span><strong>{entry.name}</strong><small>{entry.writable ? "writable" : "read only"}</small>
          </button>
        ))}
        {!busy && !error && listing?.entries.length === 0 && <div className="figure-empty-list">No subdirectories</div>}
      </div>
      <p>Navigate to a folder to select it. Files are confined to the configured /mnt/senzailab export root.</p>
    </section>
  );
}

export default function FigureExportComposer({
  meta,
  viewState,
  selectedCell,
  hdSettings,
  probeFilteredUnitIds,
  availableCapabilities,
  hdPath,
  probePositionsPath,
  initialSpec,
  onClose,
}: FigureExportComposerProps) {
  const [spec, setSpec] = useState<Awaited<ReturnType<typeof getFigureExportSpec>> | null>(initialSpec ?? null);
  const [specError, setSpecError] = useState("");
  const [specRefresh, setSpecRefresh] = useState(0);
  const initialType = currentFigureType(viewState);
  const [draft, dispatch] = useReducer(
    figureComposerReducer,
    createFigureComposerState({
      unitPool: meta.unitPool,
      currentClusterId: viewState.clusterId,
      initialType,
      initialSettings: snapshotPlotSettings(initialType, {
        view: viewState,
        selectedCell,
        hd: hdSettings,
      }),
      baseName: safeExportBaseName(meta.name),
    }),
  );
  const idSequence = useRef(2);
  const nextId = (prefix: "page" | "plot") => `${prefix}-${idSequence.current++}`;
  const [directoryListing, setDirectoryListing] = useState<Awaited<ReturnType<typeof listFigureExportDirectories>> | null>(null);
  const [directoryBusy, setDirectoryBusy] = useState(false);
  const [directoryError, setDirectoryError] = useState("");
  const [previewStatus, setPreviewStatus] = useState<PreviewStatus>("idle");
  const [previewUrl, setPreviewUrl] = useState("");
  const previewUrlRef = useRef("");
  const [previewError, setPreviewError] = useState("");
  const [placeholderCount, setPlaceholderCount] = useState(0);
  const [exportStatus, setExportStatus] = useState<ExportStatus>("idle");
  const [exportError, setExportError] = useState("");
  const [exportResult, setExportResult] = useState<FigureExportResult | null>(null);

  const snapshotContext = useMemo(() => ({
    view: viewState,
    selectedCell,
    hd: hdSettings,
  }), [hdSettings, selectedCell, viewState]);
  const settingsFor = (type: FigureTypeId) => snapshotPlotSettings(type, snapshotContext);

  useEffect(() => {
    if (initialSpec) return;
    const controller = new AbortController();
    setSpecError("");
    getFigureExportSpec(controller.signal)
      .then((loaded) => {
        if (controller.signal.aborted) return;
        setSpec(loaded);
      })
      .catch((caught) => {
        if (!controller.signal.aborted) {
          setSpecError(caught instanceof Error ? caught.message : "Could not load the figure export specification.");
        }
      });
    return () => controller.abort();
    // The initial snapshot is intentionally frozen when the composer opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSpec, meta.id, specRefresh]);

  useEffect(() => {
    const controller = new AbortController();
    setDirectoryBusy(true);
    setDirectoryError("");
    listFigureExportDirectories(draft.destinationDirectory, controller.signal)
      .then((listing) => {
        if (!controller.signal.aborted) setDirectoryListing(listing);
      })
      .catch((caught) => {
        if (!controller.signal.aborted) {
          setDirectoryListing(null);
          setDirectoryError(caught instanceof Error ? caught.message : "Could not list export folders.");
        }
      })
      .finally(() => { if (!controller.signal.aborted) setDirectoryBusy(false); });
    return () => controller.abort();
  }, [draft?.destinationDirectory]);

  const previewRequest = useMemo(() => {
    if (!spec || !draft.selectedUnitIds.length) return null;
    return buildFigurePreviewRequest(draft, spec.specVersion, { hdPath, probePositionsPath });
  }, [draft, hdPath, probePositionsPath, spec]);
  const previewKey = previewRequest == null ? "" : JSON.stringify(previewRequest);

  useEffect(() => {
    if (!previewRequest) {
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = "";
      setPreviewUrl("");
      setPlaceholderCount(0);
      setPreviewStatus("idle");
      return;
    }
    const controller = new AbortController();
    setPreviewStatus("waiting");
    setPreviewError("");
    const timer = window.setTimeout(() => {
      setPreviewStatus("rendering");
      previewFigureExport(meta.id, previewRequest, controller.signal)
        .then((result) => {
          if (controller.signal.aborted) return;
          const nextUrl = URL.createObjectURL(result.image);
          if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
          previewUrlRef.current = nextUrl;
          setPreviewUrl(nextUrl);
          setPlaceholderCount(result.placeholderCount);
          setPreviewStatus("ready");
        })
        .catch((caught) => {
          if (!controller.signal.aborted) {
            setPlaceholderCount(0);
            setPreviewError(caught instanceof Error ? caught.message : "Preview rendering failed.");
            setPreviewStatus("error");
          }
        });
    }, 350);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
    // previewKey is a stable deep dependency for nested page settings.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta.id, previewKey]);

  useEffect(() => () => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && exportStatus !== "exporting") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [exportStatus, onClose]);

  if (!spec) {
    return (
      <div className="figure-composer-shell" role="dialog" aria-modal="true" aria-label="Figure Export Composer">
        <header className="figure-composer-header"><div><h1>Figure Export Composer</h1><p>{meta.name}</p></div><button type="button" aria-label="Close Figure Export Composer" onClick={onClose}>×</button></header>
        <div className="figure-composer-loading">
          {specError ? <><strong>Export workspace could not load</strong><span>{specError}</span><button type="button" onClick={() => setSpecRefresh((value) => value + 1)}>Retry</button></> : <><span className="spinner" /> Loading export specification…</>}
        </div>
      </div>
    );
  }

  const previewPage = draft.pages.find((page) => page.id === draft.previewPageId) ?? draft.pages[0];
  const destinationWritable = directoryListing?.path === draft.destinationDirectory
    && directoryListing.writable;
  const validationError = composerValidationError(draft, Boolean(destinationWritable));
  const resultPlaceholderCount = exportResult?.manifest.pages.reduce(
    (sum, page) => sum + page.placeholders.length,
    0,
  ) ?? 0;

  const submitExport = async () => {
    if (validationError) return;
    setExportStatus("exporting");
    setExportError("");
    setExportResult(null);
    try {
      const result = await exportFigurePlan(
        meta.id,
        buildFigureExportRequest(draft, spec.specVersion, { hdPath, probePositionsPath }),
      );
      setExportResult(result);
      setExportStatus("complete");
    } catch (caught) {
      const conflict = caught instanceof ApiError && caught.status === 409;
      setExportError(conflict
        ? "That export already exists. Enable ‘Replace existing output’ and export again if replacement is intended."
        : caught instanceof Error ? caught.message : "Figure export failed.");
      setExportStatus("error");
    }
  };

  return (
    <div className="figure-composer-shell" role="dialog" aria-modal="true" aria-label="Figure Export Composer">
      <header className="figure-composer-header">
        <div><h1>Figure Export Composer</h1><p>{meta.sourcePath}</p></div>
        <div className="figure-composer-header-actions">
          <span>{draft.selectedUnitIds.length} units × {draft.pages.length} pages = {draft.selectedUnitIds.length * draft.pages.length} outputs</span>
          <button type="button" disabled={exportStatus === "exporting"} aria-label="Close Figure Export Composer" onClick={onClose}>×</button>
        </div>
      </header>
      {(hdPath || probePositionsPath) && (
        <div className="figure-global-notice" role="status">
          Live preview and final export use the same manually attached companion files
          {hdPath ? ` · HD: ${hdPath}` : ""}
          {probePositionsPath ? ` · Probe: ${probePositionsPath}` : ""}
        </div>
      )}
      <div className="figure-composer-workspace">
        <UnitPicker
          draft={draft}
          currentClusterId={viewState.clusterId}
          probeFilteredUnitIds={probeFilteredUnitIds}
          dispatch={dispatch}
        />
        <PageComposer
          draft={draft}
          definitions={spec.figureTypes}
          maxPlots={spec.page.maxPlots}
          settingsFor={settingsFor}
          capabilities={availableCapabilities}
          nextId={nextId}
          dispatch={dispatch}
        />
        <aside className="figure-preview-panel" aria-label="Live preview and output">
          <section className="figure-preview-section">
            <div className="figure-section-heading">
              <div><strong>Live server preview</strong><span>Same renderer as final export</span></div>
              {(previewStatus === "waiting" || previewStatus === "rendering") && <span className="spinner small" />}
            </div>
            <div className="figure-preview-selectors">
              <label><span>Unit</span><select value={draft.previewClusterId} disabled={!draft.selectedUnitIds.length} onChange={(event) => dispatch({ type: "set-preview-unit", unitId: Number(event.target.value) })}>{draft.selectedUnitIds.map((unitId) => <option key={unitId} value={unitId}>cluster {unitId}</option>)}</select></label>
              <label><span>Page</span><select value={draft.previewPageId} onChange={(event) => dispatch({ type: "set-preview-page", pageId: event.target.value })}>{draft.pages.map((page, index) => <option key={page.id} value={page.id}>{index + 1}. {page.title || "Untitled"}</option>)}</select></label>
            </div>
            <div className="figure-preview-frame">
              {previewUrl && previewStatus !== "error" && <img src={previewUrl} alt={`Preview of ${previewPage.title} for cluster ${draft.previewClusterId}`} />}
              {!previewUrl && previewStatus !== "error" && <div><span className="spinner" /> Waiting for server preview…</div>}
              {previewStatus === "error" && <div className="error-state"><strong>Preview failed</strong><span>{previewError}</span></div>}
            </div>
            {placeholderCount > 0 && <p className="figure-placeholder-warning" role="status">Preview contains {placeholderCount} labeled unavailable-data placeholder{placeholderCount === 1 ? "" : "s"}.</p>}
          </section>

          <section className="figure-output-section">
            <div className="figure-section-heading"><div><strong>Output</strong><span>Server-side under /mnt/senzailab</span></div></div>
            <div className="figure-output-grid">
              <label><span>Format</span><select value={draft.format} onChange={(event) => dispatch({ type: "set-format", format: event.target.value as FigureComposerState["format"] })}>{spec.formats.map((format) => <option key={format} value={format}>{format.toUpperCase()}</option>)}</select></label>
              <label><span>Page order</span><select value={draft.order} onChange={(event) => dispatch({ type: "set-order", order: event.target.value as FigureComposerState["order"] })}>{spec.pageOrders.map((order) => <option key={order} value={order}>{order === "unit-major" ? "Unit, then page" : "Page, then unit"}</option>)}</select></label>
              <label className="figure-base-name"><span>{draft.format === "pdf" ? "PDF name" : "Folder name"}</span><input type="text" maxLength={128} value={draft.baseName} onChange={(event) => dispatch({ type: "set-base-name", value: event.target.value })} /><small>{draft.format === "pdf" ? ".pdf is added automatically" : "PNG pages plus manifest.json"}</small></label>
            </div>
            <DestinationBrowser draft={draft} listing={directoryListing} busy={directoryBusy} error={directoryError} dispatch={dispatch} />
            <label className="check-row figure-overwrite"><input type="checkbox" checked={draft.overwrite} onChange={(event) => dispatch({ type: "set-overwrite", value: event.target.checked })} /><span>Replace existing output with this exact name</span></label>
            {validationError && <p className="figure-validation-message">{validationError}</p>}
            {exportError && <p className="figure-inline-error" role="alert">{exportError}</p>}
            {exportResult && (
              <div className="figure-export-result" role="status">
                <strong>Export complete</strong>
                <code>{exportResult.path}</code>
                <span>{exportResult.pageCount} pages · {formatBytes(exportResult.bytes)}{exportResult.overwritten ? " · replaced previous output" : ""}</span>
                {resultPlaceholderCount > 0 && <span>{resultPlaceholderCount} unavailable-data placeholder{resultPlaceholderCount === 1 ? "" : "s"} recorded in the manifest.</span>}
              </div>
            )}
            <button className="figure-export-submit" type="button" disabled={Boolean(validationError) || exportStatus === "exporting"} onClick={() => void submitExport()}>
              {exportStatus === "exporting" ? <><span className="spinner small" /> Exporting {draft.selectedUnitIds.length * draft.pages.length} pages…</> : `Export ${draft.format.toUpperCase()}`}
            </button>
          </section>
        </aside>
      </div>
    </div>
  );
}
