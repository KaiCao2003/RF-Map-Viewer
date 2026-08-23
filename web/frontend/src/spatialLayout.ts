import type { AxisGroup, CellRef } from "./types";

export const SINGLETON_Y_REFERENCE_COLUMNS = 30;
export const SINGLETON_Y_REFERENCE_ROWS = 7;
export const POLAR_INNER_BLANK_ROWS = 4;

export interface SpatialGridDimensions {
  cellWidth: number;
  cellHeight: number;
  gridWidth: number;
  gridHeight: number;
}

export interface RectSpatialLayout {
  kind: "rect";
  x: number;
  y: number;
  cellWidth: number;
  cellHeight: number;
  width: number;
  height: number;
  xGroups: AxisGroup[];
  yGroups: AxisGroup[];
}

export interface PolarSpatialLayout {
  kind: "polar";
  cx: number;
  cy: number;
  scale: number;
  totalDegrees: number;
  ringSpan: number;
  xGroups: AxisGroup[];
  yGroups: AxisGroup[];
  ringRows: number[];
}

export type SpatialLayout = RectSpatialLayout | PolarSpatialLayout;

/** Fit a map while preserving the legacy 30-by-7 singleton-y footprint. */
export function spatialGridDimensions(
  availableWidth: number,
  availableHeight: number,
  columns: number,
  rows: number,
  minimumCellWidth = 0,
): SpatialGridDimensions {
  const safeColumns = Math.max(1, Math.trunc(columns));
  const safeRows = Math.max(1, Math.trunc(rows));
  const width = Math.max(0, availableWidth);
  const height = Math.max(0, availableHeight);
  if (safeRows === 1) {
    const aspect = SINGLETON_Y_REFERENCE_COLUMNS / SINGLETON_Y_REFERENCE_ROWS;
    let gridWidth = Math.min(width, height * aspect);
    const cellWidth = Math.max(minimumCellWidth, gridWidth / safeColumns);
    gridWidth = cellWidth * safeColumns;
    const gridHeight = gridWidth / aspect;
    return { cellWidth, cellHeight: gridHeight, gridWidth, gridHeight };
  }

  const cell = Math.max(
    minimumCellWidth,
    Math.min(width / safeColumns, height / safeRows),
  );
  return {
    cellWidth: cell,
    cellHeight: cell,
    gridWidth: cell * safeColumns,
    gridHeight: cell * safeRows,
  };
}

/** Return the visual radial width occupied by one scientific y row. */
export function polarRingSpan(rows: number): number {
  return Math.trunc(rows) === 1 ? SINGLETON_Y_REFERENCE_ROWS : 1;
}

export function spatialCellAt(layout: SpatialLayout, x: number, y: number): CellRef | null {
  let displayRow: number;
  let column: number;
  if (layout.kind === "rect") {
    if (x < layout.x || x >= layout.x + layout.width || y < layout.y || y >= layout.y + layout.height) {
      return null;
    }
    column = Math.floor((x - layout.x) / layout.cellWidth);
    displayRow = Math.floor((y - layout.y) / layout.cellHeight);
  } else {
    const dx = (x - layout.cx) / layout.scale;
    const dy = (layout.cy - y) / layout.scale;
    const radius = Math.hypot(dx, dy);
    const outerRadius = POLAR_INNER_BLANK_ROWS + layout.yGroups.length * layout.ringSpan;
    if (radius < POLAR_INNER_BLANK_ROWS || radius >= outerRadius) return null;
    const ring = Math.floor((radius - POLAR_INNER_BLANK_ROWS) / layout.ringSpan);
    const mappedRow = layout.ringRows[ring];
    if (mappedRow == null) return null;
    displayRow = mappedRow;
    let degrees = (Math.atan2(dy, dx) * 180) / Math.PI;
    const start = 90 + layout.totalDegrees / 2;
    if (layout.totalDegrees >= 359.999) {
      column = Math.floor(
        (((start - degrees) % 360 + 360) % 360)
          / (layout.totalDegrees / layout.xGroups.length),
      );
    } else {
      const end = 90 - layout.totalDegrees / 2;
      while (degrees > start) degrees -= 360;
      while (degrees < end) degrees += 360;
      if (degrees < end || degrees > start) return null;
      column = Math.floor((start - degrees) / (layout.totalDegrees / layout.xGroups.length));
    }
    column = Math.max(0, Math.min(layout.xGroups.length - 1, column));
  }
  const yGroup = layout.yGroups[displayRow];
  const xGroup = layout.xGroups[column];
  return yGroup && xGroup ? [yGroup[0], yGroup[1], xGroup[0], xGroup[1]] : null;
}
