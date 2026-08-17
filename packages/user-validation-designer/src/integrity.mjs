/** Cross-section integrity rules owned by the program, never by the executor. */

export function uniqueIdDiagnostics(groups) {
  const diagnostics = [];
  for (const [entity, { records = [], field }] of Object.entries(groups)) {
    const seen = new Set();
    for (const record of records ?? []) {
      const id = record?.[field];
      if (!id) diagnostics.push({ code: "missing_id", entity, ref: null, detail: field });
      else if (seen.has(id)) diagnostics.push({ code: "duplicate_id", entity, ref: id, detail: `${field} must be unique` });
      else seen.add(id);
    }
  }
  return diagnostics;
}

export function crossReferenceDiagnostics({ personas = [], jobs = [], hypotheses = [], evidence = [], issues = [], dimensions = {}, root = null }) {
  const personaIds = new Set(personas.map((p) => p.persona_id));
  const evidenceIds = new Set(evidence.map((e) => e.evidence_id));
  const diagnostics = [];
  const check = (ids, allowed, entity, ref, field) => {
    for (const id of ids ?? []) if (!allowed.has(id)) diagnostics.push({ code: "unknown_reference", entity, ref, detail: `${field} -> ${id}` });
  };
  for (const job of jobs) check(job.persona_ids, personaIds, "job", job.job_id, "persona_ids");
  for (const hypothesis of hypotheses) {
    check(hypothesis.supporting_refs, evidenceIds, "hypothesis", hypothesis.hypothesis_id, "supporting_refs");
    check(hypothesis.contradicting_refs, evidenceIds, "hypothesis", hypothesis.hypothesis_id, "contradicting_refs");
  }
  for (const issue of issues) {
    check(issue.affected_personas, personaIds, "issue", issue.issue_id, "affected_personas");
    check(issue.evidence_refs, evidenceIds, "issue", issue.issue_id, "evidence_refs");
  }
  for (const [key, dimension] of Object.entries(dimensions)) check(dimension.evidence_refs, evidenceIds, "dimension", key, "evidence_refs");
  const visit = (value, path = "public_output") => {
    if (Array.isArray(value)) {
      value.forEach((entry, index) => visit(entry, `${path}[${index}]`));
      return;
    }
    if (!value || typeof value !== "object") return;
    for (const [key, child] of Object.entries(value)) {
      const childPath = `${path}.${key}`;
      if (["evidence_refs", "supporting_refs", "contradicting_refs"].includes(key) && Array.isArray(child)) {
        check(child, evidenceIds, "public_record", path, key);
      } else visit(child, childPath);
    }
  };
  if (root) visit(root);
  return diagnostics.filter((entry, index, all) => all.findIndex((candidate) =>
    candidate.code === entry.code && candidate.entity === entry.entity && candidate.ref === entry.ref && candidate.detail === entry.detail,
  ) === index);
}

export function exactPersonaRecords(records, personas, { field = "persona_id", label = "records" } = {}) {
  const expected = new Set((personas ?? []).map((persona) => persona.persona_id));
  const buckets = new Map([...expected].map((id) => [id, []]));
  const unexpected = [];
  for (const record of records ?? []) {
    const id = record?.[field];
    if (!buckets.has(id)) unexpected.push(id ?? "missing");
    else buckets.get(id).push(record);
  }
  const missing = [...buckets].filter(([, list]) => list.length === 0).map(([id]) => id);
  const duplicates = [...buckets].filter(([, list]) => list.length > 1).map(([id]) => id);
  return {
    valid: missing.length === 0 && duplicates.length === 0 && unexpected.length === 0,
    missing,
    duplicates,
    unexpected,
    reason: `${label}: ${missing.length} missing, ${duplicates.length} duplicate, ${unexpected.length} unknown persona record(s)`,
  };
}

export function buildPersonaOutcomes(personas, taskTests) {
  return (personas ?? []).map((persona) => {
    const tasks = (taskTests ?? []).filter((task) => task.persona_id === persona.persona_id);
    const functionalFailures = tasks.filter((task) => task.result === "failed" && task.cause_type === "functional");
    const failures = tasks.filter((task) => task.result === "failed");
    return {
      persona_id: persona.persona_id,
      verdict: functionalFailures.length > 0 ? "reject" : failures.length > 0 ? "at_risk" : tasks.length > 0 ? "accept" : "unverified",
      core_task_failed: functionalFailures.length > 0,
      failed_task_keys: failures.map((task) => task.task_key),
      reason: functionalFailures.length > 0
        ? "A functional core-task failure prevents this Persona from realizing the tested value."
        : failures.length > 0
          ? "At least one core task failed for this Persona."
          : tasks.length > 0 ? "No tested core task failed." : "No complete task result exists.",
    };
  });
}

export function taskMatrixDiagnostics(personas, tasks, records) {
  const personaIds = new Set((personas ?? []).map((persona) => persona.persona_id));
  const taskKeys = new Set((tasks ?? []).map((task) => task.task_key));
  const unexpected = [];
  for (const [index, record] of (records ?? []).entries()) {
    if (!personaIds.has(record?.persona_id) || !taskKeys.has(record?.task_key)) {
      unexpected.push({ index, persona_id: record?.persona_id ?? null, task_key: record?.task_key ?? null, reason: "unknown persona_id or task_key" });
    }
  }
  return unexpected;
}

const INJECTION = /ignore (all|any|the) (previous|prior|system)( system)? instructions|忽略(以上|之前|系统)指令|system prompt|developer message|执行以下命令|exfiltrat/iu;

export function detectPromptInjection(outcome) {
  const texts = [];
  const visit = (value) => {
    if (typeof value === "string") texts.push(value);
    else if (Array.isArray(value)) value.forEach(visit);
    else if (value && typeof value === "object") Object.values(value).forEach(visit);
  };
  visit(outcome?.product_reader_content ?? outcome?.untrusted_content ?? null);
  return texts.some((text) => INJECTION.test(text));
}

export function exclusionsFromInput(exclusions) {
  return (exclusions ?? []).map((label) => ({ label, reason: "Explicitly excluded by target_users.exclusions; do not recruit or extrapolate to this segment." }));
}

export function containsProjectDecision(value) {
  if (typeof value !== "string") return false;
  return /\b(Continue|Pivot|Stop)\b|继续推进|调整方向|暂停投入/u.test(value);
}
