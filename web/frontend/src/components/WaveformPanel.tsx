import { useEffect, useRef, useState } from "react";
import { canvasFont } from "../canvasFont";
import { formatNumber } from "../math";
import type { WaveformArtifact, WaveformChannelMode } from "../types";

interface WaveformPanelProps {
  artifact: WaveformArtifact | null;
  clusterId: number;
  loading: boolean;
  error: string;
  visible: boolean;
  mode: WaveformChannelMode;
  blocked: boolean;
  onVisibleChange: (visible: boolean) => void;
  onModeChange: (mode: WaveformChannelMode) => void;
}

type AvailableWaveform = Extract<WaveformArtifact, { available: true }>;

function interpolate(start: readonly number[], end: readonly number[], fraction: number): string {
  const value = Math.max(0, Math.min(1, fraction));
  const channels = start.map((component, index) =>
    Math.round(component + (end[index] - component) * value));
  return `rgb(${channels.join(",")})`;
}

function waveformColor(value: number, amplitude: number): string {
  const normalized = Math.max(-1, Math.min(1, value / Math.max(amplitude, 1e-12)));
  const fraction = (normalized + 1) / 2;
  const stops = [
    [0, [5, 48, 97]],
    [0.25, [67, 147, 195]],
    [0.5, [247, 247, 247]],
    [0.75, [214, 96, 77]],
    [1, [103, 0, 31]],
  ] as const;
  for (let index = 0; index < stops.length - 1; index += 1) {
    const [leftAt, leftColor] = stops[index];
    const [rightAt, rightColor] = stops[index + 1];
    if (fraction <= rightAt) {
      return interpolate(leftColor, rightColor, (fraction - leftAt) / (rightAt - leftAt));
    }
  }
  return interpolate(stops.at(-1)![1], stops.at(-1)![1], 0);
}

function WaveformCanvas({ artifact, expanded, onExpand }: {
  artifact: AvailableWaveform;
  expanded: boolean;
  onExpand?: () => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(expanded ? 900 : 310);

  useEffect(() => {
    const node = host.current;
    if (!node) return;
    const update = (next: number) => setWidth(Math.max(expanded ? 520 : 230, Math.floor(next)));
    const observer = new ResizeObserver(([entry]) => update(entry.contentRect.width));
    observer.observe(node);
    update(node.getBoundingClientRect().width);
    return () => observer.disconnect();
  }, [expanded]);

  useEffect(() => {
    const node = canvas.current;
    if (!node) return;
    const height = expanded
      ? Math.max(420, Math.min(680, width * 0.62))
      : Math.max(155, Math.min(215, width * 0.62));
    const ratio = Math.max(1, window.devicePixelRatio || 1);
    node.width = Math.floor(width * ratio);
    node.height = Math.floor(height * ratio);
    node.style.width = `${width}px`;
    node.style.height = `${height}px`;
    const context = node.getContext("2d");
    if (!context) return;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, width, height);

    const labelWidth = expanded ? 76 : 48;
    const legendWidth = expanded ? 68 : 44;
    const top = expanded ? 22 : 9;
    const bottomPadding = expanded ? 48 : 29;
    const grid = {
      left: labelWidth,
      top,
      right: width - legendWidth,
      bottom: height - bottomPadding,
    };
    const rows = artifact.valuesUv.length;
    const columns = artifact.timesMs.length;
    const rowHeight = (grid.bottom - grid.top) / rows;
    const timeLow = artifact.timeEdgesMs[0];
    const timeHigh = artifact.timeEdgesMs.at(-1)!;
    const xForTime = (value: number) => grid.left
      + (value - timeLow) / (timeHigh - timeLow) * (grid.right - grid.left);
    const amplitude = Math.max(artifact.amplitudeLimitUv, 1e-12);

    artifact.valuesUv.forEach((row, rowIndex) => {
      const y0 = grid.top + rowHeight * rowIndex;
      const y1 = grid.top + rowHeight * (rowIndex + 1);
      row.forEach((value, columnIndex) => {
        const x0 = xForTime(artifact.timeEdgesMs[columnIndex]);
        const x1 = xForTime(artifact.timeEdgesMs[columnIndex + 1]);
        context.fillStyle = waveformColor(value, amplitude);
        context.fillRect(x0, y0, Math.max(1, x1 - x0 + 0.4), Math.max(1, y1 - y0 + 0.4));
      });
      context.fillStyle = rowIndex === artifact.bestChannelRow ? "#b42318" : "#475467";
      context.font = canvasFont(expanded ? 12 : 9, rowIndex === artifact.bestChannelRow ? 600 : undefined);
      context.textAlign = "right";
      context.textBaseline = "middle";
      context.fillText(
        artifact.channelLabels[rowIndex],
        grid.left - (expanded ? 9 : 5),
        (y0 + y1) / 2,
      );
    });

    const bestTop = grid.top + rowHeight * artifact.bestChannelRow;
    context.strokeStyle = "#dc2626";
    context.lineWidth = expanded ? 2 : 1.5;
    context.strokeRect(
      grid.left,
      bestTop,
      grid.right - grid.left,
      rowHeight,
    );
    if (timeLow <= 0 && timeHigh >= 0) {
      const zeroX = xForTime(0);
      context.strokeStyle = "rgba(17, 24, 39, .78)";
      context.lineWidth = 1;
      context.beginPath();
      context.moveTo(zeroX, grid.top);
      context.lineTo(zeroX, grid.bottom);
      context.stroke();
    }
    context.strokeStyle = "#667085";
    context.lineWidth = 1;
    context.strokeRect(grid.left, grid.top, grid.right - grid.left, grid.bottom - grid.top);

    const ticks = Array.from(new Set([0, Math.floor(columns / 2), columns - 1]));
    context.fillStyle = "#475467";
    context.font = canvasFont(expanded ? 11 : 8.5);
    context.textAlign = "center";
    context.textBaseline = "top";
    ticks.forEach((index) => {
      context.fillText(
        formatNumber(artifact.timesMs[index], expanded ? 3 : 2),
        xForTime(artifact.timesMs[index]),
        grid.bottom + 5,
      );
    });
    if (expanded) {
      context.fillText("Time from spike (ms)", (grid.left + grid.right) / 2, height - 17);
    }

    const legendX = grid.right + (expanded ? 18 : 10);
    const legendWidthPixels = expanded ? 16 : 10;
    for (let y = Math.round(grid.top); y < Math.round(grid.bottom); y += 1) {
      const fraction = 1 - (y - grid.top) / (grid.bottom - grid.top);
      context.fillStyle = waveformColor((2 * fraction - 1) * amplitude, amplitude);
      context.fillRect(legendX, y, legendWidthPixels, 1.2);
    }
    context.fillStyle = "#475467";
    context.textAlign = "left";
    context.textBaseline = "middle";
    context.font = canvasFont(expanded ? 11 : 8);
    context.fillText(formatNumber(amplitude, 2), legendX + legendWidthPixels + 3, grid.top + 2);
    context.fillText(formatNumber(-amplitude, 2), legendX + legendWidthPixels + 3, grid.bottom - 2);
    context.save();
    context.translate(legendX + legendWidthPixels / 2, (grid.top + grid.bottom) / 2);
    context.rotate(-Math.PI / 2);
    context.textAlign = "center";
    context.fillStyle = "#475467";
    context.fillText("µV", 0, 0);
    context.restore();
  }, [artifact, expanded, width]);

  return (
    <div className={`waveform-canvas-host${expanded ? " expanded" : ""}`} ref={host}>
      <canvas
        ref={canvas}
        aria-label={`Local average waveform for cluster ${artifact.unitId}`}
        title={expanded ? undefined : "Double-click to enlarge"}
        onDoubleClick={onExpand}
      />
    </div>
  );
}

