import assert from "node:assert/strict";
import { test } from "node:test";
import { csrfBlocked, firstValue, hostAllowed, hostnameOf, parseAllowedHosts, type CsrfInput } from "./csrf.ts";

const ALLOWED = parseAllowedHosts("grader.school.edu,*.labs.school.edu,localhost");
const req = (o: Partial<CsrfInput>): CsrfInput => ({
  method: "POST", origin: "https://grader.school.edu", host: "grader.school.edu",
  contentType: "application/json", allowedHosts: ALLOWED, ...o,
});

test("parseAllowedHosts: defaults to local development, trims and lower-cases", () => {
  assert.deepEqual(parseAllowedHosts(undefined), ["localhost", "127.0.0.1"]);
  assert.deepEqual(parseAllowedHosts(""), ["localhost", "127.0.0.1"]);
  assert.deepEqual(parseAllowedHosts("   "), ["localhost", "127.0.0.1"]);
  assert.deepEqual(parseAllowedHosts(" A.Example.com , ,*.B.org "), ["a.example.com", "*.b.org"]);
});

test("hostnameOf strips the port and handles IPv6", () => {
  assert.equal(hostnameOf("Grader.School.edu:4321"), "grader.school.edu");
  assert.equal(hostnameOf("localhost"), "localhost");
  assert.equal(hostnameOf("[::1]:4321"), "::1");
});

test("hostAllowed: exact names and *.subdomain wildcards, never the bare domain for a wildcard", () => {
  assert.equal(hostAllowed("grader.school.edu:443", ALLOWED), true);
  assert.equal(hostAllowed("a.labs.school.edu", ALLOWED), true);
  assert.equal(hostAllowed("x.y.labs.school.edu", ALLOWED), true);
  assert.equal(hostAllowed("labs.school.edu", ALLOWED), false);
  assert.equal(hostAllowed("evil-labs.school.edu", ALLOWED), false);
  assert.equal(hostAllowed("grader.school.edu.evil.com", ALLOWED), false);
  assert.equal(hostAllowed("other.edu", ALLOWED), false);
  assert.equal(hostAllowed("", ALLOWED), false);
});

test("safe methods are never blocked", () => {
  for (const method of ["GET", "HEAD", "OPTIONS", "get"]) {
    assert.equal(csrfBlocked(req({ method, origin: "https://evil.com", contentType: null })), false, method);
  }
});

test("same-origin writes are allowed for every content type", () => {
  for (const contentType of ["application/json", "multipart/form-data; boundary=x", "text/plain", null]) {
    assert.equal(csrfBlocked(req({ contentType })), false, String(contentType));
  }
});

test("cross-site form-like writes are blocked", () => {
  for (const contentType of ["multipart/form-data; boundary=x", "application/x-www-form-urlencoded", "text/plain;charset=UTF-8", "Multipart/Form-Data"]) {
    assert.equal(csrfBlocked(req({ origin: "https://evil.com", contentType })), true, contentType);
  }
});

test("a write with no content type is blocked unless same-origin (matches Astro)", () => {
  assert.equal(csrfBlocked(req({ origin: "https://evil.com", contentType: null })), true);
  assert.equal(csrfBlocked(req({ origin: null, contentType: null })), true);
});

test("JSON without an Origin is allowed (scripts, curl); a cross-site JSON write needs a preflight we never approve", () => {
  assert.equal(csrfBlocked(req({ origin: null })), false);
  assert.equal(csrfBlocked(req({ origin: "https://evil.com" })), false);
});

test("a form write without Origin is blocked", () => {
  assert.equal(csrfBlocked(req({ origin: null, contentType: "multipart/form-data; boundary=x" })), true);
});

test("the Origin must match the host the server was addressed with, port included", () => {
  assert.equal(csrfBlocked(req({ origin: "https://grader.school.edu:8443", host: "grader.school.edu", contentType: "multipart/form-data" })), true);
  assert.equal(csrfBlocked(req({ origin: "https://grader.school.edu:8443", host: "grader.school.edu:8443", contentType: "multipart/form-data" })), false);
  assert.equal(csrfBlocked(req({ origin: "https://a.labs.school.edu", host: "b.labs.school.edu", contentType: "multipart/form-data" })), true);
});

test("a host that is not in ALLOWED_HOSTS is refused even when Origin equals Host", () => {
  const r = req({ origin: "https://lan-box", host: "lan-box", contentType: "multipart/form-data" });
  assert.equal(csrfBlocked(r), true);
  assert.equal(csrfBlocked({ ...r, allowedHosts: parseAllowedHosts("lan-box") }), false); // ...until it is listed
});

test("malformed or opaque origins are blocked for forms", () => {
  for (const origin of ["null", "not a url", "://", ""]) {
    assert.equal(csrfBlocked(req({ origin, contentType: "multipart/form-data" })), true, origin);
  }
});

test("scheme differences don't matter (TLS is usually terminated in front of this server)", () => {
  assert.equal(csrfBlocked(req({ origin: "http://grader.school.edu", contentType: "multipart/form-data" })), false);
});

test("firstValue picks the first of a forwarding chain", () => {
  assert.equal(firstValue("a.example.com, proxy.internal"), "a.example.com");
  assert.equal(firstValue(null), null);
  assert.equal(firstValue(""), null);
});
