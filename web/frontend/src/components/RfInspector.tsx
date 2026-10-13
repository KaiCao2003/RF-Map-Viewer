import { useEffect, useRef, useState, type ReactNode } from "react";
import { savedResultDetails } from "../rfResults";
import type { SavedRfResult } from "../types";

export default function RfInspector({ selection, results, unitId, loading, error, onClose }: {
  selection: ReactNode; results: SavedRfResult[]; unitId: number; loading: boolean; error: string; onClose: () => void;
}) {
  const [tab, setTab] = useState<"selection" | "results">("selection");
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.querySelector<HTMLElement>("button")?.focus();
    return () => previous?.focus();
  }, []);
  return <div className="modal-backdrop">
    <div className="settings-dialog rf-inspector" ref={dialog} role="dialog" aria-modal="true" aria-label="Inspector"
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === "Escape") { event.preventDefault(); onClose(); }
        if (event.key === "Tab") {
          const controls = [...event.currentTarget.querySelectorAll<HTMLButtonElement>("button")];
          if (event.shiftKey && document.activeElement === controls[0]) { event.preventDefault(); controls.at(-1)?.focus(); }
          else if (!event.shiftKey && document.activeElement === controls.at(-1)) { event.preventDefault(); controls[0]?.focus(); }
        }
      }}>
      <header><strong>Inspector</strong><button type="button" aria-label="Close Inspector" onClick={onClose}>×</button></header>
      <nav className="settings-tabs" aria-label="Inspector sections"><button type="button" aria-pressed={tab === "selection"} onClick={() => setTab("selection")}>Selection</button><button type="button" aria-pressed={tab === "results"} onClick={() => setTab("results")}>Saved results</button></nav>
      <div className="settings-form">{tab === "selection" ? selection : <>
        {loading && <p>Loading saved results…</p>}{error && <p role="alert">{error}</p>}
        {results.map((result) => <pre key={result.dimension}>{savedResultDetails(result, unitId)}</pre>)}
      </>}</div>
      <footer><button type="button" onClick={onClose}>Done</button></footer>
    </div>
  </div>;
}
