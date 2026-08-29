import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getWaveformArtifact } from "./api";

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const validPayload = {
  available: true,
  sourcePath: "/recording/data/waveform/ProbeA",
  unitId: 17,
  quality: "good",
  totalSpikeCount: 1000,
  selectedSpikeCount: 500,
  timeCoveragePercent: 90,
  maxPtpUv: 48,
  mode: "same_shank",
  localChannelCount: 2,
  baselineEndMs: -0.25,
  timesMs: [-0.5, 0],
  timeEdgesMs: [-0.75, -0.25, 0.25],
  valuesUv: [[-2, 2], [-4, 4]],
  channels: [
    { channelIndex: 1, channelId: 101, rawChannelIndex: 1, xUm: 0, yUm: 40, shankId: 0 },
    { channelIndex: 2, channelId: 102, rawChannelIndex: 2, xUm: 0, yUm: 60, shankId: 0 },
  ],
  channelLabels: ["ch 101", "ch 102"],
  bestChannelIndex: 2,
  bestChannelRow: 1,
  amplitudeLimitUv: 4,
};

describe("waveform API", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("normalizes a strict waveform payload and sends the channel mode", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(validPayload));

    const artifact = await getWaveformArtifact("dataset", 17, "same_shank");

    expect(artifact.available).toBe(true);
    if (!artifact.available) throw new Error("Expected an available waveform");
    expect(artifact.maxPtpUv).toBe(48);
    expect(artifact.bestChannelRow).toBe(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain("mode=same_shank");
  });

  it("preserves an explicit unavailable detail", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      available: false,
      detail: "Unit 17 is outside the good waveform scope.",
    }));

    await expect(getWaveformArtifact("dataset", 17, "same_x_column"))
      .resolves.toEqual({
        available: false,
        detail: "Unit 17 is outside the good waveform scope.",
      });
  });

  it("rejects inconsistent channel/time dimensions", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      ...validPayload,
      valuesUv: [[-2, 2]],
    }));

    await expect(getWaveformArtifact("dataset", 17, "same_shank"))
      .rejects.toThrow(/inconsistent time or channel dimensions/);
  });
});
