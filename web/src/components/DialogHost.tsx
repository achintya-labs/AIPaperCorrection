import { useEffect, useRef, useState } from "react";
import { registerDialogHost, type DialogRequest } from "../lib/dialog";

/** Renders the dialogs requested through lib/dialog.ts, one at a time. Mounted once in the layout. */
export default function DialogHost() {
  const [queue, setQueue] = useState<DialogRequest[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const current = queue[0];
  const firstRef = useRef<HTMLInputElement>(null);
  const okRef = useRef<HTMLButtonElement>(null);

  useEffect(() => registerDialogHost((r) => setQueue((q) => [...q, r])), []);

  useEffect(() => {
    if (!current) return;
    setValues(Object.fromEntries((current.fields ?? []).map((f) => [f.name, f.value ?? ""])));
    // Focus the first field (select its text), or the confirm button.
    const t = setTimeout(() => { if (firstRef.current) { firstRef.current.focus(); firstRef.current.select(); } else okRef.current?.focus(); }, 0);
    return () => clearTimeout(t);
  }, [current]);

  if (!current) return null;
  const fields = current.fields ?? [];
  const valid = fields.every((f) => {
    const v = (values[f.name] ?? "").trim();
    return (!f.required || v !== "") && (f.mustEqual === undefined || (values[f.name] ?? "").trim() === f.mustEqual);
  });

  const close = (result: Record<string, string> | boolean | null) => {
    current.resolve(result);
    setQueue((q) => q.slice(1));
  };
  const cancel = () => close(fields.length ? null : false);
  const submit = () => { if (valid) close(fields.length ? values : true); };

  return (
    <div className="dialog-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget && current.cancelLabel !== null) cancel(); }}>
      <form
        className="card dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
        onSubmit={(e) => { e.preventDefault(); submit(); }}
        onKeyDown={(e) => { if (e.key === "Escape" && current.cancelLabel !== null) { e.preventDefault(); cancel(); } }}
      >
        <h3 id="dialog-title" style={{ margin: 0 }}>{current.title ?? (fields.length ? "Edit" : "Please confirm")}</h3>
        {current.message && <p className="dialog-message">{current.message}</p>}
        {fields.map((f, i) => (
          <div className="field" key={f.name} style={{ margin: 0 }}>
            <label htmlFor={`dlg-${f.name}`}>{f.label}</label>
            <input
              id={`dlg-${f.name}`}
              ref={i === 0 ? firstRef : undefined}
              type="text"
              autoComplete="off"
              placeholder={f.placeholder}
              value={values[f.name] ?? ""}
              onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}
            />
          </div>
        ))}
        <div className="row" style={{ justifyContent: "flex-end", gap: ".5rem" }}>
          {current.cancelLabel !== null && <button type="button" onClick={cancel}>{current.cancelLabel ?? "Cancel"}</button>}
          <button
            type="submit"
            ref={okRef}
            className={current.danger ? "danger" : "primary"}
            disabled={!valid}
          >{current.confirmLabel ?? (fields.length ? "Save" : "Confirm")}</button>
        </div>
      </form>
    </div>
  );
}
