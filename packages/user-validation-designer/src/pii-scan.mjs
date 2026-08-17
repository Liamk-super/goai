/**
 * PII and credential guard. Enforces A-21 in program code rather than in the
 * prompt: personal data must never reach the model context or the report.
 *
 * Reports the LOCATION of a finding, never the value. This matters more here
 * than in the technical audit skill: user research legitimately deals with
 * people, so the boundary between "aggregate observation" (allowed) and
 * "identifiable record" (forbidden) has to be mechanical.
 */

export const REDACTED = "[REDACTED]";

/** Field names that denote personal data regardless of their value. */
const PII_KEY_PATTERN =
  /(phone|mobile|tel|телефон|手机|电话|email|e[_-]?mail|邮箱|wechat|weixin|微信|qq号|身份证|id[_-]?card|idcard|ssn|passport|护照|学号|student[_-]?id|address|地址|real[_-]?name|真实姓名|姓名|birth|生日|出生|bank[_-]?card|银行卡|contact[_-]?info|联系方式)/i;

/** Credential field names — reuse of the first skill's boundary. */
const SECRET_KEY_PATTERN =
  /(password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key|client[_-]?secret|cookie|session[_-]?id|seed[_-]?phrase|mnemonic|authorization|dotenv|env[_-]?file)/i;

/** Value shapes that are personal data wherever they appear. */
const PII_VALUE_PATTERNS = Object.freeze([
  { pattern: /(?<!\d)1[3-9]\d{9}(?!\d)/, label: "cn_mobile_number" },
  { pattern: /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/, label: "email_address" },
  { pattern: /(?<!\d)\d{17}[\dXx](?!\d)/, label: "cn_id_card" },
  { pattern: /(?<!\d)\d{16,19}(?!\d)/, label: "bank_card_like" },
  { pattern: /\b(?:\+?\d{1,3}[- ]?)?\(?\d{3}\)?[- ]?\d{3}[- ]?\d{4}\b/, label: "intl_phone_like" },
]);

const SECRET_VALUE_PATTERNS = Object.freeze([
  { pattern: /-----BEGIN [A-Z ]*PRIVATE KEY-----/, label: "private_key" },
  { pattern: /\bgh[pousr]_[A-Za-z0-9]{16,}\b/, label: "github_token" },
  { pattern: /\bsk-[A-Za-z0-9_-]{16,}\b/, label: "api_key" },
  { pattern: /\bAKIA[0-9A-Z]{16}\b/, label: "aws_key" },
  { pattern: /\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/, label: "jwt" },
]);

/**
 * Paths that are allowed to contain a URL with an @ or digits — product and
 * evidence source locations are not personal data.
 */
const URL_LIKE_PATHS = new Set([
  "product_profile.url",
  "product_profile.experience_report_ref",
]);

const OPAQUE_CREDENTIAL_REFERENCE = /^cred:\/\/[A-Za-z][A-Za-z0-9._-]{2,63}$/;

function decodeReasonably(value) {
  let decoded = value;
  for (let pass = 0; pass < 2; pass += 1) {
    try {
      const next = decodeURIComponent(decoded);
      if (next === decoded) break;
      decoded = next;
    } catch {
      break;
    }
  }
  return decoded;
}

function scanUrl(text, path, findings) {
  let url;
  try {
    url = new URL(text);
  } catch {
    findings.push({ path, label: "invalid_url", reason: "URL could not be parsed safely" });
    return;
  }
  if (url.username || url.password) {
    findings.push({ path, label: "url_userinfo_credential", reason: "URL userinfo may contain credentials" });
  }
  const sensitiveText = [url.pathname, url.search.slice(1), url.hash.slice(1)]
    .map(decodeReasonably)
    .join(" ");
  for (const { pattern, label } of [...PII_VALUE_PATTERNS, ...SECRET_VALUE_PATTERNS]) {
    if (pattern.test(sensitiveText)) findings.push({ path, label, reason: "decoded URL path, query, or fragment contains personal data or a credential" });
  }
}

/**
 * Scan an input envelope for personal data and credentials.
 * @returns {{clean: boolean, findings: Array<{path: string, label: string, reason: string}>}}
 */
export function scanInput(input) {
  const findings = [];

  const visit = (node, path) => {
    if (node === null || node === undefined) return;

    if (typeof node === "string") {
      const trimmed = node.trim();
      if (/^https?:\/\//i.test(trimmed)) {
        scanUrl(trimmed, path, findings);
        return;
      }
      if (/^cred:\/\//i.test(trimmed)) {
        if (!OPAQUE_CREDENTIAL_REFERENCE.test(trimmed) || SECRET_VALUE_PATTERNS.some(({ pattern }) => pattern.test(trimmed))) {
          findings.push({ path, label: "raw_credential_reference", reason: "cred:// must contain only an opaque credential id, never raw secret material" });
        }
        return;
      }
      if (URL_LIKE_PATHS.has(path) && /^EV-/i.test(trimmed)) return;
      const candidates = [...new Set([node, decodeReasonably(node)])];
      for (const { pattern, label } of PII_VALUE_PATTERNS) {
        if (candidates.some((candidate) => pattern.test(candidate))) {
          findings.push({ path, label, reason: "value matches a personal-data format" });
        }
      }
      for (const { pattern, label } of SECRET_VALUE_PATTERNS) {
        if (candidates.some((candidate) => pattern.test(candidate))) {
          findings.push({ path, label, reason: "value matches a known credential format" });
        }
      }
      return;
    }

    if (Array.isArray(node)) {
      node.forEach((item, index) => visit(item, `${path}[${index}]`));
      return;
    }

    if (typeof node === "object") {
      for (const [key, value] of Object.entries(node)) {
        const childPath = path ? `${path}.${key}` : key;
        if (PII_KEY_PATTERN.test(key)) {
          findings.push({
            path: childPath,
            label: "pii_field_name",
            reason: "field name denotes personal data; this skill must not receive it",
          });
          continue;
        }
        if (SECRET_KEY_PATTERN.test(key)) {
          findings.push({
            path: childPath,
            label: "credential_field_name",
            reason: "field name denotes a credential; pass an opaque reference instead",
          });
          continue;
        }
        visit(value, childPath);
      }
    }
  };

  visit(input, "");
  return { clean: findings.length === 0, findings };
}

export function isOpaqueCredentialReference(value) {
  return typeof value === "string" && OPAQUE_CREDENTIAL_REFERENCE.test(value) && !SECRET_VALUE_PATTERNS.some(({ pattern }) => pattern.test(value));
}

/** Replace personal data and credential values in free text with [REDACTED]. */
export function redact(text) {
  if (typeof text !== "string") return text;
  return [...PII_VALUE_PATTERNS, ...SECRET_VALUE_PATTERNS].reduce(
    (acc, { pattern }) => acc.replace(new RegExp(pattern.source, pattern.flags.includes("g") ? pattern.flags : `${pattern.flags}g`), REDACTED),
    text,
  );
}
