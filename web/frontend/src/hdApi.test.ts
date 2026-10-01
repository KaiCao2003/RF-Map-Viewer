import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getHdArtifact, getHdDataset } from "./api";

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const raw = (value: number) => Array.from({ length: 180 }, () => value);

describe("HD tuning API", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("retains rate-only units and absent scientific observations", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      available: true,
      sourcePath: "/recording/tuning_curves.tc",
      occupancyTimeS: null,
      metadata: null,
      units: [{ unitId: 7, rates: raw(4), spikeCounts: null, hdClass: null }],
    }));

    const artifact = await getHdDataset("dataset");

    expect(artifact.occupancyTimeS).toBeNull();
    expect(artifact.units).toEqual([{ unitId: 7, rates: raw(4), spikeCounts: null, hdClass: null }]);
    expect(artifact.metadata).toBeNull();
  });

  it("keeps Class 3, missing raw rates, and classification provenance", async () => {
    const rates: Array<number | null> = raw(2);
    rates[0] = null;
    const counts = raw(2);
    counts[0] = 0;
    const occupancy = raw(1);
    occupancy[0] = 0;
    const metadata = { classification: { class_3: "Rayleigh + shuffle + kappa", kappa_cutoff: 0.075 } };
    fetchMock.mockResolvedValueOnce(jsonResponse({
      available: true,
      source_path: "/recording/tuning_curves.json",
      occupancy_time_s: occupancy,
      metadata,
      units: [{ unit_id: 99, firing_rate_hz: rates, spike_counts: counts, hd_class: 3 }],
    }));

    const artifact = await getHdDataset("dataset");

    expect(artifact.units[0]).toEqual({ unitId: 99, rates, spikeCounts: counts, hdClass: 3 });
    expect(artifact.occupancyTimeS).toEqual(occupancy);
    expect(artifact.metadata).toEqual(metadata);
  });

  it("retains a rate-only single-unit response", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      available: true,
      unitId: 7,
      rates: raw(4),
      spikeCounts: null,
      occupancyTimeS: null,
      hdClass: null,
    }));

    const artifact = await getHdArtifact("dataset", 7);

    expect(artifact.units).toEqual([{ unitId: 7, rates: raw(4), spikeCounts: null, hdClass: null }]);
  });
});
