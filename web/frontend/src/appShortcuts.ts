import { baseBinMs } from "./math";
import type { DatasetMeta, RfOverlayMode } from "./types";

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

export function rfOverlapShortcut(event: Pick<KeyboardEvent, "code" | "shiftKey" | "metaKey" | "ctrlKey" | "altKey">): RfOverlayMode | null {
  if (!event.shiftKey || event.metaKey || event.ctrlKey || event.altKey) return null;
  if (event.code === "Digit1") return "1D";
  if (event.code === "Digit2") return "2D";
  if (event.code === "Digit0") return "None";
  return null;
}
