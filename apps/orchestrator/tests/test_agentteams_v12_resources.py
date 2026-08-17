from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_agentteams_v12_release_and_final_resource_contract_are_pinned() -> None:
    lock = json.loads((ROOT / "infra/agentteams/version-lock.json").read_text(encoding="utf-8"))
    assert lock == {
        "version": "v1.2.0",
        "release_commit": "793db24",
        "windows_installer_url": "https://raw.githubusercontent.com/agentscope-ai/AgentTeams/v1.2.0/install/agentteams-install.ps1",
        "windows_installer_sha256": "f46a6b0a4e676bf4557f83448bfdb59fdb872a01349a1320a1aedbdb2db7bb41",
        "unix_installer_url": "https://raw.githubusercontent.com/agentscope-ai/AgentTeams/v1.2.0/install/agentteams-install.sh",
        "unix_installer_sha256": "701f53c53dc476d8ca7f33428e231c1706d967ac2b517ec4c1c59d742864331d",
        "api_version": "agentteams.io/v1beta1",
        "mode": "embedded",
    }
    subprocess.run([sys.executable, str(ROOT / "scripts/build-agentteams-packages.py"), "--check"], check=True)


def test_team_references_six_independent_workers_with_one_leader() -> None:
    resource_path = ROOT / "infra/agentteams/resources/launchscope-team.yaml"
    documents = list(yaml.safe_load_all(resource_path.read_text(encoding="utf-8")))
    workers = [item for item in documents if item["kind"] == "Worker"]
    team = next(item for item in documents if item["kind"] == "Team")
    human = next(item for item in documents if item["kind"] == "Human")
    assert len(workers) == 6
    assert all(worker["spec"]["runtime"] == "copaw" for worker in workers)
    by_code = {worker["metadata"]["annotations"]["launchscope.io/agent-code"]: worker for worker in workers}
    assert by_code["user-evidence"]["spec"]["env"] == {
        "COPAW_REACT_MAX_ITERS": "__LAUNCHSCOPE_USER_COPAW_MAX_ITERS__"
    }
    assert all(
        worker["spec"]["env"] == {"COPAW_REACT_MAX_ITERS": "__LAUNCHSCOPE_COPAW_MAX_ITERS__"}
        for code, worker in by_code.items()
        if code != "user-evidence"
    )
    assert len(team["spec"]["workerMembers"]) == 6
    assert sum(item["role"] == "team_leader" for item in team["spec"]["workerMembers"]) == 1
    assert human["metadata"]["name"] == "launchscope-human-coordinator"


def test_worker_package_teaches_the_needs_input_status_contract() -> None:
    namespace = runpy.run_path(str(ROOT / "scripts/build-agentteams-packages.py"))
    contract = yaml.safe_load(
        (ROOT / "packages/contracts/agents/evaluation-manager.v1.yaml").read_text(encoding="utf-8")
    )
    files = namespace["package_files"](contract)
    assert json.loads(files["manifest.json"])["worker"]["runtime"] == "copaw"
    assert "config/AGENTS.md" in files and "config/SOUL.md" in files
    skill = files["skills/launchscope-evaluation-manager-handoff-v1/SKILL.md"]
    assert "schema_version 1.1, set status NEEDS_INPUT" in skill
    assert "For every other status, information_requests must be an empty list" in skill


def test_specialist_packages_expose_the_exact_dispatched_skill_alias() -> None:
    namespace = runpy.run_path(str(ROOT / "scripts/build-agentteams-packages.py"))
    contract = yaml.safe_load(
        (ROOT / "packages/contracts/agents/product-engineering.v2.yaml").read_text(encoding="utf-8")
    )
    files = namespace["package_files"](contract)
    assert "skills/browser-product-audit/SKILL.md" in files


def test_user_worker_package_contains_the_admitted_uvd_skill_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_USER_VALIDATION_ENABLED", "true")
    namespace = runpy.run_path(str(ROOT / "scripts/build-agentteams-packages.py"))
    contract = yaml.safe_load(
        (ROOT / "packages/contracts/agents/user-evidence.v3.yaml").read_text(encoding="utf-8")
    )
    files = namespace["package_files"](contract)
    assert files["skills/user-validation-designer/SKILL.md"] == (
        ROOT / "packages/user-validation-designer/SKILL.md"
    ).read_text(encoding="utf-8")
    assert "no second model lane" in files["skills/user-validation-designer/LAUNCHSCOPE.md"]
