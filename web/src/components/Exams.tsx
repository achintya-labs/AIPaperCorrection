import { useEffect, useState } from "react";
import { api, errMsg } from "../lib/api";
import type { Exam } from "../lib/types";
import { deleteExamFlow } from "../lib/actions";
import { Alert, TrashIcon } from "./ui";
import { matchesQuery } from "../lib/search";
import SearchBox, { NoMatches } from "./SearchBox";
import { can } from "../lib/access";

export default function Exams({ permissions = [] }: { permissions?: string[] }) {
  const mayCreate = can(permissions, "exams.create");
  const mayDelete = can(permissions, "exams.delete");
  const mayGrade = can(permissions, "tab.papers"); // the grading-readiness banner is about grading, which only they do
  const [exams, setExams] = useState<Exam[] | null>(null);
  const [name, setName] = useState("");
  const [q, setQ] = useState("");
  const [ok, setOk] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [grader, setGrader] = useState<{ provider?: string; ready: boolean; detail?: string } | null>(null);

  const load = () => api<Exam[]>("/exams").then(setExams).catch((e) => setError(errMsg(e)));
  useEffect(() => {
    let alive = true;
    api<Exam[]>("/exams").then((x) => { if (alive) setExams(x); }).catch((e) => { if (alive) setError(errMsg(e)); });
    if (mayGrade) {
      api<{ provider?: string; ready: boolean; detail?: string }>("/grader/status")
        .then((g) => { if (alive) setGrader(g); })
        .catch(() => { if (alive) setGrader(null); });
    }
    return () => { alive = false; };
  }, [mayGrade]);

  async function remove(x: Exam) {
    setError(""); setOk(""); setBusy(true);
    try {
      if (await deleteExamFlow(x)) { setOk(`Deleted “${x.name}”.`); await load(); }
    } catch (err) { setError(errMsg(err)); } finally { setBusy(false); }
  }

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError(""); setBusy(true);
    try {
      const exam = await api<Exam>("/exams", { method: "POST", json: { name } });
      window.location.href = `/admin/exams/${exam.id}`;
    } catch (err) {
      setError(errMsg(err));
      setBusy(false);
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
      {mayCreate && <form className="card row" onSubmit={create}>
        <div className="grow">
          <label htmlFor="exam-name">New exam</label>
          <input id="exam-name" type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Physics 101 midterm" required />
        </div>
        <button className="primary" disabled={busy} style={{ alignSelf: "flex-end" }}>{busy ? "Creating…" : "Create"}</button>
      </form>}
      {exams && exams.length > 0 && (
        <SearchBox value={q} onChange={setQ} label="Search exams" placeholder="Search exams"
          shown={visible.length} total={exams.length} />
      )}
      <div className="card table-card">
        {!exams ? <p className="muted" style={{ padding: "1rem" }}>Loading…</p> : exams.length === 0 ? (
          <p className="muted" style={{ padding: "1rem" }}>No exams yet.</p>
        ) : visible.length === 0 ? (
          <NoMatches query={q} onClear={() => setQ("")} />
        ) : (
          <table>
            <thead><tr><th>Name</th><th>Model</th>{mayDelete && <th></th>}</tr></thead>
            <tbody>
              {visible.map((x) => {
                const open = () => { window.location.href = `/admin/exams/${x.id}`; };
                return (
                  <tr
                    key={x.id}
                    className="clickable-row"
                    tabIndex={0}
                    role="link"
                    onClick={open}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
                    }}
                  >
                    <td>{x.name}</td>
                    <td className="mono small">{x.model_override ?? <span className="muted">default</span>}</td>
                    {mayDelete && <td>
                      <div className="actions-row">
                        <button
                          type="button"
                          className="icon-action bad"
                          disabled={busy}
                          aria-label={`Delete ${x.name}`}
                          title="Delete exam"
                          onClick={(e) => { e.stopPropagation(); remove(x); }}
                        >
                          <TrashIcon />
                        </button>
                      </div>
                    </td>}
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
