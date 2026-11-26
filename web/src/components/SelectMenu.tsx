import { useCallback, useRef, useState } from "react";
import { useDismiss } from "../lib/uiHelpers";

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
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const close = useCallback(() => setOpen(false), []);
  useDismiss(open, boxRef, close, triggerRef);

  const label = options.find((o) => o.value === value)?.label ?? value;

  function focusOption(index: number) {
    const items = listRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]');
    if (!items?.length) return;
    const clamped = ((index % items.length) + items.length) % items.length;
    items[clamped]?.focus();
  }

  function onTriggerKeyDown(e: React.KeyboardEvent) {
    if (disabled) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) setOpen(true);
      const cur = Math.max(0, options.findIndex((o) => o.value === value));
      setTimeout(() => focusOption(cur), 0);
    }
  }

  function onListKeyDown(e: React.KeyboardEvent) {
    const items = Array.from(listRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]') ?? []);
    if (!items.length) return;
    const idx = items.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "ArrowDown") { e.preventDefault(); focusOption(idx + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); focusOption(idx <= 0 ? items.length - 1 : idx - 1); }
    else if (e.key === "Home") { e.preventDefault(); focusOption(0); }
    else if (e.key === "End") { e.preventDefault(); focusOption(items.length - 1); }
  }

  return (
    <div ref={boxRef} style={{ position: "relative", minWidth }}>
      <button
        ref={triggerRef}
        type="button"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-haspopup="listbox"
        disabled={disabled}
        title={title}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onTriggerKeyDown}
        style={{ width: "100%", display: "flex", justifyContent: "space-between", alignItems: "center", gap: ".5rem" }}
      >
        <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
        <span className="small muted" style={{ flex: "none" }}>{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div ref={listRef} role="listbox" className="card menu" style={{ minWidth }} onKeyDown={onListKeyDown}>
          {options.map((o) => (
            <button
              key={o.value}
              type="button"
              role="option"
              aria-selected={o.value === value}
              className="menu-option"
              onClick={() => {
                setOpen(false);
                if (o.value !== value) onChange(o.value);
                triggerRef.current?.focus();
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
