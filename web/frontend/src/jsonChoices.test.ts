import { describe, expect, it } from "vitest";
import {
  formatJsonTimestamp,
  jsonChoiceLabel,
  mergeJsonChoices,
  urlForJsonSource,
} from "./jsonChoices";
import type { FsEntry } from "./types";

describe("Current JSON choices", () => {
  it("filters RF files, deduplicates fallbacks, and sorts by mtime descending", () => {
    const discovered: FsEntry[] = [
      { name: "old.json", path: "/mnt/senzailab/session/old.json", type: "file", size: 1, mtime: 100 },
      { name: "notes.txt", path: "/mnt/senzailab/session/notes.txt", type: "file", size: 1, mtime: 500 },
      { name: "new.JSON", path: "/mnt/senzailab/session/new.JSON", type: "file", size: 1, mtime: 300 },
      { name: "current.rfmap", path: "/mnt/senzailab/session/current.rfmap", type: "file", size: 1, mtime: 400 },
      { name: "tuning_curves.json", path: "/mnt/senzailab/session/tuning_curves.json", type: "file", size: 1, mtime: 600 },
    ];
    expect(mergeJsonChoices(discovered, "/mnt/senzailab/session/old.json", [
      "/mnt/senzailab/recent.json",
      "/mnt/senzailab/session/new.JSON",
    ])).toEqual([
      { path: "/mnt/senzailab/session/current.rfmap", mtime: 400 },
      { path: "/mnt/senzailab/session/new.JSON", mtime: 300 },
      { path: "/mnt/senzailab/session/old.json", mtime: 100 },
      { path: "/mnt/senzailab/recent.json", mtime: null },
    ]);
  });

  it("renders compact relative labels with a Tk-style local timestamp", () => {
    const seconds = new Date(2026, 7, 3, 9, 5).getTime() / 1000;
    expect(formatJsonTimestamp(seconds)).toBe("2026-08-03 09:05");
    expect(jsonChoiceLabel(
      { path: "/mnt/senzailab/session/rf.json", mtime: seconds },
      "/mnt/senzailab/session",
    )).toBe("rf.json  2026-08-03 09:05");
  });

  it("sets and replaces the current JSON query without dropping other parameters", () => {
    expect(urlForJsonSource("http://viewer/rfmapping/?mode=test&json=old", "/mnt/senzailab/a b.json"))
      .toBe("http://viewer/rfmapping/?mode=test&json=%2Fmnt%2Fsenzailab%2Fa+b.json");
  });
});
