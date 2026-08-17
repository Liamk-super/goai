"""ADR 0004: the v2 Agent identity generation and the NEEDS_INPUT handoff."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from launchscope_api.modules.evaluation.dispatch_application import DispatchApplication
from launchscope_orchestrator.agentteams_bridge import (
    AgentHandoffV1,
    AgentTeamsBridge,
    BridgePolicyError,
    SupersededHandoffError,
)
from launchscope_orchestrator.manifest_loader import AGENT_CODES, AgentContractError, AgentManifestLoader

_SPECIALISTS_THAT_MAY_ASK = {
    "product-engineering",
    "user-evidence",
    "business-investment",
    "geo-policy-trend",
}


def _handoff(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": "1.1",
        "tenant_id": str(uuid4()),
        "run_id": str(uuid4()),
        "task_id": str(uuid4()),
        "agent_code": "business-investment",
        "status": "NEEDS_INPUT",
        "dimension": "BUSINESS_INVESTMENT",
        "claims": [],
        "evidence_refs": [],
        "risk": "MEDIUM",
        "confidence": 0.4,
        "needs_human_approval": False,
        "next_action": "Ask the user who pays before grading the business dimension",
        "information_requests": [
            {
                "field": "payer",
                "question": "Who actually pays for this product?",
                "why_blocked": "No durable evidence identifies the paying party",
                "dimension": "BUSINESS_INVESTMENT",
            }
        ],
    }
    body.update(overrides)
    return body


def test_v1_identities_remain_loadable_and_unchanged() -> None:
    contracts = AgentManifestLoader().load_all()
    assert {contract.code for contract in contracts} == AGENT_CODES
    for contract in contracts:
        assert contract.version == "1.0"
        assert "information_request" not in contract.outputs


def test_v2_identities_load_and_expose_the_clarification_output() -> None:
    contracts = AgentManifestLoader().load_all("v2")
    assert {contract.code for contract in contracts} == AGENT_CODES
    by_code = {contract.code: contract for contract in contracts}
    for code in _SPECIALISTS_THAT_MAY_ASK:
        assert "information_request" in by_code[code].outputs
    assert "clarification_impact_assessment" in by_code["evaluation-manager"].outputs
    # The auditor requests more evidence through its audit result, not a user question.
    assert "information_request" not in by_code["evidence-auditor"].outputs


def test_v2_does_not_widen_skill_or_tool_authority() -> None:
    v1 = {contract.code: contract for contract in AgentManifestLoader().load_all()}
    v2 = {contract.code: contract for contract in AgentManifestLoader().load_all("v2")}
    # v2 corrects stale tool IDs but must never grant a broader capability class.
    equivalent = {
        "public-research.get.v1": "public-research-search.v1",
        "browser.read.v1": "browser-audit.v1",
        # v1 omitted the mandatory read-only context tool every task already receives.
        "repository.read.v1": None,
    }
    for code in AGENT_CODES:
        assert v1[code].allowed_skills == v2[code].allowed_skills
        assert v1[code].prohibited_actions == v2[code].prohibited_actions
        assert v1[code].risk_boundaries == v2[code].risk_boundaries
        expected = {equivalent.get(tool, tool) for tool in v1[code].allowed_tools}
        expected.discard(None)
        expected.add("launchscope-context.get.v1")
        assert set(v2[code].allowed_tools) <= expected, code


def test_v2_tool_ids_match_the_tools_dispatch_actually_grants() -> None:
    """v1 named a research tool ID (`public-research.get.v1`) that no MCP server serves,
    so `permits_tools` would reject the tools dispatch grants. v2 corrects the ID to the
    registered one without widening each agent's authority. See ADR 0004."""
    v2 = {contract.code: contract for contract in AgentManifestLoader().load_all("v2")}
    for code in AGENT_CODES:
        granted = tuple(DispatchApplication._tools_for(code))
        assert v2[code].permits_tools(granted), code


def test_unknown_generation_is_refused() -> None:
    with pytest.raises(AgentContractError, match="unknown Agent contract generation"):
        AgentManifestLoader().load_all("v7")


def test_needs_input_handoff_is_accepted_with_requests() -> None:
    handoff = AgentHandoffV1.model_validate(_handoff())
    assert handoff.status == "NEEDS_INPUT"
    assert handoff.information_requests[0].field == "payer"


def test_needs_input_requires_at_least_one_request() -> None:
    with pytest.raises(ValidationError, match="at least one information_request"):
        AgentHandoffV1.model_validate(_handoff(information_requests=[]))


def test_needs_input_is_not_a_failure() -> None:
    with pytest.raises(ValidationError, match="omit failure_class"):
        AgentHandoffV1.model_validate(_handoff(failure_class="BUSINESS"))


def test_requests_are_rejected_on_every_other_status() -> None:
    with pytest.raises(ValidationError, match="only valid when status is NEEDS_INPUT"):
        AgentHandoffV1.model_validate(_handoff(status="BLOCKED", failure_class=None))


def test_needs_input_requires_the_new_schema_minor() -> None:
    with pytest.raises(ValidationError, match="schema_version 1.1"):
        AgentHandoffV1.model_validate(_handoff(schema_version="1.0"))


