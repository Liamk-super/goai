export type HitPredictorStageCode = "IDEA" | "PROTOTYPE" | "DEMO_MVP" | "USERS" | "LIVE";
export type EvaluationRoute = "INCUBATION" | "LIGHTWEIGHT_REVIEW" | "FORMAL_EVALUATION";

export const HIT_PREDICTOR_STAGES: Array<{
  code: HitPredictorStageCode;
  label: string;
  detail: string;
}> = [
  { code: "IDEA", label: "只有想法", detail: "生成初步预测，明确最重要的假设和证据缺口" },
  { code: "PROTOTYPE", label: "已有原型", detail: "生成初步预测，优先验证核心流程与目标用户" },
  { code: "DEMO_MVP", label: "已有 Demo / MVP", detail: "可以进入完整的 1+4 预测" },
  { code: "USERS", label: "已有真实用户", detail: "结合行为证据完成完整预测" },
  { code: "LIVE", label: "已上线运营", detail: "结合运营数据完成完整预测" },
];

export function deriveProjectName(description: string): string {
  const normalized = description.trim().replace(/\s+/gu, " ");
  const firstSentence = normalized.split(/[。！？!?\n]/u)[0]?.trim() ?? "";
  return (firstSentence || "未命名产品").slice(0, 28);
}

export function normalizeProductUrl(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return "";
  const candidate = /^[a-z][a-z\d+.-]*:/iu.test(trimmed) ? trimmed : `https://${trimmed}`;
  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    throw new Error("Enter a valid product URL.");
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("Product URL must use HTTP or HTTPS.");
  }
  if (!parsed.hostname) throw new Error("Enter a valid product URL.");
  return parsed.toString();
}

export function evaluationRouteForStage(stage: HitPredictorStageCode): EvaluationRoute {
  if (stage === "IDEA") return "INCUBATION";
  if (stage === "PROTOTYPE") return "LIGHTWEIGHT_REVIEW";
  return "FORMAL_EVALUATION";
}

export function stageCodeFromProfile(value: string): HitPredictorStageCode | null {
  const normalized = value.trim().toLowerCase();
  if (/只有想法|想法阶段|idea|concept/u.test(normalized)) return "IDEA";
  if (/原型|prototype|wireframe/u.test(normalized)) return "PROTOTYPE";
  if (/(?:可以|可)实际使用的(?:\s*真实)?\s*web\s*端|web\s*端.{0,16}(?:可用|已跑通)|核心.{0,12}链路.{0,12}已跑通|功能可用/u.test(normalized)) {
    return "DEMO_MVP";
  }
  if (/demo|mvp/u.test(normalized)) return "DEMO_MVP";
  if (/真实用户|real user/u.test(normalized)) return "USERS";
  if (/已上线|上线运营|launched|production/u.test(normalized)) return "LIVE";
  return null;
}

export function nextPortraitQuestion<T extends { field: string }>(
  questions: T[],
  answers: Record<string, string>,
): T | null {
  return questions.find(question => !answers[question.field]?.trim()) ?? null;
}

export type HitPredictorIntakeSeed = {
  description: string;
  stage: HitPredictorStageCode;
  referenceUrl: string;
};

export const HIT_PREDICTOR_INTAKE_SEED_KEY = "launchscope:hit-predictor:intake-seed:v1";
