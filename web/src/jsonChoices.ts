import type { FsEntry } from "./types";

export interface JsonChoice {
  path: string;
  mtime: number | null;
}

export function sortJsonChoices(choices: JsonChoice[]): JsonChoice[] {
  const byPath = new Map<string, JsonChoice>();
  choices.forEach((choice) => {
    const existing = byPath.get(choice.path);
    if (!existing || (existing.mtime == null && choice.mtime != null)) byPath.set(choice.path, choice);
  });
  return [...byPath.values()].sort((left, right) => {
    const modified = (right.mtime ?? 0) - (left.mtime ?? 0);
    if (modified) return modified;
    const leftName = left.path.split("/").at(-1) ?? left.path;
    const rightName = right.path.split("/").at(-1) ?? right.path;
    return leftName === rightName ? 0 : leftName < rightName ? 1 : -1;
  });
}

export function mergeJsonChoices(
  discovered: FsEntry[],
  currentPath: string,
  recentPaths: string[],
): JsonChoice[] {
  return sortJsonChoices([
    ...discovered
      .filter((entry) => entry.type === "file" && /\.json$/i.test(entry.name))
      .map((entry) => ({ path: entry.path, mtime: entry.mtime })),
    { path: currentPath, mtime: null },
    ...recentPaths.map((path) => ({ path, mtime: null })),
  ]);
}

export function formatJsonTimestamp(seconds: number | null): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return "";
  const date = new Date(seconds * 1000);
  const two = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())} ${two(date.getHours())}:${two(date.getMinutes())}`;
}

export function jsonChoiceLabel(
  choice: JsonChoice,
  currentFolder: string,
  root = "/mnt/senzailab",
): string {
  const folderPrefix = `${currentFolder.replace(/\/+$/, "")}/`;
  const rootPrefix = `${root.replace(/\/+$/, "")}/`;
  const relative = choice.path.startsWith(folderPrefix)
    ? choice.path.slice(folderPrefix.length)
    : choice.path.startsWith(rootPrefix) ? choice.path.slice(rootPrefix.length) : choice.path;
  const timestamp = formatJsonTimestamp(choice.mtime);
  return timestamp ? `${relative}  ${timestamp}` : relative;
}

export function urlForJsonSource(href: string, sourcePath: string): string {
  const url = new URL(href);
  url.searchParams.set("json", sourcePath);
  return url.toString();
}
