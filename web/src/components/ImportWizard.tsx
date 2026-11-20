import { useState } from "react";
import { api, errMsg } from "../lib/api";
import type { ImportItem, ImportResult } from "../lib/types";
import { Alert } from "./ui";
import { matchesQuery } from "../lib/search";
import SearchBox, { NoMatches } from "./SearchBox";

type Layout = "files" | "combined";
type Assign = "" | "csv" | "round_robin" | "none";

const TEMPLATES: Record<Layout, string> = {
  files: "student_id,name,ta_emails\n1001,Ann Lee,ta1@school.edu;ta2@school.edu\n1002,Bo Chen,ta1@school.edu\n",
  combined: "student_id,name,first_page,last_page,ta_emails\n1001,Ann Lee,2,5,ta1@school.edu\n1002,Bo Chen,6,9,ta2@school.edu\n",
};
const TONE = { ok: "ok", conflict: "warn", error: "bad" } as const;

export default function ImportWizard({ examId }: { examId: number }) {
  const [layout, setLayout] = useState<Layout>("files");
  const [pdfs, setPdfs] = useState<File[]>([]);
  const [csv, setCsv] = useState<File | null>(null);
  const [assign, setAssign] = useState<Assign>("");
  const [reviewers, setReviewers] = useState(1);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const ready = pdfs.length > 0 && (layout === "files" || csv !== null) && (layout === "files" || pdfs.length === 1);

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

  function downloadTemplate() {
    const url = URL.createObjectURL(new Blob([TEMPLATES[layout]], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url; a.download = `import-${layout}.csv`; a.click();
    URL.revokeObjectURL(url);
  }

  const counts = (result?.items ?? []).reduce((c, i: ImportItem) => ({ ...c, [i.status]: (c[i.status] ?? 0) + 1 }), {} as Record<string, number>);
  const reset = () => { setResult(null); setPdfs([]); setCsv(null); };

  return (
    <div className="stack">
      <h2>Import answer sheets</h2>
      {error && <Alert tone="bad">{error}</Alert>}

      {result && !result.dry_run ? (
        <div className="stack">
          <Alert tone={result.created ? "ok" : "warn"}>Imported {result.created} paper{result.created === 1 ? "" : "s"}. Papers go to grading as soon as the exam has a rubric — use “Queue waiting” on the Papers tab if you imported before writing one.</Alert>
          <ImportTable items={result.items} />
          <button onClick={reset}>Import more</button>
        </div>
      ) : (
        <>
          <div className="card stack">
            <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
              <legend className="small muted">How are the scans organised?</legend>
              <div className="row">
                <label className="check"><input type="radio" checked={layout === "files"} onChange={() => { setLayout("files"); setResult(null); }} /> One PDF per student, named <span className="mono">&lt;student_id&gt;.pdf</span></label>
                <label className="check"><input type="radio" checked={layout === "combined"} onChange={() => { setLayout("combined"); setResult(null); }} /> One combined PDF, page ranges in the CSV</label>
              </div>
            </fieldset>

            <div className="grid2">
              <div className="field">
                <label htmlFor="pdfs">{layout === "files" ? "PDF files" : "Combined PDF"}</label>
                <input id="pdfs" type="file" accept="application/pdf" multiple={layout === "files"}
                  onChange={(e) => { setPdfs(Array.from(e.target.files ?? [])); setResult(null); }} />
                {pdfs.length > 0 && <span className="small muted">{pdfs.length} file{pdfs.length === 1 ? "" : "s"} selected</span>}
              </div>
              <div className="field">
                <label htmlFor="csv">Mapping CSV {layout === "files" ? "(optional)" : "(required)"}</label>
                <input id="csv" type="file" accept=".csv,text/csv" onChange={(e) => { setCsv(e.target.files?.[0] ?? null); setResult(null); }} />
                <button className="link small" onClick={downloadTemplate}>Download a template</button>
              </div>
            </div>

            <div className="grid2">
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
            <p className="small muted" style={{ margin: 0 }}>
              CSV columns: <span className="mono">student_id</span>, <span className="mono">name</span>, <span className="mono">ta_emails</span> (separate several with <span className="mono">;</span>)
              {layout === "combined" && <>, and <span className="mono">first_page</span>/<span className="mono">last_page</span> (1-based, inclusive; pages outside every range are skipped)</>}.
            </p>
            <div className="row">
              <button className="primary" disabled={!ready || busy} onClick={() => run(true)}>{busy ? "Checking…" : "Preview"}</button>
              {!ready && <span className="small muted">{layout === "combined" ? "Choose one PDF and a CSV." : "Choose at least one PDF."}</span>}
            </div>
          </div>

          {result && (
            <div className="stack">
              <div className="row">
                <Alert tone={counts.error || counts.conflict ? "warn" : "ok"}>
                  Preview: {counts.ok ?? 0} ready{counts.conflict ? `, ${counts.conflict} already imported (skipped)` : ""}{counts.error ? `, ${counts.error} with errors (skipped)` : ""}. Nothing is saved yet.
                </Alert>
              </div>
              <ImportTable items={result.items} />
              <div className="row">
                <button className="primary" disabled={busy || !counts.ok} onClick={() => run(false)}>{busy ? "Importing…" : `Import ${counts.ok ?? 0} paper${counts.ok === 1 ? "" : "s"}`}</button>
                <span className="small muted">The files upload again when you confirm.</span>
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
