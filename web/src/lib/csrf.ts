/**
 * Cross-site request protection for the web service (and, through its /api proxy, the API).
 *
 * Astro has a built-in origin check, but it needs the public hostname at BUILD time, which makes a
 * published Docker image usable only on localhost. This is the same rule, configured at RUNTIME through
 * ALLOWED_HOSTS, so one image works on any hostname.
 *
 * The rule (identical to Astro's, plus the host allow-list):
 *  - Safe methods (GET, HEAD, OPTIONS) are never blocked.
 *  - Otherwise the request must be same-origin: its Origin header names the host the server was addressed
 *    with, and that host is in ALLOWED_HOSTS.
 *  - A request with a form-like content type (or none at all) must be same-origin. Other content types,
 *    notably JSON, are allowed without an Origin: a cross-site page can't send them without a CORS
 *    preflight, which this server never approves, and the session cookie is SameSite=Lax anyway.
 */

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const FORM_TYPES = ["application/x-www-form-urlencoded", "multipart/form-data", "text/plain"];

/** "a.example.com, *.school.edu" -> ["a.example.com", "*.school.edu"]. Defaults to local development hosts. */
export function parseAllowedHosts(raw: string | undefined | null): string[] {
  const value = raw == null || raw.trim() === "" ? "localhost,127.0.0.1" : raw;
  return value.split(",").map((h) => h.trim().toLowerCase()).filter(Boolean);
}

/** "Example.com:4321" -> "example.com"; "[::1]:4321" -> "::1". */
export function hostnameOf(hostHeader: string): string {
  const h = hostHeader.trim().toLowerCase();
  if (h.startsWith("[")) return h.slice(1, h.indexOf("]") === -1 ? undefined : h.indexOf("]"));
  return h.replace(/:\d+$/, "");
}

/** Exact match, or "*.example.com" for any subdomain (but not the bare domain). */
export function hostAllowed(hostHeader: string, allowed: string[]): boolean {
  const name = hostnameOf(hostHeader);
  if (!name) return false;
  return allowed.some((pattern) =>
    pattern.startsWith("*.") ? name.endsWith(pattern.slice(1)) && name.length > pattern.length - 1 : name === pattern,
  );
}

export interface CsrfInput {
  method: string;
  origin: string | null;
  /** The host the browser addressed: X-Forwarded-Host from your proxy if present, else Host. */
  host: string | null;
  contentType: string | null;
  allowedHosts: string[];
}

export function isSameOrigin({ origin, host, allowedHosts }: Pick<CsrfInput, "origin" | "host" | "allowedHosts">): boolean {
  if (!origin || !host || !hostAllowed(host, allowedHosts)) return false;
  try {
    return new URL(origin).host.toLowerCase() === host.trim().toLowerCase();
  } catch {
    return false; // "null" and other malformed origins
  }
}

/** True when the request must be refused with 403. */
export function csrfBlocked(input: CsrfInput): boolean {
  if (SAFE_METHODS.has(input.method.toUpperCase())) return false;
  if (isSameOrigin(input)) return false;
  const type = input.contentType?.toLowerCase();
  if (!type) return true; // no content type: could be a form-less cross-site POST
  return FORM_TYPES.some((t) => type.includes(t));
}

/** First value of a possibly comma-separated forwarding header. */
export const firstValue = (v: string | null): string | null => v?.split(",")[0]?.trim() || null;
