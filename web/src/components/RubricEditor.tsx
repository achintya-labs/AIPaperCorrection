import { useEffect, useState } from "react";
import { api, apiBlob, ApiError, errMsg } from "../lib/api";
import type { Criterion, Question, Rubric } from "../lib/types";
import { downloadBlob, saveBlob } from "../lib/uiHelpers";
import { Alert, fmt } from "./ui";

type KeyedCriterion = Criterion & { _key: number };
type KeyedQuestion = Omit<Question, "criteria"> & { _key: number; criteria: KeyedCriterion[] };

let nextKey = 1;
const keyOf = () => nextKey++;

const blank = (n: number): KeyedQuestion => ({
  _key: keyOf(), number: String(n), text: "", max_points: 1, expected_answer: "", criteria: [],
});

const withKeys = (list: Question[]): KeyedQuestion[] =>
  list.map((q) => ({
    ...q,
    _key: keyOf(),
    criteria: q.criteria.map((c) => ({ ...c, _key: keyOf() })),
  }));

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
    const sum = q.criteria.reduce((s, c) => s + Math.max(0, Number(c.points) || 0), 0);
    if (sum > q.max_points) out.push(`Question ${n || "?"}: criteria add up to ${fmt(sum)}, above max ${fmt(q.max_points)}`);
  });
  return out;
}

export default function RubricEditor({ examId, canEdit = false }: { examId: number; canEdit?: boolean }) {
  if (!canEdit) return <RubricView examId={examId} />;
  return <RubricForm examId={examId} />;
}

