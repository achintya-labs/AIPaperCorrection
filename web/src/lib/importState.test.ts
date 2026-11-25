import assert from "node:assert/strict";
import test from "node:test";
import { autoSplitButton, isSplitInFlight } from "./importState.ts";
import type { AutoSplitResult } from "./types.ts";

const makeJob = (status: AutoSplitResult["status"]): AutoSplitResult => ({
  split_token: "a".repeat(32),
  filename: "scan.pdf",
  page_count: 4,
  status,
  error: status === "failed" ? "boom" : null,
  allowed_roll_numbers: [],
  roll_names: {},
  splits: [],
  existing_student_ids: [],
});

test("isSplitInFlight is only true for queued or processing", () => {
  assert.equal(isSplitInFlight(null), false);
  assert.equal(isSplitInFlight(makeJob("queued")), true);
  assert.equal(isSplitInFlight(makeJob("processing")), true);
  assert.equal(isSplitInFlight(makeJob("ready")), false);
  assert.equal(isSplitInFlight(makeJob("failed")), false);
});

test("greyed out with no file, with several files, while busy, in flight, or when a proposal is ready", () => {
  assert.equal(autoSplitButton({ pdfCount: 0, busy: false, autoResult: null }).disabled, true);
  assert.equal(autoSplitButton({ pdfCount: 2, busy: false, autoResult: null }).disabled, true);
  assert.equal(autoSplitButton({ pdfCount: 1, busy: true, autoResult: null }).disabled, true);
  assert.equal(autoSplitButton({ pdfCount: 1, busy: false, autoResult: makeJob("queued") }).disabled, true);
  assert.equal(autoSplitButton({ pdfCount: 1, busy: false, autoResult: makeJob("processing") }).disabled, true);
  assert.equal(autoSplitButton({ pdfCount: 1, busy: false, autoResult: makeJob("ready") }).disabled, true);
  assert.equal(autoSplitButton({ pdfCount: 0, busy: false, autoResult: makeJob("ready") }).action, null);
});

test("every greyed-out state explains itself except busy", () => {
  for (const [pdfCount, autoResult] of [[0, null], [2, null], [1, makeJob("ready")], [1, makeJob("queued")]] as const) {
    assert.notEqual(autoSplitButton({ pdfCount, busy: false, autoResult }).hint, "");
  }
});

test("enabled for exactly one file when idle or after a failure", () => {
  assert.deepEqual(
    (({ action, disabled }) => ({ action, disabled }))(autoSplitButton({ pdfCount: 1, busy: false, autoResult: null })),
    { action: "analyze", disabled: false },
  );
  assert.equal(autoSplitButton({ pdfCount: 1, busy: false, autoResult: makeJob("failed") }).action, "analyze");
});

test("a failed job can be retried without re-selecting the file", () => {
  const b = autoSplitButton({ pdfCount: 0, busy: false, autoResult: makeJob("failed") });
  assert.equal(b.action, "retry");
  assert.equal(b.disabled, false);
  assert.equal(b.label, "Retry split analysis");
});
