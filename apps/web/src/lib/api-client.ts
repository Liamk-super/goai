export type ApiErrorPayload = {
  error_code?: string;
  message?: string;
  correlation_id?: string;
  retryable?: boolean;
};

export class ApiError extends Error {
  readonly status: number;
  readonly payload: ApiErrorPayload;

  constructor(status: number, payload: ApiErrorPayload) {
    super(payload.message ?? `Request failed with HTTP ${status}`);
    this.status = status;
    this.payload = payload;
  }
}

import { loadDemoSession } from "./demo-session.ts";

export type WorkspaceSession = { tenantId: string; actorId: string; workspaceId?: string };
export type Project = { project_id: string; workspace_id: string; name: string; status: string };
export type Run = {
  run_id: string; project_id: string; product_version_id: string; status: string;
  standard_version: string; current_cursor: string; correlation_id: string;
  current_stage?: string | null; attention_reason?: string | null; updated_at?: string;
};
export type EvidenceNode = {
  report_id: string; decision_id: string; finding_id: string; evidence_id: string;
  object_key: string; sha256: string; source_type: string; trust_level: string;
  summary?: string; region?: string | null; fetched_at?: string | null; valid_until?: string | null; dimension?: string;
};
export type DimensionResult = {
  grade: string; evidence_confidence: string; supporting_evidence: string[];
  counter_evidence: string[]; change: "IMPROVED" | "UNCHANGED" | "REGRESSED" | "NO_BASELINE";
  region?: string | null; as_of?: string | null; valid_until?: string | null;
  trend_signal?: string | null;
};
export type Report = {
  report_id: string; run_id: string; project_id: string; decision_id: string; recommendation: string;
  standard_version: string; dimension_grades: Record<string, string>; blocking_reasons: string[];
  action_items: string[]; created_at: string; evidence_chain: EvidenceNode[];
  dimension_results?: Record<string, DimensionResult>; key_contradictions?: string[];
  action_links?: { action: string; dimension: string | null; evidence_ids: string[] }[];
  geo_trend?: { signal: string; region: string | null; as_of: string | null; valid_until: string | null; evidence_ids: string[] };
  information_gaps?: string[];
  calibration_results?: { finding_id: string; decision: string; reason: string }[];
};
export type AgentTeamsRun = {
  run_id: string; team: { name: string; agentteams_version: string; binding_status: string; team_room_id?: string | null };
  stages: { code: string; status: string; ordinal: number }[];
  tasks: { id: string; stage_code: string; agent_identity_ref: string; status: string; tool_allowlist: string[]; evidence_count?: number; summary?: string | null; failure_reason?: string | null; retryable?: boolean; needs_human_review?: boolean; tool_invocations?: { tool_code: string; status: string }[] }[];
  handoff_count: number; matrix_event_count: number;
  budget?: { currency: string; limit: string; consumed: string; status: string } | null;
};

export const apiBase = () => process.env.NEXT_PUBLIC_LAUNCHSCOPE_API_BASE ?? "";
const uuid = () => crypto.randomUUID();

export function sessionFromDocument(): WorkspaceSession {
  if (typeof window === "undefined") throw new Error("No local Demo workspace session is available.");
  const session = loadDemoSession(window.localStorage);
  if (!session) throw new Error("No local Demo workspace session is available.");
  return { tenantId: session.tenantId, actorId: session.actorId, workspaceId: session.workspaceId };
}

export class LaunchScopeApi {
  private readonly session: WorkspaceSession;

  constructor(session: WorkspaceSession) { this.session = session; }

