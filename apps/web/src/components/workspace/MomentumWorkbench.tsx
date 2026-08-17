"use client";

import { useMemo, useState } from "react";
import type { AgentTeamsRun, Project, ProjectPortrait, Run } from "../../lib/api-client";
import { EvaluationWheel } from "./EvaluationWheel";
import { useI18n } from "../i18n/LocaleProvider";
import { isSupervisorExperience } from "../../lib/supervisor-experience";
import { productNameLength } from "../../lib/product-name";
import { filterProjectRuns, runVersionLabel } from "../../lib/project-history";
import { buildSectorStates } from "../../lib/wheel-state";
import { evaluationRouteForStage, stageCodeFromProfile } from "../../lib/hit-predictor-intake";

const INTAKE_SECTIONS = [
  ["I", "Product material", "The problem, core functionality, and tangible artifacts", 3],
  ["II", "Team capability", "Who is building it, relevant experience, and current delivery capacity", 2],
  ["III", "Users and traction", "Who uses it, who pays, and whether real usage data exists", 3],
  ["IV", "Timing and region", "Target market and any policy or platform constraints", 2],
] as const;

const DEFAULT_AGENTS = [
  "Prediction project lead",
  "Product and team",
  "User evidence",
  "Business and investment",
  "Timing and policy",
  "Evidence calibration",
];

const SUPERVISOR_AGENTS = ["Prediction project lead", "Product and team", "User evidence", "Business and investment", "Evidence check"];

const AGENT_NAMES: Record<string, string> = {
  "evaluation-manager": "Prediction project lead",
  "product-engineering": "Product and team",
  "user-evidence": "User evidence",
  "business-investment": "Business and investment",
  "geo-policy-trend": "Timing and policy",
  "evidence-auditor": "Evidence check",
};

type NeedleReadout = {
  key: string;
  name: string;
  status: string;
  evidence: number;
};

/** 一整周 = 一次完整评审。走完一周，V1 归档。 */
const NOTCHES_PER_REVOLUTION = 32;

