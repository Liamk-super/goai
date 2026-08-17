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
export type ProjectPortrait = {
  project_id: string;
  product_version_id: string | null;
  version_label: string | null;
  version_number: number | null;
  confirmed_at: string | null;
  confirmed_fields: Record<string, string>;
};
export type EvaluationHistoryItem = {
  run_id: string;
  project_id: string;
  project_name: string;
  product_version_label: string | null;
  product_version_number: number | null;
  status: string;
  recommendation?: string | null;
  updated_at: string;
};
export type Run = {
  run_id: string; project_id: string; product_version_id: string; status: string;
  standard_version: string; current_cursor: string; correlation_id: string;
  current_stage?: string | null; attention_reason?: string | null; updated_at?: string;
  product_version_label?: string | null; product_version_number?: number | null;
  architecture_generation?: string; ui_mode?: "SUPERVISOR_1P4" | "LEGACY";
  dispatch_pending?: boolean;
  project_name?: string;
  locale?: "zh-CN" | "en";
  execution_control?: RunExecutionControl;
  experience_stage?: {
    ordinal: 1 | 2 | 3 | 4;
    code: "UNDERSTANDING" | "MULTI_REVIEW" | "REVIEW_REPORT" | "COMPLETED";
    label: string;
    exception: "NEEDS_INPUT" | "NEEDS_CONFIRMATION" | null;
    exception_label: string | null;
  };
};
export type RunExecutionControl = {
  run_id?: string;
  state: "ACTIVE" | "PAUSE_REQUESTED" | "PAUSED" | "PAUSE_BLOCKED" | "CLOSED";
  control_epoch: number;
  usage_settlement_status: "NONE" | "PENDING" | "SETTLED" | "UNKNOWN";
  in_flight_count: number;
  resumable?: boolean;
  pause_requested_at?: string | null;
  paused_at?: string | null;
  resumed_at?: string | null;
  last_error?: string | null;
  checkpoint?: {
    interrupted_task_ids: string[];
    completed_task_ids: string[];
    evidence_ids: string[];
    usage_summary: { quantity?: string; cost?: string };
  } | null;
  remaining_budget?: {
    currency: string; limit: string; reserved: string; consumed: string; remaining: string; status: string;
  } | null;
  usage_after_pause?: { tokens: number; cost: string };
};
export type RunRecovery = {
  run_id: string;
  run_status: "RUNNING";
  execution_control: RunExecutionControl;
  recovered_task_ids: string[];
  preserved_task_ids: string[];
  dispatched_task_count: number;
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
  calibration_results?: {
    finding_id: string; decision: string; reason: string; contract_version: string;
    rule_ids: string[]; evidence_ids: string[]; score_components: Record<string, number>; flags: string[];
  }[];
  architecture_generation?: string;
  project_name?: string;
  locale?: "zh-CN" | "en";
  deterministic_score?: {
    score: number; coverage: number; recommendation: string;
    dimension_scores: Record<string, number | null>; caps_applied: string[]; missing_agents: string[];
  } | null;
  layered_report?: {
    summary: string; actions: string[]; largest_opportunity: string | null; largest_risk: string | null;
    coverage: number; confidence: number | null; information_gaps: string[]; conflicts: string[];
    cross_domain_analysis: string[]; citations: { kind: "FINDING" | "EVIDENCE"; ref: string }[];
    version_changes: { improved?: string[]; unchanged?: string[]; new_risks?: string[] };
    decision_conflict: boolean; synthesis_status: string;
  } | null;
};
export type ReportClaimV2 = {
  claim_id: string;
  section: string;
  text: string;
  status: "VERIFIED" | "DOWNGRADED" | "PENDING_VALIDATION" | "CONFLICTED";
  decision_relevance: "CRITICAL" | "IMPORTANT" | "CONTEXT";
  citation_ids: string[];
  score_bearing: boolean;
};
export type ReportCitationV2 = {
  citation_id: string;
  claim_id: string;
  evidence_id: string;
  source_locator_id: string | null;
  support_role: "SUPPORT" | "COUNTER" | "BACKGROUND";
  audit_status: "VERIFIED" | "DOWNGRADED" | "REJECTED" | "NEEDS_MORE";
  label: number;
};
export type SourceLocatorV2 = {
  source_locator_id: string;
  evidence_id: string;
  source_kind: "PUBLIC_URL" | "SEARCH_RESULT" | "INTERNAL_MATERIAL";
  canonical_url?: string;
  title: string;
  publisher?: string | null;
  published_at?: string | null;
  fetched_at: string;
  locator: Record<string, unknown>;
  region?: string | null;
  independence_group: string;
  content_sha256: string;
};
export type ReportActionV2 = {
  action_id: string;
  title: string;
  owner: string;
  deadline_days: number;
  success_criteria: string[];
  failure_triggers: string[];
  required_evidence: string[];
  related_claim_ids: string[];
};
export type SupervisorReportDocumentV2 = {
  schema_version: "2.0";
  report_id: string;
  run_id: string;
  project_id: string;
  product_version_id: string;
  product_title: string;
  source_sha256: string;
  top_card: {
    potential_index: number;
    stage: string;
    confidence_band: "LOW" | "MEDIUM" | "HIGH";
    evidence_coverage: number;
    recommendation: "PROCEED" | "VALIDATE_FURTHER" | "ADJUST" | "PAUSE";
  };
  comparison?: {
    schema_version: "1.0";
    status: "COMPARABLE" | "STANDARD_CHANGED";
    index_before?: number;
    index_after?: number;
    index_delta?: number;
    dimension_deltas?: Array<{ dimension: string; before: number; after: number; delta: number }>;
    resolved_issues: string[];
    unchanged_issues: string[];
    new_risks: string[];
    evidence_upgrades: string[];
    evidence_downgrades: string[];
    change_reason_claim_ids: string[];
  };
  summary_claim_id: string;
  claims: ReportClaimV2[];
  highlights: string[];
  critical_issues: string[];
  role_summaries: { user: string[]; product: string[]; investment: string[] };
  cross_domain_claims: string[];
  actions: ReportActionV2[];
  confidence_breakdown: {
    profile_ref: string;
    audited_evidence_quality: number;
    evidence_coverage: number;
    independent_source_support: number;
    freshness: number;
    cross_domain_agreement: number;
    unresolved_conflict_penalty: number;
    score: number;
    band: "LOW" | "MEDIUM" | "HIGH";
  };
  agent_report_cards: Array<{
    agent_code: AgentReportSummary["agent_code"];
    report_id: string;
    title: string;
    summary_claim_ids: string[];
    source_sha256: string;
  }>;
  citations: ReportCitationV2[];
  source_directory: SourceLocatorV2[];
  audit_detail_ref: string;
};
export type ReportV2Projection = {
  report_schema_version: "2.0";
  document: SupervisorReportDocumentV2;
  integrity: { canonical_sha256: string; source_sha256: string };
  projection: { view: "FULL"; created_at: string };
};
export type ReportDimensionV3 = {
  value: number | null;
  strength: "STRONG" | "MODERATE" | "WEAK" | "INSUFFICIENT_EVIDENCE";
  evidence_level: "HIGH" | "MEDIUM" | "LOW" | "PENDING";
  positive_driver_claim_ids: string[];
  negative_driver_claim_ids: string[];
  pending_validation_claim_ids: string[];
};
export type SupervisorReportDocumentV3 = Omit<
  SupervisorReportDocumentV2,
  "schema_version" | "comparison"
