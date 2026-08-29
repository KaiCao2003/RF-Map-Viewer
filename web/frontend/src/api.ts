import type {
  DatasetMeta,
  FsPage,
  HdDatasetArtifact,
  HdUnitArtifact,
  ProbeGeometry,
  WaveformArtifact,
  WaveformChannelMode,
} from "./types";
import type { RemoteFileKind } from "./fileFormats";
import {
  FIGURE_TYPE_IDS,
  isFigureTypeId,
  type FigureDirectoryListing,
  type FigureExportRequest,
  type FigureExportResult,
  type FigureExportSpec,
  type FigureManifestPage,
  type FigureOutputFormat,
  type FigurePageOrder,
  type FigurePagePayload,
  type FigurePreviewRequest,
  type FigureSettingDefinition,
  type FigureTypeDefinition,
} from "./figureExport";

const runtimeOrigin = typeof window === "undefined" ? "http://localhost" : window.location.origin;
const appBase = new URL(import.meta.env.BASE_URL, runtimeOrigin);
const apiBase = new URL("api/", appBase);
const csrfCookieName = "rfmapping_csrf";
const unsafeMethods = new Set(["POST", "PUT", "PATCH", "DELETE"]);

type UnknownRecord = Record<string, unknown>;

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function apiUrl(path: string): string {
  return new URL(path.replace(/^\/+/, ""), apiBase).toString();
}

function cookieValue(name: string): string {
  if (typeof document === "undefined") return "";
  const prefix = `${encodeURIComponent(name)}=`;
  for (const part of document.cookie.split(";")) {
    const candidate = part.trim();
    if (candidate.startsWith(prefix)) return decodeURIComponent(candidate.slice(prefix.length));
  }
  return "";
}

function protectedFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const method = String(init.method ?? "GET").toUpperCase();
  if (!unsafeMethods.has(method)) return fetch(input, init);
  const csrfToken = cookieValue(csrfCookieName);
  if (!csrfToken) {
    throw new ApiError("The login security token is missing; reload the page.");
  }
  const headers = new Headers(init.headers);
  headers.set("X-CSRF-Token", csrfToken);
  return fetch(input, { ...init, headers });
}

function record(value: unknown): UnknownRecord {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as UnknownRecord)
    : {};
}

function first(source: UnknownRecord, ...names: string[]): unknown {
  for (const name of names) {
    if (source[name] !== undefined) return source[name];
  }
  return undefined;
}

function numbers(value: unknown): number[] {
  return Array.isArray(value) ? value.map(Number) : [];
}

function nullableNumbers(value: unknown): Array<number | null> {
  if (!Array.isArray(value)) return [];
  return value.map((item) => {
    if (item == null) return null;
    const parsed = Number(item);
    return Number.isFinite(parsed) ? parsed : null;
  });
}

function finiteNumber(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function requiredFiniteNumber(value: unknown, label: string): number {
  if (value == null || typeof value === "boolean" || (typeof value === "string" && value.trim() === "")) {
    throw new ApiError(`${label} must be finite.`);
  }
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) throw new ApiError(`${label} must be finite.`);
  return parsed;
}

type ProbeUnitPosition = { x: number; y: number } | { x: null; y: null };

function probeUnitPosition(item: UnknownRecord): ProbeUnitPosition {
  const xRaw = first(item, "x");
  const yRaw = first(item, "y", "depth");
  if (xRaw === null && yRaw === null) return { x: null, y: null };
  if (xRaw == null || yRaw == null) {
    throw new ApiError("Probe unit x and y must both be finite or both be null.");
  }
  return {
    x: requiredFiniteNumber(xRaw, "Probe unit x"),
    y: requiredFiniteNumber(yRaw, "Probe unit y"),
  };
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const payload = await response.clone().json();
    const detail = first(record(payload), "detail", "message", "error");
    if (typeof detail === "string") return detail;
    if (detail != null) return JSON.stringify(detail);
  } catch {
    try {
      const text = await response.text();
      if (text.trim()) return text.trim();
    } catch {
      // Use the HTTP status fallback below.
    }
  }
  return `${response.status} ${response.statusText}`.trim();
}

