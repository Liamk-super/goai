from __future__ import annotations

from launchscope_orchestrator.manifest_loader import AgentManifestLoader


def test_v3_agent_generation_grants_only_the_two_new_bounded_capabilities() -> None:
    contracts = {contract.code: contract for contract in AgentManifestLoader().load_all("v3")}

    user = contracts["user-evidence"]
    auditor = contracts["evidence-auditor"]
    assert user.permits_skill("user-validation-designer")
    assert user.permits_tools(
        (
            "user-validation-designer.start.v1",
            "user-validation-designer.submit-step.v1",
            "user-validation-designer.resume.v1",
        )
    )
    assert auditor.permits_tools(("user-validation-audit-context.get.v1",))
    for code, contract in contracts.items():
        if code != "user-evidence":
            assert not contract.permits_skill("user-validation-designer")
        if code != "evidence-auditor":
            assert not contract.permits_tools(("user-validation-audit-context.get.v1",))


def test_v2_agent_generation_remains_loadable_and_unchanged() -> None:
    assert {contract.version for contract in AgentManifestLoader().load_all("v2")} == {"2.0"}
