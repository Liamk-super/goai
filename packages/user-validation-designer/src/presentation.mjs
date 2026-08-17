/**
 * Human-facing presentation renderer.
 *
 * Internal analysis remains audit-heavy. This module builds the default
 * student-developer report from validated structured output only. It never
 * calls a model and never invents facts.
 */

import { createHash } from "node:crypto";
import { normalizeReportSources } from "../../_shared/report-source-normalization.mjs";

const BANNED_VISIBLE_TOKENS = [
  /\btask_id\b/giu,
  /\bproject_id\b/giu,
  /\bevidence_id\b/giu,
  /\bpersona_id\b/giu,
  /\bhypothesis_id\b/giu,
  /\bclaim_id\b/giu,
  /\bplan_id\b/giu,
  /\btier\b/giu,
  /\bfact_type\b/giu,
  /\bcap_reason\b/giu,
  /\brun_manifest\b/giu,
  /\bexecution_log\b/giu,
  /\bhandoff\b/giu,
  /\bintegrity_diagnostics\b/giu,
  /\bscoring_schema_version\b/giu,
  /\bS[1-7](?:a|b|_synthesis)?\b/gu,
  /\bE[0-5]\b/gu,
  /\bKB-[A-Z0-9-]+\b/gu,
];

const MAX_TARGET_GROUPS = 3;
const MAX_PROBLEMS = 3;
const MAX_PRIORITIES = 3;
const MAX_VALIDATION_ACTIONS = 2;
const MAX_EVIDENCE_SIGNALS = 2;
const MAX_DETAIL_EVIDENCE_SIGNALS = 6;
const MAX_DETAIL_VALIDATION_ACTIONS = 3;
const MAX_DETAIL_PERSONAS = 5;
const MAX_VISIBLE_CHARS = 1200;
const TARGET_VISIBLE_CHARS = 1000;

const DIMENSION_LABELS = Object.freeze({
  demand_strength: "用户需求",
  usage_frequency: "使用频率",
  pain_severity: "痛点强度",
  alternative_gap: "替代差距",
  willingness_to_pay: "付费意愿",
  virality: "传播性",
});

function cleanText(value, fallback = "") {
  let text = String(value ?? fallback)
    .replace(/\s+/gu, " ")
    .replace(/\|/gu, "／")
    .trim();
  for (const pattern of BANNED_VISIBLE_TOKENS) text = text.replace(pattern, "");
  return text
    .replace(/\(\s*模拟\s*\)/gu, "（模拟）")
    .replace(/（\s*模拟\s*）/gu, "（模拟）")
    .replace(/\s{2,}/gu, " ")
    .trim();
}

