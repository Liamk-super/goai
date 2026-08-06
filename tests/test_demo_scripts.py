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
    assert body.index("Get-FileHash -Algorithm SHA256") < body.index("agentteams-install-v1.2.0-launchscope.ps1")
    assert "AGENTTEAMS_MATRIX_APPSERVICE_AS_TOKEN" in body
    assert "AGENTTEAMS_MATRIX_APPSERVICE_HS_TOKEN" in body
    assert "Ensure-LocalSecret 'AGENTTEAMS_ADMIN_PASSWORD'" in body
    assert "infra/compose/.env.local" in body
    assert "Ensure-LocalSecret $name" in body
    assert "Ensure-LocalValue 'DATABASE_URL'" in body
    assert "$env:AGENTTEAMS_ENV_FILE = (Join-Path $state 'agentteams-manager.env')" in body
    assert "$env:AGENTTEAMS_DATA_DIR = 'launchscope-agentteams-data'" in body
    assert "$env:AGENTTEAMS_WORKSPACE_DIR = (Join-Path $state 'agentteams-manager')" in body
    assert "$env:AGENTTEAMS_ADMIN_USER = 'admin'" in body
    assert "-NonInteractive" in body


def test_agentteams_cli_falls_back_to_the_official_controller_container() -> None:
    helper = _script("invoke-agentteams-cli.ps1")
    assert "Get-Command agt" in helper
    assert "docker exec agentteams-controller agt" in helper
    assert "docker cp" in helper
    assert "file://\\./" in helper
    assert "docker exec --workdir $temporaryRoot" in helper
    assert '"/generated/packages/$([IO.Path]::GetFileName($localReference))"' in helper
    for name in ("demo-start.ps1", "demo-preflight.ps1", "demo-reset.ps1"):
        assert "invoke-agentteams-cli.ps1" in _script(name)
    assert "$workerPayload.workers" in _script("demo-preflight.ps1")


def test_rocketmq_broker_repairs_only_its_named_volume_then_drops_privileges() -> None:
    compose = (ROOT / "infra" / "compose" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "chown -R rocketmq:rocketmq /home/rocketmq/store" in compose
    assert "runuser -u rocketmq -- sh mqbroker" in compose
    assert "mqadmin clusterList -n rocketmq-namesrv:9876" in compose


def test_agentteams_provisioning_persists_bridge_identity_without_printing_credentials() -> None:
    body = _script("demo-provision-agentteams.ps1")
    assert "AGENTTEAMS_TEAM_ROOM_ID" in body
    assert "AGENTTEAMS_LEADER_ROOM_ID" in body
    assert "AGENTTEAMS_HUMAN_ACCESS_TOKEN" in body
    assert "LAUNCHSCOPE_MATRIX_AGENT_DIRECTORY_JSON" in body
    assert '/rooms/$encodedRoom/join' in body
    assert "/_matrix/client/v3/createRoom" in body
    assert "trusted_private_chat" in body
    assert "joined_members" in body
    assert "credentials redacted" in body
    assert "Write-Host $login.access_token" not in body


def test_start_tracks_all_processes_and_has_recorded_only_boundary() -> None:
    body = _script("demo-start.ps1")
    for process in (
        "'api'", "'mcp'", "'web'", "'ops'", "'outbox-publisher'",
        "'agentteams-dispatch-bridge'", "'agentteams-matrix-listener'",
    ):
        assert f"Start-DemoProcess {process}" in body
    assert "BLOCKED_NO_AUTHORIZED_CASE" not in body
    assert "[switch]$MaterialOnly" in body
    assert "launchscope-team.existing-human.yaml" in body
    assert "public research remains fail-closed" in body
    assert "Refusing to start over an occupied Demo application port" in body
    assert "A Demo launcher exited before readiness" in body


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
