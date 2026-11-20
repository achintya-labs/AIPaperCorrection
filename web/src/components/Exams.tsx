import { useEffect, useState } from "react";
import { api, errMsg } from "../lib/api";
import type { Exam } from "../lib/types";
import { deleteExamFlow } from "../lib/actions";
import { Alert } from "./ui";
import { matchesQuery } from "../lib/search";
import SearchBox, { NoMatches } from "./SearchBox";

export default function Exams() {
  const [exams, setExams] = useState<Exam[] | null>(null);
  const [name, setName] = useState("");
  const [q, setQ] = useState("");
  const [ok, setOk] = useState("");
  const [error, setError] = useState("");
  const [grader, setGrader] = useState<{ provider?: string; ready: boolean; detail?: string } | null>(null);

  const load = () => api<Exam[]>("/exams").then(setExams).catch((e) => setError(errMsg(e)));
  useEffect(() => { load(); }, []);
  useEffect(() => {
    api<{ provider?: string; ready: boolean; detail?: string }>("/grader/status").then(setGrader).catch(() => setGrader(null));
  }, []);

  async function remove(x: Exam) {
    setError(""); setOk("");
    try {
      if (await deleteExamFlow(x)) { setOk(`Deleted “${x.name}”.`); await load(); }
    } catch (err) { setError(errMsg(err)); }
  }

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const exam = await api<Exam>("/exams", { method: "POST", json: { name } });
      window.location.href = `/admin/exams/${exam.id}`;
    } catch (err) {
      setError(errMsg(err));
    }
  }

  const visible = (exams ?? []).filter((x) => matchesQuery([x.name, x.model_override ?? "default"], q));

  return (
    <div className="stack">
      <h1>Exams</h1>
      {error && <Alert tone="bad">{error}</Alert>}
      {ok && <Alert tone="ok">{ok}</Alert>}
      {grader && !grader.ready && (
        <Alert tone="warn">
          <strong>Grading isn't ready{grader.detail ? `: ${grader.detail}` : ""}.</strong> Papers will fail until this is fixed.
          To sign Claude in, run <span className="mono">./scripts/claude-login.sh</span> on the server (once), then reload this page.
        </Alert>
      )}
      <form className="card row" onSubmit={create}>
        <div className="grow">
          <label htmlFor="exam-name">New exam</label>
          <input id="exam-name" type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Physics 101 midterm" required />
        </div>
        <button className="primary" style={{ alignSelf: "flex-end" }}>Create</button>
      </form>
      {exams && exams.length > 0 && (
        <SearchBox value={q} onChange={setQ} label="Search exams" placeholder="Search exams"
          shown={visible.length} total={exams.length} />
      )}
      <div className="card" style={{ padding: 0 }}>
        {!exams ? <p className="muted" style={{ padding: "1rem" }}>Loading…</p> : exams.length === 0 ? (
          <p className="muted" style={{ padding: "1rem" }}>No exams yet.</p>
        ) : visible.length === 0 ? (
          <NoMatches query={q} onClear={() => setQ("")} />
        ) : (
          <table>
            <thead><tr><th>Name</th><th>Model</th><th></th></tr></thead>
            <tbody>
              {visible.map((x) => (
                <tr key={x.id}>
                  <td>{x.name}</td>
                  <td className="mono small">{x.model_override ?? <span className="muted">default</span>}</td>
                  <td><div className="row"><a className="btn" href={`/admin/exams/${x.id}`}>Manage</a><button className="danger" onClick={() => remove(x)}>Delete</button></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
