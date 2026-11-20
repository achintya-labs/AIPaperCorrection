import type { APIRoute } from "astro";

// Browser -> Astro -> API. Keeps the session cookie same-origin (no CORS) and the API private.
const API = process.env.API_INTERNAL_URL ?? "http://localhost:8000";
const HOP_BY_HOP = ["host", "connection", "content-length", "transfer-encoding", "keep-alive", "upgrade"];

export const ALL: APIRoute = async ({ request, params, url }) => {
  const path = params.path ?? "";
  if (path.split("/").includes("..")) return new Response("Bad path", { status: 400 });

  const headers = new Headers(request.headers);
  HOP_BY_HOP.forEach((h) => headers.delete(h));
  const hasBody = !["GET", "HEAD"].includes(request.method);
  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers,
    redirect: "manual",
    ...(hasBody ? { body: request.body, duplex: "half" as const } : {}),
  };

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/${path}${url.search}`, init);
  } catch {
    return new Response(JSON.stringify({ detail: "API unavailable" }), {
      status: 502,
      headers: { "content-type": "application/json" },
    });
  }
  const out = new Headers(upstream.headers);
  // fetch() already decoded the body, so these would now be wrong.
  ["content-encoding", "content-length", "transfer-encoding"].forEach((h) => out.delete(h));
  return new Response(upstream.body, { status: upstream.status, headers: out });
};
