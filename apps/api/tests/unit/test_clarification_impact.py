"""ADR 0004: the Manager's clarification impact-scoping rule.

The point of the rule is that answering a question must not silently discard
paid model work that is still valid.  In this first version the only way new
facts enter a running Run is an Agent-initiated question, so exactly the Tasks
that parked themselves resume.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from uuid import UUID, uuid4

from launchscope_api.modules.evaluation.clarification_application import ClarificationApplication


class _FakeResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeResult:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _FakeSession:
    """Returns the durable Task rows the impact rule reads."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def execute(self, _statement: object) -> _FakeResult:
        return _FakeResult(self._rows)


def _assess(rows: list[dict[str, object]], fields: set[str]) -> tuple[list[UUID], list[UUID]]:
    return ClarificationApplication._assess_impact(_FakeSession(rows), uuid4(), uuid4(), fields)


def test_only_the_parked_task_resumes() -> None:
    asking, sibling, done = uuid4(), uuid4(), uuid4()
    rows = [
        {"id": asking, "status": "NEEDS_INPUT"},
        {"id": sibling, "status": "READY"},
        {"id": done, "status": "SUCCEEDED"},
    ]

    affected, unaffected = _assess(rows, {"payer"})

    assert affected == [asking]
    assert done in unaffected
    assert sibling in unaffected


def test_succeeded_work_is_never_discarded_even_for_a_cross_cutting_field() -> None:
    """ADR 0004 promises completed Tasks keep their result and are not re-run."""

    asking, done_same, done_other = uuid4(), uuid4(), uuid4()
    rows = [
        {"id": asking, "status": "NEEDS_INPUT"},
        {"id": done_same, "status": "SUCCEEDED"},
        {"id": done_other, "status": "SUCCEEDED"},
    ]

    affected, unaffected = _assess(rows, {"problem"})

    assert affected == [asking]
    assert done_same in unaffected
    assert done_other in unaffected


def test_every_parked_task_resumes_even_when_answered_across_submissions() -> None:
    """A Task answered in an earlier partial submission must not be stranded."""

    answered_earlier, answered_now, leader = uuid4(), uuid4(), uuid4()
    rows = [
        # Both are still NEEDS_INPUT because the Run only resumes once nothing is OPEN.
        {"id": answered_earlier, "status": "NEEDS_INPUT"},
        {"id": answered_now, "status": "NEEDS_INPUT"},
        {"id": leader, "status": "SUCCEEDED"},
    ]

    affected, unaffected = _assess(rows, {"region"})

    assert sorted(affected, key=str) == sorted([answered_earlier, answered_now], key=str)
    assert unaffected == [leader]


def test_blocked_downstream_tasks_keep_waiting_for_their_dependencies() -> None:
    asking, auditor = uuid4(), uuid4()
    rows = [
        {"id": asking, "status": "NEEDS_INPUT"},
        {"id": auditor, "status": "BLOCKED"},
    ]

    affected, unaffected = _assess(rows, {"payer"})

    assert affected == [asking]
    assert unaffected == [auditor]


def test_every_task_is_classified_exactly_once() -> None:
    asking, other = uuid4(), uuid4()
    rows = [
        {"id": asking, "status": "NEEDS_INPUT"},
        {"id": other, "status": "SUCCEEDED"},
    ]

    affected, unaffected = _assess(rows, {"region"})

    assert sorted(affected + unaffected, key=str) == sorted([asking, other], key=str)
    assert not set(affected) & set(unaffected)


def test_impact_assessment_is_not_attributed_to_an_agent_that_never_ran() -> None:
    """The impact rule is a control-plane decision.

    Recording it as an Agent ref such as ``evaluation-manager@1.0`` would claim a
    provenance that no dispatched Task and no handoff can support.
    """
    source = Path(inspect.getsourcefile(ClarificationApplication) or "").read_text(encoding="utf-8")

    assert 'assessed_by_agent_ref="launchscope-control-plane"' in source
    assert "evaluation-manager@" not in source


def test_clarification_field_limits_agree_across_every_layer() -> None:
    """The Agent contract must not accept text the database would refuse.

    Before ADR 0004 was fully wired the AgentHandoff contract allowed a 2000
    character question while ``information_request.question`` was varchar(1000),
    and the REST answer limit was 2000 against a varchar(4000) column.  Each
    layer now derives from the domain constants, so this test fails if any of
    them is edited in isolation.
    """
    from launchscope_api.infrastructure.db.schema import information_request, information_request_answer
    from launchscope_domain import (
        MAX_CLARIFICATION_ANSWER_CHARS,
        MAX_CLARIFICATION_QUESTION_CHARS,
        MAX_CLARIFICATION_REASON_CHARS,
        MAX_IMPACT_DIMENSION_CHARS,
        MAX_PROFILE_FIELD_CHARS,
    )
    from launchscope_orchestrator.agentteams_bridge import InformationRequestV1

    contract = {name: field for name, field in InformationRequestV1.model_fields.items()}

    def _contract_max(name: str) -> int:
        for meta in contract[name].metadata:
            if getattr(meta, "max_length", None) is not None:
                return int(meta.max_length)
        raise AssertionError(f"{name} declares no max_length")

    def _column_max(table: object, name: str) -> int:
        return int(table.c[name].type.length)  # type: ignore[attr-defined]

    assert _contract_max("field") == MAX_PROFILE_FIELD_CHARS
    assert _contract_max("question") == MAX_CLARIFICATION_QUESTION_CHARS
    assert _contract_max("why_blocked") == MAX_CLARIFICATION_REASON_CHARS
    assert _contract_max("dimension") == MAX_IMPACT_DIMENSION_CHARS

    assert _column_max(information_request, "profile_field") == MAX_PROFILE_FIELD_CHARS
    assert _column_max(information_request, "question") == MAX_CLARIFICATION_QUESTION_CHARS
    assert _column_max(information_request, "why_blocking") == MAX_CLARIFICATION_REASON_CHARS
    assert _column_max(information_request, "impact_dimension") == MAX_IMPACT_DIMENSION_CHARS
    assert _column_max(information_request_answer, "answer_text") == MAX_CLARIFICATION_ANSWER_CHARS

    from launchscope_api.modules.evaluation.clarification_application import _MAX_ANSWER_CHARS

    assert _MAX_ANSWER_CHARS == MAX_CLARIFICATION_ANSWER_CHARS

    # The REST model is the outermost layer: an isolated edit there would let a
    # request in that the service layer and the column would then reject.
    from launchscope_api.modules.experience.api import ClarificationAnswerItem

    rest_answer = ClarificationAnswerItem.model_fields["answer"]
    rest_max = next(
        int(meta.max_length)
        for meta in rest_answer.metadata
        if getattr(meta, "max_length", None) is not None
    )
    assert rest_max == MAX_CLARIFICATION_ANSWER_CHARS
