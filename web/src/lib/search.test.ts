import assert from "node:assert/strict";
import { test } from "node:test";
import { matchesQuery, normalize } from "./search.ts";

const row = ["1001", "Ann Lee", "Biology midterm", "ready for review", null, undefined, 7];

test("empty or blank query matches everything", () => {
  assert.equal(matchesQuery(row, ""), true);
  assert.equal(matchesQuery(row, "   \t "), true);
  assert.equal(matchesQuery([], ""), true);
});

test("matches substrings in any field, ignoring case", () => {
  assert.equal(matchesQuery(row, "ann"), true);
  assert.equal(matchesQuery(row, "BIO"), true);
  assert.equal(matchesQuery(row, "100"), true);
  assert.equal(matchesQuery(row, "review"), true);
  assert.equal(matchesQuery(row, "7"), true); // numbers are searchable
  assert.equal(matchesQuery(row, "chemistry"), false);
});

test("every term must match (AND), in any order, across fields", () => {
  assert.equal(matchesQuery(row, "ann biology"), true);
  assert.equal(matchesQuery(row, "biology ann"), true);
  assert.equal(matchesQuery(row, "ann chemistry"), false);
  assert.equal(matchesQuery(row, "  ann   lee "), true); // extra whitespace is ignored
});

test("ignores accents in both the query and the data", () => {
  assert.equal(matchesQuery(["José Álvarez"], "jose alvarez"), true);
  assert.equal(matchesQuery(["Jose Alvarez"], "José"), true);
  assert.equal(normalize("Crème Brûlée"), "creme brulee");
});

test("a term cannot match across the boundary between two fields", () => {
  assert.equal(matchesQuery(["ab", "cd"], "abcd"), false);
  assert.equal(matchesQuery(["ab", "cd"], "ab cd"), true);
});

test("null, undefined and empty fields are skipped, not searched as the word 'null'", () => {
  assert.equal(matchesQuery([null, undefined, ""], "null"), false);
  assert.equal(matchesQuery([null, "x"], "undefined"), false);
});

test("regex characters in the query are treated literally", () => {
  assert.equal(matchesQuery(["a.b (c)"], "a.b"), true);
  assert.equal(matchesQuery(["axb"], "a.b"), false);
  assert.equal(matchesQuery(["x"], "(["), false); // would throw if used as a regex
});
