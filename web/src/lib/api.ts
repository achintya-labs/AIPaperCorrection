export class ApiError extends Error {
  constructor(public status: number, public detail: unknown, message: string) {
    super(message);
  }
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        if (isRecord(d) && Array.isArray(d.loc)) return `${d.loc.slice(1).join(".")}: ${String(d.msg ?? "")}`;
        return String(d);
      })
      .join("; ");
  }
  if (isRecord(detail) && "message" in detail) return String(detail.message);
  return "Request failed";
}

/** Same-origin call to the API through the Astro proxy. Body may be JSON-able or FormData. */
export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, ...rest } = init;
  const headers = new Headers(rest.headers);
  if (json !== undefined) {
    headers.set("content-type", "application/json");
    rest.body = JSON.stringify(json);
  }
  // Astro's CSRF origin check also rejects body-less non-GET requests that carry no content type
  // (and, unless the host is allow-listed, it always fails). A JSON content type keeps these
  // working from any host; it stays CSRF-safe because cross-site JSON needs a CORS preflight.
  const method = (rest.method ?? "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD" && rest.body == null && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  const res = await fetch(`/api${path}`, { ...rest, headers });
  if (res.status === 401 && !path.startsWith("/auth/login")) {
    window.location.href = "/login";
    throw new ApiError(401, null, "Session expired");
  }
  if (res.status === 204) return undefined as T;
  const ctype = res.headers.get("content-type") ?? "";
  const body: unknown = ctype.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = isRecord(body) && "detail" in body ? body.detail : body;
    throw new ApiError(res.status, detail, describe(detail));
  }
  return body as T;
}

export const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));
