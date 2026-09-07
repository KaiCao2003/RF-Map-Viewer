import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { canvasFont } from "../canvasFont";
import {
  allPositionsTimelineValues,
  finiteMinMax,
  formatNumber,
  formatResponse,
  groupResponseValue,
  groupResponseValues,
  groupTemporalMetrics,
  halfOpenRangesOverlap,
  inferTotalDegrees,
  prepareResponseMatrix,
  prepareTemporalMetricMatrices,
  snapTimeRange,
  timeBounds,
  timeGroupForMs,
  timeGroups,
  valueModeUnit,
} from "../math";
import { delayColor, paletteColor, responseRangeForPalette, rgbComposite } from "../palette";
import {
  POLAR_INNER_BLANK_ROWS,
  polarRingSpan,
  spatialCellAt,
  spatialGridDimensions,
  type PolarSpatialLayout,
  type RectSpatialLayout,
  type SpatialLayout,
} from "../spatialLayout";
import {
  timelineBinAtTime,
  timelineChartX,
  timelineGridLayout,
  timelineIntervalLabel,
} from "../timelineLayout";
import type {
  AxisGroup,
  CellRef,
  DatasetMeta,
  Matrix,
  ViewState,
} from "../types";

const INNER_BLANK_ROWS = POLAR_INNER_BLANK_ROWS;

interface CommonPlotProps {
  meta: DatasetMeta;
  counts: Float64Array;
  state: ViewState;
  unitIndex: number;
  selectedCell: CellRef;
  onSelectCell: (cell: CellRef) => void;
}

interface SpatialPlotProps extends CommonPlotProps {
  kind: "rf" | "delay";
}

interface TooltipState {
  x: number;
  y: number;
  lines: string[];
}

type RectLayout = RectSpatialLayout;
type PolarLayout = PolarSpatialLayout;
type PlotLayout = SpatialLayout;

function useContainerSize(
  ref: React.RefObject<HTMLElement | null>,
  minimumWidth = 480,
  minimumHeight = 280,
): { width: number; height: number } {
  const [size, setSize] = useState({ width: minimumWidth, height: minimumHeight });
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const update = (width: number, height: number) => setSize({
      width: Math.max(minimumWidth, Math.floor(width)),
      height: Math.max(minimumHeight, Math.floor(height)),
    });
    const observer = new ResizeObserver(([entry]) => update(entry.contentRect.width, entry.contentRect.height));
    observer.observe(node);
    const bounds = node.getBoundingClientRect();
    update(bounds.width, bounds.height);
    return () => observer.disconnect();
  }, [minimumHeight, minimumWidth, ref]);
  return size;
}

function contextFor(canvas: HTMLCanvasElement, width: number, height: number): CanvasRenderingContext2D {
  const pixelRatio = Math.max(1, window.devicePixelRatio || 1);
  canvas.width = Math.floor(width * pixelRatio);
  canvas.height = Math.floor(height * pixelRatio);
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  const context = canvas.getContext("2d")!;
  context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  context.imageSmoothingEnabled = false;
  context.clearRect(0, 0, width, height);
  return context;
}

function drawText(
  context: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  options: { color?: string; font?: string; align?: CanvasTextAlign; baseline?: CanvasTextBaseline } = {},
): void {
  context.fillStyle = options.color ?? "#1d2939";
  context.font = options.font ?? canvasFont(13);
  context.textAlign = options.align ?? "left";
  context.textBaseline = options.baseline ?? "alphabetic";
  context.fillText(text, x, y);
}

function polarCellPath(
  context: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  scale: number,
  inner: number,
  outer: number,
  start: number,
  end: number,
): void {
  context.beginPath();
  context.arc(cx, cy, outer * scale, -start, -end, start < end);
  context.arc(cx, cy, inner * scale, -end, -start, start >= end);
  context.closePath();
}

function drawRectMatrix(
  context: CanvasRenderingContext2D,
  matrix: Matrix,
  layout: RectLayout,
  color: (value: number | null) => string,
): void {
  matrix.forEach((row, y) => row.forEach((value, x) => {
    context.fillStyle = color(value);
    context.fillRect(
      layout.x + x * layout.cellWidth,
      layout.y + y * layout.cellHeight,
      Math.ceil(layout.cellWidth),
      Math.ceil(layout.cellHeight),
    );
  }));
}

function drawPolarMatrix(
  context: CanvasRenderingContext2D,
  matrix: Matrix,
  layout: PolarLayout,
  color: (value: number | null) => string,
): void {
  const thetaEdges = Array.from({ length: layout.xGroups.length + 1 }, (_, index) =>
    ((90 + layout.totalDegrees / 2 - (layout.totalDegrees * index) / layout.xGroups.length) * Math.PI) / 180,
  );
  context.fillStyle = "#f8fafc";
  context.beginPath();
  context.arc(layout.cx, layout.cy, INNER_BLANK_ROWS * layout.scale, 0, Math.PI * 2);
  context.fill();
  context.strokeStyle = "#e5e7eb";
  context.stroke();
  layout.ringRows.forEach((displayRow, ring) => {
    for (let column = 0; column < layout.xGroups.length; column += 1) {
      polarCellPath(
        context,
        layout.cx,
        layout.cy,
        layout.scale,
        INNER_BLANK_ROWS + ring * layout.ringSpan,
        INNER_BLANK_ROWS + (ring + 1) * layout.ringSpan,
        thetaEdges[column],
        thetaEdges[column + 1],
      );
      context.fillStyle = color(matrix[displayRow][column]);
      context.fill();
    }
  });
  context.beginPath();
  context.arc(
    layout.cx,
    layout.cy,
    (INNER_BLANK_ROWS + layout.yGroups.length * layout.ringSpan) * layout.scale,
    0,
    Math.PI * 2,
  );
  context.strokeStyle = "#475467";
  context.lineWidth = 1;
  context.stroke();
}

function selectedGroup(layout: PlotLayout, selected: CellRef): readonly [number, number] | null {
  const column = layout.xGroups.findIndex(([start, end]) => start <= selected[2] && selected[2] <= end);
  const displayRow = layout.yGroups.findIndex(([start, end]) => start <= selected[0] && selected[0] <= end);
  return column >= 0 && displayRow >= 0 ? [displayRow, column] : null;
}

