import { useCallback, useEffect, useState } from "react";
import { api, ApiError, errMsg } from "../lib/api";
import type { Submission, UserAdmin } from "../lib/types";
import { deletePaperFlow } from "../lib/actions";
import { Alert, ConfidenceBadge, Person, StatusBadge } from "./ui";
import { matchesQuery } from "../lib/search";
import SearchBox, { NoMatches } from "./SearchBox";
import TaMultiSelect from "./TaMultiSelect";

export default function ExamSubmissions({ examId }: { examId: number }) {
  const [subs, setSubs] = useState<Submission[] | null>(null);
  const [tas, setTas] = useState<UserAdmin[]>([]);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<{ id: number; ids: Set<number> } | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [bulkTas, setBulkTas] = useState<number[]>([]);
  const [bulkMode, setBulkMode] = useState<"replace" | "add" | "remove">("add");

  const load = useCallback(
    () => api<Submission[]>(`/submissions?exam_id=${examId}`).then(setSubs).catch((e) => setError(errMsg(e))),
    [examId],
  );
  useEffect(() => {
    load();
    api<UserAdmin[]>("/auth/users").then((u) => setTas(u.filter((x) => x.active && x.role === "ta"))).catch((e) => setError(errMsg(e)));
  }, [load]);

  // Poll while anything is in flight.
  const active = subs?.some((s) => s.status === "queued" || s.status === "grading");
  useEffect(() => {
    if (!active) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [active, load]);

  const taOf = (id: number) => tas.find((t) => t.id === id);
  const taText = (id: number) => { const t = taOf(id); return t ? `${t.name} ${t.email}` : `#${id}`; };

  async function queueWaiting() {
    setError(""); setInfo("");
    if (!confirm("Grade every paper that hasn't been graded yet (uploaded or failed)? Claude will be used for each.")) return;
    try {
      const r = await api<{ queued: number }>(`/exams/${examId}/queue`, { method: "POST" });
      setInfo(`${r.queued} paper${r.queued === 1 ? "" : "s"} queued for grading.`);
      load();
    } catch (e) { setError(errMsg(e)); }
  }

  async function regrade(s: Submission) {
    setError(""); setInfo("");
    const first = s.status === "uploaded" || s.status === "failed";
    if (!first && !confirm(`Regrade ${s.student.external_id}? Claude's grades will be replaced.`)) return;
    try {
      await api(`/submissions/${s.id}/regrade`, { method: "POST", json: {} });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && /force/.test(e.message)) {
        if (!confirm("This paper has TA-reviewed grades. Regrading will DISCARD their edits. Continue?")) return;
        try { await api(`/submissions/${s.id}/regrade`, { method: "POST", json: { force: true } }); }
        catch (e2) { setError(errMsg(e2)); return; }
      } else { setError(errMsg(e)); return; }
    }
    setInfo(`${s.student.external_id} queued for grading.`);
    load();
  }

  async function removePaper(s: Submission) {
    setError(""); setInfo("");
    const label = s.student.name ? `${s.student.external_id} (${s.student.name})` : s.student.external_id;
    try {
      if (await deletePaperFlow({ id: s.id, label })) {
        setInfo(`Deleted the paper for ${label}.`);
        load();
      }
    } catch (e) { setError(errMsg(e)); }
  }

  async function saveAssignment() {
    if (!editing) return;
    try {
      await api(`/submissions/${editing.id}/assignment`, { method: "PUT", json: { ta_ids: [...editing.ids] } });
      setEditing(null);
      load();
    } catch (e) { setError(errMsg(e)); }
  }

  const toggle = (id: number, on: boolean) =>
    setPicked((p) => { const n = new Set(p); if (on) n.add(id); else n.delete(id); return n; });

  async function gradeSelected() {
    setError(""); setInfo("");
    const ids = (subs ?? []).filter((x) => picked.has(x.id)).map((x) => x.id);
    const gradedBefore = (subs ?? []).filter((x) => picked.has(x.id) && !["uploaded", "failed"].includes(x.status)).length;
    if (!confirm(`Grade ${ids.length} selected paper${ids.length === 1 ? "" : "s"} with Claude?` +
      (gradedBefore ? `\n\n${gradedBefore} already graded; their Claude grades will be replaced.` : ""))) return;
    try {
      type Res = { queued: number; skipped: number };
      let r = await api<Res>(`/submissions/bulk/grade`, { method: "POST", json: { submission_ids: ids } });
      const reviewed = (subs ?? []).filter((x) => picked.has(x.id) && x.grades_reviewed > 0).length;
      if (reviewed && confirm(`${reviewed} selected paper${reviewed === 1 ? " has" : "s have"} TA-reviewed grades and ${reviewed === 1 ? "was" : "were"} skipped.\n\nRegrade anyway and DISCARD those edits?`)) {
        const r2 = await api<Res>(`/submissions/bulk/grade`, { method: "POST", json: { submission_ids: ids, force: true } });
        r = { queued: r.queued + r2.queued, skipped: r2.skipped };
      }
      setInfo(`${r.queued} paper${r.queued === 1 ? "" : "s"} queued for grading` + (r.skipped ? `, ${r.skipped} skipped (already queued, grading, or TA-reviewed).` : "."));
      load();
    } catch (e) { setError(errMsg(e)); }
  }

  async function assignSelected() {
    setError(""); setInfo("");
    const ids = (subs ?? []).filter((x) => picked.has(x.id)).map((x) => x.id);
        if (bulkMode === "replace" && bulkTas.length === 0 && !confirm(`Unassign everyone from ${ids.length} paper${ids.length === 1 ? "" : "s"}?`)) return;
    try {
      const r = await api<{ updated: number }>(`/submissions/bulk/assignment`, {
        method: "PUT", json: { submission_ids: ids, ta_ids: bulkTas, mode: bulkMode },
      });
      setInfo(`TAs ${{ replace: "set on", add: "added to", remove: "removed from" }[bulkMode]} ${r.updated} paper${r.updated === 1 ? "" : "s"}.`);
      load();
    } catch (e) { setError(errMsg(e)); }
  }

  const visible = (subs ?? []).filter((s) =>
    matchesQuery([s.student.external_id, s.student.name, s.status.replace(/_/g, " "), ...s.ta_ids.map(taText)], q),
  );

  const allVisiblePicked = visible.length > 0 && visible.every((s) => picked.has(s.id));
  const pickedCount = (subs ?? []).filter((s) => picked.has(s.id)).length;

  return (
    <div className="stack">
      <div className="row between">
        <h2 style={{ margin: 0 }}>Papers</h2>
        <div className="row">
          <button onClick={queueWaiting} title="Grade every paper that is uploaded or failed">Grade all ungraded</button>
          <a className="btn" href={`/api/exams/${examId}/export.csv`}>Export grades (CSV)</a>
          <a className="btn" href={`/api/exams/${examId}/export.zip`}>Export PDFs (ZIP)</a>
        </div>
      </div>
      {error && <Alert tone="bad">{error}</Alert>}
      {info && <Alert tone="ok">{info}</Alert>}
      {subs && subs.length > 0 && (
        <SearchBox value={q} onChange={setQ} label="Search papers" placeholder="Search student, TA, status"
          shown={visible.length} total={subs.length} />
      )}
      {pickedCount > 0 && (
        <div className="card row" style={{ position: "sticky", top: 0, zIndex: 20, flexWrap: "wrap", gap: ".6rem", alignItems: "center" }}>
          <strong>{pickedCount} selected</strong>
          <button className="primary" onClick={gradeSelected}>Grade selected</button>
          <span className="muted">|</span>
          <select value={bulkMode} onChange={(e) => setBulkMode(e.target.value as typeof bulkMode)} aria-label="How to apply TAs">
            <option value="add">Add TAs</option>
            <option value="replace">Set TAs (replace)</option>
            <option value="remove">Remove TAs</option>
          </select>
          <div style={{ minWidth: 220 }}><TaMultiSelect tas={tas} selected={bulkTas} onChange={setBulkTas} /></div>
          <button onClick={assignSelected} disabled={bulkTas.length === 0 && bulkMode !== "replace"}>Apply to selected</button>
          <button className="link" onClick={() => setPicked(new Set())}>Clear selection</button>
        </div>
      )}
      <div className="card table-card has-menu">
        {!subs ? <p className="muted" style={{ padding: "1rem" }}>Loading…</p> : subs.length === 0 ? (
          <p className="muted" style={{ padding: "1rem" }}>No papers imported yet.</p>
        ) : visible.length === 0 ? (
          <NoMatches query={q} onClear={() => setQ("")} />
        ) : (
          <table>
            <thead><tr>
              <th style={{ width: 28 }}>
                <input type="checkbox" aria-label="Select all shown papers" checked={allVisiblePicked}
                  onChange={(e) => setPicked((p) => { const n = new Set(p); visible.forEach((v) => e.target.checked ? n.add(v.id) : n.delete(v.id)); return n; })} />
              </th><th>Student</th><th>Status</th><th>Progress</th><th>Confidence</th><th>TAs</th><th></th></tr></thead>
            <tbody>
              {visible.map((s) => (
                <tr key={s.id}>
                  <td><input type="checkbox" aria-label={`Select ${s.student.external_id}`} checked={picked.has(s.id)} onChange={(e) => toggle(s.id, e.target.checked)} /></td>
                  <td><strong>{s.student.external_id}</strong>{s.student.name && <div className="small muted">{s.student.name}</div>}</td>
                  <td><StatusBadge status={s.status} /></td>
                  <td>{s.grades_total ? `${s.grades_reviewed} / ${s.grades_total}` : "–"}</td>
                  <td><ConfidenceBadge value={s.min_confidence} label="min" /></td>
                  <td className="tas-cell">
                    {editing?.id === s.id ? (
                      <div className="stack" style={{ minWidth: 220 }}>
                        <TaMultiSelect
                          tas={tas}
                          selected={[...editing.ids]}
                          onChange={(ids) => setEditing({ id: s.id, ids: new Set(ids) })}
                        />
                        <div className="row"><button className="primary" onClick={saveAssignment}>Save</button><button onClick={() => setEditing(null)}>Cancel</button></div>
                      </div>
                    ) : (
                      <div className="tas-view">
                        <div className="tas-list">
                          {s.ta_ids.length
                            ? s.ta_ids.map((id) => taOf(id) ? <Person key={id} user={taOf(id)} /> : <span key={id} className="muted">#{id}</span>)
                            : <span className="muted">unassigned</span>}
                        </div>
                        <button className="btn-sm" onClick={() => setEditing({ id: s.id, ids: new Set(s.ta_ids) })}>Edit</button>
                      </div>
                    )}
                  </td>
                  <td>
                    <div className="row">
                      <a className="btn" href={`/api/submissions/${s.id}/export.pdf`}>Export PDF</a>
                      {!["queued", "grading"].includes(s.status) && (
                        <button className={["uploaded", "failed"].includes(s.status) ? "primary" : ""} onClick={() => regrade(s)}>
                          {["uploaded", "failed"].includes(s.status) ? "Grade" : "Regrade"}
                        </button>
                      )}
                      <button className="danger" onClick={() => removePaper(s)} disabled={s.status === "grading"}
                        title={s.status === "grading" ? "Being graded right now; try again when it finishes" : "Permanently delete this paper"}>Delete</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
