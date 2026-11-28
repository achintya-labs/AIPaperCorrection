import test from "node:test";
import assert from "node:assert/strict";
import { logoutUrlFor } from "./logout.ts";

const URL1 = "https://auth.lan.internal:5904/logout";

test("the primary domain keeps LOGOUT_URL", () => {
  assert.equal(logoutUrlFor("lan.internal:5904", URL1, "other.org"), URL1);
  assert.equal(logoutUrlFor("lan.internal", URL1, ""), URL1);
  assert.equal(logoutUrlFor("lan.internal", URL1, undefined), URL1);
});

test("an extra domain logs out at its own portal, keeping port and path", () => {
  assert.equal(logoutUrlFor("other.org:5904", URL1, "other.org,third.com"), "https://auth.other.org:5904/logout");
  assert.equal(logoutUrlFor("THIRD.com", URL1, "other.org, third.com"), "https://auth.third.com:5904/logout");
});

test("an extra domain's own port wins; 443 is left out", () => {
  assert.equal(logoutUrlFor("other.org:8443", URL1, "other.org:8443"), "https://auth.other.org:8443/logout");
  assert.equal(logoutUrlFor("other.org", URL1, "other.org:443,x.com"), "https://auth.other.org/logout");
  assert.equal(logoutUrlFor("x.com", "https://auth.lan.internal/logout", "x.com:8443"), "https://auth.x.com:8443/logout");
});

test("an unknown host, no host or no LOGOUT_URL changes nothing", () => {
  assert.equal(logoutUrlFor("evil.example", URL1, "other.org"), URL1);
  assert.equal(logoutUrlFor(null, URL1, "other.org"), URL1);
  assert.equal(logoutUrlFor("other.org", "", "other.org"), "");
});