> & {
  schema_version: "3.0";
  locale: "zh-CN" | "en";
  dimension_scores: Record<"user_value" | "product_capability" | "investment_potential" | "evidence_quality", ReportDimensionV3>;
  evidence_coverage_profile: {
    definition_version: string;
    label: "EVIDENCE_COVERAGE";
    required_dimensions: number;
    covered_dimensions: number;
    quality_note: string;
    independent_support_note: string;
  };
  issue_priorities: Array<{ priority: "P0" | "P1" | "P2"; claim_id: string; decision_impact: string }>;
  comparison?: SupervisorReportDocumentV2["comparison"];
};
export type ReportV3Projection = {
  report_schema_version: "3.0";
  document: SupervisorReportDocumentV3;
  integrity: { canonical_sha256: string; source_sha256: string };
  projection: { view: "FULL"; created_at: string };
};
export type ReportDisplay = Report | ReportV2Projection | ReportV3Projection;
export function reportIdForDisplay(report: ReportDisplay): string {
  return "document" in report ? report.document.report_id : report.report_id;
}
export type SpecialistReportDocumentV2 = {
  schema_version: "2.0";
  report_id: string;
  run_id: string;
  project_id: string;
  product_version_id: string;
  product_title: string;
  agent_code: AgentReportSummary["agent_code"];
  source_sha256: string;
  executive_summary: string[];
  metrics: Array<{ key: string; label: string; value: string | number | boolean; claim_ids: string[] }>;
  claims: ReportClaimV2[];
  domain_payload: Record<string, unknown>;
  risks: string[];
  actions: ReportActionV2[];
  citations: ReportCitationV2[];
  source_directory: SourceLocatorV2[];
  audit_summary: { verified: number; insufficient: number; needs_more: number; conflicted: number };
  raw_audit_refs: string[];
};
export type SpecialistReportV2Projection = {
  report_schema_version: "2.0";
  document: SpecialistReportDocumentV2;
  integrity: { canonical_sha256: string; source_sha256: string };
  projection: { view: "FULL"; created_at: string; supervisor_report_id: string };
};
export type SpecialistReportDocumentV3 = Omit<SpecialistReportDocumentV2, "schema_version" | "domain_payload"> & {
  schema_version: "3.0";
  locale: "zh-CN" | "en";
  domain_payload: Record<string, unknown> & { kind: "USER_EVIDENCE" | "PRODUCT_ENGINEERING" | "BUSINESS_INVESTMENT" | "EVIDENCE_AUDIT" };
};
export type SpecialistReportV3Projection = {
  report_schema_version: "3.0";
  document: SpecialistReportDocumentV3;
  integrity: { canonical_sha256: string; source_sha256: string };
  projection: { view: "FULL"; created_at: string; supervisor_report_id: string };
};
export type AgentReportDisplay = AgentReportDetail | SpecialistReportV2Projection | SpecialistReportV3Projection;
export type ReportExportRequest = {
  kind: "SUPERVISOR" | "SPECIALIST" | "PACKAGE";
  agent_code: AgentReportSummary["agent_code"] | null;
  view: "SUMMARY" | "FULL";
  locale: string;
  include_evidence: boolean;
};
export type ReportExportArtifact = {
  export_id: string;
  report_id: string;
  run_id: string;
  kind: ReportExportRequest["kind"];
  agent_code: AgentReportSummary["agent_code"] | null;
  view: ReportExportRequest["view"];
  locale: string;
  include_evidence: boolean;
  source_sha256: string;
  status: "PENDING" | "RENDERING" | "COMPLETED" | "FAILED";
  object_key: string | null;
  sha256: string | null;
  size_bytes: number | null;
  error_code: string | null;
};
export type ReportExportReadUrl = {
  export_id: string;
  sha256: string;
  size_bytes: number;
  read_url: string;
};
export type PublicDemoShare = {
  share_id: string;
  run_id: string;
  report_id: string;
  token: string;
  status: "ACTIVE";
  include_agent_reports: true;
  include_evidence: true;
  created_at: string;
};
export type SupervisorMessageResult = {
  message_id: string; brief_id: string; brief_revision: number;
  interaction_state: "WAITING_FOR_USER" | "LEADER_PLANNING";
  confirmation_required: boolean; questions: string[]; duplicate: boolean;
};
export type ConversationChannel = "supervisor" | "user-evidence" | "product-engineering" | "business-investment";
export type ConversationChannelState = {
  channel: ConversationChannel; status: string; evidence_count: number; pending_count: number; summary: string;
};
export type ConversationMessage = {
  message_id: string; channel: ConversationChannel; role: "USER" | "SUPERVISOR" | "AGENT" | "SYSTEM";
  kind: "MESSAGE" | "ROUTING_RECEIPT" | "QUESTION" | "ANSWER"; text: string;
  route_state: "RECORDED" | "ROUTED" | "WAITING_FOR_USER" | "NEEDS_ATTENTION";
  affected_task_ids: string[]; created_at: string;
};
export type RunConversations = {
  run_id: string; channels: ConversationChannelState[]; messages: ConversationMessage[]; next_cursor: string | null;
};
export type ConversationReceipt = {
  message_id: string; run_id: string; channel: ConversationChannel;
  route_state: ConversationMessage["route_state"]; affected_task_ids: string[]; questions: string[]; duplicate: boolean;
};
export type AgentTeamsRun = {
  run_id: string; team: { name: string; agentteams_version: string; binding_status: string; team_room_id?: string | null };
  stages: { code: string; status: string; ordinal: number }[];
  tasks: { id: string; stage_code: string; agent_identity_ref: string; status: string; tool_allowlist: string[]; evidence_count?: number; summary?: string | null; failure_reason?: string | null; retryable?: boolean; needs_human_review?: boolean; tool_invocations?: { tool_code: string; status: string }[]; created_at?: string | null; updated_at?: string | null }[];
  handoff_count: number; matrix_event_count: number;
  budget?: { currency: string; limit: string; consumed: string; status: string } | null;
};
export type AgentReportStatus = "PENDING" | "AVAILABLE" | "FAILED" | "UNAVAILABLE";
export type AgentReportSummary = {
  agent_code: "user-evidence" | "product-engineering" | "business-investment" | "evidence-auditor";
  title: string;
  kind: "DOMAIN" | "AUDIT";
  status: AgentReportStatus;
  sha256: string | null;
  created_at: string | null;
  revision: number | null;
  failure_reason: string | null;
};
export type AgentReportDetail = Omit<
  AgentReportSummary,
  "status" | "failure_reason" | "revision" | "sha256" | "created_at"