  async request<T>(path: string, init: RequestInit = {}, write = false): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("X-Tenant-Id", this.session.tenantId);
    headers.set("X-Actor-Id", this.session.actorId);
    headers.set("X-Correlation-Id", uuid());
    if (write) headers.set("Idempotency-Key", uuid());
    if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    const response = await fetch(`${apiBase()}/api/v1${path}`, { ...init, headers, credentials: "include" });
    if (!response.ok) {
      let payload: ApiErrorPayload = {};
      try { payload = await response.json() as ApiErrorPayload; } catch { /* preserve HTTP error */ }
      throw new ApiError(response.status, payload);
    }
    return response.json() as Promise<T>;
  }

  listProjects() { return this.request<{ items: Project[] }>("/projects"); }
  listRuns(projectId: string) { return this.request<{ items: Run[] }>(`/projects/${projectId}/runs`); }
  getRun(runId: string) { return this.request<Run>(`/runs/${runId}`); }
  getReport(reportId: string) { return this.request<Report>(`/experience/reports/${reportId}`); }
  getReportForRun(runId: string) { return this.request<Report>(`/experience/runs/${runId}/report`); }
  getAgentTeamsRun(runId: string) { return this.request<AgentTeamsRun>(`/experience/runs/${runId}/agentteams`); }
  evidenceReadUrl(evidenceId: string) { return this.request<{ read_url: string; expires_in_seconds: number }>(`/experience/evidence/${evidenceId}/read-url`); }
  compare(projectId: string, runId: string) { return this.request<Record<string, unknown>>(`/experience/projects/${projectId}/compare/${runId}`); }
  extractIntake(rawContent: string) {
    return this.request<{ source: "MODEL_INFERENCE"; model_id: string; extracted_fields: Record<string, string | null>; missing_fields: string[]; confirmation_required: true }>(
      "/intake:extract",
      { method: "POST", body: JSON.stringify({ raw_content: rawContent, allow_external_processing: true }) },
      true,
    );
  }
  createProject(workspaceId: string, name: string) {
    return this.request<{ project_id: string }>("/projects", { method: "POST", body: JSON.stringify({ workspace_id: workspaceId, name }) }, true).then(async created => {
      const visible = await this.listProjects();
      if (!visible.items.some(project => project.project_id === created.project_id)) {
        throw new Error("The server accepted the project command but did not commit a durable PostgreSQL projection.");
      }
      return created;
    });
  }
  createVersion(projectId: string, label: string) {
    return this.request<{ product_version_id: string }>(`/projects/${projectId}/versions`, { method: "POST", body: JSON.stringify({ label }) }, true);
  }
  async uploadMaterial(versionId: string, file: File): Promise<{ material_id: string; status: string }> {
    const digest = await sha256(file);
    const mimeType = file.type || "application/octet-stream";
    const init = await this.request<{ material_id: string; upload_url: string }>(
      `/product-versions/${versionId}/materials:initiate`,
      { method: "POST", body: JSON.stringify({ display_name: file.name, sha256: digest, size_bytes: file.size, mime_type: mimeType }) }, true,
    );
    // The signed URL remains a local variable until this PUT completes. The
    // browser never receives or stores object-store access credentials.
    const upload = await fetch(init.upload_url, {
      method: "PUT",
      body: file,
      headers: { "Content-Type": mimeType, "x-amz-acl": "private", "x-amz-meta-sha256": digest },
    });
    if (!upload.ok) throw new ApiError(upload.status, { message: "The signed object upload was rejected." });
    return this.request(`/materials/${init.material_id}/complete`, { method: "POST" }, true) as Promise<{ material_id: string; status: string }>;
  }
  gapQuestions(versionId: string) { return this.request<{ correlation_id: string; questions: { field: string; question: string; priority: number }[] }>(`/product-versions/${versionId}/gap-questions`, { method: "POST" }, true); }
  answerGaps(versionId: string, correlationId: string, answers: Record<string, string>) { return this.request(`/product-versions/${versionId}/gap-answers`, { method: "POST", body: JSON.stringify({ correlation_id: correlationId, answers }) }, true); }
  confirmProfile(versionId: string) { return this.request(`/product-versions/${versionId}/profile-confirmations`, { method: "POST", body: JSON.stringify({ acknowledge_model_inference: true }) }, true); }
  plan(versionId: string) {
    return this.request<{ run_id: string; status: string; correlation_id: string }>(`/product-versions/${versionId}/plan`, { method: "POST" }, true).then(async planned => {
      const durable = await this.getRun(planned.run_id);
      if (durable.status !== planned.status) throw new Error("The planned Run was not committed to PostgreSQL.");
      return planned;
    });
  }
  executeLocalDemo(runId: string, fixturePath: string) {
    return this.request<{ run_id: string; report_id: string; status: string; execution_mode: string }>(
      `/runs/${runId}/execute-local-demo`,
      { method: "POST", body: JSON.stringify({ fixture_path: fixturePath }) },
      true,
    );
  }
  dispatch(runId: string) {
    return this.request<{ run_id: string; status: string; manifest_sha256: string; task_count: number }>(
      `/runs/${runId}/dispatch`, { method: "POST" }, true,
    );
  }
}

export const browserApi = () => new LaunchScopeApi(sessionFromDocument());

async function sha256(file: File): Promise<string> {
  const bytes = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, "0")).join("");
}
