import { useEffect, useMemo, useState } from "react";
import { api, errMsg } from "../lib/api";
import type { Exam, Submission, SubmissionStatus } from "../lib/types";
import { Alert, ConfidenceBadge, StatusBadge } from "./ui";
import { matchesQuery } from "../lib/search";
import SearchBox, { NoMatches } from "./SearchBox";

const STATUSES: SubmissionStatus[] = ["ready_for_review", "reviewed", "queued", "grading", "uploaded", "failed"];

/** Needs-attention-first: unreviewed papers with the least confident grade on top. */
function attentionOrder(a: Submission, b: Submission) {
  const rank = (s: Submission) => (s.status === "ready_for_review" ? 0 : s.status === "reviewed" ? 2 : 1);
  return (
    rank(a) - rank(b) ||
    (a.min_confidence ?? 2) - (b.min_confidence ?? 2) ||
    a.student.external_id.localeCompare(b.student.external_id)
  );
}

export default function Queue() {
  const [subs, setSubs] = useState<Submission[] | null>(null);
  const [exams, setExams] = useState<Exam[]>([]);
  const [error, setError] = useState("");
  const [examId, setExamId] = useState("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");

  useEffect(() => {
    Promise.all([api<Submission[]>("/submissions"), api<Exam[]>("/exams")])
      .then(([s, e]) => { setSubs(s); setExams(e); })
      .catch((e) => setError(errMsg(e)));
  }, []);

  const examName = (id: number) => exams.find((e) => e.id === id)?.name ?? `Exam ${id}`;
  const shown = useMemo(
    () =>
      (subs ?? [])
        .filter((s) => (!examId || s.exam_id === Number(examId)) && (!status || s.status === status))
        .filter((s) => matchesQuery([s.student.external_id, s.student.name, examName(s.exam_id), s.status.replace(/_/g, " ")], q))
        .sort(attentionOrder),
    [subs, exams, examId, status, q],
  );
  const todo = (subs ?? []).filter((s) => s.status === "ready_for_review").length;

  if (error) return <Alert tone="bad">{error}</Alert>;
  if (!subs) return <p className="muted">Loading…</p>;

  return (
    <div className="stack">
      <div className="row between">
        <h1 style={{ margin: 0 }}>My queue</h1>
        <span className="muted">{todo} paper{todo === 1 ? "" : "s"} waiting for your review</span>
      </div>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <SearchBox value={q} onChange={setQ} label="Search papers" placeholder="Search student, name, exam, status"
          shown={shown.length} total={subs.length} />
        <div>
          <label htmlFor="f-exam">Exam</label>
          <select id="f-exam" value={examId} onChange={(e) => setExamId(e.target.value)}>
            <option value="">All exams</option>
            {exams.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="f-status">Status</label>
          <select id="f-status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option>
            {STATUSES.map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
          </select>
        </div>
      </div>
      <div className="card" style={{ padding: 0 }}>
        {shown.length === 0 ? (
          q.trim() ? <NoMatches query={q} onClear={() => setQ("")} /> : <p className="muted" style={{ padding: "1rem" }}>Nothing here.</p>
        ) : (
          <table>
            <thead>
              <tr><th>Student</th><th>Exam</th><th>Status</th><th>Reviewed</th><th>Lowest confidence</th><th>Overall</th><th></th></tr>
            </thead>
            <tbody>
              {shown.map((s) => (
                <tr key={s.id}>
                  <td>
                    <strong>{s.student.external_id}</strong>
                    {s.student.name && <div className="muted small">{s.student.name}</div>}
                  </td>
                  <td>{examName(s.exam_id)}</td>
                  <td><StatusBadge status={s.status} /></td>
                  <td>{s.grades_total ? `${s.grades_reviewed} / ${s.grades_total}` : "–"}</td>
                  <td><ConfidenceBadge value={s.min_confidence} /></td>
                  <td><ConfidenceBadge value={s.overall_confidence} /></td>
                  <td>
                    {["ready_for_review", "reviewed"].includes(s.status) && (
                      <a className="btn" href={`/review/${s.id}`}>{s.status === "reviewed" ? "Open" : "Review"}</a>
                    )}
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
