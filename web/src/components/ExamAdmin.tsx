import { useEffect, useState } from "react";
import { api, errMsg } from "../lib/api";
import { deleteExamFlow } from "../lib/actions";
import type { Exam } from "../lib/types";
import { Alert } from "./ui";
import ExamSubmissions from "./ExamSubmissions";
import ImportWizard from "./ImportWizard";
import RubricEditor from "./RubricEditor";

type Tab = "rubric" | "import" | "papers";
const TABS: [Tab, string][] = [["rubric", "Rubric"], ["import", "Import"], ["papers", "Papers"]];

export default function ExamAdmin({ id }: { id: number }) {
  const [exam, setExam] = useState<Exam | null>(null);
  const [model, setModel] = useState("");
  const [tab, setTab] = useState<Tab>("rubric");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api<Exam[]>("/exams")
      .then((list) => {
        const e = list.find((x) => x.id === id);
        if (!e) return setError("Exam not found");
        setExam(e); setModel(e.model_override ?? "");
      })
      .catch((e) => setError(errMsg(e)));
  }, [id]);

  async function saveModel() {
    if (!exam) return;
    setError(""); setSaved(false);
    try {
      setExam(await api<Exam>(`/exams/${id}`, { method: "PATCH", json: { name: exam.name, model_override: model.trim() || null } }));
      setSaved(true);
    } catch (e) { setError(errMsg(e)); }
  }

  async function removeExam() {
    if (!exam) return;
    setError("");
    try {
      if (await deleteExamFlow(exam)) window.location.href = "/admin";
    } catch (e) { setError(errMsg(e)); }
  }

  if (error && !exam) return <Alert tone="bad">{error}</Alert>;
  if (!exam) return <p className="muted">Loading…</p>;

  return (
    <div className="stack">
      <div className="row between">
        <div>
          <a href="/admin" className="small">← Exams</a>
          <h1 style={{ margin: ".25rem 0 0" }}>{exam.name}</h1>
        </div>
        <button className="danger" onClick={removeExam}>Delete exam</button>
      </div>
      {error && <Alert tone="bad">{error}</Alert>}
      <div className="card row" style={{ alignItems: "flex-end" }}>
        <div className="grow" style={{ maxWidth: 360 }}>
          <label htmlFor="model">Grading model for this exam</label>
          <input id="model" type="text" className="mono" value={model} placeholder="Server default" onChange={(e) => { setModel(e.target.value); setSaved(false); }} />
        </div>
        <button onClick={saveModel} disabled={(exam.model_override ?? "") === model.trim()}>Save</button>
        {saved && <span className="small" style={{ color: "var(--ok)" }}>Saved. Applies to papers graded from now on.</span>}
      </div>
      <div className="tabs" role="tablist">
        {TABS.map(([key, label]) => (
          <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>
      {tab === "rubric" && <RubricEditor examId={id} />}
      {tab === "import" && <ImportWizard examId={id} />}
      {tab === "papers" && <ExamSubmissions examId={id} />}
    </div>
  );
}
