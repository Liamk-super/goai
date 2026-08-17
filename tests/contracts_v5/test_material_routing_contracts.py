from __future__ import annotations

import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


def test_new_material_contracts_are_valid_draft_2020_12_schemas() -> None:
    paths = [
        "manager/material/material-manifest.v1.json",
        "manager/material/material-unit.v1.json",
        "manager/material/material-selection.v1.json",
        "manager/manager-plan.v2.json",
        "tasks/agent-task-ticket.v4.json",
        "manager/run-manifest.v5.json",
        "tasks/tools/launchscope-context.get.v2.json",
        "tasks/tools/material.read.v1.json",
    ]

    for relative in paths:
        Draft202012Validator.check_schema(
            json.loads((ROOT / "packages/contracts" / relative).read_text(encoding="utf-8"))
        )


def test_v5_agents_use_scoped_material_tools() -> None:
    agent_root = ROOT / "packages/contracts/manager/agents"
    contracts = {
        path.stem.split(".")[0]: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in agent_root.glob("*.v5.yaml")
    }

    assert set(contracts) == {
        "evaluation-manager",
        "user-evidence",
        "product-engineering",
        "business-investment",
        "evidence-auditor",
    }
    assert contracts["evaluation-manager"]["allowed_tools"] == ["launchscope-context.get.v2"]
    for code in ("user-evidence", "product-engineering", "business-investment", "evidence-auditor"):
        assert "material.read.v1" in contracts[code]["allowed_tools"]
