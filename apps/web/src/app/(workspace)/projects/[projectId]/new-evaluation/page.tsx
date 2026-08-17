"use client";

import { use, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  boundedIdempotencyKey,
  browserApi,
  type ValidationTaskDraft,
} from "../../../../../lib/api-client";
import type { GapQuestion } from "../../../../../components/profile/ProfileConfirmation";
import {
  buildModelCorpus,
  extractPdfText,
  redactSensitiveText,
  type PdfTextResult,
} from "../../../../../lib/pdf-text";
import {
  ALL_INTAKE_FIELDS,
  completionOf,
  fieldSourceOf,
  INTAKE_SECTIONS,
  mergeExtraction,
  SOURCE_LABELS,
  type FieldSource,
} from "../../../../../lib/intake-draft";
import { buildSectorStates } from "../../../../../lib/wheel-state";
import { EvaluationWheel } from "../../../../../components/workspace/EvaluationWheel";
import { useVoiceCapture, VOICE_STATUS_TEXT } from "../../../../../lib/voice-capture";
import { useI18n } from "../../../../../components/i18n/LocaleProvider";
import { LocalizedErrorMessage } from "../../../../../components/i18n/LocalizedErrorMessage";
import { translateGapQuestion } from "../../../../../lib/i18n";
import { humanizeUserError } from "../../../../../lib/user-report-formatter";
import { executionMode, supervisorAdmissionEnabled } from "../../../../../lib/supervisor-experience";
import {
  ensureEvaluationVersion,
  evaluationPlanIdempotencyKey,
  evaluationValidationScriptIdempotencyKey,
  evaluationVersionState,
  evaluationVersionUrl,
  existingRunReturnPath,
  loadEvaluationDraft,
  resumableEvaluationVersionId,
  saveEvaluationDraft,
  setEvaluationVersion,
  shouldReuseValidationDraft,
  type EvaluationDraftSession,
} from "../../../../../lib/evaluation-draft-session";
import { PublicDemoDisclosure } from "../../../../../components/forms/PublicDemoDisclosure";
import {
  evaluationRouteForStage,
  HIT_PREDICTOR_INTAKE_SEED_KEY,
  HIT_PREDICTOR_STAGES,
  nextPortraitQuestion,
  stageCodeFromProfile,
  type HitPredictorIntakeSeed,
} from "../../../../../lib/hit-predictor-intake";
import { takePendingIntakeFiles } from "../../../../../lib/pending-intake-files";

type Fields = Record<string, string>;
type Phase = "collect" | "review" | "questions" | "validation";
type Mode = "quick" | "structured";
type ServerMaterialAnalysis = {
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
};
type IntakeMaterial = {
  id: string;
  file: File;
  localStatus: "QUEUED" | "PARSING" | "READY" | "FAILED";
  completedPages: number;
  analysis?: PdfTextResult;
  error?: string;
  uploadStatus: "PENDING" | "UPLOADING" | "UPLOADED" | "FAILED";
  uploadError?: string;
  uploaded?: { material_id: string; object_key: string; sha256: string };
  serverAnalysis?: ServerMaterialAnalysis;
};
type TaskGenerationStatus = "IDLE" | "GENERATING" | "READY" | "FAILED";
type MaterialDecision = "INCLUDE_PARTIAL" | "EXCLUDE";

async function mapLimit<T, R>(items: T[], limit: number, task: (item: T) => Promise<R>): Promise<R[]> {
  const results = new Array<R>(items.length);
  let cursor = 0;
  async function worker() {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      results[index] = await task(items[index]);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => worker()));
  return results;
}

