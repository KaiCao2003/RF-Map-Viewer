import { useEffect, useMemo, useRef, useState } from "react";
import { canvasFont } from "../canvasFont";
import {
  centerHdCurveOnZero,
  DEFAULT_HD_DISPLAY_BINS,
  hdRatePeak,
  headDirectionUnitVector,
  normalizeHdBinCount,
  processHdUnit,
  sharedHdPeak,
  type ProcessedHdCurve,
} from "../hdMath";
import { formatNumber } from "../math";
import type { HdDatasetArtifact, HdViewSettings } from "../types";

interface HdPanelProps {
  artifact: HdDatasetArtifact | null;
  clusterId: number;
  loading: boolean;
  error: string;
  rfPolarLayout: boolean;
  blocked: boolean;
  collapsed: boolean;
  settings: HdViewSettings;
  onSettingsChange: (settings: HdViewSettings) => void;
  onToggleCollapsed: () => void;
  onChoosePath: () => void;
}

function metadataRows(value: unknown, prefix = ""): Array<readonly [string, string]> {
  if (value == null) return prefix ? [[prefix, "n/a"]] : [];
  if (Array.isArray(value)) return [[prefix || "value", JSON.stringify(value)]];
  if (typeof value !== "object") return [[prefix || "value", String(value)]];
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
    metadataRows(child, prefix ? `${prefix}.${key}` : key));
}

function CurvePlot({ curve, mode, maximum }: {
  curve: ProcessedHdCurve;
  mode: "line" | "polar";
  maximum: number;
}) {
  const host = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(460);

  useEffect(() => {
    const node = host.current;
    if (!node) return;
    const update = (next: number) => setWidth(Math.max(260, Math.floor(next)));
    const observer = new ResizeObserver(([entry]) => update(entry.contentRect.width));
    observer.observe(node);
    update(node.getBoundingClientRect().width);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!canvas.current) return;
    const height = Math.max(300, Math.min(410, width * 0.72));
    const ratio = Math.max(1, window.devicePixelRatio || 1);
    canvas.current.width = Math.floor(width * ratio);
    canvas.current.height = Math.floor(height * ratio);
    canvas.current.style.width = `${width}px`;
    canvas.current.style.height = `${height}px`;
    const context = canvas.current.getContext("2d")!;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    const high = Number.isFinite(maximum) && maximum > 1e-12 ? maximum : 1;

    if (mode === "line") {
      const centered = centerHdCurveOnZero(curve);
      const left = 48;
      const top = 28;
      const chartWidth = width - 70;
      const chartHeight = height - 78;
      context.strokeStyle = "#d0d5dd";
      context.strokeRect(left, top, chartWidth, chartHeight);
      context.font = canvasFont(10.5);
      context.fillStyle = "#475467";
      context.textAlign = "right";
      [0, 0.5, 1].forEach((fraction) => {
        const y = top + chartHeight * (1 - fraction);
        if (fraction > 0 && fraction < 1) {
          context.save();
          context.setLineDash([3, 3]);
          context.strokeStyle = "#e4e7ec";
          context.beginPath();
          context.moveTo(left, y);
          context.lineTo(left + chartWidth, y);
          context.stroke();
          context.restore();
        }
        context.fillText(formatNumber(maximum * fraction, 2), left - 7, y + 3);
      });
      context.strokeStyle = "#7c3aed";
      context.lineWidth = 2.4;
      context.beginPath();
      let drawing = false;
      centered.rates.forEach((rate, index) => {
        if (rate == null || !Number.isFinite(rate)) {
          drawing = false;
          return;
        }
        const angle = centered.angles[index];
        const x = left + chartWidth * ((angle + 180) / 360);
        const y = top + chartHeight * (1 - rate / high);
        if (!drawing) context.moveTo(x, y); else context.lineTo(x, y);
        drawing = true;
      });
      context.stroke();
      context.font = canvasFont(10.5);
      context.fillStyle = "#475467";
      context.textAlign = "center";
      [-180, -90, 0, 90, 180].forEach((angle, index) => {
        const x = left + chartWidth * ((angle + 180) / 360);
        context.beginPath();
        context.moveTo(x, top + chartHeight);
        context.lineTo(x, top + chartHeight + 4);
        context.strokeStyle = "#667085";
        context.stroke();
        context.fillText(`${[180, 90, 0, 270, 180][index]}°`, x, top + chartHeight + 18);
      });
      context.textAlign = "center";
      context.fillText("Head direction (0° centered)", left + chartWidth / 2, height - 8);
      return;
    }

    const cx = width / 2;
    const cy = height / 2 + 6;
    const radius = Math.max(90, Math.min(width, height) / 2 - 44);
    context.strokeStyle = "#e4e7ec";
    context.lineWidth = 1;
    for (let ring = 1; ring <= 4; ring += 1) {
      context.beginPath();
      context.arc(cx, cy, radius * ring / 4, 0, Math.PI * 2);
      context.stroke();
    }
    [0, 90, 180, 270].forEach((angle) => {
      const [dx, dy] = headDirectionUnitVector(angle);
      context.beginPath();
      context.moveTo(cx, cy);
      context.lineTo(cx + dx * radius, cy + dy * radius);
      context.stroke();
      context.fillStyle = "#475467";
      context.font = canvasFont(10.5);
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText(`${angle}°`, cx + dx * (radius + 19), cy + dy * (radius + 19));
    });
    context.font = canvasFont(10);
    context.textAlign = "left";
    context.fillStyle = "#667085";
    [0, 0.5, 1].forEach((fraction) => {
      const label = fraction === 0 ? "0" : `${formatNumber(maximum * fraction, 2)} Hz`;
      context.fillText(label, cx + 5, cy - radius * fraction - (fraction ? 3 : -11));
    });
    const points = curve.rates.map((rate, index) => {
      if (rate == null || !Number.isFinite(rate)) return null;
      const [dx, dy] = headDirectionUnitVector(curve.angles[index]);
      const scaled = radius * rate / high;
      return { x: cx + dx * scaled, y: cy + dy * scaled };
    });
    const validCount = points.filter((point) => point != null).length;
    if (validCount === points.length && points.length) {
      context.beginPath();
      context.moveTo(points[0]!.x, points[0]!.y);
      points.slice(1).forEach((point) => context.lineTo(point!.x, point!.y));
      context.closePath();
      context.fillStyle = "rgba(124, 58, 237, .14)";
      context.fill();
      context.strokeStyle = "#7c3aed";
      context.lineWidth = 2.4;
      context.stroke();
    } else if (validCount) {
      const segments: Array<Array<{ x: number; y: number }>> = [];
      let current: Array<{ x: number; y: number }> = [];
      points.forEach((point) => {
        if (point) current.push(point);
        else if (current.length) {
          segments.push(current);
          current = [];
        }
      });
      if (current.length) segments.push(current);
      if (points[0] && points.at(-1) && segments.length > 1) {
        segments[0] = [...segments.at(-1)!, ...segments[0]];
        segments.pop();
      }
      context.strokeStyle = "#7c3aed";
      context.fillStyle = "#7c3aed";
      context.lineWidth = 2.4;
      segments.forEach((segment) => {
        if (segment.length === 1) {
          context.beginPath();
          context.arc(segment[0].x, segment[0].y, 2.2, 0, Math.PI * 2);
          context.fill();
          return;
        }
        context.beginPath();
        context.moveTo(segment[0].x, segment[0].y);
        segment.slice(1).forEach((point) => context.lineTo(point.x, point.y));
        context.stroke();
      });
    }
  }, [curve, maximum, mode, width]);

  return (
    <div className="hd-plot-host" ref={host}>
      <canvas ref={canvas} aria-label={`Head-direction tuning curve shown as a ${mode} plot`} />
    </div>
  );
}