async function checked(response: Response): Promise<Response> {
  if (response.status === 401) {
    if (typeof window === "undefined") throw new ApiError("Authentication required", 401);
    const login = new URL("login", appBase);
    login.searchParams.set("next", `${window.location.pathname}${window.location.search}`);
    window.location.assign(login);
    throw new ApiError("Authentication required", 401);
  }
  if (!response.ok) throw new ApiError(await errorMessage(response), response.status);
  return response;
}

function normalizeMeta(payload: unknown): DatasetMeta {
  const source = record(payload);
  const shapeValues = numbers(source.shape);
  if (shapeValues.length !== 4) throw new ApiError("Dataset metadata has an invalid four-dimensional shape.");
  const shape = shapeValues.map((item) => Math.trunc(item)) as [number, number, number, number];
  const capabilitiesSource = record(source.capabilities);
  const occupancyRaw = source.occupancyTimeSec;
  const occupancyTimeSec = Array.isArray(occupancyRaw)
    ? occupancyRaw.map((row) => (Array.isArray(row) ? row.map(Number) : []))
    : [];
  const meta: DatasetMeta = {
    id: String(source.id ?? ""),
    name: String(source.name ?? "RF dataset"),
    sourcePath: String(source.sourcePath ?? ""),
    shape,
    unitPool: numbers(source.unitPool),
    xPositions: numbers(source.xPositions),
    yPositions: numbers(source.yPositions),
    timeBinEdges: numbers(source.timeBinEdges),
    occupancyTimeSec,
    isVerticalBar: typeof source.isVerticalBar === "boolean" ? source.isVerticalBar : undefined,
    responseUnits: source.responseUnits as "spike_count",
    responseNormalization: source.responseNormalization as "none",
    capabilities: {
      probe: capabilitiesSource.probe === true,
      hd: capabilitiesSource.hd === true,
      waveform: capabilitiesSource.waveform === true,
      occupancy: capabilitiesSource.occupancy === true,
    },
  };
  if (!meta.id || meta.unitPool.length !== shape[0]) {
    throw new ApiError("Dataset metadata is missing an id or complete unit list.");
  }
  if (
    meta.xPositions.length !== shape[2] ||
    meta.yPositions.length !== shape[1] ||
    meta.timeBinEdges.length !== shape[3] + 1 ||
    meta.occupancyTimeSec.length !== shape[1] ||
    meta.occupancyTimeSec.some((row) => row.length !== shape[2]) ||
    meta.occupancyTimeSec.some((row) => row.some((value) => !Number.isFinite(value) || value < 0)) ||
    meta.responseUnits !== "spike_count" ||
    meta.responseNormalization !== "none" ||
    !meta.capabilities.occupancy
  ) {
    throw new ApiError("Dataset metadata does not match the required occupancy-normalized RF contract.");
  }
  return meta;
}

export async function listRemoteFiles(
  path = "/mnt/senzailab",
  cursor?: string,
  signal?: AbortSignal,
  kind: RemoteFileKind = "rf-json",
): Promise<FsPage> {
  const url = new URL("fs/list", apiBase);
  url.searchParams.set("path", path);
  url.searchParams.set("limit", "200");
  url.searchParams.set("kind", kind);
  if (cursor) url.searchParams.set("cursor", cursor);
  const response = await checked(await protectedFetch(url, { signal }));
  const payload = record(await response.json());
  const entriesRaw = Array.isArray(payload.entries) ? payload.entries : [];
  return {
    root: String(first(payload, "root") ?? "/mnt/senzailab"),
    path: String(first(payload, "path") ?? path),
    entries: entriesRaw.map((raw) => {
      const entry = record(raw);
      const rawType = String(first(entry, "type", "kind") ?? "file");
      return {
        name: String(first(entry, "name") ?? ""),
        path: String(first(entry, "path") ?? ""),
        type: rawType === "directory" || rawType === "dir" ? "directory" : "file",
        size: first(entry, "size") == null ? null : finiteNumber(first(entry, "size")),
        mtime: first(entry, "mtime", "modified") == null ? null : finiteNumber(first(entry, "mtime", "modified")),
      };
    }),
    nextCursor:
      first(payload, "nextCursor", "next_cursor") == null
        ? null
        : String(first(payload, "nextCursor", "next_cursor")),
  };
}

