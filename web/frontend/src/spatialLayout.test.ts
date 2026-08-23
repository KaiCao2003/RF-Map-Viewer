import { describe, expect, it } from "vitest";
import {
  polarRingSpan,
  spatialCellAt,
  spatialGridDimensions,
  type PolarSpatialLayout,
  type RectSpatialLayout,
} from "./spatialLayout";

describe("spatial plot geometry", () => {
  it("uses the legacy 30-by-7 visual aspect for singleton-y maps", () => {
    const dimensions = spatialGridDimensions(900, 600, 120, 1);

    expect(dimensions.gridWidth / dimensions.gridHeight).toBeCloseTo(30 / 7, 12);
    expect(dimensions.cellWidth * 120).toBeCloseTo(dimensions.gridWidth, 12);
    expect(dimensions.cellHeight).toBeCloseTo(dimensions.gridHeight, 12);
  });

  it("preserves square cells for maps with multiple y rows", () => {
    const dimensions = spatialGridDimensions(900, 600, 30, 7);

    expect(dimensions.cellWidth).toBeCloseTo(dimensions.cellHeight, 12);
    expect(dimensions.gridWidth / dimensions.gridHeight).toBeCloseTo(30 / 7, 12);
  });

  it("hit-tests the complete stretched singleton row", () => {
    const layout: RectSpatialLayout = {
      kind: "rect",
      x: 10,
      y: 20,
      cellWidth: 2,
      cellHeight: 28,
      width: 4,
      height: 28,
      xGroups: [[0, 0], [1, 1]],
      yGroups: [[0, 0]],
    };

    expect(spatialCellAt(layout, 13, 47)).toEqual([0, 0, 1, 1]);
  });

  it("uses and hit-tests a seven-unit singleton polar ring", () => {
    const layout: PolarSpatialLayout = {
      kind: "polar",
      cx: 20,
      cy: 20,
      scale: 1,
      totalDegrees: 360,
      ringSpan: polarRingSpan(1),
      xGroups: [[0, 0]],
      yGroups: [[0, 0]],
      ringRows: [0],
    };

    expect(layout.ringSpan).toBe(7);
    expect(spatialCellAt(layout, 30, 20)).toEqual([0, 0, 0, 0]);
    expect(spatialCellAt(layout, 31, 20)).toBeNull();
    expect(polarRingSpan(7)).toBe(1);
  });
});