def test_duplicate_request_fields_are_refused() -> None:
    duplicated = _handoff()
    requests = list(duplicated["information_requests"])  # type: ignore[arg-type]
    duplicated["information_requests"] = requests + requests
    with pytest.raises(ValidationError, match="must not repeat the same field"):
        AgentHandoffV1.model_validate(duplicated)


def test_a_1_0_consumer_still_validates_an_unchanged_success_handoff() -> None:
    evidence_id = str(uuid4())
    handoff = AgentHandoffV1.model_validate(
        _handoff(
            schema_version="1.0",
            status="SUCCEEDED",
            information_requests=[],
            evidence_refs=[evidence_id],
            claims=[
                {
                    "statement": "The paying party is the enterprise buyer",
                    "evidence_ids": [evidence_id],
                    "hypothesis": False,
                }
            ],
        )
    )
    assert handoff.schema_version == "1.0"
    assert handoff.information_requests == []


class _Directory:
    def agent_for_mxid(self, mxid: str) -> str | None:
        return "business-investment" if mxid == "@business-investment:launchscope" else None


def _matrix_event(handoff: dict[str, object]) -> dict[str, object]:
    return {
        "event_id": "$evt-1",
        "room_id": "!room:launchscope",
        "sender": "@business-investment:launchscope",
        "content": handoff,
    }


def _accept(handoff: dict[str, object], *, expected_epoch: int | None) -> object:
    bridge = AgentTeamsBridge()
    return bridge.accept_matrix_event(
        _matrix_event(handoff),
        _Directory(),
        expected_run_id=UUID(str(handoff["run_id"])),
        expected_task_id=UUID(str(handoff["task_id"])),
        expected_dispatch_epoch=expected_epoch,
    )


def test_dispatch_epoch_is_carried_to_the_agent_so_it_can_be_echoed() -> None:
    run_id, task_id, tenant_id = uuid4(), uuid4(), uuid4()
    assignment = AgentTeamsBridge().assignment_from_dispatch(
        {
            "event_type": "evaluation.task.ready.v1",
            "tenant_id": str(tenant_id),
            "run_id": str(run_id),
            "task_id": str(task_id),
            "payload": {
                "team_name": "launchscope-potential-review",
                "agent_code": "business-investment",
                "stage_code": "DIMENSION_REVIEW",
                "skill_ref": "business-investment@1.0.0",
                "context_token": "token",
                "handoff_schema": {"schema_version": "1.1"},
                "usage_policy": {"budget": 1},
                "research_policy": {"material_only": True},
                "manifest_sha256": "abc",
                "dispatch_epoch": 3,
            },
        }
    )
    assert assignment.body["dispatch_epoch"] == 3
    assert "dispatch_epoch" in str(assignment.body["instruction"])


def test_a_handoff_from_a_superseded_dispatch_is_refused() -> None:
    stale = _handoff(dispatch_epoch=0)
    with pytest.raises(SupersededHandoffError, match="stale"):
        _accept(stale, expected_epoch=1)


def test_a_superseded_handoff_is_distinguishable_from_a_contract_violation() -> None:
    """A stale reply is a benign race, so it must not be blamed on the Agent.

    The listener turns a contract violation into a synthetic BLOCKED/VALIDATION
    handoff.  Doing that for a superseded reply would both slander the Agent and
    stall the Matrix cursor, so the two cases must be separable by type.
    """
    with pytest.raises(SupersededHandoffError):
        _accept(_handoff(dispatch_epoch=0), expected_epoch=2)
    with pytest.raises(SupersededHandoffError):
        _accept(_handoff(), expected_epoch=2)
    # A real contract violation stays a plain policy error, not a superseded one.
    forged = _handoff(dispatch_epoch=1)
    with pytest.raises(BridgePolicyError) as raised:
        AgentTeamsBridge().accept_matrix_event(
            _matrix_event(forged),
            _Directory(),
            expected_run_id=uuid4(),
            expected_task_id=UUID(str(forged["task_id"])),
            expected_dispatch_epoch=1,
        )
    assert not isinstance(raised.value, SupersededHandoffError)


def test_a_handoff_from_the_current_dispatch_is_accepted() -> None:
    current = _handoff(dispatch_epoch=1)
    accepted = _accept(current, expected_epoch=1)
    assert accepted.handoff.dispatch_epoch == 1


def test_a_legacy_producer_without_an_epoch_echo_is_accepted_before_any_resume() -> None:
    legacy = _handoff()
    assert "dispatch_epoch" not in legacy
    accepted = _accept(legacy, expected_epoch=0)
    assert accepted.handoff.dispatch_epoch is None


def test_omitting_the_epoch_cannot_bypass_the_check_after_a_resume() -> None:
    """Once a Task has been re-dispatched, an absent echo must not be a bypass."""
    legacy = _handoff()
    assert "dispatch_epoch" not in legacy
    with pytest.raises(BridgePolicyError, match="omits dispatch_epoch"):
        _accept(legacy, expected_epoch=2)
