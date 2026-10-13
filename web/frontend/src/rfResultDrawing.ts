import type { SpatialLayout } from "./spatialLayout";
import { POLAR_INNER_BLANK_ROWS } from "./spatialLayout";
import type { SavedRfOverlay } from "./rfResults";
import type { RfOverlaySettings, ViewState } from "./types";

type NativeCell = { kind: "rect"; x: number; y: number; width: number; height: number; cx: number; cy: number }
  | { kind: "polar"; inner: number; outer: number; start: number; end: number; cx: number; cy: number };

export function savedNativeCell(layout: SpatialLayout, state: Pick<ViewState, "flipY" | "polarRadius">, y: number, x: number): NativeCell {
  const column = layout.xGroups.findIndex(([start, end]) => start <= x && x <= end);
  const displayRow = layout.yGroups.findIndex(([start, end]) => start <= y && y <= end);
  const [xs, xe] = layout.xGroups[column], [ys, ye] = layout.yGroups[displayRow];
  const fx = (x - xs) / (xe - xs + 1), dx = 1 / (xe - xs + 1), dy = 1 / (ye - ys + 1);
  if (layout.kind === "rect") {
    const fy = (state.flipY ? ye - y : y - ys) * dy;
    const left = layout.x + (column + fx) * layout.cellWidth;
    const top = layout.y + (displayRow + fy) * layout.cellHeight;
    const width = dx * layout.cellWidth, height = dy * layout.cellHeight;
    return { kind: "rect", x: left, y: top, width, height, cx: left + width / 2, cy: top + height / 2 };
  }
  const ring = layout.ringRows.indexOf(displayRow);
  const ascending = state.polarRadius === "MATLAB row 1 inner" || state.flipY;
  const fy = (ascending ? y - ys : ye - y) * dy;
  const inner = (POLAR_INNER_BLANK_ROWS + (ring + fy) * layout.ringSpan) * layout.scale;
  const outer = inner + dy * layout.ringSpan * layout.scale;
  const angle = (position: number) => (90 + layout.totalDegrees / 2 - layout.totalDegrees * position / layout.xGroups.length) * Math.PI / 180;
  const start = angle(column + fx), end = angle(column + fx + dx);
  const radius = (inner + outer) / 2, theta = (start + end) / 2;
  return { kind: "polar", inner, outer, start, end, cx: layout.cx + radius * Math.cos(theta), cy: layout.cy - radius * Math.sin(theta) };
}

export function drawSavedRfOverlay(context: CanvasRenderingContext2D, layout: SpatialLayout,
  state: ViewState, overlay: SavedRfOverlay, settings: RfOverlaySettings, resultOnly: boolean): void {
  const colors = ["", settings.color2d, settings.color1d, settings.overlapColor];
  context.save();
  context.lineWidth = settings.width;
  overlay.flags?.forEach((row, y) => row.forEach((flag, x) => {
    if (!flag) return;
    const cell = savedNativeCell(layout, state, y, x);
    context.strokeStyle = colors[flag];
    context.fillStyle = colors[flag];
    context.beginPath();
    if (cell.kind === "rect") context.rect(cell.x, cell.y, cell.width, cell.height);
    else if (layout.kind === "polar") {
      context.arc(layout.cx, layout.cy, cell.outer, -cell.start, -cell.end, cell.start < cell.end);
      context.arc(layout.cx, layout.cy, cell.inner, -cell.end, -cell.start, cell.start >= cell.end);
      context.closePath();
    }
    if (resultOnly) context.fill();
    context.stroke();
  }));
  if (resultOnly) {
    overlay.centers2d.forEach(([y, x]) => {
      const cell = savedNativeCell(layout, state, y, x);
      for (const [color, width] of [["white", 4], ["#1d1d1f", 2]] as const) {
        context.strokeStyle = color; context.lineWidth = width;
        context.beginPath();
        context.moveTo(cell.cx - 4, cell.cy - 4); context.lineTo(cell.cx + 4, cell.cy + 4);
        context.moveTo(cell.cx - 4, cell.cy + 4); context.lineTo(cell.cx + 4, cell.cy - 4);
        context.stroke();
      }
    });
    overlay.centers1d.forEach(({ axis, index }) => {
      const cell = savedNativeCell(layout, state, axis === "x" ? 0 : index, axis === "x" ? index : 0);
      let x = cell.cx, y = cell.cy;
      if (layout.kind === "rect") {
        if (axis === "x") y = layout.y + layout.height + 7;
        else x = layout.x - 7;
      } else {
        const radius = axis === "x" ? (POLAR_INNER_BLANK_ROWS + layout.yGroups.length * layout.ringSpan) * layout.scale + 7 : Math.hypot(x - layout.cx, y - layout.cy);
        const theta = axis === "x" ? Math.atan2(layout.cy - y, x - layout.cx) : (90 + layout.totalDegrees / 2) * Math.PI / 180;
        x = layout.cx + radius * Math.cos(theta); y = layout.cy - radius * Math.sin(theta);
      }
      context.beginPath(); context.moveTo(x, y - 4); context.lineTo(x - 4, y + 3); context.lineTo(x + 4, y + 3); context.closePath();
      context.fillStyle = "white"; context.strokeStyle = "#1d1d1f"; context.lineWidth = 2;
      context.fill(); context.stroke();
    });
  }
  context.restore();
}