export async function openRemoteDataset(path: string, signal?: AbortSignal): Promise<DatasetMeta> {
  const response = await checked(
    await protectedFetch(new URL("datasets/open", apiBase), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
      signal,
    }),
  );
  return normalizeMeta(await response.json());
}

export async function getDatasetMeta(id: string, signal?: AbortSignal): Promise<DatasetMeta> {
  const response = await checked(
    await protectedFetch(new URL(`datasets/${encodeURIComponent(id)}/meta`, apiBase), { signal }),
  );
  return normalizeMeta(await response.json());
}

function decodeLittleEndian(buffer: ArrayBuffer): Float64Array {
  if (buffer.byteLength % 8 !== 0) throw new ApiError("Unit response is not aligned to float64 values.");
  const view = new DataView(buffer);
  const values = new Float64Array(buffer.byteLength / 8);
  for (let index = 0; index < values.length; index += 1) {
    values[index] = view.getFloat64(index * 8, true);
  }
  return values;
}

function flattenJsonUnit(value: unknown, output: number[]): void {
  if (Array.isArray(value)) {
    for (const child of value) flattenJsonUnit(child, output);
    return;
  }
  output.push(Number(value));
}

export async function getUnitCounts(
  meta: DatasetMeta,
  clusterId: number,
  signal?: AbortSignal,
): Promise<Float64Array> {
  const response = await checked(
    await protectedFetch(
      new URL(
        `datasets/${encodeURIComponent(meta.id)}/units/${encodeURIComponent(String(clusterId))}`,
        apiBase,
      ),
      { signal },
    ),
  );
  const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
  let values: Float64Array;
  if (contentType.includes("json")) {
    const raw = await response.json();
    const source = record(raw);
    const flattened: number[] = [];
    flattenJsonUnit(first(source, "counts", "data", "unit") ?? raw, flattened);
    values = Float64Array.from(flattened);
  } else {
    values = decodeLittleEndian(await response.arrayBuffer());
  }
  const expected = meta.shape[1] * meta.shape[2] * meta.shape[3];
  if (values.length !== expected) {
    throw new ApiError(`Unit ${clusterId} returned ${values.length} values; expected ${expected}.`);
  }
  return values;
}

export interface DisplayedCsvRequest {
  clusterId: number;
  valueMode: string;
  rfStartMs: number;
  rfEndMs: number;
  timeResolutionMs: number;
  xBins: number;
  yBins: number;
  smoothRadius: number;
  flipY: boolean;
  palette: string;
  outputPath?: string;
  overwrite?: boolean;
}

export interface SavedArtifactResult {
  path: string;
  name: string;
  bytes: number;
  rows?: number;
  overwritten: boolean;
}

export async function exportDisplayedCsv(
  datasetId: string,
  request: DisplayedCsvRequest,
  signal?: AbortSignal,
): Promise<SavedArtifactResult> {
  const response = await checked(
    await protectedFetch(new URL(`datasets/${encodeURIComponent(datasetId)}/exports/displayed-csv`, apiBase), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal,
    }),
  );
  const payload = record(await response.json());
  return {
    path: String(first(payload, "path") ?? ""),
    name: String(first(payload, "name") ?? ""),
    bytes: finiteNumber(first(payload, "bytes")),
    rows: first(payload, "rows") == null ? undefined : finiteNumber(first(payload, "rows")),
    overwritten: Boolean(first(payload, "overwritten")),
  };
}

