import { useEffect, useMemo, useState } from "react";
import { api, errMsg } from "../lib/api";
import type { Exam, Submission, SubmissionStatus, UserAdmin } from "../lib/types";
import { Alert, ConfidenceBadge, DownloadIcon, Person, StatusBadge } from "./ui";
import { matchesQuery } from "../lib/search";
import SearchBox, { NoMatches } from "./SearchBox";
import SelectMenu from "./SelectMenu";

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

export default function Queue({ isAdmin = false }: { isAdmin?: boolean }) {
  const [subs, setSubs] = useState<Submission[] | null>(null);
  const [exams, setExams] = useState<Exam[]>([]);
  const [tas, setTas] = useState<UserAdmin[]>([]);
  const [error, setError] = useState("");
  const [examId, setExamId] = useState("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");

  useEffect(() => {
    let alive = true;
    Promise.all([
      api<Submission[]>("/submissions"),
      api<Exam[]>("/exams"),
      isAdmin ? api<UserAdmin[]>("/auth/users").catch(() => []) : Promise.resolve([]),
    ])
      .then(([s, e, u]) => { if (alive) { setSubs(s); setExams(e); setTas(u); } })
      .catch((e) => { if (alive) setError(errMsg(e)); });
    return () => { alive = false; };
  }, [isAdmin]);

  const examName = (id: number) => exams.find((e) => e.id === id)?.name ?? `Exam ${id}`;
  const taOf = (id: number) => tas.find((t) => t.id === id);
  const taText = (id: number) => { const t = taOf(id); return t ? `${t.name} ${t.email}` : `#${id}`; };
  const shown = useMemo(
    () =>
      (subs ?? [])
        .filter((s) => (!examId || s.exam_id === Number(examId)) && (!status || s.status === status))
        .filter((s) =>
          matchesQuery(
            [
              s.student.external_id,
              s.student.name,
              examName(s.exam_id),
              s.status.replace(/_/g, " "),
              ...(isAdmin ? s.ta_ids.map(taText) : []),
            ],
            q,
          ),
        )
        .sort(attentionOrder),
    [subs, exams, tas, isAdmin, examId, status, q],
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
          <span className="field-label">Exam</span>
          <SelectMenu ariaLabel="Exam" value={examId} onChange={setExamId} minWidth={170}
            options={[{ value: "", label: "All exams" }, ...exams.map((e) => ({ value: String(e.id), label: e.name }))]} />
        </div>
        <div>
          <span className="field-label">Status</span>
          <SelectMenu ariaLabel="Status" value={status} onChange={setStatus} minWidth={150}
            options={[{ value: "", label: "All" }, ...STATUSES.map((s) => ({ value: s as string, label: s.replace(/_/g, " ") }))]} />
        </div>
      </div>
      <div className="card table-card">
        {shown.length === 0 ? (
          q.trim() ? <NoMatches query={q} onClear={() => setQ("")} /> : <p className="muted" style={{ padding: "1rem" }}>Nothing here.</p>
        ) : (
          <table>
            <thead>
              <tr><th>Student</th><th>Exam</th><th>Status</th>{isAdmin && <th>TAs</th>}<th>Reviewed</th><th>Lowest confidence</th><th>Overall</th><th></th></tr>
            </thead>
            <tbody>
              {shown.map((s) => {
                const reviewable = ["ready_for_review", "reviewed"].includes(s.status);
                const examTitle = examName(s.exam_id);
                const studentTitle = s.student.name ? `${s.student.external_id} · ${s.student.name}` : s.student.external_id;
                const openReview = () => { if (reviewable) window.location.href = `/review/${s.id}`; };
                return (
                  <tr
                    key={s.id}
                    className={reviewable ? "clickable-row" : undefined}
                    tabIndex={reviewable ? 0 : undefined}
                    title={reviewable ? `Open review for ${s.student.external_id}` : undefined}
                    onClick={openReview}
                    onKeyDown={(e) => {
                      if (reviewable && e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) {
                        e.preventDefault();
                        openReview();
                      }
                    }}
                  >
                    <td className="student-cell">
                      <div className="student-info" title={studentTitle}>
                        <strong>{s.student.external_id}</strong>
                        {s.student.name && <span className="muted small student-name">{s.student.name}</span>}
                      </div>
                    </td>
                    <td className="exam-cell" title={examTitle}>{examTitle}</td>
                    <td><StatusBadge status={s.status} /></td>
                    {isAdmin && (
                      <td className="tas-cell">
                        <div className="tas-list">
                          {s.ta_ids.length
                            ? s.ta_ids.map((id) => taOf(id) ? <Person key={id} user={taOf(id)} /> : <span key={id} className="muted">#{id}</span>)
                            : <span className="muted">unassigned</span>}
                        </div>
                      </td>
                    )}
                    <td>{s.grades_total ? `${s.grades_reviewed} / ${s.grades_total}` : "–"}</td>
                    <td><ConfidenceBadge value={s.min_confidence} /></td>
                    <td><ConfidenceBadge value={s.overall_confidence} /></td>
                    <td>
                      <div className="actions-row">
                        <a
                          className="btn icon-action"
                          href={`/api/submissions/${s.id}/export.pdf`}
                          aria-label={`Export PDF for ${s.student.external_id}`}
                          title="Export PDF"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <DownloadIcon />
                        </a>
                      </div>
                    </td>
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