function clip(value, max = 60) {
  const text = cleanText(value);
  return text.length > max ? `${text.slice(0, Math.max(0, max - 1))}…` : text;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function demandLabel(judgment) {
  return {
    strong: "强",
    medium: "中",
    weak: "偏弱",
    very_weak: "偏弱",
    unverified: "待验证",
  }[judgment] ?? "待验证";
}

function confidenceLabel(confidence) {
  return { high: "高", medium: "中", low: "低" }[confidence] ?? "低";
}

function summarySentence(structured) {
  const label = demandLabel(structured.user_value_judgment);
  const hasReal = structured.evidence_level_summary?.has_real_user_evidence === true;
  if (label === "强") return "核心用户需求已出现较明确的真实信号，重点转向把关键体验做稳并继续观察复用。";
  if (label === "中") return hasReal
    ? "用户需求已有真实信号，但持续使用或替代优势仍没有完全证明。"
    : "方向可能成立，但目前主要来自模拟推演，仍需真实用户验证。";
  if (label === "偏弱") return hasReal
    ? "需求存在，但当前切换或持续使用动力不够强，先解决最影响复用的问题。"
    : "还没有足够真实用户证据证明需求强度，先验证核心场景和替代差距。";
  return "目前还不能确认用户是否真正需要并会持续使用，先补最关键的真实用户证据。";
}

function scenarioForPersona(structured, persona) {
  return (structured.scenarios_and_alternatives ?? []).find((scenario) => scenario.persona_id === persona.persona_id);
}

function personaScore(structured, persona) {
  const scenario = scenarioForPersona(structured, persona);
  const urgency = Number(persona?.behavior_keys?.urgency ?? 3);
  const archetype = persona?.archetype === "high_need" ? 2 : persona?.archetype === "edge_case" ? -1 : 0;
  const verdict = scenario?.switching_forces?.verdict === "will_switch" ? 2
    : scenario?.switching_forces?.verdict === "will_not_switch" ? -2
      : 0;
  return urgency + archetype + verdict;
}

function extractGoal(goal) {
  const text = cleanText(goal);
  const match = text.match(/我想(.+?)(?:，?以便|$)/u);
  return clip(match?.[1] ?? text, 28);
}

function personaAlternative(structured, persona) {
  const scenario = scenarioForPersona(structured, persona);
  return cleanText(
    persona?.behavior_keys?.alternative_in_use
      ?? scenario?.alternatives?.find((item) => item?.alternative_type !== "do_nothing")?.name
      ?? scenario?.alternatives?.[0]?.name,
  );
}

function behaviorLabel(structured, persona, type) {
  const scenario = scenarioForPersona(structured, persona);
  const trigger = clip(scenario?.trigger_event, 28);
  const goal = extractGoal(persona?.goal_statement);
  const alternative = clip(personaAlternative(structured, persona), 20);
  const rejection = cleanText(persona?.rejection_reasons?.[0] ?? persona?.barriers?.[0]);

  if (type === "core") {
    if (trigger && goal) return clip(`${trigger}，需要${goal}的人`, 46);
    if (goal) return clip(`当前问题最急、需要${goal}的人`, 46);
    return "当前问题最急、已经主动寻找更好解决办法的人";
  }
  if (type === "consider") {
    if (alternative && !/什么都不做/u.test(alternative)) return clip(`已经在用${alternative}，但仍想要更好结果的人`, 46);
    if (rejection) return "已有可用替代方案，但仍对当前效果不满意的人";
    return "已有替代方案，但仍可能被更好体验争取的人";
  }
  if (alternative && !/什么都不做/u.test(alternative)) return clip(`当前不够紧迫，且${alternative}基本够用的人`, 46);
  return "当前痛点不强、切换动力较弱的人";
}

function targetGroups(structured) {
  const personas = [...(structured.personas ?? [])];
  if (personas.length === 0) return [];
  personas.sort((a, b) => personaScore(structured, b) - personaScore(structured, a));
  const core = personas[0];
  const low = personas.length >= 3 ? personas.at(-1) : null;
  const middle = personas.find((persona) => persona !== core && persona !== low) ?? (personas.length >= 2 ? personas[1] : null);
  const groups = [{ key: "core", title: "核心用户", persona: core }];
  if (middle) groups.push({ key: "consider", title: "可争取用户", persona: middle });
  if (low) groups.push({ key: "later", title: "暂不优先", persona: low });
  return groups.slice(0, MAX_TARGET_GROUPS).map((group) => ({
    key: group.key,
    title: group.title,
    label: behaviorLabel(structured, group.persona, group.key),
    why: clip(group.key === "later"
      ? group.persona?.rejection_reasons?.[0] ?? group.persona?.barriers?.[0]
      : group.persona?.motivation ?? group.persona?.pains?.[0]?.description, 42),
    rejection: clip(group.persona?.rejection_reasons?.[0] ?? group.persona?.barriers?.[0], 42),
  }));
}

function friendlySource(record) {
  const kind = String(record?.kind ?? "");
  const source = String(record?.source ?? "");
  if (kind === "retention_data" || kind === "usage_data" || kind === "funnel_data") return "产品后台数据";
  if (kind === "interview") return "真实用户访谈";
  if (kind === "usability_test") return "真实可用性测试";
  if (kind === "survey") return "真实用户问卷";
  if (kind === "payment_record" || kind === "contract" || kind === "purchase") return "真实交易记录";
  if (kind === "team_statement") return "团队材料";
  if (/论坛|社区|评论|帖子/u.test(source)) return "公开用户讨论";
  return "用户侧材料";
}

function signalPriority(record) {
  return {
    payment_record: 0,
    contract: 0,
    retention_data: 1,
    usage_data: 1,
    funnel_data: 1,
    usability_test: 2,
    interview: 3,
    survey: 4,
    team_statement: 9,
  }[record?.kind] ?? 5;
}

function normalizeRetentionWording(text, records) {
  const sourceHasExplicitD30 = (records ?? []).some((record) => /\bD30\b/iu.test(String(record.observation ?? "")));
  if (sourceHasExplicitD30) return text;
  return text
    .replace(/D30\s*(?:二次)?留存/giu, "30 天内复用")
    .replace(/30\s*天\s*留存/gu, "30 天内复用");
}

function zeroPaymentObservation(observation) {
  return /(?:付费|支付|成交)[^0-9]{0,6}0(?:\D|$)/u.test(observation)
    || /(?:^|\D)0\s*(?:人|笔|单|次)?\s*(?:付费|支付|成交)/u.test(observation);
}

function evidenceSignals(structured, ingestedEvidence) {
  const records = [...(ingestedEvidence ?? [])]
    .filter((record) => record?.kind !== "team_statement")
    .sort((a, b) => signalPriority(a) - signalPriority(b));
  const output = [];
  for (const record of records) {
    let observation = normalizeRetentionWording(cleanText(record.observation), ingestedEvidence);
    if (!observation) continue;
    if ((record.kind === "payment_record" || /付费|支付|成交/u.test(observation)) && zeroPaymentObservation(observation)) {
      observation = observation.replace(/[；;，,]?\s*(?:当前)?(?:真实)?付费\s*0\s*人?/u, "").trim();
      if (record.kind === "payment_record") {
        observation = observation ? `${observation}；付费仍需通过正式收费入口验证` : "付费仍需通过正式收费入口继续验证";
      }
    }
    if (!observation) continue;
    output.push({ source: friendlySource(record), observation: clip(observation, 58) });
    if (output.length >= MAX_EVIDENCE_SIGNALS) break;
  }
  if (output.length === 0) {
    const insight = structured.simulated_findings?.insights?.[0];
    if (insight) output.push({ source: "AI 模拟推演，仅供参考", observation: clip(insight.observation, 72) });
  }
  return output;
}

function issueTitle(issue) {
  const description = cleanText(issue?.description);
  if (/模板|通用建议|泛化/u.test(description)) return "反馈过于通用";
  if (/简历|岗位|项目.*追问|追问.*项目/u.test(description)) return "个性化追问还不够深";
  if (/等待|卡住|卡死|加载|进度/u.test(description)) return "等待过程缺少明确反馈";
  if (/留存|持续使用|再次使用|回来|复用/u.test(description)) return "持续使用理由还没有被证明";
  if (/上传/u.test(description)) return "上传过程存在阻塞";
  if (/麦克风|语音/u.test(description)) return "语音交互存在阻塞";
  return clip(description, 28);
}

function issueMeaning(issue) {
  const description = cleanText(issue?.description);
  if (/简历|岗位|追问/u.test(description)) return "如果和通用 AI 差别不明显，用户没有切换理由。";
  if (/模板|通用|泛化/u.test(description) || issue?.cause_type === "content") return "用户看不到针对自己的价值，就很难形成再次使用理由。";
  if (/等待|卡住|加载|进度/u.test(description) || issue?.cause_type === "performance") return "关键路径的不确定感会直接制造中途流失。";
  if (/留存|持续|再次|回来|复用/u.test(description)) return "先确认需求是否真的会重复发生，再决定是否扩功能。";
  if (issue?.cause_type === "functional") return "核心任务做不完，会直接让用户放弃。";
  return "这个问题会削弱用户对产品价值的判断。";
}

function problemFromInsight(insight) {
  return {
    title: clip(insight.theme || insight.observation || "用户问题", 28),
    meaning: clip(insight.root_cause ? `可能原因：${insight.root_cause}` : "仍需真实用户确认。", 46),
    evidence: clip(insight.observation, 54),
    cause: /留存|持续|复用|回来/u.test(String(insight.observation ?? "")) ? "retention" : "unknown",
    severity: "major",
  };
}

function visibleProblems(structured) {
  const problems = [];
  for (const issue of structured.simulated_findings?.experience_issues ?? []) {
    if (problems.length >= MAX_PROBLEMS) break;
    const title = issueTitle(issue);
    if (problems.some((item) => item.title === title)) continue;
    problems.push({
      title,
      meaning: issueMeaning(issue),
      evidence: clip(issue.description, 52),
      cause: issue.cause_type,
      severity: issue.severity,
    });
  }
  for (const insight of structured.simulated_findings?.insights ?? []) {
    if (problems.length >= MAX_PROBLEMS) break;
    const item = problemFromInsight(insight);
    if (problems.some((problem) => problem.title === item.title)) continue;
    problems.push(item);
  }
  for (const problem of structured.top_user_problems ?? []) {
    if (problems.length >= MAX_PROBLEMS) break;
    const title = clip(problem.question, 30);
    if (!title || problems.some((item) => item.title === title)) continue;
    problems.push({
      title,
      meaning: "这个问题还没有被充分证明或解决，会影响下一步产品判断。",
      evidence: title,
      cause: /留存|持续|复用|回来/u.test(title) ? "retention" : "unknown",
      severity: "major",
    });
  }
  return problems.slice(0, MAX_PROBLEMS);
}

function recommendationForProblem(problem) {
  const combined = `${problem.title} ${problem.evidence}`;
  if (/等待|卡住|加载|进度/u.test(combined) || problem.cause === "performance") return "补充明确进度和等待反馈；验证关键路径中途放弃是否下降。";
  if (/简历|岗位|追问/u.test(combined)) return "让每轮追问都引用具体经历或目标要求；验证用户是否明显觉得比通用 AI 更针对。";
  if (/模板|通用|泛化|评价|建议/u.test(combined)) return "让报告引用本次回答，指出问题、原因和改写示例；验证“建议太泛”的反馈是否下降。";
  if (/留存|持续|复用|回来/u.test(combined) || problem.cause === "retention") return "先验证用户是否会在下一次同类场景再次回来，再决定是否增加持续使用功能。";
  if (problem.cause === "functional") return "先修复核心任务阻塞，再用同一任务复测完成率。";
  return "围绕该问题做最小改动，用同一用户任务验证问题是否明显下降。";
}

function developmentPriorities(problems) {
  return problems.slice(0, MAX_PRIORITIES).map((problem, index) => ({
    priority: index < 2 && ["blocker", "major"].includes(problem.severity) ? "P0" : index === 0 ? "P0" : "P1",
    action: clip(recommendationForProblem(problem), 66),
  }));
}

function methodName(method) {
  return {
    problem_interview: "流失用户访谈",
    usability_test: "真实可用性测试",
    survey: "定性后的问卷",
    landing_page_test: "真实落地页行为测试",
    trial_cohort_retention: "真实复用观察",
    pricing_experiment: "真实收费测试",
    presale_or_deposit: "预售或定金测试",
  }[method] ?? "真实用户验证";
}

function validationActions(structured) {
  return (structured.validation_plans ?? [])
    .filter((plan) => plan.duration?.fits_constraints !== false)
    .slice(0, MAX_VALIDATION_ACTIONS)
    .map((plan) => ({
      what: clip(plan.hypothesis, 28),
      how: clip(`${methodName(plan.method)}：${plan.tasks_or_questions?.[0]?.content ?? "收集真实用户行为"}`, 32),
      result: clip(plan.success_threshold?.expression ?? plan.success_metrics?.[0]?.metric ?? "真实用户行为是否改善", 28),
    }));
}

function scoreLabel(score, counted) {
  if (counted === false || score == null) return "待验证";
  if (score >= 4) return "强";
  if (score === 3) return "中";
  return "偏弱";
}

function friendlyBasis(key, dimension) {
  let basis = cleanText(dimension?.basis);
  if (!basis || /Applicable real evidence|Calibrated by|Sample adequacy|requires rescore/iu.test(basis)) {
    return {
      demand_strength: "看真实场景是否足够紧迫、用户是否已经付出解决成本",
      usage_frequency: "看需求是否会重复发生，而不是只在单次事件出现",
      pain_severity: "看现有问题是否足够痛，用户是否主动寻找替代",
      alternative_gap: "看产品是否明显优于现有免费或人工方案",
      willingness_to_pay: "看是否出现真实支付或明确承诺行为",
      virality: "看用户是否会自然推荐、协作或分享",
    }[key] ?? "当前依据仍不充分";
  }
  basis = basis
    .replace(/\([^)]*模拟[^)]*\)/gu, "")
    .replace(/（[^）]*模拟[^）]*）/gu, "")
    .replace(/证据等级[^，。；]*/gu, "")
    .trim();
  return clip(basis, 22);
}

function scoreSnapshot(structured) {
  const dimensions = structured.user_value_score?.dimensions ?? {};
  return Object.entries(DIMENSION_LABELS).map(([key, label]) => {
    const dimension = dimensions[key] ?? {};
    return {
      label,
      judgment: scoreLabel(dimension.score, dimension.counted),
      basis: friendlyBasis(key, dimension),
    };
  });
}

function importantMissing(structured) {
  const entry = (structured.missing_information ?? [])[0];
  if (!entry) return "";
  const field = String(entry.field ?? "");
  if (field === "product_tasks") return "还缺核心任务脚本，暂时不能判断关键流程是否能独立完成。";
  if (field.startsWith("product_profile.url")) return "还缺可访问产品或可靠体验记录，首次使用体验尚未验证。";
  if (field.includes("retention") || /留存|复用/u.test(String(entry.why_it_matters ?? ""))) return "还缺统一口径的复用数据，持续使用判断仍需验证。";
  if (field.startsWith("existing_user_evidence")) return "还缺真实用户反馈或行为数据，当前判断仍需校准。";
  return clip(entry.why_it_matters ?? entry.how_to_obtain, 54);
}


function detailedEvidenceSignals(structured, ingestedEvidence) {
  const records = [...(ingestedEvidence ?? [])]
    .filter((record) => record?.kind !== "team_statement")
    .sort((a, b) => signalPriority(a) - signalPriority(b));
  const output = [];
  for (const record of records) {
    let observation = normalizeRetentionWording(cleanText(record.observation), ingestedEvidence);
    if (!observation) continue;
    if ((record.kind === "payment_record" || /付费|支付|成交/u.test(observation)) && zeroPaymentObservation(observation)) {
      observation = observation.replace(/[；;，,]?\s*(?:当前)?(?:真实)?付费\s*0\s*人?/u, "").trim();
      if (record.kind === "payment_record") {
        observation = observation ? `${observation}；付费仍需通过正式收费入口验证` : "付费仍需通过正式收费入口继续验证";
      }
    }
    if (!observation) continue;
    output.push({ source: friendlySource(record), observation: clip(observation, 110) });
    if (output.length >= MAX_DETAIL_EVIDENCE_SIGNALS) break;
  }
  if (output.length === 0) {
    for (const insight of structured.simulated_findings?.insights ?? []) {
      const observation = clip(insight.observation, 110);
      if (observation) output.push({ source: "AI 模拟推演，仅供参考", observation });
      if (output.length >= MAX_DETAIL_EVIDENCE_SIGNALS) break;
    }
  }
  return output;
}

