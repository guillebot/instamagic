import { useQueryClient } from "@tanstack/react-query";
import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api } from "../api";
import { VersionChip } from "../VersionChip";

export function LoginPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  // Already signed in (valid cookie or trusted LAN device)? Skip the password form.
  useEffect(() => {
    let cancelled = false;
    api<{ authenticated: boolean }>("/api/auth/session")
      .then(() => {
        if (!cancelled) navigate("/", { replace: true });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await api("/api/auth/login", { method: "POST", body: JSON.stringify({ password }) });
      await queryClient.invalidateQueries({ queryKey: ["session"] });
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed.");
    }
  }

  return (
    <main className="login">
      <form className="panel" onSubmit={onSubmit}>
        <h1>Instamagic</h1>
        <p>Local review for your photography archive. Originals stay on this machine.</p>
        <label>
          Password
          <input
            type="password"
            value={password}
            autoComplete="current-password"
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error ? <p className="error">{error}</p> : null}
        <button className="button" type="submit">
          Unlock
        </button>
        <p className="login-version muted">
          Build <VersionChip />
        </p>
      </form>
    </main>
  );
}
