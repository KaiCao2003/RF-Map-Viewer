import { useEffect, useRef, useState } from "react";
import { normalizeHdBinCount } from "../hdMath";
import { DEFAULT_VIEWER_SETTINGS, invalidSettingsSection, type ViewerSettings } from "../viewerSettings";
import { PALETTES, POLAR_RADIUS_MODES, VALUE_MODES } from "../types";

const TABS = ["General", "RF Map", "Waveform", "Tuning Curve"] as const;

export default function ViewerSettingsDialog({ settings, onSave, onClose }: {
  settings: ViewerSettings;
  onSave: (settings: ViewerSettings) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(() => structuredClone(settings));
  const [tab, setTab] = useState<typeof TABS[number]>("General");
  const [error, setError] = useState("");
  const dialog = useRef<HTMLFormElement>(null);
  const patch = (value: Partial<ViewerSettings>) => setDraft((current) => ({ ...current, ...value }));

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.querySelector<HTMLElement>("button")?.focus();
    return () => previous?.focus();
  }, []);

  const checkbox = (label: string, key: keyof ViewerSettings, disabled = false) => (
    <label className="check-row"><input type="checkbox" checked={draft[key] as boolean} disabled={disabled}
      onChange={(event) => patch({ [key]: event.target.checked })} /><span>{label}</span></label>
  );
  const number = (label: string, key: keyof ViewerSettings, min: number, step: number | "any" = 1, max?: number) => (
    <label className="settings-row"><span>{label}</span><input type="number" required min={min} max={max} step={step}
      value={draft[key] as number} onChange={(event) => patch({ [key]: Number(event.target.value) })} /></label>
  );
  const select = (label: string, key: keyof ViewerSettings, values: ReadonlyArray<readonly [string, string]>) => (
    <label className="settings-row"><span>{label}</span><select value={String(draft[key])}
      onChange={(event) => patch({ [key]: event.target.value })}>{values.map(([value, name]) => <option key={value} value={value}>{name}</option>)}</select></label>
  );
  const range = (label: string, key: "sum" | "a" | "b") => (
    <label className="settings-row"><span>{label}</span><span className="settings-range">
      {[0, 1].map((index) => <input key={index} aria-label={`${label} ${index === 0 ? "start" : "end"}`} type="number" required step="any"
        value={draft.timing[key][index]} onChange={(event) => {
          const next = [...draft.timing[key]] as [number, number];
          next[index] = Number(event.target.value);
          patch({ timing: { ...draft.timing, [key]: next } });
        }} />)}
    </span></label>
  );

  return <div className="modal-backdrop settings-backdrop">
    <form className="settings-dialog" role="dialog" aria-modal="true" aria-label="Settings" ref={dialog}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === "Escape") { event.preventDefault(); onClose(); }
        if (event.key === "Tab") {
          const controls = [...event.currentTarget.querySelectorAll<HTMLElement>("button, input, select")]
            .filter((control) => !control.hasAttribute("disabled") && control.getClientRects().length > 0);
          const first = controls[0], last = controls.at(-1);
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }
      }}
      onSubmit={(event) => {
        event.preventDefault();
        const invalid = invalidSettingsSection(draft);
        if (invalid) {
          setError(`Check the numeric values in ${invalid}. Time windows must end after their start.`);
          setTab(invalid); return;
        }
        try { onSave({ ...draft, hd: { ...draft.hd, displayBins: normalizeHdBinCount(draft.hd.displayBins) } }); }
        catch (caught) { setError(caught instanceof Error ? caught.message : "Could not save settings."); }
      }}>
      <header><strong>Settings</strong><button type="button" aria-label="Close settings" onClick={onClose}>×</button></header>
      <nav className="settings-tabs" aria-label="Settings sections">{TABS.map((name) =>
        <button type="button" key={name} aria-pressed={tab === name} onClick={() => setTab(name)}>{name}</button>)}</nav>
      <div className="settings-form">
        {tab === "General" && <fieldset><legend>Views</legend>
          {checkbox("HD tuning curve", "showHd")}
          {checkbox("Auto-load HD tuning curve", "autoLoadHd", !draft.showHd)}
          {checkbox("Probe layout", "showProbe")}
          {checkbox("Auto-load Probe layout", "autoLoadProbe", !draft.showProbe)}
          <p className="muted-copy">Choose companion files manually when auto-load is off.</p>
        </fieldset>}
        {tab === "RF Map" && <>
          <fieldset><legend>Timing</legend>
            <label className="settings-row"><span>Mode</span><select value={draft.timing.mode} onChange={(event) => patch({ timing: { ...draft.timing, mode: event.target.value as "sum" | "difference" } })}><option value="sum">Sum</option><option value="difference">A − B</option></select></label>
            {range("Sum (ms)", "sum")}{range("A (ms)", "a")}{range("B (ms)", "b")}
            {number("Time bin (ms)", "timeResolutionMs", 0.000001, "any")}
            {select("Value", "valueMode", VALUE_MODES.map((value) => [value, value]))}
          </fieldset>
          <fieldset><legend>Unit filter · native bins in A</legend>
            {checkbox("Hide units with zero-spike bins", "filterUnits")}
            {number("Zero bins ≥", "zeroBinThreshold", 1, 1, 100_000)}
          </fieldset>
          <fieldset><legend>Spatial display</legend>
            {number("X bins (0 = source)", "xBins", 0, 1, 100_000)}
            {number("Y bins (0 = source)", "yBins", 0, 1, 100_000)}
            <label className="settings-row"><span>Layout</span><select value={String(draft.polarLayout)} onChange={(event) => patch({ polarLayout: event.target.value === "true" })}><option value="false">Rectangle</option><option value="true">Polar</option></select></label>
            {select("Palette", "palette", PALETTES.map((value) => [value, value]))}
            {select("Polar radius", "polarRadius", POLAR_RADIUS_MODES.map((value) => [value, value]))}
            {number("RF smoothing radius", "smoothRadius", 0, 1, 3)}
            {checkbox("Flip Y", "flipY")}{checkbox("RGB composite", "rgbMode")}
            {select("Initial tab", "initialTab", [["rf", "RF"], ["delay", "Delay / RGB"], ["timeline", "Timeline"]])}
          </fieldset>
        </>}
        {tab === "Waveform" && <fieldset><legend>Local average waveform</legend>
          {checkbox("Show waveform", "showWaveform")}
          {select("Nearby channels", "waveformChannelMode", [["same_x_column", "Same x column"], ["same_shank", "Same shank"]])}
        </fieldset>}
        {tab === "Tuning Curve" && <>
          <fieldset><legend>Data source</legend>{number("Session", "tuningSession", 1)}<p className="muted-copy">Load only the exact same-date session.</p></fieldset>
          <fieldset><legend>Head-direction display</legend>
            <label className="settings-row"><span>Plot style</span><select value={draft.hd.plotMode} onChange={(event) => patch({ hd: { ...draft.hd, plotMode: event.target.value as ViewerSettings["hd"]["plotMode"] } })}><option value="auto">Auto</option><option value="line">Line</option><option value="polar">Polar</option></select></label>
            {select("Arrangement", "hdLayout", [["side-by-side", "Side by side"], ["stacked", "Stacked"]])}
            <label className="settings-row"><span>HD bins (divisors of 180)</span><input type="number" required min={1} max={180} step={1} value={draft.hd.displayBins} onChange={(event) => patch({ hd: { ...draft.hd, displayBins: Number(event.target.value) } })} /></label>
            <label className="check-row"><input type="checkbox" checked={draft.hd.compareScale} onChange={(event) => patch({ hd: { ...draft.hd, compareScale: event.target.checked } })} /><span>Shared 0–peak Hz scale</span></label>
            <label className="check-row"><input type="checkbox" checked={draft.hd.smoothing} onChange={(event) => patch({ hd: { ...draft.hd, smoothing: event.target.checked } })} /><span>Smooth source curve (180 bins)</span></label>
            <label className="settings-row"><span>Gaussian σ (degrees)</span><input type="number" required min={0.000001} step="any" value={draft.hd.sigmaDeg} onChange={(event) => patch({ hd: { ...draft.hd, sigmaDeg: Number(event.target.value) } })} /></label>
          </fieldset>
        </>}
      </div>
      {error && <p className="dialog-error" role="alert">{error}</p>}
      <footer><span>Applied here and saved in this browser.</span><button type="button" onClick={() => { setDraft(structuredClone(DEFAULT_VIEWER_SETTINGS)); setError(""); }}>Restore Defaults</button><button type="button" onClick={onClose}>Cancel</button><button type="submit">Save</button></footer>
    </form>
  </div>;
}
