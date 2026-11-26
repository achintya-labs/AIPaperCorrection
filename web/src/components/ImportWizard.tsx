import { useEffect, useState } from "react";
import { api, ApiError, errMsg } from "../lib/api";
import type { AutoSplitResult, ImportItem, ImportResult, UserAdmin } from "../lib/types";
import { Alert, ConfidenceBadge } from "./ui";
import { autoSplitButton, isSplitInFlight } from "../lib/importState";
import { matchesQuery } from "../lib/search";
import SearchBox, { NoMatches } from "./SearchBox";
import TaMultiSelect from "./TaMultiSelect";

type Layout = "files" | "combined" | "auto";
type Assign = "" | "csv" | "round_robin" | "none";

interface EditableSplit {
  student_external_id: string;
  student_name: string;
  first_page: number;
  last_page: number;
  confidence: number | null;
  ta_ids: number[];
}

const TEMPLATES: Record<Layout, string> = {
  auto: "roll_number,name\n2024CS01,Ann Lee\n2024CS02,Bo Chen\n",
  files: "student_id,name,ta_emails\n1001,Ann Lee,ta1@school.edu;ta2@school.edu\n1002,Bo Chen,ta1@school.edu\n",
  combined: "student_id,name,first_page,last_page,ta_emails\n1001,Ann Lee,2,5,ta1@school.edu\n1002,Bo Chen,6,9,ta2@school.edu\n",
};
const TONE = { ok: "ok", conflict: "warn", error: "bad" } as const;

