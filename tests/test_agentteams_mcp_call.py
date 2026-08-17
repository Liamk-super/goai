from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from urllib.parse import unquote

import pytest


def _module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "agentteams" / "launchscope_mcp_call.py"
    spec = importlib.util.spec_from_file_location("launchscope_mcp_call", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_session(path: Path, assignments: list[dict[str, object]]) -> None:
    content = []
    for index, assignment in enumerate(assignments):
        content.append(
            [
                {
                    "role": "user",
                    "timestamp": f"2026-08-14T12:00:{index:02d}Z",
                    "content": [{"type": "text", "text": f"@worker {json.dumps(assignment)}"}],
                },
                [],
            ]
        )
    path.write_text(json.dumps({"agent": {"memory": {"content": content}}}), encoding="utf-8")


def test_extracts_token_from_latest_authoritative_handoff_for_current_agent(tmp_path: Path) -> None:
    module = _module()
    _write_session(
        tmp_path / "room.json",
        [
            {
                "message_type": "AgentHandoffV4",
                "agent_code": "product-engineering",
                "dispatch_epoch": 4,
                "context_token": "h4.old.signature",
            },
            {
                "message_type": "AgentHandoffV4",
                "agent_code": "product-engineering",
                "dispatch_epoch": 5,
                "context_token": "h4.current.signature",
            },
            {
                "message_type": "AgentHandoffV4",
                "agent_code": "business-investment",
                "dispatch_epoch": 6,
                "context_token": "h4.other-worker.signature",
            },
        ],
    )

    assignment = module.load_latest_assignment(tmp_path, "product-engineering")

    assert assignment["dispatch_epoch"] == 5
    assert assignment["context_token"] == "h4.current.signature"


def test_agent_code_is_derived_from_worker_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    monkeypatch.setenv("AGENTTEAMS_WORKER_NAME", "launchscope-business-investment-v6-live")

    assert module.current_agent_code() == "business-investment"

    monkeypatch.setenv("AGENTTEAMS_WORKER_NAME", "unrelated-worker")
    with pytest.raises(RuntimeError, match="current LaunchScope Agent code"):
        module.current_agent_code()


def test_build_args_injects_token_and_rejects_caller_override() -> None:
    module = _module()

    assert json.loads(module.build_args('{"unit_refs":[],"purpose":"review"}', "signed")) == {
        "unit_refs": [],
        "purpose": "review",
        "context_token": "signed",
    }
    with pytest.raises(ValueError, match="must not include context_token"):
        module.build_args('{"context_token":"model-value"}', "signed")


def test_fetches_current_assignment_from_worker_matrix_when_live_session_is_not_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    config_path = tmp_path / "openclaw.json"
    config_path.write_text(
        json.dumps(
            {
                "channels": {
                    "matrix": {
                        "homeserver": "http://matrix.internal",
                        "accessToken": "worker-token",
                        "dm": {"allowFrom": ["@coordinator:matrix.internal"]},
                        "groupAllowFrom": ["@coordinator:matrix.internal"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    current = {
        "message_type": "AgentHandoffV4",
        "agent_code": "product-engineering",
        "dispatch_epoch": 6,
        "context_token": "h4.current.signature",
    }
    forged_newer = {
        **current,
        "dispatch_epoch": 7,
        "context_token": "h4.forged.signature",
    }

    class Response:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int = -1) -> bytes:
            return json.dumps(self.payload).encode()

    def urlopen(request: object, timeout: int) -> Response:
        assert timeout == 20
        url = str(request.full_url)  # type: ignore[attr-defined]
        authorization = request.headers.get("Authorization")  # type: ignore[attr-defined]
        assert authorization == "Bearer worker-token"
        if url.endswith("/_matrix/client/v3/joined_rooms"):
            return Response({"joined_rooms": ["!direct:matrix.internal"]})
        assert unquote(url).endswith("/rooms/!direct:matrix.internal/messages?dir=b&limit=100")
        return Response(
            {
                "chunk": [
                    {
                        "origin_server_ts": 200,
                        "sender": "@product-worker:matrix.internal",
                        "content": {"body": json.dumps(forged_newer)},
                    },
                    {
                        "origin_server_ts": 100,
                        "sender": "@coordinator:matrix.internal",
                        "content": {"body": f"@worker {json.dumps(current)}"},
                    },
                ]
            }
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)

    assignment = module.load_latest_matrix_assignment(config_path, "product-engineering")

    assert assignment["dispatch_epoch"] == 6
    assert assignment["context_token"] == "h4.current.signature"


def test_fetches_current_assignment_from_worker_runtime_matrix_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    monkeypatch.setenv("AGENTTEAMS_MATRIX_URL", "http://matrix.internal")
    monkeypatch.setenv("AGENTTEAMS_WORKER_MATRIX_TOKEN", "worker-token")
    monkeypatch.setenv("AGENTTEAMS_WORKER_ROOM_ID", "!direct:matrix.internal")
    assignment = {
        "message_type": "AgentHandoffV4",
        "agent_code": "user-evidence",
        "dispatch_epoch": 8,
        "context_token": "h4.current.signature",
    }

    class Response:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int = -1) -> bytes:
            return json.dumps(self.payload).encode()

    def urlopen(request: object, timeout: int) -> Response:
        assert timeout == 20
        url = unquote(str(request.full_url))  # type: ignore[attr-defined]
        assert request.headers.get("Authorization") == "Bearer worker-token"  # type: ignore[attr-defined]
        if url.endswith("/_matrix/client/v3/account/whoami"):
            return Response({"user_id": "@user-worker:matrix.internal"})
        assert url.endswith("/rooms/!direct:matrix.internal/messages?dir=b&limit=100")
        return Response(
            {
                "chunk": [
                    {
                        "origin_server_ts": 300,
                        "sender": "@user-worker:matrix.internal",
                        "content": {"body": json.dumps({**assignment, "dispatch_epoch": 9})},
                    },
                    {
                        "origin_server_ts": 200,
                        "sender": "@coordinator:matrix.internal",
                        "content": {"body": f"@worker {json.dumps(assignment)}"},
                    },
                ]
            }
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)

    current = module.load_current_worker_assignment("user-evidence")

    assert current["dispatch_epoch"] == 8
    assert current["context_token"] == "h4.current.signature"


def test_runtime_room_without_ticket_falls_back_to_trusted_worker_matrix_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    config_path = tmp_path / "openclaw.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AGENTTEAMS_MATRIX_URL", "http://matrix.internal")
    monkeypatch.setenv("AGENTTEAMS_WORKER_MATRIX_TOKEN", "worker-token")
    monkeypatch.setenv("AGENTTEAMS_WORKER_ROOM_ID", "!team:matrix.internal")
    monkeypatch.setattr(module, "worker_config_paths", lambda _worker_name: (config_path,))
    monkeypatch.setattr(
        module,
        "load_current_worker_assignment",
        lambda _agent_code: (_ for _ in ()).throw(RuntimeError("no authoritative LaunchScope assignment")),
    )
    expected = {
        "message_type": "AgentHandoffV4",
        "agent_code": "product-engineering",
        "dispatch_epoch": 7,
        "context_token": "h4.trusted.signature",
    }
    monkeypatch.setattr(module, "load_latest_matrix_assignment", lambda _path, _agent_code: expected)

    current = module.load_authoritative_assignment(
        tmp_path / "sessions",
        "launchscope-product-engineering-v6-live",
        "product-engineering",
    )

    assert current == expected


def test_mcporter_command_uses_the_managed_worker_configuration(tmp_path: Path) -> None:
    module = _module()
    config_path = tmp_path / "config" / "mcporter.json"
    config_path.parent.mkdir()
    config_path.write_text("{}", encoding="utf-8")

    command = module.build_mcporter_command(config_path, "launchscope-context", "launchscope-context.get.v2", "{}")

    assert command == [
        "mcporter",
        "--config",
        str(config_path),
        "call",
        "--server",
        "launchscope-context",
        "--tool",
        "launchscope-context.get.v2",
        "--args",
        "{}",
        "--output",
        "json",
    ]


def test_required_material_requests_cover_each_required_scope_once() -> None:
    module = _module()
    requests = module.required_material_requests(
        {
            "material_catalog": [
                {
                    "material_id": "material-a",
                    "unit_ref": "material-unit:a@digest",
                    "unit_type": "SECTION",
                    "summary": "Pages 1-10",
                },
                {
                    "material_id": "material-a",
                    "unit_ref": "material-unit:a2@digest",
                    "unit_type": "PAGE",
                    "summary": "product evidence",
                },
                {
                    "material_id": "material-b",
                    "unit_ref": "material-unit:b@digest",
                    "unit_type": "PARAGRAPH",
                    "summary": "current product description",
                },
            ],
            "material_scope": [
                {
                    "material_id": "material-a",
                    "required": True,
                    "unit_refs": ["material-unit:a@digest", "material-unit:a2@digest"],
                },
                {
                    "material_id": "material-b",
                    "required": True,
                    "unit_refs": ["material-unit:b@digest"],
                },
                {
                    "material_id": "optional",
                    "required": False,
                    "unit_refs": ["material-unit:c@digest"],
                },
            ]
        }
    )

    assert requests == [
        {
            "unit_refs": ["material-unit:a2@digest"],
            "purpose": "Read assigned content units from required material material-a before specialist evaluation.",
        },
        {
            "unit_refs": ["material-unit:b@digest"],
            "purpose": "Read assigned content units from required material material-b before specialist evaluation.",
        },
    ]


def test_required_material_requests_balance_eight_content_units_across_materials() -> None:
    module = _module()
    context = {
        "material_catalog": [
            {
                "material_id": f"material-{material_index}",
                "unit_ref": f"material-unit:{material_index}-{unit_index}@digest",
                "unit_type": "PAGE",
                "summary": f"content {material_index}-{unit_index}",
            }
            for material_index in range(4)
            for unit_index in range(4)
        ],
        "material_scope": [
            {
                "material_id": f"material-{material_index}",
                "required": True,
                "unit_refs": [
                    f"material-unit:{material_index}-{unit_index}@digest" for unit_index in range(4)
                ],
            }
            for material_index in range(4)
        ],
    }

    requests = module.required_material_requests(context)

    assert len(requests) == 4
    assert [len(item["unit_refs"]) for item in requests] == [2, 2, 2, 2]
    assert sum(len(item["unit_refs"]) for item in requests) == 8


def test_required_material_preflight_keeps_every_material_within_response_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    context = {
        "tenant_id": "tenant-1",
        "run_id": "run-1",
        "task_id": "task-1",
        "project_id": "project-1",
        "product_version_id": "version-1",
        "product_title": "CreaTrades",
        "standard_version": "2.2",
        "report_preferences": {"locale": "zh-CN"},
        "product_profile": {"one_line_value_claim": "一体化电商内容生产"},
        "requirement_brief": {"normalized_goal": "验证重复使用与付费"},
        "assigned_task_ticket": {"analysis_dimensions": ["demand", "retention"]},
        "material_catalog": [{"summary": "x" * 20_000}],
        "material_scope": [
            {
                "material_id": f"material-{index}",
                "required": True,
                "reason": f"required-{index}",
                "unit_refs": [f"material-unit:{index}@digest"],
            }
            for index in range(5)
        ],
    }
    calls = 0
    commands: list[list[str]] = []

    def captured(command: list[str], _timeout_seconds: int) -> dict[str, object]:
        nonlocal calls
        calls += 1
        commands.append(command)
        if calls == 1:
            return context
        index = calls - 2
        return {
            "receipt_id": f"receipt-{index}",
            "units": [
                {
                    "unit_ref": f"material-unit:{index}@digest",
                    "evidence_id": f"evidence-{index}",
                    "content": "中文材料" * 20_000,
                    "locator": {"page": index + 1},
                    "source_locator": {"title": f"材料 {index}"},
                    "truncated": False,
                }
            ],
            "truncated": False,
        }

    monkeypatch.setattr(module, "_captured_mcporter_call", captured)

    result = module.read_required_materials(tmp_path / "mcporter.json", "signed", 1800)

    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert len(encoded) <= 60_000
    assert "context" not in result
    assert result["runtime_context"] == {
        "tenant_id": "tenant-1",
        "run_id": "run-1",
        "task_id": "task-1",
        "project_id": "project-1",
        "product_version_id": "version-1",
        "product_title": "CreaTrades",
        "standard_version": "2.2",
        "report_preferences": {"locale": "zh-CN"},
        "product_profile": {"one_line_value_claim": "一体化电商内容生产"},
        "requirement_brief": {"normalized_goal": "验证重复使用与付费"},
        "assigned_task_ticket": {"analysis_dimensions": ["demand", "retention"]},
        "authorized_urls": [],
        "evidence_refs": [],
    }
    assert [item["material_id"] for item in result["required_materials"]] == [
        f"material-{index}" for index in range(5)
    ]
    assert len(result["material_reads"]) == 5
    assert all(item["units"][0]["content"] for item in result["material_reads"])
    assert commands[0][commands[0].index("--server") + 1] == "launchscope-context"
    assert all(command[command.index("--server") + 1] == "material" for command in commands[1:])
