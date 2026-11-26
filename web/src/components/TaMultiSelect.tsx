import { useCallback, useRef, useState } from "react";
import type { User } from "../lib/types";
import { matchesQuery } from "../lib/search";
import { useDismiss } from "../lib/uiHelpers";
import { Person } from "./ui";

export default function TaMultiSelect({
  tas,
  selected,
  onChange,
}: {
  tas: User[];
  selected: number[];
  onChange: (ids: number[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
  }, []);
  useDismiss(open, boxRef, close, triggerRef);

  if (tas.length === 0) return <span className="small muted">No active TAs</span>;

  const chosen = tas.filter((t) => selected.includes(t.id));
  const label =
    chosen.length === 0 ? <span className="muted">Select TAs…</span>
      : chosen.length === 1 ? <Person user={chosen[0]} />
      : `${chosen.length} TAs selected`;
  const filtered = tas.filter((t) => matchesQuery([t.email, t.name], query));

  return (
    <div ref={boxRef} style={{ position: "relative" }}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => (open ? close() : setOpen(true))}
        aria-expanded={open}
        className="multiselect-trigger"
      >
        <span className="multiselect-label">{label}</span>
        <span className="small muted">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="card menu" style={{ minWidth: 260 }}>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search TAs…"
            autoFocus
          />
          <div style={{ display: "grid", gap: ".2rem" }}>
            {filtered.length === 0 ? (
              <span className="small muted">No matching TAs</span>
            ) : (
              filtered.map((t) => (
                <label key={t.id} className={`check multiselect-option${selected.includes(t.id) ? " selected" : ""}`}>
                  <input
                    type="checkbox"
                    checked={selected.includes(t.id)}
                    onChange={(e) =>
                      onChange(e.target.checked ? [...selected, t.id] : selected.filter((id) => id !== t.id))
                    }
                  />
                  <Person user={t} />
                </label>
              ))
            )}
          </div>
          {selected.length > 0 && (
            <button type="button" className="link small" onClick={() => onChange([])} style={{ textAlign: "left" }}>
              Clear selection
            </button>
          )}
        </div>
      )}
    </div>
  );
}
