import type { AutoSplitResult } from "./types";

export function isSplitInFlight(autoResult: AutoSplitResult | null): boolean {
  return autoResult !== null && (autoResult.status === "queued" || autoResult.status === "processing");
}

export type AutoSplitAction = "analyze" | "retry" | null;

export interface AutoSplitButton {
  /** What a click does; null while the button is greyed out. */
  action: AutoSplitAction;
  disabled: boolean;
  label: string;
  /** Why it is greyed out / what to do next (empty when enabled). */
  hint: string;
}

/**
 * Single source of truth for the "Analyze & propose split" button.
 *
 * - enabled only when exactly one PDF is selected and nothing is running or awaiting a decision
 * - a failed job can be retried without re-selecting the file; selecting a new file starts a fresh analysis
 * - while a proposal is ready the button is greyed out: import or discard it first, otherwise the
 *   TA's edits would be thrown away and the old job orphaned
 */
export function autoSplitButton(opts: {
  pdfCount: number;
  busy: boolean;
  autoResult: AutoSplitResult | null;
}): AutoSplitButton {
  const { pdfCount, busy, autoResult } = opts;
  const grey = (label: string, hint: string): AutoSplitButton => ({ action: null, disabled: true, label, hint });

  if (busy) return grey("Working…", "");
  if (isSplitInFlight(autoResult)) return grey("Analyzing in background…", "Claude is working; you can leave and come back.");
  if (autoResult?.status === "ready") {
    return grey("Analyze & propose split", "A proposal is ready below. Import or discard it before analyzing another PDF.");
  }
  if (pdfCount > 1) return grey("Analyze & propose split", "Select exactly one combined PDF.");
  if (pdfCount === 1) return { action: "analyze", disabled: false, label: "Analyze & propose split", hint: "" };
  if (autoResult?.status === "failed") {
    return { action: "retry", disabled: false, label: "Retry split analysis", hint: "" };
  }
  return grey("Analyze & propose split", "Choose one combined PDF.");
}
