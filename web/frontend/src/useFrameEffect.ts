import { useEffect, useRef } from "react";
import { createFrameScheduler } from "./requestLifecycle";

/** Canvas work follows at most once per display frame, using the latest props. */
export function useFrameEffect(draw: () => void): void {
  const scheduler = useRef<ReturnType<typeof createFrameScheduler> | null>(null);
  useEffect(() => {
    scheduler.current ??= createFrameScheduler(
      (callback) => window.requestAnimationFrame(callback),
      (id) => window.cancelAnimationFrame(id),
    );
    scheduler.current.schedule(draw);
  }, [draw]);
  useEffect(() => () => scheduler.current?.cancel(), []);
}
