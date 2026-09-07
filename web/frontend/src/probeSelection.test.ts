import { describe, expect, it } from "vitest";
import { nearestProbeUnitToRegionCenter, probeUnitsInRegion } from "./probeSelection";
import { navigationUnitIds } from "./unitFilter";
import type { ProbeGeometry } from "./types";

const geometry: ProbeGeometry = {
  probe: "ProbeA",
  channels: [],
  units: [
    { unitId: 11, x: 0, y: 0 },
    { unitId: 22, x: 100, y: 100 },
    { unitId: 33, x: 300, y: 300 },
    { unitId: 44, x: null, y: null },
  ],
};

describe("probe selection", () => {
  it("intersects positioned units with the RF document unit pool", () => {
    expect(probeUnitsInRegion(geometry, { xMin: -10, xMax: 150, yMin: -10, yMax: 150 }, [22, 33, 44]))
      .toEqual([22]);
  });

  it("selects the eligible positioned unit nearest the region center", () => {
    const region = { xMin: 50, xMax: 250, yMin: 50, yMax: 250 };
    expect(nearestProbeUnitToRegionCenter(geometry, region, [22, 33])).toBe(22);
  });

  it("preserves a real zero-unit filter", () => {
    expect(probeUnitsInRegion(geometry, { xMin: 500, xMax: 600, yMin: 500, yMax: 600 }, [11, 22, 33]))
      .toEqual([]);
  });

  it("excludes explicitly unpositioned units from spatial regions", () => {
    const region = { xMin: -1000, xMax: 1000, yMin: -1000, yMax: 1000 };
    expect(probeUnitsInRegion(geometry, region, [11, 44])).toEqual([11]);
    expect(nearestProbeUnitToRegionCenter(geometry, region, [44])).toBeNull();
  });

  it("restores the complete RF unit pool when the region is cleared", () => {
    expect(probeUnitsInRegion(geometry, null, [11, 22, 44])).toEqual([11, 22, 44]);
  });

  it("re-derives region navigation when the quality-visible pool widens", () => {
    const region = { xMin: -10, xMax: 150, yMin: -10, yMax: 150 };
    const narrow = probeUnitsInRegion(geometry, region, [11]);
    const widened = probeUnitsInRegion(geometry, region, [11, 22, 33]);

    expect(narrow).toEqual([11]);
    expect(widened).toEqual([11, 22]);
    expect(navigationUnitIds([11, 22, 33], widened)).toEqual([11, 22]);
  });
});
