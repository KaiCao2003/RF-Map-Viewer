import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { WaveformArtifact } from "../types";
import WaveformPanel from "./WaveformPanel";

const available: WaveformArtifact = {
  available: true,
  sourcePath: "/recording/data/waveform/ProbeA",
  unitId: 17,
  quality: "good",
  totalSpikeCount: 1000,
  selectedSpikeCount: 500,
  timeCoveragePercent: 90,
  maxPtpUv: 48,
  mode: "same_x_column",
  localChannelCount: 2,
  baselineEndMs: -0.25,
  timesMs: [-0.5, -0.25, 0, 0.25],
  timeEdgesMs: [-0.625, -0.375, -0.125, 0.125, 0.375],
  valuesUv: [[-2, -1, 1, 2], [-4, -2, 2, 4]],
  channels: [
    { channelIndex: 1, channelId: 101, rawChannelIndex: 1, xUm: 0, yUm: 40, shankId: 0 },
    { channelIndex: 2, channelId: 102, rawChannelIndex: 2, xUm: 0, yUm: 60, shankId: 0 },
  ],
  channelLabels: ["ch 101", "ch 102"],
  bestChannelIndex: 2,
  bestChannelRow: 1,
  amplitudeLimitUv: 4,
};

describe("WaveformPanel", () => {
  it("renders the compact waveform controls and unit summary", () => {
    const html = renderToStaticMarkup(
      <WaveformPanel
        artifact={available}
        clusterId={17}
        loading={false}
        error=""
        visible
        mode="same_x_column"
        blocked={false}
        onVisibleChange={() => undefined}
        onModeChange={() => undefined}
      />,
    );

    expect(html).toContain("Local waveform");
    expect(html).toContain("Same x column");
    expect(html).toContain("Same shank");
    expect(html).toContain("500/1000 spikes");
    expect(html).toContain("best ch 102");
    expect(html).toContain("best + 1 nearest");
    expect(html).toContain("max PTP 48 µV");
    expect(html).toContain("Local average waveform for cluster 17");
  });

  it("keeps a validated unavailable detail explicit", () => {
    const html = renderToStaticMarkup(
      <WaveformPanel
        artifact={{ available: false, detail: "Unit 42 is outside the good waveform scope." }}
        clusterId={42}
        loading={false}
        error=""
        visible
        mode="same_shank"
        blocked={false}
        onVisibleChange={() => undefined}
        onModeChange={() => undefined}
      />,
    );

    expect(html).toContain("Unit 42 is outside the good waveform scope.");
  });
});
