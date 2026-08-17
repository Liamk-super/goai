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

material_analysis = _tenant_table(
    "material_analysis",
    _column("material_id", uuid(), nullable=False),
    _column("product_version_id", uuid(), nullable=False),
    _column("status", String(32), nullable=False),
    _column("attempt", Integer, nullable=False),
    _column("parser_version", String(80), nullable=False),
    _column("model_id", String(200)),
    _column("manifest_object_key", String(1024)),
    _column("manifest_sha256", String(64)),
    _column("page_count", Integer, nullable=False),
    _column("unit_count", Integer, nullable=False),
    _column("coverage", json(), nullable=False),
    _column("error_code", String(120)),
    _column("error_message", String(2000)),
    _column("external_consent", Boolean, nullable=False),
    _column("created_at", timestamp(), nullable=False),
    _column("updated_at", timestamp(), nullable=False),
    _column("completed_at", timestamp()),
)

material_unit = _tenant_table(
    "material_unit",
    _column("analysis_id", uuid(), nullable=False),
    _column("material_id", uuid(), nullable=False),
    _column("product_version_id", uuid(), nullable=False),
    _column("parent_unit_id", uuid()),
    _column("ordinal", Integer, nullable=False),
    _column("unit_type", String(32), nullable=False),
    _column("locator", json(), nullable=False),
    _column("tags", json(), nullable=False),
    _column("confidence", Numeric(5, 4), nullable=False),
    _column("contains_sensitive_data", Boolean, nullable=False),
    _column("object_key", String(1024), nullable=False),
    _column("sha256", String(64), nullable=False),
    _column("summary", String(2000), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

material_selection = _tenant_table(
    "material_selection",
    _column("product_version_id", uuid(), nullable=False),
    _column("revision", Integer, nullable=False),
    _column("idempotency_key", String(255), nullable=False),
    _column("request_sha256", String(64), nullable=False),
    _column("object_key", String(1024), nullable=False),
    _column("sha256", String(64), nullable=False),
    _column("confirmed_by", String(255), nullable=False),
    _column("confirmed_at", timestamp(), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

material_selection_item = _tenant_table(
    "material_selection_item",
    _column("selection_id", uuid(), nullable=False),
    _column("material_id", uuid(), nullable=False),
    _column("analysis_id", uuid(), nullable=False),
    _column("decision", String(32), nullable=False),
    _column("acknowledged_uncovered_locators", json(), nullable=False),
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

user_validation_script = _tenant_table(
    "user_validation_script",
    _column("product_version_id", uuid(), nullable=False),
    _column("revision", Integer, nullable=False),
    _column("object_key", String(1024), nullable=False),
    _column("sha256", String(64), nullable=False),
    _column("product_tasks_sha256", String(64), nullable=False),
    _column("task_count", Integer, nullable=False),
    _column("confirmed_by", String(255), nullable=False),
    _column("idempotency_key", String(200), nullable=False),
    _column("request_sha256", String(64), nullable=False),
    _column("confirmed_at", timestamp(), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

user_evidence_metadata = _tenant_table(
    "user_evidence_metadata",
    _column("product_version_id", uuid(), nullable=False),
    _column("object_key", String(1024), nullable=False),
    _column("sha256", String(64), nullable=False),
    _column("kind", String(40), nullable=False),
    _column("claimed_tier", String(16), nullable=False),
    _column("source_tier", String(32)),
    _column("source", String(1000), nullable=False),
    _column("observed_at", timestamp(), nullable=False),
    _column("expires_at", timestamp()),
    _column("sample_size", Integer),
    _column("segment", String(500)),
    _column("aggregate_observation", String(4000), nullable=False),
    _column("applicability", json(), nullable=False),
    _column("supporting_claim_refs", json(), nullable=False),
    _column("contradicting_claim_refs", json(), nullable=False),
    _column("idempotency_key", String(200), nullable=False),
    _column("request_sha256", String(64), nullable=False),
    _column("created_by", String(255), nullable=False),
    _column("created_at", timestamp(), nullable=False),
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

requirement_brief = _tenant_table(
    "requirement_brief",
    _column("product_version_id", uuid(), nullable=False),
    _column("revision", Integer, nullable=False),
    _column("schema_version", String(16), nullable=False),
    _column("raw_input_object_key", String(1024), nullable=False),
    _column("raw_input_sha256", String(64), nullable=False),
    _column("document", json(), nullable=False),
    _column("confirmation_required", Boolean, nullable=False),
    _column("status", String(32), nullable=False),
    _column("created_by", String(255), nullable=False),
    _column("created_at", timestamp(), nullable=False),
    _column("confirmed_at", timestamp()),
)

supervisor_chat_message = _tenant_table(
    "supervisor_chat_message",
    _column("product_version_id", uuid(), nullable=False),
    _column("brief_id", uuid()),
    _column("role", String(16), nullable=False),
    _column("message_kind", String(32), nullable=False),
    _column("object_key", String(1024), nullable=False),
    _column("sha256", String(64), nullable=False),
    _column("request_sha256", String(64), nullable=False),
    _column("idempotency_key", String(200), nullable=False),
    _column("correlation_id", uuid(), nullable=False),
    _column("interaction_state", String(32), nullable=False),
    _column("created_by", String(255), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

run_conversation_message = _tenant_table(
    "run_conversation_message",
    _column("run_id", uuid(), nullable=False),
    _column("channel", String(40), nullable=False),
    _column("role", String(16), nullable=False),
    _column("message_kind", String(32), nullable=False),
    _column("object_key", String(1024), nullable=False),
    _column("sha256", String(64), nullable=False),
    _column("request_sha256", String(64), nullable=False),
    _column("idempotency_key", String(200), nullable=False),
    _column("correlation_id", uuid(), nullable=False),
    _column("route_state", String(32), nullable=False),
    _column("affected_task_ids", json(), nullable=False),
    _column("response", json(), nullable=False),
    _column("created_by", String(255), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

requirement_change = _tenant_table(
    "requirement_change",
    _column("run_id", uuid(), nullable=False),
    _column("brief_id", uuid(), nullable=False),
    _column("document", json(), nullable=False),
    _column("status", String(32), nullable=False),
    _column("created_by", String(255), nullable=False),
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
    _column("last_failure_class", String(120)),
    _column("attention_reason", String(1000)),
    _column("run_kind", String(40), nullable=False, default="FULL_EVALUATION"),
    _column("baseline_run_id", uuid()),
    _column("input_snapshot_sha256", String(64)),
    _column("content_fingerprint_sha256", String(64)),
    _column("report_profile_ref", String(160)),
    _column("created_at", timestamp(), nullable=False),
    _column("updated_at", timestamp(), nullable=False),
)

run_execution_control = _tenant_table(
    "run_execution_control",
    _column("run_id", uuid(), nullable=False),
    _column("state", String(32), nullable=False),
    _column("control_epoch", Integer, nullable=False),
    _column("requested_by", String(255)),
    _column("pause_reason", String(64)),
    _column("usage_settlement_status", String(32), nullable=False),
    _column("in_flight_count", Integer, nullable=False),
    _column("pause_requested_at", timestamp()),
    _column("paused_at", timestamp()),
    _column("resumed_at", timestamp()),
    _column("closed_at", timestamp()),
    _column("last_error", String(1000)),
    _column("created_at", timestamp(), nullable=False),
    _column("updated_at", timestamp(), nullable=False),
)

run_control_request = _tenant_table(
    "run_control_request",
    _column("run_id", uuid(), nullable=False),
    _column("operation", String(16), nullable=False),
    _column("idempotency_key", String(200), nullable=False),
    _column("request_sha256", String(64), nullable=False),
    _column("response", json(), nullable=False),
    _column("correlation_id", uuid(), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

run_execution_checkpoint = _tenant_table(
    "run_execution_checkpoint",
    _column("run_id", uuid(), nullable=False),
    _column("control_epoch", Integer, nullable=False),
    _column("interrupted_task_ids", json(), nullable=False),
    _column("completed_task_ids", json(), nullable=False),
    _column("evidence_ids", json(), nullable=False),
    _column("usage_summary", json(), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

run_execution_event = _tenant_table(
    "run_execution_event",
    _column("run_id", uuid(), nullable=False),
    _column("event_type", String(64), nullable=False),
    _column("control_state", String(32), nullable=False),
    _column("control_epoch", Integer, nullable=False),
    _column("data", json(), nullable=False),
    _column("occurred_at", timestamp(), nullable=False),
)

run_limit_amendment = _tenant_table(
    "run_limit_amendment",
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid(), nullable=False),
    _column("amendment_version", Integer, nullable=False),
    _column("dispatch_epoch", Integer, nullable=False),
    _column("control_epoch", Integer, nullable=False),
    _column("matrix_event_id", String(255), nullable=False),
    _column("matrix_payload_sha256", String(64), nullable=False),
    _column("model_calls", Integer, nullable=False),
    _column("input_tokens", Integer, nullable=False),
    _column("output_tokens", Integer, nullable=False),
    _column("reason", String(1000), nullable=False),
    _column("authorized_by", String(255), nullable=False),
    _column("idempotency_key", String(200), nullable=False),
    _column("request_sha256", String(64), nullable=False),
    _column("correlation_id", uuid(), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

run_limit_amendment_replay = _tenant_table(
    "run_limit_amendment_replay",
    _column("amendment_id", uuid(), nullable=False),
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid(), nullable=False),
    _column("matrix_event_id", String(255), nullable=False),
    _column("matrix_payload_sha256", String(64), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

run_canonical_event_recovery = _tenant_table(
    "run_canonical_event_recovery",
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid(), nullable=False),
    _column("dispatch_epoch", Integer, nullable=False),
    _column("control_epoch", Integer, nullable=False),
    _column("matrix_event_id", String(255), nullable=False),
    _column("source_payload_sha256", String(64), nullable=False),
    _column("reason", String(1000), nullable=False),
    _column("authorized_by", String(255), nullable=False),
    _column("idempotency_key", String(200), nullable=False),
    _column("request_sha256", String(64), nullable=False),
    _column("correlation_id", uuid(), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

run_canonical_event_replay = _tenant_table(
    "run_canonical_event_replay",
    _column("recovery_id", uuid(), nullable=False),
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid(), nullable=False),
    _column("matrix_event_id", String(255), nullable=False),
    _column("canonical_payload_sha256", String(64), nullable=False),
    _column("created_at", timestamp(), nullable=False),
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
    # ADR 0004: incremented on every clarification re-dispatch so the task-ready
    # Outbox idempotency key is unique per attempt instead of colliding.
    _column("dispatch_epoch", Integer, nullable=False),
    _column("last_failure_class", String(40)),
    _column("last_error", String(1000)),
    _column("side_effect_started", Boolean, nullable=False),
    _column("created_at", timestamp(), nullable=False),
    _column("updated_at", timestamp(), nullable=False),
)

model_invocation = _tenant_table(
    "model_invocation",
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid(), nullable=False),
    _column("delivery_id", uuid()),
    _column("agent_code", String(120), nullable=False),
    _column("control_epoch", Integer, nullable=False),
    _column("dispatch_epoch", Integer),
    _column("invocation_seq", Integer),
    _column("model", String(255), nullable=False),
    _column("status", String(32), nullable=False),
    _column("delivery_status", String(32), nullable=False),
    _column("upstream_request_id", String(255)),
    _column("request_sha256", String(64), nullable=False),
    _column("prompt_tokens", Integer),
    _column("completion_tokens", Integer),
    _column("cost", Numeric(20, 6)),
    _column("budget_held_amount", Numeric(20, 6), nullable=False),
    _column("started_at", timestamp(), nullable=False),
    _column("submitted_at", timestamp()),
    _column("terminal_seen_at", timestamp()),
    _column("usage_received_at", timestamp()),
    _column("settled_at", timestamp()),
    _column("failure_class", String(64)),
    _column("last_error", String(1000)),
)

task_dependency = _tenant_table(
    "task_dependency",
    _column("task_id", uuid(), nullable=False),
    _column("depends_on_task_id", uuid(), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

agent_plan = _tenant_table(
    "agent_plan",
    _column("run_id", uuid(), nullable=False),
    _column("planning_task_id", uuid(), nullable=False),
    _column("dispatch_epoch", Integer, nullable=False),
    _column("plan_version", Integer, nullable=False),
    _column("evaluation_mode", String(48), nullable=False),
    _column("raw_plan", json(), nullable=False),
    _column("plan_sha256", String(64), nullable=False),
    _column("status", String(32), nullable=False),
    _column("matrix_event_id", String(255)),
    _column("rejection_code", String(64)),
    _column("decision_reason", String(2000)),
    _column("supersedes_plan_id", uuid()),
    _column("created_at", timestamp(), nullable=False),
    _column("decided_at", timestamp()),
)

agent_task_ticket = _tenant_table(
    "agent_task_ticket",
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid(), nullable=False),
    _column("plan_id", uuid(), nullable=False),
    _column("dispatch_epoch", Integer, nullable=False),
    _column("target_agent", String(120), nullable=False),
    _column("ticket_sha256", String(64), nullable=False),
    _column("public_summary", json(), nullable=False),
    _column("usage_baseline", json()),
    _column("status", String(32), nullable=False),
    _column("expires_at", timestamp(), nullable=False),
    _column("created_at", timestamp(), nullable=False),
    _column("delivered_at", timestamp()),
)

task_material_scope = _tenant_table(
    "task_material_scope",
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid(), nullable=False),
    _column("plan_id", uuid(), nullable=False),
    _column("material_id", uuid(), nullable=False),
    _column("analysis_id", uuid(), nullable=False),
    _column("unit_ids", json(), nullable=False),
    _column("unit_refs", json(), nullable=False),
    _column("reason", String(1000), nullable=False),
    _column("required", Boolean, nullable=False),
    _column("scope_sha256", String(64), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

material_read_receipt = _tenant_table(
    "material_read_receipt",
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid(), nullable=False),
    _column("agent_code", String(120), nullable=False),
    _column("purpose", String(500), nullable=False),
    _column("unit_refs", json(), nullable=False),
    _column("parameters_sha256", String(64), nullable=False),
    _column("result_sha256", String(64)),
    _column("status", String(32), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

manager_synthesis = _tenant_table(
    "manager_synthesis",
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid(), nullable=False),
    _column("dispatch_epoch", Integer, nullable=False),
    _column("deterministic_candidate", String(40), nullable=False),
    _column("proposed_recommendation", String(40), nullable=False),
    _column("raw_synthesis", json(), nullable=False),
    _column("synthesis_sha256", String(64), nullable=False),
    _column("status", String(32), nullable=False),
    _column("approval_request_id", uuid()),
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

skill_execution = _tenant_table(
    "skill_execution",
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid(), nullable=False),
    _column("skill_code", String(120), nullable=False),
    _column("skill_version", String(20), nullable=False),
    _column("mode", String(40), nullable=False),
    _column("status", String(40), nullable=False),
    _column("current_step", String(16)),
    _column("revision", Integer, nullable=False),
    _column("checkpoint_object_key", String(1024), nullable=False),
    _column("checkpoint_sha256", String(64), nullable=False),
    _column("idempotency_key", String(200), nullable=False),
    _column("request_sha256", String(64), nullable=False),
    _column("last_error_code", String(80)),
    _column("created_at", timestamp(), nullable=False),
    _column("updated_at", timestamp(), nullable=False),
)

skill_execution_step = _tenant_table(
    "skill_execution_step",
    _column("execution_id", uuid(), nullable=False),
    _column("step_id", String(16), nullable=False),
    _column("attempt", Integer, nullable=False),
    _column("revision", Integer, nullable=False),
    _column("idempotency_key", String(200), nullable=False),
    _column("input_sha256", String(64), nullable=False),
    _column("output_object_key", String(1024), nullable=False),
    _column("output_sha256", String(64), nullable=False),
    _column("status", String(40), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

skill_result = _tenant_table(
    "skill_result",
    _column("execution_id", uuid(), nullable=False),
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid(), nullable=False),
    _column("schema_version", String(20), nullable=False),
    _column("mode", String(40), nullable=False),
    _column("status", String(40), nullable=False),
    _column("object_key", String(1024), nullable=False),
    _column("sha256", String(64), nullable=False),
    _column("size_bytes", Integer, nullable=False),
    _column("summary", json(), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

skill_result_evidence = Table(
    "skill_result_evidence",
    metadata,
    _column("tenant_id", uuid(), nullable=False, index=True),
    _column("skill_result_id", uuid(), nullable=False),
    _column("evidence_id", uuid(), nullable=False),
    _column("external_evidence_id", String(255), nullable=False),
    _column("origin", String(40), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

agent_report_artifact = _tenant_table(
    "agent_report_artifact",
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid(), nullable=False),
    _column("agent_code", String(120), nullable=False),
    _column("report_kind", String(32), nullable=False),
    _column("revision", Integer, nullable=False),
    _column("object_key", String(1024), nullable=False),
    _column("sha256", String(64), nullable=False),
    _column("mime_type", String(255), nullable=False),
    _column("status", String(32), nullable=False),
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

evidence_source_locator = _tenant_table(
    "evidence_source_locator",
    _column("evidence_id", uuid(), nullable=False),
    _column("ordinal", Integer, nullable=False),
    _column("source_kind", String(32), nullable=False),
    _column("canonical_url", String(2048)),
    _column("title", String(1000), nullable=False),
    _column("publisher", String(500)),
    _column("published_at", timestamp()),
    _column("fetched_at", timestamp(), nullable=False),
    _column("locator", json(), nullable=False),
    _column("region", String(100)),
    _column("independence_group", String(500), nullable=False),
    _column("content_sha256", String(64), nullable=False),
    _column("screenshot_sha256", String(64)),
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
    _column("contract_version", String(20), nullable=False, default="1.0"),
    _column("rule_ids", json(), nullable=False, default=list),
    _column("referenced_evidence_ids", json(), nullable=False, default=list),
    _column("score_components", json(), nullable=False, default=dict),
    _column("flags", json(), nullable=False, default=list),
    _column("source_finding_sha256", String(64)),
    _column("audit_round", Integer),
    _column("remediation_target", json()),
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

report_claim_citation = _tenant_table(
    "report_claim_citation",
    _column("report_id", uuid(), nullable=False),
    _column("claim_id", String(160), nullable=False),
    _column("citation_id", String(160), nullable=False),
    _column("evidence_id", uuid(), nullable=False),
    _column("source_locator_id", uuid()),
    _column("support_role", String(32), nullable=False),
    _column("audit_status", String(32), nullable=False),
    _column("label", Integer, nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

public_demo_disclosure_acceptance = _tenant_table(
    "public_demo_disclosure_acceptance",
    _column("project_id", uuid(), nullable=False),
    _column("product_version_id", uuid(), nullable=False),
    _column("run_id", uuid()),
    _column("actor_id", String(255), nullable=False),
    _column("policy_version", String(120), nullable=False),
    _column("accepted_at", timestamp(), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

public_demo_share = _tenant_table(
    "public_demo_share",
    _column("run_id", uuid(), nullable=False),
    _column("report_id", uuid(), nullable=False),
    _column("token_sha256", String(64), nullable=False),
    _column("status", String(32), nullable=False),
    _column("include_agent_reports", Boolean, nullable=False),
    _column("include_evidence", Boolean, nullable=False),
    _column("created_at", timestamp(), nullable=False),
    _column("revoked_at", timestamp()),
)

report_export_artifact = _tenant_table(
    "report_export_artifact",
    _column("run_id", uuid(), nullable=False),
    _column("report_id", uuid(), nullable=False),
    _column("agent_code", String(120)),
    _column("kind", String(32), nullable=False),
    _column("view", String(16), nullable=False),
    _column("locale", String(20), nullable=False),
    _column("include_evidence", Boolean, nullable=False),
    _column("renderer_version", String(80), nullable=False),
    _column("source_sha256", String(64), nullable=False),
    _column("idempotency_key", String(255), nullable=False),
    _column("request_sha256", String(64), nullable=False),
    _column("status", String(32), nullable=False),
    _column("object_key", String(1024)),
    _column("sha256", String(64)),
    _column("size_bytes", Integer),
    _column("error_code", String(120)),
    _column("created_at", timestamp(), nullable=False),
    _column("completed_at", timestamp()),
)

project_dossier_snapshot = _tenant_table(
    "project_dossier_snapshot",
    _column("project_id", uuid(), nullable=False),
    _column("product_version_id", uuid(), nullable=False),
    _column("run_id", uuid(), nullable=False),
    _column("decision_id", uuid(), nullable=False),
    _column("report_id", uuid(), nullable=False),
    _column("schema_version", String(20), nullable=False),
    _column("document", json(), nullable=False),
    _column("sha256", String(64), nullable=False),
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
    _column("dispatch_epoch", Integer, nullable=False, default=0),
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

agentteams_task_delivery = _tenant_table(
    "agentteams_task_delivery",
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid(), nullable=False),
    _column("dispatch_epoch", Integer, nullable=False),
    _column("agent_code", String(120), nullable=False),
    _column("worker_name", String(160), nullable=False),
    _column("room_id", String(255), nullable=False),
    _column("assignment_event_id", String(255), nullable=False),
    _column("status", String(32), nullable=False),
    _column("max_model_calls", Integer, nullable=False),
    _column("accounting_mode", String(32), nullable=False),
    _column("usage_baseline", json()),
    _column("delivered_at", timestamp(), nullable=False),
    _column("deadline_at", timestamp(), nullable=False),
    _column("completed_at", timestamp()),
)

physical_worker_execution_lease = _tenant_table(
    "physical_worker_execution_lease",
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid(), nullable=False),
    _column("delivery_id", uuid(), nullable=False),
    _column("dispatch_epoch", Integer, nullable=False),
    _column("control_epoch", Integer, nullable=False),
    _column("agent_code", String(120), nullable=False),
    _column("worker_name", String(160), nullable=False),
    _column("state", String(32), nullable=False),
    _column("credential_sha256", String(64), nullable=False),
    _column("credential_expires_at", timestamp(), nullable=False),
    _column("prepared_at", timestamp(), nullable=False),
    _column("activated_at", timestamp()),
    _column("draining_at", timestamp()),
    _column("released_at", timestamp()),
    _column("last_error", String(1000)),
    _column("created_at", timestamp(), nullable=False),
    _column("updated_at", timestamp(), nullable=False),
)

model_usage_reconciliation = _tenant_table(
    "model_usage_reconciliation",
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid(), nullable=False),
    _column("delivery_id", uuid(), nullable=False),
    _column("state", String(32), nullable=False),
    _column("invocation_ids", json(), nullable=False),
    _column("gateway_usage", json(), nullable=False),
    _column("copaw_baseline", json()),
    _column("copaw_terminal", json()),
    _column("usage_record_ids", json(), nullable=False),
    _column("difference_reason", String(1000)),
    _column("reconciled_at", timestamp()),
    _column("posted_at", timestamp()),
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

information_request = _tenant_table(
    "information_request",
    _column("run_id", uuid(), nullable=False),
    _column("task_id", uuid(), nullable=False),
    _column("agent_identity_ref", String(200), nullable=False),
    _column("profile_field", String(120), nullable=False),
    _column("question", String(1000), nullable=False),
    _column("why_blocking", String(1000), nullable=False),
    _column("impact_dimension", String(64), nullable=False),
    _column("answer_kind", String(32), nullable=False),
    _column("status", String(32), nullable=False),
    _column("answered_at", timestamp()),
    _column("created_at", timestamp(), nullable=False),
    _column("updated_at", timestamp(), nullable=False),
)

information_request_answer = _tenant_table(
    "information_request_answer",
    _column("information_request_id", uuid(), nullable=False),
    _column("run_id", uuid(), nullable=False),
    _column("answer_text", String(4000), nullable=False),
    _column("answer_sha256", String(64), nullable=False),
    _column("profile_revision", Integer),
    _column("evidence_id", uuid()),
    _column("supersedes_id", uuid()),
    _column("answered_by", String(255), nullable=False),
    _column("correlation_id", String(200), nullable=False),
    _column("idempotency_key", String(200), nullable=False),
    _column("submission_sha256", String(64), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)

clarification_impact_assessment = _tenant_table(
    "clarification_impact_assessment",
    _column("run_id", uuid(), nullable=False),
    _column("assessed_by_agent_ref", String(200), nullable=False),
    _column("answered_request_ids", json(), nullable=False),
    _column("affected_task_ids", json(), nullable=False),
    _column("unaffected_task_ids", json(), nullable=False),
    _column("rationale", String(2000), nullable=False),
    _column("created_at", timestamp(), nullable=False),
)


TENANT_SCOPED_TABLES: tuple[str, ...] = tuple(table.name for table in metadata.sorted_tables if "tenant_id" in table.c)

__all__ = ["TENANT_SCOPED_TABLES", "metadata"]
