import { useEffect, useRef } from "react";
import { SearchIcon } from "./ui";

/**
 * A search field for a table. Press "/" anywhere on the page to jump to it, Esc to clear.
 * The count only appears while a search is active.
 */
export default function SearchBox({
  value,
  onChange,
  label,
  placeholder = "Search…",
  shown,
  total,
}: {
  value: string;
  onChange: (v: string) => void;
  label: string;
  placeholder?: string;
  shown: number;
  total: number;
}) {
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      // With several boxes on a page, only the first one answers.
      if (document.querySelector("[data-searchbox]") !== ref.current) return;
      e.preventDefault();
      ref.current?.focus();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="searchbox" role="search">
      <div className="searchfield">
        <SearchIcon />
        <input
          ref={ref}
          data-searchbox
          type="search"
          aria-label={label}
          placeholder={`${placeholder}  ( / )`}
          value={value}
          autoComplete="off"
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Escape") onChange(""); }}
        />
      </div>
      {value.trim() && (
        <>
          <span className="small muted" aria-live="polite">{shown} of {total}</span>
          <button className="link" onClick={() => { onChange(""); ref.current?.focus(); }}>Clear</button>
        </>
      )}
    </div>
  );
}

/** Shown in place of a table when a search matches nothing. */
export function NoMatches({ query, onClear }: { query: string; onClear: () => void }) {
  return (
    <p className="muted" style={{ padding: "1rem" }}>
      Nothing matches “{query.trim()}”. <button className="link" onClick={onClear}>Clear search</button>
    </p>
  );
}
