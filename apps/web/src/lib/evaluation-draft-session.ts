import type { FieldSource } from "./intake-draft.ts";
import type { ValidationTaskDraft } from "./api-client.ts";

export type EvaluationDraftSession = {
  projectId: string;
  mode: "quick" | "structured";
  phase: "collect" | "review" | "questions" | "stage-guidance" | "validation";
  activeSector: number;
  fields: Record<string, string>;
  sources: Record<string, FieldSource>;
  rawContent: string;
  externalConsent: boolean;
  publicDemoDisclosureAccepted?: boolean;
  questions: { field: string; question: string; priority: number }[];
  correlationId: string;
  versionId: string;
  versionLabel: string;
  validationTasks: ValidationTaskDraft[];
  validationDirty: boolean;
  evidenceKind: string;
  evidenceTier: string;
  evidenceSource: string;
  evidenceObservedAt: string;
  evidenceSampleSize: string;
  evidenceSegment: string;
  evidenceObservation: string;
  submittedRunId?: string;
  savedAt: string;
};

export type EvaluationVersionState = {
  activeVersionId: string;
  pending: Promise<string> | null;
};

const PREFIX = "launchscope.evaluation-draft.v1:";

export function evaluationVersionState(activeVersionId = ""): EvaluationVersionState {
  return { activeVersionId, pending: null };
}

export function setEvaluationVersion(state: EvaluationVersionState, versionId: string): void {
  state.activeVersionId = versionId;
}

export async function ensureEvaluationVersion(
  state: EvaluationVersionState,
  create: () => Promise<string>,
): Promise<string> {
  if (state.activeVersionId) return state.activeVersionId;
  if (!state.pending) {
    state.pending = create().then(versionId => {
      state.activeVersionId = versionId;
      return versionId;
    });
  }
  try {
    return await state.pending;
  } finally {
    state.pending = null;
  }
}

export function evaluationDraftKey(projectId: string): string {
  return `${PREFIX}${projectId}`;
}

export function loadEvaluationDraft(
  storage: Pick<Storage, "getItem">,
  projectId: string,
): EvaluationDraftSession | null {
  const raw = storage.getItem(evaluationDraftKey(projectId));
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as EvaluationDraftSession;
    return value.projectId === projectId && value.fields && typeof value.rawContent === "string" ? value : null;
  } catch {
    return null;
  }
}

export function saveEvaluationDraft(
  storage: Pick<Storage, "setItem">,
  draft: EvaluationDraftSession,
): void {
  storage.setItem(evaluationDraftKey(draft.projectId), JSON.stringify(draft));
}

export function resumableEvaluationVersionId(requestedVersionId: string | null): string | null {
  const value = requestedVersionId?.trim() ?? "";
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu.test(value)
    ? value
    : null;
}

export function evaluationVersionUrl(currentUrl: string, versionId: string): string {
  const url = new URL(currentUrl);
  url.searchParams.set("versionId", versionId);
  return url.toString();
}

export function existingRunReturnPath(requestedRunId: string | null): string | null {
  const runId = resumableEvaluationVersionId(requestedRunId);
  return runId ? `/runs/${runId}` : null;
}

export function evaluationPlanIdempotencyKey(versionId: string, requestedRunId: string | null): string {
  const previousRunId = resumableEvaluationVersionId(requestedRunId);
  return previousRunId
    ? `final-review:${versionId}:after:${previousRunId}:plan`
    : `final-review:${versionId}:plan`;
}

export function evaluationValidationScriptIdempotencyKey(
  versionId: string,
  requestedRunId: string | null,
): string {
  const previousRunId = resumableEvaluationVersionId(requestedRunId);
  return previousRunId
    ? `final-review:${versionId}:after:${previousRunId}:script`
    : `final-review:${versionId}:script`;
}

export function shouldReuseValidationDraft(
  requestedRunId: string | null,
  tasks: ValidationTaskDraft[],
): boolean {
  return resumableEvaluationVersionId(requestedRunId) !== null && tasks.length >= 1 && tasks.length <= 5;
}
