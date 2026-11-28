/**
 * Where "Log out" goes. LOGOUT_URL points at the login portal of the primary domain (auth.<DOMAIN>), but every extra domain has its own
 * portal (auth.<extra>) holding its own session, so on an extra domain the link must go there instead (with that domain's own port, if it has one).
 */
export function logoutUrlFor(host: string | null | undefined, logoutUrl: string, extraDomains: string | undefined): string {
  if (!logoutUrl || !host) return logoutUrl;
  const name = host.replace(/:\d+$/, "").toLowerCase();
  // Entries are "name" or "name:port" (the port people use on that domain, which a proxy in front may make differ from the base domain's).
  const extra = (extraDomains ?? "").split(/[,\s]+/).map((d) => d.trim().toLowerCase()).filter(Boolean)
    .map((d) => { const [n, p] = d.split(":"); return { name: n, port: p }; })
    .find((d) => d.name === name);
  if (!extra) return logoutUrl;
  try {
    const u = new URL(logoutUrl);
    u.hostname = `auth.${name}`;
    if (extra.port) u.port = extra.port === "443" ? "" : extra.port; // otherwise keep the base domain's port; the path is kept either way
    return u.toString();
  } catch {
    return logoutUrl;
  }
}
