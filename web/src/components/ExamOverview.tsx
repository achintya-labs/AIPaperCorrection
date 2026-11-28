import { useEffect, useState } from "react";
import { api, apiBlob, errMsg } from "../lib/api";
import { saveBlob } from "../lib/uiHelpers";
import type { ExamOverview as Overview, OverviewBin } from "../lib/types";
import { Alert, DownloadIcon } from "./ui";

const GRADED_COLOR = "#6ea8fe";
const REVIEWED_COLOR = "#5ec27a";

const num = (n: number | null | undefined, digits = 2) =>
  n == null ? "–" : String(Math.round(n * 10 ** digits) / 10 ** digits);

const pct = (part: number, total: number) =>
  total > 0 ? Math.round((part / total) * 100) : 0;

/** Marks per bar. Blank = let the server choose from the data. Returns null for text that isn't a positive number. */
const parseWidth = (s: string) => {
  const n = Number(s);
  return s.trim() !== "" && Number.isFinite(n) && n > 0 ? n : null;
};

export default function ExamOverview({ examId, canExport }: { examId: number; canExport: boolean }) {
  const [data, setData] = useState<Overview | null>(null);
  const [width, setWidth] = useState(""); // blank = automatic
  const [error, setError] = useState("");
  const [view, setView] = useState<"plots" | "table">("plots");
  const asked = parseWidth(width);
  const invalid = width.trim() !== "" && asked === null;

  useEffect(() => {
    if (invalid) return;
    let alive = true;
    const t = setTimeout(() => {
      api<Overview>(`/exams/${examId}/overview${asked ? `?bin_width=${asked}` : ""}`)
        .then((o) => { if (alive) { setData(o); setError(""); } })
        .catch((e) => { if (alive) setError(errMsg(e)); });
    }, 250);
    return () => { alive = false; clearTimeout(t); };
  }, [examId, asked, invalid]);

  async function exportCsv() {
    try {
      saveBlob(
        `exam-${examId}-overview.csv`,
        await apiBlob(`/exams/${examId}/overview.csv${asked ? `?bin_width=${asked}` : ""}`),
      );
    } catch (e) { setError(errMsg(e)); }
  }

  if (!data) return error ? <Alert tone="bad">{error}</Alert> : <p className="muted">Loading…</p>;

  const total = data.total_papers;
  const ymax = Math.max(1, ...data.bins.flatMap((b) => [b.graded, b.reviewed]));
  const maxLabel = data.max_score != null ? `out of ${num(data.max_score)}` : undefined;

  return (
    <div className="stack">
      {error && <Alert tone="bad">{error}</Alert>}

      <div className="overview-kpis">
        <section className="card stat-card">
          <header className="stat-head">
            <span className="section-title">Papers</span>
            <span className="small muted">{total} total</span>
          </header>
          <div className="stat-pair">
            <div className="stat-item">
              <div className="stat-label"><span className="dot" style={{ background: GRADED_COLOR }} />Papers Graded</div>
              <div className="stat-value">{data.graded.count} <span className="stat-denom">/ {total}</span></div>
              <div className="stat-bar"><div style={{ width: `${pct(data.graded.count, total)}%`, background: GRADED_COLOR }} /></div>
            </div>
            <div className="stat-item">
              <div className="stat-label"><span className="dot" style={{ background: REVIEWED_COLOR }} />Papers Reviewed</div>
              <div className="stat-value">{data.reviewed.count} <span className="stat-denom">/ {total}</span></div>
              <div className="stat-bar"><div style={{ width: `${pct(data.reviewed.count, total)}%`, background: REVIEWED_COLOR }} /></div>
            </div>
          </div>
        </section>

        <section className="card stat-card">
          <header className="stat-head">
            <span className="section-title">Mean</span>
            {maxLabel && <span className="small muted">{maxLabel}</span>}
          </header>
          <div className="stat-pair">
            <div className="stat-item">
              <div className="stat-label"><span className="dot" style={{ background: GRADED_COLOR }} />Mean (Graded)</div>
              <div className="stat-value">{num(data.graded.mean)}</div>
            </div>
            <div className="stat-item">
              <div className="stat-label"><span className="dot" style={{ background: REVIEWED_COLOR }} />Mean (Reviewed)</div>
              <div className="stat-value">{num(data.reviewed.mean)}</div>
            </div>
          </div>
        </section>

        <section className="card stat-card">
          <header className="stat-head">
            <span className="section-title">Standard deviation</span>
            <span className="small muted">population</span>
          </header>
          <div className="stat-pair">
            <div className="stat-item">
              <div className="stat-label"><span className="dot" style={{ background: GRADED_COLOR }} />Std dev (Graded)</div>
              <div className="stat-value">{num(data.graded.std_dev)}</div>
            </div>
            <div className="stat-item">
              <div className="stat-label"><span className="dot" style={{ background: REVIEWED_COLOR }} />Std dev (Reviewed)</div>
              <div className="stat-value">{num(data.reviewed.std_dev)}</div>
            </div>
          </div>
        </section>
      </div>

      <div className="card overview-toolbar">
        <div className="row" style={{ gap: ".75rem" }}>
          <div className="bin-control">
            <label htmlFor="bin-width">Bin width (marks)</label>
            <div className="row" style={{ gap: ".4rem" }}>
              <input
                id="bin-width"
                type="number"
                min="0"
                step="any"
                inputMode="decimal"
                value={width}
                placeholder={`Auto (${num(data.auto_bin_width)})`}
                aria-invalid={invalid}
                onChange={(e) => setWidth(e.target.value)}
              />
              {width !== "" && (
                <button type="button" onClick={() => setWidth("")}>Auto</button>
              )}
            </div>
          </div>
          <span className={`small ${invalid ? "bad-text" : "muted"}`} style={{ alignSelf: "flex-end", paddingBottom: ".45rem" }}>
            {invalid ? "Enter a positive number." : `${data.bins.length} bins · ${num(data.bin_width)} marks wide`}
          </span>
        </div>

        <div className="row" style={{ gap: ".5rem" }}>
          <div className="seg" role="group" aria-label="Distribution view">
            <button type="button" aria-pressed={view === "plots"} onClick={() => setView("plots")}>Plots</button>
            <button type="button" aria-pressed={view === "table"} onClick={() => setView("table")}>Table</button>
          </div>
          {canExport && (
            <button type="button" className="btn-icon-text" onClick={exportCsv} title="Download statistics and bin counts as CSV">
              <DownloadIcon />
              <span>Export CSV</span>
            </button>
          )}
        </div>
      </div>

      {view === "table" ? (
        <div className="card table-card">
          <table>
            <thead><tr><th>Marks range</th><th>Graded</th><th>Reviewed</th></tr></thead>
            <tbody>
              {data.bins.map((b) => (
                <tr key={b.start}>
                  <td className="mono small">{num(b.start)} – {num(b.end)}</td>
                  <td>{b.graded}</td>
                  <td>{b.reviewed}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="overview-plots">
          <Histogram title="Graded" count={data.graded.count} field="graded" color={GRADED_COLOR} bins={data.bins} ymax={ymax} />
          <Histogram title="Reviewed" count={data.reviewed.count} field="reviewed" color={REVIEWED_COLOR} bins={data.bins} ymax={ymax} />
        </div>
      )}

      <p className="small muted" style={{ margin: 0 }}>
        <strong>Graded</strong> uses AI scores for all graded papers; <strong>Reviewed</strong> uses final scores for completed reviews.
      </p>
    </div>
  );
}

/** Number of papers (y) against marks (x). Both plots share the y scale so they can be compared directly. */
function Histogram({
  title,
  count,
  field,
  color,
  bins,
  ymax,
}: {
  title: string;
  count: number;
  field: "graded" | "reviewed";
  color: string;
  bins: OverviewBin[];
  ymax: number;
}) {
  const W = 560, H = 230, L = 38, R = 10, T = 14, B = 38;
  const iw = W - L - R, ih = H - T - B;
  const bw = bins.length > 0 ? iw / bins.length : iw;
  const step = ymax <= 5 ? 1 : Math.ceil(ymax / 5);
  const ticks = Array.from({ length: Math.floor(ymax / step) + 1 }, (_, i) => i * step);
  const every = Math.max(1, Math.ceil(bins.length / 8));
  const [hover, setHover] = useState<number | null>(null);
  const hb = hover == null ? null : bins[hover];

  return (
    <section className="card plot-card">
      <header className="plot-head">
        <div className="row" style={{ gap: ".45rem" }}>
          <span className="dot" style={{ background: color }} />
          <strong>{title}</strong>
        </div>
        <span className="small muted">
          {hb
            ? `${num(hb.start)}–${num(hb.end)} marks: ${hb[field]} paper${hb[field] === 1 ? "" : "s"}`
            : `${count} paper${count === 1 ? "" : "s"}`}
        </span>
      </header>

      {count === 0 ? (
        <div className="plot-empty muted small">No {title.toLowerCase()} papers yet.</div>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} className="plot-svg" role="img" aria-label={`${title}: number of papers by marks`}>
          {ticks.map((t) => {
            const yy = T + ih - (t / ymax) * ih;
            return (
              <g key={t}>
                <line x1={L} x2={W - R} y1={yy} y2={yy} stroke="var(--border)" strokeDasharray={t === 0 ? undefined : "3 3"} strokeWidth="1" />
                <text x={L - 8} y={yy + 4} textAnchor="end" fontSize="11" fill="var(--muted)">{t}</text>
              </g>
            );
          })}
          {bins.map((b, i) => {
            const v = b[field];
            const h = (v / ymax) * ih;
            const x = L + i * bw;
            const pad = Math.min(3, bw * 0.12);
            const active = hover === i;
            return (
              <g key={b.start} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                <rect x={x} y={T} width={bw} height={ih} fill={active ? "rgba(255,255,255,0.03)" : "transparent"} />
                {v > 0 && (
                  <rect
                    x={x + pad}
                    y={T + ih - h}
                    width={Math.max(bw - pad * 2, 2)}
                    height={h}
                    rx={Math.min(4, Math.max((bw - pad * 2) / 3, 1))}
                    fill={color}
                    opacity={hover == null || active ? 0.92 : 0.45}
                  />
                )}
              </g>
            );
          })}
          <line x1={L} x2={W - R} y1={T + ih} y2={T + ih} stroke="var(--muted)" strokeWidth="1" />
          {bins.map((b, i) => (
            i % every === 0
              ? <text key={b.start} x={L + i * bw} y={H - 18} textAnchor="middle" fontSize="11" fill="var(--muted)">{num(b.start)}</text>
              : null
          ))}
          <text x={L + iw / 2} y={H - 2} textAnchor="middle" fontSize="11" fill="var(--muted)">Marks</text>
          <text transform={`translate(11 ${T + ih / 2}) rotate(-90)`} textAnchor="middle" fontSize="11" fill="var(--muted)">Papers</text>
        </svg>
      )}
    </section>
  );
}
