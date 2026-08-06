from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _script(name: str) -> str:
    return (ROOT / "scripts" / name).read_text(encoding="utf-8")


def test_bootstrap_pins_official_agentteams_installer() -> None:
    body = _script("demo-bootstrap.ps1")
    assert "/AgentTeams/v1.2.0/install/agentteams-install.ps1" in body
    assert "f46a6b0a4e676bf4557f83448bfdb59fdb872a01349a1320a1aedbdb2db7bb41" in body
    assert "Get-FileHash -Algorithm SHA256" in body


def test_start_tracks_all_processes_and_has_recorded_only_boundary() -> None:
    body = _script("demo-start.ps1")
    for process in (
        "'api'", "'mcp'", "'web'", "'ops'", "'outbox-publisher'",
        "'agentteams-dispatch-bridge'", "'agentteams-matrix-listener'",
    ):
        assert f"Start-DemoProcess {process}" in body
    assert "BLOCKED_NO_AUTHORIZED_CASE" not in body


def test_stop_and_reset_are_scope_and_state_guarded() -> None:
    stop = _script("demo-stop.ps1")
    reset = _script("demo-reset.ps1")
    assert "started_at" in stop and "executable" in stop and "Stop-Process" in stop
    assert "Assert-LocalDemo -RequireForce" in reset
    for unsafe in ("RUNNING", "NEEDS_ATTENTION", "SUBMISSION_UNKNOWN", "CLAIMED"):
        assert unsafe in reset
    assert "down --volumes --remove-orphans" in reset
    assert "Remove-Item -Recurse" not in reset


def test_demo_template_contains_no_secret_value() -> None:
    values = {}
    for line in (ROOT / ".env.demo.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    for key in values:
        if any(marker in key for marker in ("PASSWORD", "API_KEY", "TOKEN", "SECRET", "ACCESS_KEY")):
            assert values[key] == ""
