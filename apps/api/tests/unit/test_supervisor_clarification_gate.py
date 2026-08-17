from launchscope_api.modules.supervisor.planning_application import _explicit_clarification_gate


def test_confirmed_validation_task_creates_bounded_region_gate() -> None:
    request = _explicit_clarification_gate({
        "validation_tasks": [{
            "task_key": "identify_required_clarification_for_region_payment_login",
            "description": (
                "The target is not finally determined. This fact must be clarified before this Task can proceed. "
                "Ask exactly one necessary question: Which target market should be evaluated, US or EU?"
            ),
            "expected_observable_outcome": "The Run enters WAITING_FOR_USER before browser/search.",
        }],
    })

    assert request is not None
    assert request.field == "target_region"
    assert request.question == "Which target market should be evaluated, US or EU?"
    assert request.dimension == "USER_USAGE"


def test_ordinary_validation_task_does_not_create_clarification_gate() -> None:
    assert _explicit_clarification_gate({
        "validation_tasks": [{
            "task_key": "inspect_public_site",
            "user_action": "Inspect the public product page.",
            "expected_observable_outcome": "A source-bound note is recorded.",
        }],
    }) is None