function materialId(file: File) {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function locatorLabel(locator: Record<string, unknown>) {
  if (typeof locator.page === "number") return `Page ${locator.page}`;
  if (typeof locator.image === "number") return `Image ${locator.image}`;
  if (typeof locator.section === "string") return locator.section;
  return "Unspecified location";
}

function intakeErrorMessage(message: string, t: (key: string, values?: Record<string, string | number>) => string) {
  if (message.startsWith("the intake model provider returned HTTP ")) {
    return t("The company model endpoint returned HTTP {status}. Continue with the full form; an administrator can inspect the endpoint separately.", { status: message.split(" ").at(-1) ?? "unknown" });
  }
  if (message === "the intake model provider request failed before a usable response") {
    return t("No usable model response was received. Continue with the full form; the original description remains in Quick input.");
  }
  if (message === "the intake model response was truncated before a complete product profile") {
    return t("The model reached its output limit before completing the product profile. Continue with the full form; the original description is preserved.");
  }
  if (
    message === "the intake model provider returned an invalid response envelope"
    || message === "the intake model response did not contain a complete product profile JSON object"
    || message === "the configured model could not produce a valid extraction draft"
  ) {
    return t("The model result could not be organized into a product profile. Continue with the full form; the original description is preserved.");
  }
  return message;
}

export default function NewEvaluationPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const { locale, t } = useI18n();
  const router = useRouter();
  const searchParams = useSearchParams();
  const returnRunId = searchParams.get("returnRunId");
  const rerunFromId = searchParams.get("rerunFromId");
  const existingRunPath = existingRunReturnPath(returnRunId);
  const [projectName, setProjectName] = useState("");
  const [mode, setMode] = useState<Mode>("quick");
  const [activeSector, setActiveSector] = useState(0);
  const [fields, setFields] = useState<Fields>({});
  const [sources, setSources] = useState<Record<string, FieldSource>>({});
  const [rawContent, setRawContent] = useState("");
  const [intakeFiles, setIntakeFiles] = useState<IntakeMaterial[]>([]);
  const [serverAnalyses, setServerAnalyses] = useState<ServerMaterialAnalysis[]>([]);
  const intakeFilesRef = useRef<IntakeMaterial[]>([]);
  const [modelContext, setModelContext] = useState("");
  const [externalConsent, setExternalConsent] = useState(false);
  const [publicDemoDisclosureAccepted, setPublicDemoDisclosureAccepted] = useState(false);
  const [publicDemoDisclosureOpen, setPublicDemoDisclosureOpen] = useState(false);
  const [publicDemoDisclosureSaving, setPublicDemoDisclosureSaving] = useState(false);
  const [publicDemoDisclosureError, setPublicDemoDisclosureError] = useState<string>();
  const [questions, setQuestions] = useState<GapQuestion[]>([]);
  const [correlationId, setCorrelationId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [versionLabel, setVersionLabel] = useState("V1");
  const [phase, setPhase] = useState<Phase>("collect");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [materialDecisions, setMaterialDecisions] = useState<Record<string, MaterialDecision>>({});
  const [draftSecondsRemaining, setDraftSecondsRemaining] = useState<number | null>(null);
  const [validationTasks, setValidationTasks] = useState<ValidationTaskDraft[]>([]);
  const [taskGenerationStatus, setTaskGenerationStatus] = useState<TaskGenerationStatus>("IDLE");
  const [taskGenerationError, setTaskGenerationError] = useState<string>();
  const [validationDirty, setValidationDirty] = useState(false);
  const [evidenceFile, setEvidenceFile] = useState<File>();
  const [evidenceKind, setEvidenceKind] = useState("interview");
  const [evidenceTier, setEvidenceTier] = useState("E3");
  const [evidenceSource, setEvidenceSource] = useState("");
  const [evidenceObservedAt, setEvidenceObservedAt] = useState("");
  const [evidenceSampleSize, setEvidenceSampleSize] = useState("");
  const [evidenceSegment, setEvidenceSegment] = useState("");
  const [evidenceObservation, setEvidenceObservation] = useState("");
  const rawRef = useRef<HTMLTextAreaElement>(null);
  const parseChain = useRef<Promise<void>>(Promise.resolve());
  const versionState = useRef(evaluationVersionState());
  const externalConsentRef = useRef(externalConsent);
  const publicDemoDisclosureAcceptedRef = useRef(false);
  const publicDemoDisclosureVersionRef = useRef("");
  const publicDemoDisclosureWaiters = useRef<Array<() => void>>([]);
  const skipInitialDraftWrite = useRef(true);
  const pendingTransferStarted = useRef("");

  const voice = useVoiceCapture(text => {
    setRawContent(current => (current ? `${current} ${text}` : text));
  }, locale);

  const completion = useMemo(() => completionOf(fields), [fields]);
  const sectors = useMemo(() => buildSectorStates(fields), [fields]);
  const stageCode = useMemo(() => stageCodeFromProfile(fields.stage ?? ""), [fields.stage]);
  const preliminaryPrediction = Boolean(
    stageCode && evaluationRouteForStage(stageCode) !== "FORMAL_EVALUATION",
  );

  function draftValue(overrides: Partial<EvaluationDraftSession> = {}): EvaluationDraftSession {
    return {
      projectId,
      mode,
      phase,
      activeSector,
      fields,
      sources,
      rawContent,
      externalConsent,
      publicDemoDisclosureAccepted,
      questions,
      correlationId,
      versionId,
      versionLabel,
      validationTasks,
      validationDirty,
      evidenceKind,
      evidenceTier,
      evidenceSource,
      evidenceObservedAt,
      evidenceSampleSize,
      evidenceSegment,
      evidenceObservation,
      savedAt: new Date().toISOString(),
      ...overrides,
    };
  }

  useEffect(() => {
    const draft = loadEvaluationDraft(window.sessionStorage, projectId);
    const resumedVersionId = resumableEvaluationVersionId(searchParams.get("versionId"));
    if (draft) {
      setMode(draft.mode);
      setPhase(draft.phase === "stage-guidance" ? "review" : draft.phase);
      setActiveSector(draft.activeSector);
      setFields(draft.fields);
      setSources(draft.sources);
      setRawContent(draft.rawContent);
      setExternalConsent(draft.externalConsent);
      externalConsentRef.current = draft.externalConsent;
      const disclosureAccepted = Boolean(draft.publicDemoDisclosureAccepted);
      setPublicDemoDisclosureAccepted(disclosureAccepted);
      publicDemoDisclosureAcceptedRef.current = disclosureAccepted;
      setQuestions(draft.questions);
      setCorrelationId(draft.correlationId);
      const restoredVersionId = resumedVersionId ?? draft.versionId;
      setEvaluationVersion(versionState.current, restoredVersionId);
      setVersionId(restoredVersionId);
      setVersionLabel(draft.versionLabel);
      setValidationTasks(draft.validationTasks);
      setValidationDirty(draft.validationDirty);
      setTaskGenerationStatus(draft.validationTasks.length ? "READY" : "IDLE");
      setEvidenceKind(draft.evidenceKind);
      setEvidenceTier(draft.evidenceTier);
      setEvidenceSource(draft.evidenceSource);
      setEvidenceObservedAt(draft.evidenceObservedAt);
      setEvidenceSampleSize(draft.evidenceSampleSize);
      setEvidenceSegment(draft.evidenceSegment);
      setEvidenceObservation(draft.evidenceObservation);
    } else if (resumedVersionId) {
      setEvaluationVersion(versionState.current, resumedVersionId);
      setVersionId(resumedVersionId);
      void browserApi().getProjectPortrait(projectId).then(portrait => {
        if (portrait.product_version_id !== resumedVersionId) return;
        const confirmedFields = portrait.confirmed_fields;
        setFields(confirmedFields);
        setSources(Object.fromEntries(Object.keys(confirmedFields).map(key => [key, "user"])));
        setPhase("review");
      }).catch(() => undefined);
    } else {
      const seedRaw = window.sessionStorage.getItem(HIT_PREDICTOR_INTAKE_SEED_KEY);
      if (seedRaw) {
        try {
          const seed = JSON.parse(seedRaw) as HitPredictorIntakeSeed;
          const stage = HIT_PREDICTOR_STAGES.find(item => item.code === seed.stage);
          const seededFields = {
            stage: stage?.label ?? "",
            inspectable_materials: seed.referenceUrl ?? "",
          };
          setRawContent(seed.description ?? "");
          setFields(seededFields);
          setSources(Object.fromEntries(Object.keys(seededFields).filter(key => seededFields[key as keyof typeof seededFields]).map(key => [key, "user"])));
          window.sessionStorage.removeItem(HIT_PREDICTOR_INTAKE_SEED_KEY);
        } catch {
          window.sessionStorage.removeItem(HIT_PREDICTOR_INTAKE_SEED_KEY);
        }
      }
    }
    const intakeTransferId = searchParams.get("intakeTransfer") ?? "";
    if (intakeTransferId && pendingTransferStarted.current !== intakeTransferId) {
      pendingTransferStarted.current = intakeTransferId;
      const nextSearchParams = new URLSearchParams(searchParams.toString());
      nextSearchParams.delete("intakeTransfer");
      const nextQuery = nextSearchParams.toString();
      const cleanPath = `/projects/${projectId}/new-evaluation${nextQuery ? `?${nextQuery}` : ""}`;
      void takePendingIntakeFiles(intakeTransferId).then(files => {
        router.replace(cleanPath);
        if (!files.length) {
          setError(t("Selected materials could not be restored. Choose them again."));
          return;
        }
        addFiles(files);
      }).catch(() => {
        router.replace(cleanPath);
        setError(t("Selected materials could not be restored. Choose them again."));
      });
    }
    void browserApi().listProjects().then(result => {
      setProjectName(result.items.find(item => item.project_id === projectId)?.name ?? "");
    }).catch(() => undefined);
  }, [projectId, router, searchParams, t]);

  useEffect(() => {
    if (!versionId) return;
    let active = true;
    void browserApi().listMaterialAnalyses(versionId).then(result => {
      if (active) setServerAnalyses(result.items);
    }).catch(() => undefined);
    return () => { active = false; };
  }, [versionId]);

  useEffect(() => {
    if (skipInitialDraftWrite.current) {
      skipInitialDraftWrite.current = false;
      return;
    }
    saveEvaluationDraft(window.sessionStorage, draftValue());
  }, [
    activeSector, correlationId, evidenceKind, evidenceObservation, evidenceObservedAt, evidenceSampleSize,
    evidenceSegment, evidenceSource, evidenceTier, externalConsent, fields, mode, phase, projectId,
    publicDemoDisclosureAccepted, questions,
    rawContent, sources, validationDirty, validationTasks, versionId, versionLabel,
  ]);

  function update(key: string, value: string) {
    setFields(current => ({ ...current, [key]: value }));
    setSources(current => ({ ...current, [key]: "user" }));
  }

  function setExternalProcessingConsent(value: boolean) {
    externalConsentRef.current = value;
    setExternalConsent(value);
    if (value && versionId) {
      void retryConsentBlockedAnalyses(versionId).catch(cause => {
        setError(cause instanceof Error ? cause.message : t("Material analysis failed"));
      });
    }
  }

  function updateIntakeFiles(updater: (current: IntakeMaterial[]) => IntakeMaterial[]) {
    const next = updater(intakeFilesRef.current);
    intakeFilesRef.current = next;
    setIntakeFiles(next);
  }

  function patchMaterial(id: string, patch: Partial<IntakeMaterial>) {
    updateIntakeFiles(current => current.map(item => item.id === id ? { ...item, ...patch } : item));
  }

  function showServerAnalyses(items: ServerMaterialAnalysis[]) {
    setServerAnalyses(items);
    updateIntakeFiles(current => current.map(item => ({
      ...item,
      serverAnalysis: items.find(analysis => analysis.material_id === item.uploaded?.material_id),
    })));
  }

  async function monitorMaterialAnalyses(activeVersion: string) {
    const api = browserApi();
    for (let attempt = 0; attempt < 120; attempt += 1) {
      const analyses = (await api.listMaterialAnalyses(activeVersion)).items;
      showServerAnalyses(analyses);
      if (!analyses.some(item => ["QUEUED", "PARSING"].includes(item.status))) return analyses;
      await new Promise(resolve => window.setTimeout(resolve, 2_000));
    }
    return (await api.listMaterialAnalyses(activeVersion)).items;
  }

  async function parseMaterial(item: IntakeMaterial) {
    patchMaterial(item.id, { localStatus: "PARSING", error: undefined, completedPages: 0 });
    const mime = item.file.type || "application/octet-stream";
    if (mime !== "application/pdf") {
      patchMaterial(item.id, { localStatus: "READY", completedPages: 1 });
      return;
    }
    try {
      const analysis = await extractPdfText(item.file, completedPages => {
        patchMaterial(item.id, { completedPages });
      });
      patchMaterial(item.id, { localStatus: "READY", analysis, completedPages: analysis.pageCount });
    } catch (cause) {
      patchMaterial(item.id, {
        localStatus: "FAILED",
        error: cause instanceof Error ? cause.message : t("PDF text extraction failed. Paste the text to continue."),
      });
    }
  }

  function addFiles(selected: File[]) {
    setError(undefined);
    const existing = new Set(intakeFilesRef.current.map(item => item.id));
    const additions = selected
      .filter(file => !existing.has(materialId(file)))
      .map(file => ({
        id: materialId(file),
        file,
        localStatus: "QUEUED" as const,
        completedPages: 0,
        uploadStatus: "PENDING" as const,
      }));
    if (!additions.length) return;
    updateIntakeFiles(current => [...current, ...additions]);
    parseChain.current = parseChain.current.then(async () => {
      await mapLimit(additions, 2, parseMaterial);
    });
    void uploadNewMaterials(additions);
  }

  function retryMaterial(item: IntakeMaterial) {
    parseChain.current = parseChain.current.then(() => parseMaterial(item));
  }

  async function ensureActiveVersion() {
    return ensureEvaluationVersion(versionState.current, async () => {
      const api = browserApi();
      const existing = await api.listRuns(projectId);
      const nextLabel = `V${existing.items.length + 1}`;
      setVersionLabel(nextLabel);
      const created = await api.createVersion(projectId, nextLabel);
      setEvaluationVersion(versionState.current, created.product_version_id);
      setVersionId(created.product_version_id);
      saveEvaluationDraft(window.sessionStorage, draftValue({ versionId: created.product_version_id }));
      window.history.replaceState(
        window.history.state,
        "",
        evaluationVersionUrl(window.location.href, created.product_version_id),
      );
      return created.product_version_id;
    });
  }

  function markPublicDemoDisclosureAccepted() {
    publicDemoDisclosureAcceptedRef.current = true;
    setPublicDemoDisclosureAccepted(true);
  }

  async function ensurePublicDemoDisclosure(activeVersion: string) {
    if (publicDemoDisclosureAcceptedRef.current) return;
    publicDemoDisclosureVersionRef.current = activeVersion;
    const status = await browserApi().getPublicDemoDisclosure(activeVersion);
    if (status.accepted) {
      markPublicDemoDisclosureAccepted();
      return;
    }
    setPublicDemoDisclosureError(undefined);
    setPublicDemoDisclosureOpen(true);
    await new Promise<void>(resolve => publicDemoDisclosureWaiters.current.push(resolve));
  }

  async function acceptPublicDemoDisclosure() {
    const activeVersion = publicDemoDisclosureVersionRef.current || versionId;
    if (!activeVersion || publicDemoDisclosureSaving) return;
    setPublicDemoDisclosureSaving(true);
    setPublicDemoDisclosureError(undefined);
    try {
      await browserApi().acceptPublicDemoDisclosure(activeVersion);
      markPublicDemoDisclosureAccepted();
      setPublicDemoDisclosureOpen(false);
      publicDemoDisclosureWaiters.current.splice(0).forEach(resolve => resolve());
    } catch (cause) {
      setPublicDemoDisclosureError(
        cause instanceof Error ? cause.message : t("Public Demo disclosure could not be recorded."),
      );
    } finally {
      setPublicDemoDisclosureSaving(false);
    }
  }

  async function uploadNewMaterials(items: IntakeMaterial[]) {
    try {
      const activeVersion = await ensureActiveVersion();
      await ensurePublicDemoDisclosure(activeVersion);
      const api = browserApi();
      await mapLimit(items, 2, async item => {
        patchMaterial(item.id, { uploadStatus: "UPLOADING", uploadError: undefined });
        try {
          const uploaded = await api.uploadMaterial(activeVersion, item.file, false);
          patchMaterial(item.id, { uploadStatus: "UPLOADED", uploaded });
        } catch (cause) {
          patchMaterial(item.id, {
            uploadStatus: "FAILED",
            uploadError: cause instanceof Error ? cause.message : t("Material upload failed"),
          });
        }
      });
      const analyses = await monitorMaterialAnalyses(activeVersion);
      if (externalConsentRef.current && analyses.some(item => item.status === "NEEDS_CONSENT")) {
        await retryConsentBlockedAnalyses(activeVersion);
      }
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : t("Material upload failed");
      items.forEach(item => patchMaterial(item.id, { uploadStatus: "FAILED", uploadError: message }));
    }
  }

  async function retryConsentBlockedAnalyses(activeVersion: string) {
    const api = browserApi();
    const analyses = (await api.listMaterialAnalyses(activeVersion)).items;
    const blocked = analyses.filter(item => item.status === "NEEDS_CONSENT");
    for (const item of blocked) {
      await api.retryMaterialAnalysis(item.material_id, true);
      for (let attempt = 0; attempt < 120; attempt += 1) {
        const current = (await api.listMaterialAnalyses(activeVersion)).items;
        const latest = current.find(analysis => analysis.material_id === item.material_id);
        showServerAnalyses(current);
        if (latest && !["QUEUED", "PARSING"].includes(latest.status)) break;
        await new Promise(resolve => window.setTimeout(resolve, 2_000));
      }
    }
    if (blocked.length) void monitorMaterialAnalyses(activeVersion);
  }

  async function retryAuthoritativeAnalysis(item: IntakeMaterial) {
    if (!item.uploaded || !versionId) return;
    setBusy(true);
    setError(undefined);
    try {
      await browserApi().retryMaterialAnalysis(item.uploaded.material_id, externalConsent);
      patchMaterial(item.id, {
        serverAnalysis: item.serverAnalysis ? { ...item.serverAnalysis, status: "QUEUED" } : undefined,
      });
      void monitorMaterialAnalyses(versionId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Material analysis failed"));
    } finally {
      setBusy(false);
    }
  }

  async function uploadAnalyzeAndConfirmMaterials(activeVersion: string) {
    await ensurePublicDemoDisclosure(activeVersion);
    const api = browserApi();
    let analyses = (await api.listMaterialAnalyses(activeVersion)).items;
    if (rawContent.trim() && !analyses.some(item => item.display_name === "product-intake.txt")) {
      await api.uploadMaterial(
        activeVersion,
        new File([rawContent.trim()], "product-intake.txt", { type: "text/plain" }),
        externalConsent,
      );
    }
    const uploadResults = await mapLimit(intakeFilesRef.current, 2, async item => {
      if (item.uploadStatus === "UPLOADING") {
        for (let attempt = 0; attempt < 60 && intakeFilesRef.current.find(candidate => candidate.id === item.id)?.uploadStatus === "UPLOADING"; attempt += 1) {
          await new Promise(resolve => window.setTimeout(resolve, 500));
        }
      }
      const currentItem = intakeFilesRef.current.find(candidate => candidate.id === item.id) ?? item;
      if (currentItem.uploadStatus === "UPLOADED" && currentItem.uploaded) return null;
      patchMaterial(item.id, { uploadStatus: "UPLOADING", uploadError: undefined });
      try {
        const uploaded = currentItem.uploaded ?? await api.uploadMaterial(activeVersion, item.file, false);
        patchMaterial(item.id, { uploadStatus: "UPLOADED", uploaded });
        return null;
      } catch (cause) {
        const uploadError = cause instanceof Error ? cause.message : t("Material upload failed");
        patchMaterial(item.id, { uploadStatus: "FAILED", uploadError });
        return uploadError;
      }
    });
    if (uploadResults.some(Boolean)) {
      throw new Error(t("Some files failed to upload. Successful files were preserved; retry to continue."));
    }
    analyses = (await api.listMaterialAnalyses(activeVersion)).items;
    if (externalConsent) {
      const consentBlocked = analyses.filter(item => item.status === "NEEDS_CONSENT");
      if (consentBlocked.length) {
        await mapLimit(consentBlocked, 2, item => api.retryMaterialAnalysis(item.material_id, true));
        analyses = (await api.listMaterialAnalyses(activeVersion)).items;
      }
    }
    showServerAnalyses(analyses);
    for (let attempt = 0; attempt < 120 && analyses.some(item => ["QUEUED", "PARSING"].includes(item.status)); attempt += 1) {
      await new Promise(resolve => window.setTimeout(resolve, 2_000));
      analyses = (await api.listMaterialAnalyses(activeVersion)).items;
      showServerAnalyses(analyses);
    }
    if (analyses.some(item => ["QUEUED", "PARSING"].includes(item.status))) {
      throw new Error(t("Material analysis is still running. Please retry shortly."));
    }
    const existingSelection = await api.getMaterialSelection(activeVersion);
    const selectedAnalysisIds = new Set(existingSelection.selection?.items.map(item => item.analysis_id) ?? []);
    if (selectedAnalysisIds.size === analyses.length && analyses.every(item => selectedAnalysisIds.has(item.analysis_id))) {
      return;
    }
    const unresolved: string[] = [];
    const decisions = analyses.map(item => {
      if (materialDecisions[item.analysis_id] === "EXCLUDE") {
        return {
          material_id: item.material_id,
          analysis_id: item.analysis_id,
          decision: "EXCLUDE" as const,
          acknowledged_uncovered_locators: [],
        };
      }
      if (item.status === "READY") {
        return {
          material_id: item.material_id,
          analysis_id: item.analysis_id,
          decision: "INCLUDE" as const,
          acknowledged_uncovered_locators: [],
        };
      }
      if (item.status === "PARTIAL") {
        const decision = materialDecisions[item.analysis_id];
        if (!decision) unresolved.push(item.display_name);
        return {
          material_id: item.material_id,
          analysis_id: item.analysis_id,
          decision: decision ?? "EXCLUDE",
          acknowledged_uncovered_locators: decision === "INCLUDE_PARTIAL" ? item.coverage.uncovered_locators : [],
        };
      }
      const decision = materialDecisions[item.analysis_id];
      if (decision !== "EXCLUDE") unresolved.push(item.display_name);
      return {
        material_id: item.material_id,
        analysis_id: item.analysis_id,
        decision: "EXCLUDE" as const,
        acknowledged_uncovered_locators: [],
      };
    });
    if (unresolved.length) {
      throw new Error(t("Review and decide every coverage gap below before continuing: {names}", { names: unresolved.join(", ") }));
    }
    if (decisions.length) {
      await api.submitMaterialSelection(
        activeVersion,
        decisions,
        boundedIdempotencyKey("material-selection", activeVersion, JSON.stringify(decisions)),
      );
    }
  }

  async function prepareDraft() {
    setBusy(true);
    setError(undefined);
    try {
      const api = browserApi();
      const activeVersion = await ensureActiveVersion();
      await parseChain.current;
      const currentFiles = intakeFilesRef.current;
      const failed = currentFiles.filter(item => item.localStatus === "FAILED");
      if (failed.length) throw new Error(t("Retry or remove failed files before continuing."));
      const material = buildModelCorpus(
        rawContent.trim(),
        currentFiles.flatMap(item => item.analysis ? [item.analysis] : []),
      ).text;
      setModelContext(material);
      await uploadAnalyzeAndConfirmMaterials(activeVersion);
      if (material) {
        if (!externalConsent) throw new Error(t("Before AI extraction, explicitly allow this material to be sent to the configured model service."));
        setDraftSecondsRemaining(120);
        const timer = window.setInterval(() => {
          setDraftSecondsRemaining(current => current === null ? null : Math.max(0, current - 1));
        }, 1_000);
        try {
          const extraction = await api.extractIntake(material, activeVersion);
          const merged = mergeExtraction(fields, extraction.extracted_fields);
          setFields(merged.fields);
          setSources(merged.sources);
        } finally {
          window.clearInterval(timer);
          setDraftSecondsRemaining(null);
        }
      }
      setPhase("review");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : t("Material extraction failed");
      setError(intakeErrorMessage(message, t));
    } finally {
      setBusy(false);
    }
  }

  async function continueWithoutModelDraft() {
    setBusy(true);
    setError(undefined);
    try {
      const activeVersion = await ensureActiveVersion();
      await parseChain.current;
      if (intakeFilesRef.current.some(item => item.localStatus === "FAILED")) {
        throw new Error(t("Retry or remove failed files before continuing."));
      }
      await uploadAnalyzeAndConfirmMaterials(activeVersion);
      setPhase("review");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Material extraction failed"));
    } finally {
      setBusy(false);
    }
  }

  async function commitProfile() {
    setBusy(true);
    setError(undefined);
    try {
      const api = browserApi();
      const activeVersion = await ensureActiveVersion();
      setEvaluationVersion(versionState.current, activeVersion);
      setVersionId(activeVersion);
      await uploadAnalyzeAndConfirmMaterials(activeVersion);
      if (existingRunPath) {
        router.push(existingRunPath);
        return;
      }
      const gaps = await api.gapQuestions(activeVersion);
      setCorrelationId(gaps.correlation_id);
      setQuestions(gaps.questions);
      const answerable = Object.fromEntries(
        gaps.questions
          .filter(question => fields[question.field]?.trim())
          .map(question => [question.field, fields[question.field].trim()]),
      );
      if (Object.keys(answerable).length) await api.answerGaps(activeVersion, gaps.correlation_id, answerable);
      const unresolved = gaps.questions.filter(question => !answerable[question.field]);
      setQuestions(unresolved);
      if (unresolved.length) {
        setPhase("questions");
        return;
      }
      await api.confirmProfile(activeVersion);
      await continueAfterProfile(activeVersion);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Product profile confirmation failed"));
    } finally {
      setBusy(false);
    }
  }

  async function answerCurrentQuestion() {
    setBusy(true);
    setError(undefined);
    try {
      const current = nextPortraitQuestion(questions, {});
      if (!current) return;
      const answer = fields[current.field]?.trim();
      if (!answer) throw new Error(t("Answer the current question or choose an explicit unknown option."));
      await browserApi().answerGaps(versionId, correlationId, { [current.field]: answer });
      const remaining = questions.filter(question => question.field !== current.field);
      setQuestions(remaining);
      if (remaining.length) return;
      await browserApi().confirmProfile(versionId);
      await continueAfterProfile(versionId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Additional information submission failed"));
    } finally {
      setBusy(false);
    }
  }

  async function continueAfterProfile(activeVersion: string) {
    if (!stageCode) {
      setError(t("Choose one of the listed product stages before starting a prediction."));
      setPhase("review");
      return;
    }
    if (shouldReuseValidationDraft(rerunFromId, validationTasks)) {
      setTaskGenerationStatus("READY");
      setTaskGenerationError(undefined);
      setPhase("validation");
      return;
    }
    await generateTaskDrafts(activeVersion);
    setPhase("validation");
  }

  async function generateTaskDrafts(activeVersion = versionId) {
    setTaskGenerationStatus("GENERATING");
    setTaskGenerationError(undefined);
    try {
      const context = buildModelCorpus(
        JSON.stringify({ product_description: rawContent, product_profile: fields }, null, 2),
        intakeFilesRef.current.flatMap(item => item.analysis ? [item.analysis] : []),
      ).text || modelContext;
      const result = await browserApi().generateValidationTasks(
        context,
        `validation-draft:${activeVersion}:${validationDirty ? "regenerate" : "initial"}`,
      );
      setValidationTasks(result.tasks);
      setValidationDirty(false);
      setTaskGenerationStatus("READY");
    } catch (cause) {
      setTaskGenerationStatus(validationTasks.length ? "READY" : "FAILED");
      setTaskGenerationError(cause instanceof Error ? cause.message : t("Validation task generation failed"));
    }
  }

  function updateValidationTask(index: number, patch: Partial<ValidationTaskDraft>) {
    setValidationTasks(current => current.map((item, itemIndex) => (
      itemIndex === index ? { ...item, ...patch } : item
    )));
    setValidationDirty(true);
  }

  async function confirmValidationAndPlan() {
    setBusy(true);
    setError(undefined);
    try {
      if (!supervisorAdmissionEnabled()) {
        throw new Error(t("Recorded mode is read-only. Start LaunchScope in Material or Live mode to run an evaluation."));
      }
      if (!stageCode) {
        throw new Error(t("Choose one of the listed product stages before starting a prediction."));
      }
      const tasks = validationTasks.map(item => ({
        task_key: item.task_key.trim(),
        description: item.description.trim(),
        expected_observable_outcome: item.expected_observable_outcome.trim(),
        max_steps: item.max_steps,
      }));
      if (tasks.some(item => !item.task_key || !item.description || !item.expected_observable_outcome)) {
        throw new Error(t("Each core task requires a task key, action description, and observable outcome."));
      }
      const api = browserApi();
      await api.putUserValidationScript(
        versionId,
        tasks,
        evaluationValidationScriptIdempotencyKey(versionId, rerunFromId),
      );
      if (evidenceFile) {
        if (!evidenceSource.trim() || !evidenceObservedAt || !evidenceObservation.trim()) {
          throw new Error(t("Real user evidence requires a source, observation time, and aggregate observation."));
        }
        const uploaded = await api.uploadMaterial(versionId, evidenceFile);
        await api.registerUserEvidence(versionId, {
          object_key: uploaded.object_key,
          sha256: uploaded.sha256,
          kind: evidenceKind,
          claimed_tier: evidenceTier,
          source: evidenceSource.trim(),
          observed_at: new Date(evidenceObservedAt).toISOString(),
          expires_at: null,
          sample_size: evidenceSampleSize ? Number(evidenceSampleSize) : null,
          segment: evidenceSegment.trim() || null,
          aggregate_observation: evidenceObservation.trim(),
          applicability: {},
          supporting_claim_refs: [],
          contradicting_claim_refs: [],
        });
      }
      const run = await api.plan(
        versionId,
        evaluationPlanIdempotencyKey(versionId, rerunFromId),
        preliminaryPrediction ? "USER_VALIDATION" : "FULL_POTENTIAL",
      );
      await api.dispatch(run.run_id, `final-review:${run.run_id}:dispatch`);
      const durable = await api.getRun(run.run_id);
      if (durable.status !== "RUNNING" || durable.current_stage !== "LEADER_PLANNING") {
        throw new Error(t("The evaluation did not durably enter supervisor planning."));
      }
      saveEvaluationDraft(window.sessionStorage, draftValue({
        phase: "review",
        versionId: "",
        submittedRunId: run.run_id,
      }));
      setPhase("review");
      setEvaluationVersion(versionState.current, "");
      setVersionId("");
      router.push(`/runs/${run.run_id}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("User-validation script submission failed"));
    } finally {
      setBusy(false);
    }
  }

  const fileParsing = intakeFiles.some(item => item.localStatus === "QUEUED" || item.localStatus === "PARSING");
  const hasMaterial = Boolean(rawContent.trim() || intakeFiles.length);
  const allLocalReady = intakeFiles.length > 0 && intakeFiles.every(item => ["READY", "FAILED"].includes(item.localStatus));
  const allUploaded = intakeFiles.length > 0 && intakeFiles.every(item => item.uploadStatus === "UPLOADED");
  const serverTerminal = intakeFiles.length > 0 && intakeFiles.every(item => item.serverAnalysis && !["QUEUED", "PARSING"].includes(item.serverAnalysis.status));
  const serverHasGaps = intakeFiles.some(item => (item.serverAnalysis?.coverage.uncovered_locators.length ?? 0) > 0);
  const localMaterialIds = new Set(intakeFiles.flatMap(item => item.uploaded ? [item.uploaded.material_id] : []));
  const persistedAnalyses = serverAnalyses.filter(item => (
    !localMaterialIds.has(item.material_id) && item.display_name !== "product-intake.txt"
  ));
  const pipelineStages = [
    { label: t("Local preview"), detail: t("At most two preview pages"), state: fileParsing ? "active" : allLocalReady ? "done" : "pending" },
    { label: t("Private upload"), detail: t("Original file stored privately"), state: intakeFiles.some(item => item.uploadStatus === "UPLOADING") ? "active" : allUploaded ? "done" : "pending" },
    { label: t("Server parsing"), detail: t("Authoritative text and structure"), state: intakeFiles.some(item => ["QUEUED", "PARSING"].includes(item.serverAnalysis?.status ?? "")) ? "active" : serverTerminal ? "done" : allUploaded ? "active" : "pending" },
    { label: t("Visual analysis"), detail: externalConsent ? t("Authorized server-side vision") : t("Waiting for consent"), state: serverHasGaps && serverTerminal ? "attention" : serverTerminal ? "done" : externalConsent && allUploaded ? "active" : "pending" },
    { label: t("Profile draft"), detail: draftSecondsRemaining === null ? t("Editable, non-authoritative") : t("Model timeout in {seconds}s", { seconds: draftSecondsRemaining }), state: draftSecondsRemaining !== null ? "active" : phase !== "collect" ? "done" : "pending" },
  ];
  const dispatchEnabled = supervisorAdmissionEnabled();
  const modeLabel = executionMode();

  const phaseLabel = phase === "collect"
    ? t("Add details")
    : phase === "review"
      ? t("Review")
      : phase === "questions"
        ? t("Clarify")
        : t("Define validation tasks");

  return (
    <main className="workspace-main workspace-main-tall">
      <PublicDemoDisclosure
        open={publicDemoDisclosureOpen}
        busy={publicDemoDisclosureSaving}
        error={publicDemoDisclosureError}
        onAccept={() => { void acceptPublicDemoDisclosure(); }}
      />
      {projectName && <p className="evaluation-target-banner"><strong>{t("Prediction target")}</strong>{projectName}</p>}
      <div className="wheel-stage" data-docked="true">
        <div className="wheel-pane">
          <div className="wheel-frame instrument-shell">
            <EvaluationWheel
              architectureGeneration="supervisor-1p4-v1"
              sectors={sectors}
              activeSector={activeSector}
              onSelectSector={index => {
                setActiveSector(index);
                if (phase === "collect" && mode === "quick") setMode("structured");
              }}
            />
            <div className="wheel-core-static">
              <span className="core-cta-label">{phaseLabel}</span>
              {versionLabel !== "V1" && <span className="core-stage-read">{versionLabel}</span>}
            </div>
          </div>
        </div>

        <div className="wheel-side">
          {phase === "collect" && (
            <section className="dock-panel dock-enter" aria-label={t("Product intake")}>
              <div className="dock-head">
                <span className="bearing">{t("Step 2 of 3 · {filled} / {total} completed", { filled: completion.filled, total: completion.total })}</span>
                <h2>{t("Tell us what your product does")}</h2>
                <p>{t("Speak or paste an introduction, or complete each section. Both modes update the same profile.")}</p>
              </div>

              <div className="intake-mode-switch" role="tablist" aria-label={t("Input mode")}>
                <button
                  role="tab"
                  id="mode-tab-quick"
                  aria-selected={mode === "quick"}
                  aria-controls="mode-panel-quick"
                  tabIndex={mode === "quick" ? 0 : -1}
                  onClick={() => setMode("quick")}
                  onKeyDown={event => {
                    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
                      event.preventDefault();
                      setMode("structured");
                      document.getElementById("mode-tab-structured")?.focus();
                    }
                  }}
                >
                  {t("Quick input")}
                </button>
                <button
                  role="tab"
                  id="mode-tab-structured"
                  aria-selected={mode === "structured"}
                  aria-controls="mode-panel-structured"
                  tabIndex={mode === "structured" ? 0 : -1}
                  onClick={() => setMode("structured")}
                  onKeyDown={event => {
                    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
                      event.preventDefault();
                      setMode("quick");
                      document.getElementById("mode-tab-quick")?.focus();
                    }
                  }}
                >
                  {t("Full form")}
                </button>
              </div>

              {mode === "quick" && (
                <div id="mode-panel-quick" role="tabpanel" aria-labelledby="mode-tab-quick" className="quick-capture">
                  <label>
                    <span className="field-name">{t("Product URL")}</span>
                    <span className="field-hint">{t("Enter the exact product page the evaluation team may inspect.")}</span>
                    <input
                      type="url"
                      value={fields.inspectable_materials ?? ""}
                      onChange={event => update("inspectable_materials", event.target.value)}
                      placeholder="https://creatrades.com"
                    />
                  </label>
                  <label>
                    <span className="field-name">{t("Describe the product in your own words")}</span>
                    <span className="field-hint">{t("What problem does it solve, who is it for, and how far has it progressed? Write whatever comes to mind.")}</span>
                    <textarea
                      ref={rawRef}
                      value={voice.interim ? `${rawContent}${rawContent ? " " : ""}${voice.interim}` : rawContent}
                      onChange={event => setRawContent(event.target.value)}
                      placeholder={t("Paste a product introduction, interview notes, or a README…")}
                    />
                  </label>

                  <div className="capture-tools">
                    {voice.state !== "unsupported" && (
                      <button
                        type="button"
                        className="secondary voice-button"
                        data-recording={voice.state === "recording"}
                        onClick={() => (voice.state === "recording" ? voice.stop() : voice.start())}
                      >
                        {voice.state === "recording" ? t("Stop recording") : t("Start speaking")}
                      </button>
                    )}
                    <label
                      className="drop-zone"
                      style={{ margin: 0, flex: 1, minWidth: 180 }}
                      onDragOver={event => event.preventDefault()}
                      onDrop={event => {
                        event.preventDefault();
                        addFiles([...event.dataTransfer.files]);
                      }}
                    >
                      <input
                        type="file"
                        multiple
                        accept=".pdf,.doc,.docx,.txt,image/*"
                        onChange={event => {
                          addFiles([...(event.target.files ?? [])]);
                          event.currentTarget.value = "";
                        }}
                      />
                      <span>{t("Drop or choose documents (PDF / document / image)")}</span>
                    </label>
                  </div>
                  {VOICE_STATUS_TEXT[voice.state] && (
                    <p
                      className="voice-status"
                      role="status"
                      data-tone={voice.state === "denied" || voice.state === "error" ? "error" : undefined}
                    >
                      {t(VOICE_STATUS_TEXT[voice.state])}
                    </p>
                  )}

                  {intakeFiles.length > 0 && (
                    <ol className="material-pipeline" aria-label={t("Material processing progress")}>
                      {pipelineStages.map((stage, index) => (
                        <li key={stage.label} data-state={stage.state}>
                          <span className="pipeline-index">{String(index + 1).padStart(2, "0")}</span>
                          <span><strong>{stage.label}</strong><small>{stage.detail}</small></span>
                        </li>
                      ))}
                    </ol>
                  )}

                  {intakeFiles.length > 0 && (
                    <ul className="material-analysis-list" aria-label={t("Added materials")}>
                      {intakeFiles.map(item => {
                        const pages = item.analysis?.pages ?? [];
                        const tablePages = pages.filter(page => page.table.status !== "NOT_DETECTED").length;
                        const visualCandidates = pages.filter(page => page.visual.status !== "NOT_DETECTED").length;
                        const visualUnderstood = pages.filter(page => page.visual.status === "UNDERSTOOD").length;
                        const reviewPages = pages.filter(page => item.analysis?.contextPages.includes(page.pageNumber)).slice(0, 2);
                        return (
                          <li key={item.id} data-state={item.localStatus === "FAILED" || item.uploadStatus === "FAILED" ? "error" : item.localStatus.toLowerCase()}>
                            <div className="material-analysis-head">
                              <div>
                                <strong title={item.file.name}>{item.file.name}</strong>
                                <span>{item.file.type || "application/octet-stream"} · {(item.file.size / 1024 / 1024).toFixed(2)} MB</span>
                              </div>
                              <button
                                type="button"
                                className="secondary material-remove"
                                disabled={busy || item.uploadStatus === "UPLOADING"}
                                onClick={() => item.uploaded && item.serverAnalysis
                                  ? setMaterialDecisions(current => ({ ...current, [item.serverAnalysis!.analysis_id]: "EXCLUDE" }))
                                  : updateIntakeFiles(current => current.filter(candidate => candidate.id !== item.id))}
                              >
                                {t(item.uploaded ? "Exclude this file" : "Remove")}
                              </button>
                            </div>
                            <dl className="material-analysis-grid" aria-live="polite">
                              <div><dt>{t("Upload")}</dt><dd>{t(item.uploadStatus)}</dd></div>
                              <div><dt>{t("Pages")}</dt><dd>{item.analysis?.pageCount ?? (item.localStatus === "PARSING" ? `${item.completedPages}…` : "—")}</dd></div>
                              <div><dt>{t("Text")}</dt><dd>{item.analysis ? t("{count} characters read", { count: item.analysis.characterCount }) : t(item.localStatus)}</dd></div>
                              <div><dt>{t("Tables")}</dt><dd>{item.analysis ? t("{count} candidate pages", { count: tablePages }) : "—"}</dd></div>
                              <div><dt>{t("Quick visual preview")}</dt><dd>{item.analysis ? `${Math.min(visualUnderstood, 2)} / ${Math.min(visualCandidates, 2)}` : "—"}</dd></div>
                              <div><dt>{t("Context coverage")}</dt><dd>{item.analysis?.contextPages.length ? item.analysis.contextPages.join(", ") : "—"}</dd></div>
                              <div><dt>{t("Authoritative analysis")}</dt><dd>{item.serverAnalysis ? t(item.serverAnalysis.status) : "—"}</dd></div>
                              <div><dt>{t("Parsed units")}</dt><dd>{item.serverAnalysis?.unit_count ?? "—"}</dd></div>
                              <div><dt>{t("Visual coverage")}</dt><dd>{item.serverAnalysis ? `${item.serverAnalysis.coverage.visual_inspected} / ${item.serverAnalysis.coverage.total}` : "—"}</dd></div>
                              <div><dt>{t("Uncovered locations")}</dt><dd>{item.serverAnalysis?.coverage.uncovered_locators.length ?? "—"}</dd></div>
                            </dl>
                            {item.serverAnalysis?.error_message && (
                              <div className="material-analysis-error" role="alert">
                                <span>{humanizeUserError(item.serverAnalysis.error_message, locale)}</span>
                              </div>
                            )}
                            {item.serverAnalysis && ["PARTIAL", "FAILED", "NEEDS_CONSENT"].includes(item.serverAnalysis.status) && (
                              <section className="material-coverage-review" aria-label={t("Coverage decision for {name}", { name: item.file.name })}>
                                <div>
                                  <strong>{t("Coverage needs your decision")}</strong>
                                  <span>{t("The original file remains preserved. Choose how this version may use the analyzed portion.")}</span>
                                </div>
                                {item.serverAnalysis.coverage.uncovered_locators.length > 0 && (
                                  <ul>
                                    {item.serverAnalysis.coverage.uncovered_locators.map((locator, index) => (
                                      <li key={`${locatorLabel(locator)}-${index}`}>
                                        <strong>{t(locatorLabel(locator))}</strong>
                                        <span>{t(String(locator.reason ?? "Not covered"))}</span>
                                      </li>
                                    ))}
                                  </ul>
                                )}
                                <div className="material-decision-actions" role="group" aria-label={t("Material decision")}>
                                  {item.serverAnalysis.status === "PARTIAL" && (
                                    <button
                                      type="button"
                                      className={materialDecisions[item.serverAnalysis.analysis_id] === "INCLUDE_PARTIAL" ? "decision-selected" : "secondary"}
                                      onClick={() => setMaterialDecisions(current => ({ ...current, [item.serverAnalysis!.analysis_id]: "INCLUDE_PARTIAL" }))}
                                    >
                                      {t("Include analyzed portion")}
                                    </button>
                                  )}
                                  {item.serverAnalysis.status === "NEEDS_CONSENT" && !externalConsent && (
                                    <button type="button" className="secondary" onClick={() => setExternalProcessingConsent(true)}>
                                      {t("Allow server visual analysis")}
                                    </button>
                                  )}
                                  {item.serverAnalysis.status === "FAILED" && (
                                    <button type="button" className="secondary" disabled={busy} onClick={() => void retryAuthoritativeAnalysis(item)}>
                                      {t("Retry server analysis")}
                                    </button>
                                  )}
                                  <button
                                    type="button"
                                    className={materialDecisions[item.serverAnalysis.analysis_id] === "EXCLUDE" ? "decision-selected" : "secondary"}
                                    onClick={() => setMaterialDecisions(current => ({ ...current, [item.serverAnalysis!.analysis_id]: "EXCLUDE" }))}
                                  >
                                    {t("Exclude this file")}
                                  </button>
                                </div>
                              </section>
                            )}
                            {reviewPages.length > 0 && (
                              <details className="material-page-review">
                                <summary>{t("Review page-level analysis ({count} pages)", { count: reviewPages.length })}</summary>
                                <div className="material-page-review-scroll">
                                  <table>
                                    <thead>
                                      <tr>
                                        <th>{t("Page")}</th>
                                        <th>{t("Text status")}</th>
                                        <th>{t("Table status")}</th>
                                        <th>{t("Visual status")}</th>
                                        <th>{t("Recognition")}</th>
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {reviewPages.map(page => (
                                        <tr key={page.pageNumber}>
                                          <td>{page.pageNumber}</td>
                                          <td>{t(page.textStatus)}</td>
                                          <td>{t(page.table.status)}</td>
                                          <td>{t(page.visual.status)}</td>
                                          <td>
                                            <strong>{t(page.visual.recognitionType)} · {t(page.visual.source)}</strong>
                                            {page.visual.summary && <span>{page.visual.summary}</span>}
                                          </td>
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                </div>
                              </details>
                            )}
                            {(item.error || item.uploadError) && (
                              <div className="material-analysis-error" role="alert">
                                <span>{humanizeUserError(item.error || item.uploadError || "", locale)}</span>
                                <button
                                  type="button"
                                  className="secondary"
                                  data-retry-kind="local-or-upload"
                                  disabled={busy}
                                  onClick={() => item.uploadError ? void uploadNewMaterials([item]) : retryMaterial(item)}
                                >
                                  {t(item.uploadError ? "Retry upload" : "Retry preview")}
                                </button>
                              </div>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  )}

                  {persistedAnalyses.length > 0 && (
                    <ul className="material-analysis-list" aria-label={t("Added materials")}>
                      {persistedAnalyses.map(item => (
                        <li key={item.analysis_id} data-state={item.status.toLowerCase()}>
                          <div className="material-analysis-head">
                            <div>
                              <strong title={item.display_name}>{item.display_name}</strong>
                              <span>{item.mime_type} · {t("Authoritative analysis")}: {t(item.status)}</span>
                            </div>
                          </div>
                          <dl className="material-analysis-grid" aria-live="polite">
                            <div><dt>{t("Pages")}</dt><dd>{item.page_count || "—"}</dd></div>
                            <div><dt>{t("Parsed units")}</dt><dd>{item.unit_count}</dd></div>
                            <div><dt>{t("Visual coverage")}</dt><dd>{`${item.coverage.visual_inspected} / ${item.coverage.total}`}</dd></div>
                            <div><dt>{t("Uncovered locations")}</dt><dd>{item.coverage.uncovered_locators.length}</dd></div>
                          </dl>
                          {item.status === "PARTIAL" && (
                            <section className="material-coverage-review" aria-label={t("Coverage decision for {name}", { name: item.display_name })}>
                              <div>
                                <strong>{t("Coverage needs your decision")}</strong>
                                <span>{t("The original file remains preserved. Choose how this version may use the analyzed portion.")}</span>
                              </div>
                              <ul>
                                {item.coverage.uncovered_locators.map((locator, index) => (
                                  <li key={`${locatorLabel(locator)}-${index}`}>
                                    <strong>{t(locatorLabel(locator))}</strong>
                                    <span>{t(String(locator.reason ?? "Not covered"))}</span>
                                  </li>
                                ))}
                              </ul>
                              <div className="material-decision-actions" role="group" aria-label={t("Material decision")}>
                                <button
                                  type="button"
                                  className={materialDecisions[item.analysis_id] === "INCLUDE_PARTIAL" ? "decision-selected" : "secondary"}
                                  onClick={() => setMaterialDecisions(current => ({ ...current, [item.analysis_id]: "INCLUDE_PARTIAL" }))}
                                >
                                  {t("Include analyzed portion")}
                                </button>
                                <button
                                  type="button"
                                  className={materialDecisions[item.analysis_id] === "EXCLUDE" ? "decision-selected" : "secondary"}
                                  onClick={() => setMaterialDecisions(current => ({ ...current, [item.analysis_id]: "EXCLUDE" }))}
                                >
                                  {t("Exclude this file")}
                                </button>
                              </div>
                            </section>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}

                  {error && (
                    <div className="capture-tools">
                      <LocalizedErrorMessage value={error} />
                      <button
                        type="button"
                        className="secondary"
                        onClick={() => {
                          setMode("structured");
                          setError(undefined);
                        }}
                      >
                        {t("Continue with the full form")}
                      </button>
                    </div>
                  )}

                  <div className="form-actions">
                    {hasMaterial && (
                      <label className="consent-row">
                        <input
                          type="checkbox"
                          checked={externalConsent}
                          onChange={event => setExternalProcessingConsent(event.target.checked)}
                        />
                        <span>
                          {t("I allow the text above to be sent to the configured model to create a draft for my review. Original files still use isolated private upload.")}
                        </span>
                      </label>
                    )}
                    <button
                      onClick={prepareDraft}
                      disabled={busy || fileParsing || (hasMaterial && !externalConsent)}
                    >
                      {fileParsing ? t("Building quick preview…") : draftSecondsRemaining !== null ? t("Creating draft · {seconds}s", { seconds: draftSecondsRemaining }) : busy ? t("Waiting for authoritative analysis…") : t("Organize on the wheel")}
                    </button>
                  </div>
                </div>
              )}

              {mode === "structured" && (
                <div id="mode-panel-structured" role="tabpanel" aria-labelledby="mode-tab-structured" className="structured-intake">
                  {intakeFiles.length > 0 && (
                    <ol className="material-pipeline" aria-label={t("Material processing progress")}>
                      {pipelineStages.map((stage, index) => (
                        <li key={stage.label} data-state={stage.state}>
                          <span className="pipeline-index">{String(index + 1).padStart(2, "0")}</span>
                          <span><strong>{stage.label}</strong><small>{stage.detail}</small></span>
                        </li>
                      ))}
                    </ol>
                  )}
                  <div className="structured-material-capture">
                    <span className="field-name">{t("Added materials")}</span>
                    <label
                      className="drop-zone"
                      onDragOver={event => event.preventDefault()}
                      onDrop={event => {
                        event.preventDefault();
                        addFiles([...event.dataTransfer.files]);
                      }}
                    >
                      <input
                        type="file"
                        multiple
                        accept=".pdf,.doc,.docx,.txt,image/*"
                        onChange={event => {
                          addFiles([...(event.target.files ?? [])]);
                          event.currentTarget.value = "";
                        }}
                      />
                      <span>{t("Drop or choose documents (PDF / document / image)")}</span>
                    </label>
                    {intakeFiles.length > 0 && (
                      <ul className="material-analysis-list" aria-label={t("Added materials")}>
                        {intakeFiles.map(item => (
                          <li key={item.id} data-state={item.localStatus === "FAILED" ? "error" : item.localStatus.toLowerCase()}>
                            <div className="material-analysis-head">
                              <div>
                                <strong title={item.file.name}>{item.file.name}</strong>
                                <span>
                                  {t(item.localStatus)} · {t(item.uploadStatus)} · {(item.file.size / 1024 / 1024).toFixed(2)} MB
                                </span>
                                {item.error && <span role="alert">{humanizeUserError(item.error, locale)}</span>}
                                {item.serverAnalysis && <span>{t("Authoritative analysis")}: {t(item.serverAnalysis.status)}</span>}
                              </div>
                              <button
                                type="button"
                                className="secondary material-remove"
                                disabled={busy || item.uploadStatus === "UPLOADING"}
                                onClick={() => item.uploaded && item.serverAnalysis
                                  ? setMaterialDecisions(current => ({ ...current, [item.serverAnalysis!.analysis_id]: "EXCLUDE" }))
                                  : updateIntakeFiles(current => current.filter(candidate => candidate.id !== item.id))}
                              >
                                {t(item.uploaded ? "Exclude this file" : "Remove")}
                              </button>
                              {item.serverAnalysis && ["PARTIAL", "FAILED", "NEEDS_CONSENT"].includes(item.serverAnalysis.status) && (
                                <button type="button" className="secondary material-remove" onClick={() => setMode("quick")}>
                                  {t("Review coverage")}
                                </button>
                              )}
                            </div>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  {INTAKE_SECTIONS.map((section, index) => {
                    const filled = section.fields.filter(field => fields[field.key]?.trim()).length;
                    return (
                      <details key={section.id} className="intake-group" open={index === activeSector}>
                        <summary onClick={() => setActiveSector(index)}>
                          <span>{section.code} · {t(section.title)}</span>
                          <span className="g-meta">{filled} / {section.fields.length}</span>
                        </summary>
                        <div className="field-set">
                          {section.fields.map(field => (
                            <label key={field.key}>
                              <span className="field-name">{t(field.label)}</span>
                              <span className="field-hint">{t(field.hint)}</span>
                              <textarea
                                className={`weight-${field.weight}`}
                                value={fields[field.key] ?? ""}
                                onChange={event => update(field.key, event.target.value)}
                                placeholder={t(field.placeholder)}
                              />
                            </label>
                          ))}
                        </div>
                      </details>
                    );
                  })}
                  {error && <LocalizedErrorMessage value={error} />}
                  <div className="form-actions">
                    {hasMaterial && (
                      <label className="consent-row">
                        <input
                          type="checkbox"
                          checked={externalConsent}
                          onChange={event => setExternalProcessingConsent(event.target.checked)}
                        />
                        <span>
                          {t("I allow the text above to be sent to the configured model to create a draft for my review. Original files still use isolated private upload.")}
                        </span>
                      </label>
                    )}
                    {completion.filled === completion.total && (
                      <button type="button" className="secondary" onClick={continueWithoutModelDraft} disabled={busy || fileParsing}>
                        {t("Continue with the completed fields")}
                      </button>
                    )}
                    <button onClick={prepareDraft} disabled={busy || fileParsing || (hasMaterial && !externalConsent)}>
                      {fileParsing ? t("Building quick preview…") : draftSecondsRemaining !== null ? t("Creating draft · {seconds}s", { seconds: draftSecondsRemaining }) : busy ? t("Waiting for authoritative analysis…") : t("Organize on the wheel")}
                    </button>
                  </div>
                </div>
              )}
            </section>
          )}

          {phase === "review" && (
            <section className="dock-panel dock-enter" aria-label={t("Profile confirmation")}>
              <div className="dock-head">
                <span className="bearing">{t("Step 3 of 3 · you decide")}</span>
                <h2>{t("Check whether we understood correctly")}</h2>
                <p>{t("This profile was organized from your material. AI may misunderstand it; edit each item before confirmation makes it authoritative for this version.")}</p>
              </div>
              <ul className="profile-dock-list">
                {ALL_INTAKE_FIELDS.map(field => {
                  const source = fieldSourceOf(field.key, fields, sources);
                  const value = fields[field.key]?.trim() ?? "";
                  return (
                    <li key={field.key}>
                      <details>
                        <summary>
                          <span className="p-key">{t(field.label)}</span>
                          <span className="p-preview" data-empty={!value}>
                            {value || t("Empty · select to add or preserve the gap")}
                          </span>
                          <span className="source-tag" data-source={source === "unknown" ? "missing" : source}>
                            {t(SOURCE_LABELS[source])}
                          </span>
                        </summary>
                        <textarea
                          value={fields[field.key] ?? ""}
                          onChange={event => update(field.key, event.target.value)}
                          placeholder={t(field.placeholder)}
                        />
                      </details>
                    </li>
                  );
                })}
              </ul>
              {error && <LocalizedErrorMessage value={error} />}
              <div className="form-actions">
                <button className="secondary" onClick={() => setPhase("collect")}>
                  {t("Back to edit")}
                </button>
                <button onClick={commitProfile} disabled={busy}>
                  {busy ? t("Saving…") : t("Confirm and continue")}
                </button>
              </div>
            </section>
          )}

          {phase === "questions" && (
            <section className="dock-panel dock-enter" aria-label={t("Critical follow-up questions")}>
              <div className="dock-head">
                <span className="bearing">{t("Insufficient details · only critical questions")}</span>
                <h2>{t("One question at a time · {count} remaining", { count: questions.length })}</h2>
                <p>{t("Answering ‘I don’t know’ is explicitly recorded as unknown; AI will not invent an answer.")}</p>
              </div>
              <div className="question-stack">
                {questions.slice(0, 1).map(question => (
                  <label key={question.field}>
                    <span className="field-name">
                      {String(question.priority).padStart(2, "0")} · {translateGapQuestion(locale, question.field, question.question)}
                    </span>
                    <span className="field-hint">{t("This question affects the formal evaluation scope and evidence level.")}</span>
                    <textarea
                      className="weight-primary"
                      value={fields[question.field] ?? ""}
                      onChange={event => update(question.field, event.target.value)}
                      placeholder={t("Enter an answer or type unknown")}
                    />
                    <span className="question-quick-choices"><button type="button" className="secondary" onClick={() => update(question.field, t("Not sure yet"))}>{t("Not sure yet")}</button><button type="button" className="secondary" onClick={() => update(question.field, t("Answer after adding material"))}>{t("Answer after adding material")}</button></span>
                  </label>
                ))}
              </div>
              {error && <LocalizedErrorMessage value={error} />}
              <div className="form-actions">
                <button onClick={answerCurrentQuestion} disabled={busy || !questions[0] || !fields[questions[0].field]?.trim()}>
                  {busy ? t("Saving…") : questions.length > 1 ? t("Save and show next question") : t("Confirm project portrait")}
                </button>
              </div>
            </section>
          )}

          {phase === "validation" && (
            <section className="dock-panel dock-enter" aria-label={t("User-validation script")}>
              <div className="dock-head">
                <span className="bearing">{preliminaryPrediction ? t("Preliminary prediction · hypotheses first") : t("UVD · frozen before the Run")}</span>
                <h2>{preliminaryPrediction ? t("Review 1–5 core validation tasks before the preliminary prediction") : t("Review 1–5 AI-generated core tasks")}</h2>
                <p>{preliminaryPrediction ? t("This early-stage prediction will identify the value hypothesis, evidence gaps, and next validation actions. It is not a full-potential score.") : t("The draft uses the product description, URL, profile, and page-level material context. Your edits always take priority and only confirmation freezes the script.")}</p>
              </div>
              {taskGenerationStatus === "GENERATING" && (
                <div className="task-generation-state" role="status" aria-live="polite">
                  <span className="task-generation-pulse" aria-hidden="true" />
                  <div><strong>{t("Generating validation tasks…")}</strong><p>{t("Building observable tasks from the confirmed profile and cited material pages.")}</p></div>
                </div>
              )}
              {taskGenerationStatus === "FAILED" && (
                <div className="task-generation-state" role="alert" data-tone="error">
                  <div><strong>{t("Validation task generation failed")}</strong><p>{taskGenerationError}</p></div>
                  <button type="button" className="secondary" onClick={() => void generateTaskDrafts()}>
                    {t("Retry generation")}
                  </button>
                </div>
              )}
              {taskGenerationStatus === "READY" && (
                <div className="task-draft-toolbar" role="status">
                  <span>{validationDirty ? t("Edited by you · your version will be frozen") : t("AI draft · review before freezing")}</span>
                  <button type="button" className="secondary" onClick={() => void generateTaskDrafts()}>
                    {t("Regenerate draft")}
                  </button>
                </div>
              )}
              <div className="question-stack" aria-busy={taskGenerationStatus === "GENERATING"}>
                {validationTasks.map((item, index) => (
                  <fieldset key={`${item.task_key}-${index}`} className="validation-task-card">
                    <legend>{t("Core task {index}", { index: index + 1 })}</legend>
                    <label>
                      <span className="field-name">{t("Stable task key")}</span>
                      <input
                        value={item.task_key}
                        onChange={event => updateValidationTask(index, { task_key: event.target.value })}
                        placeholder={t("e.g. create_weekly_plan")}
                      />
                    </label>
                    <label>
                      <span className="field-name">{t("User action")}</span>
                      <textarea
                        value={item.description}
                        onChange={event => updateValidationTask(index, { description: event.target.value })}
                        placeholder={t("Starting state and action the user must complete")}
                      />
                    </label>
                    <label>
                      <span className="field-name">{t("Expected observable outcome")}</span>
                      <textarea
                        value={item.expected_observable_outcome}
                        onChange={event => updateValidationTask(index, {
                          expected_observable_outcome: event.target.value,
                        })}
                        placeholder={t("Expected change in the page, file, or state")}
                      />
                    </label>
                    <label>
                      <span className="field-name">{t("Maximum steps (optional)")}</span>
                      <input
                        type="number"
                        min={1}
                        max={100}
                        value={item.max_steps ?? ""}
                        onChange={event => updateValidationTask(index, {
                          max_steps: event.target.value ? Number(event.target.value) : null,
                        })}
                      />
                    </label>
                    {(item.rationale || item.source_hints.length > 0) && (
                      <div className="task-draft-basis">
                        {item.rationale && <p><strong>{t("Why this task")}</strong>{item.rationale}</p>}
                        {item.source_hints.length > 0 && (
                          <p><strong>{t("Source hints")}</strong>{item.source_hints.join(" · ")}</p>
                        )}
                      </div>
                    )}
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => {
                        setValidationTasks(current => current.filter((_, itemIndex) => itemIndex !== index));
                        setValidationDirty(true);
                      }}
                    >
                      {t("Remove task")}
                    </button>
                  </fieldset>
                ))}
              </div>
              {taskGenerationStatus !== "GENERATING" && validationTasks.length < 5 && (
                <button
                  type="button"
                  className="secondary"
                  onClick={() => {
                    setValidationTasks(current => [...current, {
                      task_key: `core_task_${current.length + 1}`,
                      description: "",
                      expected_observable_outcome: "",
                      max_steps: 8,
                      rationale: "",
                      source_hints: [],
                    }]);
                    setTaskGenerationStatus("READY");
                    setTaskGenerationError(undefined);
                    setValidationDirty(true);
                  }}
                >
                  {t("Add core task")}
                </button>
              )}

              <details className="intake-group" style={{ marginTop: 18 }}>
                <summary>
                  <span>{t("Optional · register real user evidence")}</span>
                  <span className="g-meta">{t("Aggregated content without PII only")}</span>
                </summary>
                <div className="field-set">
                  <label>
                    <span className="field-name">{t("Evidence file")}</span>
                    <input
                      type="file"
                      accept=".json,.csv,.txt,application/json,text/csv,text/plain"
                      onChange={event => setEvidenceFile(event.target.files?.[0])}
                    />
                  </label>
                  <label>
                    <span className="field-name">{t("Type")}</span>
                    <select value={evidenceKind} onChange={event => setEvidenceKind(event.target.value)}>
                      <option value="interview">{t("Interview")}</option>
                      <option value="survey">{t("Survey")}</option>
                      <option value="usability_test">{t("Usability test")}</option>
                      <option value="usage_data">{t("Usage data")}</option>
                      <option value="retention_data">{t("Retention data")}</option>
                      <option value="payment_record">{t("Payment record")}</option>
                      <option value="team_statement">{t("Team statement")}</option>
                    </select>
                  </label>
                  <label>
                    <span className="field-name">{t("Claimed evidence tier")}</span>
                    <select value={evidenceTier} onChange={event => setEvidenceTier(event.target.value)}>
                      {['E0', 'E1', 'E2', 'E3', 'E4', 'E5'].map(tier => <option key={tier}>{tier}</option>)}
                    </select>
                  </label>
                  <label>
                    <span className="field-name">{t("Traceable source")}</span>
                    <input value={evidenceSource} onChange={event => setEvidenceSource(event.target.value)} />
                  </label>
                  <label>
                    <span className="field-name">{t("Observed at")}</span>
                    <input
                      type="datetime-local"
                      value={evidenceObservedAt}
                      onChange={event => setEvidenceObservedAt(event.target.value)}
                    />
                  </label>
                  <label>
                    <span className="field-name">{t("Sample size")}</span>
                    <input
                      type="number"
                      min={1}
                      value={evidenceSampleSize}
                      onChange={event => setEvidenceSampleSize(event.target.value)}
                    />
                  </label>
                  <label>
                    <span className="field-name">{t("Applicable user segment")}</span>
                    <input value={evidenceSegment} onChange={event => setEvidenceSegment(event.target.value)} />
                  </label>
                  <label>
                    <span className="field-name">{t("Aggregate observation")}</span>
                    <textarea
                      value={evidenceObservation}
                      onChange={event => setEvidenceObservation(event.target.value)}
                      placeholder={t("Write summary facts only; no names, phone numbers, email addresses, or raw transcripts")}
                    />
                  </label>
                </div>
              </details>
              {error && <LocalizedErrorMessage value={error} />}
              {!dispatchEnabled && modeLabel === "RECORDED" && (
                <div className="supervisor-exception" role="status">
                  <span>{t("Recorded mode is read-only")}</span>
                  <p>{t("Start LaunchScope in Material or Live mode to execute the four-stage evaluation.")}</p>
                  <a href="/recorded-snapshot">{t("Open recorded snapshot")}</a>
                </div>
              )}
              <div className="form-actions">
                <button
                  onClick={confirmValidationAndPlan}
                  disabled={busy || !dispatchEnabled || taskGenerationStatus !== "READY" || validationTasks.length === 0}
                >
                  {busy
                    ? t("Starting supervisor planning…")
                    : dispatchEnabled
                      ? preliminaryPrediction
                        ? t("Confirm and start preliminary prediction")
                        : t("Confirm and start evaluation")
                      : t("Recorded mode is read-only")}
                </button>
              </div>
            </section>
          )}

        </div>
      </div>
    </main>
  );
}
