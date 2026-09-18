import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getServerPaths, listRemoteFiles } from "./api";

describe("configured server paths", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it("uses the backend roots for source and export locations", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      rfRoot: "/mnt/recordings #2",
      outputRoot: "/mnt/exports",
      figureExportRoot: "/mnt/figures",
    })));
    const controller = new AbortController();
    expect(await getServerPaths(controller.signal)).toEqual({
      rfRoot: "/mnt/recordings #2",
      exportRoot: "/mnt/exports",
      figureExportRoot: "/mnt/figures",
    });
    expect(new URL(String(vi.mocked(fetch).mock.calls[0][0])).pathname).toMatch(/\/api\/health$/);
    expect(vi.mocked(fetch).mock.calls[0][1]?.signal).toBe(controller.signal);
  });

  it("lets the backend select its root for the initial file listing", async () => {
    const root = "/mnt/recordings #2";
    vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      root, path: root, entries: [], nextCursor: null,
    })));
    const page = await listRemoteFiles();
    const url = new URL(String(vi.mocked(fetch).mock.calls[0][0]));
    expect(url.searchParams.get("path")).toBe("");
    expect(page.root).toBe(root);
    expect(page.path).toBe(root);
  });
});
