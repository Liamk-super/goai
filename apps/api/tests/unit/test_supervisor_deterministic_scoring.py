from launchscope_api.modules.supervisor.completion_application import DeterministicScoringEngine


def test_auditor_is_not_counted_as_a_missing_domain_agent() -> None:
    planned = [
        {"agent": agent, "required": True, "status": "SUCCEEDED"}
        for agent in (
            "user-evidence",
            "product-engineering",
            "business-investment",
            "evidence-auditor",
        )
    ]
    audited = [
        {
            "audit_decision": "ACCEPTED",
            "finding": {"agent_code": agent, "score_input": 3},
        }
        for agent in ("user-evidence", "product-engineering", "business-investment")
    ]
    profile = {
        "weights": {
            "user_value": 0.25,
            "product_capability": 0.25,
            "investment_potential": 0.25,
            "evidence_quality": 0.25,
        },
        "thresholds": {"PROCEED": 80, "VALIDATE_FURTHER": 60, "ADJUST": 40},
        "recommendation_caps": {
            "missing_optional_agent": "VALIDATE_FURTHER",
            "unresolved_conflict": "ADJUST",
            "low_coverage": "ADJUST",
        },
        "coverage_rules": {"proceed_minimum": 1.0},
    }

    result = DeterministicScoringEngine().score(
        profile,
        audited,
        planned,
        unresolved_conflicts=False,
    )

    assert result.coverage == 1.0
    assert result.missing_agents == ()
    assert result.caps_applied == ()
