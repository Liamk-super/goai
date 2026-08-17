from __future__ import annotations

import json
import subprocess
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "packages" / "evidence-grounding-audit"


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_evidence_calibration_input_and_output_schemas_validate_demo() -> None:
    input_schema = _load(SKILL / "schema" / "input.schema.json")
    output_schema = _load(SKILL / "schema" / "output.schema.json")
    fixture = _load(SKILL / "examples" / "demo.input.json")
    Draft202012Validator.check_schema(input_schema)
    Draft202012Validator.check_schema(output_schema)
    Draft202012Validator(input_schema).validate(fixture)
    completed = subprocess.run(
        ["node", str(SKILL / "runner" / "cli.mjs")],
        input=json.dumps(fixture, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
        cwd=ROOT,
    )
    result = json.loads(completed.stdout)
    Draft202012Validator(output_schema).validate(result)


def test_demo_handoff_excludes_rejected_claims_from_accepted_pool() -> None:
    result = _load(ROOT / "deliverables" / "evidence-calibration-v1" / "evidence_calibration_result.json")
    handoff = result["structured_output"]["supervisor_handoff"]
    assert all(item["verdict"] == "PASS" for item in handoff["accepted_claims"])
    assert all(item["verdict"] != "REJECT" for item in handoff["accepted_claims"])
