import { useEffect, useRef } from "react";

export const LINUX_EXPORT_ROOT = "/mnt/ssd4.1/Apps/rfmapping/exports/";

interface SaveArtifactDialogProps {
  title: string;
  value: string;
  extension: ".csv" | ".png";
  busy: boolean;
  error: string;
  overwritePending: boolean;
  onChange: (value: string) => void;
  onClose: () => void;
  onSubmit: (overwrite: boolean) => void;
}

export default function SaveArtifactDialog({
  title,
  value,
  extension,
  busy,
  error,
  overwritePending,
  onChange,
  onClose,
  onSubmit,
}: SaveArtifactDialogProps) {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.select(), []);

  return (
    <div className="modal-backdrop">
      <form
        className="info-dialog save-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onSubmit={(event) => {
          event.preventDefault();
          if (value.trim()) onSubmit(overwritePending);
        }}
      >
        <header>
          <strong>{title}</strong>
          <button type="button" aria-label="Close" disabled={busy} onClick={onClose}>×</button>
        </header>
        <div className="save-dialog-body">
          <label htmlFor="artifact-relative-path">Save under</label>
          <code>{LINUX_EXPORT_ROOT}</code>
          <label htmlFor="artifact-relative-path">Relative path</label>
          <input
            id="artifact-relative-path"
            ref={input}
            type="text"
            value={value}
            disabled={busy}
            spellCheck={false}
            onChange={(event) => onChange(event.target.value)}
          />
          <span className="save-extension">Missing {extension} is added automatically.</span>
          {error && <p className="save-error" role="alert">{error}</p>}
        </div>
        <footer>
          <button type="button" disabled={busy} onClick={onClose}>Cancel</button>
          <button className={overwritePending ? "danger-button" : ""} type="submit" disabled={busy || !value.trim()}>
            {busy ? "Saving…" : overwritePending ? "Overwrite" : "Save"}
          </button>
        </footer>
      </form>
    </div>
  );
}
