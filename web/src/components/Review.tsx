import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, errMsg } from "../lib/api";
import type { Grade, SubmissionDetail } from "../lib/types";
import { pageScrollFor } from "../lib/dockedScroll";
import { Alert, ConfidenceBadge, StatusBadge, fmt } from "./ui";

interface Draft { score: string; feedback: string }
const LOW = 0.6;

const baseline = (g: Grade): Draft => ({
  score: String(g.final_score ?? g.ai_score ?? 0),
  feedback: g.final_feedback ?? g.ai_feedback,
});

export default function Review({ id }: { id: number }) {
  const [detail, setDetail] = useState<SubmissionDetail | null>(null);
  const [drafts, setDrafts] = useState<Record<number, Draft>>({});
  const [page, setPage] = useState(1);
  const [activeGrade, setActiveGrade] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<number | "complete" | null>(null);
  // Per-grade notice, e.g. "changed by someone else".
  const [notes, setNotes] = useState<Record<number, { tone: "bad" | "warn" | "ok"; text: string }>>({});

  const load = useCallback(async () => {
    try {
      const d = await api<SubmissionDetail>(`/submissions/${id}/detail`);
      setDetail(d);
      setDrafts(Object.fromEntries(d.grades.map((g) => [g.id, baseline(g)])));
    } catch (e) {
      setError(errMsg(e));
    }
  }, [id]);
  useEffect(() => { load(); }, [load]);

  // A new page starts at its top, not wherever the previous one was scrolled to.
  const sheetRef = useRef<HTMLDivElement>(null);
  useEffect(() => { sheetRef.current?.scrollTo({ top: 0 }); }, [page]);

  // Wheel over either pane: move the page first until the panes reach the top of the window, then the pane.
  const gradesRef = useRef<HTMLElement>(null);
  const ready = detail !== null;
  const pageCount = detail?.pages.length ?? 0;
  useEffect(() => {
    const panes = [sheetRef.current, gradesRef.current].filter((p): p is HTMLElement => p !== null);
    const onWheel = (e: WheelEvent) => {
      if (e.ctrlKey) return; // pinch / ctrl-wheel zoom
      const pane = e.currentTarget as HTMLElement;
      const by = pageScrollFor({
        deltaY: e.deltaY,
        deltaMode: e.deltaMode,
        paneScrollTop: pane.scrollTop,
        pageY: window.scrollY,
        pageMax: document.documentElement.scrollHeight - window.innerHeight,
        viewportH: window.innerHeight,
        nested: getComputedStyle(pane).overflowY !== "visible",
      });
      if (by) {
        e.preventDefault();
        window.scrollBy({ top: by });
      }
    };
    panes.forEach((p) => p.addEventListener("wheel", onWheel, { passive: false }));
    return () => panes.forEach((p) => p.removeEventListener("wheel", onWheel));
  }, [ready, pageCount]);

  const pageOf = useMemo(() => {
    const m: Record<string, number> = {};
    detail?.pages.forEach((p) => p.question_numbers.forEach((q) => { m[q] ??= p.index; }));
    return m;
  }, [detail]);

  if (error && !detail) return <Alert tone="bad">{error}</Alert>;
  if (!detail) return <p className="muted">Loading…</p>;

  const editable = detail.status === "ready_for_review" || detail.status === "reviewed";
  const current = detail.pages.find((p) => p.index === page);
  const pending = detail.grades.filter((g) => g.status === "pending").length;
  const effective = (g: Grade) => g.final_score ?? g.ai_score ?? 0;
  const total = detail.grades.reduce((s, g) => s + effective(g), 0);
  const maxTotal = detail.grades.reduce((s, g) => s + g.max_points, 0);

  const setDraft = (g: Grade, patch: Partial<Draft>) =>
    setDrafts((d) => ({ ...d, [g.id]: { ...d[g.id], ...patch } }));
  const isDirty = (g: Grade) => {
    const b = baseline(g), d = drafts[g.id];
    return d.score !== b.score || d.feedback !== b.feedback;
  };
  const scoreError = (g: Grade): string => {
    const n = Number(drafts[g.id].score);
    if (drafts[g.id].score.trim() === "" || Number.isNaN(n)) return "Enter a number";
    if (n < -g.max_points || n > g.max_points) return `Must be between ${-g.max_points} and ${g.max_points}`;
    return "";
  };

  function replaceGrade(updated: Grade) {
    setDetail((d) => d && { ...d, grades: d.grades.map((g) => (g.id === updated.id ? updated : g)) });
  }

  async function save(g: Grade, d: Draft) {
    setBusy(g.id);
    setNotes((n) => { const { [g.id]: _, ...rest } = n; return rest; });
    try {
      const updated = await api<Grade>(`/grades/${g.id}`, {
        method: "PATCH",
        json: { version: g.version, final_score: Number(d.score), final_feedback: d.feedback },
      });
      replaceGrade(updated);
      setDrafts((all) => ({ ...all, [g.id]: baseline(updated) }));
      setNotes((n) => ({ ...n, [g.id]: { tone: "ok", text: updated.status === "reviewed" ? "Approved" : "Saved your changes" } }));
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && (e.detail as any)?.current) {
        // Take the new version so the next save is deliberate, but keep the user's draft text.
        const cur = (e.detail as any).current as Grade;
        replaceGrade(cur);
        setNotes((n) => ({
          ...n,
          [g.id]: {
            tone: "warn",
            text: `Someone else changed this grade first (now ${fmt(cur.final_score)} pts: “${cur.final_feedback ?? ""}”). Your draft is kept below — save again to overwrite theirs, or reset to take theirs.`,
          },
        }));
      } else {
        setNotes((n) => ({ ...n, [g.id]: { tone: "bad", text: errMsg(e) } }));
      }
    } finally {
      setBusy(null);
    }
  }

  async function complete() {
    setBusy("complete");
    try {
      await api(`/submissions/${id}/complete`, { method: "POST" });
      await load();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  const focusGrade = (g: Grade) => {
    setActiveGrade(g.id);
    if (pageOf[g.question_number]) setPage(pageOf[g.question_number]);
  };

  const completeButton = (
    <button className="primary" onClick={complete} disabled={!editable || pending > 0 || busy !== null || detail.status === "reviewed"}
      title={pending ? `${pending} question(s) still need review` : ""}>
      {detail.status === "reviewed" ? "Review complete" : "Complete review"}
    </button>
  );

  return (
    <div className="stack">
      <div className="row between">
        <div>
          <a href="/" className="small">← My queue</a>
          <h1 style={{ margin: ".25rem 0 0" }}>
            {detail.student.external_id}
            {detail.student.name && <span className="muted"> · {detail.student.name}</span>}
          </h1>
        </div>
        <div className="row">
          <StatusBadge status={detail.status} />
          <ConfidenceBadge value={detail.overall_confidence} label="overall" />
          <strong>{fmt(total)} / {fmt(maxTotal)} pts</strong>
          <a className="btn" href={`/api/submissions/${id}/export.pdf`}>Export PDF</a>
          {completeButton}
        </div>
      </div>

      {error && <Alert tone="bad">{error}</Alert>}
      {!editable && <Alert tone="warn">This paper is {detail.status.replace(/_/g, " ")}{detail.error ? `: ${detail.error}` : ""}. It can't be reviewed right now.</Alert>}
      {editable && pending > 0 && <Alert>Grades below are Claude's suggestions. Check each against the page and approve or edit it — {pending} left.</Alert>}

      <div className="review">
        <section className="viewer card" aria-label="Scanned pages">
          <div className="row between">
            <h2 style={{ margin: 0 }}>Page {page} of {detail.pages.length}</h2>
            {current && <ConfidenceBadge value={current.confidence} label="read" />}
          </div>
          <div className="thumbs" role="group" aria-label="Pages">
            {detail.pages.map((p) => (
              <button key={p.index} aria-pressed={p.index === page}
                className={(p.confidence ?? 1) < LOW ? "low" : ""} onClick={() => setPage(p.index)}
                title={p.question_numbers.length ? `Questions ${p.question_numbers.join(", ")}` : "No questions found"}>
                {p.index}{p.question_numbers.length ? ` · Q${p.question_numbers.join(",")}` : ""}
              </button>
            ))}
          </div>
          {current ? (
            <div className="sheet" ref={sheetRef} tabIndex={0} aria-label={`Scanned page ${page}, scrollable`}>
              <img src={`/api/submissions/${id}/pages/${page}/image`} alt={`Scanned page ${page}`} />
              <details style={{ marginTop: ".5rem" }}>
                <summary className="small">Claude's transcription of this page</summary>
                <div className="transcript">{current.transcription || "(empty)"}</div>
              </details>
            </div>
          ) : (
            <p className="muted">No page images yet.</p>
          )}
        </section>

        <section className="stack grades" aria-label="Grades" ref={gradesRef}>
          {detail.grades.map((g) => {
            const d = drafts[g.id];
            const err = scoreError(g);
            const dirty = isDirty(g);
            const note = notes[g.id];
            const low = (g.ai_confidence ?? 1) < LOW && g.status === "pending";
            return (
              <article key={g.id} className={`card grade ${activeGrade === g.id ? "active" : ""} ${low ? "low" : ""}`}
                onClick={() => focusGrade(g)}>
                <div className="row between">
                  <h3 style={{ margin: 0 }}>Question {g.question_number} <span className="muted">· max {fmt(g.max_points)}</span></h3>
                  <div className="row">
                    <StatusBadge status={g.status} />
                    <ConfidenceBadge value={g.ai_confidence} />
                  </div>
                </div>
                {g.question_text && <p className="small muted" style={{ margin: ".35rem 0" }}>{g.question_text}</p>}
                {(g.expected_answer || g.criteria.length > 0) && (
                  <details className="small" onClick={(e) => e.stopPropagation()}>
                    <summary>Rubric</summary>
                    {g.expected_answer && <div className="multiline"><strong>Expected:</strong>{"\n"}{g.expected_answer}</div>}
                    <ul>{g.criteria.map((c, i) => <li key={i}>{c.description} ({fmt(c.points)})</li>)}</ul>
                  </details>
                )}
                <div className="small" style={{ margin: ".5rem 0" }}>
                  <strong>Claude:</strong> {fmt(g.ai_score)} pts
                  <div className="multiline claude-feedback">{g.ai_feedback}</div>
                </div>

                <div onClick={(e) => e.stopPropagation()}>
                  <div className="grid2">
                    <div className="field">
                      <label htmlFor={`s-${g.id}`}>Score</label>
                      <input id={`s-${g.id}`} type="number" step="0.5" min={-g.max_points} max={g.max_points} value={d.score}
                        disabled={!editable} aria-invalid={!!err} onChange={(e) => setDraft(g, { score: e.target.value })} />
                      {err && <span className="small" style={{ color: "var(--bad)" }}>{err}</span>}
                    </div>
                  </div>
                  <div className="field">
                    <label htmlFor={`f-${g.id}`}>Feedback</label>
                    <textarea id={`f-${g.id}`} value={d.feedback} disabled={!editable}
                      onChange={(e) => setDraft(g, { feedback: e.target.value })} />
                  </div>
                  {note && <div style={{ marginBottom: ".5rem" }}><Alert tone={note.tone}>{note.text}</Alert></div>}
                  <div className="row">
                    <button className="primary" disabled={!editable || busy !== null || !!err || !dirty} onClick={() => save(g, d)}>
                      {busy === g.id ? "Saving…" : "Save changes"}
                    </button>
                    <button disabled={!editable || busy !== null || dirty || g.status !== "pending"}
                      title={dirty ? "Save or reset your edits first" : "Mark Claude's grade as checked and correct"}
                      onClick={() => save(g, { score: String(g.ai_score ?? 0), feedback: g.ai_feedback })}>
                      Approve AI grade
                    </button>
                    <button className="link" disabled={!dirty} onClick={() => setDrafts((all) => ({ ...all, [g.id]: baseline(g) }))}>Reset</button>
                  </div>
                </div>
              </article>
            );
          })}
          <div className="card row between grades-footer">
            <span><strong>{fmt(total)} / {fmt(maxTotal)} pts</strong> · {pending ? `${pending} left to check` : "all checked"}</span>
            {completeButton}
          </div>
        </section>
      </div>
    </div>
  );
}
