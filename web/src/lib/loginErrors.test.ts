import assert from "node:assert/strict";
import test from "node:test";
import { loginErrorMessage } from "./loginErrors.ts";

test("known codes map to messages, unknown or missing to nothing", () => {
  assert.match(loginErrorMessage("google_not_allowed"), /no access/);
  assert.notEqual(loginErrorMessage("google_failed"), "");
  assert.equal(loginErrorMessage("<script>"), "");
  assert.equal(loginErrorMessage(null), "");
});
