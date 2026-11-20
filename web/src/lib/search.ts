/** Lower-case and strip accents, so "jose" finds "José". */
export const normalize = (s: string): string =>
  s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

/**
 * Does a table row match what the user typed?
 *
 * The query is split on whitespace and EVERY term must appear (as a substring, ignoring case and accents)
 * somewhere in the row's searchable fields, so "ann bio" finds Ann in a paper for "Biology". An empty query
 * matches everything. Fields are joined with a separator so a term can't match across two fields.
 */
export function matchesQuery(
  fields: ReadonlyArray<string | number | null | undefined>,
  query: string,
): boolean {
  const terms = normalize(query).split(/\s+/).filter(Boolean);
  if (terms.length === 0) return true;
  const haystack = normalize(fields.filter((f) => f != null && f !== "").join("\n"));
  return terms.every((t) => haystack.includes(t));
}
