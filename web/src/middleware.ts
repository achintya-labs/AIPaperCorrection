import { defineMiddleware } from "astro:middleware";
import { csrfBlocked, firstValue, parseAllowedHosts } from "./lib/csrf";

const API = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

async function currentUser(cookie: string | null): Promise<App.Locals["user"]> {
  if (!cookie) return null;
  try {
    const res = await fetch(`${API}/auth/me`, { headers: { cookie } });
    return res.ok ? await res.json() : null;
  } catch {
    return null;
  }
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

  const user = await currentUser(ctx.request.headers.get("cookie"));
  ctx.locals.user = user;
  if (!user && path !== "/login") return ctx.redirect("/login");
  if (user && path === "/login") return ctx.redirect("/");
  if (path.startsWith("/admin") && user?.role !== "admin") {
    return ctx.redirect("/");
  }
  return next();
});
