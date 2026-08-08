import { describe, expect, it } from "vitest";
import {
  timelineBinAtTime,
  timelineGridLayout,
  timelineIntervalLabel,
  timelinePositionFraction,
} from "./timelineLayout";

describe("timeline layout math", () => {
  it("keeps real m17 30×7 maps readable by reducing columns instead of shrinking maps", () => {
    const narrow = timelineGridLayout({ width: 480, count: 100, xCount: 30, yCount: 7 });
    const medium = timelineGridLayout({ width: 1024, count: 100, xCount: 30, yCount: 7 });
    const wide = timelineGridLayout({ width: 1440, count: 100, xCount: 30, yCount: 7 });
    expect(narrow.columns).toBe(1);
    expect(narrow.cell).toBeGreaterThanOrEqual(10);
    expect(medium.columns).toBe(1);
    expect(medium.gridHeight).toBeGreaterThanOrEqual(120);
    expect(wide.columns).toBe(2);
    expect(wide.cell).toBeGreaterThanOrEqual(10);
    expect(wide.gridHeight).toBeGreaterThanOrEqual(120);
    expect(wide.rows).toBe(Math.ceil(100 / wide.columns));
  });

  it("uses at most four columns for square or polar previews", () => {
    const layout = timelineGridLayout({ width: 1600, count: 240, xCount: 14, yCount: 14 });
    expect(layout.columns).toBe(4);
    expect(layout.gridHeight).toBeGreaterThanOrEqual(120);
  });

  it("falls back to one column on narrow viewports", () => {
    expect(timelineGridLayout({ width: 480, count: 20, xCount: 11, yCount: 3 }).columns).toBe(1);
  });

  it("labels every map with its complete physical interval", () => {
    expect(timelineIntervalLabel(-100, -90, String)).toBe("-100–-90 ms");
  });

  it("positions data and hit testing by physical time", () => {
    expect(timelinePositionFraction(25, 0, 100)).toBe(0.25);
    expect(timelineBinAtTime(20, [[0, 10], [10, 30], [30, 100]])).toBe(1);
    expect(timelineBinAtTime(100, [[0, 10], [10, 30], [30, 100]])).toBe(2);
  });
});
