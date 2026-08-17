from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_anonymous_fixture_is_hash_locked() -> None:
    fixture = json.loads((ROOT / "tests/fixtures/user-agent/anonymous-case.json").read_text(encoding="utf-8"))
    input_bytes = (ROOT / fixture["input_path"]).read_bytes()
    assert fixture["privacy"] == "SYNTHETIC_NO_PERSONAL_DATA"
    assert fixture["network_allowed"] is False
    assert fixture["external_actions_allowed"] is False
    assert hashlib.sha256(input_bytes).hexdigest() == fixture["input_sha256"]


def test_recorded_user_agent_isolated_path(tmp_path: Path) -> None:
    output_dir = tmp_path / "recorded"
    completed = subprocess.run(
        [
            str(ROOT / ".venv/Scripts/python.exe"),
            str(ROOT / "scripts/test-user-agent.py"),
            "--mode",
            "Recorded",
            "--output-dir",
            str(output_dir),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    manifest = json.loads((output_dir / "run-manifest.json").read_text(encoding="utf-8"))
    result = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "PASS"
    assert manifest["skill_version"] == "1.0.5"
    assert manifest["matrix_used"] is False
    assert manifest["other_agents_started"] is False
    assert manifest["transition_count"] > 0
    assert result["structured_output"]["human_report"] == result["structured_output"]["summary_report"]


def test_real_model_budget_blocks_before_network_submission(tmp_path: Path) -> None:
    output_dir = tmp_path / "budget-blocked"
    environment = os.environ | {
        "AGENTTEAMS_MODEL_ID": "fixture-model",
        "AGENTTEAMS_MODEL_BASE_URL": "https://model.invalid/v1",
        "AGENTTEAMS_MODEL_API_KEY": "fixture-key",
        "LAUNCHSCOPE_MODEL_INPUT_USD_PER_MILLION": "1",
        "LAUNCHSCOPE_MODEL_OUTPUT_USD_PER_MILLION": "1",
    }
    completed = subprocess.run(
        [
            str(ROOT / ".venv/Scripts/python.exe"),
            str(ROOT / "scripts/test-user-agent.py"),
            "--mode",
            "RealModel",
            "--output-dir",
            str(output_dir),
            "--authorize-real-model",
            "--budget-limit-usd",
            "0",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert completed.returncode == 4, completed.stderr
    manifest = json.loads((output_dir / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "BUDGET_BLOCKED"
    assert manifest["transitions"][-1]["state"] == "BUDGET_BLOCKED_BEFORE_SUBMISSION"