> & {
  run_id: string;
  mime_type: string;
  format: "json" | "markdown" | "text";
  content: string;
  sha256: string;
  created_at: string;
  audit_round: number | null;
  projection_status?: "ORIGINAL_ARTIFACT" | "LEGACY_SOURCE_PROJECTED";
  content_sha256?: string;
};
export type Clarification = {
  request_id: string; task_id: string; agent_code: string; field: string;
  question: string; why_blocking: string; impact_dimension: string;
};
export type ClarificationAnswerResult = {
  run_id: string; run_status: string; affected_task_ids: string[];
  unaffected_task_ids: string[]; dispatched: boolean;
};
export type ValidationTask = {
  task_key: string; description: string; expected_observable_outcome: string; max_steps?: number | null;
};
export type ValidationTaskDraft = ValidationTask & { rationale: string; source_hints: string[] };
export type VisualPageDraft = {
  model_id: string;
  recognition_type: "TEXT" | "TABLE" | "IMAGE" | "DIAGRAM" | "SCREENSHOT" | "SCAN" | "MIXED";
  summary: string;
  rotation_degrees: 0 | 90 | 180 | 270;
  confidence: number;
  table: null | { title?: string | null; headers?: string[]; rows?: string[][] };
};
export type UserValidationResult = {
  run_id: string; skill_result_ref: string; schema_version: string; status: string; mode: string;
  summary: Record<string, unknown>; skill_result_sha256: string; report_url: string; expires_in_seconds: number;
  presentation: null | {
    version: "0.4";
    summary: { markdown: UserValidationPresentationFormat; html: UserValidationPresentationFormat };
    full: { markdown: UserValidationPresentationFormat; html: UserValidationPresentationFormat };
  };
};
export type UserValidationPresentationFormat = { available: boolean; content_sha256: string | null };
export type UserValidationReport = {
  run_id: string; skill_result_ref: string; variant: "summary" | "full"; format: "html" | "markdown";
  content: string; content_sha256: string; skill_result_sha256: string;
};

