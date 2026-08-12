import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listRemoteFiles } from "../api";
import type { FsEntry } from "../types";

interface RemoteBrowserProps {
  busy?: boolean;
  initialPath?: string;
  kind?: "rf-json" | "tuning-json" | "positions-csv";
  title?: string;
  onOpen: (path: string) => void;
}

function parentPath(path: string, root: string): string {
  if (path === root) return root;
  const parent = path.replace(/\/+$/, "").replace(/\/[^/]+$/, "");
  return parent.startsWith(root) ? parent : root;
}

function humanSize(bytes: number | null): string {
  if (bytes == null) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

function modifiedLabel(seconds: number | null): string {
  if (seconds == null) return "";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(seconds * 1000),
  );
}

function acceptedBasename(path: string, kind: NonNullable<RemoteBrowserProps["kind"]>): boolean {
  const basename = path.replace(/\/+$/, "").split("/").at(-1)?.toLocaleLowerCase() ?? "";
  if (kind === "positions-csv") return basename === "positions.csv";
  if (kind === "tuning-json") return basename === "tuning_curves.json";
  return basename.endsWith(".json") && basename !== "tuning_curves.json";
}

export default function RemoteBrowser({
  busy = false,
  initialPath = "/mnt/senzailab",
  kind = "rf-json",
  title = "Remote RF JSON browser",
  onOpen,
}: RemoteBrowserProps) {
  const [root, setRoot] = useState("/mnt/senzailab");
  const [path, setPath] = useState(initialPath);
  const [entries, setEntries] = useState<FsEntry[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [pathDraft, setPathDraft] = useState(initialPath);
  const request = useRef<{ sequence: number; controller: AbortController } | null>(null);

  const load = useCallback(async (targetPath: string, nextCursor?: string) => {
    request.current?.controller.abort();
    const sequence = (request.current?.sequence ?? 0) + 1;
    const controller = new AbortController();
    request.current = { sequence, controller };
    setLoading(true);
    setError("");
    try {
      const page = await listRemoteFiles(targetPath, nextCursor, controller.signal, kind);
      if (request.current?.sequence !== sequence || controller.signal.aborted) return;
      setRoot(page.root);
      setPath(page.path);
      setPathDraft(page.path);
      setCursor(page.nextCursor);
      setEntries((current) => (nextCursor ? [...current, ...page.entries] : page.entries));
    } catch (caught) {
      if (request.current?.sequence === sequence && !controller.signal.aborted) {
        setError(caught instanceof Error ? caught.message : "Could not list this folder.");
      }
    } finally {
      if (request.current?.sequence === sequence) setLoading(false);
    }
  }, [kind]);

  useEffect(() => {
    void load(initialPath);
    return () => request.current?.controller.abort();
  }, [initialPath, load]);

  const visible = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return entries.filter((entry) => !needle || entry.name.toLocaleLowerCase().includes(needle));
  }, [entries, query]);

  const breadcrumbs = useMemo(() => {
    const relative = path.slice(root.length).split("/").filter(Boolean);
    const crumbs = [{ name: "senzailab", path: root }];
    for (let index = 0; index < relative.length; index += 1) {
      crumbs.push({ name: relative[index], path: `${root}/${relative.slice(0, index + 1).join("/")}` });
    }
    return crumbs;
  }, [path, root]);

  return (
    <section className="source-panel" aria-label={title}>
      <div className="browser-toolbar">
        <button
          type="button"
          aria-label="Go to parent folder"
          title="Parent folder"
          disabled={path === root || loading}
          onClick={() => void load(parentPath(path, root))}
        >
          ↑
        </button>
        <nav className="breadcrumbs" aria-label="Current remote folder">
          {breadcrumbs.map((crumb, index) => (
            <span key={crumb.path}>
              {index > 0 && <span className="crumb-divider">/</span>}
              <button type="button" onClick={() => void load(crumb.path)} disabled={loading || crumb.path === path}>
                {crumb.name}
              </button>
            </span>
          ))}
        </nav>
      </div>

      <form
        className="path-entry"
        onSubmit={(event) => {
          event.preventDefault();
          const target = pathDraft.trim();
          if (!target) return;
          if (acceptedBasename(target, kind)) onOpen(target);
          else if (/\.(?:json|csv)$/i.test(target)) {
            setError(kind === "positions-csv"
              ? "Choose a file named positions.csv."
              : kind === "tuning-json"
                ? "Choose a file named tuning_curves.json."
                : "Choose an RF mapping JSON file.");
          } else void load(target);
        }}
      >
        <input value={pathDraft} onChange={(event) => setPathDraft(event.target.value)} aria-label="Remote path" spellCheck={false} />
        <button type="submit" disabled={loading || busy}>Go</button>
      </form>

      <label className="search-field">
        <span aria-hidden="true">⌕</span>
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Filter"
          aria-label="Filter this folder"
        />
      </label>

      <div className="file-list" role="list" aria-busy={loading}>
        {error && (
          <div className="inline-error" role="alert">
            <span>{error}</span>
            <button type="button" onClick={() => void load(path)}>Retry</button>
          </div>
        )}
        {!error && visible.map((entry) => (
          <button
            className="file-row"
            type="button"
            key={entry.path}
            disabled={busy}
            onClick={() => entry.type === "directory"
              ? void load(entry.path)
              : acceptedBasename(entry.path, kind) && onOpen(entry.path)}
            onDoubleClick={() => entry.type === "file" && acceptedBasename(entry.path, kind) && onOpen(entry.path)}
          >
            <span className={`file-icon ${entry.type}`} aria-hidden="true">
              {entry.type === "directory" ? "▰" : kind === "positions-csv" ? "CSV" : kind === "tuning-json" ? "HD" : "JSON"}
            </span>
            <span className="file-name">{entry.name}</span>
            <span className="file-meta">{entry.type === "file" ? humanSize(entry.size) : "Folder"}</span>
            <span className="file-meta file-date">{modifiedLabel(entry.mtime)}</span>
            <span className="row-chevron" aria-hidden="true">›</span>
          </button>
        ))}
        {!loading && !error && !visible.length && (
          <div className="empty-list">No matching files.</div>
        )}
        {loading && !entries.length && (
          <div className="list-loading"><span className="spinner small" /> Reading folder…</div>
        )}
      </div>

      {cursor && (
        <button className="secondary-button load-more" type="button" disabled={loading} onClick={() => void load(path, cursor)}>
          {loading ? "Loading…" : "Load more"}
        </button>
      )}
    </section>
  );
}
