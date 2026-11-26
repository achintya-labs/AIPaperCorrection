import { useEffect, useState } from "react";
import { api, errMsg } from "../lib/api";
import { deleteExamFlow } from "../lib/actions";
import type { Exam } from "../lib/types";
import { Alert } from "./ui";
import ExamSubmissions from "./ExamSubmissions";
import ImportWizard from "./ImportWizard";
import RubricEditor from "./RubricEditor";

type Tab = "rubric" | "import" | "papers" | "advanced";
const TABS: [Tab, string][] = [
  ["rubric", "Rubric"],
  ["import", "Import"],
  ["papers", "Papers"],
  ["advanced", "Advanced"],
];

export default function ExamAdmin({ id }: { id: number }) {
  const [exam, setExam] = useState<Exam | null>(null);
  const [model, setModel] = useState("");
  const [tab, setTab] = useState<Tab>("rubric");
  const [tabReady, setTabReady] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [name, setName] = useState("");
  const [nameSaved, setNameSaved] = useState(false);

  // Keep the chosen tab across reloads (and in the URL): #papers, #import, ...
  useEffect(() => {
    const h = window.location.hash.slice(1);
    const found = TABS.find(([k]) => k === h);
    if (found) setTab(found[0]);
    setTabReady(true);
  }, []);
  const pickTab = (t: Tab) => {
    setTab(t);
    window.history.replaceState(null, "", `#${t}`);
  };

  useEffect(() => {
    api<Exam[]>("/exams")
      .then((list) => {
        const e = list.find((x) => x.id === id);
        if (!e) return setError("Exam not found");
        setExam(e); setModel(e.model_override ?? ""); setName(e.name);
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

  async function saveName() {
    if (!exam) return;
    const next = name.trim();
    if (!next) return setError("The exam needs a name");
    setError(""); setNameSaved(false);
    try {
      // PATCH replaces both fields, so send the model override as it is saved, not the unsaved draft.
      const updated = await api<Exam>(`/exams/${id}`, { method: "PATCH", json: { name: next, model_override: exam.model_override } });
      setExam(updated); setName(updated.name); setNameSaved(true);
    } catch (e) { setError(errMsg(e)); }
  }

  async function removeExam() {
    if (!exam) return;
    setError("");
    try {
      if (await deleteExamFlow(exam)) window.location.href = "/admin/exams";
    } catch (e) { setError(errMsg(e)); }
  }

  if (error && !exam) return <Alert tone="bad">{error}</Alert>;
  if (!exam) return <p className="muted">Loading…</p>;

  return (
    <div className="stack">
      <div>
        <a href="/admin/exams" className="small">← Exams</a>
        <h1 style={{ margin: ".25rem 0 0" }}>{exam.name}</h1>
      </div>
      {error && <Alert tone="bad">{error}</Alert>}
      <div className="tabs" role="tablist">
        {TABS.map(([key, label]) => (
          <button key={key} role="tab" aria-selected={tab === key} onClick={() => pickTab(key)}>{label}</button>
        ))}
      </div>
      {tabReady && tab === "rubric" && <RubricEditor examId={id} />}
      {tabReady && tab === "import" && <ImportWizard examId={id} />}
      {tabReady && tab === "papers" && <ExamSubmissions examId={id} />}
      {tabReady && tab === "advanced" && (
        <div className="stack">
          <div className="card row" style={{ alignItems: "flex-end" }}>
            <div className="grow" style={{ maxWidth: 360 }}>
              <label htmlFor="exam-name">Exam name</label>
              <input id="exam-name" type="text" value={name} maxLength={255} onChange={(e) => { setName(e.target.value); setNameSaved(false); }} />
            </div>
            <button onClick={saveName} disabled={!name.trim() || name.trim() === exam.name}>Save</button>
            {nameSaved && <span className="small" style={{ color: "var(--ok)" }}>Renamed. Only the label changes; papers, rubric and grades stay as they are.</span>}
          </div>
          <div className="card row" style={{ alignItems: "flex-end" }}>
            <div className="grow" style={{ maxWidth: 360 }}>
              <label htmlFor="model">Grading model for this exam</label>
              <input id="model" type="text" className="mono" value={model} placeholder="Server default" onChange={(e) => { setModel(e.target.value); setSaved(false); }} />
            </div>
            <button onClick={saveModel} disabled={(exam.model_override ?? "") === model.trim()}>Save</button>
            {saved && <span className="small" style={{ color: "var(--ok)" }}>Saved. Applies to papers graded from now on.</span>}
          </div>
          <div className="card row between">
            <div>
              <strong>Delete exam</strong>
              <div className="small muted">Permanently removes all rubric versions, papers, and grades in this exam.</div>
            </div>
            <button className="danger" onClick={removeExam}>Delete exam</button>
          </div>
        </div>
      )}
    </div>
  );
}
