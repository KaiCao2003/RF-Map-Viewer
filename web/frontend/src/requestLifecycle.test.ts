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

it("serializes waveform reads and keeps only the latest queued unit", async () => {
  const { LatestSerialRead } = await import("./requestLifecycle");
  const queue = new LatestSerialRead();
  let resolveFirst: (value: number) => void = () => undefined;
  const first = new Promise<number>((resolve) => { resolveFirst = resolve; });
  const calls: number[] = [];
  const results: number[] = [];
  const failed = vi.fn();
  const done = vi.fn();
  queue.submit(() => { calls.push(1); return first; }, (v) => results.push(v), failed, done);
  queue.submit(async () => { calls.push(2); return 2; }, (v) => results.push(v), failed, done);
  queue.submit(async () => { calls.push(3); return 3; }, (v) => results.push(v), failed, done);
  expect(calls).toEqual([1]);
  resolveFirst(1);
  await first;
  await Promise.resolve();
  expect(calls).toEqual([1, 3]);
  expect(results).toEqual([3]);
  expect(done).toHaveBeenCalledTimes(1);
  expect(failed).not.toHaveBeenCalled();
});