function switchingLabel(verdict) {
  return {
    will_switch: "切换动力较强",
    borderline: "是否切换仍不确定",
    will_not_switch: "当前切换动力较弱",
  }[verdict] ?? "仍需验证";
}

function detailedPersonas(structured) {
  return (structured.personas ?? []).slice(0, MAX_DETAIL_PERSONAS).map((persona) => {
    const scenario = scenarioForPersona(structured, persona);
    const pains = (persona.pains ?? [])
      .map((pain) => cleanText(pain?.description))
      .filter(Boolean)
      .slice(0, 2);
    return {
      title: clip(persona.label || behaviorLabel(structured, persona, "consider"), 44),
      goal: clip(persona.goal_statement, 84),
      scene: clip(scenario?.trigger_event, 72),
      motivation: clip(persona.motivation, 70),
      alternative: clip(personaAlternative(structured, persona), 48),
      rejection: clip(persona.rejection_reasons?.[0] ?? persona.barriers?.[0], 70),
      switching: switchingLabel(scenario?.switching_forces?.verdict),
      pains,
    };
  });
}

function detailedValidationPlans(structured) {
  return (structured.validation_plans ?? [])
    .filter((plan) => plan.duration?.fits_constraints !== false)
    .slice(0, MAX_DETAIL_VALIDATION_ACTIONS)
    .map((plan) => {
      const sampleValue = plan.sample_size?.value;
      const sampleUnit = plan.sample_size?.unit;
      const sample = sampleValue != null ? `${sampleValue}${sampleUnit ? ` ${cleanText(sampleUnit)}` : ""}` : "按可执行样本推进";
      const cost = plan.estimated_cost?.money_cny != null ? `约 ¥${plan.estimated_cost.money_cny}` : "按现有资源执行";
      const duration = plan.duration?.weeks != null ? `${plan.duration.weeks} 周` : "短周期验证";
      return {
        title: clip(plan.hypothesis, 62),
        method: methodName(plan.method),
        target: clip(plan.target_participants?.segment_label ?? plan.validation_target?.falsifiable_statement, 76),
        sample: cleanText(sample),
        duration: cleanText(duration),
        cost: cleanText(cost),
        tasks: (plan.tasks_or_questions ?? []).map((item) => clip(item?.content, 92)).filter(Boolean).slice(0, 3),
        threshold: clip(plan.success_threshold?.expression ?? plan.success_metrics?.[0]?.metric ?? "观察真实行为是否改善", 92),
      };
    });
}

function detailedMissing(structured) {
  return (structured.missing_information ?? []).slice(0, 3).map((entry) => {
    const field = String(entry.field ?? "");
    if (field === "product_tasks") return "还缺核心任务脚本，无法完整判断关键流程是否能独立完成。";
    if (field.startsWith("product_profile.url")) return "还缺可访问产品或可靠体验记录，首次使用体验仍未验证。";
    if (field.includes("retention") || /留存|复用/u.test(String(entry.why_it_matters ?? ""))) return "还缺统一口径的复用数据，持续使用判断仍需校准。";
    if (field.startsWith("existing_user_evidence")) return "还缺真实用户反馈或行为数据，当前判断主要用于提出待验证方向。";
    return clip(entry.why_it_matters ?? entry.how_to_obtain, 82);
  }).filter(Boolean);
}

function detailProblemEvidence(problems) {
  return problems.map((problem) => ({
    title: problem.title,
    evidence: clip(problem.evidence, 100),
    meaning: clip(problem.meaning, 84),
  }));
}


function headlineProblem(problems, whyNot) {
  const text = `${problems.map((item) => item.title).join(" ")} ${whyNot}`;
  if ((/通用|个性化|追问|反馈/u.test(text)) && /ChatGPT|面经|替代|免费/u.test(text)) {
    return "产品还没有证明自己比现有替代方案明显更好";
  }
  if (/通用|个性化|追问|反馈/u.test(text)) return "核心价值还不够针对，用户很难感知明显差异";
  if (/留存|持续|复用|回来/u.test(text)) return "持续使用理由还没有被证明";
  return problems[0]?.title ?? "核心用户问题仍未被充分验证";
}

function buildViewModel({ input, structured, ingestedEvidence }) {
  const productName = cleanText(input?.product_profile?.name, "产品");
  const groups = targetGroups(structured);
  const problems = visibleProblems(structured);
  const priorities = developmentPriorities(problems);
  const validations = validationActions(structured);
  const evidence = evidenceSignals(structured, ingestedEvidence);
  const scores = scoreSnapshot(structured);
  const core = groups.find((group) => group.title === "核心用户")?.label ?? "当前问题最急、已经主动寻找更好解决办法的人";
  const consider = groups.find((group) => group.title === "可争取用户")?.label ?? "已有替代方案，但仍可能被更好体验争取的人";
  const later = groups.find((group) => group.title === "暂不优先")?.label ?? "当前痛点不强、切换动力较弱的人";
  const firstWhyUse = groups.find((group) => group.title === "核心用户")?.why || "当前问题足够紧迫，希望更快获得结果";
  const firstWhyNot = groups.find((group) => group.title === "可争取用户")?.rejection
    || groups.find((group) => group.title === "暂不优先")?.rejection
    || "现有替代方案已经能解决一部分需求";
  const firstProblem = headlineProblem(problems, firstWhyNot);

  return {
    productName,
    demand: demandLabel(structured.user_value_judgment),
    confidence: confidenceLabel(structured.evidence_confidence),
    summary: summarySentence(structured),
    groups,
    core,
    consider,
    later,
    bestFit: clip(`当前最值得争取的是${core.replace(/的人$/u, "的人群")}，而不是所有目标用户。`, 76),
    whyUse: clip(firstWhyUse, 48),
    whyNot: clip(firstWhyNot, 48),
    maxProblem: firstProblem,
    problems,
    priorities,
    validations,
    evidence,
    scores,
    missing: importantMissing(structured),
    detailEvidence: detailedEvidenceSignals(structured, ingestedEvidence),
    detailPersonas: detailedPersonas(structured),
    detailValidations: detailedValidationPlans(structured),
    detailMissing: detailedMissing(structured),
    detailProblems: detailProblemEvidence(problems),
  };
}

function renderMarkdownFromView(view, ingestedEvidence) {
  const lines = [
    `# ${view.productName}｜用户验证`,
    "",
    `> **用户需求：${view.demand}**　**证据可信度：${view.confidence}**`,
    `> ${view.summary}`,
    "",
    "## 1. 一眼看懂",
    `- **核心用户：** ${view.core}`,
    `- **可争取：** ${view.consider}`,
    `- **暂不优先：** ${view.later}`,
    `- **为什么会用：** ${view.whyUse}`,
    `- **为什么不用：** ${view.whyNot}`,
    `- **最大问题：** ${view.maxProblem}`,
  ];

  lines.push("", "## 2. 当前最关键的问题");
  if (view.problems.length === 0) lines.push("当前还没有足够证据定位具体产品问题，先补真实体验或核心任务数据。" );
  else view.problems.forEach((problem, index) => lines.push(`${index + 1}. **${problem.title}**：${clip(problem.meaning, 50)}`));

  lines.push("", "## 3. 下一版先改什么");
  if (view.priorities.length === 0) lines.push("当前不建议凭空加功能，先验证最关键的用户问题。" );
  else view.priorities.forEach((item) => lines.push(`- **${item.priority}**｜${item.action}`));

  lines.push("", "## 4. 为什么这么判断");
  lines.push("| 维度 | 判断 | 依据 |", "|---|---|---|");
  for (const item of view.scores) lines.push(`| ${item.label} | ${item.judgment} | ${item.basis} |`);
  for (const signal of view.evidence) lines.push(`- **${signal.source}**：${signal.observation}`);
  if (view.missing) lines.push(`> **仍待确认：** ${view.missing}`);

  lines.push("", "## 5. 下一步怎么验证");
  if (view.validations.length === 0) lines.push("1. 围绕最大问题做最小验证，先看真实用户行为是否改变。" );
  else view.validations.forEach((item, index) => lines.push(`${index + 1}. **${item.what}**｜${item.how}｜看：${item.result}`));

  let markdown = lines.join("\n").replace(/\n{3,}/gu, "\n\n").trim();
  markdown = normalizeRetentionWording(markdown, ingestedEvidence);
  for (const pattern of BANNED_VISIBLE_TOKENS) markdown = markdown.replace(pattern, "");
  markdown = markdown.replace(/[ \t]{2,}/gu, " ").replace(/ \n/gu, "\n");

  // Keep the default report scannable. Remove secondary evidence before ever
  // truncating a sentence; the internal structure remains intact elsewhere.
  if (markdown.length > MAX_VISIBLE_CHARS && view.evidence.length > 0) {
    const evidenceLines = view.evidence.map((signal) => `- **${signal.source}**：${signal.observation}`);
    for (const line of evidenceLines.reverse()) {
      markdown = markdown.replace(`\n${line}`, "");
      if (markdown.length <= MAX_VISIBLE_CHARS) break;
    }
  }
  if (markdown.length > MAX_VISIBLE_CHARS) {
    markdown = markdown
      .split("\n")
      .map((line) => line.length > 96 ? `${line.slice(0, 95)}…` : line)
      .join("\n");
  }
  return markdown;
}