function drawSelection(context: CanvasRenderingContext2D, layout: PlotLayout, selected: CellRef): void {
  const group = selectedGroup(layout, selected);
  if (!group) return;
  const [displayRow, column] = group;
  context.save();
  if (layout.kind === "rect") {
    const x = layout.x + column * layout.cellWidth;
    const y = layout.y + displayRow * layout.cellHeight;
    context.strokeStyle = "#101828";
    context.lineWidth = 2;
    context.strokeRect(x + 1, y + 1, layout.cellWidth - 2, layout.cellHeight - 2);
    context.strokeStyle = "white";
    context.lineWidth = 1;
    context.strokeRect(x + 3, y + 3, layout.cellWidth - 6, layout.cellHeight - 6);
  } else {
    const ring = layout.ringRows.indexOf(displayRow);
    if (ring < 0) return;
    const start = ((90 + layout.totalDegrees / 2 - (layout.totalDegrees * column) / layout.xGroups.length) * Math.PI) / 180;
    const end = ((90 + layout.totalDegrees / 2 - (layout.totalDegrees * (column + 1)) / layout.xGroups.length) * Math.PI) / 180;
    polarCellPath(
      context,
      layout.cx,
      layout.cy,
      layout.scale,
      INNER_BLANK_ROWS + ring * layout.ringSpan,
      INNER_BLANK_ROWS + (ring + 1) * layout.ringSpan,
      start,
      end,
    );
    context.strokeStyle = "white";
    context.lineWidth = 4;
    context.stroke();
    context.strokeStyle = "#101828";
    context.lineWidth = 2;
    context.stroke();
  }
  context.restore();
}

function drawAxes(
  context: CanvasRenderingContext2D,
  layout: RectLayout,
  meta: DatasetMeta,
): void {
  context.strokeStyle = "#344054";
  context.lineWidth = 1;
  context.strokeRect(layout.x, layout.y, layout.width, layout.height);
  const tickStep = Math.max(1, Math.floor(layout.xGroups.length / 6));
  layout.xGroups.forEach(([start, end], index) => {
    if (index % tickStep !== 0 && index !== layout.xGroups.length - 1) return;
    const x = layout.x + (index + 0.5) * layout.cellWidth;
    drawText(context, formatNumber((meta.xPositions[start] + meta.xPositions[end]) / 2, 2), x, layout.y + layout.height + 19, {
      color: "#475467", font: canvasFont(11), align: "center",
    });
  });
  layout.yGroups.forEach(([start, end], index) => {
    const y = layout.y + (index + 0.5) * layout.cellHeight;
    const position = formatNumber((meta.yPositions[start] + meta.yPositions[end]) / 2, 2);
    const indexLabel = start === end ? `${start + 1}` : `${start + 1}–${end + 1}`;
    drawText(context, `${indexLabel} / ${position}`, layout.x - 10, y, {
      color: "#475467", font: canvasFont(11), align: "right", baseline: "middle",
    });
  });
  drawText(context, "x position", layout.x + layout.width / 2, layout.y + layout.height + 46, { color: "#475467", align: "center" });
  context.save();
  context.translate(layout.x - 62, layout.y + layout.height / 2);
  context.rotate(-Math.PI / 2);
  drawText(context, "yIdx / y", 0, 0, { color: "#475467", align: "center" });
  context.restore();
}

function drawColorbar(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  height: number,
  low: number,
  high: number,
  color: (value: number | null) => string,
  suffix: string,
): void {
  for (let step = 0; step < 90; step += 1) {
    const value = high - ((high - low) * step) / 90;
    context.fillStyle = color(value);
    context.fillRect(x, y + (height * step) / 90, 16, height / 90 + 1);
  }
  context.strokeStyle = "#475467";
  context.strokeRect(x, y, 16, height);
  drawText(context, `${formatNumber(high, 1)}${suffix}`, x + 24, y + 3, { color: "#475467", font: canvasFont(11) });
  drawText(context, `${formatNumber(low, 1)}${suffix}`, x + 24, y + height, { color: "#475467", font: canvasFont(11) });
}

function finiteMaximum(matrix: Matrix): number {
  let high = 0;
  matrix.forEach((row) => row.forEach((value) => {
    if (value != null && Number.isFinite(value)) high = Math.max(high, value);
  }));
  return high;
}

function hitTest(layout: PlotLayout, x: number, y: number): CellRef | null {
  return spatialCellAt(layout, x, y);
}

function groupLabel(axis: "x" | "y", group: AxisGroup, positions: number[]): string {
  const [start, end] = group;
  if (start === end) return `${axis}Idx ${start + 1}; ${axis} ${formatNumber(positions[start], 2)}`;
  return `${axis}Idx ${start + 1}–${end + 1}; ${axis} ${formatNumber(positions[start], 2)}…${formatNumber(positions[end], 2)}`;
}

function tooltipLines(
  meta: DatasetMeta,
  counts: Float64Array,
  state: ViewState,
  cell: CellRef,
  displayBin?: number,
): string[] {
  const groups = timeGroups(meta, state.timeResolutionMs);
  const activeIndex = displayBin ?? timeGroupForMs(meta, groups, state.activeTimeCenterMs);
  const index = Math.max(0, activeIndex);
  const active = groupResponseValue(counts, meta, cell, groups[index], state.valueMode);
  const rfRange = snapTimeRange(meta, state.rfStartMs, state.rfEndMs);
  const rfBounds = timeBounds(meta, rfRange);
  const rfValue = groupResponseValue(counts, meta, cell, rfRange, state.valueMode);
  const total = groupResponseValue(counts, meta, cell, [0, meta.shape[3] - 1], state.valueMode);
  const temporal = groupTemporalMetrics(counts, meta, cell, groups);
  const [activeStart, activeEnd] = timeBounds(meta, groups[index]);
  return [
    groupLabel("y", [cell[0], cell[1]], meta.yPositions),
    groupLabel("x", [cell[2], cell[3]], meta.xPositions),
    `bin ${index + 1} (${formatNumber(activeStart)}–${formatNumber(activeEnd)} ms): ${formatResponse(active, state.valueMode)} ${valueModeUnit(state.valueMode)}`,
    `RF sum ${formatNumber(rfBounds[0])}–${formatNumber(rfBounds[1])} ms: ${formatResponse(rfValue, state.valueMode)} ${valueModeUnit(state.valueMode)}`,
    `full window: ${formatResponse(total, state.valueMode)} ${valueModeUnit(state.valueMode)}`,
    temporal.delayMs == null ? "count-rate peak delay n/a" : `count-rate peak delay ${formatNumber(temporal.delayMs, 1)} ms`,
  ];
}

