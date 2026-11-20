import { useEffect, useState } from "react";
import { api, ApiError, errMsg } from "../lib/api";
import type { Question, Rubric } from "../lib/types";
import { Alert, fmt } from "./ui";

const blank = (n: number): Question => ({ number: String(n), text: "", max_points: 1, expected_answer: "", criteria: [] });

/** Client-side mirror of the API's rules, so mistakes show up before saving. */
function problems(qs: Question[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  qs.forEach((q) => {
    const n = q.number.trim();
    if (!n) out.push("Every question needs a number");
    else if (seen.has(n)) out.push(`Question number ${n} is used twice`);
    seen.add(n);
    if (!(q.max_points > 0)) out.push(`Question ${n || "?"}: max points must be above 0`);
    const sum = q.criteria.reduce((s, c) => s + (Number(c.points) || 0), 0);
    if (sum > q.max_points) out.push(`Question ${n || "?"}: criteria add up to ${fmt(sum)}, above max ${fmt(q.max_points)}`);
  });
  return out;
}

export default function RubricEditor({ examId }: { examId: number }) {
  const [version, setVersion] = useState<number | null>(null);
  const [qs, setQs] = useState<Question[]>([]);
  const [saved, setSaved] = useState<string>("[]");
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");
  const [busy, setBusy] = useState(false);

  const strip = (list: Question[]) =>
    list.map(({ number, text, max_points, expected_answer, criteria }) => ({ number, text, max_points, expected_answer, criteria }));

  useEffect(() => {
    api<Rubric>(`/exams/${examId}/rubric`)
      .then((r) => { setVersion(r.version); setQs(strip(r.questions)); setSaved(JSON.stringify(strip(r.questions))); })
      .catch((e) => { if (!(e instanceof ApiError && e.status === 404)) setError(errMsg(e)); else setQs([blank(1)]); });
  }, [examId]);

  const update = (i: number, patch: Partial<Question>) => setQs((l) => l.map((q, j) => (j === i ? { ...q, ...patch } : q)));
  const errs = problems(qs);
  const dirty = JSON.stringify(strip(qs)) !== saved;
  const totalPts = qs.reduce((s, q) => s + (Number(q.max_points) || 0), 0);

  async function save() {
    setBusy(true); setError(""); setOk("");
    try {
      const r = await api<Rubric>(`/exams/${examId}/rubrics`, { method: "POST", json: { questions: strip(qs) } });
      setVersion(r.version); setSaved(JSON.stringify(strip(r.questions)));
      setOk(`Saved as version ${r.version}. Papers graded earlier keep the version they used; regrade them to apply this one.`);
    } catch (e) { setError(errMsg(e)); } finally { setBusy(false); }
  }

  function exportJson() {
    const url = URL.createObjectURL(new Blob([JSON.stringify({ questions: strip(qs) }, null, 2)], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url; a.download = `rubric-exam-${examId}.json`; a.click();
    URL.revokeObjectURL(url);
  }
  async function importJson(file: File | undefined) {
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      const list: any[] = Array.isArray(data) ? data : data.questions;
      if (!Array.isArray(list) || !list.length) throw new Error("Expected {\"questions\": [...]}");
      setQs(list.map((q) => ({
        number: String(q.number ?? ""), text: q.text ?? "", max_points: Number(q.max_points) || 0,
        expected_answer: q.expected_answer ?? "",
        criteria: (q.criteria ?? []).map((c: any) => ({ description: String(c.description ?? ""), points: Number(c.points) || 0 })),
      })));
      setError(""); setOk("Imported into the editor — review it, then save.");
    } catch (e) { setError(`Could not read that file: ${errMsg(e)}`); }
  }

  return (
    <div className="stack">
      <div className="row between">
        <h2 style={{ margin: 0 }}>Rubric {version ? <span className="muted">· version {version}</span> : <span className="muted">· none yet</span>}</h2>
        <div className="row">
          <span className="muted small">{qs.length} questions · {fmt(totalPts)} pts</span>
          <button onClick={exportJson}>Export JSON</button>
          <label className="btn" style={{ margin: 0, color: "var(--text)" }}>
            Import JSON
            <input type="file" accept="application/json,.json" hidden onChange={(e) => { importJson(e.target.files?.[0]); e.target.value = ""; }} />
          </label>
        </div>
      </div>
      {error && <Alert tone="bad">{error}</Alert>}
      {ok && <Alert tone="ok">{ok}</Alert>}
      {errs.length > 0 && <Alert tone="warn"><ul style={{ margin: 0, paddingLeft: "1.1rem" }}>{errs.map((e, i) => <li key={i}>{e}</li>)}</ul></Alert>}

      {qs.map((q, i) => (
        <div className="card" key={i}>
          <div className="row" style={{ alignItems: "flex-end" }}>
            <div style={{ width: 110 }}>
              <label htmlFor={`n${i}`}>Number</label>
              <input id={`n${i}`} type="text" value={q.number} onChange={(e) => update(i, { number: e.target.value })} />
            </div>
            <div style={{ width: 130 }}>
              <label htmlFor={`m${i}`}>Max points</label>
              <input id={`m${i}`} type="number" min={0} step="0.5" value={q.max_points} onChange={(e) => update(i, { max_points: Number(e.target.value) })} />
            </div>
            <div className="grow" />
            <button className="danger" onClick={() => setQs((l) => l.filter((_, j) => j !== i))}>Remove</button>
          </div>
          <div className="field" style={{ marginTop: ".75rem" }}>
            <label htmlFor={`t${i}`}>Question text</label>
            <textarea id={`t${i}`} value={q.text} onChange={(e) => update(i, { text: e.target.value })} />
          </div>
          <div className="field">
            <label htmlFor={`e${i}`}>Model answer</label>
            <textarea id={`e${i}`} value={q.expected_answer} onChange={(e) => update(i, { expected_answer: e.target.value })} />
          </div>
          <label>Grading criteria</label>
          {q.criteria.map((c, k) => (
            <div className="row" key={k} style={{ marginBottom: ".4rem" }}>
              <input className="grow" type="text" aria-label="Criterion description" value={c.description} placeholder="What earns these points"
                onChange={(e) => update(i, { criteria: q.criteria.map((x, j) => (j === k ? { ...x, description: e.target.value } : x)) })} />
              <input style={{ width: 90 }} type="number" min={0} step="0.5" aria-label="Criterion points" value={c.points}
                onChange={(e) => update(i, { criteria: q.criteria.map((x, j) => (j === k ? { ...x, points: Number(e.target.value) } : x)) })} />
              <button className="danger" aria-label="Remove criterion" onClick={() => update(i, { criteria: q.criteria.filter((_, j) => j !== k) })}>×</button>
            </div>
          ))}
          <button onClick={() => update(i, { criteria: [...q.criteria, { description: "", points: 1 }] })}>+ Criterion</button>
        </div>
      ))}

      <div className="row">
        <button onClick={() => setQs((l) => [...l, blank(l.length + 1)])}>+ Add question</button>
        <button className="primary" disabled={busy || !dirty || errs.length > 0 || qs.length === 0} onClick={save}>
          {busy ? "Saving…" : version ? "Save as new version" : "Save rubric"}
        </button>
      </div>
    </div>
  );
}
