import { useEffect, useMemo, useRef, useState } from "react";
import { canvasFont } from "../canvasFont";
import { probeUnitsInRegion } from "../probeSelection";
import type { ProbeGeometry } from "../types";

export interface ProbeSelection {
  xMin: number;
  xMax: number;
  yMin: number;
  yMax: number;
}

interface ProbeLayoutProps {
  geometry: ProbeGeometry;
  availableUnitIds: number[];
  currentClusterId: number;
  selection: ProbeSelection | null;
  onSelection: (selection: ProbeSelection | null, unitIds: number[]) => void;
  onCluster: (clusterId: number) => void;
}

interface PlotTransform {
  xMin: number;
  xMax: number;
  yMin: number;
  yMax: number;
  left: number;
  top: number;
  width: number;
  height: number;
}

function paddedRange(values: number[], fallback: readonly [number, number]): readonly [number, number] {
  if (!values.length) return fallback;
  const low = Math.min(...values);
  const high = Math.max(...values);
  const span = Math.max(high - low, 20);
  return [low - span * 0.08, high + span * 0.08];
}

function toCanvas(transform: PlotTransform, x: number, y: number): readonly [number, number] {
  return [
    transform.left + ((x - transform.xMin) / (transform.xMax - transform.xMin)) * transform.width,
    transform.top + (1 - (y - transform.yMin) / (transform.yMax - transform.yMin)) * transform.height,
  ];
}

function fromCanvas(transform: PlotTransform, x: number, y: number): readonly [number, number] {
  return [
    transform.xMin + ((x - transform.left) / transform.width) * (transform.xMax - transform.xMin),
    transform.yMin + (1 - (y - transform.top) / transform.height) * (transform.yMax - transform.yMin),
  ];
}