export function SpatialPlot({
  meta,
  counts,
  state,
  unitIndex,
  selectedCell,
  onSelectCell,
  kind,
}: SpatialPlotProps) {
  const wrapper = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const layout = useRef<PlotLayout | null>(null);
  const { width, height } = useContainerSize(wrapper, 480, 280);
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);
  const groups = useMemo(() => timeGroups(meta, state.timeResolutionMs), [meta, state.timeResolutionMs]);

  useEffect(() => {
    if (!canvas.current) return;
    const context = contextFor(canvas.current, width, height);
    const range = snapTimeRange(meta, state.rfStartMs, state.rfEndMs);
    const response = prepareResponseMatrix(
      counts,
      meta,
      range,
      state.valueMode,
      state.xBins,
      state.yBins,
      state.flipY,
      state.smoothRadius,
    );
    const temporal = prepareTemporalMetricMatrices(
      counts,
      meta,
      groups,
      state.xBins,
      state.yBins,
      state.flipY,
      state.smoothRadius,
    );
    const prepared = kind === "rf"
      ? response
      : { matrix: temporal.delay, xGroups: temporal.xGroups, yGroups: temporal.yGroups };
    const responsePrepared = kind === "delay" && state.rgbMode
      ? prepareResponseMatrix(counts, meta, [0, meta.shape[3] - 1], state.valueMode, state.xBins, state.yBins, state.flipY, state.smoothRadius).matrix
      : null;
    const entropyPrepared = kind === "delay" && state.rgbMode
      ? temporal.entropy
      : null;
    const [finiteLow, finiteHigh] = finiteMinMax(prepared.matrix);
    const [autoLow, autoHigh] = responseRangeForPalette(finiteLow, finiteHigh, state.palette);
    const low = kind === "delay" && !state.rgbMode ? meta.timeBinEdges[0] * 1000 : autoLow;
    const high = kind === "delay" && !state.rgbMode ? meta.timeBinEdges.at(-1)! * 1000 : autoHigh;
    const title = kind === "rf"
      ? `RF map - ${state.valueMode}: ${formatNumber(state.rfStartMs)} to ${formatNumber(state.rfEndMs)} ms`
      : state.rgbMode ? "RGB composite" : "Delay map - peak count-rate interval center";
    const totalDegrees = inferTotalDegrees(meta.xPositions);
    const subtitle = state.polarLayout
      ? kind === "delay" && state.rgbMode
        ? `Polar layout; R ${state.valueMode}; G count-rate-peak delay; B temporal entropy`
        : `Polar layout; total angle ${formatNumber(totalDegrees, 0)}°; radius: ${state.polarRadius}`
      : kind === "delay" && state.rgbMode
        ? `R ${state.valueMode}; G count-rate-peak delay; B temporal entropy`
        : `Unit ${String(unitIndex).padStart(3, "0")} / cluster ${state.clusterId}`;
    drawText(context, title, 20, 22, { font: canvasFont(19, 600), color: "#111827" });
    drawText(context, subtitle, 20, 44, { color: "#667085", font: canvasFont(13) });
    const maxResponse = Math.max(1, finiteMaximum(responsePrepared ?? [[0]]));

    const color = (value: number | null, row = 0, column = 0) => {
      if (kind === "delay" && state.rgbMode) {
        const responseValue = responsePrepared?.[row]?.[column] ?? null;
        const entropy = entropyPrepared?.[row]?.[column] ?? null;
        return rgbComposite(responseValue, value, entropy, maxResponse, meta.timeBinEdges[0] * 1000, meta.timeBinEdges.at(-1)! * 1000);
      }
      return kind === "delay" ? delayColor(value, low, high) : paletteColor(value, low, high, state.palette);
    };

    if (!state.polarLayout) {
      const marginLeft = 78;
      const marginRight = state.rgbMode && kind === "delay" ? 188 : 104;
      const marginTop = 56;
      const marginBottom = 68;
      const dimensions = spatialGridDimensions(
        width - marginLeft - marginRight,
        height - marginTop - marginBottom,
        prepared.xGroups.length,
        prepared.yGroups.length,
        4,
      );
      const { cellWidth, cellHeight, gridWidth, gridHeight } = dimensions;
      const nextLayout: RectLayout = {
        kind: "rect",
        x: marginLeft + Math.max(0, (width - marginLeft - marginRight - gridWidth) / 2),
        y: marginTop + Math.max(0, (height - marginTop - marginBottom - gridHeight) / 2),
        cellWidth,
        cellHeight,
        width: gridWidth,
        height: gridHeight,
        xGroups: prepared.xGroups,
        yGroups: prepared.yGroups,
      };
      layout.current = nextLayout;
      prepared.matrix.forEach((row, rowIndex) => row.forEach((value, column) => {
        context.fillStyle = color(value, rowIndex, column);
        context.fillRect(
          nextLayout.x + column * cellWidth,
          nextLayout.y + rowIndex * cellHeight,
          Math.ceil(cellWidth),
          Math.ceil(cellHeight),
        );
      }));
      drawSelection(context, nextLayout, selectedCell);
      drawAxes(context, nextLayout, meta);
      if (kind === "delay" && state.rgbMode) {
        [[`R ${valueModeUnit(state.valueMode)}`, "#dc2626"], ["G delay", "#16a34a"], ["B entropy", "#2563eb"]].forEach(([label, swatch], index) => {
          const x = nextLayout.x + nextLayout.width + 34;
          const y = nextLayout.y + index * 27;
          context.fillStyle = swatch;
          context.fillRect(x, y, 16, 16);
          drawText(context, label, x + 24, y + 12, { color: "#475467" });
        });
      } else {
        drawColorbar(
          context,
          nextLayout.x + nextLayout.width + 38,
          nextLayout.y,
          Math.min(220, nextLayout.height),
          low,
          high,
          (value) => kind === "delay" ? delayColor(value, low, high) : paletteColor(value, low, high, state.palette),
          kind === "delay" ? " ms" : ` ${valueModeUnit(state.valueMode)}`,
        );
      }
    } else {
      const ringSpan = polarRingSpan(prepared.yGroups.length);
      const radiusUnits = INNER_BLANK_ROWS + prepared.yGroups.length * ringSpan + 1;
      const horizontalMargin = kind === "delay" && state.rgbMode ? 220 : 180;
      const scale = Math.max(4, Math.min((width - horizontalMargin) / (2 * radiusUnits), (height - 130) / (2 * radiusUnits)));
      const ringRows = state.polarRadius === "MATLAB row 1 inner"
        ? prepared.yGroups.map((_group, index) => index).sort((a, b) => prepared.yGroups[a][0] - prepared.yGroups[b][0])
        : prepared.yGroups.map((_group, index) => index).reverse();
      const nextLayout: PolarLayout = {
        kind: "polar",
        cx: width / 2,
        cy: height / 2 + 22,
        scale,
        totalDegrees,
        ringSpan,
        xGroups: prepared.xGroups,
        yGroups: prepared.yGroups,
        ringRows,
      };
      layout.current = nextLayout;
      if (kind === "delay" && state.rgbMode) {
        context.fillStyle = "#f8fafc";
        context.beginPath();
        context.arc(nextLayout.cx, nextLayout.cy, INNER_BLANK_ROWS * scale, 0, Math.PI * 2);
        context.fill();
        context.strokeStyle = "#e5e7eb";
        context.stroke();
        const thetaEdges = Array.from({ length: prepared.xGroups.length + 1 }, (_, index) =>
          ((90 + totalDegrees / 2 - (totalDegrees * index) / prepared.xGroups.length) * Math.PI) / 180,
        );
        ringRows.forEach((displayRow, ring) => {
          prepared.matrix[displayRow].forEach((value, column) => {
            polarCellPath(
              context,
              nextLayout.cx,
              nextLayout.cy,
              scale,
              INNER_BLANK_ROWS + ring * ringSpan,
              INNER_BLANK_ROWS + (ring + 1) * ringSpan,
              thetaEdges[column],
              thetaEdges[column + 1],
            );
            context.fillStyle = color(value, displayRow, column);
            context.fill();
          });
        });
      } else {
        drawPolarMatrix(context, prepared.matrix, nextLayout, color);
      }
      drawSelection(context, nextLayout, selectedCell);
      const outer = (INNER_BLANK_ROWS + prepared.yGroups.length * ringSpan) * scale;
      if (kind === "delay" && state.rgbMode) {
        context.beginPath();
        context.arc(nextLayout.cx, nextLayout.cy, outer, 0, Math.PI * 2);
        context.strokeStyle = "#475467";
        context.lineWidth = 1;
        context.stroke();
        const legendX = Math.min(nextLayout.cx + outer + 26, width - 154);
        const legendY = Math.max(64, nextLayout.cy - 40);
        [[`R ${valueModeUnit(state.valueMode)}`, "#dc2626"], ["G delay", "#16a34a"], ["B entropy", "#2563eb"]].forEach(([label, swatch], index) => {
          const y = legendY + index * 26;
          context.fillStyle = swatch;
          context.fillRect(legendX, y, 16, 16);
          drawText(context, label, legendX + 24, y + 8, { color: "#475467", baseline: "middle" });
        });
      } else {
        drawText(context, "x columns span visual angle", nextLayout.cx, nextLayout.cy - outer - 18, { color: "#475467", align: "center" });
        drawText(context, `Values: ${kind === "delay" ? "delay (ms)" : state.valueMode}`, nextLayout.cx, nextLayout.cy + outer + 22, { color: "#475467", align: "center" });
        drawColorbar(
          context,
          width - 124,
          nextLayout.cy - Math.min(220, 2 * outer) / 2,
          Math.min(220, 2 * outer),
          low,
          high,
          (value) => kind === "delay" ? delayColor(value, low, high) : paletteColor(value, low, high, state.palette),
          kind === "delay" ? " ms" : ` ${valueModeUnit(state.valueMode)}`,
        );
      }
    }
  }, [counts, groups, height, kind, meta, selectedCell, state, unitIndex, width]);

  const pointerCell = (event: React.PointerEvent<HTMLCanvasElement> | React.MouseEvent<HTMLCanvasElement>): CellRef | null => {
    const bounds = event.currentTarget.getBoundingClientRect();
    return layout.current ? hitTest(layout.current, event.clientX - bounds.left, event.clientY - bounds.top) : null;
  };

  return (
    <div className="canvas-stage spatial-stage" ref={wrapper}>
      <canvas
        ref={canvas}
        aria-label={kind === "rf" ? "RF response heatmap" : state.rgbMode ? "RGB response composite" : "Peak delay heatmap"}
        onPointerMove={(event) => {
          const cell = pointerCell(event);
          const bounds = event.currentTarget.getBoundingClientRect();
          setTooltip(cell ? {
            x: event.clientX - bounds.left + 14,
            y: event.clientY - bounds.top + 14,
            lines: tooltipLines(meta, counts, state, cell),
          } : null);
        }}
        onPointerLeave={() => setTooltip(null)}
        onClick={(event) => {
          const cell = pointerCell(event);
          if (cell) onSelectCell(cell);
        }}
      />
      {tooltip && (
        <div className="plot-tooltip" style={{ left: tooltip.x, top: tooltip.y }}>
          {tooltip.lines.map((line, index) => <span key={`${index}-${line}`}>{line}</span>)}
        </div>
      )}
    </div>
  );
}

