/** One owner for replaceable loads, including responses already being decoded. */
export class LatestRequest {
  private controller: AbortController | null = null;

  begin(): AbortSignal {
    this.cancel();
    this.controller = new AbortController();
    return this.controller.signal;
  }

  isCurrent(signal: AbortSignal): boolean {
    return this.controller?.signal === signal && !signal.aborted;
  }

  cancel(): void {
    this.controller?.abort();
    this.controller = null;
  }
}

/** Preserve the scheduled deadline while replacing work with the latest state. */
export function createFrameScheduler(
  request: (callback: () => void) => number,
  cancel: (id: number) => void,
) {
  let frame: number | null = null;
  let pending: (() => void) | null = null;
  return {
    schedule(callback: () => void): void {
      pending = callback;
      if (frame !== null) return;
      frame = request(() => {
        frame = null;
        const draw = pending;
        pending = null;
        draw?.();
      });
    },
    cancel(): void {
      if (frame !== null) cancel(frame);
      frame = null;
      pending = null;
    },
  };
}

/** Unit navigation must not retain every large response buffer ever visited. */
export class UnitCountsCache {
  private values = new Map<number, Float64Array>();

  constructor(private readonly limit = 8) {}

  has(clusterId: number): boolean {
    return this.values.has(clusterId);
  }

  get(clusterId: number): Float64Array | undefined {
    const value = this.values.get(clusterId);
    if (value) {
      this.values.delete(clusterId);
      this.values.set(clusterId, value);
    }
    return value;
  }

  set(clusterId: number, values: Float64Array): void {
    this.values.delete(clusterId);
    this.values.set(clusterId, values);
    while (this.values.size > Math.max(1, this.limit)) {
      this.values.delete(this.values.keys().next().value!);
    }
  }
}
