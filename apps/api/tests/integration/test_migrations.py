"""Migration ordering and repeatability checks."""

from __future__ import annotations

import ast
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text


def _config(database) -> Config:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database.url.render_as_string(hide_password=False))
    return config


def test_revision_identifiers_fit_alembic_version_column() -> None:
    versions = Path(__file__).resolve().parents[2] / "migrations" / "versions"
    revisions: dict[str, str] = {}
    for path in versions.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if any(isinstance(target, ast.Name) and target.id == "revision" for target in node.targets):
                revisions[path.name] = ast.literal_eval(node.value)
    assert revisions
    assert {name: revision for name, revision in revisions.items() if len(revision) > 32} == {}


def test_report_v22_revision_extends_material_routing_head() -> None:
    path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0031_report_v22.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assignments = {
        target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}
    }
    assert assignments == {
        "revision": "0031_report_v22",
        "down_revision": "0030_material_routing_v2",
    }


def test_failure_class_width_revision_extends_report_v22_head() -> None:
    path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0032_failure_class_width.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assignments = {
        target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}
    }
    assert assignments == {
        "revision": "0032_failure_class_width",
        "down_revision": "0031_report_v22",
    }


def test_report_revision_width_extends_failure_class_width_head() -> None:
    path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0033_report_revision_width.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assignments = {
        target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}
    }
    assert assignments == {
        "revision": "0033_report_revision_width",
        "down_revision": "0032_failure_class_width",
    }


def test_run_limit_amendment_extends_report_revision_width_head() -> None:
    path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0034_run_limit_amendment.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assignments = {
        target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}
    }
    assert assignments == {
        "revision": "0034_run_limit_amendment",
        "down_revision": "0033_report_revision_width",
    }


def test_canonical_event_recovery_extends_limit_amendment_head() -> None:
    path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0035_canonical_event_recovery.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assignments = {
        target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}
    }
    assert assignments == {
        "revision": "0035_canonical_event_recovery",
        "down_revision": "0034_run_limit_amendment",
    }


def test_handoff_dispatch_epoch_extends_canonical_recovery_head() -> None:
    path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0036_handoff_dispatch_epoch.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assignments = {
        target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}
    }
    assert assignments == {
        "revision": "0036_handoff_dispatch_epoch",
        "down_revision": "0035_canonical_event_recovery",
    }


def test_migrations_reach_head_and_repeat_without_new_rows(database) -> None:
    config = _config(database)
    command.upgrade(config, "head")
    first = database.connect()
    try:
        version_before = first.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        first.close()
    command.upgrade(config, "head")
    second = database.connect()
    try:
        version_after = second.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        tables = {row[0] for row in second.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))}
        user_validation_versions = set(second.execute(text(
            "SELECT version, manifest_sha256 FROM skill_version "
            "WHERE skill_code = 'user-validation-designer'"
        )).tuples())
        rls_tables = {
            row[0] for row in second.execute(text("SELECT relname FROM pg_class WHERE relrowsecurity = true"))
        }
        delivery_ledger_indexes = {
            row[0]
            for row in second.execute(text(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
                "AND indexname IN ('uq_physical_worker_open_lease', "
                "'uq_physical_worker_credential_digest', 'uq_model_invocation_delivery_seq')"
            ))
        }
        gap_priority_constraint = second.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'intake_gap_question'::regclass "
                "AND conname = 'intake_gap_question_priority_check'"
            )
        ).scalar_one()
        task_failure_class_width = second.execute(
            text(
                "SELECT character_maximum_length FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'task' AND column_name = 'last_failure_class'"
            )
        ).scalar_one()
        report_revision_constraint = second.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'agent_report_artifact'::regclass "
                "AND conname = 'agent_report_artifact_revision_check'"
            )
        ).scalar_one()
    finally:
        second.close()
    assert version_before == version_after == "0035_canonical_event_recovery"
    assert task_failure_class_width == 120
    assert "revision >= 0" in report_revision_constraint
    assert "priority <= 6" in gap_priority_constraint
    assert delivery_ledger_indexes == {
        "uq_physical_worker_open_lease",
        "uq_physical_worker_credential_digest",
        "uq_model_invocation_delivery_seq",
    }
    assert {
        "project",
        "product_version",
        "evaluation_run",
        "stage",
        "task",
        "evidence",
        "finding",
        "decision",
        "memory_candidate",
        "memory_item",
        "rag_retrieval",
        "agentteams_run_binding",
        "matrix_event_receipt",
        "information_request",
        "information_request_answer",
        "clarification_impact_assessment",
        "agentteams_task_delivery",
        "user_validation_script",
        "user_evidence_metadata",
        "skill_execution",
        "skill_execution_step",
        "skill_result",
        "skill_result_evidence",
        "agent_plan",
        "manager_synthesis",
        "agent_task_ticket",
        "requirement_brief",
        "supervisor_chat_message",
        "requirement_change",
        "project_dossier_snapshot",
        "agent_report_artifact",
        "run_execution_control",
        "run_control_request",
        "run_execution_checkpoint",
        "run_execution_event",
        "model_invocation",
        "physical_worker_execution_lease",
        "model_usage_reconciliation",
        "run_conversation_message",
        "material_analysis",
        "material_unit",
        "material_selection",
        "material_selection_item",
        "task_material_scope",
        "material_read_receipt",
        "evidence_source_locator",
        "report_claim_citation",
        "public_demo_disclosure_acceptance",
        "public_demo_share",
        "report_export_artifact",
        "run_limit_amendment",
        "run_limit_amendment_replay",
        "run_canonical_event_recovery",
        "run_canonical_event_replay",
    } <= tables
    assert {
        "agent_plan",
        "manager_synthesis",
        "agent_task_ticket",
        "requirement_brief",
        "supervisor_chat_message",
        "requirement_change",
        "project_dossier_snapshot",
        "agent_report_artifact",
        "run_execution_control",
        "run_control_request",
        "run_execution_checkpoint",
        "run_execution_event",
        "model_invocation",
        "physical_worker_execution_lease",
        "model_usage_reconciliation",
        "run_conversation_message",
        "material_analysis",
        "material_unit",
        "material_selection",
        "material_selection_item",
        "task_material_scope",
        "material_read_receipt",
        "evidence_source_locator",
        "report_claim_citation",
        "public_demo_disclosure_acceptance",
        "public_demo_share",
        "report_export_artifact",
        "run_limit_amendment",
        "run_limit_amendment_replay",
        "run_canonical_event_recovery",
        "run_canonical_event_replay",
    } <= rls_tables
    assert user_validation_versions == {
        ("1.0.4", "1a206a37958f1788abfd0605746816bffc8717993411f4cb663648ed843b5b2b"),
        ("1.0.5", "0964927ad124e301386b21626ef59f2f161230c55acc534b980b2a267d3ad285"),
        ("1.1.0", "2fd4d2d965d9277cc484560f989c402e9ddbbf8e65e4b5c3032c7f56071174e6"),
    }
