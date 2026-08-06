"""SQLAlchemy table metadata for the LaunchScope control-plane schema.

The database is created by the versioned Alembic migrations.  This metadata is
kept intentionally free of ORM models so repositories can adapt persistence to
the domain aggregates without making ``packages/domain`` depend on SQLAlchemy.
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, MetaData, Numeric, String, Table, Uuid

metadata = MetaData()


def _tenant_table(name: str, *columns: object) -> Table:
    return Table(
        name,
        metadata,
        # The database migration adds the composite foreign keys and RLS.  The
        # duplicate (tenant_id, id) key is what makes cross-tenant references
        # impossible even when an id is known by another tenant.
        Column("id", Uuid(as_uuid=True), primary_key=True),
        Column("tenant_id", Uuid(as_uuid=True), nullable=False, index=True),
        *columns,
    )


def _column(name: str, type_: object, **kwargs: object) -> Column[object]:
    return Column(name, type_, **kwargs)


def uuid() -> Uuid:
    return Uuid(as_uuid=True)


def json() -> JSON:
    return JSON()


def timestamp() -> DateTime:
    return DateTime(timezone=True)


tenant = Table(
    "tenant",
    metadata,
    _column("id", uuid(), primary_key=True),
    _column("slug", String(120), nullable=False, unique=True),
    _column("status", String(32), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

workspace = _tenant_table(
    "workspace",
    _column("name", String(200), nullable=False),
    _column("status", String(32), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

workspace_member = _tenant_table(
    "workspace_member",
    _column("workspace_id", uuid(), nullable=False),
    _column("actor_id", String(255), nullable=False),
    _column("role", String(32), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

project = _tenant_table(
    "project",
    _column("workspace_id", uuid(), nullable=False),
    _column("name", String(200), nullable=False),
    _column("dossier_status", String(32), nullable=False),
    _column("created_at", timestamp(), nullable=False),
    _column("updated_at", timestamp(), nullable=False),
)

product_version = _tenant_table(
    "product_version",
    _column("project_id", uuid(), nullable=False),
    _column("version_number", Integer, nullable=False),
    _column("label", String(100), nullable=False),
    _column("stage", String(64), nullable=False),
    _column("source_version", String(100)),
    _column("status", String(32), nullable=False),
    _column("submitted_by", String(255)),
    _column("submitted_at", timestamp()),
    _column("created_at", timestamp(), nullable=False),
)

material = _tenant_table(
    "material",
    _column("product_version_id", uuid(), nullable=False),
    _column("source_type", String(64), nullable=False),
    _column("object_key", String(1024), nullable=False),
    _column("sha256", String(64), nullable=False),
    _column("size_bytes", Integer, nullable=False),
    _column("mime_type", String(255), nullable=False),
    _column("display_name", String(255), nullable=False),
    _column("trust_level", String(16), nullable=False),
    _column("ingest_status", String(32), nullable=False),
    _column("rejection_reason", String(1000)),
    _column("object_metadata", json(), nullable=False),
    _column("submitted_at", timestamp(), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

product_profile = _tenant_table(
    "product_profile",
    _column("product_version_id", uuid(), nullable=False),
    _column("confirmed_fields", json(), nullable=False),
    _column("confirmation_status", String(32), nullable=False),
    _column("confirmed_by", String(255), nullable=False),
    _column("confirmed_at", timestamp(), nullable=False),
    _column("supersedes_id", uuid()),
    _column("created_at", timestamp(), nullable=False),
)

product_profile_draft = _tenant_table(
    "product_profile_draft",
    _column("product_version_id", uuid(), nullable=False),
    _column("source", String(32), nullable=False),
    _column("inferred_fields", json(), nullable=False),
    _column("answered_fields", json(), nullable=False),
    _column("status", String(32), nullable=False),
    _column("created_at", timestamp(), nullable=False),
    _column("confirmed_at", timestamp()),
)

intake_gap_question = _tenant_table(
    "intake_gap_question",
    _column("product_version_id", uuid(), nullable=False),
    _column("draft_id", uuid(), nullable=False),
    _column("correlation_id", uuid(), nullable=False),
    _column("field", String(100), nullable=False),
    _column("question", String(2000), nullable=False),
    _column("priority", Integer, nullable=False),
    _column("answer", String()),
    _column("answered_by", String(255)),
    _column("answered_at", timestamp()),
    _column("created_at", timestamp(), nullable=False),
)

agent_identity = Table(
    "agent_identity",
    metadata,
    _column("id", uuid(), primary_key=True),
    _column("code", String(120), nullable=False, unique=True),
    _column("version", String(20), nullable=False),
    _column("capabilities", json(), nullable=False),
    _column("allowed_actions", json(), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

skill_version = Table(
    "skill_version",
    metadata,
    _column("id", uuid(), primary_key=True),
    _column("skill_code", String(120), nullable=False),
    _column("version", String(20), nullable=False),
    _column("manifest_sha256", String(64), nullable=False),
    _column("input_schema", json(), nullable=False),
    _column("output_schema", json(), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

evaluation_run = _tenant_table(
    "evaluation_run",
    _column("project_id", uuid(), nullable=False),
    _column("product_version_id", uuid(), nullable=False),
    _column("status", String(40), nullable=False),
    _column("current_stage", String(64)),
    _column("state_flags", json(), nullable=False),
    _column("standard_version", String(20), nullable=False),
    _column("correlation_id", uuid(), nullable=False),
    _column("idempotency_key", String(200), nullable=False),
    _column("last_failure_class", String(40)),
    _column("attention_reason", String(1000)),
    _column("created_at", timestamp(), nullable=False),
    _column("updated_at", timestamp(), nullable=False),
)

run_manifest = Table(
    "run_manifest",
    metadata,
    _column("run_id", uuid(), primary_key=True),
    _column("tenant_id", uuid(), nullable=False, index=True),
    _column("frozen_config", json(), nullable=False),
    _column("manifest_sha256", String(64), nullable=False),
    _column("budget", json(), nullable=False),
    _column("security_policy", json(), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

stage = _tenant_table(
    "stage",
    _column("run_id", uuid(), nullable=False),
    _column("code", String(64), nullable=False),
    _column("ordinal", Integer, nullable=False),
    _column("status", String(32), nullable=False),
    _column("started_at", timestamp()),
    _column("completed_at", timestamp()),
)

task = _tenant_table(
    "task",
    _column("run_id", uuid(), nullable=False),
    _column("stage_id", uuid(), nullable=False),
    _column("agent_identity_id", uuid()),
    _column("skill_version_id", uuid()),
    _column("stage_code", String(64), nullable=False),
    _column("agent_identity_ref", String(200), nullable=False),
    _column("skill_ref", String(200), nullable=False),
    _column("skill_version", String(20), nullable=False),
    _column("status", String(40), nullable=False),
    _column("lease_token", String(255)),
    _column("idempotency_key", String(200), nullable=False),
    _column("dependencies", json(), nullable=False),
    _column("tool_allowlist", json(), nullable=False),
    _column("budget_slice", json()),
    _column("timeout_seconds", Integer, nullable=False),
    _column("success_condition", json(), nullable=False),
    _column("evidence_requirement", String(1000)),
    _column("required", Boolean, nullable=False),
    _column("correction_attempts", Integer, nullable=False),
    _column("transient_retries", Integer, nullable=False),
    _column("last_failure_class", String(40)),
    _column("last_error", String(1000)),
    _column("side_effect_started", Boolean, nullable=False),
    _column("created_at", timestamp(), nullable=False),
    _column("updated_at", timestamp(), nullable=False),
)

task_dependency = _tenant_table(
    "task_dependency",
    _column("task_id", uuid(), nullable=False),
    _column("depends_on_task_id", uuid(), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

run_status_history = _tenant_table(
    "run_status_history",
    _column("run_id", uuid(), nullable=False),
    _column("from_status", String(40), nullable=False),
    _column("to_status", String(40), nullable=False),
    _column("reason", String(1000), nullable=False),
    _column("failure_class", String(40)),
    _column("occurred_at", timestamp(), nullable=False),
)

skill_invocation = _tenant_table(
    "skill_invocation",
    _column("task_id", uuid(), nullable=False),
    _column("skill_version_id", uuid(), nullable=False),
    _column("status", String(32), nullable=False),
    _column("idempotency_key", String(200), nullable=False),
    _column("estimated_cost", Numeric(20, 6), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

tool_invocation = _tenant_table(
    "tool_invocation",
    _column("skill_invocation_id", uuid(), nullable=False),
    _column("tool_code", String(120), nullable=False),
    _column("risk_tier", String(32), nullable=False),
    _column("status", String(32), nullable=False),
    _column("parameters_sha256", String(64), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

evidence = _tenant_table(
    "evidence",
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid()),
    _column("material_id", uuid()),
    _column("source_type", String(64), nullable=False),
    _column("object_key", String(1024), nullable=False),
    _column("sha256", String(64), nullable=False),
    _column("size_bytes", Integer, nullable=False),
    _column("mime_type", String(255), nullable=False),
    _column("evidence_level", String(16), nullable=False),
    _column("trust_level", String(16), nullable=False),
    _column("summary", String(4000), nullable=False),
    _column("published_at", timestamp()),
    _column("fetched_at", timestamp()),
    _column("valid_from", timestamp()),
    _column("valid_until", timestamp()),
    _column("region", String(100)),
    _column("simulated", Boolean, nullable=False),
    _column("supersedes_id", uuid()),
    _column("created_at", timestamp(), nullable=False),
)

finding = _tenant_table(
    "finding",
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid()),
    _column("dimension_code", String(64), nullable=False),
    _column("grade", String(40), nullable=False),
    _column("claim_type", String(64), nullable=False),
    _column("statement", String(10000), nullable=False),
    _column("is_hypothesis", Boolean, nullable=False),
    _column("submitted_by", String(255), nullable=False),
    _column("submitted_at", timestamp(), nullable=False),
    _column("supersedes_id", uuid()),
    _column("structured_result", json(), nullable=False),
    _column("simulated", Boolean, nullable=False),
    _column("hard_block", Boolean, nullable=False),
    _column("block_reason", String(1000)),
)

finding_evidence = Table(
    "finding_evidence",
    metadata,
    _column("tenant_id", uuid(), nullable=False, index=True),
    _column("finding_id", uuid(), nullable=False),
    _column("evidence_id", uuid(), nullable=False),
    _column("relation_type", String(32), nullable=False),
)

conflict_record = _tenant_table(
    "conflict_record",
    _column("run_id", uuid(), nullable=False),
    _column("finding_id", uuid(), nullable=False),
    _column("conflicting_refs", json(), nullable=False),
    _column("resolution_status", String(32), nullable=False),
    _column("reason", String(2000), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

evidence_audit = _tenant_table(
    "evidence_audit",
    _column("run_id", uuid(), nullable=False),
    _column("finding_id", uuid(), nullable=False),
    _column("decision", String(40), nullable=False),
    _column("auditor_id", String(255), nullable=False),
    _column("reason", String(2000), nullable=False),
    _column("audited_at", timestamp(), nullable=False),
)

decision = _tenant_table(
    "decision",
    _column("run_id", uuid(), nullable=False),
    _column("recommendation", String(40), nullable=False),
    _column("standard_version", String(20), nullable=False),
    _column("dimension_grades", json(), nullable=False),
    _column("hard_blocks", json(), nullable=False),
    _column("supersedes_id", uuid()),
    _column("created_at", timestamp(), nullable=False),
)

decision_finding = Table(
    "decision_finding",
    metadata,
    _column("tenant_id", uuid(), nullable=False, index=True),
    _column("decision_id", uuid(), nullable=False),
    _column("finding_id", uuid(), nullable=False),
    _column("role", String(32), nullable=False),
)

report = _tenant_table(
    "report",
    _column("run_id", uuid(), nullable=False),
    _column("decision_id", uuid(), nullable=False),
    _column("object_key", String(1024), nullable=False),
    _column("sha256", String(64), nullable=False),
    _column("status", String(32), nullable=False),
    _column("action_items", json(), nullable=False),
    _column("supersedes_id", uuid()),
    _column("created_at", timestamp(), nullable=False),
)

memory_candidate = _tenant_table(
    "memory_candidate",
    _column("project_id", uuid(), nullable=False),
    _column("source_finding_id", uuid()),
    _column("status", String(32), nullable=False),
    _column("candidate", json(), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

memory_item = _tenant_table(
    "memory_item",
    _column("project_id", uuid(), nullable=False),
    _column("product_version_id", uuid()),
    _column("source_finding_id", uuid()),
    _column("item_type", String(64), nullable=False),
    _column("validity_status", String(32), nullable=False),
    _column("valid_until", timestamp()),
    _column("region", String(100)),
    _column("permission_scope", String(120)),
    _column("search_text", String(8000)),
    _column("content", json(), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

rag_retrieval = _tenant_table(
    "rag_retrieval",
    _column("project_id", uuid(), nullable=False),
    _column("product_version_id", uuid(), nullable=False),
    _column("run_id", uuid()),
    _column("query_sha256", String(64), nullable=False),
    _column("filters", json(), nullable=False),
    _column("hit_memory_ids", json(), nullable=False),
    _column("hit_finding_ids", json(), nullable=False),
    _column("hit_evidence_ids", json(), nullable=False),
    _column("result_sha256", String(64), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

approval_request = _tenant_table(
    "approval_request",
    _column("run_id", uuid(), nullable=False),
    _column("tool_code", String(120), nullable=False),
    _column("parameters_sha256", String(64), nullable=False),
    _column("status", String(32), nullable=False),
    _column("expires_at", timestamp(), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

usage_record = _tenant_table(
    "usage_record",
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid()),
    _column("category", String(100), nullable=False),
    _column("quantity", Numeric(20, 6), nullable=False),
    _column("cost", Numeric(20, 6), nullable=False),
    _column("idempotency_key", String(200), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

audit_event = _tenant_table(
    "audit_event",
    _column("run_id", uuid()),
    _column("actor_type", String(64), nullable=False),
    _column("action", String(120), nullable=False),
    _column("outcome", String(32), nullable=False),
    _column("payload_sha256", String(64), nullable=False),
    _column("metadata", json(), nullable=False),
    _column("occurred_at", timestamp(), nullable=False),
)

budget_reservation = _tenant_table(
    "budget_reservation",
    _column("run_id", uuid(), nullable=False),
    _column("category", String(64), nullable=False),
    _column("currency", String(8), nullable=False),
    _column("limit_amount", Numeric(20, 6), nullable=False),
    _column("reserved_amount", Numeric(20, 6), nullable=False),
    _column("consumed_amount", Numeric(20, 6), nullable=False),
    _column("released_amount", Numeric(20, 6), nullable=False),
    _column("status", String(32), nullable=False),
    _column("idempotency_key", String(200), nullable=False),
    _column("created_at", timestamp(), nullable=False),
    _column("updated_at", timestamp(), nullable=False),
)

retention_policy = _tenant_table(
    "retention_policy",
    _column("temporary_days", Integer, nullable=False),
    _column("evidence_days", Integer, nullable=False),
    _column("trace_body_days", Integer, nullable=False),
    _column("metrics_days", Integer, nullable=False),
    _column("audit_days", Integer, nullable=False),
    _column("created_at", timestamp(), nullable=False),
    _column("updated_at", timestamp(), nullable=False),
)

deletion_tombstone = _tenant_table(
    "deletion_tombstone",
    _column("target_type", String(32), nullable=False),
    _column("target_id", uuid(), nullable=False),
    _column("target_sha256", String(64), nullable=False),
    _column("actor_id", String(255), nullable=False),
    _column("reason", String(500), nullable=False),
    _column("result", json(), nullable=False),
    _column("occurred_at", timestamp(), nullable=False),
)

matrix_handoff = _tenant_table(
    "matrix_handoff",
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid(), nullable=False),
    _column("room_id", String(255), nullable=False),
    _column("sender_agent", String(120), nullable=False),
    _column("receiver_agent", String(120), nullable=False),
    _column("kind", String(40), nullable=False),
    _column("finding_id", uuid()),
    _column("evidence_ids", json(), nullable=False),
    _column("risk", String(32), nullable=False),
    _column("confidence", Numeric(5, 4), nullable=False),
    _column("approval_required", Boolean, nullable=False),
    _column("payload_sha256", String(64), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

trace_metadata = _tenant_table(
    "trace_metadata",
    _column("run_id", uuid()),
    _column("stage_id", uuid()),
    _column("task_id", uuid()),
    _column("correlation_id", uuid(), nullable=False),
    _column("span_id", String(128), nullable=False),
    _column("attributes", json(), nullable=False),
    _column("payload_sha256", String(64)),
    _column("created_at", timestamp(), nullable=False),
)

outbox_message = _tenant_table(
    "outbox_message",
    _column("aggregate_id", uuid(), nullable=False),
    _column("aggregate_type", String(120), nullable=False),
    _column("event_type", String(160), nullable=False),
    _column("event_id", uuid(), nullable=False),
    _column("schema_version", String(20), nullable=False),
    _column("idempotency_key", String(200), nullable=False),
    _column("payload", json(), nullable=False),
    _column("publish_status", String(32), nullable=False),
    _column("available_at", timestamp(), nullable=False),
    _column("published_at", timestamp()),
    _column("attempts", Integer, nullable=False),
    _column("last_error", String(2000)),
    _column("claimed_by", String(160)),
    _column("claimed_at", timestamp()),
    _column("occurred_at", timestamp(), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

inbox_message = _tenant_table(
    "inbox_message",
    _column("outbox_message_id", uuid()),
    _column("consumer_name", String(120), nullable=False),
    _column("dedupe_key", String(200), nullable=False),
    _column("event_id", uuid(), nullable=False),
    _column("event_type", String(160), nullable=False),
    _column("payload", json(), nullable=False),
    _column("processing_status", String(32), nullable=False),
    _column("received_at", timestamp(), nullable=False),
    _column("processed_at", timestamp()),
    _column("last_error", String(2000)),
    _column("created_at", timestamp(), nullable=False),
)

event_delivery_attempt = _tenant_table(
    "event_delivery_attempt",
    _column("outbox_message_id", uuid(), nullable=False),
    _column("attempt_no", Integer, nullable=False),
    _column("status", String(32), nullable=False),
    _column("error", String(2000)),
    _column("attempted_at", timestamp(), nullable=False),
)

agentteams_run_binding = _tenant_table(
    "agentteams_run_binding",
    _column("run_id", uuid(), nullable=False),
    _column("agentteams_version", String(32), nullable=False),
    _column("team_name", String(160), nullable=False),
    _column("team_room_id", String(255)),
    _column("leader_room_id", String(255)),
    _column("binding_status", String(32), nullable=False),
    _column("created_at", timestamp(), nullable=False),
    _column("updated_at", timestamp(), nullable=False),
)

matrix_event_receipt = _tenant_table(
    "matrix_event_receipt",
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid()),
    _column("room_id", String(255), nullable=False),
    _column("matrix_event_id", String(255), nullable=False),
    _column("sender_mxid", String(255), nullable=False),
    _column("payload_sha256", String(64), nullable=False),
    _column("processing_status", String(32), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)


TENANT_SCOPED_TABLES: tuple[str, ...] = tuple(table.name for table in metadata.sorted_tables if "tenant_id" in table.c)

__all__ = ["TENANT_SCOPED_TABLES", "metadata"]
