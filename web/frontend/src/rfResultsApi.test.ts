import { afterEach, expect, it, vi } from "vitest";
import { getDatasetMeta, getSavedRfResult } from "./api";

const response = (payload: unknown) => new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
afterEach(() => vi.unstubAllGlobals());

it("requests the selected saved sidecar and an explicit reload", async () => {
  const payload = { available: false, dimension: "1d", polarity: "inhibitory", resultPath: "/recording/rf_inhibitory_1d.npz", detail: "Missing sidecar" };
  const fetchMock = vi.fn().mockResolvedValue(response(payload));
  vi.stubGlobal("fetch", fetchMock);
  const controller = new AbortController();
  expect(await getSavedRfResult("abc", "1d", "inhibitory", true, controller.signal)).toEqual(payload);
  const [url, init] = fetchMock.mock.calls[0];
  expect(new URL(url).pathname).toMatch(/\/api\/datasets\/abc\/rf-results$/);
  expect(new URL(url).searchParams.get("force_reload")).toBe("true");
  expect(new URL(url).searchParams.get("polarity")).toBe("inhibitory");
  expect(init.signal).toBe(controller.signal);
});

it("retains optional presentation counts and rejects invalid count matrices", async () => {
  const payload = { id: "d", name: "data", sourcePath: "/source", shape: [1, 1, 2, 1], unitPool: [3],
    xPositions: [0, 1], yPositions: [0], timeBinEdges: [0, 0.1], occupancyTimeSec: [[10, 20]],
    stimulusPresentationCounts: [[100, 200]], responseUnits: "spike_count", responseNormalization: "none", capabilities: { occupancy: true } };
  const fetchMock = vi.fn().mockResolvedValueOnce(response(payload))
    .mockResolvedValueOnce(response({ ...payload, stimulusPresentationCounts: null }))
    .mockResolvedValueOnce(response({ ...payload, stimulusPresentationCounts: [[0.5, 3]] }));
  vi.stubGlobal("fetch", fetchMock);
  expect((await getDatasetMeta("d")).stimulusPresentationCounts).toEqual([[100, 200]]);
  expect((await getDatasetMeta("d")).stimulusPresentationCounts).toBeNull();
  await expect(getDatasetMeta("d")).rejects.toThrow("RF contract");
});
