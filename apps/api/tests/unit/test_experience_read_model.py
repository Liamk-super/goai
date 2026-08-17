from datetime import UTC, datetime

from launchscope_api.modules.experience.read_model import ExperienceReadApplication


def test_action_links_keep_the_dimension_named_by_each_action() -> None:
    dimensions = {
        name: {"grade": "INSUFFICIENT_EVIDENCE", "supporting_evidence": [name.lower()]}
        for name in ("PRODUCT_IMPLEMENTATION", "USER_USAGE", "BUSINESS_INVESTMENT", "GEO_POLICY_TREND")
    }

    links = ExperienceReadApplication._action_links([
        "Collect stronger authorized evidence for Product Implementation",
        "Collect stronger authorized evidence for User Usage",
        "Collect stronger authorized evidence for Business Investment",
    ], dimensions)

    assert [item["dimension"] for item in links] == [
        "PRODUCT_IMPLEMENTATION", "USER_USAGE", "BUSINESS_INVESTMENT",
    ]
    assert links[0]["evidence_ids"] == ["product_implementation"]


def test_completed_run_projects_every_persisted_stage_as_completed() -> None:
    completed_at = datetime(2026, 8, 13, 6, 45, tzinfo=UTC)

    projected = ExperienceReadApplication._stage_projection(
        {
            "code": "DOMAIN_REVIEW",
            "ordinal": 2,
            "status": "RUNNING",
            "started_at": completed_at,
            "completed_at": None,
        },
        {"status": "COMPLETED", "updated_at": completed_at},
    )

    assert projected["status"] == "COMPLETED"
    assert projected["completed_at"] == "2026-08-13T06:45:00Z"
