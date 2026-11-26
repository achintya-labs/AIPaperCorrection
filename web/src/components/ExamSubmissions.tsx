import { useCallback, useEffect, useState } from "react";
import { api, ApiError, errMsg } from "../lib/api";
import type { Submission, UserAdmin } from "../lib/types";
import { deletePaperFlow } from "../lib/actions";
import { confirmBox, formBox } from "../lib/dialog";
import { Alert, ConfidenceBadge, Person, StatusBadge } from "./ui";
import { matchesQuery } from "../lib/search";
import SearchBox, { NoMatches } from "./SearchBox";
import TaMultiSelect from "./TaMultiSelect";
import SelectMenu from "./SelectMenu";

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
    if (!(await confirmBox({ title: "Grade all ungraded papers?", message: "Every paper that is uploaded or failed will be sent to Claude.", confirmLabel: "Grade all" }))) return;
    try {
      const r = await api<{ queued: number }>(`/exams/${examId}/queue`, { method: "POST" });
      setInfo(`${r.queued} paper${r.queued === 1 ? "" : "s"} queued for grading.`);
      load();
    } catch (e) { setError(errMsg(e)); }
  }

  async function regrade(s: Submission) {
    setError(""); setInfo("");
    const first = s.status === "uploaded" || s.status === "failed";
    if (!first && !(await confirmBox({ title: `Regrade ${s.student.external_id}?`, message: "Claude's grades for this paper will be replaced.", confirmLabel: "Regrade" }))) return;
    try {
      await api(`/submissions/${s.id}/regrade`, { method: "POST", json: {} });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && /force/.test(e.message)) {
        if (!(await confirmBox({ title: "Discard TA edits?", message: "This paper has TA-reviewed grades. Regrading will DISCARD their edits.", confirmLabel: "Regrade anyway", danger: true }))) return;
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
    if (!(await confirmBox({
      title: `Grade ${ids.length} selected paper${ids.length === 1 ? "" : "s"}?`,
      message: "They will be sent to Claude." + (gradedBefore ? `\n\n${gradedBefore} already graded; their Claude grades will be replaced.` : ""),
      confirmLabel: "Grade",
    }))) return;
    try {
      type Res = { queued: number; skipped: number };
      let r = await api<Res>(`/submissions/bulk/grade`, { method: "POST", json: { submission_ids: ids } });
      const reviewed = (subs ?? []).filter((x) => picked.has(x.id) && x.grades_reviewed > 0).length;
      if (reviewed && await confirmBox({
        title: "Discard TA edits?",
        message: `${reviewed} selected paper${reviewed === 1 ? " has" : "s have"} TA-reviewed grades and ${reviewed === 1 ? "was" : "were"} skipped.\n\nRegrade anyway and DISCARD those edits?`,
        confirmLabel: "Regrade anyway", danger: true,
      })) {
        const r2 = await api<Res>(`/submissions/bulk/grade`, { method: "POST", json: { submission_ids: ids, force: true } });
        r = { queued: r.queued + r2.queued, skipped: r2.skipped };
      }
      setInfo(`${r.queued} paper${r.queued === 1 ? "" : "s"} queued for grading` + (r.skipped ? `, ${r.skipped} skipped (already queued, grading, or TA-reviewed).` : "."));
      load();
    } catch (e) { setError(errMsg(e)); }
  }

  async function deleteSelected() {
    setError(""); setInfo("");
    const ids = (subs ?? []).filter((x) => picked.has(x.id)).map((x) => x.id);
    if (!(await confirmBox({
      title: `Delete ${ids.length} selected paper${ids.length === 1 ? "" : "s"}?`,
      message: "Their page images, Claude's grades and any TA review work are permanently deleted. The student records are kept. This can't be undone.",
      confirmLabel: "Delete", danger: true,
    }))) return;
    const del = (force: boolean) =>
      api<{ deleted: number }>(`/submissions/bulk/delete`, { method: "POST", json: { submission_ids: ids, force } });
    try {
      let r;
      try { r = await del(false); }
      catch (e) {
        if (!(e instanceof ApiError && e.status === 409 && /force/.test(e.message))) throw e;
        if (!(await confirmBox({ title: "Delete anyway?", message: e.message, confirmLabel: "Delete anyway", danger: true }))) return;
        r = await del(true);
      }
      setPicked(new Set());
      setInfo(`Deleted ${r.deleted} paper${r.deleted === 1 ? "" : "s"}.`);
      load();
    } catch (e) { setError(errMsg(e)); }
  }

  async function editStudent(s: Submission) {
    setError(""); setInfo("");
    const v = await formBox({
      title: "Edit student",
      fields: [
        { name: "external_id", label: "Roll number", value: s.student.external_id, required: true },
        { name: "name", label: "Name", value: s.student.name },
      ],
    });
    if (!v) return;
    if (v.external_id.trim() === s.student.external_id && v.name.trim() === s.student.name) return;
    try {
      await api(`/students/${s.student.id}`, { method: "PATCH", json: { external_id: v.external_id, name: v.name } });
      setInfo("Student updated.");
      load();
    } catch (e) { setError(errMsg(e)); }
  }

  async function assignSelected() {
    setError(""); setInfo("");
    const ids = (subs ?? []).filter((x) => picked.has(x.id)).map((x) => x.id);
        if (bulkMode === "replace" && bulkTas.length === 0 && !(await confirmBox({ title: `Unassign everyone from ${ids.length} paper${ids.length === 1 ? "" : "s"}?`, confirmLabel: "Unassign", danger: true }))) return;
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
          <button className="primary" onClick={queueWaiting} title="Grade every paper that is uploaded or failed">Grade all ungraded</button>
          <a className="btn" href={`/api/exams/${examId}/export.csv`}>Export grades (CSV)</a>
          <a className="btn" href={`/api/exams/${examId}/export.zip?raw=true`} title="The original scans of every paper, graded or not">Export raw PDFs (ZIP)</a>
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
        <div className="card bulkbar">
          <div className="bulk-top">
            <div className="row" style={{ gap: ".6rem" }}>
              <strong>{pickedCount} selected</strong>
              <button className="link" onClick={() => setPicked(new Set())}>Clear</button>
            </div>
            <div className="row" style={{ gap: ".5rem" }}>
              <a className="btn" href={`/api/exams/${examId}/export.zip?ids=${(subs ?? []).filter((x) => picked.has(x.id)).map((x) => x.id).join(",")}`}>Export PDFs (ZIP)</a>
              <button className="primary" onClick={gradeSelected}>Grade selected</button>
              <button className="danger" onClick={deleteSelected}>Delete selected</button>
            </div>
          </div>
          <div className="bulk-assign">
            <div className="bulk-mode">
              <SelectMenu value={bulkMode} onChange={setBulkMode} ariaLabel="How to apply TAs" minWidth={130}
                options={[{ value: "add", label: "Add TAs" }, { value: "replace", label: "Set TAs" }, { value: "remove", label: "Remove TAs" }]} />
            </div>
            <div className="bulk-ta"><TaMultiSelect tas={tas} selected={bulkTas} onChange={setBulkTas} /></div>
            <button onClick={assignSelected} disabled={bulkTas.length === 0 && bulkMode !== "replace"}>Apply</button>
          </div>
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
                  <td>
                    <div className="row" style={{ gap: ".5rem", flexWrap: "nowrap" }}>
                      <div><strong>{s.student.external_id}</strong>{s.student.name && <div className="small muted">{s.student.name}</div>}</div>
                      <button className="icon-btn" aria-label={`Edit ${s.student.external_id}`} title="Edit roll number / name"
                        onClick={() => editStudent(s)}>✎</button>
                    </div>
                  </td>
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
