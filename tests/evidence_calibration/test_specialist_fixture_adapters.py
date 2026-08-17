from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _json(path: str) -> object:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_current_product_user_and_investment_fixtures_are_consumed() -> None:
    request = {
        "task_id": "current-specialist-fixtures",
        "project_id": "fixture-project",
        "product_version": "V1.0",
        "generated_at": "2026-08-11T00:00:00Z",
        "expected_agents": ["product", "user", "investment"],
        "agent_results": [
            {
                "source_agent": "product",
                "status": "COMPLETED",
                "project_id": "fixture-project",
                "product_version": "V1.0",
                "payload": _json("reference/skills/product-technical-audit/examples/normal-complete.output.json"),
            },
            {
                "source_agent": "user",
                "status": "COMPLETED",
                "project_id": "fixture-project",
                "product_version": "V1.0",
                "payload": _json("packages/user-validation-designer/examples/output.example.json"),
            },
            {
                "source_agent": "investment",
                "status": "COMPLETED",
                "project_id": "fixture-project",
                "product_version": "V1.0",
                "payload": _json("reference/skills/business-investment-assessment/examples/output.fixture.json"),
            },
        ],
    }
    completed = subprocess.run(
        ["node", str(ROOT / "packages/evidence-grounding-audit/runner/cli.mjs")],
        input=json.dumps(request, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
        cwd=ROOT,
    )
    result = json.loads(completed.stdout)
    claims = result["structured_output"]["claims"]
    decisions = result["structured_output"]["calibration_decisions"]
    assert result["status"] == "completed"
    assert {item["source_agent"] for item in claims} == {
        "product-engineering", "user-evidence", "business-investment"
    }
    assert len(decisions) == len(claims) > 0
    assert all(item["verdict"] in {"PASS", "DOWNGRADE", "REQUEST_MORE_EVIDENCE", "REJECT"} for item in decisions)