export async function saveHdImage(
  datasetId: string,
  clusterId: number,
  options: { outputPath?: string; overwrite?: boolean } = {},
  signal?: AbortSignal,
): Promise<SavedArtifactResult> {
  const response = await checked(
    await protectedFetch(new URL(`datasets/${encodeURIComponent(datasetId)}/hd/${encodeURIComponent(String(clusterId))}/save-image`, apiBase), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(options),
      signal,
    }),
  );
  const payload = record(await response.json());
  return {
    path: String(first(payload, "path") ?? ""),
    name: String(first(payload, "name") ?? ""),
    bytes: finiteNumber(first(payload, "bytes")),
    overwritten: Boolean(first(payload, "overwritten")),
  };
}

export async function getProbeGeometry(
  id: string,
  options: { positionsPath?: string } = {},
  signal?: AbortSignal,
): Promise<ProbeGeometry> {
  const url = new URL(`datasets/${encodeURIComponent(id)}/probe`, apiBase);
  if (options.positionsPath) url.searchParams.set("path", options.positionsPath);
  const response = await checked(
    await protectedFetch(url, { signal }),
  );
  const payload = record(await response.json());
  const channels = Array.isArray(payload.channels) ? payload.channels : [];
  const units = Array.isArray(payload.units) ? payload.units : [];
  const seenUnitIds = new Set<number>();
  return {
    probe: String(first(payload, "probe", "probeName", "probe_name") ?? "Probe"),
    channels: channels.map((raw) => {
      const item = record(raw);
      return {
        channelId: finiteNumber(first(item, "channelId", "channel_id", "channel")),
        x: finiteNumber(first(item, "x")),
        y: finiteNumber(first(item, "y", "depth")),
        shank: finiteNumber(first(item, "shank", "shankId", "shank_id")),
      };
    }),
    units: units.map((raw) => {
      const item = record(raw);
      const position = probeUnitPosition(item);
      const unitId = requiredFiniteNumber(
        first(item, "unitId", "unit_id", "clusterId", "cluster_id"),
        "Probe unit ID",
      );
      if (!Number.isInteger(unitId)) throw new ApiError("Probe unit ID must be an integer.");
      if (seenUnitIds.has(unitId)) throw new ApiError(`Duplicate Probe unit ID ${unitId}.`);
      seenUnitIds.add(unitId);
      return {
        unitId,
        ...position,
      };
    }),
  };
}

function normalizeHdUnit(payload: unknown, fallbackUnitId?: number): HdUnitArtifact | null {
  const source = record(payload);
  const unitIdRaw = first(source, "unitId", "unit_id", "clusterId", "cluster_id") ?? fallbackUnitId;
  const unitId = Number(unitIdRaw);
  if (!Number.isFinite(unitId)) return null;
  const rates = nullableNumbers(first(source, "rates", "firingRateHz", "firing_rate_hz"));
  const countsRaw = first(source, "spikeCounts", "spike_counts");
  if (!Array.isArray(countsRaw)) return null;
  const counts = countsRaw.map((value) => finiteNumber(value));
  const hdClassRaw = first(source, "hdClass", "hd_class");
  return {
    unitId,
    rates,
    spikeCounts: counts,
    hdClass: hdClassRaw == null ? null : finiteNumber(hdClassRaw),
  };
}

function normalizeHdDataset(payload: unknown): HdDatasetArtifact {
  const source = record(payload);
  const unitsRaw = Array.isArray(source.units) ? source.units : [];
  const units = unitsRaw
    .map((unit) => normalizeHdUnit(unit))
    .filter((unit): unit is HdUnitArtifact => unit != null);
  const occupancyRaw = first(source, "occupancyTimeS", "occupancy_time_s");
  const metadataRaw = first(source, "metadata");
  return {
    available: Boolean(first(source, "available")),
    sourcePath: first(source, "sourcePath", "source_path", "path") == null
      ? null
      : String(first(source, "sourcePath", "source_path", "path")),
    occupancyTimeS: Array.isArray(occupancyRaw) ? occupancyRaw.map((value) => finiteNumber(value)) : null,
    units,
    metadata: metadataRaw && typeof metadataRaw === "object" && !Array.isArray(metadataRaw)
      ? metadataRaw as Record<string, unknown>
      : null,
  };
}

