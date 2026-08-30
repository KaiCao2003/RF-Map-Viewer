import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getUnitFilter } from "./api";

describe("unit filter API", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it("requests the RF selection window and parses native zero-bin counts", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({
      sourceBinRange: [1, 2],
      rfTimeRangeMs: [0, 200],
      zeroSpikeSpatialBinThreshold: 1,
      spatialBinCount: 4,
      comparison: "visible when zero-bin count is less than threshold",
      visibleUnitIds: [22],
      excludedUnitIds: [11],
      zeroSpikeSpatialBinCounts: [
        { unitId: 11, zeroBinCount: 1 },
        { unitId: 22, zeroBinCount: 0 },
      ],
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    const result = await getUnitFilter("dataset/id", 0, 200, 1);

    expect(result.visibleUnitIds).toEqual([22]);
    expect(result.zeroSpikeSpatialBinCounts[0]).toEqual({ unitId: 11, zeroBinCount: 1 });
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("datasets/dataset%2Fid/unit-filter");
    expect(url).toContain("rfStartMs=0");
    expect(url).toContain("rfEndMs=200");
    expect(url).toContain("zeroSpikeSpatialBinThreshold=1");
  });
});