export const apiBase = () => process.env.NEXT_PUBLIC_LAUNCHSCOPE_API_BASE ?? "";
const uuid = () => crypto.randomUUID();

export function stableAsciiKey(value: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

export function boundedIdempotencyKey(prefix: string, scope: string, payload: string): string {
  return `${prefix}:${scope}:${stableAsciiKey(payload)}`;
}

async function publicExportRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-Correlation-Id", uuid());
  if (init.body) headers.set("Content-Type", "application/json");
  const response = await fetch(`${apiBase()}/api/v1${path}`, { ...init, headers, credentials: "omit" });
  if (!response.ok) {
    let payload: ApiErrorPayload = {};
    try { payload = await response.json() as ApiErrorPayload; } catch { /* preserve HTTP status */ }
    throw new ApiError(response.status, payload);
  }
  return response.json() as Promise<T>;
}

export function createPublicDemoReportExport(
  token: string,
  reportId: string,
  request: ReportExportRequest,
  idempotencyKey: string,
) {
  return publicExportRequest<ReportExportArtifact>(
    `/public/demo/v2/reports/${reportId}/exports?token=${encodeURIComponent(token)}`,
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(request),
    },
  );
}

export function getPublicDemoReportExport(token: string, exportId: string) {
  return publicExportRequest<ReportExportArtifact>(
    `/public/demo/v2/report-exports/${exportId}?token=${encodeURIComponent(token)}`,
  );
}

export function getPublicDemoReportExportReadUrl(token: string, exportId: string) {
  return publicExportRequest<ReportExportReadUrl>(
    `/public/demo/v2/report-exports/${exportId}/read-url?token=${encodeURIComponent(token)}`,
  );
}

export function sessionFromDocument(): WorkspaceSession {
  if (typeof window === "undefined") throw new Error("No local Demo workspace session is available.");
  const session = loadDemoSession(window.localStorage);
  if (!session) throw new Error("No local Demo workspace session is available.");
  return { tenantId: session.tenantId, actorId: session.actorId, workspaceId: session.workspaceId };
}

