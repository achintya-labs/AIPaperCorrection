import { useEffect, useState } from "react";
import { api, errMsg } from "../lib/api";
import { deleteExamFlow } from "../lib/actions";
import type { Exam } from "../lib/types";
import { Alert } from "./ui";
import ExamSubmissions from "./ExamSubmissions";
import ImportWizard from "./ImportWizard";
import RubricEditor from "./RubricEditor";

import ExamOverview from "./ExamOverview";
import { can, pickTab as chooseTab, visibleTabs, type ExamTab } from "../lib/access";

export default function ExamAdmin({ id, permissions = [] }: { id: number; permissions?: string[] }) {
  const TABS = visibleTabs(permissions); // only the tabs this person's role allows; the API refuses the rest as well
  const mayDelete = can(permissions, "exams.delete");
  const [exam, setExam] = useState<Exam | null>(null);
  const [model, setModel] = useState("");
  const [tab, setTab] = useState<ExamTab | null>(null);
  const [tabReady, setTabReady] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [name, setName] = useState("");
  const [nameSaved, setNameSaved] = useState(false);

  // Keep the chosen tab across reloads (and in the URL): #papers, #import, ...
  useEffect(() => {
    setTab(chooseTab(window.location.hash, permissions));
    setTabReady(true);
  }, [permissions.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps
  const pickTab = (t: ExamTab) => {
    setTab(t);
    window.history.replaceState(null, "", `#${t}`);
  };

  useEffect(() => {
    let alive = true;
    api<Exam[]>("/exams")
      .then((list) => {
        if (!alive) return;
        const e = list.find((x) => x.id === id);
        if (!e) return setError("Exam not found");
        setExam(e); setModel(e.model_override ?? ""); setName(e.name);
      })
      .catch((e) => { if (alive) setError(errMsg(e)); });
    return () => { alive = false; };
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
        {TABS.map(({ key, label }) => (
          <button key={key} role="tab" aria-selected={tab === key} onClick={() => pickTab(key)}>{label}</button>
        ))}
      </div>
      {tabReady && tab === "rubric" && <RubricEditor examId={id} canEdit={can(permissions, "rubric.edit")} />}
      {tabReady && tab === "import" && <ImportWizard examId={id} />}
      {tabReady && tab === "papers" && <ExamSubmissions examId={id} canManage={can(permissions, "papers.manage")} />}
      {tabReady && tab === "overview" && <ExamOverview examId={id} canExport={can(permissions, "overview.export")} />}
      {tabReady && !tab && <Alert tone="warn">Your role can't open any part of this exam.</Alert>}
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
          {mayDelete && <div className="card row between">
            <div>
              <strong>Delete exam</strong>
              <div className="small muted">Permanently removes all rubric versions, papers, and grades in this exam.</div>
            </div>
            <button className="danger" onClick={removeExam}>Delete exam</button>
          </div>}
        </div>
      )}
    </div>
  );
}
