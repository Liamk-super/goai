import type { Locale } from "./i18n.ts";
import type { Report } from "./api-client.ts";

export type StudentReport = {
  verdict: string;
  summary: string;
  scoreLabel: string | null;
  reasons: string[];
  opportunity: string;
  risk: string;
  actions: string[];
  gaps: string[];
};

const ZH_VERDICTS: Record<string, string> = {
  PROCEED: "可以进入下一阶段",
  VALIDATE_FURTHER: "需要继续验证",
  ADJUST: "建议调整方向",
  PAUSE: "建议暂缓投入",
};

const EN_VERDICTS: Record<string, string> = {
  PROCEED: "Proceed to the next stage",
  VALIDATE_FURTHER: "Validate further",
  ADJUST: "Adjust the direction",
  PAUSE: "Pause further investment",
};

function mostlyEnglish(value: string): boolean {
  const latin = (value.match(/[A-Za-z]/g) ?? []).length;
  const chinese = (value.match(/[\u3400-\u9fff]/g) ?? []).length;
  return latin > 18 && latin > chinese * 2;
}

function cleanChinese(value: string): string {
  return value
    .replaceAll("VALIDATE_FURTHER", "继续验证")
    .replaceAll("REQUEST_MORE_EVIDENCE", "需要补充证据")
    .replaceAll("ACCEPTED", "已通过证据核对")
    .replaceAll("DOWNGRADED", "结论已降级")
    .replaceAll("REJECTED", "该结论未采用")
    .replace(/audited findings/gi, "经过证据核对的结论")
    .replace(/remediation/gi, "针对性补充验证")
    .replace(/unit economics/gi, "单位经济")
    .replace(/PMF Signal/gi, "产品市场匹配信号")
    .replace(/\bPMF\b/g, "产品市场匹配（PMF）")
    .replace(/\bCER\b/g, "证据可信度")
    .replace(/\bRICE\b/g, "优先级评估")
    .replace(/claim_id|evidence_refs/gi, "证据引用")
    .trim();
}

function zhAction(value: string, index: number): string {
  if (!value || mostlyEnglish(value)) {
    const lower = value.toLowerCase();
    if (/pay|revenue|price|cost|econom/.test(lower)) {
      return "用真实报价或付费测试确认用户愿意为什么价值付多少钱。";
    }
    if (/user|usage|retention|seller|interview|test/.test(lower)) {
      return "邀请 5—10 名目标用户完成核心任务，记录完成率、卡点和再次使用意愿。";
    }
    if (/product|technical|reliab|api|mvp/.test(lower)) {
      return "先修复最影响核心体验的一个问题，再用同一批任务复测。";
    }
    return [
      "围绕当前最大证据缺口，完成一次可复核的真实用户验证。",
      "收集连续使用或真实付费数据，确认需求不是一次性兴趣。",
      "每次只调整一个关键假设，再创建新版本进行对比预测。",
    ][Math.min(index, 2)];
  }
  return cleanChinese(value);
}

function zhGap(value: string): string {
  const lower = value.toLowerCase();
  if (/product_capability|product capability/.test(lower)) return "还缺少核心功能可用性和稳定交付方面的证据。";
  if (/user_value|user value/.test(lower)) return "还缺少真实用户使用效果和持续需求方面的证据。";
  if (/investment_potential|investment potential/.test(lower)) return "还缺少价格、成本、增长和付费意愿方面的证据。";
  if (/evidence_quality|evidence quality/.test(lower)) return "现有材料还不足以支撑更确定的结论。";
  if (/user-evidence|usage|retention|seller|user/.test(lower) || mostlyEnglish(value)) {
    return "还缺少可复核的真实用户使用、留存或付费数据。";
  }
  if (/business-investment|revenue|price|cost/.test(lower)) {
    return "还缺少价格、成本和付费意愿方面的真实数据。";
  }
  if (/product-engineering|technical|reliab/.test(lower)) {
    return "还缺少核心功能稳定性和实际完成效果的验证。";
  }
  return cleanChinese(value.replaceAll("_", " "));
}

function unique(values: string[], limit = 3): string[] {
  return [...new Set(values.map(value => value.trim()).filter(Boolean))].slice(0, limit);
}

