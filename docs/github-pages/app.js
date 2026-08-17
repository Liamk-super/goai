const steps = [
  {
    index: "01 / 05",
    time: "T + 00",
    title: "问题进入：定义此次要验证什么",
    summary: "用户的目标、已有材料和决策约束被整理成固定样例输入。它只用于说明数据应如何进入控制面。",
    output: "明确范围、限制条件与证据缺口",
    mode: "DISPLAY_ONLY",
    rule: "不把未经核验的主张直接变成评分或推荐。",
  },
  {
    index: "02 / 05",
    time: "T + 04",
    title: "主管拆解：先制定约束，再分配研究边界",
    summary: "主管角色将问题拆成用户、产品、商业与证据四类视角，并保留每条主张需要的引用、门槛与反证条件。",
    output: "固定任务图与准入条件",
    mode: "PLAN_RENDERED",
    rule: "主管只负责规划和整合，不能自行研究、改分或提交业务状态。",
  },
  {
    index: "03 / 05",
    time: "T + 12",
    title: "专项并行：让四种判断彼此挑战",
    summary: "四份固定摘要分别讨论用户行为、产品交付、商业化与证据质量。页面展示分工，不代表有代理在此时运行。",
    output: "四份专项样例报告",
    mode: "PARALLEL_SAMPLE",
    rule: "功能、调研意愿和早期样本不能自动推导为商业可行性。",
  },
  {
    index: "04 / 05",
    time: "T + 18",
    title: "证据校准：保留冲突，也保留未知",
    summary: "审计角色标记证据强度、引用位置与待补缺口。可信度不足的条目可进入待办，但不会推动主评分或建议。",
    output: "证据目录、冲突与准入结果",
    mode: "AUDIT_SAMPLE",
    rule: "未知的外部副作用或账单状态必须失败关闭，不能自动重试。",
  },
  {
    index: "05 / 05",
    time: "T + 24",
    title: "行动建议：把判断转为下一次验证",
    summary: "综合报告不宣告“爆款”，而是将当前证据落在可验证的行动、成功门槛、失败信号与复查条件上。",
    output: "继续验证与三项行动计划",
    mode: "REPORT_RENDERED",
    rule: "63 分是可解释的展示性指数，不是 63% 成功概率。",
  },
];

const stepButtons = [...document.querySelectorAll(".flow-step")];
const stepIndex = document.querySelector("#step-index");
const stepTime = document.querySelector("#step-time");
const stepTitle = document.querySelector("#step-title");
const stepSummary = document.querySelector("#step-summary");
const stepOutput = document.querySelector("#step-output");
const stepMode = document.querySelector("#step-mode");
const stepRule = document.querySelector("#step-rule");

function showStep(index) {
  const step = steps[index];
  stepButtons.forEach((button, buttonIndex) => {
    const selected = buttonIndex === index;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-current", selected ? "step" : "false");
  });
  stepIndex.textContent = step.index;
  stepTime.textContent = step.time;
  stepTitle.textContent = step.title;
  stepSummary.textContent = step.summary;
  stepOutput.textContent = step.output;
  stepMode.textContent = step.mode;
  stepRule.textContent = step.rule;
}

stepButtons.forEach(button => {
  button.addEventListener("click", () => showStep(Number(button.dataset.step)));
});

document.querySelectorAll("[data-jump]").forEach(button => {
  button.addEventListener("click", () => document.querySelector(`#${button.dataset.jump}`)?.scrollIntoView({ behavior: "smooth" }));
});

const reportToggle = document.querySelector("#report-toggle");
const specialistSummary = document.querySelector("#specialist-summary");

reportToggle.addEventListener("click", () => {
  const expanded = reportToggle.getAttribute("aria-expanded") === "true";
  reportToggle.setAttribute("aria-expanded", String(!expanded));
  specialistSummary.hidden = expanded;
  reportToggle.innerHTML = `${expanded ? "展开" : "收起"}四份专项摘要 <span aria-hidden="true">${expanded ? "+" : "−"}</span>`;
});
