import { baseBinMs } from "./math";
import type { DatasetMeta } from "./types";

export type ExportShortcutAction = "figure-composer" | "displayed-csv" | null;

export interface ExportShortcutEvent {
  key: string;
  metaKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
}

export function exportShortcutAction(event: ExportShortcutEvent): ExportShortcutAction {
  if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "e") return null;
  return event.shiftKey ? "displayed-csv" : "figure-composer";
}

export function steppedTimeResolutionMs(
  meta: DatasetMeta,
  currentResolutionMs: number,
  direction: -1 | 1,
): number {
  return currentResolutionMs + direction * baseBinMs(meta);
}