interface TimelinePlotProps extends CommonPlotProps {
  onSelectTime: (binIndex: number, extend: boolean) => void;
  onScrollFraction: (fraction: number) => void;
}

interface PreparedTimelineBin {
  matrix: Matrix;
  xGroups: AxisGroup[];
  yGroups: AxisGroup[];
}

interface TimelineTileLayout {
  plot: PlotLayout;
  binIndex: number;
  frameX: number;
  frameY: number;
  frameWidth: number;
  frameHeight: number;
  labelBottom: number;
}

interface TimelineMapRowProps {
  rowIndex: number;
  startBin: number;
  endBin: number;
  width: number;
  miniLeft: number;
  gapX: number;
  slotWidth: number;
  rowHeight: number;
  miniGridWidth: number;
  miniGridHeight: number;
  preparedBins: PreparedTimelineBin[];
  groups: AxisGroup[];
  meta: DatasetMeta;
  globalHigh: number;
  palette: ViewState["palette"];
  polarLayout: boolean;
  polarRadius: ViewState["polarRadius"];
  selectionLower: number;
  selectionUpper: number;
  scrollRoot: HTMLDivElement | null;
  tooltipFor: (cell: CellRef, binIndex: number) => string[];
  onSelectCell: (cell: CellRef) => void;
  onSelectTime: (binIndex: number, extend: boolean) => void;
}

