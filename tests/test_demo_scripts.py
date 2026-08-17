from __future__ import annotations

import runpy
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _script(name: str) -> str:
    return (ROOT / "scripts" / name).read_text(encoding="utf-8")


def test_root_launcher_defaults_to_live_mode_and_reuses_demo_scripts() -> None:
    body = (ROOT / "start.ps1").read_text(encoding="utf-8")
    command = (ROOT / "start.cmd").read_text(encoding="utf-8")

    assert "[string]$Mode = 'Live'" in body
    assert "[ValidateSet('Recorded', 'Material', 'Live')]" in body
    assert "scripts/demo-bootstrap.ps1" in body
    assert "scripts/demo-start.ps1" in body
    assert "scripts/demo-stop.ps1" in body
    assert "-KeepInfrastructure" in body
    assert "'Recorded' { $startArguments += '-RecordedOnly' }" in body
    assert "'Material' { $startArguments += '-MaterialOnly' }" in body
    assert "Restarting the existing LaunchScope instance" in body
    assert "Test-TcpPort '127.0.0.1'" in body
    assert "function Stop-ResidualLaunchScopeProcesses" in body
    assert "Application port(s) remain occupied by an unverified process" in body
    assert 'Start-Process "$($demoPorts.WebOrigin)/"' in body
    assert "/api/v1/demo/default-session" in body
    assert "egress_gate_enforced" in body
    assert "The fixed Demo workspace binding is missing" in body
    assert '"%~dp0start.ps1" %*' in command
    assert "LAUNCHSCOPE_DEMO_DEFAULT_WORKSPACE_FILE" in command
    assert "DELIVERY_SCOPED_MODEL_TOKEN_ENABLED" in command
    assert "MODEL_USAGE_LEDGER_MODE" in command


def test_demo_application_ports_are_configurable_across_the_one_click_chain() -> None:
    root_start = (ROOT / "start.ps1").read_text(encoding="utf-8")
    command = (ROOT / "start.cmd").read_text(encoding="utf-8")
    common = _script("demo-common.ps1")
    demo_start = _script("demo-start.ps1")
    preflight = _script("demo-preflight.ps1")

    assert "[int]$WebPort = 0" in root_start
    assert "[int]$OpsPort = 0" in root_start
    assert "Initialize-DemoApplicationPorts" in common
    assert "MINIO_API_CORS_ALLOW_ORIGIN" in common
    assert "LAUNCHSCOPE_WEB_PORT" in command
    assert "LAUNCHSCOPE_OPS_PORT" in command
    assert "@launchscope/web','exec','next','dev'" in demo_start
    assert "@launchscope/ops','exec','next','dev'" in demo_start
    assert "[int]$env:LAUNCHSCOPE_WEB_PORT" in preflight
    assert "[int]$env:LAUNCHSCOPE_OPS_PORT" in preflight


def test_demo_start_rejects_revision_ids_that_do_not_fit_alembic() -> None:
    body = _script("demo-start.ps1")
    assert "apps/api/migrations/versions" in body
    assert "Alembic revision IDs must be 32 characters or fewer" in body
    assert ".Value.Length -gt 32" in body


def test_demo_start_rebuilds_next_caches_and_requires_http_readiness() -> None:
    body = _script("demo-start.ps1")
    web_config = (ROOT / "apps" / "web" / "next.config.mjs").read_text(encoding="utf-8")
    cache_index = body.index("Join-Path $root 'apps/web/.next-demo'")
    web_start_index = body.index("Start-DemoProcess 'web'")

    assert cache_index < web_start_index
    assert "Join-Path $root 'apps/ops/.next'" in body
    assert "Remove-Item -LiteralPath $nextCachePath -Recurse -Force" in body
    assert "SetEnvironmentVariable('NEXT_DIST_DIR', '.next-demo', 'Process')" in body
    assert "SetEnvironmentVariable('NEXT_DIST_DIR', $null, 'Process')" in body
    assert 'distDir: process.env.NEXT_DIST_DIR || ".next"' in web_config
    assert "function Test-DemoHttpOk" in body
    assert 'Test-DemoHttpOk "$($demoPorts.WebOrigin)/"' in body
    assert 'Test-DemoHttpOk "$($demoPorts.OpsOrigin)/audit/events"' in body
    assert "Test-DemoHttpOk 'http://127.0.0.1:8100/docs'" in body
    assert "$deadline = (Get-Date).AddSeconds(180)" in body


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
    assert '"/$($relative.Replace(\'\\\',\'/\'))"' in helper
    assert "$packageParent = $packageTarget.Substring" in helper
    for name in ("demo-start.ps1", "demo-preflight.ps1", "demo-reset.ps1"):
        assert "invoke-agentteams-cli.ps1" in _script(name)
    assert "$workerPayload.workers" in _script("demo-preflight.ps1")


