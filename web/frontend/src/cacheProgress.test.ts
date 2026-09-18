import { afterEach, describe, expect, it, vi } from "vitest";
import { startCacheProgressPolling } from "./cacheProgress";
import type { CacheProgress } from "./types";

const pending: CacheProgress = { indexed: true, cachedUnits: 1, totalUnits: 3, complete: false, error: null };
const complete: CacheProgress = { ...pending, cachedUnits: 3, complete: true };

afterEach(() => vi.useRealTimers());

describe("indexed cache progress", () => {
  it("recovers after a failed request and continues until the cache completes", async () => {
    vi.useFakeTimers();
    const read = vi.fn()
      .mockRejectedValueOnce(new Error("Temporary network failure"))
      .mockResolvedValueOnce(pending)
      .mockResolvedValueOnce(complete);
    const updated = vi.fn();
    const failed = vi.fn();
    const stop = startCacheProgressPolling(read, updated, failed);
    await vi.advanceTimersByTimeAsync(200);
    expect(failed).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(updated).toHaveBeenLastCalledWith(pending);
    await vi.advanceTimersByTimeAsync(500);
    expect(updated).toHaveBeenLastCalledWith(complete);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(read).toHaveBeenCalledTimes(3);
    stop();
  });

  it("cancels an in-flight request and cannot publish stale progress after switching files", async () => {
    vi.useFakeTimers();
    let finish: (progress: CacheProgress) => void = () => undefined;
    const read = vi.fn((_signal: AbortSignal) => new Promise<CacheProgress>((resolve) => { finish = resolve; }));
    const updated = vi.fn();
    const failed = vi.fn();
    const stop = startCacheProgressPolling(read, updated, failed);
    await vi.advanceTimersByTimeAsync(200);
    stop();
    expect(read.mock.calls[0][0].aborted).toBe(true);
    finish(complete);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(updated).not.toHaveBeenCalled();
    expect(failed).not.toHaveBeenCalled();
    expect(read).toHaveBeenCalledOnce();
  });
});
