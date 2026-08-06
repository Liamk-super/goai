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
      if (session.schemaVersion !== DEMO_SESSION_SCHEMA) throw new Error("服务端返回了不兼容的本地体验会话。");
      saveDemoSession(window.localStorage, session);
      window.location.assign("/projects");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法创建本地体验会话");
      setBusy(false);
    }
  }

  return <main className="demo-login">
    <section className="page-header reveal">
      <div><p className="eyebrow">LaunchScope v0.3</p><h1>进入产品证据室。</h1><p className="lede">输入一个昵称，系统会为本次体验创建隔离的本地租户与工作区。</p></div>
    </section>
    <section className="panel reveal">
      <div className="demo-warning"><strong>本地体验身份</strong><span>身份只缓存在当前浏览器，不是正式 OAuth、OIDC 或生产登录。</span></div>
      <form onSubmit={submit}>
        <label>昵称<input autoFocus minLength={2} maxLength={40} value={displayName} onChange={event => setDisplayName(event.target.value)} placeholder="2—40 个字符" required /></label>
        {error && <div role="alert">{error}</div>}
        <div className="form-actions"><button disabled={busy}>{busy ? "正在创建工作区…" : "开始本地体验"}</button></div>
      </form>
      <p><a href="/recorded-snapshot">打开已标注的只读验收快照</a></p>
    </section>
  </main>;
}
