/** Deterministic admission and role-scope gates. */

const UNIVERSAL = [
  { pattern: /所有人|任何人|每个人|人人都|全体|大众|全民/u, label: "universal_claim_zh" },
  { pattern: /\b(everyone|anyone|everybody|all users|all people|general public|the masses)\b/iu, label: "universal_claim_en" },
];

const BARE_DEMOGRAPHIC = /^(学生|大学生|年轻人|白领|上班族|家长|老师|企业|开发者|用户|students?|young people|employees?)$/iu;
const BEHAVIOURAL_QUALIFIER = /每天|每周|每月|小时|分钟|预算|付费|购买|正在|经常|频繁|截止|通勤|复习|工作流|场景|替代|绕行|痛点|\d|daily|weekly|budget|pay|buy|using|workflow|deadline|commut/iu;

export const CLARIFICATION_QUESTIONS = Object.freeze([
  "谁最痛？请给出具体人群、触发场景，以及当前为问题付出的时间、金钱或绕行成本。",
  "谁付钱？使用者与付费者是否相同；若不同，谁做购买决策？",
  "谁最先用？什么具体行为或场景会触发第一批真实使用？",
]);

function classifyDescription(text, path) {
  const value = typeof text === "string" ? text.normalize("NFC").trim() : "";
  const negatedUniversal = /不是所有人|并非所有人|不是任何人|not\s+(everyone|anyone|everybody|all\s+users)/iu.test(value);
  const universal = negatedUniversal ? [] : UNIVERSAL.filter(({ pattern }) => pattern.test(value)).map(({ label }) => `${path}:${label}`);
  if (universal.length > 0) return { verdict: "too_broad", matched: universal };
  if (BARE_DEMOGRAPHIC.test(value)) return { verdict: "borderline", matched: [`${path}:demographic_only`] };
  if (!BEHAVIOURAL_QUALIFIER.test(value)) return { verdict: "borderline", matched: [`${path}:missing_behavioral_qualifier`] };
  return { verdict: "executable", matched: [] };
}

export function checkTargetUserBreadth(targetUsers) {
  const raw = classifyDescription(targetUsers?.raw_description, "raw_description");
  const segments = Array.isArray(targetUsers?.segments)
    ? targetUsers.segments.filter((segment) => typeof segment === "string" && segment.trim())
    : [];
  const segmentChecks = segments.map((segment, index) => classifyDescription(segment, `segments[${index}]`));
  const checks = [raw, ...segmentChecks];
  const matched = checks.flatMap((check) => check.matched);
  const hasTooBroad = checks.some((check) => check.verdict === "too_broad");
  const allExecutable = segments.length > 0 && segmentChecks.every((check) => check.verdict === "executable");
  const verdict = hasTooBroad ? "too_broad" : raw.verdict === "executable" && (segments.length === 0 || allExecutable) ? "executable" : "borderline";
  return {
    verdict,
    matched_broad_patterns: matched,
    reason: verdict === "too_broad"
      ? "The raw target description or at least one segment is universal or lacks enough constraints to be recruitable. Segments cannot bypass admission."
      : verdict === "borderline"
        ? "The audience is demographic-only or missing behavioural, situational, pain, payer, or adoption constraints; modelling may proceed only with reduced confidence."
        : "The target definition contains concrete behavioural or situational qualifiers.",
    clarification_questions: verdict === "executable" ? [] : [...CLARIFICATION_QUESTIONS],
  };
}

const EXTERNAL_ACTION_PATTERNS = Object.freeze([
  { pattern: /(帮我|直接|自动|请你)?(发送|发出|群发|投放)(问卷|邮件|短信|消息|链接)/u, label: "send_survey_or_message" },
  { pattern: /(联系|访谈|约见|加微信|打电话给)(真实|实际|这些|此类)?(用户|受访者|客户)/u, label: "contact_user" },
  { pattern: /(发布|上线|部署)(落地页|着陆页|landing|页面|问卷)/iu, label: "publish_page" },
  { pattern: /(采集|收集|抓取)(个人|用户)(信息|数据|隐私|手机号|邮箱)/u, label: "collect_pii" },
  { pattern: /(收费|扣款|收取定金|charge|bill)(真实|用户|客户)?/iu, label: "billable" },
  { pattern: /(send|blast)\s+(the\s+)?(survey|questionnaire|email)/i, label: "send_survey_or_message" },
  { pattern: /(recruit|contact|interview)\s+(real\s+)?(users|participants|customers)\s+(now|directly|yourself)/i, label: "contact_user" },
]);

export function scanForExternalActionRequests(input) {
  const findings = [];
  const texts = [
    ["validation_goal.objective", input?.validation_goal?.objective],
    ...(input?.validation_goal?.focus_questions ?? []).map((text, i) => [`validation_goal.focus_questions[${i}]`, text]),
    ["constraints.compliance_notes", input?.constraints?.compliance_notes],
  ];
  for (const [path, text] of texts) {
    if (typeof text !== "string") continue;
    const designOnly = /(设计|规划|方案|草拟|脚本|如何访谈|dry[- ]?run|design|plan|draft)/iu.test(text);
    const explicitNoExecution = /(不要|无需|不需|禁止|仅设计|不实际)(实际)?(联系|发送|发布|执行|访谈|招募)|do\s+not\s+(contact|send|publish|execute|recruit|interview)/iu.test(text);
    const explicitExecutionNow = /(现在|立即|马上|直接)(联系|发送|发布|执行|访谈|招募)|\b(now|immediately|right now)\b/iu.test(text);
    for (const { pattern, label } of EXTERNAL_ACTION_PATTERNS) if (pattern.test(text)) findings.push({ path, label });
    if ((designOnly || explicitNoExecution) && !explicitExecutionNow) {
      for (let index = findings.length - 1; index >= 0; index -= 1) {
        if (findings[index].path === path && ["contact_user", "send_survey_or_message", "publish_page"].includes(findings[index].label)) findings.splice(index, 1);
      }
    }
  }
  return { clean: findings.length === 0, findings };
}

const SCOPE_PATTERNS = Object.freeze([
  { pattern: /\b(TAM|SAM|SOM|market size)\b|市场规模|投资价值|融资|商业模式|单位经济|是否值得投资/iu, redirect_to: "investment_business_agent" },
  { pattern: /政策|法规|地区市场|区域环境|policy|regulation|regional market/iu, redirect_to: "review_supervisor_agent" },
  { pattern: /代码|架构|技术实现|性能优化|code|architecture|implementation/iu, redirect_to: "product_team_expert_agent" },
]);
const USER_SCOPE = /用户|需求|使用|拒绝|任务|体验|痛点|付费意愿|访谈|留存|adopt|user|need|usability|retention/iu;

export function checkObjectiveScope(validationGoal) {
  const parts = [validationGoal?.objective, ...(validationGoal?.focus_questions ?? [])].filter((v) => typeof v === "string");
  const text = parts.join("\n");
  const redirects = [];
  for (const rule of SCOPE_PATTERNS) {
    if (rule.pattern.test(text)) redirects.push({ question: text, redirect_to: rule.redirect_to });
  }
  const unique = redirects.filter((entry, index, all) => all.findIndex((x) => x.redirect_to === entry.redirect_to) === index);
  return { redirects: unique, has_user_scope: USER_SCOPE.test(text), fully_out_of_scope: unique.length > 0 && !USER_SCOPE.test(text) };
}
