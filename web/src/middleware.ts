import { defineMiddleware } from "astro:middleware";
import { csrfBlocked, firstValue, parseAllowedHosts } from "./lib/csrf";

const API = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

// "sso" (default): a gateway signs people in and sends Remote-Email/Remote-Name; "local": our own login page and cookie.
const SSO = (process.env.AUTH_MODE ?? "sso") !== "local";

interface Who { user: App.Locals["user"]; status: number }

async function currentUser(h: Headers): Promise<Who> {
  const headers: Record<string, string> = {};
  // Local mode identifies you by the session cookie; sso by the headers the gateway adds (the API decides whether to trust them).
  for (const name of ["cookie", "remote-user", "remote-name", "remote-email"]) {
    const v = h.get(name);
    if (v) headers[name] = v;
  }
  if (!SSO && !headers.cookie) return { user: null, status: 401 };
  try {
    const res = await fetch(`${API}/auth/me`, { headers });
    return { user: res.ok ? await res.json() : null, status: res.status };
  } catch {
    return { user: null, status: 503 };
  }
}

const NOTICE: Record<number, [string, string]> = {
  401: ["Not signed in", "This page is only reachable through the sign-in gateway. Open it from its normal address."],
  403: ["Not enrolled in Paper Grader", "You are signed in, but your account is not in the Paper Grader admin or TA group. Ask an administrator to add you."],
  503: ["Temporarily unavailable", "The service or the user directory can't be reached right now. Try again in a minute."],
};

/** A plain page for sign-in problems in sso mode (there is no login page to send people to). */
function notice(status: number): Response {
  const [title, text] = NOTICE[status] ?? NOTICE[503];
  const logout = process.env.LOGOUT_URL ? `<p><a href="${process.env.LOGOUT_URL.replace(/"/g, "")}">Sign out</a></p>` : "";
  const html = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${title}</title>` +
    `<body style="background:#0a0a0a;color:#ededed;font:16px system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0">` +
    `<main style="max-width:28rem;padding:1.5rem"><h1 style="font-size:1.4rem">${title}</h1><p style="color:#8f8f8f">${text}</p>${logout}</main>`;
  return new Response(html, { status, headers: { "content-type": "text/html; charset=utf-8" } });
}

export const onRequest = defineMiddleware(async (ctx, next) => {
  // Cross-site request protection for every route, including the /api proxy. Configured at runtime
  // (ALLOWED_HOSTS) so a single published image works on any hostname; see lib/csrf.ts.
  const h = ctx.request.headers;
  if (csrfBlocked({
    method: ctx.request.method,
    origin: h.get("origin"),
    host: firstValue(h.get("x-forwarded-host")) ?? h.get("host"),
    contentType: h.get("content-type"),
    allowedHosts: parseAllowedHosts(process.env.ALLOWED_HOSTS),
  })) {
    return new Response("Cross-site POST form submissions are forbidden", { status: 403 });
  }

  const path = ctx.url.pathname;
  // The proxy forwards to the API, which enforces auth itself; assets need no session.
  if (path.startsWith("/api/") || path.startsWith("/_astro/") || path === "/favicon.svg") return next();

  const { user, status } = await currentUser(ctx.request.headers);
  ctx.locals.user = user;
  if (SSO) {
    if (!user) return notice(status);  // 401 not via the gateway, 403 not enrolled, 503 directory down
    if (path === "/login") return ctx.redirect("/");  // the login page is retired in sso mode
  } else {
    if (!user && path !== "/login") return ctx.redirect("/login");
    if (user && path === "/login") return ctx.redirect("/");
  }
  if (path.startsWith("/admin") && user?.role !== "admin") {
    return ctx.redirect("/");
  }
  return next();
});
