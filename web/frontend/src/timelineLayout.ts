export interface TimelineGridLayout {
  left: number;
  right: number;
  gapX: number;
  columns: number;
  rows: number;
  slotWidth: number;
  cell: number;
  gridWidth: number;
  gridHeight: number;
  labelGap: number;
  labelHeight: number;
  rowGap: number;
  rowHeight: number;
}

export interface TimelineGridInput {
  width: number;
  count: number;
  xCount: number;
  yCount: number;
}

const MAX_COLUMNS = 4;
const TARGET_GRID_HEIGHT = 180;
const MIN_READABLE_GRID_HEIGHT = 120;

export function timelineGridLayout(input: TimelineGridInput): TimelineGridLayout {
  const width = Math.max(280, input.width);
  const count = Math.max(1, Math.trunc(input.count));
  const xCount = Math.max(1, Math.trunc(input.xCount));
  const yCount = Math.max(1, Math.trunc(input.yCount));
  const left = 32;
  const right = 32;
  const gapX = 18;
  const labelGap = 8;
  const labelHeight = 20;
  const rowGap = 24;
  const availableWidth = Math.max(1, width - left - right);
  const responsiveMaximum = width >= 1180 ? 4 : width >= 820 ? 3 : width >= 560 ? 2 : 1;
  let columns = Math.min(count, MAX_COLUMNS, responsiveMaximum);
  let slotWidth = 1;
  let cell = 1;
  let gridHeight = 1;

  while (columns >= 1) {
    slotWidth = Math.max(1, (availableWidth - (columns - 1) * gapX) / columns);
    cell = Math.min(TARGET_GRID_HEIGHT / yCount, slotWidth / xCount);
    gridHeight = cell * yCount;
    if (gridHeight >= MIN_READABLE_GRID_HEIGHT || columns === 1) break;
    columns -= 1;
  }

  const gridWidth = cell * xCount;
  const rowHeight = gridHeight + labelGap + labelHeight + rowGap;
  return {
    left,
    right,
    gapX,
    columns,
    rows: Math.ceil(count / columns),
    slotWidth,
    cell,
    gridWidth,
    gridHeight,
    labelGap,
    labelHeight,
    rowGap,
    rowHeight,
  };
}

export function timelineIntervalLabel(startMs: number, endMs: number, format: (value: number) => string): string {
  return `${format(startMs)}–${format(endMs)} ms`;
}

export function timelinePositionFraction(timeMs: number, axisStartMs: number, axisEndMs: number): number {
  if (![timeMs, axisStartMs, axisEndMs].every(Number.isFinite) || axisEndMs <= axisStartMs) return 0;
  return Math.max(0, Math.min(1, (timeMs - axisStartMs) / (axisEndMs - axisStartMs)));
}

export function timelineChartX(
  timeMs: number,
  axisStartMs: number,
  axisEndMs: number,
  chartX: number,
  chartWidth: number,
): number {
  return chartX + chartWidth * timelinePositionFraction(timeMs, axisStartMs, axisEndMs);
}

export function timelineBinAtTime(
  timeMs: number,
  bounds: ReadonlyArray<readonly [number, number]>,
): number | null {
  if (!bounds.length || !Number.isFinite(timeMs)) return null;
  const index = bounds.findIndex(([_start, end]) => timeMs < end);
  return index < 0 ? bounds.length - 1 : index;
}
