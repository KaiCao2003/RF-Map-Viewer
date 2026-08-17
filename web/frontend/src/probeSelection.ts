import type { ProbeGeometry, ProbeUnit } from "./types";

export interface ProbeRegion {
  xMin: number;
  xMax: number;
  yMin: number;
  yMax: number;
}

export type PositionedProbeUnit = ProbeUnit & { x: number; y: number };

export function hasProbePosition(unit: ProbeUnit): unit is PositionedProbeUnit {
  return (
    typeof unit.x === "number" && Number.isFinite(unit.x)
    && typeof unit.y === "number" && Number.isFinite(unit.y)
  );
}

export function probeUnitsInRegion(
  geometry: ProbeGeometry,
  region: ProbeRegion | null,
  availableUnitIds: ReadonlyArray<number>,
): number[] {
  if (region == null) return [...availableUnitIds];
  const positioned = new Map(
    geometry.units.filter(hasProbePosition).map((unit) => [unit.unitId, unit]),
  );
  return availableUnitIds.filter((unitId) => {
    const unit = positioned.get(unitId);
    if (!unit) return false;
    return (
      region.xMin <= unit.x && unit.x <= region.xMax
      && region.yMin <= unit.y && unit.y <= region.yMax
    );
  });
}

export function nearestProbeUnitToRegionCenter(
  geometry: ProbeGeometry,
  region: ProbeRegion,
  eligibleUnitIds: ReadonlyArray<number>,
): number | null {
  const positioned = new Map(
    geometry.units.filter(hasProbePosition).map((unit) => [unit.unitId, unit]),
  );
  const centerX = (region.xMin + region.xMax) / 2;
  const centerY = (region.yMin + region.yMax) / 2;
  let nearest: number | null = null;
  let nearestDistance = Number.POSITIVE_INFINITY;
  for (const unitId of eligibleUnitIds) {
    const unit = positioned.get(unitId);
    if (!unit) continue;
    const distance = (unit.x - centerX) ** 2 + (unit.y - centerY) ** 2;
    if (distance < nearestDistance) {
      nearest = unitId;
      nearestDistance = distance;
    }
  }
  return nearest;
}
