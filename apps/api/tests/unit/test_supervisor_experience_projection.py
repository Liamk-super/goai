from datetime import UTC, datetime
from uuid import uuid4

from launchscope_api.modules.experience.read_model import ExperienceReadApplication


def test_generation_v4_progress_is_mapped_only_from_durable_state() -> None:
    assert ExperienceReadApplication._experience_stage("RUNNING", "LEADER_PLANNING", {}) == {
        "ordinal": 1,
        "code": "UNDERSTANDING",
        "label": "正在了解项目",
        "exception": None,
        "exception_label": None,
    }
    assert ExperienceReadApplication._experience_stage("RUNNING", "DOMAIN_REVIEW", {})["ordinal"] == 2
    assert ExperienceReadApplication._experience_stage("RUNNING", "SUPERVISOR_SYNTHESIS", {})["ordinal"] == 3
    assert ExperienceReadApplication._experience_stage("COMPLETED", "COMPLETED", {})["ordinal"] == 4


def test_queued_dispatch_is_not_presented_as_active_reasoning() -> None:
    assert ExperienceReadApplication._experience_stage(
        "RUNNING", "LEADER_PLANNING", {"dispatch_pending": True}
    ) == {
        "ordinal": 1,
        "code": "UNDERSTANDING",
        "label": "等待执行服务",
        "exception": None,
        "exception_label": None,
    }


def test_run_projection_allows_unrequested_execution_control_timestamps() -> None:
    result = ExperienceReadApplication._run_projection(
        {
            "id": uuid4(),
            "project_id": uuid4(),
            "product_version_id": uuid4(),
            "status": "NEEDS_ATTENTION",
            "standard_version": "1.0",
            "correlation_id": uuid4(),
            "current_stage": "LEADER_PLANNING",
            "attention_reason": "runtime unavailable",
            "updated_at": datetime.now(UTC),
            "state_flags": {"architecture_generation": "supervisor-1p4-v1"},
            "pause_requested_at": None,
            "paused_at": None,
            "resumed_at": None,
        },
        "event.current",
    )

    assert result["status"] == "NEEDS_ATTENTION"
    assert result["execution_control"]["pause_requested_at"] is None


def test_report_v22_generations_keep_the_supervisor_experience() -> None:
    for generation in ("supervisor-1p4-material-routing-v2", "supervisor-1p4-report-v22"):
        result = ExperienceReadApplication._run_projection(
            {
                "id": uuid4(),
                "project_id": uuid4(),
                "product_version_id": uuid4(),
                "status": "NEEDS_ATTENTION",
                "standard_version": "1.0",
                "correlation_id": uuid4(),
                "current_stage": "LEADER_PLANNING",
                "attention_reason": "runtime unavailable",
                "updated_at": datetime.now(UTC),
                "state_flags": {"architecture_generation": generation},
                "pause_requested_at": None,
                "paused_at": None,
                "resumed_at": None,
            },
            "event.current",
        )

        assert result["architecture_generation"] == generation
        assert result["ui_mode"] == "SUPERVISOR_1P4"


def test_user_facing_exceptions_are_derived_from_persisted_run_flags() -> None:
    needs_input = ExperienceReadApplication._experience_stage("WAITING_FOR_USER", "DOMAIN_REVIEW", {})
    assert needs_input["exception"] == "NEEDS_INPUT"
    assert needs_input["exception_label"] == "需要补充信息"

    needs_confirmation = ExperienceReadApplication._experience_stage(
        "RUNNING", "DOMAIN_REVIEW", {"waiting_for_approval": True}
    )
    assert needs_confirmation["exception"] == "NEEDS_CONFIRMATION"
    assert needs_confirmation["exception_label"] == "需要确认"

    needs_attention = ExperienceReadApplication._experience_stage("NEEDS_ATTENTION", "NEEDS_ATTENTION", {})
    assert needs_attention["exception"] == "NEEDS_CONFIRMATION"


def test_pre_manifest_generation_comes_only_from_the_persisted_run_marker() -> None:
    assert ExperienceReadApplication._architecture_generation(
        {
            "status": "WAITING_FOR_USER",
            "state_flags": {"architecture_generation": "supervisor-1p4-v1"},
        }
    ) == "supervisor-1p4-v1"
    assert ExperienceReadApplication._architecture_generation(
        {"status": "PLANNED", "state_flags": {}}
    ) == "legacy-1p5"


def test_generation_v4_layered_report_keeps_process_details_separate() -> None:
    score = {
        "score": 71.5,
        "coverage": 0.6667,
        "recommendation": "VALIDATE_FURTHER",
        "dimension_scores": {"user": 76.0, "evidence_quality": 80.0},
        "caps_applied": ["low_coverage:VALIDATE_FURTHER"],
        "missing_agents": ["business-investment"],
    }
    synthesis = {
        "raw_synthesis": {
            "summary": "建议先验证付费意愿。",
            "actions": ["完成 12 次目标用户访谈"],
            "cross_domain_analysis": ["用户痛点与产品交付能力匹配。"],
            "risks": ["商业证据仍然不足。"],
            "conflicts": ["价格敏感度样本存在冲突。"],
            "citations": [{"kind": "FINDING", "ref": "finding:1"}],
            "version_changes": {"improved": [], "unchanged": [], "new_risks": []},
            "decision_conflict": False,
        },
        "status": "ACCEPTED",
    }

    result = ExperienceReadApplication._layered_report(score, synthesis)

    assert result is not None
    assert result["coverage"] == 0.6667
    assert result["confidence"] == 0.8
    assert result["largest_opportunity"] == "用户痛点与产品交付能力匹配。"
    assert result["largest_risk"] == "商业证据仍然不足。"
    assert result["information_gaps"] == ["business-investment", "价格敏感度样本存在冲突。"]


def test_legacy_report_does_not_invent_a_supervisor_layer() -> None:
    assert ExperienceReadApplication._layered_report(
        {"USER_USAGE": "MODERATE"}, None
    ) is None