export async function getHdDataset(
  id: string,
  path?: string,
  session = 1,
  signal?: AbortSignal,
): Promise<HdDatasetArtifact> {
  const url = new URL(`datasets/${encodeURIComponent(id)}/hd`, apiBase);
  if (path) url.searchParams.set("path", path);
  url.searchParams.set("session", String(session));
  const response = await checked(await protectedFetch(url, { signal }));
  return normalizeHdDataset(await response.json());
}

export async function getHdArtifact(
  id: string,
  clusterId: number,
  path?: string,
  session = 1,
  signal?: AbortSignal,
): Promise<HdDatasetArtifact> {
  const url = new URL(`datasets/${encodeURIComponent(id)}/hd/${encodeURIComponent(String(clusterId))}`, apiBase);
  if (path) url.searchParams.set("path", path);
  url.searchParams.set("session", String(session));
  const response = await checked(
    await protectedFetch(url, { signal }),
  );
  const payload = record(await response.json());
  const normalized = normalizeHdDataset(payload);
  const unit = normalizeHdUnit(payload, clusterId);
  return {
    ...normalized,
    units: normalized.units.length ? normalized.units : unit ? [unit] : [],
  };
}

function normalizeWaveformArtifact(payload: unknown): WaveformArtifact {
  const source = record(payload);
  if (source.available !== true) {
    return {
      available: false,
      detail: String(source.detail ?? "Local average waveform is unavailable."),
    };
  }
  const mode = source.mode === "same_shank" ? "same_shank" : "same_x_column";
  const timesMs = numbers(source.timesMs);
  const timeEdgesMs = numbers(source.timeEdgesMs);
  const rows = Array.isArray(source.valuesUv)
    ? source.valuesUv.map((row) => numbers(row))
    : [];
  const labels = Array.isArray(source.channelLabels)
    ? source.channelLabels.map((value) => String(value))
    : [];
  const channels = Array.isArray(source.channels) ? source.channels.map((raw) => {
    const item = record(raw);
    return {
      channelIndex: requiredFiniteNumber(item.channelIndex, "Waveform channel index"),
      channelId: requiredFiniteNumber(item.channelId, "Waveform channel ID"),
      rawChannelIndex: requiredFiniteNumber(item.rawChannelIndex, "Waveform raw channel index"),
      xUm: requiredFiniteNumber(item.xUm, "Waveform channel x"),
      yUm: requiredFiniteNumber(item.yUm, "Waveform channel y"),
      shankId: requiredFiniteNumber(item.shankId, "Waveform shank ID"),
    };
  }) : [];
  const bestChannelRow = requiredFiniteNumber(source.bestChannelRow, "Waveform best channel row");
  if (
    !timesMs.length
    || timeEdgesMs.length !== timesMs.length + 1
    || rows.length !== channels.length
    || rows.length !== labels.length
    || rows.some((row) => row.length !== timesMs.length)
    || !Number.isInteger(bestChannelRow)
    || bestChannelRow < 0
    || bestChannelRow >= rows.length
  ) {
    throw new ApiError("Waveform artifact has inconsistent time or channel dimensions.");
  }
  return {
    available: true,
    sourcePath: String(source.sourcePath ?? ""),
    unitId: requiredFiniteNumber(source.unitId, "Waveform unit ID"),
    quality: String(source.quality ?? ""),
    totalSpikeCount: requiredFiniteNumber(source.totalSpikeCount, "Waveform total spike count"),
    selectedSpikeCount: requiredFiniteNumber(source.selectedSpikeCount, "Waveform selected spike count"),
    timeCoveragePercent: requiredFiniteNumber(source.timeCoveragePercent, "Waveform time coverage"),
    maxPtpUv: requiredFiniteNumber(source.maxPtpUv, "Waveform maximum peak-to-peak amplitude"),
    mode,
    localChannelCount: requiredFiniteNumber(source.localChannelCount, "Waveform local channel count"),
    baselineEndMs: requiredFiniteNumber(source.baselineEndMs, "Waveform baseline end"),
    timesMs,
    timeEdgesMs,
    valuesUv: rows,
    channels,
    channelLabels: labels,
    bestChannelIndex: requiredFiniteNumber(source.bestChannelIndex, "Waveform best channel index"),
    bestChannelRow,
    amplitudeLimitUv: requiredFiniteNumber(source.amplitudeLimitUv, "Waveform amplitude limit"),
  };
}

