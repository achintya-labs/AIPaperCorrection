import { useEffect, useRef, useState } from "react";

export type SelectOption<T extends string> = { value: T; label: string };

/** A styled single-choice dropdown (same look as the TA picker), used instead of a native <select>. */
export default function SelectMenu<T extends string>({
  value,
  options,
  onChange,
  disabled = false,
  title,
  ariaLabel,
  minWidth = 110,
}: {
  value: T;
  options: SelectOption<T>[];
  onChange: (value: T) => void;
  disabled?: boolean;
  title?: string;
  ariaLabel?: string;
  minWidth?: number;
}) {
  const [open, setOpen] = useState(false);
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

  const label = options.find((o) => o.value === value)?.label ?? value;

  return (
    <div ref={boxRef} style={{ position: "relative", minWidth }}>
      <button
        type="button"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-haspopup="listbox"
        disabled={disabled}
        title={title}
        onClick={() => setOpen((o) => !o)}
        style={{ width: "100%", display: "flex", justifyContent: "space-between", alignItems: "center", gap: ".5rem" }}
      >
        <span>{label}</span>
        <span className="small muted">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div
          role="listbox"
          className="card stack"
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: 0,
            right: 0,
            minWidth,
            zIndex: 30,
            padding: ".35rem",
            gap: ".2rem",
            boxShadow: "0 8px 24px rgba(0, 0, 0, 0.5)",
          }}
        >
          {options.map((o) => (
            <button
              key={o.value}
              type="button"
              role="option"
              aria-selected={o.value === value}
              onClick={() => {
                setOpen(false);
                if (o.value !== value) onChange(o.value);
              }}
              style={{
                textAlign: "left",
                border: 0,
                background: o.value === value ? "var(--info-bg)" : "transparent",
                color: o.value === value ? "var(--info)" : "var(--text)",
                fontWeight: o.value === value ? 600 : 400,
              }}
            >
              {o.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
