import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getProbeGeometry } from "./api";

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("probe geometry API", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("preserves finite positions and explicit null/null positions", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      probe: "ProbeA",
      channels: [],
      units: [
        { unitId: 11, x: 10, y: 20 },
        { unitId: 22, x: null, y: null },
      ],
    }));

    const geometry = await getProbeGeometry("dataset");

    expect(geometry.units).toEqual([
      { unitId: 11, x: 10, y: 20 },
      { unitId: 22, x: null, y: null },
    ]);
  });

  it.each([
    { unitId: 22, x: null, y: 20 },
    { unitId: 22, x: 10, y: null },
    { unitId: 22, x: "bad", y: "bad" },
    { unitId: 22, x: "Infinity", y: "Infinity" },
    { unitId: 22 },
  ])("rejects malformed coordinate pairs: %o", async (unit) => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      probe: "ProbeA",
      channels: [],
      units: [unit],
    }));

    await expect(getProbeGeometry("dataset")).rejects.toThrow(/Probe unit/);
  });

  it.each([
    { units: [{ unitId: 1.5, x: 10, y: 20 }] },
    {
      units: [
        { unitId: 22, x: 10, y: 20 },
        { unitId: 22, x: 30, y: 40 },
      ],
    },
  ])("rejects invalid or duplicate unit IDs: $units", async ({ units }) => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ probe: "ProbeA", channels: [], units }));

    await expect(getProbeGeometry("dataset")).rejects.toThrow(/unit ID/i);
  });
});
