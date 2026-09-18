import type { CacheProgress } from "./types";

/** Poll one dataset until complete; temporary request failures remain retryable. */
export function startCacheProgressPolling(
  read: (signal: AbortSignal) => Promise<CacheProgress>,
  update: (progress: CacheProgress) => void,
  failed: (error: unknown) => void,
): () => void {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout>;
  const poll = async () => {
    let complete = false;
    let delay = 500;
    try {
      const progress = await read(controller.signal);
      if (controller.signal.aborted) return;
      complete = progress.complete;
      update(progress);
    } catch (error) {
      if (controller.signal.aborted) return;
      failed(error);
      delay = 1_000;
    } finally {
      if (!controller.signal.aborted && !complete) timer = setTimeout(poll, delay);
    }
  };
  timer = setTimeout(poll, 200);
  return () => { controller.abort(); clearTimeout(timer); };
}
