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

export function probeUnitsInPolygon(geometry: ProbeGeometry, vertices: ReadonlyArray<readonly [number, number]>): number[] {
  if (vertices.length < 3) return [];
  return geometry.units.filter(hasProbePosition).filter(({ x, y }) => {
    let inside = false;
    for (let i = 0; i < vertices.length; i += 1) {
      const [x0, y0] = vertices[i], [x1, y1] = vertices[(i + 1) % vertices.length];
      const dx = x1 - x0, dy = y1 - y0, length = Math.hypot(dx, dy);
      if (!length) continue;
      const cross = (x - x0) * dy - (y - y0) * dx;
      const along = ((x - x0) * dx + (y - y0) * dy) / length;
      if (Math.abs(cross) <= 1e-9 * length && along >= -1e-9 && along <= length + 1e-9) return true;
      if ((y0 > y) !== (y1 > y) && x < x0 + (y - y0) * dx / dy) inside = !inside;
    }
    return inside;
  }).map((unit) => unit.unitId);
}

export function probeUnitInfo(unit: PositionedProbeUnit): string {
  return `Unit ${unit.unitId} · x ${unit.x.toFixed(1)} µm · y ${unit.y.toFixed(1)} µm`;
}
