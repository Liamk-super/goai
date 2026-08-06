from __future__ import annotations

import shutil

import pytest

from launchscope_orchestrator.manifest_loader import AGENT_CODES, AgentContractError, AgentManifestLoader


def test_fixed_1_plus_5_identity_contracts_are_complete_and_restricted() -> None:
    contracts = AgentManifestLoader().load_all()

    assert {contract.code for contract in contracts} == AGENT_CODES
    manager = next(contract for contract in contracts if contract.code == "evaluation-manager")
    assert manager.role == "manager"
    assert "does_not_make_specialist_conclusions" in manager.risk_boundaries
    for contract in contracts:
        assert {"run.write", "task.write", "memory.write", "report.write"}.issubset(contract.prohibited_actions)


def test_agent_contract_hash_detects_tampering(tmp_path) -> None:
    source = AgentManifestLoader().manifest_root / "product-engineering.v1.yaml"
    altered = tmp_path / source.name
    shutil.copy(source, altered)
    altered.write_text(
        altered.read_text(encoding="utf-8").replace("technical feasibility", "unbounded authority"),
        encoding="utf-8",
    )

    with pytest.raises(AgentContractError, match="hash mismatch"):
        AgentManifestLoader().load_file(altered)
