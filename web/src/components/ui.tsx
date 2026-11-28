import type { ReactNode } from "react";
import type { IconDefinition } from "@fortawesome/free-solid-svg-icons";
import {
  faClipboardCheck,
  faFileArrowDown,
  faMagnifyingGlass,
  faPen,
  faPlay,
  faRotateRight,
  faTrash,
} from "@fortawesome/free-solid-svg-icons";
import type { GradeStatus, SubmissionStatus } from "../lib/types";

/** Confidence is shown as text as well as colour so it never relies on colour alone. */
export function ConfidenceBadge({ value, label }: { value: number | null | undefined; label?: string }) {
  if (value == null) return <span className="badge neutral">{label ? `${label} ` : ""}n/a</span>;
  const tone = value < 0.35 ? "bad" : value < 0.5 ? "warn-dark" : value <= 0.75 ? "ok-light" : "ok";
  return (
    <span className={`badge ${tone}`} title="Claude's own estimate; use it to decide where to look first">
      {label ? `${label} ` : ""}{Math.round(value * 100)}%
    </span>
  );
}

const STATUS_TONE: Record<SubmissionStatus | GradeStatus, string> = {
  uploaded: "neutral", queued: "neutral", grading: "", failed: "bad",
  ready_for_review: "warn", reviewed: "ok", pending: "warn", overridden: "",
};
const STATUS_LABEL: Partial<Record<SubmissionStatus | GradeStatus, string>> = {
  ready_for_review: "review",
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

function FaIcon({ icon }: { icon: IconDefinition }) {
  const [w, h, , , d] = icon.icon;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} fill="currentColor" aria-hidden="true">
      {Array.isArray(d) ? d.map((p, i) => <path key={i} d={p} />) : <path d={d} />}
    </svg>
  );
}

export function ReviewIcon() { return <FaIcon icon={faClipboardCheck} />; }
export function DownloadIcon() { return <FaIcon icon={faFileArrowDown} />; }
export function PlayIcon() { return <FaIcon icon={faPlay} />; }
export function RegradeIcon() { return <FaIcon icon={faRotateRight} />; }
export function TrashIcon() { return <FaIcon icon={faTrash} />; }
export function EditIcon() { return <FaIcon icon={faPen} />; }
export function SearchIcon() { return <FaIcon icon={faMagnifyingGlass} />; }