export async function getWaveformArtifact(
  id: string,
  clusterId: number,
  mode: WaveformChannelMode,
  signal?: AbortSignal,
): Promise<WaveformArtifact> {
  const url = new URL(
    `datasets/${encodeURIComponent(id)}/waveform/${encodeURIComponent(String(clusterId))}`,
    apiBase,
  );
  url.searchParams.set("mode", mode);
  const response = await checked(await protectedFetch(url, { signal }));
  return normalizeWaveformArtifact(await response.json());
}

function normalizeFigureSetting(payload: unknown): FigureSettingDefinition {
  const source = record(payload);
  const type = String(first(source, "type"));
  if (!["number", "integer", "boolean", "string", "object"].includes(type)) {
    throw new ApiError(`Figure setting has unsupported type ${type}.`);
  }
  const choices = Array.isArray(source.choices) ? source.choices : undefined;
  return {
    type: type as FigureSettingDefinition["type"],
    default: source.default,
    minimum: source.minimum == null ? undefined : finiteNumber(source.minimum),
    maximum: source.maximum == null ? undefined : finiteNumber(source.maximum),
    choices,
    description: String(source.description ?? ""),
  };
}

function normalizeFigurePlot(payload: unknown): FigurePagePayload["plots"][number] {
  const source = record(payload);
  const type = String(source.type ?? "");
  if (!isFigureTypeId(type)) throw new ApiError(`Unknown figure type ${type || "(empty)"}.`);
  return {
    type,
    settings: record(source.settings),
  };
}

function normalizeFigurePage(payload: unknown): FigurePagePayload {
  const source = record(payload);
  const plots = Array.isArray(source.plots) ? source.plots.map(normalizeFigurePlot) : [];
  if (!plots.length) throw new ApiError("Figure spec default page has no plots.");
  return { title: String(source.title ?? ""), plots };
}

function normalizeFigureExportSpec(payload: unknown): FigureExportSpec {
  const source = record(payload);
  const definitions = Array.isArray(source.figureTypes)
    ? source.figureTypes.map((raw): FigureTypeDefinition => {
      const item = record(raw);
      const id = String(item.id ?? "");
      if (!isFigureTypeId(id)) throw new ApiError(`Unknown figure type ${id || "(empty)"}.`);
      const capability = item.capability === "hd"
        || item.capability === "probe"
        || item.capability === "waveform"
        ? item.capability
        : undefined;
      return {
        id,
        label: String(item.label ?? id),
        family: String(item.family ?? ""),
        projection: String(item.projection ?? ""),
        settings: Object.fromEntries(
          Object.entries(record(item.settings)).map(([name, setting]) => [
            name,
            normalizeFigureSetting(setting),
          ]),
        ),
        capability,
      };
    })
    : [];
  const available = new Set(definitions.map((definition) => definition.id));
  const missing = FIGURE_TYPE_IDS.filter((id) => !available.has(id));
  if (missing.length) throw new ApiError(`Figure spec is missing: ${missing.join(", ")}.`);
  const page = record(source.page);
  const formats = Array.isArray(source.formats)
    ? source.formats.filter((value): value is FigureOutputFormat => value === "pdf" || value === "png")
    : [];
  const pageOrders = Array.isArray(source.pageOrders)
    ? source.pageOrders.filter((value): value is FigurePageOrder => value === "unit-major" || value === "page-major")
    : [];
  if (!formats.length || !pageOrders.length) throw new ApiError("Figure export formats or page orders are missing.");
  return {
    specVersion: Math.trunc(finiteNumber(source.specVersion, -1)),
    figureTypes: definitions,
    formats,
    pageOrders,
    page: {
      minPlots: Math.trunc(finiteNumber(page.minPlots, 1)),
      maxPlots: Math.trunc(finiteNumber(page.maxPlots, 12)),
      default: normalizeFigurePage(page.default),
    },
  };
}

