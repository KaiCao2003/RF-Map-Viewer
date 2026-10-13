import { useEffect, useRef, useState } from "react";
import { saveAdnSelection } from "../api";
import { probeUnitsInPolygon } from "../probeSelection";
import type { AdnSelection, ProbeGeometry } from "../types";
import ProbeLayout from "./ProbeLayout";

export default function AdnAnnotationDialog({ datasetId, positionsPath, geometry, selection, showUnitInfo, onSaved, onClose }: {
  datasetId: string;
  positionsPath: string | null;
  geometry: ProbeGeometry;
  selection: Extract<AdnSelection, { available: true }>;
  showUnitInfo: boolean;
  onSaved: (selection: AdnSelection) => void;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState(selection.unitIds);
  const [history, setHistory] = useState<number[][]>([]);
  const [zoom, setZoom] = useState(1);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => previous?.focus();
  }, []);
  const changeSelection = (next: number[]) => {
    setHistory((current) => [...current, selected]); setSelected(next); setStatus("");
  };
  const save = async () => {
    setBusy(true); setError("");
    try {
      const saved = await saveAdnSelection(datasetId, positionsPath, selected);
      onSaved(saved); setStatus(`Saved ${selected.length} ADN units · ${selection.path}`);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not save ADN annotation."); }
    finally { setBusy(false); }
  };
  return <div className="modal-backdrop"><div className="adn-dialog" role="dialog" aria-modal="true" aria-label={`Annotate ADN · ${geometry.probe}`} ref={dialog}
    onKeyDown={(event) => {
      event.stopPropagation();
      if (event.key === "Escape" && !busy) { event.preventDefault(); onClose(); }
      if (event.key === "Tab") {
        const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)")];
        const first = buttons[0], last = buttons.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    }}>
    <header><strong>Annotate ADN · {geometry.probe}</strong><button type="button" disabled={busy} onClick={onClose} aria-label="Close ADN annotation">×</button></header>
    <div className="adn-heading"><span>Draw a closed loop to add ADN units. Orange = selected ADN.</span>
      <button type="button" onClick={() => setZoom((value) => Math.max(1, value / 1.5))}>−</button><span>Depth: {zoom.toFixed(1)}×</span><button type="button" onClick={() => setZoom((value) => Math.min(8, value * 1.5))}>+</button></div>
    <div className="adn-content"><ProbeLayout geometry={geometry} availableUnitIds={geometry.units.map((unit) => unit.unitId)} currentClusterId={-1}
      selection={null} onSelection={() => undefined} onCluster={() => undefined} showUnitInfo={showUnitInfo}
      selectedAdnIds={selected} depthZoom={zoom} onLasso={(points) => {
        if (busy) return;
        const added = probeUnitsInPolygon(geometry, points);
        if (added.some((id) => !selected.includes(id))) changeSelection([...new Set([...selected, ...added])].sort((a, b) => a - b));
      }} />
      <aside><strong>{selected.length} ADN units</strong><div className="adn-unit-list">{selected.join(", ") || "None selected"}</div>
        <button type="button" disabled={busy || !history.length} onClick={() => { setSelected(history.at(-1)!); setHistory((items) => items.slice(0, -1)); setStatus(""); }}>Undo</button>
        <button type="button" disabled={busy || !selected.length} onClick={() => changeSelection([])}>Clear selection</button>
        <button type="button" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save ADN"}</button>
      </aside></div>
    <p className="adn-status" role={error ? "alert" : "status"}>{error || status || `Save ADN writes the selection to ${selection.path}`}</p>
  </div></div>;
}
