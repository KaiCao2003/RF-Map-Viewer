import { useEffect, useState } from "react";
import { formatNumber } from "../math";

/** Keep incomplete edits such as a leading minus sign out of plot state. */
export default function RfWindowInput({ value, label, onCommit }: {
  value: number;
  label: string;
  onCommit: (value: number) => void;
}) {
  const [draft, setDraft] = useState(() => formatNumber(value, 6));
  useEffect(() => setDraft(formatNumber(value, 6)), [value]);
  return <input type="text" inputMode="decimal" value={draft} aria-label={label}
    onChange={(event) => setDraft(event.target.value)}
    onBlur={() => {
      const parsed = Number(draft);
      if (draft.trim() && Number.isFinite(parsed)) onCommit(parsed);
      setDraft(formatNumber(value, 6));
    }}
    onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }} />;
}