export function formatStudentReport(report: Report, locale: Locale): StudentReport {
  const layer = report.layered_report;
  const score = report.deterministic_score;
  if (locale === "en") {
    return {
      verdict: EN_VERDICTS[report.recommendation] ?? report.recommendation.replaceAll("_", " "),
      summary: layer?.summary || "The result is based on the available product, user, business, and evidence checks.",
      scoreLabel: score ? `Overall review reference score ${score.score.toFixed(0)} / 100` : null,
      reasons: unique(layer?.cross_domain_analysis ?? report.blocking_reasons ?? []),
      opportunity: layer?.largest_opportunity ?? "No primary opportunity has been established yet.",
      risk: layer?.largest_risk ?? "The main risk is insufficient real-world validation.",
      actions: unique(layer?.actions ?? report.action_items ?? []),
      gaps: unique(layer?.information_gaps ?? report.information_gaps ?? []),
    };
  }

  const accepted = report.calibration_results?.filter(item => item.decision === "ACCEPTED").length ?? 0;
  const weakened = report.calibration_results?.filter(item => item.decision !== "ACCEPTED").length ?? 0;
  const evidenceCount = report.evidence_chain.length;
  const coverage = score ? Math.round(score.coverage * 100) : null;
  const rawSummary = layer?.summary ?? "";
  const summary = rawSummary && !mostlyEnglish(rawSummary)
    ? cleanChinese(rawSummary)
    : report.recommendation === "PROCEED"
      ? "现有证据已经支持产品进入下一阶段，但仍应保留小步验证，避免一次性扩大投入。"
      : report.recommendation === "ADJUST"
        ? "当前方向里有值得保留的部分，但关键用户需求或商业证据不够稳定，建议先调整再继续投入。"
        : report.recommendation === "PAUSE"
          ? "现有材料还不足以支持继续投入，先停下来补齐关键证据会更稳妥。"
          : `目前有 ${Math.max(accepted, evidenceCount)} 条可核对的证据说明这个方向值得继续做，但真实用户长期使用和付费数据仍然不足，现在更适合继续验证，而不是直接扩大投入。`;
  const rawReasons = layer?.cross_domain_analysis ?? [];
  const reasons = unique([
    accepted > 0 ? `${accepted} 条结论通过了证据核对，可以作为本轮判断依据。` : `${evidenceCount} 条材料或证据进入了本轮判断。`,
    weakened > 0 ? `${weakened} 条结论因为证据不足被降级或未采用。` : "没有发现需要隐藏的证据降级记录。",
    coverage !== null ? `本轮可用证据覆盖度约为 ${coverage}%，未覆盖部分不会被当成已验证。` : "缺少的信息已单独列出，没有用猜测补齐。",
    ...rawReasons.filter(value => !mostlyEnglish(value)).map(cleanChinese),
  ]);
  const rawOpportunity = layer?.largest_opportunity ?? "";
  const rawRisk = layer?.largest_risk ?? "";
  const rawActions = layer?.actions ?? report.action_items ?? [];
  const rawGaps = layer?.information_gaps ?? report.information_gaps ?? [];

  return {
    verdict: ZH_VERDICTS[report.recommendation] ?? "需要继续判断",
    summary,
    scoreLabel: score ? `综合评审参考分 ${score.score.toFixed(0)} / 100` : null,
    reasons,
    opportunity: rawOpportunity && !mostlyEnglish(rawOpportunity)
      ? cleanChinese(rawOpportunity)
      : "产品方向已经具备可继续验证的基础，下一步最有价值的是把真实用户行为变成可复核证据。",
    risk: rawRisk && !mostlyEnglish(rawRisk)
      ? cleanChinese(rawRisk)
      : "最大风险是把少量反馈或短期兴趣误当成稳定需求，过早扩大投入。",
    actions: unique((rawActions.length ? rawActions : ["", "", ""]).map(zhAction)),
    gaps: unique(rawGaps.map(zhGap)),
  };
}

export type UserErrorPresentation = {
  summary: string;
  technicalDetail?: string;
};