def test_rocketmq_broker_repairs_only_its_named_volume_then_drops_privileges() -> None:
    compose = (ROOT / "infra" / "compose" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "chown -R rocketmq:rocketmq /home/rocketmq/store" in compose
    assert "runuser -u rocketmq -- sh mqbroker" in compose
    assert "ROCKETMQ_BROKER_VIP_PORT" not in compose
    assert "ROCKETMQ_BROKER_PORT" not in compose
    assert "ROCKETMQ_BROKER_HA_PORT" not in compose
    assert "mqadmin clusterList -n rocketmq-namesrv:9876" in compose


def test_agentteams_provisioning_persists_bridge_identity_without_printing_credentials() -> None:
    body = _script("demo-provision-agentteams.ps1")
    assert "Get-AgentTeamsWorkerResourceMap" in body
    assert "Get-AgentTeamsTeamName" in body
    assert "Get-AgentTeamsHumanName" in body
    assert "$expectedWorkers = $workerResources.Count" in body
    assert "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)" in body
    assert "$OutputEncoding = [Console]::OutputEncoding" in body
    assert "function Invoke-AgentTeamsJson" in body
    assert "& $cli @CommandArguments > $rawPath" in body
    assert "Get-Content -LiteralPath $rawPath -Raw" in body
    assert "for ($attempt = 1; $attempt -le 5; $attempt += 1)" in body
    assert "did not return valid JSON after 5 attempts" in body
    assert "All six LaunchScope Workers" not in body
    assert "AGENTTEAMS_TEAM_ROOM_ID" in body
    assert "AGENTTEAMS_LEADER_ROOM_ID" in body
    assert "AGENTTEAMS_HUMAN_ACCESS_TOKEN" in body
    assert "LAUNCHSCOPE_MATRIX_AGENT_DIRECTORY_JSON" in body
    assert '/rooms/$encodedRoom/join' in body
    assert "/_matrix/client/v3/createRoom" in body
    assert "trusted_private_chat" in body
    assert "joined_members" in body
    assert "$expectedMembers = @([string]$human[0].matrixUserID, [string]$WorkerMXID)" in body
    assert "$roomMembers.Count -eq 2" in body
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
    assert "$(if($MaterialOnly){'true'}else{'false'})" in body
    assert "Get-AgentTeamsExistingHumanResourceName" in body
    assert "public research remains fail-closed" in body
    assert "Refusing to start over an occupied Demo application port" in body
    assert "A Demo launcher exited before readiness" in body
    assert "$expectedProcessNames -contains $record.name" in body
    assert 'Workspace  $($demoPorts.WebOrigin)/' in body
    assert "/api/v1/demo/default-session" in body
    assert "LAUNCHSCOPE_DEMO_DEFAULT_WORKSPACE_FILE" in body
    assert "Set-AgentTeamsActiveModel -ProviderId 'launchscope-model-egress' -ModelByWorker $runtimeModels" in body
    assert "Wait-AgentTeamsWorkerConsoles" in body
    assert "$runtimeModels[$agentCode] = $model" in body
    assert "Set-AgentTeamsRunningMaxIters -MaxIters $copawMaxIters" in body
    assert "$copawMaxIters -gt 256" in body
    assert "$userCopawMaxIters -gt 256" in body
    assert body.rindex("Set-AgentTeamsRunningMaxIters -MaxIters $copawMaxIters") > body.index(
        "demo-provision-agentteams.ps1"
    )
    assert body.rindex("Set-AgentTeamsRunningMaxIters -MaxIters $copawMaxIters") > body.index(
        "if (-not $ready) { throw"
    )
    assert body.index("LAUNCHSCOPE_USER_COPAW_MAX_ITERS") < body.index(
        "$resource = $resource.Replace('__LAUNCHSCOPE_USER_COPAW_MAX_ITERS__'"
    )
    assert "LAUNCHSCOPE_AGENTTEAMS_WORKER_NAMES_JSON" in body
    assert "DELIVERY_SCOPED_MODEL_TOKEN_ENABLED', 'true'" in body
    assert "MODEL_USAGE_LEDGER_MODE', 'GATEWAY_DELIVERY'" in body
    assert "launchscope_api.model_gateway cutover-status" not in body
    assert "model-gateway-credential-refresh" not in body
    assert "COPAW_TASK_DELTA" not in body
    assert "__LAUNCHSCOPE_COPAW_MAX_ITERS__" in body
    assert "LAUNCHSCOPE_EXECUTION_MODE" in body
    assert "NEXT_PUBLIC_LAUNCHSCOPE_EXECUTION_MODE" in body
    assert "LAUNCHSCOPE_EXECUTION_READINESS_FILE" in body
    assert "reconcile-undelivered-once" in body
    assert "dispatch_enabled" in body
    assert "demo-provision-agentteams.ps1" in body
    assert "Test-AgentTeamsBridgeDirectoryMatchesGeneration" in body


def test_live_scripts_select_the_generation_specific_agentteams_bundle() -> None:
    common = _script("demo-common.ps1")
    start = _script("demo-start.ps1")
    preflight = _script("demo-preflight.ps1")

    assert "function Test-Supervisor1P4Enabled" in common
    assert "function Get-AgentTeamsWorkerResourceMap" in common
    assert "function Get-AgentTeamsWorkerMap" in common
    assert "LAUNCHSCOPE_REPORT_V2_ENABLED" in common
    assert "LAUNCHSCOPE_REPORT_V3_ENABLED" in common
    assert "SetEnvironmentVariable('LAUNCHSCOPE_REPORT_V3_ENABLED', 'true', 'Process')" in common
    assert "$reportV3Enabled -or $reportV2Enabled" in common
    assert "return 'v6'" in common
    assert "function Import-DemoEnvironmentValues" in common
    assert '"infra/agentteams/resources/launchscope-team-$(Get-AgentTeamsGeneration).yaml"' in common
    assert '"infra/agentteams/generated/packages-$(Get-AgentTeamsGeneration)"' in common
    assert '"launchscope-evaluation-supervisor-$generation-live"' in common
    assert '"launchscope-human-coordinator-$(Get-AgentTeamsGeneration)-live"' in common
    assert '"launchscope-potential-review-$(Get-AgentTeamsGeneration)-operational"' in common
    assert "if ($generation -eq 'v4')" in common
    assert "function Test-AgentTeamsBridgeDirectoryMatchesGeneration" in common
    assert '"agentteams-worker-$($entry.Value)"' in common
    assert "'geo-policy-trend'" not in common
    assert "$expected = (Get-AgentTeamsWorkerMap).Count" in common
    assert "if ($ready -eq $expected)" in common
    assert "$MaxIters -gt 256" in common
    assert "CoPaw max iterations must be between 1 and 256" in common

    assert "$resourceRelativePath = Get-AgentTeamsResourceRelativePath" in start
    assert "$packageDirectoryRelativePath = Get-AgentTeamsPackageDirectoryRelativePath" in start
    assert "foreach($agentCode in (Get-AgentTeamsWorkerMap).Keys)" in start
    assert "Get-AgentTeamsRenderedResourceName" in start
    assert "Get-AgentTeamsHumanName" in start
    provision_index = start.index("demo-provision-agentteams.ps1")
    capture_index = start.index("$provisionExitCode = $LASTEXITCODE", provision_index)
    reload_index = start.index("Import-DemoEnvironmentValues", provision_index)
    verify_index = start.index("Test-AgentTeamsBridgeDirectoryMatchesGeneration", provision_index)
    assert provision_index < capture_index < reload_index < verify_index
    assert "$provisionExitCode -ne 0" in start
    assert "LAUNCHSCOPE_MATRIX_AGENT_DIRECTORY_JSON" in start[reload_index:verify_index]
    assert "LAUNCHSCOPE_MATRIX_AGENT_ROOMS_JSON" in start[reload_index:verify_index]
    assert "Import-DemoEnvironment (Join-Path $root $EnvironmentFile)" not in start[provision_index:verify_index]

    assert "$agentTeamsGeneration = Get-AgentTeamsGeneration" in preflight
    assert "LAUNCHSCOPE_PROVIDER_COST_MODE" in preflight
    assert "'TOKEN_ONLY'" in preflight
    assert "$costMode -eq 'TOKEN_ONLY' -or $pricesConfigured" in preflight
    assert "$agentTeamsWorkerMap = Get-AgentTeamsWorkerMap" in preflight
    assert "$selectedNames = @($agentTeamsWorkerResourceMap.Values)" in preflight
    assert "expected_worker_count=$expectedAgentTeamsWorkers" in preflight
    assert "Delivery-scoped model control" in preflight
    assert "Worker model-call guards" in preflight
    assert "elseif($BootstrapMode){'NOT_RUNNING'}else{'FAIL'}" in preflight
    assert "llm_retry_enabled" in common
    assert "llm_max_concurrent" in common
    assert "llm_max_qpm" in common
    assert "for ($attempt = 1; $attempt -le 3; $attempt += 1)" in common
    assert "CoPaw heartbeat configuration failed" in common
    assert "[switch]$BootstrapMode" in preflight
    assert "$notRunningBlocks = $RequireExternalCase -and -not $BootstrapMode" in preflight
    assert "Six AgentTeams Workers" not in preflight
    assert "$preflightArguments += '-BootstrapMode'" in start
    assert "NEXT_PUBLIC_LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED" in start
    assert "AgentTeams bridge generation" in preflight
    assert "AgentTeams Matrix dispatch rooms" in preflight
    assert "AgentTeams heartbeats disabled" in preflight
    assert "LAUNCHSCOPE_MATRIX_AGENT_ROOMS_JSON" in preflight
    assert "/joined_members" in preflight
    assert "/account/whoami" in preflight


def test_acceptance_exporter_branches_on_each_run_manifest_generation() -> None:
    namespace = runpy.run_path(str(ROOT / "scripts" / "export-v01-acceptance.py"))
    validate = namespace["_validate_manifest_topology"]
    v4_codes = namespace["V4_AGENT_CODES"]
    legacy_run_id = uuid4()
    v4_run_id = uuid4()
    v4_manifest = {
        "run_id": v4_run_id,
        "frozen_config": {
            "architecture_generation": "supervisor-1p4-v1",
            "agent_contract_generation": "v4",
            "agents": {code: {} for code in v4_codes},
            "physical_topology": {
                "worker_count": 5,
                "leader": "evaluation-manager",
                "workers": [
                    "user-evidence",
                    "product-engineering",
                    "business-investment",
                    "evidence-auditor",
                ],
                "peer_mentions": False,
            },
        },
    }

    assert validate([
        {"run_id": legacy_run_id, "frozen_config": {"agent_contract_generation": "v3"}},
        v4_manifest,
    ]) == {str(legacy_run_id): "legacy", str(v4_run_id): "v4"}

    invalid = {
        **v4_manifest,
        "frozen_config": {
            **v4_manifest["frozen_config"],
            "agents": {**v4_manifest["frozen_config"]["agents"], "geo-policy-trend": {}},
        },
    }
    with pytest.raises(ValueError, match="exactly the five"):
        validate([invalid])


def test_copaw_running_limit_uses_the_supported_hot_reload_api() -> None:
    body = _script("demo-common.ps1")
    assert "function Wait-AgentTeamsWorkerConsoles" in body
    assert "[int]$StableSeconds = 0" in body
    assert "$readySince = $null" in body
    assert "/api/agent/running-config" in body
    assert "X-Agent-Id" in body
    assert "Invoke-RestMethod -Method Get" in body
    assert "Invoke-RestMethod -Method Put" in body


def test_live_start_requires_worker_consoles_to_remain_stable_after_resource_apply() -> None:
    body = _script("demo-start.ps1")
    common = _script("demo-common.ps1")
    assert "Wait-AgentTeamsWorkerConsoles -TimeoutSeconds 600 -StableSeconds 30" in body
    assert "Wait-AgentTeamsTeamReady -TimeoutSeconds 600" in body
    assert "Sync-AgentTeamsPackageSkills -PackageDirectory $packageDirectoryRelativePath" in body
    assert "function Sync-AgentTeamsPackageSkills" in common
    assert "agentteams-storage/agents/$workerName/skills/" in common
    assert "agentteams-storage/agents/$workerName/.copaw/workspaces/default/skills/" in common
    assert '"launchscope-potential-review-$(Get-AgentTeamsGeneration)-operational"' in common


def test_copaw_model_switch_uses_the_supported_active_model_api() -> None:
    body = _script("demo-common.ps1")
    assert "/api/models/active" in body
    assert "/api/models/$ProviderId/models" in body
    assert "foreach ($scope in @('global', 'agent'))" in body
    assert "?scope=effective" in body
    assert "current.active_llm.model -eq $targetModel" in body
    assert "X-Agent-Id" in body
    assert "/api/models/custom-providers" in body
    assert "/api/models/$ProviderId/config" in body
    assert "AGENTTEAMS_MODEL_BASE_URL" in body
    assert "AGENTTEAMS_MODEL_API_KEY" in body


def test_only_model_gateway_process_inherits_the_external_model_secret() -> None:
    start = _script("demo-start.ps1")
    bootstrap = _script("demo-bootstrap.ps1")
    assert "LAUNCHSCOPE_MODEL_UPSTREAM_API_KEY', $gatewayUpstreamApiKey" in start
    assert "LAUNCHSCOPE_MODEL_UPSTREAM_API_KEY', $null" in start
    assert "issue-intake-token" in start
    assert 'http://127.0.0.1:$($env:LAUNCHSCOPE_MODEL_GATEWAY_PORT)/v1/intake' in start
    assert "LAUNCHSCOPE_INTAKE_MODEL_API_KEY', $intakeGatewayToken" in start
    assert "LAUNCHSCOPE_INTAKE_MODEL_API_KEY', $null" in start
    assert "$env:AGENTTEAMS_LLM_API_KEY = 'lsmg.v2.unassigned'" in bootstrap
    assert "$env:AGENTTEAMS_LLM_API_KEY = $env:AGENTTEAMS_MODEL_API_KEY" not in bootstrap


def test_stop_and_reset_are_scope_and_state_guarded() -> None:
    stop = _script("demo-stop.ps1")
    reset = _script("demo-reset.ps1")
    assert "started_at" in stop and "executable" in stop and "Stop-Process" in stop
    assert "Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue" in stop
    assert "[switch]$KeepInfrastructure" in stop
    assert "Assert-LocalDemo -RequireForce" in reset
    for unsafe in ("RUNNING", "NEEDS_ATTENTION", "SUBMISSION_UNKNOWN", "CLAIMED"):
        assert unsafe in reset
    assert "down --volumes --remove-orphans" in reset
    assert "Get-AgentTeamsRenderedResourceName" in reset
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
    assert values["LAUNCHSCOPE_PROVIDER_COST_MODE"] == "TOKEN_ONLY"
    assert values["LAUNCHSCOPE_REQUIRE_PROVIDER_USAGE"] == "true"
    assert values["LAUNCHSCOPE_DEMO_DEFAULT_WORKSPACE_FILE"] == ".demo/default-workspace.json"
    assert values["LAUNCHSCOPE_REPORT_V3_ENABLED"] == "true"


def test_m7b_runtime_export_is_generation_aware_and_redacted() -> None:
    body = _script("export-m7b-runtime-evidence.ps1")
    assert "Get-AgentTeamsWorkerResourceMap" in body
    assert "Get-AgentTeamsWorkerMap" in body
    assert "/api/models/active?scope=effective" in body
    assert "/api/token-usage" in body
    assert "by_model" in body and "by_provider" in body
    assert "resource_sha256" in body and "package_sha256" in body
    assert "provider_cost_mode" in body
    assert "matrix_user_id" in body and "room_id" in body
    assert "AGENTTEAMS_MODEL_API_KEY" not in body
    assert "AGENTTEAMS_HUMAN_ACCESS_TOKEN" not in body