function renderHtmlFromView(view) {
  const targetCards = view.groups.map((group) => {
    const tone = group.key === "core" ? "core" : group.key === "consider" ? "consider" : "later";
    return `
      <article class="audience-card audience-${tone}">
        <div class="audience-kicker">${escapeHtml(group.title)}</div>
        <strong>${escapeHtml(group.label)}</strong>
        ${group.why ? `<p>${escapeHtml(group.why)}</p>` : ""}
      </article>`;
  }).join("");

  const problemCards = view.problems.length > 0
    ? view.problems.map((problem, index) => `
      <article class="problem-card">
        <span class="problem-index">0${index + 1}</span>
        <div><strong>${escapeHtml(problem.title)}</strong><p>${escapeHtml(problem.meaning)}</p></div>
      </article>`).join("")
    : `<p class="muted">当前还没有足够证据定位具体产品问题。</p>`;

  const problemEvidence = view.detailProblems.length > 0
    ? view.detailProblems.map((problem) => `<li><strong>${escapeHtml(problem.title)}</strong><span>${escapeHtml(problem.evidence || problem.meaning)}</span></li>`).join("")
    : `<li><span>暂时没有可展开的具体问题依据。</span></li>`;

  const priorityRows = view.priorities.length > 0
    ? view.priorities.map((item) => `<li><b class="priority-tag ${item.priority === "P0" ? "priority-p0" : "priority-p1"}">${escapeHtml(item.priority)}</b><span>${escapeHtml(item.action)}</span></li>`).join("")
    : `<li><b class="priority-tag priority-p0">P0</b><span>先验证最大用户问题，不凭空增加功能。</span></li>`;

  const scoreRows = view.scores.map((item) => `<tr><td>${escapeHtml(item.label)}</td><td><b>${escapeHtml(item.judgment)}</b></td><td>${escapeHtml(item.basis)}</td></tr>`).join("");
  const evidenceList = view.detailEvidence.map((signal) => `<li><b>${escapeHtml(signal.source)}</b><span>${escapeHtml(signal.observation)}</span></li>`).join("");
  const missingList = view.detailMissing.map((item) => `<li>${escapeHtml(item)}</li>`).join("");

  const personaDetails = view.detailPersonas.length > 0
    ? view.detailPersonas.map((persona) => `
      <article class="persona-detail">
        <h4>${escapeHtml(persona.title)}</h4>
        <dl>
          ${persona.scene ? `<div><dt>典型场景</dt><dd>${escapeHtml(persona.scene)}</dd></div>` : ""}
          ${persona.goal ? `<div><dt>核心目标</dt><dd>${escapeHtml(persona.goal)}</dd></div>` : ""}
          ${persona.alternative ? `<div><dt>当前替代</dt><dd>${escapeHtml(persona.alternative)}</dd></div>` : ""}
          ${persona.motivation ? `<div><dt>为什么会用</dt><dd>${escapeHtml(persona.motivation)}</dd></div>` : ""}
          ${persona.rejection ? `<div><dt>为什么可能不用</dt><dd>${escapeHtml(persona.rejection)}</dd></div>` : ""}
          <div><dt>切换判断</dt><dd>${escapeHtml(persona.switching)}</dd></div>
        </dl>
        ${persona.pains.length ? `<p class="detail-pain"><b>主要痛点：</b>${escapeHtml(persona.pains.join("；"))}</p>` : ""}
      </article>`).join("")
    : `<p class="muted">当前没有足够信息形成详细用户画像。</p>`;

  const validationDetails = view.detailValidations.length > 0
    ? view.detailValidations.map((plan, index) => `
      <article class="validation-detail">
        <div class="validation-head"><span>0${index + 1}</span><strong>${escapeHtml(plan.title)}</strong></div>
        <div class="validation-meta"><span>${escapeHtml(plan.method)}</span><span>${escapeHtml(plan.sample)}</span><span>${escapeHtml(plan.duration)}</span><span>${escapeHtml(plan.cost)}</span></div>
        ${plan.target ? `<p><b>验证对象：</b>${escapeHtml(plan.target)}</p>` : ""}
        ${plan.tasks.length ? `<ol>${plan.tasks.map((task) => `<li>${escapeHtml(task)}</li>`).join("")}</ol>` : ""}
        <p><b>看什么：</b>${escapeHtml(plan.threshold)}</p>
      </article>`).join("")
    : `<p class="muted">暂无可展开的完整验证方案。</p>`;

  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escapeHtml(view.productName)}｜用户验证</title>