const TimelineMapRow = memo(function TimelineMapRow({
  rowIndex,
  startBin,
  endBin,
  width,
  miniLeft,
  gapX,
  slotWidth,
  rowHeight,
  miniGridWidth,
  miniGridHeight,
  preparedBins,
  groups,
  meta,
  globalHigh,
  palette,
  polarLayout,
  polarRadius,
  selectionLower,
  selectionUpper,
  scrollRoot,
  tooltipFor,
  onSelectCell,
  onSelectTime,
}: TimelineMapRowProps) {
  const host = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const layouts = useRef<TimelineTileLayout[]>([]);
  const [visible, setVisible] = useState(rowIndex < 3);
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);

  useEffect(() => {
    const node = host.current;
    if (!node) return;
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => setVisible(entry.isIntersecting),
      { root: scrollRoot, rootMargin: "320px 0px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [scrollRoot]);

  useEffect(() => {
    if (!visible || !canvas.current) {
      layouts.current = [];
      setTooltip(null);
      return;
    }
    const context = contextFor(canvas.current, width, rowHeight);
    const nextLayouts: TimelineTileLayout[] = [];
    for (let binIndex = startBin; binIndex < endBin; binIndex += 1) {
      const prepared = preparedBins[binIndex];
      if (!prepared) continue;
      const column = binIndex - startBin;
      const slotX = miniLeft + column * (slotWidth + gapX);
      let plot: PlotLayout;
      let frameX: number;
      let frameY: number;
      let frameWidth: number;
      let frameHeight: number;
      if (polarLayout) {
        const ringSpan = polarRingSpan(prepared.yGroups.length);
        const size = Math.min(miniGridWidth, miniGridHeight);
        const radiusUnits = INNER_BLANK_ROWS + prepared.yGroups.length * ringSpan;
        const scale = Math.max(0.7, size / (2 * radiusUnits));
        const radius = radiusUnits * scale;
        const polar: PolarLayout = {
          kind: "polar",
          cx: slotX + slotWidth / 2,
          cy: miniGridHeight / 2,
          scale,
          totalDegrees: inferTotalDegrees(meta.xPositions),
          ringSpan,
          xGroups: prepared.xGroups,
          yGroups: prepared.yGroups,
          ringRows: polarRadius === "MATLAB row 1 inner"
            ? prepared.yGroups.map((_group, index) => index).sort((a, b) => prepared.yGroups[a][0] - prepared.yGroups[b][0])
            : prepared.yGroups.map((_group, index) => index).reverse(),
        };
        plot = polar;
        frameX = polar.cx - radius;
        frameY = polar.cy - radius;
        frameWidth = radius * 2;
        frameHeight = radius * 2;
        drawPolarMatrix(context, prepared.matrix, polar, (value) => paletteColor(value, 0, globalHigh, palette));
      } else {
        const dimensions = spatialGridDimensions(
          miniGridWidth,
          miniGridHeight,
          prepared.xGroups.length,
          prepared.yGroups.length,
        );
        const { cellWidth, cellHeight, gridWidth, gridHeight } = dimensions;
        const rect: RectLayout = {
          kind: "rect",
          x: slotX + Math.max(0, (slotWidth - gridWidth) / 2),
          y: Math.max(0, (miniGridHeight - gridHeight) / 2),
          cellWidth,
          cellHeight,
          width: gridWidth,
          height: gridHeight,
          xGroups: prepared.xGroups,
          yGroups: prepared.yGroups,
        };
        plot = rect;
        frameX = rect.x;
        frameY = rect.y;
        frameWidth = rect.width;
        frameHeight = rect.height;
        drawRectMatrix(context, prepared.matrix, rect, (value) => paletteColor(value, 0, globalHigh, palette));
      }
      const [start, end] = timeBounds(meta, groups[binIndex]);
      const selected = halfOpenRangesOverlap(start, end, selectionLower, selectionUpper);
      context.strokeStyle = selected ? "#16a34a" : "#cbd5e1";
      context.lineWidth = selected ? 2 : 1;
      if (plot.kind === "polar") {
        context.beginPath();
        context.arc(plot.cx, plot.cy, frameWidth / 2, 0, Math.PI * 2);
        context.stroke();
      } else {
        context.strokeRect(frameX, frameY, frameWidth, frameHeight);
      }
      const labelY = miniGridHeight + 8;
      drawText(context, timelineIntervalLabel(start, end, (value) => formatNumber(value)), slotX + slotWidth / 2, labelY, {
        color: selected ? "#15803d" : "#475467",
        font: canvasFont(11, selected ? 600 : undefined),
        align: "center",
        baseline: "top",
      });
      nextLayouts.push({
        plot,
        binIndex,
        frameX,
        frameY,
        frameWidth,
        frameHeight,
        labelBottom: labelY + 20,
      });
    }
    layouts.current = nextLayouts;
  }, [
    endBin,
    globalHigh,
    groups,
    gapX,
    meta,
    miniGridHeight,
    miniGridWidth,
    miniLeft,
    palette,
    polarLayout,
    polarRadius,
    preparedBins,
    rowHeight,
    selectionLower,
    selectionUpper,
    slotWidth,
    startBin,
    visible,
    width,
  ]);

  const eventHit = (
    event: React.PointerEvent<HTMLCanvasElement> | React.MouseEvent<HTMLCanvasElement>,
  ): { cell: CellRef | null; bin: number | null } => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - bounds.left;
    const y = event.clientY - bounds.top;
    const tile = layouts.current.find((candidate) =>
      x >= candidate.frameX
      && x <= candidate.frameX + candidate.frameWidth
      && y >= candidate.frameY
      && y <= candidate.labelBottom,
    );
    if (tile) {
      if (y <= tile.frameY + tile.frameHeight) {
        return { cell: hitTest(tile.plot, x, y), bin: tile.binIndex };
      }
      return { cell: null, bin: tile.binIndex };
    }
    return { cell: null, bin: null };
  };

  const firstBounds = timeBounds(meta, groups[startBin]);
  const lastBounds = timeBounds(meta, groups[endBin - 1]);
  return (
    <div
      className="timeline-tile-row"
      ref={host}
      style={{ height: rowHeight }}
      role="group"
      aria-label={`Timeline map row ${rowIndex + 1}, ${formatNumber(firstBounds[0])} to ${formatNumber(lastBounds[1])} ms`}
    >
      {visible && (
        <>
          <canvas
            ref={canvas}
            aria-label={`RF maps ${startBin + 1} through ${endBin} of ${groups.length}`}
            onPointerMove={(event) => {
              const hit = eventHit(event);
              const bounds = event.currentTarget.getBoundingClientRect();
              setTooltip(hit.cell ? {
                x: event.clientX - bounds.left + 14,
                y: event.clientY - bounds.top + 14,
                lines: [
                  `timeline bin ${timelineIntervalLabel(
                    timeBounds(meta, groups[hit.bin!])[0],
                    timeBounds(meta, groups[hit.bin!])[1],
                    (value) => formatNumber(value),
                  )}`,
                  ...tooltipFor(hit.cell, hit.bin!),
                ],
              } : null);
            }}
            onPointerLeave={() => setTooltip(null)}
            onClick={(event) => {
              const hit = eventHit(event);
              if (hit.cell) onSelectCell(hit.cell);
              if (hit.bin != null) onSelectTime(hit.bin, event.shiftKey || event.ctrlKey || event.metaKey || event.altKey);
            }}
          />
          {tooltip && (
            <div className="plot-tooltip" style={{ left: tooltip.x, top: tooltip.y }}>
              {tooltip.lines.map((line, index) => <span key={`${index}-${line}`}>{line}</span>)}
            </div>
          )}
        </>
      )}
    </div>
  );
});

const TIMELINE_CHART_HEIGHT = 276;
const TIMELINE_CHART_X = 72;
const TIMELINE_CHART_Y = 88;
const TIMELINE_CHART_PLOT_HEIGHT = 132;

export function TimelinePlot({
  meta,
  counts,
  state,
  selectedCell,
  onSelectCell,
  onSelectTime,
  onScrollFraction,
}: TimelinePlotProps) {
  const wrapper = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const chartCanvas = useRef<HTMLCanvasElement>(null);
  const scrollTimer = useRef<number | null>(null);
  const resetProgrammaticScroll = useRef<number | null>(null);
  const latestScrollFraction = useRef(state.timelineScrollFraction);
  const lastPublishedScroll = useRef(state.timelineScrollFraction);
  const locallyPublishedScroll = useRef<number | null>(null);
  const programmaticScroll = useRef(false);
  const scrollPublisher = useRef(onScrollFraction);
  const [scrollRoot, setScrollRoot] = useState<HTMLDivElement | null>(null);
  const { width } = useContainerSize(wrapper, 480, 280);
  const groups = useMemo(() => timeGroups(meta, state.timeResolutionMs), [meta, state.timeResolutionMs]);
  const preparedBins = useMemo<PreparedTimelineBin[]>(() => groups.map(([start, end]) =>
    prepareResponseMatrix(
      counts,
      meta,
      [start, end],
      state.valueMode,
      state.xBins,
      state.yBins,
      state.flipY,
      state.smoothRadius,
    ),
  ), [counts, groups, meta, state.flipY, state.smoothRadius, state.valueMode, state.xBins, state.yBins]);
  const globalHigh = useMemo(() => preparedBins.reduce<number>((high, prepared) =>
    prepared.matrix.reduce<number>((matrixHigh, row) =>
      row.reduce<number>((rowHigh, value) => value == null || !Number.isFinite(value) ? rowHigh : Math.max(rowHigh, value), matrixHigh),
    high),
  1), [preparedBins]);
  const sourceXCount = preparedBins[0]?.xGroups.length ?? 1;
  const sourceYCount = preparedBins[0]?.yGroups.length ?? 1;
  const sourceRingSpan = polarRingSpan(sourceYCount);
  const polarDiameter = 2 * (INNER_BLANK_ROWS + sourceYCount * sourceRingSpan);
  const layoutXCount = state.polarLayout ? polarDiameter : sourceXCount;
  const layoutYCount = state.polarLayout ? polarDiameter : sourceYCount;
  const gridLayout = timelineGridLayout({
    width,
    count: groups.length,
    xCount: layoutXCount,
    yCount: layoutYCount,
  });
  const miniLeft = gridLayout.left;
  const gapX = gridLayout.gapX;
  const columnCount = gridLayout.columns;
  const rowCount = gridLayout.rows;
  const slotWidth = gridLayout.slotWidth;
  const miniGridWidth = gridLayout.gridWidth;
  const miniGridHeight = gridLayout.gridHeight;
  const rowHeight = gridLayout.rowHeight;
  const contentHeight = TIMELINE_CHART_HEIGHT + rowCount * rowHeight;
  const selectionLower = Math.min(state.timelineStartMs, state.timelineEndMs);
  const selectionUpper = Math.max(state.timelineStartMs, state.timelineEndMs);

  const assignScroller = useCallback((node: HTMLDivElement | null) => {
    scroller.current = node;
    if (node) setScrollRoot((current) => current === node ? current : node);
  }, []);

  const tooltipFor = useCallback((cell: CellRef, binIndex: number) =>
    tooltipLines(meta, counts, state, cell, binIndex), [
    counts,
    meta,
    state.activeTimeCenterMs,
    state.rfEndMs,
    state.rfStartMs,
    state.timeResolutionMs,
    state.valueMode,
  ]);

  useEffect(() => {
    scrollPublisher.current = onScrollFraction;
  }, [onScrollFraction]);

  useEffect(() => {
    lastPublishedScroll.current = state.timelineScrollFraction;
  }, [state.timelineScrollFraction]);

  useEffect(() => {
    if (!chartCanvas.current || !groups.length) return;
    const context = contextFor(chartCanvas.current, width, TIMELINE_CHART_HEIGHT);
    const timeTotals = allPositionsTimelineValues(counts, meta, groups, state.valueMode);
    const selectedValues = groupResponseValues(counts, meta, selectedCell, groups, state.valueMode).map((value) => value ?? 0);
    const chartX = TIMELINE_CHART_X;
    const chartY = TIMELINE_CHART_Y;
    const chartWidth = Math.max(320, width - TIMELINE_CHART_X * 2);
    const chartHeight = TIMELINE_CHART_PLOT_HEIGHT;
    const axisStart = meta.timeBinEdges[0] * 1000;
    const axisEnd = meta.timeBinEdges.at(-1)! * 1000;
    drawText(context, `Timeline and ${groups.length} bin maps`, 20, 22, { font: canvasFont(19, 700), color: "#111827" });
    drawText(
      context,
      `Timeline selection ${formatNumber(state.timelineStartMs)} to ${formatNumber(state.timelineEndMs)} ms; time res ${formatNumber(state.timeResolutionMs)} ms; ${state.valueMode}.`,
      20,
      44,
      { color: "#667085" },
    );
    const zeroX = chartX + (chartWidth * (0 - axisStart)) / Math.max(axisEnd - axisStart, 1);
    if (axisStart < 0 && axisEnd >= 0) {
      context.fillStyle = "#f8fafc";
      context.fillRect(chartX, chartY, zeroX - chartX, chartHeight);
    }
    context.strokeStyle = "#cbd5e1";
    context.strokeRect(chartX, chartY, chartWidth, chartHeight);
    if (axisStart <= 0 && axisEnd >= 0) {
      context.save();
      context.setLineDash([4, 3]);
      context.strokeStyle = "#7c3aed";
      context.beginPath();
      context.moveTo(zeroX, chartY);
      context.lineTo(zeroX, chartY + chartHeight);
      context.stroke();
      context.restore();
      drawText(context, "VS 0 ms", zeroX + 4, chartY + 11, { color: "#6d28d9", font: canvasFont(10, 600) });
    }
    const legendY = chartY - 11;
    context.strokeStyle = "#2563eb";
    context.lineWidth = 2;
    context.beginPath();
    context.moveTo(chartX, legendY);
    context.lineTo(chartX + 16, legendY);
    context.stroke();
    drawText(
      context,
      state.valueMode === "Spike count" ? "All positions (sum)" : "All positions (pooled rate)",
      chartX + 21,
      legendY,
      { color: "#2563eb", font: canvasFont(11), baseline: "middle" },
    );
    context.strokeStyle = "#dc2626";
    context.beginPath();
    context.moveTo(chartX + 196, legendY);
    context.lineTo(chartX + 212, legendY);
    context.stroke();
    drawText(context, "Selected cell", chartX + 217, legendY, {
      color: "#dc2626", font: canvasFont(11), baseline: "middle",
    });
    const drawSeries = (values: number[], color: string, maximum: number, lineWidth: number) => {
      const points = values.map((value, index) => {
        const bounds = timeBounds(meta, groups[index]);
        return {
          x: timelineChartX((bounds[0] + bounds[1]) / 2, axisStart, axisEnd, chartX, chartWidth),
          y: chartY + chartHeight - (chartHeight * value) / Math.max(maximum, 1),
        };
      });
      if (points.length < 2) return;
      context.beginPath();
      context.moveTo(points[0].x, points[0].y);
      for (let index = 1; index < points.length; index += 1) {
        context.lineTo(points[index].x, points[index].y);
      }
      context.strokeStyle = color;
      context.lineWidth = lineWidth;
      context.lineJoin = "round";
      context.stroke();
    };
    const allMax = Math.max(1, ...timeTotals);
    const selectedMax = Math.max(1, ...selectedValues);
    drawSeries(timeTotals, "#2563eb", allMax, 2);
    drawSeries(selectedValues, "#dc2626", selectedMax, 1.8);
    const redAxisX = chartX - 20;
    const blueAxisX = chartX + chartWidth + 20;
    context.strokeStyle = "#dc2626";
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(redAxisX, chartY);
    context.lineTo(redAxisX, chartY + chartHeight);
    context.moveTo(redAxisX - 4, chartY);
    context.lineTo(redAxisX, chartY);
    context.moveTo(redAxisX - 4, chartY + chartHeight);
    context.lineTo(redAxisX, chartY + chartHeight);
    context.stroke();
    drawText(context, formatResponse(selectedMax, state.valueMode), redAxisX - 7, chartY, {
      color: "#dc2626", align: "right", font: canvasFont(10),
    });
    drawText(context, "0", redAxisX - 7, chartY + chartHeight, {
      color: "#dc2626", align: "right", font: canvasFont(10),
    });
    context.strokeStyle = "#2563eb";
    context.beginPath();
    context.moveTo(blueAxisX, chartY);
    context.lineTo(blueAxisX, chartY + chartHeight);
    context.moveTo(blueAxisX, chartY);
    context.lineTo(blueAxisX + 4, chartY);
    context.moveTo(blueAxisX, chartY + chartHeight);
    context.lineTo(blueAxisX + 4, chartY + chartHeight);
    context.stroke();
    drawText(context, formatResponse(allMax, state.valueMode), blueAxisX + 7, chartY, {
      color: "#2563eb", font: canvasFont(10),
    });
    drawText(context, "0", blueAxisX + 7, chartY + chartHeight, {
      color: "#2563eb", font: canvasFont(10),
    });
    if (selectionLower > axisStart || selectionUpper < axisEnd) {
      const startX = chartX + chartWidth * ((selectionLower - axisStart) / Math.max(axisEnd - axisStart, 1));
      const endX = chartX + chartWidth * ((selectionUpper - axisStart) / Math.max(axisEnd - axisStart, 1));
      context.strokeStyle = "#16a34a";
      context.lineWidth = 2;
      context.strokeRect(startX, chartY, endX - startX, chartHeight);
    }
    const tickEvery = Math.max(1, Math.ceil(groups.length / 5));
    for (let boundary = 0; boundary <= groups.length; boundary += tickEvery) {
      const safeBoundary = Math.min(boundary, groups.length);
      const time = safeBoundary === 0 ? axisStart : timeBounds(meta, groups[safeBoundary - 1])[1];
      const x = timelineChartX(time, axisStart, axisEnd, chartX, chartWidth);
      context.strokeStyle = "#64748b";
      context.beginPath();
      context.moveTo(x, chartY + chartHeight);
      context.lineTo(x, chartY + chartHeight + 4);
      context.stroke();
      drawText(context, formatNumber(time), x, chartY + chartHeight + 18, {
        color: "#475467",
        align: safeBoundary === 0 ? "left" : safeBoundary === groups.length ? "right" : "center",
        font: canvasFont(10),
      });
      if (safeBoundary === groups.length) break;
    }
    drawText(context, "Time from VS onset (ms)", chartX + chartWidth / 2, chartY + chartHeight + 38, { color: "#475467", align: "center", font: canvasFont(11) });
  }, [
    counts,
    groups,
    meta,
    selectedCell,
    selectionLower,
    selectionUpper,
    state.timeResolutionMs,
    state.timelineEndMs,
    state.timelineStartMs,
    state.valueMode,
    width,
  ]);

  useEffect(() => {
    const localEcho = locallyPublishedScroll.current;
    if (localEcho != null && Math.abs(localEcho - state.timelineScrollFraction) < 0.000001) {
      locallyPublishedScroll.current = null;
      lastPublishedScroll.current = state.timelineScrollFraction;
      return;
    }
    locallyPublishedScroll.current = null;
    if (scrollTimer.current != null) {
      window.clearTimeout(scrollTimer.current);
      scrollTimer.current = null;
    }
    latestScrollFraction.current = state.timelineScrollFraction;
    lastPublishedScroll.current = state.timelineScrollFraction;
    const node = scroller.current;
    if (!node) return;
    const maximum = node.scrollHeight - node.clientHeight;
    if (maximum <= 0) return;
    const target = state.timelineScrollFraction * maximum;
    if (Math.abs(node.scrollTop - target) <= 1) return;
    programmaticScroll.current = true;
    node.scrollTop = target;
    if (resetProgrammaticScroll.current != null) window.cancelAnimationFrame(resetProgrammaticScroll.current);
    resetProgrammaticScroll.current = window.requestAnimationFrame(() => {
      programmaticScroll.current = false;
      resetProgrammaticScroll.current = null;
    });
  }, [contentHeight, state.timelineScrollFraction]);

  useEffect(() => () => {
    if (scrollTimer.current != null) window.clearTimeout(scrollTimer.current);
    if (resetProgrammaticScroll.current != null) window.cancelAnimationFrame(resetProgrammaticScroll.current);
  }, []);

  const scheduleScrollPublish = useCallback((node: HTMLDivElement) => {
    if (programmaticScroll.current) return;
    const maximum = node.scrollHeight - node.clientHeight;
    if (maximum <= 0) return;
    latestScrollFraction.current = node.scrollTop / maximum;
    if (scrollTimer.current != null) return;
    scrollTimer.current = window.setTimeout(() => {
      scrollTimer.current = null;
      const fraction = latestScrollFraction.current;
      if (Math.abs(fraction - lastPublishedScroll.current) < 0.001) return;
      lastPublishedScroll.current = fraction;
      locallyPublishedScroll.current = fraction;
      scrollPublisher.current(fraction);
    }, 80);
  }, []);

  const chartBinFromEvent = (event: React.MouseEvent<HTMLCanvasElement>): number | null => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - bounds.left;
    const y = event.clientY - bounds.top;
    const chartX = TIMELINE_CHART_X;
    const chartWidth = Math.max(320, width - TIMELINE_CHART_X * 2);
    if (x < chartX || x > chartX + chartWidth || y < TIMELINE_CHART_Y || y > TIMELINE_CHART_Y + TIMELINE_CHART_PLOT_HEIGHT) return null;
    const axisStart = meta.timeBinEdges[0] * 1000;
    const axisEnd = meta.timeBinEdges.at(-1)! * 1000;
    const time = axisStart + ((x - chartX) / chartWidth) * (axisEnd - axisStart);
    return timelineBinAtTime(time, groups.map((group) => timeBounds(meta, group)));
  };

  return (
    <div className="timeline-wrapper" ref={wrapper}>
      <div className="timeline-scroll" ref={assignScroller} onScroll={(event) => scheduleScrollPublish(event.currentTarget)}>
        <div className="timeline-content" style={{ minHeight: contentHeight }}>
          <div className="canvas-stage timeline-chart">
            <canvas
              ref={chartCanvas}
              aria-label={`Timeline response summary for cluster ${state.clusterId}`}
              onClick={(event) => {
                const bin = chartBinFromEvent(event);
                if (bin != null) onSelectTime(bin, event.shiftKey || event.ctrlKey || event.metaKey || event.altKey);
              }}
            />
          </div>
          <div className="timeline-map-rows" aria-label={`All ${groups.length} timeline RF maps`}>
            {Array.from({ length: rowCount }, (_, rowIndex) => {
              const startBin = rowIndex * columnCount;
              const endBin = Math.min(groups.length, startBin + columnCount);
              return (
                <TimelineMapRow
                  key={`${startBin}-${endBin}`}
                  rowIndex={rowIndex}
                  startBin={startBin}
                  endBin={endBin}
                  width={width}
                  miniLeft={miniLeft}
                  gapX={gapX}
                  slotWidth={slotWidth}
                  rowHeight={rowHeight}
                  miniGridWidth={miniGridWidth}
                  miniGridHeight={miniGridHeight}
                  preparedBins={preparedBins}
                  groups={groups}
                  meta={meta}
                  globalHigh={globalHigh}
                  palette={state.palette}
                  polarLayout={state.polarLayout}
                  polarRadius={state.polarRadius}
                  selectionLower={selectionLower}
                  selectionUpper={selectionUpper}
                  scrollRoot={scrollRoot}
                  tooltipFor={tooltipFor}
                  onSelectCell={onSelectCell}
                  onSelectTime={onSelectTime}
                />
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
