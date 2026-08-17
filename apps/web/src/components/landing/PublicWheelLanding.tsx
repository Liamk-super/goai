"use client";

import { useEffect, useRef, useState } from "react";
import { LaunchScopeApi } from "../../lib/api-client";
import type { DemoSession } from "../../lib/demo-session";
import { restoreDemoSession } from "../../lib/demo-session-recovery";
import { beadTone, historyItemToBead, type HistoryBead } from "../../lib/history-beads";
import { buildSectorStates } from "../../lib/wheel-state";
import { EvaluationWheel } from "../workspace/EvaluationWheel";
import { LocaleSelect, useI18n } from "../i18n/LocaleProvider";
import { LocalizedErrorMessage } from "../i18n/LocalizedErrorMessage";
import { executionMode } from "../../lib/supervisor-experience";
import { demoCopy } from "../../lib/hit-predictor-demo-data";
import {
  discardPendingIntakeFiles,
  mergePendingIntakeFiles,
  stagePendingIntakeFiles,
} from "../../lib/pending-intake-files";
import {
  deriveProjectName,
  HIT_PREDICTOR_INTAKE_SEED_KEY,
  HIT_PREDICTOR_STAGES,
  normalizeProductUrl,
  type HitPredictorStageCode,
} from "../../lib/hit-predictor-intake";

function BrandMark() {
  return (
    <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true">
      <circle cx="13" cy="13" r="11.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="13" cy="13" r="3.2" fill="currentColor" />
      {Array.from({ length: 4 }, (_, i) => {
        const a = (i * 90 * Math.PI) / 180;
        return (
          <line
            key={i}
            x1={13 + 6 * Math.sin(a)}
            y1={13 - 6 * Math.cos(a)}
            x2={13 + 10.5 * Math.sin(a)}
            y2={13 - 10.5 * Math.cos(a)}
            stroke="currentColor"
            strokeWidth="1.4"
          />
        );
      })}
    </svg>
  );
}

function apiFor(session: DemoSession): LaunchScopeApi {
  return new LaunchScopeApi({
    tenantId: session.tenantId,
    actorId: session.actorId,
    workspaceId: session.workspaceId,
  });
}

