from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _decode_assignment(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    if start < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    if not str(value.get("message_type", "")).startswith("AgentHandoffV"):
        return None
    token = value.get("context_token")
    epoch = value.get("dispatch_epoch")
    if not isinstance(token, str) or not token.startswith(("h2.", "h3.", "h4.")):
        return None
    if not isinstance(epoch, int) or epoch < 0:
        return None
    return value


_AGENT_CODES = (
    "evaluation-manager",
    "user-evidence",
    "product-engineering",
    "business-investment",
    "evidence-auditor",
)


def current_agent_code() -> str:
    worker_name = os.getenv("AGENTTEAMS_WORKER_NAME", "")
    for agent_code in _AGENT_CODES:
        if re.fullmatch(rf"launchscope-{re.escape(agent_code)}-v\d+-live", worker_name):
            return agent_code
    raise RuntimeError("current LaunchScope Agent code cannot be derived from AGENTTEAMS_WORKER_NAME")


def load_latest_assignment(session_dir: Path, agent_code: str) -> dict[str, Any]:
    candidates: list[tuple[str, int, dict[str, Any]]] = []
    for path in session_dir.glob("*.json"):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            content = document["agent"]["memory"]["content"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            continue
        for pair in content if isinstance(content, list) else []:
            if not isinstance(pair, list) or not pair or not isinstance(pair[0], dict):
                continue
            message = pair[0]
            if message.get("role") != "user":
                continue
            blocks = message.get("content")
            for block in blocks if isinstance(blocks, list) else []:
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue
                assignment = _decode_assignment(str(block.get("text", "")))
                if assignment is not None and assignment.get("agent_code") == agent_code:
                    candidates.append(
                        (
                            str(message.get("timestamp", "")),
                            int(assignment["dispatch_epoch"]),
                            assignment,
                        )
                    )
    if not candidates:
        raise RuntimeError("no authoritative LaunchScope assignment is available in the current session")
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def _matrix_json(base_url: str, access_token: str, path: str, *, timeout: int = 20) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read(2_000_001)
    if len(payload) > 2_000_000:
        raise RuntimeError("Matrix response exceeded the 2 MB read limit")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise RuntimeError("Matrix returned a non-object response")
    return value


def load_latest_matrix_assignment(config_path: Path, agent_code: str) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    try:
        matrix = config["channels"]["matrix"]
        base_url = str(matrix["homeserver"])
        access_token = str(matrix["accessToken"])
    except (KeyError, TypeError) as exc:
        raise RuntimeError("worker Matrix configuration is incomplete") from exc
    dm = matrix.get("dm") if isinstance(matrix, dict) else None
    allowed_senders = {
        str(sender)
        for sender in [
            *(dm.get("allowFrom", []) if isinstance(dm, dict) else []),
            *(matrix.get("groupAllowFrom", []) if isinstance(matrix, dict) else []),
        ]
        if isinstance(sender, str) and sender
    }
    if not base_url or not access_token or not allowed_senders:
        raise RuntimeError("worker Matrix configuration is incomplete")
    joined = _matrix_json(base_url, access_token, "/_matrix/client/v3/joined_rooms")
    room_ids = joined.get("joined_rooms")
    if not isinstance(room_ids, list):
        raise RuntimeError("Matrix joined_rooms response is malformed")
    candidates: list[tuple[int, dict[str, Any]]] = []
    for room_id in room_ids:
        if not isinstance(room_id, str):
            continue
        room = urllib.parse.quote(room_id, safe="")
        messages = _matrix_json(
            base_url,
            access_token,
            f"/_matrix/client/v3/rooms/{room}/messages?dir=b&limit=100",
        )
        chunk = messages.get("chunk")
        for event in chunk if isinstance(chunk, list) else []:
            if not isinstance(event, dict) or event.get("sender") not in allowed_senders:
                continue
            content = event.get("content")
            if not isinstance(content, dict):
                continue
            assignment = _decode_assignment(str(content.get("body", "")))
            if assignment is not None and assignment.get("agent_code") == agent_code:
                candidates.append((int(str(event.get("origin_server_ts", 0))), assignment))
    if not candidates:
        raise RuntimeError("no authoritative LaunchScope assignment is available in the worker Matrix rooms")
    return max(candidates, key=lambda item: item[0])[1]


def load_current_worker_assignment(agent_code: str) -> dict[str, Any]:
    try:
        base_url = os.environ["AGENTTEAMS_MATRIX_URL"]
        access_token = os.environ["AGENTTEAMS_WORKER_MATRIX_TOKEN"]
        room_id = os.environ["AGENTTEAMS_WORKER_ROOM_ID"]
    except KeyError as exc:
        raise RuntimeError("worker Matrix runtime environment is incomplete") from exc
    whoami = _matrix_json(base_url, access_token, "/_matrix/client/v3/account/whoami")
    worker_mxid = str(whoami.get("user_id", ""))
    if not worker_mxid:
        raise RuntimeError("Matrix whoami returned no user_id")
    room = urllib.parse.quote(room_id, safe="")
    messages = _matrix_json(
        base_url,
        access_token,
        f"/_matrix/client/v3/rooms/{room}/messages?dir=b&limit=100",
    )
    chunk = messages.get("chunk")
    candidates: list[tuple[int, dict[str, Any]]] = []
    for event in chunk if isinstance(chunk, list) else []:
        if not isinstance(event, dict) or event.get("sender") in {None, "", worker_mxid}:
            continue
        content = event.get("content")
        if not isinstance(content, dict):
            continue
        assignment = _decode_assignment(str(content.get("body", "")))
        if assignment is not None and assignment.get("agent_code") == agent_code:
            candidates.append((int(str(event.get("origin_server_ts", 0))), assignment))
    if not candidates:
        raise RuntimeError("no authoritative LaunchScope assignment is available in the worker Matrix room")
    return max(candidates, key=lambda item: item[0])[1]


def worker_root_paths(worker_name: str) -> tuple[Path, ...]:
    return tuple(
        dict.fromkeys(
            (
                Path.home() / ".copaw-worker" / worker_name,
                Path("/root/.copaw-worker") / worker_name,
            )
        )
    )


def worker_config_paths(worker_name: str) -> tuple[Path, ...]:
    return tuple(root / "openclaw.json" for root in worker_root_paths(worker_name))


def mcporter_config_path(worker_name: str) -> Path:
    for root in worker_root_paths(worker_name):
        path = root / "config" / "mcporter.json"
        if path.is_file():
            return path
    raise RuntimeError("managed Worker MCP configuration is unavailable")


def load_authoritative_assignment(session_dir: Path, worker_name: str, agent_code: str) -> dict[str, Any]:
    failures: list[RuntimeError] = []
    if all(
        os.getenv(name)
        for name in (
            "AGENTTEAMS_MATRIX_URL",
            "AGENTTEAMS_WORKER_MATRIX_TOKEN",
            "AGENTTEAMS_WORKER_ROOM_ID",
        )
    ):
        try:
            return load_current_worker_assignment(agent_code)
        except RuntimeError as exc:
            failures.append(exc)
    for config_path in worker_config_paths(worker_name):
        if not config_path.is_file():
            continue
        try:
            return load_latest_matrix_assignment(config_path, agent_code)
        except RuntimeError as exc:
            failures.append(exc)
    try:
        return load_latest_assignment(session_dir, agent_code)
    except RuntimeError as exc:
        failures.append(exc)
    raise RuntimeError("no authoritative LaunchScope assignment is available") from failures[-1]


def build_args(raw_args: str, context_token: str) -> str:
    value = json.loads(raw_args)
    if not isinstance(value, dict):
        raise ValueError("MCP arguments must be a JSON object")
    if "context_token" in value:
        raise ValueError("MCP arguments must not include context_token")
    value["context_token"] = context_token
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def build_mcporter_command(config_path: Path, server: str, tool: str, encoded_args: str) -> list[str]:
    return [
        "mcporter",
        "--config",
        str(config_path),
        "call",
        "--server",
        server,
        "--tool",
        tool,
        "--args",
        encoded_args,
        "--output",
        "json",
    ]


def required_material_requests(context: dict[str, Any]) -> list[dict[str, object]]:
    catalog = {
        str(item.get("unit_ref")): item
        for item in context.get("material_catalog", [])
        if isinstance(item, dict) and item.get("unit_ref")
    }
    material_order: list[str] = []
    candidates_by_material: dict[str, list[str]] = {}
    scopes = context.get("material_scope")
    for scope in scopes if isinstance(scopes, list) else []:
        if not isinstance(scope, dict) or scope.get("required") is not True:
            continue
        material_id = str(scope.get("material_id", ""))
        unit_refs = scope.get("unit_refs")
        if not material_id or not isinstance(unit_refs, list) or not unit_refs:
            continue
        if material_id not in candidates_by_material:
            material_order.append(material_id)
            candidates_by_material[material_id] = []
        scoped_refs = [str(value) for value in unit_refs]
        readable_refs = [
            value
            for value in scoped_refs
            if value in catalog
            and str(catalog[value].get("unit_type")) not in {"DOCUMENT", "SECTION", "IMAGE"}
            and bool(str(catalog[value].get("summary", "")).strip())
        ]
        for value in readable_refs or scoped_refs:
            if value not in candidates_by_material[material_id]:
                candidates_by_material[material_id].append(value)
    if len(material_order) > 8:
        raise RuntimeError("required materials exceed the eight-unit specialist read budget")
    selected_by_material = {material_id: [] for material_id in material_order}
    while sum(len(values) for values in selected_by_material.values()) < 8:
        added = False
        for material_id in material_order:
            selected = selected_by_material[material_id]
            candidates = candidates_by_material[material_id]
            if len(selected) >= len(candidates):
                continue
            selected.append(candidates[len(selected)])
            added = True
            if sum(len(values) for values in selected_by_material.values()) == 8:
                break
        if not added:
            break
    return [
        {
            "unit_refs": selected_by_material[material_id],
            "purpose": (
                f"Read assigned content units from required material {material_id} before specialist evaluation."
            ),
        }
        for material_id in material_order
    ]


def _captured_mcporter_call(command: list[str], timeout_seconds: int) -> dict[str, Any]:
    completed = subprocess.run(command, check=False, timeout=timeout_seconds, capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError("managed MCP call failed")
    try:
        value = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("managed MCP call returned malformed JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("managed MCP call returned a non-object result")
    if value.get("isError") is True:
        content = value.get("content")
        message = "managed MCP tool returned an error"
        if isinstance(content, list) and content and isinstance(content[0], dict):
            message = str(content[0].get("text", message))
        raise RuntimeError(message)
    return value


def _truncate_utf8(value: object, max_bytes: int) -> tuple[str, bool]:
    encoded = str(value or "").encode("utf-8")
    if len(encoded) <= max_bytes:
        return encoded.decode("utf-8"), False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def _compact_material_read(result: dict[str, Any]) -> dict[str, object]:
    compact_units: list[dict[str, object]] = []
    units = result.get("units")
    for unit in units if isinstance(units, list) else []:
        if not isinstance(unit, dict):
            continue
        content, content_truncated = _truncate_utf8(unit.get("content"), 6_000)
        visual_summary, visual_truncated = _truncate_utf8(unit.get("visual_summary"), 1_500)
        compact_units.append(
            {
                "unit_ref": unit.get("unit_ref"),
                "evidence_id": unit.get("evidence_id"),
                "content": content,
                "visual_summary": visual_summary or None,
                "locator": unit.get("locator"),
                "source_locator": unit.get("source_locator"),
                "truncated": bool(unit.get("truncated")) or content_truncated or visual_truncated,
            }
        )
    return {
        "receipt_id": result.get("receipt_id"),
        "units": compact_units,
        "truncated": bool(result.get("truncated")) or any(bool(unit["truncated"]) for unit in compact_units),
    }


def specialist_runtime_context(context: dict[str, Any]) -> dict[str, object]:
    evidence_refs = context.get("evidence_refs")
    bounded_refs = []
    for item in evidence_refs if isinstance(evidence_refs, list) else []:
        if not isinstance(item, dict):
            continue
        bounded_refs.append({**item, "summary": str(item.get("summary", ""))[:200]})
        if len(bounded_refs) == 12:
            break
    return {
        "tenant_id": context.get("tenant_id"),
        "run_id": context.get("run_id"),
        "task_id": context.get("task_id"),
        "project_id": context.get("project_id"),
        "product_version_id": context.get("product_version_id"),
        "product_title": context.get("product_title"),
        "standard_version": context.get("standard_version"),
        "report_preferences": context.get("report_preferences") or {},
        "product_profile": context.get("product_profile") or {},
        "requirement_brief": context.get("requirement_brief"),
        "assigned_task_ticket": context.get("assigned_task_ticket"),
        "authorized_urls": context.get("authorized_urls") or [],
        "evidence_refs": bounded_refs,
    }


def read_required_materials(
    config_path: Path,
    context_token: str,
    timeout_seconds: int,
) -> dict[str, object]:
    context = _captured_mcporter_call(
        build_mcporter_command(
            config_path,
            "launchscope-context",
            "launchscope-context.get.v2",
            build_args("{}", context_token),
        ),
        timeout_seconds,
    )
    reads: list[dict[str, object]] = []
    for request in required_material_requests(context):
        result = _captured_mcporter_call(
            build_mcporter_command(
                config_path,
                "material",
                "material.read.v1",
                build_args(json.dumps(request, ensure_ascii=False), context_token),
            ),
            timeout_seconds,
        )
        reads.append(_compact_material_read(result))
    scopes = context.get("material_scope")
    required_materials = [
        {
            "material_id": scope.get("material_id"),
            "reason": scope.get("reason"),
            "unit_ref": scope.get("unit_refs", [None])[0],
        }
        for scope in (scopes if isinstance(scopes, list) else [])
        if isinstance(scope, dict) and scope.get("required") is True
    ]
    output: dict[str, object] = {
        "runtime_context": specialist_runtime_context(context),
        "required_materials": required_materials,
        "material_reads": reads,
    }
    if len(json.dumps(output, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 60_000:
        raise RuntimeError("bounded required-material preflight exceeded its response budget")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server")
    parser.add_argument("--tool")
    parser.add_argument("--args-json", default="{}")
    parser.add_argument("--session-dir", type=Path, default=Path.cwd() / "sessions")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--read-required-materials", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.timeout_seconds <= 3600:
        raise ValueError("timeout must be between 1 and 3600 seconds")
    if not args.read_required_materials and (not args.server or not args.tool):
        parser.error("--server and --tool are required unless --read-required-materials is used")
    agent_code = current_agent_code()
    worker_name = os.environ["AGENTTEAMS_WORKER_NAME"]
    assignment = load_authoritative_assignment(
        args.session_dir,
        worker_name,
        agent_code,
    )
    config_path = mcporter_config_path(worker_name)
    if args.read_required_materials:
        result = read_required_materials(config_path, str(assignment["context_token"]), args.timeout_seconds)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    encoded_args = build_args(args.args_json, str(assignment["context_token"]))
    completed = subprocess.run(
        build_mcporter_command(config_path, args.server, args.tool, encoded_args),
        check=False,
        timeout=args.timeout_seconds,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
