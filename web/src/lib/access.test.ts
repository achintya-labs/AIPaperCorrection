import test from "node:test";
import assert from "node:assert/strict";
import { pageAllowed, pickTab, visibleTabs } from "./access.ts";

const TA = ["exams.view", "tab.overview", "overview.export"];
const ADMIN = ["exams.view", "tab.papers", "tab.rubric", "tab.upload", "tab.overview", "tab.advanced"];

test("a TA sees only the Overview tab; an admin sees all, with Overview first", () => {
  assert.deepEqual(visibleTabs(TA).map((t) => t.key), ["overview"]);
  assert.deepEqual(visibleTabs(ADMIN).map((t) => t.key), ["overview", "papers", "rubric", "import", "advanced"]);
  assert.deepEqual(visibleTabs(undefined), []);
});

test("the URL hash picks a tab only if it is allowed, otherwise the first allowed one", () => {
  assert.equal(pickTab("#papers", ADMIN), "papers");
  assert.equal(pickTab("#advanced", TA), "overview");
  assert.equal(pickTab("", TA), "overview");
  assert.equal(pickTab("", ADMIN), "overview");
  assert.equal(pickTab("#papers", []), null);
});

test("pages: TAs get the exams section but not the rest of /admin", () => {
  const ta = { role: "ta", permissions: TA };
  assert.equal(pageAllowed("/admin/exams", ta), true);
  assert.equal(pageAllowed("/admin/exams/7", ta), true);
  assert.equal(pageAllowed("/admin/users", ta), false);
  assert.equal(pageAllowed("/admin", ta), false);
  assert.equal(pageAllowed("/admin/examsx", ta), false);
  assert.equal(pageAllowed("/", ta), true);
  assert.equal(pageAllowed("/admin/users", { role: "admin", permissions: ADMIN }), true);
  assert.equal(pageAllowed("/admin/exams", { role: "ta" }), false);
});
