import { useEffect, useRef, useState } from "react";
import type { User } from "../lib/types";
import { matchesQuery } from "../lib/search";
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

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

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
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="multiselect-trigger"
      >
        <span className="multiselect-label">{label}</span>
        <span className="small muted">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div
          className="card stack"
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: 0,
            right: 0,
            minWidth: 240,
            zIndex: 30,
            padding: ".5rem",
            gap: ".4rem",
            boxShadow: "0 8px 24px rgba(0, 0, 0, 0.5)",
          }}
        >
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search TAs…"
            autoFocus
          />
          <div style={{ maxHeight: 200, overflowY: "auto", display: "grid", gap: ".25rem" }}>
            {filtered.length === 0 ? (
              <span className="small muted">No matching TAs</span>
            ) : (
              filtered.map((t) => (
                <label key={t.id} className="check multiselect-option">
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
