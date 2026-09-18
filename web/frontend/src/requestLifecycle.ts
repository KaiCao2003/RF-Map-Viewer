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

/** Keep one read active and replace queued navigation with the newest unit. */
export class LatestSerialRead {
  private active = false;
  private revision = 0;
  private pending: (() => Promise<void>) | null = null;

  submit<T>(read: () => Promise<T>, result: (value: T) => void, failure: (error: unknown) => void, done: () => void): void {
    const revision = ++this.revision;
    this.pending = async () => {
      this.active = true;
      try {
        const value = await read();
        if (revision === this.revision) result(value);
      } catch (error) {
        if (revision === this.revision) failure(error);
      } finally {
        if (revision === this.revision) done();
        this.active = false;
        this.run();
      }
    };
    this.run();
  }

  cancel(): void { this.revision += 1; this.pending = null; }

  private run(): void {
    if (this.active || !this.pending) return;
    const read = this.pending;
    this.pending = null;
    void read();
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
