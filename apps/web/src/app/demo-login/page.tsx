"use client";

import { FormEvent, useState } from "react";

import { apiBase, ApiError } from "../../lib/api-client";
import { DEMO_SESSION_SCHEMA, saveDemoSession, type DemoSession } from "../../lib/demo-session";

export default function DemoLoginPage() {
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${apiBase()}/api/v1/demo/sessions`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
        body: JSON.stringify({ display_name: displayName }),
      });
      if (!response.ok) throw new ApiError(response.status, await response.json().catch(() => ({})));
      const session = await response.json() as DemoSession;
      if (session.schemaVersion !== DEMO_SESSION_SCHEMA) throw new Error("The server returned an unsupported Demo session.");
      saveDemoSession(window.localStorage, session);
      window.location.assign("/projects");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to create the local Demo session");
      setBusy(false);
    }
  }

  return <main className="demo-login">
    <section className="page-header reveal">
      <div><p className="eyebrow">LaunchScope v0.2</p><h1>Enter the evidence room.</h1><p className="lede">Choose a nickname to create an isolated local tenant and workspace for this Demo.</p></div>
    </section>
    <section className="panel reveal">
      <div className="demo-warning"><strong>Local Demo Identity</strong><span>This is browser-cached Demo identification, not OAuth, OIDC, or production authentication.</span></div>
      <form onSubmit={submit}>
        <label>Nickname<input autoFocus minLength={2} maxLength={40} value={displayName} onChange={event => setDisplayName(event.target.value)} placeholder="2–40 characters" required /></label>
        {error && <div role="alert">{error}</div>}
        <div className="form-actions"><button disabled={busy}>{busy ? "Creating workspace…" : "Start local Demo"}</button></div>
      </form>
      <p><a href="/recorded-snapshot">Open the labelled read-only acceptance fallback</a></p>
    </section>
  </main>;
}