<style>
:root{
  --ink:#172033;--muted:#667085;--line:#e6eaf0;--paper:#fff;--bg:#f5f7fb;
  --brand:#3157d5;--brand-dark:#1d3269;--brand-soft:#eef2ff;
  --core:#087f70;--core-soft:#ecfdf7;--core-line:#a7f3d0;
  --consider:#4f46e5;--consider-soft:#eef2ff;--consider-line:#c7d2fe;
  --later:#b66a09;--later-soft:#fff8e8;--later-line:#f7d9a8;
  --danger:#b42318;--danger-soft:#fff1f0;--danger-line:#fecaca;
  --success:#137a52;--warning:#a15c00;
}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;line-height:1.58}
.wrap{max-width:980px;margin:0 auto;padding:24px 18px 60px}.hero{background:linear-gradient(135deg,#172b55 0%,#3157d5 70%,#4f67d9 100%);color:#fff;border-radius:22px;padding:28px 30px;box-shadow:0 12px 36px #1f3a7820}.hero-top{display:flex;align-items:flex-start;justify-content:space-between;gap:20px}.hero h1{margin:0;font-size:29px;letter-spacing:-.02em}.badges{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}.badge{padding:5px 10px;border:1px solid #ffffff45;border-radius:999px;font-size:12px;background:#ffffff12}.hero p{margin:12px 0 0;max-width:780px;font-size:14px;color:#eef2ff}.reading-path{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:18px;padding-top:14px;border-top:1px solid #ffffff28;font-size:12px;color:#dbe4ff}.reading-path span{padding:4px 9px;border-radius:999px;background:#ffffff12}.reading-path i{font-style:normal;opacity:.55}
.section{background:var(--paper);border:1px solid var(--line);border-radius:18px;padding:22px;margin-top:14px;box-shadow:0 2px 10px #17203308}.section h2{font-size:18px;margin:0 0 15px}.section-lead{font-size:12px;color:var(--muted);margin:-7px 0 14px}.audiences{display:grid;grid-template-columns:repeat(3,1fr);gap:11px}.audience-card{position:relative;padding:16px 16px 15px;border-radius:14px;border:1px solid;min-height:132px}.audience-card::before{content:"";position:absolute;left:0;right:0;top:0;height:4px;border-radius:14px 14px 0 0}.audience-core{background:var(--core-soft);border-color:var(--core-line)}.audience-core::before{background:var(--core)}.audience-consider{background:var(--consider-soft);border-color:var(--consider-line)}.audience-consider::before{background:var(--consider)}.audience-later{background:var(--later-soft);border-color:var(--later-line)}.audience-later::before{background:var(--later)}.audience-kicker{font-size:12px;font-weight:800;margin:1px 0 7px}.audience-core .audience-kicker{color:var(--core)}.audience-consider .audience-kicker{color:var(--consider)}.audience-later .audience-kicker{color:var(--later)}.audience-card strong{display:block;font-size:14px;line-height:1.65}.audience-card p{font-size:12px;color:var(--muted);margin:7px 0 0}.best-fit{margin:12px 0 0;padding:10px 13px;border-radius:10px;background:#f8fafc;border:1px solid #edf0f4;font-size:13px;font-weight:650}.decision-strip{display:grid;grid-template-columns:1fr 1fr 1.15fr;margin-top:12px;border:1px solid var(--line);border-radius:13px;overflow:hidden;background:#fff}.decision-item{padding:11px 13px;min-height:76px}.decision-item+ .decision-item{border-left:1px solid var(--line)}.decision-item b{display:block;font-size:12px;margin-bottom:4px}.decision-use b{color:var(--success)}.decision-no b{color:var(--warning)}.decision-risk{background:var(--danger-soft)}.decision-risk b{color:var(--danger)}.decision-item span{font-size:13px}
.problem-card{display:flex;gap:13px;padding:13px 0;border-bottom:1px solid var(--line)}.problem-card:last-of-type{border-bottom:0}.problem-index{display:flex;align-items:center;justify-content:center;min-width:34px;height:28px;border-radius:8px;background:var(--danger-soft);color:var(--danger);font-weight:800;font-size:12px}.problem-card strong{font-size:14px}.problem-card p{margin:3px 0 0;color:var(--muted);font-size:13px}.priority{list-style:none;padding:0;margin:0}.priority li{display:flex;align-items:flex-start;gap:11px;padding:11px 0;border-bottom:1px solid var(--line)}.priority li:last-child{border-bottom:0}.priority-tag{min-width:36px;color:#fff;border-radius:7px;text-align:center;height:25px;line-height:25px;font-size:11px}.priority-p0{background:var(--danger)}.priority-p1{background:var(--brand)}.priority span{font-size:13px;padding-top:2px}
.inline-details{margin-top:8px;border-top:1px dashed var(--line);padding-top:9px}.inline-details summary,.deep-dive summary{list-style:none;cursor:pointer;color:var(--brand);font-size:13px;font-weight:700;display:flex;align-items:center;justify-content:space-between;gap:12px}.inline-details summary::-webkit-details-marker,.deep-dive summary::-webkit-details-marker{display:none}.inline-details summary::after,.deep-dive summary::after{content:"＋";font-size:18px;font-weight:400;color:#7a8bc4}.inline-details[open] summary::after,.deep-dive[open] summary::after{content:"−"}.detail-list{list-style:none;margin:10px 0 0;padding:0}.detail-list li{display:grid;grid-template-columns:minmax(120px,180px) 1fr;gap:12px;padding:8px 0;border-bottom:1px solid #f0f2f5;font-size:12px}.detail-list li:last-child{border-bottom:0}.detail-list strong{color:var(--ink)}.detail-list span{color:var(--muted)}
.deep-section{background:linear-gradient(180deg,#fbfcff,#fff)}.deep-intro{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:10px}.deep-intro p{margin:0;color:var(--muted);font-size:13px}.deep-dive{border:1px solid var(--line);border-radius:13px;padding:0 14px;background:#fff;margin-top:9px}.deep-dive summary{padding:13px 0}.deep-content{border-top:1px solid var(--line);padding:13px 0 5px}.score{width:100%;border-collapse:collapse;font-size:12px}.score td,.score th{padding:8px 9px;border-bottom:1px solid var(--line);text-align:left}.score th{color:var(--muted);font-weight:600;background:#fafbfc}.score td:nth-child(2) b{color:var(--brand)}.evidence-list,.missing-list{margin:11px 0 0;padding-left:18px;font-size:12px}.evidence-list li,.missing-list li{margin:6px 0}.evidence-list b{color:var(--ink)}.evidence-list span{color:var(--muted)}.note{background:var(--later-soft);border:1px solid var(--later-line);border-radius:9px;padding:9px 11px;font-size:12px;margin-top:10px}.persona-detail{padding:12px 0;border-bottom:1px solid var(--line)}.persona-detail:last-child{border-bottom:0}.persona-detail h4{margin:0 0 8px;font-size:14px}.persona-detail dl{display:grid;grid-template-columns:1fr 1fr;gap:6px 14px;margin:0}.persona-detail dl>div{display:grid;grid-template-columns:72px 1fr;gap:7px}.persona-detail dt{font-size:11px;color:var(--muted);font-weight:700}.persona-detail dd{margin:0;font-size:12px}.detail-pain{font-size:12px;color:var(--muted);margin:8px 0 0}.validation-detail{padding:12px 0;border-bottom:1px solid var(--line)}.validation-detail:last-child{border-bottom:0}.validation-head{display:flex;gap:10px;align-items:flex-start}.validation-head>span{color:var(--brand);font-weight:800}.validation-head strong{font-size:13px}.validation-meta{display:flex;gap:7px;flex-wrap:wrap;margin:8px 0}.validation-meta span{font-size:11px;color:#53607a;background:#f4f6fa;border-radius:999px;padding:3px 8px}.validation-detail p,.validation-detail li{font-size:12px}.validation-detail p{margin:6px 0}.validation-detail ol{margin:7px 0;padding-left:20px}.muted{color:var(--muted);font-size:13px}.footer{margin-top:16px;text-align:center;color:#8790a4;font-size:11px;padding:12px}
@media(max-width:760px){.wrap{padding:12px}.hero{padding:22px}.hero-top{display:block}.badges{justify-content:flex-start;margin-top:10px}.audiences{grid-template-columns:1fr}.decision-strip{grid-template-columns:1fr}.decision-item+ .decision-item{border-left:0;border-top:1px solid var(--line)}.persona-detail dl{grid-template-columns:1fr}.section{padding:17px}}
</style></head>
<body><main class="wrap">
<section class="hero">
  <div class="hero-top"><div><h1>${escapeHtml(view.productName)}｜用户验证</h1></div><div class="badges"><span class="badge">用户需求：${escapeHtml(view.demand)}</span><span class="badge">证据可信度：${escapeHtml(view.confidence)}</span></div></div>
  <p>${escapeHtml(view.summary)}</p>
  <div class="reading-path"><span>3–5 秒：先看结论</span><i>→</i><span>30 秒：看问题与动作</span><i>→</i><span>需要时：展开验证依据</span></div>
</section>

<section class="section summary-section">
  <h2>1. 一眼看懂</h2>
  <div class="audiences">${targetCards}</div>
  <div class="best-fit">${escapeHtml(view.bestFit)}</div>
  <div class="decision-strip">
    <div class="decision-item decision-use"><b>为什么会用</b><span>${escapeHtml(view.whyUse)}</span></div>
    <div class="decision-item decision-no"><b>为什么不用</b><span>${escapeHtml(view.whyNot)}</span></div>
    <div class="decision-item decision-risk"><b>最大问题</b><span>${escapeHtml(view.maxProblem)}</span></div>
  </div>
</section>

<section class="section">
  <h2>2. 当前最关键的问题</h2>
  <p class="section-lead">只保留会改变下一版产品决策的 Top 问题。</p>
  ${problemCards}
  <details class="inline-details"><summary>查看这些问题的具体依据</summary><ul class="detail-list">${problemEvidence}</ul></details>
</section>

<section class="section">
  <h2>3. 下一版先改什么</h2>
  <p class="section-lead">优先处理最影响用户切换和复用的事项，不为了“完整”继续堆功能。</p>
  <ul class="priority">${priorityRows}</ul>
</section>

<section class="section deep-section">
  <div class="deep-intro"><div><h2>4. 想验证结论？展开详细分析</h2><p>比赛材料、复盘或需要核查判断时再展开；默认阅读到这里已经足够。</p></div></div>

  <details class="deep-dive">
    <summary>评分框架与关键依据</summary>
    <div class="deep-content">
      <table class="score"><thead><tr><th>维度</th><th>判断</th><th>依据</th></tr></thead><tbody>${scoreRows}</tbody></table>
      ${evidenceList ? `<h4>关键证据</h4><ul class="evidence-list">${evidenceList}</ul>` : ""}
      ${missingList ? `<div class="note"><b>仍待确认：</b><ul class="missing-list">${missingList}</ul></div>` : (view.missing ? `<div class="note"><b>仍待确认：</b>${escapeHtml(view.missing)}</div>` : "")}
    </div>
  </details>

  <details class="deep-dive">
    <summary>用户画像与使用场景</summary>
    <div class="deep-content">${personaDetails}</div>
  </details>

  <details class="deep-dive">
    <summary>完整验证方案</summary>
    <div class="deep-content">${validationDetails}</div>
  </details>
</section>
<div class="footer">默认展示为开发者精简版；详细分析来自同一份结构化结果，可按需展开核查。</div>
</main></body></html>`;
}


function renderSummaryHtmlFromView(view) {
  const targetCards = view.groups.map((group) => {
    const tone = group.key === "core" ? "core" : group.key === "consider" ? "consider" : "later";
    return `
      <article class="audience-card audience-${tone}">
        <div class="audience-kicker">${escapeHtml(group.title)}</div>
        <strong>${escapeHtml(group.label)}</strong>
        ${group.why ? `<p>${escapeHtml(group.why)}</p>` : ""}
      </article>`;
  }).join("");

  const problemCards = view.problems.length > 0
    ? view.problems.map((problem, index) => `
      <article class="problem-card">
        <span class="problem-index">0${index + 1}</span>
        <div><strong>${escapeHtml(problem.title)}</strong><p>${escapeHtml(problem.meaning)}</p></div>
      </article>`).join("")
    : `<p class="muted">当前还没有足够证据定位具体产品问题。</p>`;

  const priorityRows = view.priorities.length > 0
    ? view.priorities.map((item) => `<li><b class="priority-tag ${item.priority === "P0" ? "priority-p0" : "priority-p1"}">${escapeHtml(item.priority)}</b><span>${escapeHtml(item.action)}</span></li>`).join("")
    : `<li><b class="priority-tag priority-p0">P0</b><span>先验证最大用户问题，不凭空增加功能。</span></li>`;

  return `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escapeHtml(view.productName)}｜用户验证精简版</title>
<style>
:root{--bg:#f5f7fb;--ink:#152033;--muted:#6b7485;--line:#e5e9f0;--brand:#3157d5;--core:#0f8b70;--core-soft:#eaf8f3;--core-line:#bde8d9;--consider:#5264d9;--consider-soft:#eef0ff;--consider-line:#d3d8ff;--later:#bd781b;--later-soft:#fff7e8;--later-line:#f1d6a8;--danger:#c2413b;--danger-soft:#fff0ef;--success:#14855f;--warning:#b87316}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;line-height:1.65}.wrap{max-width:980px;margin:auto;padding:22px}.hero{background:linear-gradient(135deg,#17264a,#3157d5);color:#fff;border-radius:20px;padding:28px 30px;box-shadow:0 16px 40px #213a8224}.hero-top{display:flex;justify-content:space-between;gap:18px;align-items:flex-start}.hero h1{font-size:26px;margin:0}.hero p{margin:12px 0 0;font-size:14px;max-width:760px;color:#eef2ff}.badges{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}.badge{font-size:12px;padding:5px 10px;border:1px solid #ffffff3b;background:#ffffff17;border-radius:999px}.reading-path{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:14px;font-size:12px;color:#d9e1ff}.section{background:#fff;border:1px solid var(--line);border-radius:17px;padding:22px;margin-top:14px;box-shadow:0 2px 10px #17203308}.section h2{font-size:18px;margin:0 0 15px}.section-lead{font-size:12px;color:var(--muted);margin:-7px 0 14px}.audiences{display:grid;grid-template-columns:repeat(3,1fr);gap:11px}.audience-card{position:relative;padding:16px;border-radius:14px;border:1px solid;min-height:126px}.audience-card:before{content:"";position:absolute;left:0;right:0;top:0;height:4px;border-radius:14px 14px 0 0}.audience-core{background:var(--core-soft);border-color:var(--core-line)}.audience-core:before{background:var(--core)}.audience-consider{background:var(--consider-soft);border-color:var(--consider-line)}.audience-consider:before{background:var(--consider)}.audience-later{background:var(--later-soft);border-color:var(--later-line)}.audience-later:before{background:var(--later)}.audience-kicker{font-size:12px;font-weight:800;margin:1px 0 7px}.audience-core .audience-kicker{color:var(--core)}.audience-consider .audience-kicker{color:var(--consider)}.audience-later .audience-kicker{color:var(--later)}.audience-card strong{display:block;font-size:14px}.audience-card p{font-size:12px;color:var(--muted);margin:7px 0 0}.best-fit{margin:12px 0 0;padding:10px 13px;border-radius:10px;background:#f8fafc;border:1px solid #edf0f4;font-size:13px;font-weight:650}.decision-strip{display:grid;grid-template-columns:1fr 1fr 1.15fr;margin-top:12px;border:1px solid var(--line);border-radius:13px;overflow:hidden;background:#fff}.decision-item{padding:11px 13px;min-height:72px}.decision-item+.decision-item{border-left:1px solid var(--line)}.decision-item b{display:block;font-size:12px;margin-bottom:4px}.decision-use b{color:var(--success)}.decision-no b{color:var(--warning)}.decision-risk{background:var(--danger-soft)}.decision-risk b{color:var(--danger)}.decision-item span{font-size:13px}.problem-card{display:flex;gap:13px;padding:12px 0;border-bottom:1px solid var(--line)}.problem-card:last-child{border-bottom:0}.problem-index{display:flex;align-items:center;justify-content:center;min-width:34px;height:28px;border-radius:8px;background:var(--danger-soft);color:var(--danger);font-weight:800;font-size:12px}.problem-card strong{font-size:14px}.problem-card p{margin:3px 0 0;color:var(--muted);font-size:13px}.priority{list-style:none;padding:0;margin:0}.priority li{display:flex;align-items:flex-start;gap:11px;padding:11px 0;border-bottom:1px solid var(--line)}.priority li:last-child{border-bottom:0}.priority-tag{min-width:36px;color:#fff;border-radius:7px;text-align:center;height:25px;line-height:25px;font-size:11px}.priority-p0{background:var(--danger)}.priority-p1{background:var(--brand)}.priority span{font-size:13px;padding-top:2px}.full-cta{display:flex;align-items:center;justify-content:space-between;gap:16px;background:linear-gradient(90deg,#f1f4ff,#f8faff);border:1px solid #dce3ff;border-radius:14px;padding:14px 16px;margin-top:14px}.full-cta strong{display:block;font-size:13px}.full-cta span{font-size:12px;color:var(--muted)}.full-cta b{white-space:nowrap;background:var(--brand);color:#fff;border-radius:9px;padding:8px 12px;font-size:12px}.muted{color:var(--muted);font-size:13px}.footer{text-align:center;color:#8790a4;font-size:11px;padding:14px}
@media(max-width:760px){.wrap{padding:12px}.hero{padding:22px}.hero-top{display:block}.badges{justify-content:flex-start;margin-top:10px}.audiences{grid-template-columns:1fr}.decision-strip{grid-template-columns:1fr}.decision-item+.decision-item{border-left:0;border-top:1px solid var(--line)}.section{padding:17px}.full-cta{align-items:flex-start}.full-cta b{display:none}}
</style></head><body><main class="wrap">
<section class="hero"><div class="hero-top"><div><h1>${escapeHtml(view.productName)}｜用户验证精简版</h1></div><div class="badges"><span class="badge">用户需求：${escapeHtml(view.demand)}</span><span class="badge">证据可信度：${escapeHtml(view.confidence)}</span></div></div><p>${escapeHtml(view.summary)}</p><div class="reading-path"><span>3–5 秒：先看客户与结论</span><span>→</span><span>30 秒：看关键问题与动作</span></div></section>
<section class="section"><h2>1. 一眼看懂</h2><div class="audiences">${targetCards}</div><div class="best-fit">${escapeHtml(view.bestFit)}</div><div class="decision-strip"><div class="decision-item decision-use"><b>为什么会用</b><span>${escapeHtml(view.whyUse)}</span></div><div class="decision-item decision-no"><b>为什么不用</b><span>${escapeHtml(view.whyNot)}</span></div><div class="decision-item decision-risk"><b>最大问题</b><span>${escapeHtml(view.maxProblem)}</span></div></div></section>
<section class="section"><h2>2. 当前最关键的问题</h2><p class="section-lead">只保留会改变下一版产品决策的 Top 问题。</p>${problemCards}</section>
<section class="section"><h2>3. 下一版先改什么</h2><p class="section-lead">优先处理最影响用户切换和复用的事项。</p><ul class="priority">${priorityRows}</ul><div class="full-cta"><div><strong>需要写策划书、比赛材料或核查证据？</strong><span>同次运行会同时生成《用户验证完备版》，包含用户画像、证据、评分依据与完整执行方案。</span></div><b>查看完备版</b></div></section>
<div class="footer">精简版用于快速决策；完备版用于证据核查、执行与材料引用。</div></main></body></html>`;
}

function renderFullMarkdownFromView(view) {
  const lines = [
    `# ${view.productName}｜用户验证完备版`,
    "",
    `> **用户需求：${view.demand}**　**证据可信度：${view.confidence}**`,
    `> ${view.summary}`,
    "",
    "## 1. 核心结论与目标用户",
    `- **核心用户：** ${view.core}`,
    `- **可争取：** ${view.consider}`,
    `- **暂不优先：** ${view.later}`,
    `- **总判断：** ${view.bestFit}`,
    `- **最大问题：** ${view.maxProblem}`,
    "",
    "## 2. 用户画像与使用场景",
  ];
  if (view.detailPersonas.length === 0) lines.push("当前没有足够信息形成详细用户画像。");
  else view.detailPersonas.forEach((persona, index) => {
    lines.push(`### ${index + 1}. ${persona.title}`);
    if (persona.scene) lines.push(`- **典型场景：** ${persona.scene}`);
    if (persona.goal) lines.push(`- **核心目标：** ${persona.goal}`);
    if (persona.alternative) lines.push(`- **当前替代：** ${persona.alternative}`);
    if (persona.motivation) lines.push(`- **为什么会用：** ${persona.motivation}`);
    if (persona.rejection) lines.push(`- **为什么可能不用：** ${persona.rejection}`);
    lines.push(`- **切换判断：** ${persona.switching}`);
    if (persona.pains.length) lines.push(`- **主要痛点：** ${persona.pains.join("；")}`);
  });

  lines.push("", "## 3. 关键证据与判断依据");
  if (view.detailEvidence.length === 0) lines.push("当前没有可引用的真实用户证据，以下判断应视为待验证方向。");
  else view.detailEvidence.forEach((signal) => lines.push(`- **${signal.source}：** ${signal.observation}`));

  lines.push("", "## 4. 当前最关键的用户问题");
  if (view.detailProblems.length === 0) lines.push("当前还没有足够证据定位具体产品问题。");
  else view.detailProblems.forEach((problem, index) => {
    const recommendation = view.priorities[index]?.action ?? "围绕该问题做最小改动后，用同一用户任务复测。";
    lines.push(`### 问题 ${index + 1}：${problem.title}`);
    lines.push(`- **发现依据：** ${problem.evidence || "仍需补证"}`);
    lines.push(`- **为什么重要：** ${problem.meaning}`);
    lines.push(`- **建议：** ${recommendation}`);
  });

  lines.push("", "## 5. 用户价值评分框架", "| 维度 | 当前判断 | 一句话依据 |", "|---|---|---|");
  view.scores.forEach((item) => lines.push(`| ${item.label} | ${item.judgment} | ${item.basis} |`));
  lines.push("", "> 评分用于说明用户侧证据强弱和判断依据，不代表产品成功率。待验证维度不会被当作低分处理。");

  lines.push("", "## 6. 产品改进优先级");
  if (view.priorities.length === 0) lines.push("当前不建议凭空加功能，先验证最关键的用户问题。");
  else view.priorities.forEach((item) => lines.push(`- **${item.priority}**｜${item.action}`));

  lines.push("", "## 7. 完整用户验证执行方案");
  if (view.detailValidations.length === 0) lines.push("暂无可执行的详细验证方案。");
  else view.detailValidations.forEach((plan, index) => {
    lines.push(`### 方案 ${index + 1}：${plan.title}`);
    lines.push(`- **方法：** ${plan.method}`);
    if (plan.target) lines.push(`- **验证对象：** ${plan.target}`);
    lines.push(`- **样本：** ${plan.sample}`);
    lines.push(`- **周期：** ${plan.duration}`);
    lines.push(`- **成本：** ${plan.cost}`);
    if (plan.tasks.length) {
      lines.push("- **执行步骤：**");
      plan.tasks.forEach((task, taskIndex) => lines.push(`  ${taskIndex + 1}. ${task}`));
    }
    lines.push(`- **看什么结果：** ${plan.threshold}`);
  });

  lines.push("", "## 8. 仍缺什么信息与使用边界");
  if (view.detailMissing.length === 0) lines.push("当前没有额外的关键资料缺口。");
  else view.detailMissing.forEach((item) => lines.push(`- ${item}`));
  lines.push("", "---", "**阅读说明：** 完备版用于核查判断、制定执行方案和引用到比赛/策划材料；内部审计 ID、状态机与 Agent 交接仍保留在机器输出，不在本报告中展示。");
  let markdown = lines.join("\n").replace(/\n{3,}/gu, "\n\n").trim();
  for (const pattern of BANNED_VISIBLE_TOKENS) markdown = markdown.replace(pattern, "");
  return markdown.replace(/[ \t]{2,}/gu, " ").replace(/ \n/gu, "\n");
}

function renderFullHtmlFromView(view) {
  const audienceRows = view.groups.map((group) => `<tr><td><span class="pill pill-${group.key}">${escapeHtml(group.title)}</span></td><td>${escapeHtml(group.label)}</td><td>${escapeHtml(group.why || group.rejection || "仍需验证")}</td></tr>`).join("");
  const evidenceRows = view.detailEvidence.length ? view.detailEvidence.map((signal) => `<tr><td>${escapeHtml(signal.source)}</td><td>${escapeHtml(signal.observation)}</td><td>${signal.source.includes("模拟") ? '<span class="confidence pending">参考</span>' : '<span class="confidence solid">可引用</span>'}</td></tr>`).join("") : `<tr><td colspan="3">当前没有可引用的真实用户证据，相关判断应视为待验证方向。</td></tr>`;
  const scoreRows = view.scores.map((item) => `<tr><td>${escapeHtml(item.label)}</td><td><b>${escapeHtml(item.judgment)}</b></td><td>${escapeHtml(item.basis)}</td></tr>`).join("");
  const problemCards = view.detailProblems.length ? view.detailProblems.map((problem, index) => `<article class="problem"><div class="problem-no">0${index + 1}</div><div><h3>${escapeHtml(problem.title)}</h3><p><b>发现依据：</b>${escapeHtml(problem.evidence || "仍需补证")}</p><p><b>为什么重要：</b>${escapeHtml(problem.meaning)}</p><p><b>建议：</b>${escapeHtml(view.priorities[index]?.action ?? "围绕该问题做最小改动后，用同一用户任务复测。")}</p></div></article>`).join("") : `<p class="muted">当前还没有足够证据定位具体产品问题。</p>`;
  const personaCards = view.detailPersonas.length ? view.detailPersonas.map((persona, index) => `<article class="persona"><div class="persona-title"><span>0${index + 1}</span><h3>${escapeHtml(persona.title)}</h3><em>${escapeHtml(persona.switching)}</em></div><div class="grid2">${persona.scene ? `<div><b>典型场景</b><p>${escapeHtml(persona.scene)}</p></div>` : ""}${persona.goal ? `<div><b>核心目标</b><p>${escapeHtml(persona.goal)}</p></div>` : ""}${persona.alternative ? `<div><b>当前替代</b><p>${escapeHtml(persona.alternative)}</p></div>` : ""}${persona.motivation ? `<div><b>为什么会用</b><p>${escapeHtml(persona.motivation)}</p></div>` : ""}${persona.rejection ? `<div><b>为什么可能不用</b><p>${escapeHtml(persona.rejection)}</p></div>` : ""}${persona.pains.length ? `<div><b>主要痛点</b><p>${escapeHtml(persona.pains.join("；"))}</p></div>` : ""}</div></article>`).join("") : `<p class="muted">当前没有足够信息形成详细用户画像。</p>`;
  const priorityRows = view.priorities.length ? view.priorities.map((item) => `<li><b class="priority-tag ${item.priority === "P0" ? "p0" : "p1"}">${escapeHtml(item.priority)}</b><span>${escapeHtml(item.action)}</span></li>`).join("") : `<li><b class="priority-tag p0">P0</b><span>先验证最大用户问题，不凭空增加功能。</span></li>`;
  const validationCards = view.detailValidations.length ? view.detailValidations.map((plan, index) => `<article class="plan"><div class="plan-head"><span>方案 0${index + 1}</span><h3>${escapeHtml(plan.title)}</h3></div><div class="meta"><i>${escapeHtml(plan.method)}</i><i>${escapeHtml(plan.sample)}</i><i>${escapeHtml(plan.duration)}</i><i>${escapeHtml(plan.cost)}</i></div>${plan.target ? `<p><b>验证对象：</b>${escapeHtml(plan.target)}</p>` : ""}${plan.tasks.length ? `<div><b>执行步骤：</b><ol>${plan.tasks.map((task) => `<li>${escapeHtml(task)}</li>`).join("")}</ol></div>` : ""}<p><b>看什么结果：</b>${escapeHtml(plan.threshold)}</p></article>`).join("") : `<p class="muted">暂无可执行的详细验证方案。</p>`;
  const missingRows = view.detailMissing.length ? view.detailMissing.map((item) => `<li>${escapeHtml(item)}</li>`).join("") : `<li>当前没有额外的关键资料缺口。</li>`;

  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHtml(view.productName)}｜用户验证完备版</title><style>
:root{--bg:#f4f6fa;--card:#fff;--ink:#182235;--muted:#667085;--line:#e4e8ef;--brand:#3157d5;--brand-soft:#eef2ff;--core:#0f8b70;--core-soft:#eaf8f3;--consider:#5264d9;--consider-soft:#eef0ff;--later:#bd781b;--later-soft:#fff6e5;--danger:#c2413b;--danger-soft:#fff0ef;--success:#177d59}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;line-height:1.72}.wrap{max-width:1040px;margin:auto;padding:24px}.hero{background:#17264a;color:#fff;border-radius:20px;padding:30px;box-shadow:0 16px 40px #213a8220}.hero h1{margin:0;font-size:28px}.hero p{color:#e6ebff;margin:12px 0 0;max-width:800px}.badges{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.badge{font-size:12px;padding:5px 10px;border-radius:999px;border:1px solid #ffffff35;background:#ffffff12}.hero-note{margin-top:15px;padding-top:12px;border-top:1px solid #ffffff2b;font-size:12px;color:#cfd7f5}.section{background:var(--card);border:1px solid var(--line);border-radius:17px;padding:24px;margin-top:15px;box-shadow:0 2px 10px #17203308}.section h2{font-size:19px;margin:0 0 15px}.section>p.lead{margin:-7px 0 15px;color:var(--muted);font-size:13px}.summary-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.summary-cell{padding:13px;border-radius:12px;background:#f8fafc;border:1px solid #edf0f4}.summary-cell b{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}.summary-cell span{font-size:14px;font-weight:650}.summary-cell.risk{background:var(--danger-soft);border-color:#f3cdca}.summary-cell.risk b{color:var(--danger)}table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:10px 11px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{font-size:12px;color:var(--muted);background:#fafbfc}.pill{display:inline-block;padding:4px 9px;border-radius:999px;font-size:11px;font-weight:800}.pill-core{background:var(--core-soft);color:var(--core)}.pill-consider{background:var(--consider-soft);color:var(--consider)}.pill-later{background:var(--later-soft);color:var(--later)}.confidence{font-size:11px;padding:3px 8px;border-radius:999px}.confidence.solid{background:#eaf8f3;color:var(--success)}.confidence.pending{background:#fff6e5;color:var(--later)}.problem{display:grid;grid-template-columns:42px 1fr;gap:12px;padding:14px 0;border-bottom:1px solid var(--line)}.problem:last-child{border-bottom:0}.problem-no{display:flex;align-items:center;justify-content:center;width:38px;height:30px;background:var(--danger-soft);color:var(--danger);border-radius:8px;font-weight:800;font-size:12px}.problem h3{margin:1px 0 8px;font-size:15px}.problem p{margin:5px 0;font-size:13px}.persona{border:1px solid var(--line);border-radius:14px;padding:16px;margin-top:10px}.persona-title{display:flex;align-items:center;gap:10px}.persona-title>span{color:var(--brand);font-size:12px;font-weight:800}.persona-title h3{font-size:15px;margin:0;flex:1}.persona-title em{font-style:normal;font-size:11px;color:var(--brand);background:var(--brand-soft);padding:3px 8px;border-radius:999px}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px 18px;margin-top:12px}.grid2 div{padding-top:7px;border-top:1px solid #f0f2f5}.grid2 b{font-size:11px;color:var(--muted)}.grid2 p{font-size:13px;margin:3px 0}.priority{list-style:none;padding:0;margin:0}.priority li{display:flex;gap:12px;padding:11px 0;border-bottom:1px solid var(--line)}.priority li:last-child{border-bottom:0}.priority-tag{min-width:38px;height:25px;line-height:25px;text-align:center;color:#fff;border-radius:7px;font-size:11px}.p0{background:var(--danger)}.p1{background:var(--brand)}.plan{border:1px solid var(--line);border-radius:14px;padding:16px;margin-top:10px}.plan-head{display:flex;gap:10px;align-items:flex-start}.plan-head span{color:var(--brand);font-size:11px;font-weight:800}.plan-head h3{font-size:14px;margin:0}.meta{display:flex;gap:7px;flex-wrap:wrap;margin:10px 0}.meta i{font-style:normal;background:#f4f6fa;color:#53607a;border-radius:999px;padding:3px 8px;font-size:11px}.plan p,.plan li{font-size:13px}.plan ol{margin:6px 0;padding-left:21px}.callout{padding:13px 15px;border-radius:12px;background:var(--brand-soft);border:1px solid #d9e0ff;font-size:13px}.missing{margin:0;padding-left:20px;font-size:13px}.missing li{margin:7px 0}.muted{color:var(--muted);font-size:13px}.footer{text-align:center;color:#8790a4;font-size:11px;padding:16px}
@media(max-width:760px){.wrap{padding:12px}.hero{padding:22px}.summary-grid,.grid2{grid-template-columns:1fr}.section{padding:17px}.persona-title{align-items:flex-start;flex-wrap:wrap}}
</style></head><body><main class="wrap"><section class="hero"><h1>${escapeHtml(view.productName)}｜用户验证完备版</h1><p>${escapeHtml(view.summary)}</p><div class="badges"><span class="badge">用户需求：${escapeHtml(view.demand)}</span><span class="badge">证据可信度：${escapeHtml(view.confidence)}</span></div><div class="hero-note">本报告用于核查判断、制定用户验证与产品改进方案，以及引用到比赛/策划材料。所有结论仍以同次运行的结构化结果为基础。</div></section>
<section class="section"><h2>1. 核心结论与目标用户</h2><div class="summary-grid"><div class="summary-cell"><b>核心用户</b><span>${escapeHtml(view.core)}</span></div><div class="summary-cell"><b>可争取用户</b><span>${escapeHtml(view.consider)}</span></div><div class="summary-cell"><b>暂不优先</b><span>${escapeHtml(view.later)}</span></div><div class="summary-cell risk"><b>当前最大问题</b><span>${escapeHtml(view.maxProblem)}</span></div></div><div class="callout" style="margin-top:12px"><b>总判断：</b>${escapeHtml(view.bestFit)}</div></section>
<section class="section"><h2>2. 目标用户分群与判断依据</h2><p class="lead">身份标签只作为背景，核心仍看触发场景、替代方案和切换动力。</p><table><thead><tr><th>层级</th><th>用户描述</th><th>为什么这样判断</th></tr></thead><tbody>${audienceRows}</tbody></table>${personaCards}</section>
<section class="section"><h2>3. 关键证据</h2><p class="lead">优先展示真实行为与真实访谈；没有真实证据时会明确标记为参考。</p><table><thead><tr><th>来源</th><th>观察</th><th>用途</th></tr></thead><tbody>${evidenceRows}</tbody></table></section>
<section class="section"><h2>4. 当前最关键的用户问题</h2>${problemCards}</section>
<section class="section"><h2>5. 用户价值评分与依据</h2><table><thead><tr><th>维度</th><th>判断</th><th>一句话依据</th></tr></thead><tbody>${scoreRows}</tbody></table><div class="callout" style="margin-top:12px">评分用于说明用户侧证据强弱，不是“用户喜欢程度”或产品成功概率；待验证不等于低分。</div></section>
<section class="section"><h2>6. 产品改进优先级</h2><ul class="priority">${priorityRows}</ul></section>
<section class="section"><h2>7. 完整用户验证执行方案</h2><p class="lead">可以直接作为下一轮用户研究或比赛方案材料的执行骨架。</p>${validationCards}</section>
<section class="section"><h2>8. 仍缺什么信息与使用边界</h2><ul class="missing">${missingRows}</ul><div class="callout" style="margin-top:12px">完备版仍不会暴露内部审计 ID、状态机或 Agent 交接字段；这些只留在机器输出，避免把工程日志当成用户报告。</div></section><div class="footer">完备版与精简版来自同一份结构化结果；结论必须保持一致。</div></main></body></html>`;
}

export function renderSummaryReport({ input, structured, ingestedEvidence = [] }) {
  if (!structured || structured.target_user_definition?.admitted !== true) return null;
  const view = buildViewModel({ input, structured, ingestedEvidence });
  return renderMarkdownFromView(view, ingestedEvidence);
}

export function renderSummaryReportHtml({ input, structured, ingestedEvidence = [] }) {
  if (!structured || structured.target_user_definition?.admitted !== true) return null;
  const view = buildViewModel({ input, structured, ingestedEvidence });
  return renderSummaryHtmlFromView(view);
}

export function renderFullReport({ input, structured, ingestedEvidence = [] }) {
  if (!structured || structured.target_user_definition?.admitted !== true) return null;
  const view = buildViewModel({ input, structured, ingestedEvidence });
  return renderFullMarkdownFromView(view);
}

export function renderFullReportHtml({ input, structured, ingestedEvidence = [] }) {
  if (!structured || structured.target_user_definition?.admitted !== true) return null;
  const view = buildViewModel({ input, structured, ingestedEvidence });
  return renderFullHtmlFromView(view);
}

// Backward-compatible aliases: the historical human_report is now explicitly
// the summary report. Consumers that need evidence and execution detail should
// read full_report / full_report_html.
export function renderHumanReport(args) {
  return renderSummaryReport(args);
}

export function renderHumanReportHtml(args) {
  return renderSummaryReportHtml(args);
}

export function visibleReportHasInternalTokens(report) {
  if (!report) return false;
  return BANNED_VISIBLE_TOKENS.some((pattern) => {
    pattern.lastIndex = 0;
    return pattern.test(report);
  });
}

export const HUMAN_REPORT_LIMITS = Object.freeze({
  max_target_groups: MAX_TARGET_GROUPS,
  max_problems: MAX_PROBLEMS,
  max_development_priorities: MAX_PRIORITIES,
  max_validation_actions: MAX_VALIDATION_ACTIONS,
  max_evidence_signals: MAX_EVIDENCE_SIGNALS,
  max_detail_evidence_signals: MAX_DETAIL_EVIDENCE_SIGNALS,
  max_detail_validation_actions: MAX_DETAIL_VALIDATION_ACTIONS,
  max_detail_personas: MAX_DETAIL_PERSONAS,
  target_visible_chars: TARGET_VISIBLE_CHARS,
  max_visible_chars: MAX_VISIBLE_CHARS,
  max_main_sections: 5,
});

function canonicalReportValue(value) {
  if (Array.isArray(value)) return value.map(canonicalReportValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonicalReportValue(value[key])]));
  }
  return value;
}

function stableReportId(value) {
  const normalized = String(value).trim().replace(/[^A-Za-z0-9._-]+/gu, "-").replace(/^-+|-+$/gu, "");
  if (!normalized) throw new Error("finding keys must contain a stable identifier");
  return normalized;
}

function normalizeDecisionRelevance(value) {
  return ["CRITICAL", "IMPORTANT", "CONTEXT"].includes(value) ? value : "IMPORTANT";
}

export function buildUserSpecialistReportV2({ identity, findings, domainPayload = {}, actions = [] }) {
  if (!identity || !Array.isArray(findings) || findings.length === 0) throw new Error("identity and findings are required");
  const normalized = findings.map((finding) => {
    const key = stableReportId(finding.key);
    const sources = normalizeReportSources(finding.sources);
    const admitted = sources.some((source) => source.supportRole === "SUPPORT");
    const claimId = `claim-user-${key}`;
    return {
      claim: {
        claim_id: claimId,
        section: finding.section,
        text: finding.text,
        status: admitted ? "VERIFIED" : "PENDING_VALIDATION",
        decision_relevance: normalizeDecisionRelevance(finding.decision_relevance),
        citation_ids: sources.map((_, index) => `citation-user-${key}-${index + 1}`),
        score_bearing: admitted,
      },
      citations: sources.map((source, index) => ({
        citation_id: `citation-user-${key}-${index + 1}`,
        claim_id: claimId,
        evidence_id: source.directory.evidence_id,
        source_locator_id: source.directory.source_locator_id,
        support_role: source.supportRole,
        audit_status: "VERIFIED",
        label: index + 1,
      })),
      sources: sources.map((source) => source.directory),
    };
  });
  const claims = normalized.map((item) => item.claim);
  const citations = normalized.flatMap((item) => item.citations).map((item, index) => ({ ...item, label: index + 1 }));
  const sourceDirectory = [...new Map(normalized.flatMap((item) => item.sources).map((item) => [item.source_locator_id, item])).values()];
  const pending = claims.filter((item) => !item.score_bearing);
  const sourceSha256 = createHash("sha256")
    .update(JSON.stringify(canonicalReportValue({ identity, findings, domainPayload, actions })))
    .digest("hex");
  const normalizedActions = (actions.length ? actions : [{}]).map((value, index) => ({
    action_id: `action-user-${stableReportId(value.key ?? String(index + 1))}`,
    title: value.title ?? "补齐真实用户行为证据",
    owner: value.owner ?? "用户研究负责人",
    deadline_days: value.deadline_days ?? 14,
    success_criteria: value.success_criteria ?? ["目标用户在同一任务中出现可观察的使用或付费行为"],
    failure_triggers: value.failure_triggers ?? ["真实用户行为不支持核心需求判断"],
    required_evidence: value.required_evidence ?? ["访谈、可用性测试、留存、使用或支付记录"],
    related_claim_ids: value.related_claim_ids?.length ? value.related_claim_ids : pending.length ? pending.map((item) => item.claim_id) : [claims[0].claim_id],
  }));
  return {
    schema_version: "2.0",
    ...identity,
    agent_code: "user-evidence",
    source_sha256: sourceSha256,
    executive_summary: claims.slice(0, 3).map((item) => item.claim_id),
    metrics: [{ key: "pending_validation", label: "待验证判断", value: pending.length, claim_ids: pending.map((item) => item.claim_id) }],
    claims,
    domain_payload: domainPayload,
    risks: pending.map((item) => item.claim_id),
    actions: normalizedActions,
    citations,
    source_directory: sourceDirectory,
    audit_summary: { verified: claims.length - pending.length, insufficient: 0, needs_more: pending.length, conflicted: 0 },
    raw_audit_refs: [],
  };
}

export function selectUserSpecialistReportV2(report, view = "summary") {
  if (!["summary", "full"].includes(view)) throw new Error("view must be summary or full");
  return {
    schema_version: report.schema_version,
    report_id: report.report_id,
    source_sha256: report.source_sha256,
    view,
    claim_ids: view === "summary" ? report.executive_summary : report.claims.map((item) => item.claim_id),
  };
}
