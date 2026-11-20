import { useState } from "react";
import { api, errMsg } from "../lib/api";
import { Alert } from "./ui";

export default function LoginForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/auth/login", { method: "POST", json: { email, password } });
      window.location.href = "/";
    } catch (err) {
      setError(errMsg(err));
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit} style={{ maxWidth: 360, margin: "4rem auto" }}>
      <h1>Sign in</h1>
      {error && <Alert tone="bad">{error}</Alert>}
      <div className="field">
        <label htmlFor="email">Email</label>
        <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required autoFocus />
      </div>
      <div className="field">
        <label htmlFor="password">Password</label>
        <input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
      </div>
      <button className="primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
    </form>
  );
}
