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

  return <main className="harbor">
    <div className="harbor-inner enters">
      <span className="bearing">LaunchScope v0.3</span>
      <h1>进入产品证据室。</h1>
      <p>输入一个昵称，系统会为本次体验创建隔离的本地租户与工作区。</p>

      <div className="harbor-note">
        <strong>本地体验身份</strong>
        <span>身份只缓存在当前浏览器，不是正式 OAuth、OIDC 或生产登录。</span>
      </div>

      <form onSubmit={submit}>
        <label>
          <span className="field-name">昵称</span>
          <span className="field-hint">用于标识本次体验的工作区，2—40 个字符。</span>
          <input autoFocus minLength={2} maxLength={40} value={displayName} onChange={event => setDisplayName(event.target.value)} placeholder="给这次体验起个名字" required />
        </label>
        {error && <p role="alert">{error}</p>}
        <div className="form-actions">
          <button disabled={busy}>{busy ? "正在创建工作区…" : "开始本地体验"}</button>
          <a className="button secondary" href="/recorded-snapshot">打开只读验收快照</a>
        </div>
      </form>
    </div>
  </main>;
}
