import { describe, expect, it } from "vitest";
import { DEFAULT_VIEWER_SETTINGS, invalidSettingsSection, loadViewerSettings, saveViewerSettings, viewerSettingsPatch, VIEWER_SETTINGS_KEY } from "./viewerSettings";
import type { DatasetMeta } from "./types";

function storage(entries: Record<string, string> = {}) {
  const values = new Map(Object.entries(entries));
  return { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } };
}

const meta: DatasetMeta = {
  id: "test", name: "synthetic", sourcePath: "/synthetic.rfmap", shape: [1, 1, 2, 5],
  unitPool: [17], xPositions: [-90, 90], yPositions: [0], timeBinEdges: [-0.1, 0, 0.1, 0.2, 0.3, 0.4],
  occupancyTimeSec: [[1, 1]], responseUnits: "spike_count", responseNormalization: "none",
  capabilities: { probe: false, hd: false, waveform: false, occupancy: true },
};

describe("viewer settings", () => {
  it("preserves the separate preferences saved by existing browsers", () => {
    const saved = storage({
      "rfmapping-companion-preferences-v1": JSON.stringify({ tuningSession: 3, showWaveform: false, waveformChannelMode: "same_shank" }),
      "rfmapping-zero-bin-unit-filter-v1": JSON.stringify({ enabled: false, zeroSpikeSpatialBinThreshold: 8 }),
      "rfmapping-hd-layout-v1": "stacked",
    });
    expect(loadViewerSettings(saved)).toMatchObject({ tuningSession: 3, showWaveform: false, waveformChannelMode: "same_shank", filterUnits: false, zeroBinThreshold: 8, hdLayout: "stacked" });
  });

  it("round trips all four settings groups without losing HD or RF defaults", () => {
    const saved = storage();
    const settings = { ...DEFAULT_VIEWER_SETTINGS, showHd: false, autoLoadProbe: false,
      valueMode: "Spike count" as const, palette: "Viridis" as const, xBins: 5,
      hd: { ...DEFAULT_VIEWER_SETTINGS.hd, displayBins: 30, plotMode: "polar" as const, compareScale: true } };
    saveViewerSettings(saved, settings);
    expect(loadViewerSettings(saved)).toEqual(settings);
  });

  it("restores malformed and unsupported saved values individually", () => {
    const saved = storage({ [VIEWER_SETTINGS_KEY]: JSON.stringify({ timeResolutionMs: 0, xBins: -1, tuningSession: 0,
      showHd: "false", palette: "invalid", hd: { displayBins: 31, sigmaDeg: -5 }, timing: { mode: "sum", sum: [1, 1], a: [80, 160], b: [0, 80] } }) });
    expect(loadViewerSettings(saved)).toMatchObject({ timeResolutionMs: 1, xBins: 0, tuningSession: 1,
      showHd: true, palette: "Gray", hd: { displayBins: 30, sigmaDeg: DEFAULT_VIEWER_SETTINGS.hd.sigmaDeg }, timing: DEFAULT_VIEWER_SETTINGS.timing });
    expect(loadViewerSettings(storage({ [VIEWER_SETTINGS_KEY]: "broken" }))).toEqual(DEFAULT_VIEWER_SETTINGS);
  });

  it("keeps later sidebar preferences after a Settings save", () => {
    const saved = storage();
    saveViewerSettings(saved, DEFAULT_VIEWER_SETTINGS);
    saved.setItem("rfmapping-companion-preferences-v1", JSON.stringify({ tuningSession: 2, showWaveform: false, waveformChannelMode: "same_shank" }));
    saved.setItem("rfmapping-zero-bin-unit-filter-v1", JSON.stringify({ enabled: false, zeroSpikeSpatialBinThreshold: 5 }));
    saved.setItem("rfmapping-hd-layout-v1", "stacked");
    expect(loadViewerSettings(saved)).toMatchObject({ tuningSession: 2, showWaveform: false, waveformChannelMode: "same_shank", filterUnits: false, zeroBinThreshold: 5, hdLayout: "stacked" });
  });

  it("validates values in inactive settings sections before applying them", () => {
    expect(invalidSettingsSection(DEFAULT_VIEWER_SETTINGS)).toBeNull();
    expect(invalidSettingsSection({ ...DEFAULT_VIEWER_SETTINGS, tuningSession: 0 })).toBe("Tuning Curve");
    expect(invalidSettingsSection({ ...DEFAULT_VIEWER_SETTINGS, hd: { ...DEFAULT_VIEWER_SETTINGS.hd, sigmaDeg: NaN } })).toBe("Tuning Curve");
    expect(invalidSettingsSection({ ...DEFAULT_VIEWER_SETTINGS, xBins: -1 })).toBe("RF Map");
    expect(invalidSettingsSection({ ...DEFAULT_VIEWER_SETTINGS, zeroBinThreshold: 1.5 })).toBe("RF Map");
    expect(invalidSettingsSection({ ...DEFAULT_VIEWER_SETTINGS, timing: { ...DEFAULT_VIEWER_SETTINGS.timing, sum: [1, 1] } })).toBe("RF Map");
  });

  it("adapts saved spatial bins and timing to each dataset without filtering its timeline", () => {
    const patch = viewerSettingsPatch(meta, { ...DEFAULT_VIEWER_SETTINGS, xBins: 10, yBins: 0, timeResolutionMs: 150 });
    expect(patch).toMatchObject({ xBins: 2, yBins: 1, rfStartMs: 0, rfEndMs: 200 });
    expect(patch.timeResolutionMs).toBeCloseTo(200);
    expect(patch).not.toHaveProperty("timelineStartMs");
    expect(patch).not.toHaveProperty("timelineEndMs");
    expect(patch).not.toHaveProperty("clusterId");
  });
});
