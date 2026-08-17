import assert from "node:assert/strict";
import test from "node:test";

import type { Report, Run } from "../../src/lib/api-client.ts";
import {
  executionMode,
  hasLayeredSupervisorReport,
  isSupervisorExperience,
  supervisorAdmissionEnabled,
  supervisorControlAction,
  supervisorProgress,
} from "../../src/lib/supervisor-experience.ts";

const run = (values: Partial<Run>): Run => ({
  run_id: "run", project_id: "project", product_version_id: "version", status: "RUNNING",
  standard_version: "1.0", current_cursor: "event.initial", correlation_id: "correlation",
  ...values,
});

test("feature flag off blocks new admission while completed v4 Runs remain readable", () => {
  const previousFlag = process.env.NEXT_PUBLIC_LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED;
  const previousMode = process.env.NEXT_PUBLIC_LAUNCHSCOPE_EXECUTION_MODE;
  process.env.NEXT_PUBLIC_LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED = "false";
  process.env.NEXT_PUBLIC_LAUNCHSCOPE_EXECUTION_MODE = "LIVE";
  assert.equal(supervisorAdmissionEnabled(), false);
  assert.equal(isSupervisorExperience(run({
    status: "COMPLETED", ui_mode: "SUPERVISOR_1P4", architecture_generation: "supervisor-1p4-v1",
  })), true);
  assert.equal(isSupervisorExperience(run({ ui_mode: "LEGACY", architecture_generation: "legacy-1p5" })), false);
  assert.equal(isSupervisorExperience(run({ ui_mode: "LEGACY", architecture_generation: "supervisor-1p4-v1" })), false);
  if (previousFlag === undefined) delete process.env.NEXT_PUBLIC_LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED;
  else process.env.NEXT_PUBLIC_LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED = previousFlag;
  if (previousMode === undefined) delete process.env.NEXT_PUBLIC_LAUNCHSCOPE_EXECUTION_MODE;
  else process.env.NEXT_PUBLIC_LAUNCHSCOPE_EXECUTION_MODE = previousMode;
});

test("recorded mode never admits a real supervisor dispatch", () => {
  const previousFlag = process.env.NEXT_PUBLIC_LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED;
  const previousMode = process.env.NEXT_PUBLIC_LAUNCHSCOPE_EXECUTION_MODE;
  process.env.NEXT_PUBLIC_LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED = "true";
  process.env.NEXT_PUBLIC_LAUNCHSCOPE_EXECUTION_MODE = "RECORDED";
  try {
    assert.equal(executionMode(), "RECORDED");
    assert.equal(supervisorAdmissionEnabled(), false);
  } finally {
    if (previousFlag === undefined) delete process.env.NEXT_PUBLIC_LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED;
    else process.env.NEXT_PUBLIC_LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED = previousFlag;
    if (previousMode === undefined) delete process.env.NEXT_PUBLIC_LAUNCHSCOPE_EXECUTION_MODE;
    else process.env.NEXT_PUBLIC_LAUNCHSCOPE_EXECUTION_MODE = previousMode;
  }
});

test("v4 progress renders the backend ordinal without reading chat text or timers", () => {
  const projected = run({
    ui_mode: "SUPERVISOR_1P4",
    architecture_generation: "supervisor-1p4-v1",
    experience_stage: {
      ordinal: 3,
      code: "REVIEW_REPORT",
      label: "审核并生成报告",
      exception: null,
      exception_label: null,
    },
  });

  assert.equal(isSupervisorExperience(projected), true);
  assert.deepEqual(supervisorProgress(projected).map(item => item.state), ["complete", "complete", "current", "upcoming"]);
});

test("v2.2 material routing and report generations keep the supervisor experience", () => {
  for (const architectureGeneration of ["supervisor-1p4-material-routing-v2", "supervisor-1p4-report-v22"]) {
    const projected = run({
      ui_mode: "SUPERVISOR_1P4",
      architecture_generation: architectureGeneration,
      experience_stage: {
        ordinal: 1,
        code: "UNDERSTANDING",
        label: "正在了解项目",
        exception: "NEEDS_CONFIRMATION",
        exception_label: "需要确认",
      },
    });

    assert.equal(isSupervisorExperience(projected), true);
    assert.equal(hasLayeredSupervisorReport({
      architecture_generation: architectureGeneration,
      layered_report: { summary: "x" },
    } as Report), true);
  }
});

test("NEEDS_ATTENTION is presented as the persisted confirmation exception", () => {
  const projected = run({
    ui_mode: "SUPERVISOR_1P4",
    architecture_generation: "supervisor-1p4-v1",
    status: "NEEDS_ATTENTION",
    experience_stage: {
      ordinal: 2,
      code: "MULTI_REVIEW",
      label: "多维预测",
      exception: "NEEDS_CONFIRMATION",
      exception_label: "需要确认",
    },
  });

  assert.equal(projected.experience_stage?.exception, "NEEDS_CONFIRMATION");
  assert.equal(supervisorControlAction(projected), "RECOVER");
});

test("execution controls distinguish a user pause from a needs-attention stop", () => {
  const active = run({
    execution_control: {
      state: "ACTIVE", control_epoch: 2, usage_settlement_status: "NONE", in_flight_count: 0,
    },
  });
  const paused = run({
    execution_control: {
      state: "PAUSED", control_epoch: 3, usage_settlement_status: "SETTLED", in_flight_count: 0,
    },
  });
  const needsAttention = run({
    status: "NEEDS_ATTENTION",
    execution_control: {
      state: "ACTIVE", control_epoch: 2, usage_settlement_status: "NONE", in_flight_count: 0,
    },
  });

  assert.equal(supervisorControlAction(active), "PAUSE");
  assert.equal(supervisorControlAction(paused), "RESUME");
  assert.equal(supervisorControlAction(needsAttention), "RECOVER");
});

test("only a persisted v4 synthesis activates the layered report", () => {
  const report = {
    architecture_generation: "supervisor-1p4-v1",
    layered_report: { summary: "x" },
  } as Report;
  assert.equal(hasLayeredSupervisorReport(report), true);
  assert.equal(hasLayeredSupervisorReport({ ...report, architecture_generation: "legacy-1p5" }), false);
});
