import type { ReactNode } from "react";
import type { GradeStatus, SubmissionStatus } from "../lib/types";

/** Confidence is shown as text as well as colour so it never relies on colour alone. */
export function ConfidenceBadge({ value, label }: { value: number | null | undefined; label?: string }) {
  if (value == null) return <span className="badge neutral">{label ? `${label} ` : ""}n/a</span>;
  const tone = value < 0.6 ? "bad" : value < 0.8 ? "warn" : "ok";
  const word = value < 0.6 ? "low" : value < 0.8 ? "medium" : "high";
  return (
    <span className={`badge ${tone}`} title="Claude's own estimate; use it to decide where to look first">
      {label ? `${label} ` : ""}{Math.round(value * 100)}% · {word}
    </span>
  );
}

const STATUS_TONE: Record<SubmissionStatus | GradeStatus, string> = {
  uploaded: "neutral", queued: "neutral", grading: "", failed: "bad",
  ready_for_review: "warn", reviewed: "ok", pending: "warn", overridden: "",
};
const STATUS_LABEL: Partial<Record<SubmissionStatus | GradeStatus, string>> = {
  ready_for_review: "ready for review",
};
export function StatusBadge({ status }: { status: SubmissionStatus | GradeStatus }) {
  return <span className={`badge ${STATUS_TONE[status]}`}>{STATUS_LABEL[status] ?? status}</span>;
}

export function Alert({ tone = "", children }: { tone?: "" | "bad" | "warn" | "ok"; children: ReactNode }) {
  return <div className={`alert ${tone}`} role={tone === "bad" ? "alert" : "status"}>{children}</div>;
}

export const fmt = (n: number | null | undefined) => (n == null ? "–" : String(Math.round(n * 100) / 100));

/** A person: name on top (falls back to the email), email below and subdued. Never lets the two blur together. */
export function Person({ user }: { user: { name?: string | null; email: string } | undefined }) {
  if (!user) return null;
  const name = user.name?.trim();
  return (
    <span className="person">
      <span className="person-name">{name || user.email}</span>
      {name && <span className="person-email">{user.email}</span>}
    </span>
  );
}