function isTechnicalError(value: string): boolean {
  return /(?:Error|Exception|Timeout|Traceback|HTTP\s*\d{3}|SUBMISSION_UNKNOWN|USAGE_UNKNOWN|BILLING_UNKNOWN|PAID_TIMEOUT|NEEDS_ATTENTION|MCP_CONTEXT_UNREACHABLE|RUNTIME_UNAVAILABLE|incomplete chunked read|disconnected before settlement)/i.test(value);
}

export function formatUserVisibleError(value: string, locale: Locale): UserErrorPresentation {
  const detail = value.trim();
  const technicalDetail = isTechnicalError(detail) ? detail : undefined;
  if (/SUBMISSION_UNKNOWN|USAGE_UNKNOWN|BILLING_UNKNOWN|PAID_TIMEOUT|NEEDS_ATTENTION/i.test(detail)) {
    return {
      summary: locale === "zh-CN"
        ? "系统暂时无法确认本次分析的提交或用量状态，已经停止继续执行，也不会自动重复提交。已完成结果和证据均会保留。"
        : "The system could not confirm the submission or usage state. Execution stopped without an automatic resubmission, and completed results and evidence were preserved.",
      technicalDetail,
    };
  }
  if (/stream client disconnected before settlement/i.test(detail)) {
    return {
      summary: locale === "zh-CN"
        ? "模型响应中断，用量尚待核对。预测已安全停止，不会自动重复提交，已有材料和进度均会保留。"
        : "The model response was interrupted before usage could be settled. The prediction stopped without an automatic resubmission, and existing materials and progress were preserved.",
      technicalDetail,
    };
  }
  if (/RemoteProtocolError|incomplete chunked read|closed connection without sending complete message body/i.test(detail)) {
    return {
      summary: locale === "zh-CN"
        ? "上游分析服务提前中断了响应。本次预测已经停止，已完成结果和证据均会保留。"
        : "The upstream analysis service ended its response early. This prediction stopped, and completed results and evidence were preserved.",
      technicalDetail,
    };
  }
  if (/ConnectTimeout|connection timed out|request timed out|\btimeout\b/i.test(detail)) {
    return {
      summary: locale === "zh-CN"
        ? "连接上游分析服务时超时。本次操作已经停止，已有材料、结果和证据均会保留。"
        : "The connection to the upstream analysis service timed out. The operation stopped, and existing materials, results, and evidence were preserved.",
      technicalDetail,
    };
  }
  if (/MCP_CONTEXT_UNREACHABLE|RUNTIME_UNAVAILABLE|connection|network/i.test(detail)) {
    return {
      summary: locale === "zh-CN"
        ? "部分外部服务暂时不可用。系统已保留现有材料和进度，恢复服务后可以继续处理。"
        : "Part of the external service is temporarily unavailable. Existing materials and progress were preserved so processing can continue after service recovery.",
      technicalDetail,
    };
  }
  if (isTechnicalError(detail) || (locale === "zh-CN" && (/^[A-Z][A-Z0-9_]+(?::|$)/.test(detail) || mostlyEnglish(detail)))) {
    return {
      summary: locale === "zh-CN"
        ? "预测过程中遇到了一项需要处理的问题，已有资料和完成进度均会保留。"
        : "The prediction encountered an issue that needs attention. Existing information and completed progress were preserved.",
      technicalDetail: detail,
    };
  }
  return { summary: locale === "zh-CN" ? cleanChinese(detail) : detail };
}

export function humanizeUserError(value: string, locale: Locale): string {
  return formatUserVisibleError(value, locale).summary;
}

export function formatUserVisibleAgentText(
  value: string,
  locale: Locale,
  channel: "supervisor" | "user-evidence" | "product-engineering" | "business-investment",
): string {
  if (locale === "en" || !mostlyEnglish(value)) return locale === "zh-CN" ? cleanChinese(value) : value;
  const fallback = {
    supervisor: "项目负责人正在汇总各个专业视角，并根据已核对的证据更新结论。",
    "user-evidence": "正在核对目标用户、真实使用和付费意愿方面的证据。",
    "product-engineering": "正在检查核心功能、交付能力和实际可用程度。",
    "business-investment": "正在判断商业空间、成本、价格和继续投入的条件。",
  };
  return fallback[channel];
}
