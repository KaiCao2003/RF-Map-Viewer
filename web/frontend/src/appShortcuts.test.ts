import { describe, expect, it } from "vitest";
import type { DatasetMeta } from "./types";
import { exportShortcutAction, steppedTimeResolutionMs } from "./appShortcuts";

const meta: DatasetMeta = {
  id: "fixture",
  name: "fixture.json",
  sourcePath: "/mnt/senzailab/fixture.json",
  shape: [1, 1, 1, 4],
  unitPool: [17],
  xPositions: [0],
  yPositions: [0],
  timeBinEdges: [-0.005, -0.0025, 0, 0.0025, 0.005],
  occupancyTimeSec: [[1]],
  responseUnits: "spike_count",
  responseNormalization: "none",
  capabilities: { probe: false, hd: false, waveform: false, occupancy: true },
};

describe("viewer keyboard shortcut parity", () => {
  it("steps time resolution by one RF source bin", () => {
    expect(steppedTimeResolutionMs(meta, 5, -1)).toBeCloseTo(2.5);
    expect(steppedTimeResolutionMs(meta, 5, 1)).toBeCloseTo(7.5);
  });

  it("routes Command/Ctrl+Shift+E to displayed CSV export", () => {
    expect(exportShortcutAction({ key: "E", metaKey: true, ctrlKey: false, shiftKey: true }))
      .toBe("displayed-csv");
    expect(exportShortcutAction({ key: "e", metaKey: false, ctrlKey: true, shiftKey: true }))
      .toBe("displayed-csv");
  });

  it("keeps unshifted Command/Ctrl+E on the Figure Composer", () => {
    expect(exportShortcutAction({ key: "e", metaKey: true, ctrlKey: false, shiftKey: false }))
      .toBe("figure-composer");
    expect(exportShortcutAction({ key: "e", metaKey: false, ctrlKey: false, shiftKey: true }))
      .toBeNull();
  });
});