export function PublicWheelLanding({ startOpen = false }: { startOpen?: boolean }) {
  const { locale, t } = useI18n();
  const recordedMode = executionMode() === "RECORDED";
  const [session, setSession] = useState<DemoSession | null>(null);
  const [beads, setBeads] = useState<HistoryBead[]>([]);
  const [docked, setDocked] = useState(startOpen);
  const [description, setDescription] = useState("");
  const [projectName, setProjectName] = useState("");
  const [projectNameEdited, setProjectNameEdited] = useState(false);
  const [stageCode, setStageCode] = useState<HitPredictorStageCode>("IDEA");
  const [referenceUrl, setReferenceUrl] = useState("");
  const [materialFiles, setMaterialFiles] = useState<File[]>([]);
  const [materialDragActive, setMaterialDragActive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [historyPreviewOpen, setHistoryPreviewOpen] = useState(false);
  const descriptionInput = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    void restoreDemoSession(window.localStorage, controller.signal).then(async restored => {
      setSession(restored);
      const api = apiFor(restored);
      const result = await api.listEvaluationHistory({ limit: 6 });
      setBeads(result.items.map(historyItemToBead));
    }).catch(cause => {
      if (cause instanceof DOMException && cause.name === "AbortError") return;
      setError(cause instanceof Error ? cause.message : t("Demo session unavailable"));
      setBeads([]);
    });
    return () => controller.abort();
  }, [t]);

  useEffect(() => {
    const syncFromLocation = () => {
      setDocked(new URL(window.location.href).searchParams.get("start") === "1");
    };
    window.addEventListener("popstate", syncFromLocation);
    return () => window.removeEventListener("popstate", syncFromLocation);
  }, [recordedMode]);

  useEffect(() => {
    if (!docked || !session) return;
    const focusTimer = window.setTimeout(() => descriptionInput.current?.focus(), 120);
    return () => window.clearTimeout(focusTimer);
  }, [docked, session]);

  function setStartState(open: boolean, historyMode: "push" | "replace") {
    const url = new URL(window.location.href);
    if (open) url.searchParams.set("start", "1");
    else url.searchParams.delete("start");
    const nextUrl = `${url.pathname}${url.search}${url.hash}`;
    if (historyMode === "push") window.history.pushState(null, "", nextUrl);
    else window.history.replaceState(null, "", nextUrl);
    setDocked(open);
  }

  async function startEvaluation() {
    setBusy(true);
    setError(undefined);
    try {
      if (!session) throw new Error(t("The fixed Demo workspace is not available."));
      setStartState(true, "push");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("The fixed Demo workspace is not available."));
    } finally {
      setBusy(false);
    }
  }

  async function createProject(event: React.FormEvent) {
    event.preventDefault();
    let normalizedReferenceUrl = "";
    try {
      normalizedReferenceUrl = normalizeProductUrl(referenceUrl);
    } catch (cause) {
      setError(t(cause instanceof Error ? cause.message : "Enter a valid product URL."));
      return;
    }
    const seed = { description: description.trim(), stage: stageCode, referenceUrl: normalizedReferenceUrl };
    window.sessionStorage.setItem(HIT_PREDICTOR_INTAKE_SEED_KEY, JSON.stringify(seed));
    if (recordedMode) {
      window.location.assign("/recorded-snapshot?intake=1");
      return;
    }
    if (!session?.workspaceId) {
      setError(t("The visitor identity has no workspace. Refresh and try again."));
      return;
    }
    setBusy(true);
    setError(undefined);
    let transferId = "";
    try {
      if (materialFiles.length) {
        transferId = crypto.randomUUID();
        await stagePendingIntakeFiles(transferId, materialFiles);
      }
      const project = await apiFor(session).createProject(session.workspaceId, projectName.trim());
      const transferQuery = transferId ? `?intakeTransfer=${encodeURIComponent(transferId)}` : "";
      window.location.assign(`/projects/${project.project_id}/new-evaluation${transferQuery}`);
    } catch (cause) {
      if (transferId) void discardPendingIntakeFiles(transferId).catch(() => undefined);
      setError(cause instanceof Error ? cause.message : t("Project creation failed"));
      setBusy(false);
    }
  }

  function addMaterialFiles(selected: File[]) {
    setMaterialFiles(current => mergePendingIntakeFiles(current, selected));
  }

  function updateDescription(value: string) {
    setDescription(value);
    if (!projectNameEdited) setProjectName(deriveProjectName(value));
  }

  function continueProject(bead: HistoryBead) {
    window.location.assign(`/runs/${bead.runId}`);
  }

  const leftBeads = beads.slice(0, 3);
  const rightBeads = beads.slice(3, 6);

  function beadButton(bead: HistoryBead, interactive = true) {
    return (
      <button
        key={bead.runId}
        className="history-bead"
        data-state={beadTone(bead.status)}
        onClick={() => continueProject(bead)}
        tabIndex={interactive ? undefined : -1}
        aria-label={t("Continue {name}, {version}, {signal}", { name: bead.name, version: bead.version, signal: bead.signal })}
      >
        <span className="bead-orb">{bead.version}</span>
        <span className="bead-name">{bead.name}</span>
        <span className="bead-signal">{t(bead.signal)} · {bead.updatedAt ? new Date(bead.updatedAt).toLocaleDateString(locale) : "—"}</span>
      </button>
    );
  }

  return (
    <div className="landing">
      <header className="landing-top">
        <a className="landing-brand" href="/" aria-label={t("LaunchScope")}>
          <BrandMark />
          <span className="brand-name">{t("LaunchScope")}</span>
        </a>
        <div className="landing-top-actions">
          {!docked && (
            <>
              <a className="landing-toolbar-button" data-active="demo" href="/demo/hit-predictor">
                {locale === "zh-CN" ? demoCopy.landingDemoZh : demoCopy.landingDemoEn}
              </a>
              <button
                type="button"
                className="landing-toolbar-button"
                data-active={historyPreviewOpen || undefined}
                aria-expanded={historyPreviewOpen}
                aria-controls="landing-history-preview"
                onClick={() => setHistoryPreviewOpen(current => !current)}
              >
                {t("Evaluation history")}
              </button>
              <a className="landing-toolbar-button" href="/projects">
                {t("All projects")}
              </a>
            </>
          )}
          <details className="landing-menu">
            <summary className="landing-toolbar-button" aria-label={t("More destinations")}>{t("Menu")}</summary>
            <div className="landing-menu-list">
              <LocaleSelect compact />
            </div>
          </details>
        </div>
      </header>

      <main className="landing-center">
        {!docked && (
          <>
            <p className="landing-tagline">{t("Describe your product. Get an evidence-calibrated decision on whether to keep investing and what to validate next.")}</p>
            <p className="landing-start-hint">{t("Start with the product description; a project name will be generated and can be changed later.")}</p>
          </>
        )}

        <div
          id={!docked ? "landing-history-preview" : undefined}
          className={docked ? "wheel-stage landing-stage" : "landing-wheel landing-wheel-history"}
          data-docked={docked || undefined}
          data-history-visible={!docked && historyPreviewOpen || undefined}
        >
          {!docked && (
            <div className="bead-side bead-side-left" aria-hidden={!historyPreviewOpen}>
              <span className="bead-side-title">{t("Recent predictions")}</span>
              {leftBeads.map(bead => beadButton(bead, historyPreviewOpen))}
            </div>
          )}
          <div className={docked ? "wheel-pane" : undefined}>
            <div className="wheel-frame instrument-shell">
              <EvaluationWheel
                sectors={buildSectorStates({})}
                ambient={!docked}
                architectureGeneration="supervisor-1p4-v1"
              />
              {!docked ? (
                <button
                  className="wheel-core-button"
                  onClick={startEvaluation}
                  disabled={busy || !session}
                  aria-label={t("Start an evaluation")}
                >
                  <span className="core-cta-label">
                    {recordedMode
                      ? t("Open recorded snapshot")
                      : !session
                          ? t("Restoring workspace…")
                          : busy
                            ? t("Preparing…")
                            : t("Start prediction")}
                  </span>
                  <span className="core-cta-sub">{t(recordedMode ? "Recorded snapshot" : "Evidence first")}</span>
                </button>
              ) : (
                <div className="wheel-core-static">
                  <span className="core-cta-label" title={projectName.trim() || undefined}>{projectName.trim() || t("A new evaluation")}</span>
                  <span className="core-stage-read">{t(HIT_PREDICTOR_STAGES.find(item => item.code === stageCode)?.label ?? "Choose a stage")}</span>
                </div>
              )}
            </div>
          </div>
          {!docked && (
            <div className="bead-side bead-side-right" aria-hidden={!historyPreviewOpen}>
              <span className="bead-side-title">{t("Recent predictions")}</span>
              {rightBeads.map(bead => beadButton(bead, historyPreviewOpen))}
            </div>
          )}

          {docked && (
            <div className="wheel-side">
              <section className="dock-panel dock-enter hit-predictor-intake" aria-label={t("Describe the product") }>
                <div className="dock-head">
                  <span className="bearing">{t("Product description / 01")}</span>
                  <h2>{t("What product are you building?")}</h2>
                  <p>{t("Describe who it serves, the problem, and the core experience. You can add a link or material now.")}</p>
                </div>
                <form onSubmit={createProject}>
                  <label>
                    <span className="field-name">{t("Product description")}</span>
                    <textarea ref={descriptionInput} value={description} onChange={event => updateDescription(event.target.value)} rows={5} maxLength={4000} placeholder={t("e.g. An AI mock interview tool for students that gives repeatable, evidence-based practice feedback")}/>
                  </label>
                  <fieldset className="hit-predictor-stage-picker">
                    <legend className="field-name">{t("Current stage")}</legend>
                    {HIT_PREDICTOR_STAGES.map(stage => <label key={stage.code} data-selected={stageCode === stage.code || undefined}><input type="radio" name="product-stage" value={stage.code} checked={stageCode === stage.code} onChange={() => setStageCode(stage.code)} /><span><strong>{t(stage.label)}</strong><small>{t(stage.detail)}</small></span></label>)}
                  </fieldset>
                  <div className="hit-predictor-material-row">
                    <label><span className="field-name">{t("Product link (optional)")}</span><input type="text" inputMode="url" autoCapitalize="none" spellCheck={false} value={referenceUrl} onChange={event => setReferenceUrl(event.target.value)} onBlur={() => { try { setReferenceUrl(normalizeProductUrl(referenceUrl)); } catch {} }} placeholder="creatrades.com" /></label>
                    <div className="landing-material-field">
                      <span className="field-name">{t("Upload material (optional)")}</span>
                      <label
                        className="landing-material-drop"
                        data-drag-active={materialDragActive || undefined}
                        onDragEnter={event => { event.preventDefault(); setMaterialDragActive(true); }}
                        onDragOver={event => { event.preventDefault(); setMaterialDragActive(true); }}
                        onDragLeave={() => setMaterialDragActive(false)}
                        onDrop={event => {
                          event.preventDefault();
                          setMaterialDragActive(false);
                          addMaterialFiles([...event.dataTransfer.files]);
                        }}
                      >
                        <input
                          type="file"
                          multiple
                          aria-label={t("Upload material (optional)")}
                          accept=".pdf,.doc,.docx,.ppt,.pptx,.png,.jpg,.jpeg,.webp,.txt,.md"
                          onChange={event => {
                            addMaterialFiles([...(event.target.files ?? [])]);
                            event.currentTarget.value = "";
                          }}
                        />
                        <span>
                          <strong>{t("Drop or choose documents (PDF / document / image)")}</strong>
                          <small>{t("Files are transferred once to the project portrait.")}</small>
                        </span>
                      </label>
                      {materialFiles.length > 0 && (
                        <div className="landing-material-selection">
                          <small>{t("{count} files selected", { count: materialFiles.length })}</small>
                          <ul aria-label={t("Added materials")}>
                            {materialFiles.map(file => (
                              <li key={`${file.name}:${file.size}:${file.lastModified}`}>
                                <span title={file.name}>{file.name}</span>
                                <button
                                  type="button"
                                  aria-label={t("Remove {name}", { name: file.name })}
                                  onClick={() => setMaterialFiles(current => current.filter(item => item !== file))}
                                >
                                  ×
                                </button>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  </div>
                  {description.trim() && (
                    <label className="prediction-target prediction-target-editable">
                      <strong>{t("Generated project name")}</strong>
                      <input
                        aria-label={t("Generated project name")}
                        value={projectName}
                        minLength={2}
                        maxLength={200}
                        onChange={event => { setProjectName(event.target.value); setProjectNameEdited(true); }}
                      />
                    </label>
                  )}
                  {error && <LocalizedErrorMessage value={error} />}
                  <div className="form-actions">
                    <button disabled={busy || description.trim().length < 12 || projectName.trim().length < 2}>
                      {busy ? t("Creating dossier…") : t("Continue to project portrait")}
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => { setStartState(false, "replace"); setError(undefined); }}
                    >
                      {t("Back")}
                    </button>
                  </div>
                </form>
              </section>
            </div>
          )}
        </div>

        {!docked && error && <LocalizedErrorMessage value={error} className="landing-error" />}

        {!docked && session && beads.length > 0 && (
          <div className="bead-rail" data-visible={historyPreviewOpen || undefined} aria-hidden={!historyPreviewOpen} aria-label={t("Evaluation history")}>
            <span className="bead-rail-label">{t("Evaluation history · select to continue")}</span>
            {beads.map(bead => beadButton(bead, historyPreviewOpen))}
          </div>
        )}

        {!docked && (
          <ol className="landing-benefits" aria-label={t("Why use the predictor") }>
            <li><span>01</span><strong>{t("Independent multi-role review")}</strong><p>{t("Product, users, business, and the outside environment are assessed from separate perspectives.")}</p></li>
            <li><span>02</span><strong>{t("Evidence calibration")}</strong><p>{t("Important conclusions are checked again to reduce unsupported AI guesses.")}</p></li>
            <li><span>03</span><strong>{t("Practical next actions")}</strong><p>{t("You get the 1–3 most worthwhile next steps, not only a score.")}</p></li>
          </ol>
        )}
      </main>

    </div>
  );
}
