import { describe, expect, it, vi } from "vitest";
import { createFrameScheduler, LatestRequest, UnitCountsCache } from "./requestLifecycle";

describe("replaceable file loads", () => {
  it("aborts the previous request and rejects its late decoded response", () => {
    const requests = new LatestRequest();
    const older = requests.begin();
    const latest = requests.begin();
    expect(older.aborted).toBe(true);
    expect(requests.isCurrent(older)).toBe(false);
    expect(requests.isCurrent(latest)).toBe(true);
    requests.cancel();
    expect(latest.aborted).toBe(true);
    expect(requests.isCurrent(latest)).toBe(false);
  });
});

describe("coalesced canvas rendering", () => {
  it("keeps its deadline during continuous changes and draws the latest state", () => {
    const callbacks: Array<() => void> = [];
    const request = vi.fn((callback: () => void) => callbacks.push(callback));
    const scheduler = createFrameScheduler(request, vi.fn());
    const obsolete = vi.fn();
    const latest = vi.fn();
    scheduler.schedule(obsolete);
    for (let change = 0; change < 100; change += 1) scheduler.schedule(latest);
    expect(request).toHaveBeenCalledTimes(1);
    callbacks[0]();
    expect(obsolete).not.toHaveBeenCalled();
    expect(latest).toHaveBeenCalledTimes(1);
    scheduler.schedule(latest);
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("cancels queued work when a plot unmounts", () => {
    let callback: () => void = () => undefined;
    const cancel = vi.fn();
    const draw = vi.fn();
    const scheduler = createFrameScheduler((next) => { callback = next; return 7; }, cancel);
    scheduler.schedule(draw);
    scheduler.cancel();
    expect(cancel).toHaveBeenCalledWith(7);
    callback();
    expect(draw).not.toHaveBeenCalled();
  });
});

describe("unit response cache", () => {
  it("evicts the least recently viewed response while retaining neighbors", () => {
    const cache = new UnitCountsCache(2);
    const first = new Float64Array([1]);
    cache.set(1, first);
    cache.set(2, new Float64Array([2]));
    expect(cache.get(1)).toBe(first);
    cache.set(3, new Float64Array([3]));
    expect(cache.has(1)).toBe(true);
    expect(cache.has(2)).toBe(false);
    expect(cache.has(3)).toBe(true);
  });
});
