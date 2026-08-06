"""LaunchScope OpenTelemetry semantic conventions.

Only identifiers, versions, counters, timings, outcomes and hashes belong in
the general-purpose telemetry backend. Business bodies remain in the guarded
evidence store.
"""

from __future__ import annotations

TRACE_LEVELS = (
    "evaluation_run",
    "stage",
    "agent_task",
    "skill_invocation",
    "llm_call",
    "tool_call",
    "rag_retrieval",
    "evidence_write",
)

ALLOWED_ATTRIBUTES = frozenset(
    {
        "launchscope.tenant.id",
        "launchscope.workspace.id",
        "launchscope.project.id",
        "launchscope.product_version.id",
        "launchscope.run.id",
        "launchscope.stage.code",
        "launchscope.task.id",
        "launchscope.agent.code",
        "launchscope.agent.version",
        "launchscope.skill.code",
        "launchscope.skill.version",
        "launchscope.model.id",
        "launchscope.model.version",
        "launchscope.tool.code",
        "launchscope.tool.version",
        "launchscope.schema.version",
        "launchscope.correlation.id",
        "launchscope.failure.class",
        "launchscope.outcome",
        "launchscope.retry.count",
        "launchscope.token.count",
        "launchscope.cost.amount",
        "launchscope.cost.currency",
        "launchscope.duration.ms",
        "launchscope.evidence.id",
        "launchscope.payload.sha256",
        "launchscope.approval.status",
        "launchscope.degradation.code",
    }
)

__all__ = ["ALLOWED_ATTRIBUTES", "TRACE_LEVELS"]