/** Read-only: the rubric as saved, with no editing, import/export or downloads. */
function RubricView({ examId }: { examId: number }) {
  const [rubric, setRubric] = useState<Rubric | null | "none">(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    api<Rubric>(`/exams/${examId}/rubric`)
      .then((r) => { if (alive) setRubric(r); })
      .catch((e) => { if (!alive) return; if (e instanceof ApiError && e.status === 404) setRubric("none"); else setError(errMsg(e)); });
    return () => { alive = false; };
  }, [examId]);
  if (error) return <Alert tone="bad">{error}</Alert>;
  if (!rubric) return <p className="muted">Loading…</p>;
  if (rubric === "none") return <p className="muted">No rubric yet.</p>;
  const total = rubric.questions.reduce((s, q) => s + q.max_points, 0);
  return (
    <div className="stack">
      <h2 style={{ margin: 0 }}>Rubric <span className="muted">· version {rubric.version}</span> <span className="muted small">· {rubric.questions.length} questions · {fmt(total)} pts</span></h2>
      {rubric.questions.map((q) => (
        <div className="card stack" key={q.id ?? q.number}>
          <div className="row between"><strong>Question {q.number}</strong><span className="badge neutral">{fmt(q.max_points)} pts</span></div>
          {q.text && <div><span className="field-label">Question text</span><div style={{ whiteSpace: "pre-wrap" }}>{q.text}</div></div>}
          {q.expected_answer && <div><span className="field-label">Model answer</span><div style={{ whiteSpace: "pre-wrap" }}>{q.expected_answer}</div></div>}
          {q.criteria.length > 0 && (
            <div>
              <span className="field-label">Grading criteria</span>
              <ul style={{ margin: ".25rem 0 0", paddingLeft: "1.1rem" }}>
                {q.criteria.map((c, i) => <li key={i}>{c.description} <span className="muted">({c.points > 0 ? "+" : ""}{fmt(c.points)})</span></li>)}
              </ul>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function RubricForm({ examId }: { examId: number }) {
  const [version, setVersion] = useState<number | null>(null);
  const [qs, setQs] = useState<KeyedQuestion[]>([]);
  const [saved, setSaved] = useState<string>("[]");
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");
  const [busy, setBusy] = useState(false);

  const strip = (list: KeyedQuestion[]): Question[] =>
    list.map(({ number, text, max_points, expected_answer, criteria }) => ({
      number, text, max_points, expected_answer,
      criteria: criteria.map(({ description, points }) => ({ description, points })),
    }));

  useEffect(() => {
    let alive = true;
    api<Rubric>(`/exams/${examId}/rubric`)
      .then((r) => {
        if (!alive) return;
        const keyed = withKeys(r.questions);
        setVersion(r.version); setQs(keyed); setSaved(JSON.stringify(strip(keyed)));
      })
      .catch((e) => {
        if (!alive) return;
        if (!(e instanceof ApiError && e.status === 404)) setError(errMsg(e));
        else setQs([blank(1)]);
      });
    return () => { alive = false; };
  }, [examId]);

  const update = (i: number, patch: Partial<KeyedQuestion>) => setQs((l) => l.map((q, j) => (j === i ? { ...q, ...patch } : q)));
  const errs = problems(qs);
  const dirty = JSON.stringify(strip(qs)) !== saved;
  const totalPts = qs.reduce((s, q) => s + (Number(q.max_points) || 0), 0);

  async function save() {
    setBusy(true); setError(""); setOk("");
    try {
      const r = await api<Rubric>(`/exams/${examId}/rubrics`, { method: "POST", json: { questions: strip(qs) } });
      const keyed = withKeys(r.questions);
      setVersion(r.version); setQs(keyed); setSaved(JSON.stringify(strip(keyed)));
      setOk(`Saved as version ${r.version}. Papers graded earlier keep the version they used; regrade them to apply this one.`);
    } catch (e) { setError(errMsg(e)); } finally { setBusy(false); }
  }

  /** A blank starting point: fill it in, then Import JSON. */
  function downloadTemplate() {
    const template = {
      questions: [
        {
          number: "1",
          text: "The question as printed (optional)",
          max_points: 5,
          expected_answer: "The model answer and any marking notes Claude should follow",
          criteria: [
            { description: "What earns these marks", points: 3 },
            { description: "Another thing that earns marks", points: 2 },
            { description: "A deduction (negative points)", points: -1 },
          ],
        },
        { number: "2", text: "", max_points: 2, expected_answer: "", criteria: [] },
      ],
    };
    downloadBlob("rubric-template.json", JSON.stringify(template, null, 2), "application/json");
  }

  /** The PDF is generated from what is in the editor right now, saved or not. */
  async function downloadPdf() {
    setError("");
    try {
      const blob = await apiBlob(`/exams/${examId}/rubric.pdf`, {
        method: "POST",
        json: { version: dirty ? null : version, questions: strip(qs) },
      });
      saveBlob(`rubric-exam-${examId}${dirty || !version ? "-draft" : `-v${version}`}.pdf`, blob);
    } catch (e) { setError(errMsg(e)); }
  }

  function exportJson() {
    downloadBlob(`rubric-exam-${examId}.json`, JSON.stringify({ questions: strip(qs) }, null, 2), "application/json");
  }
  async function importJson(file: File | undefined) {
    if (!file) return;
    try {
      const data: unknown = JSON.parse(await file.text());
      const list = Array.isArray(data)
        ? data
        : typeof data === "object" && data !== null && "questions" in data && Array.isArray((data as { questions: unknown }).questions)
          ? (data as { questions: unknown[] }).questions
          : null;
      if (!list || !list.length) throw new Error("Expected {\"questions\": [...]}");
      setQs(list.map((raw) => {
        const q = (typeof raw === "object" && raw !== null ? raw : {}) as Record<string, unknown>;
        const rawCrit = Array.isArray(q.criteria) ? q.criteria : [];
        return {
          _key: keyOf(),
          number: String(q.number ?? ""),
          text: String(q.text ?? ""),
          max_points: Number(q.max_points) || 0,
          expected_answer: String(q.expected_answer ?? ""),
          criteria: rawCrit.map((rc) => {
            const c = (typeof rc === "object" && rc !== null ? rc : {}) as Record<string, unknown>;
            return { _key: keyOf(), description: String(c.description ?? ""), points: Number(c.points) || 0 };
          }),
        };
      }));
      setError(""); setOk("Imported into the editor — review it, then save.");
    } catch (e) { setError(`Could not read that file: ${errMsg(e)}`); }
  }

  return (
    <div className="stack">
      <div className="row between">
        <h2 style={{ margin: 0 }}>Rubric {version ? <span className="muted">· version {version}</span> : <span className="muted">· none yet</span>}</h2>
        <div className="row">
          <span className="muted small">{qs.length} questions · {fmt(totalPts)} pts</span>
          <button onClick={downloadPdf} disabled={errs.length > 0 || qs.length === 0} title="A PDF of the rubric as it is in the editor now">Download PDF</button>
          <button onClick={exportJson}>Export JSON</button>
          <label className="btn" style={{ margin: 0, color: "var(--text)" }}>
            Import JSON
            <input type="file" accept="application/json,.json" hidden onChange={(e) => { importJson(e.target.files?.[0]); e.target.value = ""; }} />
          </label>
          <button className="primary" onClick={downloadTemplate} title="A blank rubric JSON to fill in and import">JSON template</button>
        </div>
      </div>
      {error && <Alert tone="bad">{error}</Alert>}
      {ok && <Alert tone="ok">{ok}</Alert>}
      {errs.length > 0 && <Alert tone="warn"><ul style={{ margin: 0, paddingLeft: "1.1rem" }}>{errs.map((e, i) => <li key={i}>{e}</li>)}</ul></Alert>}

      {qs.map((q, i) => (
        <div className="card" key={q._key}>
          <div className="row" style={{ alignItems: "flex-end" }}>
            <div style={{ width: 110 }}>
              <label htmlFor={`n${q._key}`}>Number</label>
              <input id={`n${q._key}`} type="text" value={q.number} onChange={(e) => update(i, { number: e.target.value })} />
            </div>
            <div style={{ width: 130 }}>
              <label htmlFor={`m${q._key}`}>Max points</label>
              <input id={`m${q._key}`} type="number" min={0} step="0.5" value={q.max_points} onChange={(e) => update(i, { max_points: Number(e.target.value) })} />
            </div>
            <div className="grow" />
            <button type="button" className="danger" onClick={() => setQs((l) => l.filter((_, j) => j !== i))}>Remove</button>
          </div>
          <div className="field" style={{ marginTop: ".75rem" }}>
            <label htmlFor={`t${q._key}`}>Question text</label>
            <textarea id={`t${q._key}`} value={q.text} onChange={(e) => update(i, { text: e.target.value })} />
          </div>
          <div className="field">
            <label htmlFor={`e${q._key}`}>Model answer</label>
            <textarea id={`e${q._key}`} value={q.expected_answer} onChange={(e) => update(i, { expected_answer: e.target.value })} />
          </div>
          <span className="field-label">Grading criteria</span>
          {q.criteria.map((c, k) => (
            <div className="row" key={c._key} style={{ marginBottom: ".4rem" }}>
              <input className="grow" type="text" aria-label="Criterion description" value={c.description} placeholder="What earns these points"
                onChange={(e) => update(i, { criteria: q.criteria.map((x, j) => (j === k ? { ...x, description: e.target.value } : x)) })} />
              <input style={{ width: 90 }} type="number" step="0.5" aria-label="Criterion points (negative = deduction)" value={c.points}
                onChange={(e) => update(i, { criteria: q.criteria.map((x, j) => (j === k ? { ...x, points: Number(e.target.value) } : x)) })} />
              <button type="button" className="danger" aria-label="Remove criterion" onClick={() => update(i, { criteria: q.criteria.filter((_, j) => j !== k) })}>×</button>
            </div>
          ))}
          <button type="button" onClick={() => update(i, { criteria: [...q.criteria, { _key: keyOf(), description: "", points: 1 }] })}>+ Criterion</button>
        </div>
      ))}

      <div className="row">
        <button type="button" onClick={() => setQs((l) => [...l, blank(l.length + 1)])}>+ Add question</button>
        <button type="button" className="primary" disabled={busy || !dirty || errs.length > 0 || qs.length === 0} onClick={save}>
          {busy ? "Saving…" : version ? "Save as new version" : "Save rubric"}
        </button>
      </div>
    </div>
  );
}
