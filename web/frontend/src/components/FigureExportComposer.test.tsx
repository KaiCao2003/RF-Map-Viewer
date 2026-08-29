import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import FigureExportComposer from "./FigureExportComposer";
import { FIGURE_TYPE_IDS, type FigureExportSpec } from "../figureExport";
import type { DatasetMeta, HdViewSettings, ViewState } from "../types";

const meta: DatasetMeta = {
  id: "dataset",
  name: "session.json",
  sourcePath: "/mnt/senzailab/session/rf.json",
  shape: [3, 2, 2, 3],
  unitPool: [41, 7, 88],
  xPositions: [-10, 10],
  yPositions: [-5, 5],
  timeBinEdges: [-0.1, 0, 0.1, 0.2],
  occupancyTimeSec: [[1, 1], [1, 1]],
  responseUnits: "spike_count",
  responseNormalization: "none",
  capabilities: { probe: false, hd: false, waveform: false, occupancy: true },
};

const view: ViewState = {
  clusterId: 7,
  valueMode: "Spike count",
  activeTimeCenterMs: 50,
  timelineStartMs: -100,
  timelineEndMs: 200,
  timelineAnchorMs: null,
  rfStartMs: 0,
  rfEndMs: 200,
  timeResolutionMs: 100,
  xBins: 2,
  yBins: 2,
  smoothRadius: 0,
  flipY: false,
  palette: "Gray",
  polarRadius: "Display bottom inner",
  polarLayout: false,
  rgbMode: false,
  selectedCellYMidpoint: 0,
  selectedCellXMidpoint: 0,
  timelineScrollFraction: 0,
  selectedTab: "rf",
};

const hd: HdViewSettings = {
  plotMode: "auto",
  displayBins: 30,
  smoothing: true,
  sigmaDeg: 18,
  compareScale: false,
};

const spec: FigureExportSpec = {
  specVersion: 1,
  figureTypes: FIGURE_TYPE_IDS.map((id) => ({
    id,
    label: id,
    family: id.split(".")[0],
    projection: id.split(".")[1] ?? "cartesian",
    settings: {},
    ...(id.startsWith("hd.")
      ? { capability: "hd" as const }
      : id === "probe"
        ? { capability: "probe" as const }
        : id === "waveform.local_average"
          ? { capability: "waveform" as const }
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

describe("FigureExportComposer", () => {
  it("renders a full-screen composer with explicit unit, page, preview, and destination controls", () => {
    const html = renderToStaticMarkup(
      <FigureExportComposer
        meta={meta}
        viewState={view}
        selectedCell={[0, 0, 0, 0]}
        hdSettings={hd}
        probeFilteredUnitIds={[7, 88]}
        availableCapabilities={{ hd: false, probe: false, waveform: false }}
        hdPath={null}
        probePositionsPath={null}
        tuningSession={1}
        waveformChannelMode="same_x_column"
        initialSpec={spec}
        onClose={() => undefined}
      />,
    );

    expect(html).toContain("Figure Export Composer");
    expect(html).toContain("Unit selection presets");
    expect(html).toContain("Probe filtered (2)");
    expect(html).toContain('role="listbox"');
    expect(html).toContain('aria-multiselectable="true"');
    expect(html).toContain('aria-label="000 cluster 41"');
    expect(html).toContain("Command/Ctrl-click toggles; Shift-click selects a range");
    expect(html).toContain("Page templates");
    expect(html).toContain('aria-label="Move Page 1 earlier"');
    expect(html).toContain('aria-label="Move Page 1 later"');
    expect(html).toContain("Live server preview");
    expect(html).toContain("Same renderer as final export");
    expect(html).toContain("/mnt/senzailab");
    expect(html).toContain("Replace existing output");
    for (const id of FIGURE_TYPE_IDS) expect(html).toContain(`value="${id}"`);
    expect(html).toContain("hd.line — unavailable");
    expect(html).not.toContain("Dataset views");
  });

  it("shows manually attached companions as shared preview/export inputs", () => {
    const html = renderToStaticMarkup(
      <FigureExportComposer
        meta={meta}
        viewState={view}
        selectedCell={null}
        hdSettings={hd}
        probeFilteredUnitIds={null}
        availableCapabilities={{ hd: true, probe: true, waveform: true }}
        hdPath="/mnt/senzailab/session/tuning_curves.json"
        probePositionsPath="/mnt/senzailab/session/positions.csv"
        tuningSession={2}
        waveformChannelMode="same_shank"
        initialSpec={spec}
        onClose={() => undefined}
      />,
    );

    expect(html).toContain("Live preview and final export use the same manually attached companion files");
    expect(html).toContain("tuning_curves.json");
    expect(html).toContain("positions.csv");
  });
});