export function MomentumWorkbench({
  project,
  runs,
  team,
  portrait,
}: {
  project: Project;
  runs: Run[];
  team?: AgentTeamsRun;
  portrait?: ProjectPortrait;
}) {
  const { status, t } = useI18n();
  const latest = runs[0];
  const supervisorMode = latest ? isSupervisorExperience(latest) : true;
  const [activeSector, setActiveSector] = useState(0);
  const [versionSearch, setVersionSearch] = useState("");
  const visibleRuns = useMemo(
    () => filterProjectRuns(runs, versionSearch),
    [runs, versionSearch],
  );

  const needles = useMemo(() => {
    if (!team?.tasks.length) {
      return (supervisorMode ? SUPERVISOR_AGENTS : DEFAULT_AGENTS).map((name, index) => ({
        key: `${name}-${index}`,
        name: t(name),
        status: index === 0 && latest ? latest.status : "IDLE",
        evidence: 0,
      }));
    }
    const byAgent = new Map<string, NeedleReadout>();
    for (const item of team.tasks) {
      const code = item.agent_identity_ref.split("@")[0];
      const current = byAgent.get(code);
      byAgent.set(code, {
        key: code,
        name: t(AGENT_NAMES[code] ?? code.replaceAll("-", " ")),
        status: code === "evaluation-manager" ? (latest?.status ?? item.status) : item.status,
        evidence: (current?.evidence ?? 0) + (item.evidence_count ?? 0),
      });
    }
    return [...byAgent.values()];
  }, [latest, supervisorMode, team]);

  /** 已落库的证据总数 —— 唯一让盘面前进的东西。没有证据，盘面不动。 */
  const evidenceCount = useMemo(
    () => needles.reduce((sum, n) => sum + n.evidence, 0),
    [needles],
  );
  const notch = Math.min(evidenceCount, NOTCHES_PER_REVOLUTION);
  const hasPortrait = Boolean(portrait?.product_version_id);
  const portraitStage = stageCodeFromProfile(portrait?.confirmed_fields.stage ?? "");
  const preliminaryPrediction = Boolean(
    portraitStage && evaluationRouteForStage(portraitStage) !== "FORMAL_EVALUATION",
  );

  const sectors = useMemo(
    () => hasPortrait
      ? buildSectorStates(portrait?.confirmed_fields ?? {})
      :
      INTAKE_SECTIONS.map(([code, name, , total]) => ({
        key: name,
        code,
        name,
        filled: runs.length ? total : 0,
        total,
      })),
    [hasPortrait, portrait?.confirmed_fields, runs.length],
  );

  const active = INTAKE_SECTIONS[activeSector];

  const savedPortraitHref = portrait?.product_version_id
    ? `/projects/${project.project_id}/new-evaluation?versionId=${encodeURIComponent(portrait.product_version_id)}`
    : `/projects/${project.project_id}/new-evaluation`;
  const primaryHref = latest
    ? `/runs/${latest.run_id}`
    : hasPortrait ? savedPortraitHref : `/projects/${project.project_id}/new-evaluation`;
  const primaryLabel =
    latest?.status === "COMPLETED"
      ? t("View results")
      : latest
        ? t("View progress")
        : hasPortrait
          ? preliminaryPrediction
            ? t("Start preliminary prediction")
            : t("Start prediction")
          : t("Add details");

  /** 一句话说清「现在该干什么」。用户不需要读四个读数才知道下一步。 */
  const nextStep = latest
    ? latest.status === "COMPLETED"
      ? t("The prediction is complete. Review the result and supporting evidence.")
      : t("The predictor is checking the project. The dial advances as new evidence arrives.")
    : hasPortrait
      ? preliminaryPrediction
        ? t("Your confirmed project portrait is saved. Review it and start a preliminary prediction to test the most important assumptions.")
        : t("Your confirmed project portrait is saved. Review it and start the full prediction when you are ready.")
      : t("No details yet. Complete the four basic sections to start a prediction.");

  return (
    <section className="binnacle">
      <div className="binnacle-plate">
        <div className="wheel-frame instrument-shell project-wheel-frame">
          <EvaluationWheel
            sectors={sectors}
            team={team}
            architectureGeneration={supervisorMode ? "supervisor-1p4-v1" : "legacy-1p5"}
            notch={notch}
            activeSector={activeSector}
            onSelectSector={setActiveSector}
          />
          <div className="core-read project-core-read">
            <strong className="core-name" data-name-length={productNameLength(project.name)} title={project.name}>{project.name}</strong>
            <a className="button core-cta" href={primaryHref}>
              {primaryLabel}
            </a>
          </div>
        </div>
      </div>

      <aside className="binnacle-side" aria-label={t("Project readings")}>
        <div className="side-lead">
          <p className="prediction-target"><strong>{t("Prediction target")}</strong>{project.name}</p>
          <p className="plate-kicker">{t("Next step")}</p>
          <p className="side-lead-text">{nextStep}</p>
          <a className="button secondary" href={primaryHref}>
            {primaryLabel}
          </a>
        </div>

        <div className="side-block">
          <p className="plate-kicker">
            {active[0]} · {t(active[1])}
          </p>
          <p className="side-note">{t(active[2])}</p>
        </div>

        <div className="side-block">
          <p className="plate-kicker">
            {t("{count} evidence items · progress {notch} / {total}", { count: evidenceCount, notch, total: NOTCHES_PER_REVOLUTION })}
          </p>
          {/* 棘轮刻度条：每一格对应一条真实证据落库。
              没有事件就不亮 —— 这不是进度条动画。 */}
          <div className="detent-bar" aria-label={t("Advanced {notch} / {total} notches", { notch, total: NOTCHES_PER_REVOLUTION })}>
            {Array.from({ length: NOTCHES_PER_REVOLUTION }, (_, i) => (
              <i key={i} data-lit={i < notch} />
            ))}
          </div>
        </div>

        <div className="side-block">
          <p className="plate-kicker">{t("Prediction team {topology}", { topology: supervisorMode ? t("Project lead 1+4") : "1+5" })}</p>
          <ul className="agent-chips">
            {needles.map((n) => (
              <li key={n.key} data-state={n.status.toLowerCase()}>
                <span className="chip-name">{n.name}</span>
                <span className="chip-read">
                  {n.evidence ? t("{count} items", { count: n.evidence }) : status(n.status)}
                </span>
              </li>
            ))}
          </ul>
        </div>

        {runs.length > 0 && (
          <div className="side-block version-history-block">
            <div className="version-history-heading">
              <p className="plate-kicker">{t("Version history")}</p>
              <span>{t("{count} prediction versions", { count: runs.length })}</span>
            </div>
            <label className="version-search">
              <span className="field-name">{t("Find a version")}</span>
              <input
                type="search"
                value={versionSearch}
                onChange={event => setVersionSearch(event.target.value)}
                placeholder={t("Search by version, for example V2")}
              />
            </label>
            <ul className="record-list">
              {visibleRuns.map((run) => (
                <li key={run.run_id}>
                  <a href={`/runs/${run.run_id}`}>
                    {runVersionLabel(run, runs.length - runs.indexOf(run))}
                  </a>
                  <span className="status" data-state={run.status.toLowerCase()}>
                    {status(run.status)}
                  </span>
                </li>
              ))}
            </ul>
            {visibleRuns.length === 0 && (
              <div className="version-search-empty" role="status">
                <span>{t("No matching versions.")}</span>
                <button type="button" className="quiet" onClick={() => setVersionSearch("")}>
                  {t("Clear search")}
                </button>
              </div>
            )}
          </div>
        )}
      </aside>
    </section>
  );
}