export default function ImportWizard({ examId }: { examId: number }) {
  const [layout, setLayout] = useState<Layout>("files");
  const [pdfs, setPdfs] = useState<File[]>([]);
  const [csv, setCsv] = useState<File | null>(null);
  const [rosterCsv, setRosterCsv] = useState<File | null>(null);
  const [assign, setAssign] = useState<Assign>("");
  const [reviewers, setReviewers] = useState(1);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [autoResult, setAutoResult] = useState<AutoSplitResult | null>(null);
  const [splits, setSplits] = useState<EditableSplit[]>([]);
  const [tas, setTas] = useState<UserAdmin[]>([]);
  const [bulkTa, setBulkTa] = useState<string>("");
  const [zoomPage, setZoomPage] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // Bumped to remount the (uncontrolled) file inputs so they never show a file the state has forgotten.
  const [inputKey, setInputKey] = useState(0);

  useEffect(() => {
    api<AutoSplitResult[]>(`/exams/${examId}/imports/auto-split`)
      .then((jobs) => {
        if (jobs.length > 0) {
          const latest = jobs[0];
          setLayout("auto");
          setAutoResult(latest);
          if (latest.status === "ready") {
            setSplits(latest.splits.map((sp) => ({ ...sp, ta_ids: [] })));
          }
        }
      })
      .catch(() => {});
  }, [examId]);

  useEffect(() => {
    if (layout !== "auto") return;
    api<UserAdmin[]>("/auth/users")
      .then((u) => setTas(u.filter((x) => x.active && x.role === "ta")))
      .catch(() => {});
  }, [layout]);

  const splitInFlight = isSplitInFlight(autoResult);
  const splitLocked = layout === "auto" && (splitInFlight || autoResult?.status === "ready");
  const splitBtn = autoSplitButton({ pdfCount: pdfs.length, busy, autoResult });
  useEffect(() => {
    if (!splitInFlight || !autoResult) return;
    const token = autoResult.split_token;
    const timer = setInterval(() => {
      api<AutoSplitResult>(`/exams/${examId}/imports/auto-split/${token}`)
        .then((res) => {
          setAutoResult(res);
          if (res.status === "ready") {
            setSplits(res.splits.map((sp) => ({ ...sp, ta_ids: [] })));
          }
        })
        .catch((e) => {
          if (e instanceof ApiError && e.status === 404) {
            // Job was discarded elsewhere: stop polling instead of erroring forever.
            setAutoResult(null); setSplits([]);
          } else setError(errMsg(e));
        });
    }, 3000);
    return () => clearInterval(timer);
  }, [splitInFlight, autoResult?.split_token, examId]);

  useEffect(() => {
    if (zoomPage === null || !autoResult) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setZoomPage(null);
      else if (e.key === "ArrowLeft") setZoomPage((p) => (p && p > 1 ? p - 1 : p));
      else if (e.key === "ArrowRight") setZoomPage((p) => (p && p < autoResult.page_count ? p + 1 : p));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoomPage, autoResult]);

  function chooseLayout(next: Layout) {
    if (next === layout) return;
    setLayout(next); setResult(null); setError("");
    // Files picked for one layout are wrong for another (multi vs single PDF): start clean.
    setPdfs([]); setCsv(null); setRosterCsv(null); setInputKey((k) => k + 1);
  }

  const ready =
    pdfs.length > 0 &&
    (layout === "files" || (pdfs.length === 1 && (layout === "auto" || csv !== null)));

  async function run(dry: boolean) {
    setBusy(true); setError("");
    const fd = new FormData();
    pdfs.forEach((f) => fd.append("files", f));
    if (csv) fd.append("mapping_csv", csv);
    if (assign) fd.append("assign", assign);
    fd.append("reviewers_per_paper", String(reviewers));
    fd.append("dry_run", String(dry));
    try {
      setResult(await api<ImportResult>(`/exams/${examId}/imports`, { method: "POST", body: fd }));
    } catch (e) { setError(errMsg(e)); } finally { setBusy(false); }
  }

  async function runAutoSplit() {
    if (pdfs.length !== 1) return;
    setBusy(true); setError(""); setResult(null);
    const stale = autoResult && autoResult.status === "failed" ? autoResult : null;
    const fd = new FormData();
    fd.append("file", pdfs[0]);
    if (rosterCsv) fd.append("roll_numbers_csv", rosterCsv);
    try {
      const res = await api<AutoSplitResult>(`/exams/${examId}/imports/auto-split`, { method: "POST", body: fd });
      if (stale) {
        // Replace the failed job rather than leaving it behind (best effort).
        api(`/exams/${examId}/imports/auto-split/${stale.split_token}`, { method: "DELETE" }).catch(() => {});
      }
      setAutoResult(res);
      setSplits(res.status === "ready" ? res.splits.map((sp) => ({ ...sp, ta_ids: [] })) : []);
    } catch (e) { setError(errMsg(e)); } finally { setBusy(false); }
  }

  async function discardAutoSplit() {
    if (!autoResult) return;
    setBusy(true); setError("");
    try {
      await api(`/exams/${examId}/imports/auto-split/${autoResult.split_token}`, { method: "DELETE" });
      setAutoResult(null); setSplits([]); setZoomPage(null);
    } catch (e) { setError(errMsg(e)); } finally { setBusy(false); }
  }

  async function retryAutoSplit() {
    if (!autoResult) return;
    setBusy(true); setError("");
    try {
      const res = await api<AutoSplitResult>(`/exams/${examId}/imports/auto-split/${autoResult.split_token}/retry`, {
        method: "POST",
      });
      setAutoResult(res);
      setSplits([]);
    } catch (e) { setError(errMsg(e)); } finally { setBusy(false); }
  }

  async function commitAutoSplit() {
    if (!autoResult) return;
    setBusy(true); setError("");
    try {
      const res = await api<ImportResult>(`/exams/${examId}/imports/auto-split/${autoResult.split_token}/commit`, {
        method: "POST",
        json: {
          items: splits.map((s) => ({
            student_external_id: s.student_external_id.trim(),
            student_name: s.student_name.trim(),
            first_page: s.first_page,
            last_page: s.last_page,
            ta_ids: s.ta_ids,
          })),
        },
      });
      setResult(res);
      setAutoResult(null); setSplits([]); setZoomPage(null);
      setPdfs([]); setRosterCsv(null); setInputKey((k) => k + 1);
    } catch (e) { setError(errMsg(e)); } finally { setBusy(false); }
  }

  function applyBulkTa() {
    if (bulkTa === "") {
      setSplits((all) => all.map((s) => ({ ...s, ta_ids: [] })));
    } else if (bulkTa === "round_robin") {
      if (tas.length === 0) return;
      setSplits((all) => all.map((s, i) => ({ ...s, ta_ids: [tas[i % tas.length].id] })));
    } else {
      const id = Number(bulkTa);
      setSplits((all) => all.map((s) => ({ ...s, ta_ids: [id] })));
    }
  }

  function downloadTemplate() {
    const url = URL.createObjectURL(new Blob([TEMPLATES[layout]], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url; a.download = layout === "auto" ? "roster.csv" : `import-${layout}.csv`; a.click();
    URL.revokeObjectURL(url);
  }

  const counts = (result?.items ?? []).reduce((c, i: ImportItem) => ({ ...c, [i.status]: (c[i.status] ?? 0) + 1 }), {} as Record<string, number>);
  const reset = () => {
    setResult(null);
    setAutoResult(null);
    setSplits([]);
    setPdfs([]);
    setCsv(null);
    setRosterCsv(null);
    setZoomPage(null);
    setInputKey((k) => k + 1);
  };

  const splitErrors = (() => {
    if (!autoResult) return [];
    const errs: string[] = [];
    const seen = new Set<string>();
    const sorted = [...splits].sort((a, b) => a.first_page - b.first_page || a.last_page - b.last_page);
    let lastEnd = 0;
    for (const s of sorted) {
      const sid = s.student_external_id.trim();
      if (!sid) errs.push("Every paper needs a roll number / student ID.");
      else if (seen.has(sid)) errs.push(`Duplicate roll number: ${sid}`);
      seen.add(sid);
      if (s.first_page < 1 || s.last_page < s.first_page || s.last_page > autoResult.page_count) {
        errs.push(`Invalid page range ${s.first_page}-${s.last_page} (1..${autoResult.page_count}).`);
      } else if (s.first_page <= lastEnd) {
        errs.push(`Page range ${s.first_page}-${s.last_page} overlaps another paper.`);
      }
      lastEnd = Math.max(lastEnd, s.last_page);
    }
    return [...new Set(errs)];
  })();


  const layoutOptions: { value: Layout; title: string; desc: React.ReactNode }[] = [
    { value: "files", title: "One PDF per student", desc: <>Each file is named <span className="mono">&lt;student_id&gt;.pdf</span></> },
    { value: "combined", title: "One combined PDF", desc: "Page ranges per student come from a CSV" },
    { value: "auto", title: "Combined PDF, auto-split", desc: "Claude finds each paper and its roll number" },
  ];

  return (
    <div className="import">
      <h2 className="import-title">Import answer sheets</h2>
      {error && <Alert tone="bad">{error}</Alert>}

      {result && !result.dry_run ? (
        <div className="stack">
          <Alert tone={result.created ? "ok" : "warn"}>Imported {result.created} paper{result.created === 1 ? "" : "s"}. Grading doesn't start automatically: use “Grade all” or select papers on the Papers tab when you're ready.</Alert>
          <ImportTable items={result.items} />
          <div className="actionbar"><button onClick={reset}>Import more</button></div>
        </div>
      ) : (
        <>
          <div className="card import-card">
            <fieldset className="import-section">
              <legend className="section-title">How are the scans organised?</legend>
              <div className="opts">
                {layoutOptions.map((o) => (
                  <label key={o.value} className="opt">
                    <input type="radio" name="layout" checked={layout === o.value} onChange={() => chooseLayout(o.value)} />
                    <span className="opt-title">{o.title}</span>
                    <span className="opt-desc">{o.desc}</span>
                  </label>
                ))}
              </div>
            </fieldset>

            <div className="import-section">
              <div className="section-title">Files</div>
              <div className="fields">
                <div className="field">
                  <label htmlFor="pdfs">{layout === "files" ? "PDF files" : "Combined PDF"}</label>
                  <input key={`pdfs-${inputKey}`} id="pdfs" type="file" accept="application/pdf" multiple={layout === "files"} disabled={splitLocked}
                    onChange={(e) => { setPdfs(Array.from(e.target.files ?? [])); setResult(null); }} />
                  <span className="hint">
                    {pdfs.length > 0
                      ? `${pdfs.length} file${pdfs.length === 1 ? "" : "s"} selected`
                      : layout === "files" ? "Select one or more PDFs." : "Select a single PDF."}
                  </span>
                </div>
                {layout !== "auto" ? (
                  <div className="field">
                    <label htmlFor="csv">Mapping CSV {layout === "files" ? "(optional)" : "(required)"}</label>
                    <input key={`csv-${inputKey}`} id="csv" type="file" accept=".csv,text/csv" onChange={(e) => { setCsv(e.target.files?.[0] ?? null); setResult(null); }} />
                    <span className="hint">
                      <button type="button" className="link small" onClick={downloadTemplate}>Download a template</button>
                    </span>
                  </div>
                ) : (
                  <div className="field">
                    <label htmlFor="roster-csv">Student roster (.csv, optional)</label>
                    <input key={`roll-${inputKey}`} id="roster-csv" type="file" accept=".csv,text/csv" disabled={splitLocked}
                      onChange={(e) => setRosterCsv(e.target.files?.[0] ?? null)} />
                    <span className="hint">
                      One column of roll numbers, or two: roll number and name. A header row is optional. Claude may only use
                      these roll numbers, and the names help it match papers.{" "}
                      <button type="button" className="link small" onClick={downloadTemplate}>Download a template</button>
                    </span>
                  </div>
                )}
              </div>
            </div>

            {layout !== "auto" ? (
              <>
                <div className="import-section">
                  <div className="section-title">Assignment</div>
                  <div className="fields">
                    <div className="field">
                      <label htmlFor="assign">Assign to TAs</label>
                      <select id="assign" value={assign} onChange={(e) => { setAssign(e.target.value as Assign); setResult(null); }}>
                        <option value="">Automatic (from CSV if it has ta_emails, else nobody)</option>
                        <option value="csv">From the CSV's ta_emails column</option>
                        <option value="round_robin">Spread evenly across all TAs</option>
                        <option value="none">Don't assign yet</option>
                      </select>
                    </div>
                    {assign === "round_robin" && (
                      <div className="field">
                        <label htmlFor="rev">TAs per paper</label>
                        <input id="rev" type="number" min={1} value={reviewers} onChange={(e) => setReviewers(Math.max(1, Number(e.target.value)))} />
                      </div>
                    )}
                  </div>
                  <p className="hint import-note">
                    CSV columns: <span className="mono">student_id</span>, <span className="mono">name</span>, <span className="mono">ta_emails</span> (separate several with <span className="mono">;</span>)
                    {layout === "combined" && <>, and <span className="mono">first_page</span>/<span className="mono">last_page</span> (1-based, inclusive; pages outside every range are skipped)</>}.
                  </p>
                </div>
                <div className="actionbar">
                  <button className="primary" disabled={!ready || busy} onClick={() => run(true)}>{busy ? "Checking…" : "Preview"}</button>
                  {!ready && <span className="hint">{layout === "combined" ? "Choose one PDF and a CSV." : "Choose at least one PDF."}</span>}
                </div>
              </>
            ) : (
              <div className="actionbar">
                <button
                  className="primary"
                  disabled={splitBtn.disabled}
                  title={splitBtn.hint || undefined}
                  onClick={splitBtn.action === "analyze" ? runAutoSplit : splitBtn.action === "retry" ? retryAutoSplit : undefined}
                >
                  {splitBtn.label}
                </button>
                {splitBtn.hint && <span className="hint">{splitBtn.hint}</span>}
              </div>
            )}
          </div>

          {layout === "auto" && autoResult && (autoResult.status === "queued" || autoResult.status === "processing") && (
            <div className="card statuscard">
              <div>
                <div><strong>{autoResult.filename}</strong> <span className="muted">({autoResult.page_count} pages)</span></div>
                <div>{autoResult.status === "queued" ? "Queued for Claude…" : "Claude is analyzing pages…"}</div>
                <div className="hint">Runs in the background. You can leave this page and come back anytime.</div>
              </div>
              <button onClick={discardAutoSplit} disabled={busy}>Cancel</button>
            </div>
          )}

          {layout === "auto" && autoResult && autoResult.status === "failed" && (
            <div className="stack">
              <Alert tone="bad">Auto-split failed for {autoResult.filename}: {autoResult.error || "Unknown error"}</Alert>
              <div className="actionbar actionbar-plain">
                <button className="primary" disabled={busy} onClick={retryAutoSplit}>{busy ? "Requeuing…" : "Retry"}</button>
                <button disabled={busy} onClick={discardAutoSplit}>Discard</button>
              </div>
            </div>
          )}

          {layout === "auto" && autoResult && autoResult.status === "ready" && (
            <div className="stack">
              <div className="card proposal-bar">
                <div>
                  <div><strong>Proposed split:</strong> {splits.length} paper{splits.length === 1 ? "" : "s"} across {autoResult.page_count} pages</div>
                  <div className="hint">Verify the start/end page thumbnails and roll numbers below, then assign TAs.</div>
                </div>
                <div className="bulk">
                  <div className="field">
                    <label htmlFor="bulk-ta">Bulk assign all papers</label>
                    <select id="bulk-ta" value={bulkTa} onChange={(e) => setBulkTa(e.target.value)}>
                      <option value="">Unassigned (clear all)</option>
                      {tas.length > 0 && <option value="round_robin">Spread evenly (round-robin)</option>}
                      {tas.map((t) => <option key={t.id} value={String(t.id)}>{t.name ? `${t.name} — ${t.email}` : t.email}</option>)}
                    </select>
                  </div>
                  <button onClick={applyBulkTa}>Apply to all</button>
                  <button onClick={() => setSplits((all) => [
                    ...all,
                    {
                      student_external_id: "",
                      student_name: "",
                      first_page: Math.min(autoResult.page_count, (all[all.length - 1]?.last_page ?? 0) + 1),
                      last_page: autoResult.page_count,
                      confidence: null,
                      ta_ids: [],
                    },
                  ])}>+ Add paper</button>
                </div>
              </div>

              {splitErrors.length > 0 && (
                <Alert tone="bad">{splitErrors.join(" ")}</Alert>
              )}

              {autoResult.allowed_roll_numbers.length > 0 && (
                <datalist id="allowed-rolls">
                  {autoResult.allowed_roll_numbers.map((r) => <option key={r} value={r} label={autoResult.roll_names[r] || undefined} />)}
                </datalist>
              )}

              {splits.map((sp, idx) => {
                const alreadyExists = autoResult.existing_student_ids.includes(sp.student_external_id.trim());
                const notInAllowed =
                  autoResult.allowed_roll_numbers.length > 0 &&
                  sp.student_external_id.trim() !== "" &&
                  !autoResult.allowed_roll_numbers.includes(sp.student_external_id.trim());
                const pagesInRange =
                  sp.first_page >= 1 && sp.last_page >= sp.first_page && sp.last_page <= autoResult.page_count
                    ? Array.from({ length: sp.last_page - sp.first_page + 1 }, (_, i) => sp.first_page + i)
                    : [];
                const update = (patch: Partial<EditableSplit>) =>
                  setSplits((all) => all.map((x, k) => (k === idx ? { ...x, ...patch } : x)));
                return (
                  <div key={idx} className="card paper">
                    <div className="paper-head">
                      <div className="paper-title">
                        <strong>Paper {idx + 1}</strong>
                        <span className="hint">Pages {sp.first_page}–{sp.last_page}</span>
                        {sp.confidence !== null && <ConfidenceBadge value={sp.confidence} label="split" />}
                        {alreadyExists && <span className="badge warn">already imported (will skip)</span>}
                        {notInAllowed && <span className="badge warn">not in the roster</span>}
                      </div>
                      <button className="danger" onClick={() => setSplits((all) => all.filter((_, k) => k !== idx))}
                        disabled={splits.length <= 1}>Remove</button>
                    </div>

                    <div className="split-fields">
                      <div className="field">
                        <label htmlFor={`roll-${idx}`}>Roll number / Student ID</label>
                        <input id={`roll-${idx}`} type="text" list={autoResult.allowed_roll_numbers.length > 0 ? "allowed-rolls" : undefined}
                          value={sp.student_external_id} placeholder="e.g. 2024CS01"
                          onChange={(e) => {
                            const roll = e.target.value;
                            const fromRoster = autoResult.roll_names[roll.trim()];
                            const previous = autoResult.roll_names[sp.student_external_id.trim()];
                            // Fill the name from the roster, but never overwrite one the TA typed.
                            const fill = fromRoster && (!sp.student_name || sp.student_name === previous);
                            update(fill ? { student_external_id: roll, student_name: fromRoster } : { student_external_id: roll });
                          }} />
                      </div>
                      <div className="field">
                        <label htmlFor={`name-${idx}`}>Name (optional)</label>
                        <input id={`name-${idx}`} type="text" value={sp.student_name} placeholder="Student name"
                          onChange={(e) => update({ student_name: e.target.value })} />
                      </div>
                      <div className="field">
                        <label htmlFor={`first-${idx}`}>First page</label>
                        <input id={`first-${idx}`} type="number" min={1} max={autoResult.page_count} value={sp.first_page}
                          onChange={(e) => update({ first_page: Number(e.target.value) })} />
                      </div>
                      <div className="field">
                        <label htmlFor={`last-${idx}`}>Last page</label>
                        <input id={`last-${idx}`} type="number" min={1} max={autoResult.page_count} value={sp.last_page}
                          onChange={(e) => update({ last_page: Number(e.target.value) })} />
                      </div>
                      <div className="field">
                        <label>Assign TA(s)</label>
                        <TaMultiSelect tas={tas} selected={sp.ta_ids} onChange={(next) => update({ ta_ids: next })} />
                      </div>
                    </div>

                    {pagesInRange.length > 0 && (
                      <div className="pages">
                        <div className="hint">Visual check (click a page to zoom)</div>
                        <div className="pagestrip">
                          {pagesInRange.map((pNum, pIdx) => {
                            const isStart = pIdx === 0;
                            const isEnd = pIdx === pagesInRange.length - 1;
                            return (
                              <button key={pNum} type="button" className={`pagethumb${isStart ? " start" : ""}`}
                                onClick={() => setZoomPage(pNum)} title={`Click to inspect page ${pNum}`}>
                                <img
                                  src={`/api/exams/${examId}/imports/auto-split/${autoResult.split_token}/pages/${pNum}/thumb`}
                                  alt={`Page ${pNum}`}
                                  loading="lazy"
                                  decoding="async"
                                />
                                <span className="cap">p.{pNum}{isStart ? " · start" : isEnd ? " · end" : ""}</span>
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}

              <div className="actionbar actionbar-plain">
                <button className="primary" disabled={busy || splits.length === 0 || splitErrors.length > 0} onClick={commitAutoSplit}>
                  {busy ? "Importing…" : `Import ${splits.length} paper${splits.length === 1 ? "" : "s"}`}
                </button>
                <button onClick={discardAutoSplit}>Discard</button>
              </div>
            </div>
          )}

          {zoomPage !== null && autoResult && (
            <div className="zoom-backdrop" onClick={() => setZoomPage(null)}>
              <div className="card zoom-modal" onClick={(e) => e.stopPropagation()}>
                <div className="zoom-head">
                  <strong>Page {zoomPage} of {autoResult.page_count}</strong>
                  <div className="row">
                    <button disabled={zoomPage <= 1} onClick={() => setZoomPage((p) => (p && p > 1 ? p - 1 : p))}>← Prev</button>
                    <button disabled={zoomPage >= autoResult.page_count} onClick={() => setZoomPage((p) => (p && p < autoResult.page_count ? p + 1 : p))}>Next →</button>
                    <button onClick={() => setZoomPage(null)}>Close</button>
                  </div>
                </div>
                <img
                  src={`/api/exams/${examId}/imports/auto-split/${autoResult.split_token}/pages/${zoomPage}/image`}
                  alt={`Page ${zoomPage}`}
                />
              </div>
            </div>
          )}

          {result && (
            <div className="stack">
              <Alert tone={counts.error || counts.conflict ? "warn" : "ok"}>
                Preview: {counts.ok ?? 0} ready{counts.conflict ? `, ${counts.conflict} already imported (skipped)` : ""}{counts.error ? `, ${counts.error} with errors (skipped)` : ""}. Nothing is saved yet.
              </Alert>
              <ImportTable items={result.items} />
              <div className="actionbar actionbar-plain">
                <button className="primary" disabled={busy || !counts.ok} onClick={() => run(false)}>{busy ? "Importing…" : `Import ${counts.ok ?? 0} paper${counts.ok === 1 ? "" : "s"}`}</button>
                <span className="hint">The files upload again when you confirm.</span>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function ImportTable({ items }: { items: ImportItem[] }) {
  const [q, setQ] = useState("");
  const visible = items.filter((i) =>
    matchesQuery([i.student_external_id, i.filename, i.status, i.detail, ...i.ta_emails], q),
  );
  return (
    <div className="stack">
    <SearchBox value={q} onChange={setQ} label="Search import results" placeholder="Search student, file, TA, status, problem"
      shown={visible.length} total={items.length} />
    <div className="card" style={{ padding: 0, overflowX: "auto" }}>
      {visible.length === 0 ? <NoMatches query={q} onClear={() => setQ("")} /> : (
      <table>
        <thead><tr><th>Student</th><th>File</th><th>Pages</th><th>TAs</th><th>Result</th></tr></thead>
        <tbody>
          {visible.map((i, k) => (
            <tr key={k}>
              <td>{i.student_external_id || "?"}</td>
              <td className="small">{i.filename}</td>
              <td>{i.pages ?? "–"}</td>
              <td className="small">{i.ta_emails.join(", ") || <span className="muted">none</span>}</td>
              <td><span className={`badge ${TONE[i.status]}`}>{i.status}</span>{i.detail && <div className="small muted">{i.detail}</div>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      )}
    </div>
    </div>
  );
}