export default function ProbeLayout({
  geometry,
  availableUnitIds,
  currentClusterId,
  selection,
  onSelection,
  onCluster,
}: ProbeLayoutProps) {
  const container = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const transform = useRef<PlotTransform | null>(null);
  const dragStart = useRef<readonly [number, number] | null>(null);
  const draftSelection = useRef<ProbeSelection | null>(null);
  const [size, setSize] = useState({ width: 300, height: 420 });
  const [draft, setDraft] = useState<ProbeSelection | null>(null);
  const [hoverUnit, setHoverUnit] = useState<number | null>(null);
  const width = size.width;
  const height = Math.max(280, Math.min(430, size.height - 43));
  const availableSet = useMemo(() => new Set(availableUnitIds), [availableUnitIds]);
  const units = useMemo(
    () => geometry.units.filter((unit) => availableSet.has(unit.unitId)),
    [availableSet, geometry.units],
  );

  useEffect(() => {
    const node = container.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setSize({
      width: Math.max(240, Math.floor(entry.contentRect.width)),
      height: Math.max(323, Math.floor(entry.contentRect.height)),
    }));
    observer.observe(node);
    const bounds = node.getBoundingClientRect();
    setSize({
      width: Math.max(240, Math.floor(bounds.width)),
      height: Math.max(323, Math.floor(bounds.height)),
    });
    return () => observer.disconnect();
  }, []);

  const shankColors = useMemo(() => {
    const colors = ["#64748b", "#8b5cf6", "#0f766e", "#c2410c", "#0369a1", "#be185d"];
    return new Map([...new Set(geometry.channels.map((channel) => channel.shank))].map((shank, index) => [shank, colors[index % colors.length]]));
  }, [geometry]);

  useEffect(() => {
    const node = canvas.current;
    if (!node) return;
    const ratio = Math.max(1, window.devicePixelRatio || 1);
    node.width = width * ratio;
    node.height = height * ratio;
    node.style.width = `${width}px`;
    node.style.height = `${height}px`;
    const context = node.getContext("2d")!;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    const allX = [...geometry.channels.map((item) => item.x), ...units.map((item) => item.x)];
    const allY = [...geometry.channels.map((item) => item.y), ...units.map((item) => item.y)];
    const [xMin, xMax] = paddedRange(allX, [-100, 100]);
    const [yMin, yMax] = paddedRange(allY, [0, 4000]);
    const left = width < 320 ? 36 : 44;
    const right = 13;
    const top = 47;
    const bottom = 42;
    const next: PlotTransform = {
      xMin, xMax, yMin, yMax, left, top,
      width: Math.max(80, width - left - right),
      height: Math.max(150, height - top - bottom),
    };
    transform.current = next;
    context.fillStyle = "#101828";
    context.font = canvasFont(14, 700);
    context.fillText(`${geometry.probe} layout`, 8, 18);
    context.fillStyle = "#667085";
    context.font = canvasFont(10.5);
    context.fillText(`${geometry.channels.length} channels · ${units.length}/${availableUnitIds.length} RF units positioned`, 8, 36);
    context.fillStyle = "#fbfcfd";
    context.fillRect(next.left, next.top, next.width, next.height);
    context.strokeStyle = "#d0d5dd";
    context.strokeRect(next.left, next.top, next.width, next.height);
    const shanks = [...new Set(geometry.channels.map((channel) => channel.shank))];
    shanks.forEach((shank) => {
      const channels = geometry.channels.filter((channel) => channel.shank === shank);
      if (!channels.length) return;
      const channelX = channels.map((channel) => channel.x);
      const channelY = channels.map((channel) => channel.y);
      const xRange = paddedRange(channelX, [0, 20]);
      const yRange = paddedRange(channelY, [0, 100]);
      const [left, bottom] = toCanvas(next, xRange[0], yRange[0]);
      const [right, top] = toCanvas(next, xRange[1], yRange[1]);
      context.fillStyle = `${shankColors.get(shank)}18`;
      context.fillRect(left, top, right - left, bottom - top);
    });
    geometry.channels.forEach((channel) => {
      const [x, y] = toCanvas(next, channel.x, channel.y);
      context.fillStyle = shankColors.get(channel.shank) ?? "#64748b";
      context.fillRect(x - 3.2, y - 1.8, 6.4, 3.6);
    });
    const active = draft ?? selection;
    if (active) {
      const [left, bottom] = toCanvas(next, active.xMin, active.yMin);
      const [right, top] = toCanvas(next, active.xMax, active.yMax);
      context.fillStyle = "rgba(37, 99, 235, 0.10)";
      context.fillRect(left, top, right - left, bottom - top);
      context.strokeStyle = "#2563eb";
      context.lineWidth = 1.5;
      context.strokeRect(left, top, right - left, bottom - top);
    }
    units.forEach((unit) => {
      const [x, y] = toCanvas(next, unit.x, unit.y);
      const isCurrent = unit.unitId === currentClusterId;
      const isHover = unit.unitId === hoverUnit;
      context.beginPath();
      context.arc(x, y, isCurrent ? 6 : isHover ? 5 : 3.4, 0, Math.PI * 2);
      context.fillStyle = isCurrent ? "#dc2626" : "#111827";
      context.fill();
      if (isCurrent) {
        context.strokeStyle = "white";
        context.lineWidth = 2;
        context.stroke();
      }
    });
    context.fillStyle = "#475467";
    context.font = canvasFont(10);
    context.textAlign = "center";
    context.fillText("lateral position (µm)", next.left + next.width / 2, height - 15);
    context.save();
    context.translate(11, next.top + next.height / 2);
    context.rotate(-Math.PI / 2);
    context.fillText("depth / y (µm)", 0, 0);
    context.restore();
  }, [availableUnitIds.length, currentClusterId, draft, geometry, height, hoverUnit, selection, shankColors, units, width]);

  const pointerCoordinates = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    return [event.clientX - bounds.left, event.clientY - bounds.top] as const;
  };

  const nearestUnit = (screenX: number, screenY: number): number | null => {
    if (!transform.current) return null;
    let nearest: number | null = null;
    let distance = 11;
    units.forEach((unit) => {
      const [x, y] = toCanvas(transform.current!, unit.x, unit.y);
      const candidate = Math.hypot(x - screenX, y - screenY);
      if (candidate < distance) {
        distance = candidate;
        nearest = unit.unitId;
      }
    });
    return nearest;
  };

  return (
    <div className="probe-view" ref={container}>
      <canvas
        ref={canvas}
        aria-label={`${geometry.probe} channel and unit layout`}
        onPointerDown={(event) => {
          if (!transform.current) return;
          event.currentTarget.setPointerCapture(event.pointerId);
          dragStart.current = pointerCoordinates(event);
          draftSelection.current = null;
          setDraft(null);
        }}
        onPointerMove={(event) => {
          const [x, y] = pointerCoordinates(event);
          setHoverUnit(nearestUnit(x, y));
          if (!dragStart.current || !transform.current) return;
          const [startX, startY] = dragStart.current;
          if (Math.hypot(x - startX, y - startY) < 3) return;
          const first = fromCanvas(transform.current, startX, startY);
          const last = fromCanvas(transform.current, x, y);
          const nextDraft = {
            xMin: Math.min(first[0], last[0]), xMax: Math.max(first[0], last[0]),
            yMin: Math.min(first[1], last[1]), yMax: Math.max(first[1], last[1]),
          };
          draftSelection.current = nextDraft;
          setDraft(nextDraft);
        }}
        onPointerLeave={() => setHoverUnit(null)}
        onPointerUp={(event) => {
          const [x, y] = pointerCoordinates(event);
          const dragged = draftSelection.current;
          const clickedUnit = nearestUnit(x, y);
          let next = dragged;
          if (!next && clickedUnit == null && transform.current) {
            const clickedChannel = geometry.channels
              .map((channel) => ({ channel, point: toCanvas(transform.current!, channel.x, channel.y) }))
              .sort((a, b) => Math.hypot(a.point[0] - x, a.point[1] - y) - Math.hypot(b.point[0] - x, b.point[1] - y))[0];
            if (clickedChannel && Math.hypot(clickedChannel.point[0] - x, clickedChannel.point[1] - y) < 12) {
              next = {
                xMin: clickedChannel.channel.x - 80,
                xMax: clickedChannel.channel.x + 80,
                yMin: clickedChannel.channel.y - 37.5,
                yMax: clickedChannel.channel.y + 37.5,
              };
            }
          }
          dragStart.current = null;
          draftSelection.current = null;
          setDraft(null);
          if (clickedUnit != null && !dragged) onCluster(clickedUnit);
          if (next) onSelection(next, probeUnitsInRegion(geometry, next, availableUnitIds));
        }}
      />
      <div className="probe-toolbar">
        <span>
          {selection
              ? `${probeUnitsInRegion(geometry, selection, availableUnitIds).length
                ? `${probeUnitsInRegion(geometry, selection, availableUnitIds).length} units in region`
                : "No units in region"}`
              : "Click a channel for a 160 × 75 µm region, or drag any region."}
        </span>
        {hoverUnit != null && <span className="probe-hover">Cluster {hoverUnit}</span>}
        <button
          className="secondary-button compact"
          type="button"
          disabled={!selection}
          onClick={() => onSelection(null, [...availableUnitIds])}
        >
          Clear filter
        </button>
      </div>
    </div>
  );
}
