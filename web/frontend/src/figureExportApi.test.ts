import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  exportFigurePlan,
  getFigureExportSpec,
  listFigureExportDirectories,
  previewFigureExport,
} from "./api";
import { FIGURE_TYPE_IDS, type FigureExportRequest, type FigurePreviewRequest } from "./figureExport";

const specPayload = {
  specVersion: 1,
  figureTypes: FIGURE_TYPE_IDS.map((id) => ({
    id,
    label: id,
    family: id.split(".")[0],
    projection: id.split(".")[1] ?? "cartesian",
    settings: {},
    ...(id.startsWith("hd.")
      ? { capability: "hd" }
      : id === "probe"
        ? { capability: "probe" }
        : id === "waveform.local_average"
          ? { capability: "waveform" }
          : {}),
  })),
  pageOrders: ["unit-major", "page-major"],
  formats: ["pdf", "png"],
  page: {
    minPlots: 1,
    maxPlots: 12,
    default: { title: "", plots: [{ type: "rf.cartesian", settings: {} }] },
  },
};

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("figure export API", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("document", { cookie: "rfmapping_csrf=test-token" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads and validates the shared registry and destination directories", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(specPayload))
      .mockResolvedValueOnce(jsonResponse({
        path: "session",
        writable: true,
        entries: [{ name: "nested", path: "session/nested", writable: false }],
      }));

    const spec = await getFigureExportSpec();
    const directories = await listFigureExportDirectories("session");

    expect(spec.figureTypes.map((item) => item.id)).toEqual(FIGURE_TYPE_IDS);
    expect(spec.page.maxPlots).toBe(12);
    expect(directories).toEqual({
      path: "session",
      writable: true,
      entries: [{ name: "nested", path: "session/nested", writable: false }],
    });
    expect(String(fetchMock.mock.calls[1][0])).toContain("figure-exports/directories?path=session");
  });

  it("posts companion-aware preview plans and exposes placeholder headers", async () => {
    fetchMock.mockResolvedValueOnce(new Response(new Uint8Array([137, 80, 78, 71]), {
      status: 200,
      headers: {
        "Content-Type": "image/png",
        "X-RF-Render-SHA256": "abc123",
        "X-RF-Placeholder-Count": "2",
        "X-RF-Cluster-Id": "7",
        "X-RF-Page-Index": "0",
      },
    }));
    const request: FigurePreviewRequest = {
      specVersion: 1,
      clusterId: 7,
      pageIndex: 0,
      pages: [{ title: "Page", plots: [{ type: "hd.line", settings: {} }] }],
      hdPath: "/mnt/senzailab/session/tuning_curves.json",
      tuningSession: 1,
      waveformChannelMode: "same_x_column",
    };

    const result = await previewFigureExport("dataset/id", request);

    expect(result.placeholderCount).toBe(2);
    expect(result.sha256).toBe("abc123");
    const [url, init] = fetchMock.mock.calls[0] as [URL, RequestInit];
    expect(String(url)).toContain("datasets/dataset%2Fid/figure-exports/preview");
    expect(JSON.parse(String(init.body))).toEqual(request);
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("test-token");
  });

  it("normalizes final export status and manifest placeholders", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      format: "pdf",
      path: "/mnt/senzailab/session/report.pdf",
      pageCount: 1,
      bytes: 2048,
      overwritten: false,
      manifest: {
        specVersion: 1,
        format: "pdf",
        order: "unit-major",
        source: "/mnt/senzailab/session/rf.json",
        pages: [{
          outputIndex: 0,
          clusterId: 7,
          unitIndex: 1,
          pageIndex: 0,
          title: "Page",
          file: "report.pdf",
          sha256: null,
          placeholders: ["HD unavailable"],
        }],
      },
    }));
    const request: FigureExportRequest = {
      specVersion: 1,
      clusterIds: [7],
      order: "unit-major",
      format: "pdf",
      pages: [{ title: "Page", plots: [{ type: "rf.cartesian", settings: {} }] }],
      tuningSession: 1,
      waveformChannelMode: "same_x_column",
      destination: { directory: "session", baseName: "report", overwrite: false },
    };

    const result = await exportFigurePlan("dataset", request);

    expect(result.path).toBe("/mnt/senzailab/session/report.pdf");
    expect(result.manifest.pages[0].placeholders).toEqual(["HD unavailable"]);
    expect(JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body))).toEqual(request);
  });
});