export default function WaveformPanel({
  artifact,
  clusterId,
  loading,
  error,
  visible,
  mode,
  blocked,
  onVisibleChange,
  onModeChange,
}: WaveformPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const available = artifact?.available === true ? artifact : null;

  useEffect(() => {
    if (!expanded) return;
    const close = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopImmediatePropagation();
      setExpanded(false);
    };
    window.addEventListener("keydown", close, true);
    return () => window.removeEventListener("keydown", close, true);
  }, [expanded]);

  useEffect(() => {
    if (!visible || !available) setExpanded(false);
  }, [available, visible]);

  return (
    <section className="sidebar-block waveform-sidebar-block" aria-label="Local average waveform companion">
      <div className="waveform-sidebar-heading">
        <h2>Local waveform</h2>
        <label className="check-row">
          <input
            type="checkbox"
            checked={visible}
            onChange={(event) => onVisibleChange(event.target.checked)}
          />
          <span>Show</span>
        </label>
      </div>
      {visible && (
        <>
          <label className="display-row waveform-mode-row">
            <span>Channels</span>
            <select value={mode} onChange={(event) => onModeChange(event.target.value as WaveformChannelMode)}>
              <option value="same_x_column">Same x column</option>
              <option value="same_shank">Same shank</option>
            </select>
          </label>
          {blocked ? (
            <div className="waveform-status">Unavailable while no Probe-filtered unit is selected.</div>
          ) : loading ? (
            <div className="waveform-status"><span className="spinner small" /> Loading cluster {clusterId} waveform…</div>
          ) : error ? (
            <div className="waveform-status error-state">{error}</div>
          ) : available ? (
            <>
              <WaveformCanvas artifact={available} expanded={false} onExpand={() => setExpanded(true)} />
              <div className="waveform-summary">
                <span>{available.quality} · {available.selectedSpikeCount}/{available.totalSpikeCount} spikes</span>
                <span>{formatNumber(available.timeCoveragePercent, 1)}% time coverage · best ch {available.channels[available.bestChannelRow]?.channelId}</span>
                <span>{mode === "same_x_column" ? "Same x column" : "Same shank"} · best + {Math.max(0, available.channels.length - 1)} nearest · max PTP {formatNumber(available.maxPtpUv, 3)} µV</span>
              </div>
            </>
          ) : (
            <div className="waveform-status">{artifact?.available === false ? artifact.detail : "No local average waveform found."}</div>
          )}
        </>
      )}
      {expanded && available && (
        <div className="modal-backdrop waveform-zoom-backdrop" onMouseDown={(event) => {
          if (event.currentTarget === event.target) setExpanded(false);
        }}>
          <section className="waveform-zoom-dialog" role="dialog" aria-modal="true" aria-label={`Local average waveform for cluster ${clusterId}`}>
            <header>
              <div>
                <strong>Cluster {clusterId} local average waveform</strong>
                <span>{mode === "same_x_column" ? "Same x column" : "Same shank"} · double-click or Esc to close</span>
              </div>
              <button type="button" aria-label="Close waveform enlargement" onClick={() => setExpanded(false)}>×</button>
            </header>
            <WaveformCanvas
              artifact={available}
              expanded
              onExpand={() => setExpanded(false)}
            />
          </section>
        </div>
      )}
    </section>
  );
}