export async function getFigureExportSpec(signal?: AbortSignal): Promise<FigureExportSpec> {
  const response = await checked(
    await protectedFetch(new URL("figure-exports/spec", apiBase), { signal }),
  );
  return normalizeFigureExportSpec(await response.json());
}

export async function listFigureExportDirectories(
  path = "",
  signal?: AbortSignal,
): Promise<FigureDirectoryListing> {
  const url = new URL("figure-exports/directories", apiBase);
  url.searchParams.set("path", path);
  const response = await checked(await protectedFetch(url, { signal }));
  const payload = record(await response.json());
  const entries = Array.isArray(payload.entries) ? payload.entries : [];
  return {
    path: String(payload.path ?? ""),
    writable: Boolean(payload.writable),
    entries: entries.map((raw) => {
      const entry = record(raw);
      return {
        name: String(entry.name ?? ""),
        path: String(entry.path ?? ""),
        writable: Boolean(entry.writable),
      };
    }),
  };
}

export interface FigurePreviewResult {
  image: Blob;
  sha256: string;
  placeholderCount: number;
  clusterId: number;
  pageIndex: number;
}

export async function previewFigureExport(
  datasetId: string,
  request: FigurePreviewRequest,
  signal?: AbortSignal,
): Promise<FigurePreviewResult> {
  const response = await checked(
    await protectedFetch(
      new URL(`datasets/${encodeURIComponent(datasetId)}/figure-exports/preview`, apiBase),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
        signal,
      },
    ),
  );
  return {
    image: await response.blob(),
    sha256: response.headers.get("X-RF-Render-SHA256") ?? "",
    placeholderCount: Math.max(0, Math.trunc(finiteNumber(response.headers.get("X-RF-Placeholder-Count")))),
    clusterId: Math.trunc(finiteNumber(response.headers.get("X-RF-Cluster-Id"), request.clusterId)),
    pageIndex: Math.trunc(finiteNumber(response.headers.get("X-RF-Page-Index"), request.pageIndex)),
  };
}

function normalizeFigureManifestPage(payload: unknown): FigureManifestPage {
  const source = record(payload);
  return {
    outputIndex: Math.trunc(finiteNumber(source.outputIndex)),
    clusterId: Math.trunc(finiteNumber(source.clusterId)),
    unitIndex: Math.trunc(finiteNumber(source.unitIndex)),
    pageIndex: Math.trunc(finiteNumber(source.pageIndex)),
    title: String(source.title ?? ""),
    file: source.file == null ? null : String(source.file),
    sha256: source.sha256 == null ? null : String(source.sha256),
    placeholders: Array.isArray(source.placeholders)
      ? source.placeholders.map((value) => String(value))
      : [],
  };
}

export async function exportFigurePlan(
  datasetId: string,
  request: FigureExportRequest,
  signal?: AbortSignal,
): Promise<FigureExportResult> {
  const response = await checked(
    await protectedFetch(
      new URL(`datasets/${encodeURIComponent(datasetId)}/figure-exports`, apiBase),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
        signal,
      },
    ),
  );
  const payload = record(await response.json());
  const manifest = record(payload.manifest);
  const format: FigureOutputFormat = payload.format === "png" ? "png" : "pdf";
  const order: FigurePageOrder = manifest.order === "page-major" ? "page-major" : "unit-major";
  return {
    format,
    path: String(payload.path ?? ""),
    pageCount: Math.trunc(finiteNumber(payload.pageCount)),
    bytes: finiteNumber(payload.bytes),
    overwritten: Boolean(payload.overwritten),
    manifest: {
      specVersion: Math.trunc(finiteNumber(manifest.specVersion)),
      format: manifest.format === "png" ? "png" : "pdf",
      order,
      source: String(manifest.source ?? ""),
      pages: Array.isArray(manifest.pages)
        ? manifest.pages.map(normalizeFigureManifestPage)
        : [],
    },
  };
}