export default function HdPanel({
  artifact,
  clusterId,
  loading,
  error,
  rfPolarLayout,
  blocked,
  collapsed,
  settings,
  onSettingsChange,
  onToggleCollapsed,
  onChoosePath,
}: HdPanelProps) {
  const [showInfo, setShowInfo] = useState(false);
  const { plotMode, displayBins, smoothing, sigmaDeg, compareScale } = settings;
  const sigma = sigmaDeg * DEFAULT_HD_DISPLAY_BINS / 360;
  const unit = blocked ? null : artifact?.units.find((candidate) => candidate.unitId === clusterId) ?? null;
  const options = useMemo(() => ({ displayBins, smoothing, sigma }), [displayBins, sigma, smoothing]);
  const processed = useMemo(() => {
    if (!unit) return { curve: null, error: "" };
    if (!artifact?.occupancyTimeS) {
      return { curve: null, error: "HD occupancy is unavailable." };
    }
    try {
      return { curve: processHdUnit(unit, artifact.occupancyTimeS, options), error: "" };
    } catch (caught) {
      return { curve: null, error: caught instanceof Error ? caught.message : "HD processing failed." };
    }
  }, [artifact?.occupancyTimeS, options, unit]);
  const sharedMaximum = useMemo(() => {
    if (!compareScale || !artifact?.occupancyTimeS) return null;
    try {
      return sharedHdPeak(artifact.units, artifact.occupancyTimeS, options);
    } catch {
      return null;
    }
  }, [artifact, compareScale, options]);
  const maximum = sharedMaximum ?? (processed.curve ? hdRatePeak(processed.curve.rates) : 0);
  const resolvedMode = plotMode === "auto" ? (rfPolarLayout ? "polar" : "line") : plotMode;
  const provenance = useMemo(() => metadataRows(artifact?.metadata), [artifact?.metadata]);

  if (collapsed) {
    return (
      <button className="hd-collapsed-rail" type="button" onClick={onToggleCollapsed}>
        Show HD tuning curve
      </button>
    );
  }

  return (
    <section className="hd-panel" aria-label="HD tuning curve companion">
      <header className="hd-heading">
        <div>
          <h2>HD Tuning Curve</h2>
          <p>{blocked ? "No active cluster" : `cluster ${clusterId}`}{artifact?.sourcePath ? ` · ${artifact.sourcePath}` : ""}</p>
        </div>
        <div className="hd-actions">
          {unit?.hdClass === 1 || unit?.hdClass === 2
            ? <span className={`hd-class-badge class-${unit.hdClass}`}>HD {unit.hdClass}</span>
            : null}
          {artifact?.metadata && <button type="button" onClick={() => setShowInfo((value) => !value)}>Info</button>}
          <button type="button" onClick={onChoosePath}>Choose .tc / tuning_curves.json…</button>
          <button type="button" aria-label="Collapse HD tuning curve" onClick={onToggleCollapsed}>›</button>
        </div>
      </header>

      {showInfo && provenance.length > 0 && (
        <dl className="hd-provenance">
          {provenance.map(([key, value]) => (
            <div key={key}><dt>{key}</dt><dd>{value}</dd></div>
          ))}
        </dl>
      )}

      {blocked ? (
        <div className="companion-empty"><strong>No units in Probe region</strong><span>Clear or redraw the Probe filter; no stale HD curve is shown.</span></div>
      ) : loading ? (
        <div className="companion-empty"><span className="spinner" /> Loading HD tuning data…</div>
      ) : error ? (
        <div className="companion-empty error-state"><strong>HD tuning data could not be loaded</strong><span>{error}</span><button type="button" onClick={onChoosePath}>Choose .tc / tuning_curves.json…</button></div>
      ) : !artifact?.available ? (
        <div className="companion-empty"><strong>HD tuning unavailable</strong><span>No tuning_curves.tc or tuning_curves.json was found automatically for this recording date. Generate one with the analysis pipeline, or choose a matching remote file.</span><button type="button" onClick={onChoosePath}>Choose .tc / tuning_curves.json…</button></div>
      ) : !unit ? (
        <div className="companion-empty"><strong>No HD curve for cluster {clusterId}</strong><span>The RF map remains available.</span></div>
      ) : processed.error ? (
        <div className="companion-empty error-state"><strong>HD curve is invalid</strong><span>{processed.error}</span></div>
      ) : processed.curve ? (
        <>
          <div className="hd-controls">
            <label><span>Plot</span><select value={plotMode} onChange={(event) => onSettingsChange({ ...settings, plotMode: event.target.value as HdViewSettings["plotMode"] })}><option value="auto">Auto</option><option value="polar">Polar</option><option value="line">Line</option></select></label>
            <label><span>Bins</span><select value={displayBins} onChange={(event) => onSettingsChange({ ...settings, displayBins: normalizeHdBinCount(Number(event.target.value)) })}>{[1, 2, 3, 4, 5, 6, 9, 10, 12, 15, 18, 20, 30, 36, 45, 60, 90, 180].map((bins) => <option key={bins}>{bins}</option>)}</select></label>
            <label className="check-row"><input type="checkbox" checked={smoothing} onChange={(event) => onSettingsChange({ ...settings, smoothing: event.target.checked })} /><span>Smooth</span></label>
            <label><span>σ (deg)</span><input type="number" min="0.1" step="0.5" disabled={!smoothing} value={formatNumber(sigmaDeg, 4)} onChange={(event) => { const degrees = Number(event.target.value); if (Number.isFinite(degrees) && degrees > 0) onSettingsChange({ ...settings, sigmaDeg: degrees }); }} /></label>
            <label className="check-row"><input type="checkbox" checked={compareScale} onChange={(event) => onSettingsChange({ ...settings, compareScale: event.target.checked })} /><span>Shared file scale</span></label>
          </div>
          <CurvePlot curve={processed.curve} mode={resolvedMode} maximum={maximum} />
          <p className="hd-status">Peak scale {formatNumber(maximum, 3)} Hz · {resolvedMode === "polar" ? "0° north, positive counter-clockwise" : "0° centered"}</p>
        </>
      ) : null}
    </section>
  );
}
