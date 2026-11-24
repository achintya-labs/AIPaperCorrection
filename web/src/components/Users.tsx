import { useEffect, useRef, useState } from "react";
import { api, ApiError, errMsg } from "../lib/api";
import type { Role, User, UserAdmin } from "../lib/types";
import { Alert } from "./ui";
import { matchesQuery } from "../lib/search";
import SearchBox, { NoMatches } from "./SearchBox";

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

// No look-alike characters (0/O, 1/l/I), so a generated password can be read out or typed from a message.
const ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
function generatePassword(length = 16) {
  const bytes = new Uint32Array(length);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => ALPHABET[b % ALPHABET.length]).join("");
}

export default function Users() {
  const [users, setUsers] = useState<UserAdmin[] | null>(null);
  const [me, setMe] = useState<User | null>(null);
  const [form, setForm] = useState({ email: "", name: "", password: "", role: "ta" as Role });
  const [generatedPw, setGeneratedPw] = useState(false);
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState<number | null>(null);
  const [resetting, setResetting] = useState<{ id: number; value: string; generated: boolean } | null>(null);

  const load = () => api<UserAdmin[]>("/auth/users").then(setUsers).catch((e) => setError(errMsg(e)));
  useEffect(() => {
    load();
    api<User>("/auth/me").then(setMe).catch(() => {});
  }, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError(""); setOk("");
    try {
      await api("/auth/users", { method: "POST", json: form });
      setOk(
        generatedPw
          ? `Created ${form.email} with password: ${form.password}  (shown once; share it securely).`
          : form.password === ""
            ? `Created ${form.email} without a password. They can only sign in with Google.`
            : `Created ${form.email}. Share the password with them securely.`,
      );
      setForm({ email: "", name: "", password: "", role: "ta" });
      setGeneratedPw(false);
      load();
    } catch (err) { setError(errMsg(err)); }
  }

  async function patch(u: UserAdmin, body: { role?: Role; active?: boolean }, done: string) {
    setError(""); setOk(""); setBusy(u.id);
    try {
      await api(`/auth/users/${u.id}`, { method: "PATCH", json: body });
      setOk(done);
      await load();
    } catch (err) { setError(errMsg(err)); } finally { setBusy(null); }
  }

  function changeRole(u: UserAdmin, role: Role) {
    if (role === u.role) return;
    const msg = role === "admin"
      ? `Make ${u.email} an Admin? Admins can see every paper and manage exams and users.`
      : `Make ${u.email} a TA? They will only see papers assigned to them.`;
    if (confirm(msg)) patch(u, { role }, `${u.email} is now ${role === "admin" ? "an Admin" : "a TA"}. It applies immediately.`);
  }

  function toggleActive(u: UserAdmin) {
    if (u.active && !confirm(`Deactivate ${u.email}? They are signed out immediately and can't sign in. Their assignments and review history are kept.`)) return;
    patch(u, { active: !u.active }, `${u.email} ${u.active ? "deactivated" : "reactivated"}.`);
  }

  async function resetPassword(u: UserAdmin) {
    if (!resetting) return;
    setError(""); setOk(""); setBusy(u.id);
    try {
      await api(`/auth/users/${u.id}/password`, { method: "PUT", json: { password: resetting.value } });
      setOk(
        resetting.value === ""
          ? `${u.email} no longer has a password and can only sign in with Google. Their other sessions were signed out.`
          : resetting.generated
          ? `Password for ${u.email} reset to: ${resetting.value}  (shown once; share it securely). Their other sessions were signed out.`
          : `Password for ${u.email} updated. Their other sessions were signed out.`,
      );
      setResetting(null);
    } catch (err) { setError(errMsg(err)); } finally { setBusy(null); }
  }

  async function remove(u: UserAdmin) {
    setError(""); setOk("");
    const effects = [
      u.assigned_papers ? `${plural(u.assigned_papers, "paper")} will become unassigned` : "",
      "they are signed out immediately",
      "this can't be undone",
    ].filter(Boolean).join("; ");
    if (!confirm(`Delete ${u.email} permanently?\n\n${effects}.`)) return;
    setBusy(u.id);
    try {
      await api(`/auth/users/${u.id}`, { method: "DELETE" });
      setOk(`Deleted ${u.email}.`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && u.reviewed_grades > 0) {
        // Refused because they reviewed grades: offer the explicit, destructive option.
        if (confirm(`${u.email} reviewed ${plural(u.reviewed_grades, "grade")}.\n\nDeleting keeps those grades but erases who reviewed them. Deactivating instead keeps that history.\n\nDelete anyway?`)) {
          try {
            await api(`/auth/users/${u.id}?force=true`, { method: "DELETE" });
            setOk(`Deleted ${u.email}; their grades were kept without a reviewer.`);
          } catch (e2) { setError(errMsg(e2)); }
        }
      } else {
        setError(errMsg(err));
      }
    } finally { setBusy(null); await load(); }
  }

  const visible = (users ?? []).filter((u) =>
    matchesQuery([u.email, u.name, u.role === "admin" ? "admin" : "ta", u.active ? "active" : "inactive"], q),
  );

  return (
    <div className="stack">
      <h1>Users</h1>
      {error && <Alert tone="bad">{error}</Alert>}
      {ok && <Alert tone="ok">{ok}</Alert>}
      <form className="card" onSubmit={create}>
        <h2>Add a user</h2>
        <div className="grid2">
          <div className="field"><label htmlFor="u-email">Email</label><input id="u-email" type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></div>
          <div className="field"><label htmlFor="u-name">Name</label><input id="u-name" type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
          <div className="field">
            <label htmlFor="u-pw">Initial password <span className="muted">(leave blank for Google sign-in only)</span></label>
            <div className="row" style={{ gap: ".5rem" }}>
              <input id="u-pw" type={generatedPw ? "text" : "password"} className={generatedPw ? "mono grow" : "grow"} minLength={8} maxLength={72} autoComplete="new-password"
                value={form.password} onChange={(e) => { setForm({ ...form, password: e.target.value }); setGeneratedPw(false); }} />
              <button type="button" onClick={() => { setForm({ ...form, password: generatePassword() }); setGeneratedPw(true); }}>Generate</button>
            </div>
          </div>
          <div className="field"><label>Role</label>
            <RoleSelect ariaLabel="Role for new user" value={form.role} onChange={(role) => setForm({ ...form, role })} />
          </div>
        </div>
        <button className="primary">Create user</button>
      </form>
      {users && users.length > 0 && (
        <SearchBox value={q} onChange={setQ} label="Search users" placeholder="Search email, name, role, status"
          shown={visible.length} total={users.length} />
      )}
      <div className="card" style={{ padding: 0, overflow: "visible" }}>
        {!users ? <p className="muted" style={{ padding: "1rem" }}>Loading…</p> : visible.length === 0 ? (
          <NoMatches query={q} onClear={() => setQ("")} />
        ) : (
          <table>
            <thead><tr><th>User</th><th>Role</th><th>Status</th><th>Activity</th><th></th></tr></thead>
            <tbody>
              {visible.map((u) => {
                const self = u.id === me?.id;
                return (
                  <tr key={u.id}>
                    <td>
                      <strong>{u.email}</strong>{self && <span className="badge neutral" style={{ marginLeft: ".4rem" }}>you</span>}
                      {!u.has_password && <span className="badge neutral" style={{ marginLeft: ".4rem" }} title="No password set: can only sign in with Google">Google only</span>}
                      {u.name && <div className="muted small">{u.name}</div>}
                    </td>
                    <td>
                      <RoleSelect
                        ariaLabel={`Role for ${u.email}`}
                        value={u.role}
                        disabled={self || busy === u.id}
                        title={self ? "Ask another Admin to change your own role" : undefined}
                        onChange={(role) => changeRole(u, role)}
                      />
                    </td>
                    <td><span className={`badge ${u.active ? "ok" : "bad"}`}>{u.active ? "active" : "inactive"}</span></td>
                    <td className="small muted">{plural(u.assigned_papers, "paper")} assigned · {plural(u.reviewed_grades, "grade")} reviewed</td>
                    <td>
                      {resetting?.id === u.id ? (
                        <div className="stack" style={{ minWidth: 260 }}>
                          <div className="field" style={{ margin: 0 }}>
                            <label htmlFor={`rp-${u.id}`}>New password for {u.email}{!self && <span className="muted"> (blank = Google sign-in only)</span>}</label>
                            <input id={`rp-${u.id}`} type="text" className="mono" autoComplete="off" minLength={8} maxLength={72}
                              value={resetting.value} onChange={(e) => setResetting({ id: u.id, value: e.target.value, generated: false })} />
                          </div>
                          <div className="row">
                            <button className="primary" disabled={busy === u.id || (self ? resetting.value.length < 8 : resetting.value !== "" && resetting.value.length < 8)} onClick={() => resetPassword(u)}>{resetting.value === "" ? "Remove password" : "Set password"}</button>
                            <button onClick={() => setResetting({ id: u.id, value: generatePassword(), generated: true })}>Generate</button>
                            <button className="link" onClick={() => setResetting(null)}>Cancel</button>
                          </div>
                        </div>
                      ) : (
                      <div className="row">
                        <button disabled={busy === u.id} onClick={() => setResetting({ id: u.id, value: "", generated: false })}
                          title="Set a new password; other sessions are signed out">Reset password</button>
                        <button disabled={self || busy === u.id} onClick={() => toggleActive(u)}
                          title={self ? "You can't deactivate yourself" : ""}>{u.active ? "Deactivate" : "Reactivate"}</button>
                        <button className="danger" disabled={self || busy === u.id} onClick={() => remove(u)}
                          title={self ? "You can't delete yourself" : ""}>Delete</button>
                      </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

const ROLES: { value: Role; label: string }[] = [
  { value: "ta", label: "TA" },
  { value: "admin", label: "Admin" },
];

function RoleSelect({
  value,
  onChange,
  disabled = false,
  title,
  ariaLabel,
}: {
  value: Role;
  onChange: (role: Role) => void;
  disabled?: boolean;
  title?: string;
  ariaLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const label = ROLES.find((r) => r.value === value)?.label ?? value;

  return (
    <div ref={boxRef} style={{ position: "relative", minWidth: 110 }}>
      <button
        type="button"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-haspopup="listbox"
        disabled={disabled}
        title={title}
        onClick={() => setOpen((o) => !o)}
        style={{ width: "100%", display: "flex", justifyContent: "space-between", alignItems: "center", gap: ".5rem" }}
      >
        <span>{label}</span>
        <span className="small muted">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div
          role="listbox"
          className="card stack"
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: 0,
            right: 0,
            minWidth: 120,
            zIndex: 30,
            padding: ".35rem",
            gap: ".2rem",
            boxShadow: "0 8px 24px rgba(0, 0, 0, 0.5)",
          }}
        >
          {ROLES.map((r) => (
            <button
              key={r.value}
              type="button"
              role="option"
              aria-selected={r.value === value}
              onClick={() => {
                setOpen(false);
                if (r.value !== value) onChange(r.value);
              }}
              style={{
                textAlign: "left",
                border: 0,
                background: r.value === value ? "var(--info-bg)" : "transparent",
                color: r.value === value ? "var(--info)" : "var(--text)",
                fontWeight: r.value === value ? 600 : 400,
              }}
            >
              {r.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
