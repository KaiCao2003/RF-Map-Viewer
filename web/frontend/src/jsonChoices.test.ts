import { describe, expect, it } from "vitest";
import {
  formatJsonTimestamp,
  jsonChoiceLabel,
  mergeJsonChoices,
  parentDirectory,
  urlForJsonSource,
} from "./jsonChoices";
import type { FsEntry } from "./types";

describe("Current JSON choices", () => {
  it("keeps RF and companion browsing beside sources under the configured data root", () => {
    const root = "/mnt/lab recordings/#SessionData";
    expect(parentDirectory(`${root}/session/results.rfmap`)).toBe(`${root}/session`);
    expect(parentDirectory(`${root}/session/tuning_curves.tc`)).toBe(`${root}/session`);
    expect(parentDirectory(`${root}/session/positions.probe`)).toBe(`${root}/session`);
    expect(parentDirectory("/results.rfmap")).toBe("/");
    expect(jsonChoiceLabel({ path: `${root}/other/results.rfmap`, mtime: null }, `${root}/session`, root))
      .toBe("other/results.rfmap");
    expect(jsonChoiceLabel({ path: `${root}/other/results.rfmap`, mtime: null }, `${root}/session`))
      .toBe(`${root}/other/results.rfmap`);
  });

  it("filters RF files, deduplicates fallbacks, and sorts by mtime descending", () => {
    const discovered: FsEntry[] = [
      { name: "old.json", path: "/data/rfmapping/session/old.json", type: "file", size: 1, mtime: 100 },
      { name: "notes.txt", path: "/data/rfmapping/session/notes.txt", type: "file", size: 1, mtime: 500 },
      { name: "new.JSON", path: "/data/rfmapping/session/new.JSON", type: "file", size: 1, mtime: 300 },
      { name: "current.rfmap", path: "/data/rfmapping/session/current.rfmap", type: "file", size: 1, mtime: 400 },
      { name: "tuning_curves.json", path: "/data/rfmapping/session/tuning_curves.json", type: "file", size: 1, mtime: 600 },
    ];
    expect(mergeJsonChoices(discovered, "/data/rfmapping/session/old.json", [
      "/data/rfmapping/recent.json",
      "/data/rfmapping/session/new.JSON",
    ])).toEqual([
      { path: "/data/rfmapping/session/current.rfmap", mtime: 400 },
      { path: "/data/rfmapping/session/new.JSON", mtime: 300 },
      { path: "/data/rfmapping/session/old.json", mtime: 100 },
      { path: "/data/rfmapping/recent.json", mtime: null },
    ]);
  });

  it("renders compact relative labels with a Tk-style local timestamp", () => {
    const seconds = new Date(2026, 7, 3, 9, 5).getTime() / 1000;
    expect(formatJsonTimestamp(seconds)).toBe("2026-08-03 09:05");
    expect(jsonChoiceLabel(
      { path: "/data/rfmapping/session/rf.json", mtime: seconds },
      "/data/rfmapping/session",
    )).toBe("rf.json  2026-08-03 09:05");
  });

  it("sets and replaces the current JSON query without dropping other parameters", () => {
    expect(urlForJsonSource("http://viewer/rfmapping/?mode=test&json=old", "/data/rfmapping/a b.json"))
      .toBe("http://viewer/rfmapping/?mode=test&json=%2Fdata%2Frfmapping%2Fa+b.json");
  });
});