export class LaunchScopeApi {
  private readonly session: WorkspaceSession;

  constructor(session: WorkspaceSession) { this.session = session; }

  async request<T>(path: string, init: RequestInit = {}, write = false, idempotencyKey?: string): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("X-Tenant-Id", this.session.tenantId);
    headers.set("X-Actor-Id", this.session.actorId);
    headers.set("X-Correlation-Id", uuid());
    if (typeof document !== "undefined") headers.set("Accept-Language", document.documentElement.lang);
    // A retry must replay the original submission, so a caller that owns a
    // logical write passes a stable key instead of minting one per attempt.
    if (write) headers.set("Idempotency-Key", idempotencyKey ?? uuid());
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
  getProjectPortrait(projectId: string) { return this.request<ProjectPortrait>(`/projects/${projectId}/portrait`); }
  listRuns(projectId: string) { return this.request<{ items: Run[] }>(`/projects/${projectId}/runs`); }
  listEvaluationHistory(options: { limit?: number; offset?: number; search?: string; sort?: "newest" | "oldest" } = {}) {
    const query = new URLSearchParams({
      limit: String(options.limit ?? 6),
      offset: String(options.offset ?? 0),
      sort: options.sort ?? "newest",
    });
    if (options.search?.trim()) query.set("search", options.search.trim());
    return this.request<{ items: EvaluationHistoryItem[]; has_more: boolean; total: number }>(
      `/experience/history?${query.toString()}`,
    );
  }
  getRun(runId: string) { return this.request<Run>(`/runs/${runId}`); }
  getRunExecutionControl(runId: string) {
    return this.request<RunExecutionControl>(`/runs/${runId}/execution-control`);
  }
  pauseRun(runId: string, expectedControlEpoch: number, idempotencyKey: string) {
    return this.request<RunExecutionControl>(
      `/runs/${runId}/pause`,
      { method: "POST", body: JSON.stringify({ expected_control_epoch: expectedControlEpoch, reason: "USER_EXIT" }) },
      true,
      idempotencyKey,
    );
  }
  resumeRun(runId: string, expectedControlEpoch: number, idempotencyKey: string) {
    return this.request<RunExecutionControl>(
      `/runs/${runId}/resume`,
      { method: "POST", body: JSON.stringify({ expected_control_epoch: expectedControlEpoch }) },
      true,
      idempotencyKey,
    );
  }
  recoverRun(runId: string, expectedControlEpoch: number, idempotencyKey: string) {
    return this.request<RunRecovery>(
      `/runs/${runId}/recover`,
      { method: "POST", body: JSON.stringify({ expected_control_epoch: expectedControlEpoch, force: true }) },
      true,
      idempotencyKey,
    );
  }
  getReport(reportId: string) { return this.request<Report>(`/experience/reports/${reportId}`); }
  getReportV2(reportId: string) {
    return this.request<ReportV2Projection>(`/experience/v2/reports/${reportId}`);
  }
  getReportV3(reportId: string) {
    return this.request<ReportV3Projection>(`/experience/v3/reports/${reportId}`);
  }
  createPublicDemoShare(reportId: string) {
    return this.request<PublicDemoShare>(
      `/experience/v2/reports/${reportId}/public-demo-share`,
      { method: "POST" },
      true,
      `public-demo-share-v1:${reportId}`,
    );
  }
  createReportExport(reportId: string, request: ReportExportRequest, idempotencyKey: string) {
    return this.request<ReportExportArtifact>(
      `/experience/reports/${reportId}/exports`,
      { method: "POST", body: JSON.stringify(request) },
      true,
      idempotencyKey,
    );
  }
  getReportExport(exportId: string) {
    return this.request<ReportExportArtifact>(`/experience/report-exports/${exportId}`);
  }
  getReportExportReadUrl(exportId: string) {
    return this.request<ReportExportReadUrl>(`/experience/report-exports/${exportId}/read-url`);
  }
  async getReportForDisplay(reportId: string): Promise<ReportDisplay> {
    try {
      return await this.getReportV3(reportId);
    } catch (cause) {
      if (!(cause instanceof ApiError) || cause.status !== 404) throw cause;
      try {
        return await this.getReportV2(reportId);
      } catch (legacyCause) {
        if (!(legacyCause instanceof ApiError) || legacyCause.status !== 404) throw legacyCause;
        return this.getReport(reportId);
      }
    }
  }
  getReportV2ForRun(runId: string) {
    return this.request<ReportV2Projection>(`/experience/v2/runs/${runId}/report`);
  }
  getReportV3ForRun(runId: string) {
    return this.request<ReportV3Projection>(`/experience/v3/runs/${runId}/report`);
  }
  async getReportForRunDisplay(runId: string): Promise<ReportDisplay> {
    try {
      return await this.getReportV3ForRun(runId);
    } catch (cause) {
      if (!(cause instanceof ApiError) || cause.status !== 404) throw cause;
      try {
        return await this.getReportV2ForRun(runId);
      } catch (legacyCause) {
        if (!(legacyCause instanceof ApiError) || legacyCause.status !== 404) throw legacyCause;
        return this.getReportForRun(runId);
      }
    }
  }
  getReportForRun(runId: string) { return this.request<Report>(`/experience/runs/${runId}/report`); }
  getAgentTeamsRun(runId: string) { return this.request<AgentTeamsRun>(`/experience/runs/${runId}/agentteams`); }
  listAgentReports(runId: string) {
    return this.request<{ run_id: string; reports: AgentReportSummary[] }>(
      `/experience/runs/${runId}/agent-reports`,
    );
  }
  listAgentReportsV2(runId: string) {
    return this.request<{ run_id: string; reports: AgentReportSummary[] }>(
      `/experience/v2/runs/${runId}/agent-reports`,
    );
  }
  listAgentReportsV3(runId: string) {
    return this.request<{ run_id: string; reports: AgentReportSummary[] }>(
      `/experience/v3/runs/${runId}/agent-reports`,
    );
  }
  async listAgentReportsForDisplay(runId: string) {
    try {
      return await this.listAgentReportsV3(runId);
    } catch (cause) {
      if (!(cause instanceof ApiError) || cause.status !== 404) throw cause;
      try {
        return await this.listAgentReportsV2(runId);
      } catch (legacyCause) {
        if (!(legacyCause instanceof ApiError) || legacyCause.status !== 404) throw legacyCause;
        return this.listAgentReports(runId);
      }
    }
  }
  getAgentReport(runId: string, agentCode: AgentReportSummary["agent_code"]) {
    return this.request<AgentReportDetail>(`/experience/runs/${runId}/agent-reports/${agentCode}`);
  }
  getAgentReportV2(runId: string, agentCode: AgentReportSummary["agent_code"]) {
    return this.request<SpecialistReportV2Projection>(
      `/experience/v2/runs/${runId}/agent-reports/${agentCode}`,
    );
  }
  getAgentReportV3(runId: string, agentCode: AgentReportSummary["agent_code"]) {
    return this.request<SpecialistReportV3Projection>(
      `/experience/v3/runs/${runId}/agent-reports/${agentCode}`,
    );
  }
  async getAgentReportForDisplay(runId: string, agentCode: AgentReportSummary["agent_code"]): Promise<AgentReportDisplay> {
    try {
      return await this.getAgentReportV3(runId, agentCode);
    } catch (cause) {
      if (!(cause instanceof ApiError) || cause.status !== 404) throw cause;
      try {
        return await this.getAgentReportV2(runId, agentCode);
      } catch (legacyCause) {
        if (!(legacyCause instanceof ApiError) || legacyCause.status !== 404) throw legacyCause;
        return this.getAgentReport(runId, agentCode);
      }
    }
  }
  evidenceReadUrl(evidenceId: string) { return this.request<{ read_url: string; expires_in_seconds: number }>(`/experience/evidence/${evidenceId}/read-url`); }
  compare(projectId: string, runId: string) { return this.request<Record<string, unknown>>(`/experience/projects/${projectId}/compare/${runId}`); }
  extractIntake(rawContent: string, productVersionId?: string) {
    return this.request<{ source: "MODEL_INFERENCE"; model_id: string; extracted_fields: Record<string, string | null>; missing_fields: string[]; confirmation_required: true }>(
      "/intake:extract",
      {
        method: "POST",
        body: JSON.stringify({
          raw_content: rawContent,
          allow_external_processing: true,
          product_version_id: productVersionId,
        }),
      },
      true,
    );
  }
  analyzeVisualPage(payload: {
    file_name: string; page_number: number; image_data_url: string; text_hint: string; local_table_detected: boolean;
  }, variant = "original") {
    return this.request<VisualPageDraft>(
      "/intake:analyze-visual-page",
      { method: "POST", body: JSON.stringify({ ...payload, allow_external_processing: true }) },
      true,
      `visual:${stableAsciiKey(payload.file_name)}:${payload.page_number}:${stableAsciiKey(variant)}`,
    );
  }
  generateValidationTasks(context: string, idempotencyKey?: string) {
    return this.request<{ model_id: string; tasks: ValidationTaskDraft[] }>(
      "/intake:generate-validation-tasks",
      { method: "POST", body: JSON.stringify({ context, allow_external_processing: true }) },
      true,
      idempotencyKey,
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
  getPublicDemoDisclosure(versionId: string) {
    return this.request<{
      product_version_id: string;
      policy_version: string;
      accepted: boolean;
      acceptance_id: string | null;
      accepted_at: string | null;
    }>(`/product-versions/${versionId}/public-demo-disclosure`);
  }
  acceptPublicDemoDisclosure(versionId: string) {
    return this.request<{
      product_version_id: string;
      policy_version: string;
      accepted: true;
      acceptance_id: string;
      accepted_at: string;
    }>(
      `/product-versions/${versionId}/public-demo-disclosure:accept`,
      { method: "POST" },
      true,
      `public-demo-disclosure:${versionId}:public-demo-evidence-v1`,
    );
  }
  async uploadMaterial(versionId: string, file: File, allowExternalProcessing = false): Promise<{
    material_id: string; status: string; object_key: string; sha256: string;
    analysis_id?: string; analysis_status?: string;
  }> {
    const digest = await sha256(file);
    const mimeType = file.type || "application/octet-stream";
    const stableKey = `material:${versionId}:${digest}`;
    const init = await this.request<{ material_id: string; upload_url: string }>(
      `/product-versions/${versionId}/materials:initiate`,
      { method: "POST", body: JSON.stringify({ display_name: file.name, sha256: digest, size_bytes: file.size, mime_type: mimeType }) }, true,
      `${stableKey}:init`,
    );
    // The signed URL remains a local variable until this PUT completes. The
    // browser never receives or stores object-store access credentials.
    const upload = await fetch(init.upload_url, {
      method: "PUT",
      body: file,
      headers: { "Content-Type": mimeType, "x-amz-acl": "private", "x-amz-meta-sha256": digest },
    });
    if (!upload.ok) throw new ApiError(upload.status, { message: "The signed object upload was rejected." });
    return this.request(
      `/materials/${init.material_id}/complete?allow_external_processing=${allowExternalProcessing}`,
      { method: "POST" },
      true,
      `${stableKey}:complete`,
    ) as Promise<{
      material_id: string; status: string; object_key: string; sha256: string;
      analysis_id?: string; analysis_status?: string;
    }>;
  }
  listMaterialAnalyses(versionId: string) {
    return this.request<{
      product_version_id: string;
      items: Array<{
        analysis_id: string;
        material_id: string;
        display_name: string;
        mime_type: string;
        status: "QUEUED" | "PARSING" | "NEEDS_CONSENT" | "READY" | "PARTIAL" | "FAILED" | "EXCLUDED";
        page_count: number;
        unit_count: number;
        coverage: {
          total: number;
          parsed: number;
          visual_inspected: number;
          uncovered_locators: Array<Record<string, unknown>>;
        };
        error_code?: string;
        error_message?: string;
      }>;
    }>(`/product-versions/${versionId}/material-analyses`);
  }
  retryMaterialAnalysis(materialId: string, allowExternalProcessing: boolean) {
    return this.request<{
      analysis_id: string;
      material_id: string;
      status: "QUEUED" | "PARSING" | "NEEDS_CONSENT" | "READY" | "PARTIAL" | "FAILED";
    }>(
      `/materials/${materialId}/analysis:retry`,
      { method: "POST", body: JSON.stringify({ allow_external_processing: allowExternalProcessing }) },
      true,
      boundedIdempotencyKey("material-analysis", materialId, String(allowExternalProcessing)),
    );
  }
  getMaterialSelection(versionId: string) {
    return this.request<{
      selection: null | {
        selection_id: string;
        revision: number;
        items: Array<{
          material_id: string;
          analysis_id: string;
          decision: "INCLUDE" | "INCLUDE_PARTIAL" | "EXCLUDE";
          acknowledged_uncovered_locators: Array<Record<string, unknown>>;
        }>;
      };
    }>(`/product-versions/${versionId}/material-selection`);
  }
  submitMaterialSelection(
    versionId: string,
    items: Array<{
      material_id: string;
      analysis_id: string;
      decision: "INCLUDE" | "INCLUDE_PARTIAL" | "EXCLUDE";
      acknowledged_uncovered_locators: Array<Record<string, unknown>>;
    }>,
    idempotencyKey: string,
  ) {
    return this.request(
      `/product-versions/${versionId}/material-selection`,
      { method: "POST", body: JSON.stringify({ items }) },
      true,
      idempotencyKey,
    );
  }
  gapQuestions(versionId: string) { return this.request<{ correlation_id: string; questions: { field: string; question: string; priority: number }[] }>(`/product-versions/${versionId}/gap-questions`, { method: "POST" }, true); }
  answerGaps(versionId: string, correlationId: string, answers: Record<string, string>) { return this.request(`/product-versions/${versionId}/gap-answers`, { method: "POST", body: JSON.stringify({ correlation_id: correlationId, answers }) }, true); }
  confirmProfile(versionId: string) { return this.request(`/product-versions/${versionId}/profile-confirmations`, { method: "POST", body: JSON.stringify({ acknowledge_model_inference: true }) }, true); }
  plan(
    versionId: string,
    idempotencyKey?: string,
    evaluationMode: "FULL_POTENTIAL" | "USER_VALIDATION" = "FULL_POTENTIAL",
  ) {
    return this.request<{ run_id: string; status: string; correlation_id: string }>(
      `/product-versions/${versionId}/plan`,
      { method: "POST", body: JSON.stringify({ evaluation_mode: evaluationMode }) },
      true,
      idempotencyKey,
    ).then(async planned => {
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
  dispatch(runId: string, idempotencyKey?: string) {
    return this.request<{ run_id: string; status: string; manifest_sha256: string; task_count: number }>(
      `/runs/${runId}/dispatch`, { method: "POST" }, true, idempotencyKey,
    );
  }
  submitSupervisorMessage(
    projectId: string,
    versionId: string,
    message: string,
    allowExternalProcessing: boolean,
    idempotencyKey: string,
  ) {
    return this.request<SupervisorMessageResult>(
      `/projects/${projectId}/versions/${versionId}/supervisor/messages`,
      {
        method: "POST",
        body: JSON.stringify({ message, allow_external_processing: allowExternalProcessing }),
      },
      true,
      idempotencyKey,
    );
  }
  listRunConversations(runId: string, cursor?: string) {
    const query = new URLSearchParams({ limit: "100" });
    if (cursor) query.set("cursor", cursor);
    return this.request<RunConversations>(`/runs/${runId}/conversations?${query.toString()}`);
  }
  submitRunConversationMessage(
    runId: string,
    channel: ConversationChannel,
    message: string,
    allowExternalProcessing: boolean,
    idempotencyKey: string,
  ) {
    return this.request<ConversationReceipt>(
      `/runs/${runId}/conversations/${channel}/messages`,
      {
        method: "POST",
        body: JSON.stringify({ message, allow_external_processing: allowExternalProcessing }),
      },
      true,
      idempotencyKey,
    );
  }
  putUserValidationScript(versionId: string, tasks: ValidationTask[], idempotencyKey?: string) {
    return this.request<{
      script_id: string; revision: number; sha256: string; product_tasks_hash: string; task_count: number;
    }>(
      `/product-versions/${versionId}/user-validation-script`,
      { method: "PUT", body: JSON.stringify({ tasks }) },
      true,
      idempotencyKey,
    );
  }
  registerUserEvidence(versionId: string, payload: Record<string, unknown>) {
    return this.request<{ user_evidence_id: string; claimed_tier: string; sha256: string }>(
      `/product-versions/${versionId}/user-evidence`,
      { method: "POST", body: JSON.stringify(payload) },
      true,
    );
  }
  getUserValidationResult(runId: string) {
    return this.request<UserValidationResult>(`/runs/${runId}/user-validation-result`);
  }
  getUserValidationReport(runId: string, variant: "summary" | "full", format: "html" | "markdown") {
    return this.request<UserValidationReport>(
      `/runs/${runId}/user-validation-reports/${variant}?format=${format}`,
    );
  }
  createUserEvidenceRecheck(runId: string) {
    return this.request<{ run_id: string; status: string; run_kind: string; baseline_run_id: string }>(
      `/runs/${runId}/user-evidence-rechecks`,
      { method: "POST" },
      true,
    );
  }
  listClarifications(runId: string) {
    return this.request<{ run_id: string; items: Clarification[]; correlation_id: string }>(`/runs/${runId}/clarifications`);
  }
  answerClarifications(runId: string, answers: { request_id: string; answer: string }[], idempotencyKey: string) {
    return this.request<ClarificationAnswerResult>(
      `/runs/${runId}/clarifications:answer`,
      { method: "POST", body: JSON.stringify({ answers }) },
      true,
      idempotencyKey,
    );
  }
}

export const browserApi = () => new LaunchScopeApi(sessionFromDocument());

async function sha256(file: File): Promise<string> {
  const bytes = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, "0")).join("");
}
