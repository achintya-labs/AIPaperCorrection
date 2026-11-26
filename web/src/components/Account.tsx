import { useEffect, useState } from "react";
import { api, errMsg } from "../lib/api";
import type { User } from "../lib/types";
import { Alert } from "./ui";

export default function Account({ sso = false, ldapAdminUrl = "" }: { sso?: boolean; ldapAdminUrl?: string }) {
  const [me, setMe] = useState<User | null>(null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    api<User>("/auth/me").then((u) => { if (alive) setMe(u); }).catch(() => {});
    return () => { alive = false; };
  }, []);

  // Cheap checks before a round trip; the server enforces the real rules.
  const problem =
    next && next.length < 8 ? "The new password needs at least 8 characters"
    : confirm && next !== confirm ? "The new passwords don't match"
    : next && current && next === current ? "The new password must be different from the current one"
    : "";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(""); setOk(""); setBusy(true);
    try {
      await api("/auth/me/password", { method: "PUT", json: { current_password: current, new_password: next } });
      setCurrent(""); setNext(""); setConfirm("");
      setOk("Password changed. Your other sessions were signed out; this one stays signed in.");
    } catch (err) {
      setError(errMsg(err));
    } finally { setBusy(false); }
  }

  if (sso) {
    return (
      <div className="stack" style={{ maxWidth: 480 }}>
        <h1>Account</h1>
        {me && <p className="muted">{me.name ? `${me.name} · ` : ""}{me.email} · {me.role === "admin" ? "Admin" : "TA"}</p>}
        <div className="card stack">
          <p style={{ margin: 0 }}>
            Your sign-in, name and role are managed in the user directory (LDAP), not here. Change your password or name
            there{ldapAdminUrl ? <> at <a href={ldapAdminUrl}>{ldapAdminUrl.replace(/^https?:\/\//, "")}</a></> : ""}, and ask an administrator to change your role.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="stack" style={{ maxWidth: 480 }}>
      <h1>Account</h1>
      {me && <p className="muted">{me.name ? `${me.name} · ` : ""}{me.email} · {me.role === "admin" ? "Admin" : "TA"}</p>}
      <form className="card stack" onSubmit={submit}>
        <h2 style={{ margin: 0 }}>Change password</h2>
        {error && <Alert tone="bad">{error}</Alert>}
        {ok && <Alert tone="ok">{ok}</Alert>}
        <div className="field">
          <label htmlFor="pw-current">Current password</label>
          <input id="pw-current" type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="pw-new">New password</label>
          <input id="pw-new" type="password" autoComplete="new-password" required minLength={8} maxLength={72} value={next} onChange={(e) => setNext(e.target.value)} />
          <span className="small muted">8 to 72 characters.</span>
        </div>
        <div className="field">
          <label htmlFor="pw-confirm">Confirm new password</label>
          <input id="pw-confirm" type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </div>
        {problem && <span className="small" style={{ color: "var(--bad)" }} role="alert">{problem}</span>}
        <div>
          <button className="primary" disabled={busy || !current || !next || !confirm || !!problem}>
            {busy ? "Saving…" : "Change password"}
          </button>
        </div>
      </form>
    </div>
  );
}
