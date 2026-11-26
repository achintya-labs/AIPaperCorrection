import { useEffect, useState } from "react";
import { api, errMsg } from "../lib/api";
import type { Usage, UsageWindow } from "../lib/types";
import { Alert } from "./ui";

/** "in 2h 14m" / "in 3d 4h" for a future ISO time; "now" once it has passed. */
export function untilText(iso: string | null, now = Date.now()): string {
  const t = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(t)) return "";
  const mins = Math.round((t - now) / 60000);
  if (mins <= 0) return "now";
  const d = Math.floor(mins / 1440), h = Math.floor((mins % 1440) / 60), m = mins % 60;
  return d > 0 ? `in ${d}d ${h}h` : h > 0 ? `in ${h}h ${m}m` : `in ${m}m`;
}

const PLACEHOLDERS: UsageWindow[] = [
  { key: "five_hour", label: "Session (5-hour)", used_percent: null, resets_at: null, status: null, detail: null },
  { key: "seven_day", label: "Weekly (7-day)", used_percent: null, resets_at: null, status: null, detail: null },
];

const tone = (pct: number | null, status: string | null) =>
  status === "rejected" || (pct ?? 0) >= 90 ? "bad" : status === "allowed_warning" || (pct ?? 0) >= 70 ? "warn" : "ok";

function Meter({ w }: { w: UsageWindow }) {
  const pct = w.used_percent;
  const shown = pct == null ? null : Math.min(100, pct);
  const reset = untilText(w.resets_at);
  return (
    <div className="meter-row">
      <div className="row between" style={{ gap: ".5rem" }}>
        <strong>{w.label}</strong>
        <span className="small muted">
          {pct == null ? (w.status ?? "–") : `${Math.round(pct)}% used`}
          {w.detail && ` · ${w.detail}`}
          {reset && ` · resets ${reset}`}
        </span>
      </div>
      <div className="meter" role="progressbar" aria-label={w.label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={shown ?? undefined}
        title={w.resets_at ? `Resets ${new Date(w.resets_at).toLocaleString()}` : undefined}>
        <div className={`meter-fill ${tone(pct, w.status)}`} style={{ width: `${shown ?? 0}%` }} />
      </div>
    </div>
  );
}

export default function ModelUsage() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // Plan quota only exists for a Claude login. With an API key the whole section is hidden (until we know, show nothing).
  const [planQuota, setPlanQuota] = useState<boolean | null>(null);
  useEffect(() => {
    let alive = true;
    api<{ plan_quota?: boolean }>("/grader/status")
      .then((s) => { if (alive) setPlanQuota(s.plan_quota !== false); })
      .catch(() => { if (alive) setPlanQuota(true); });
    return () => { alive = false; };
  }, []);

  async function check() {
    setBusy(true); setError("");
    try { setUsage(await api<Usage>("/grader/usage", { method: "POST" })); }
    catch (e) { setError(errMsg(e)); }
    finally { setBusy(false); }
  }

  if (planQuota !== true) return null;

  return (
    <div className="stack">
      <h2 style={{ margin: 0 }}>Models</h2>
      <div className="card stack">
        <div className="row between">
          <h3 style={{ margin: 0 }}>Claude quota</h3>
          <button type="button" className="primary" onClick={check} disabled={busy}>{busy ? "Checking…" : usage ? "Check again" : "Check quota"}</button>
        </div>
        {error && <Alert tone="bad">{error}</Alert>}
        {/* Always the same size: greyed-out placeholders until a check has run, then the real numbers. */}
        <div className={`meters ${usage ? "" : "inactive"}`} aria-disabled={!usage}>
          {(usage?.windows ?? PLACEHOLDERS).map((w) => <Meter key={w.key} w={w} />)}
          <div className="small muted">
            {usage
              ? `${usage.source === "subscription" ? "Claude plan limits" : "API key rate limits (per minute)"} · checked ${new Date(usage.checked_at).toLocaleTimeString()} via ${usage.model}`
              : "Not checked yet"}
          </div>
        </div>
      </div>
    </div>
  );
}
