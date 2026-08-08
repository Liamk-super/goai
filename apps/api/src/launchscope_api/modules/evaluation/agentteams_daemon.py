"""RocketMQ dispatch-to-Matrix and Matrix result listener processes."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session

from launchscope_api.infrastructure.db.schema import agentteams_run_binding
from launchscope_api.infrastructure.db.session import DatabaseSettings, create_database_engine, session_factory
from launchscope_api.infrastructure.messaging.inbox import InboxConsumer
from launchscope_domain.events import EventEnvelope
from launchscope_domain.value_objects import TenantScope
from launchscope_orchestrator.agentteams_bridge import AgentTeamsBridge

_TRANSIENT_RECEIVE_MARKERS = (
    "DEADLINE_EXCEEDED",
    "no new message",
    "NO_NEW_MESSAGE",
    "MESSAGE_NOT_FOUND",
    "UNAVAILABLE",
    "Stream removed",
)


def _is_transient_receive_error(exc: BaseException) -> bool:
    """An idle or briefly unavailable RocketMQ long-poll must not kill the daemon."""
    text = f"{type(exc).__name__}: {exc}"
    return any(marker.lower() in text.lower() for marker in _TRANSIENT_RECEIVE_MARKERS)


class MatrixHumanClient:
    def __init__(self, base_url: str, access_token: str, agent_mxids: dict[str, str]) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.agent_mxids = agent_mxids

    def send_assignment(self, event: EventEnvelope) -> tuple[str, str]:
        assignment = AgentTeamsBridge().assignment_from_dispatch(event.to_dict())
        agent_code = str(assignment.body["agent_code"])
        try:
            agent_mxid = self.agent_mxids[agent_code]
        except KeyError as exc:
            raise RuntimeError(f"no Matrix identity configured for Agent {agent_code}") from exc
        create_payload = json.dumps({
            "invite": [agent_mxid], "is_direct": True, "preset": "trusted_private_chat",
            "name": f"LaunchScope {agent_code} task {event.task_id}",
        }, separators=(",", ":")).encode("utf-8")
        create_request = urllib.request.Request(
            f"{self.base_url}/_matrix/client/v3/createRoom", data=create_payload, method="POST",
            headers={"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(create_request, timeout=20) as response:
            created = json.loads(response.read(65_537))
        room_id = str(created.get("room_id", ""))
        if not room_id:
            raise RuntimeError("Matrix task-room creation returned no room_id")
        transaction_id = urllib.parse.quote(str(event.event_id), safe="")
        room = urllib.parse.quote(room_id, safe="")
        url = f"{self.base_url}/_matrix/client/v3/rooms/{room}/send/m.room.message/{transaction_id}"
        payload = json.dumps({
            "msgtype": "m.text", "body": json.dumps(assignment.body, separators=(",", ":")),
            "launchscope_assignment": assignment.body,
        }, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            url, data=payload, method="PUT",
            headers={"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read(65_537))
        event_id = str(result.get("event_id", ""))
        if not event_id:
            raise RuntimeError("Matrix send returned no event_id")
        return room_id, event_id


def _event(value: dict[str, object]) -> EventEnvelope:
    payload = value.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("RocketMQ event payload must be an object")
    return EventEnvelope(
        event_type=str(value["event_type"]), event_id=UUID(str(value["event_id"])),
        tenant_id=UUID(str(value["tenant_id"])), run_id=UUID(str(value["run_id"])),
        task_id=UUID(str(value["task_id"])) if value.get("task_id") else None,
        correlation_id=UUID(str(value["correlation_id"])),
        causation_id=UUID(str(value["causation_id"])) if value.get("causation_id") else None,
        idempotency_key=str(value["idempotency_key"]), schema_version=str(value["schema_version"]),
        occurred_at=datetime.fromisoformat(str(value["occurred_at"]).replace("Z", "+00:00")), payload=payload,
    )


def dispatch_bridge() -> None:
    from rocketmq import (  # type: ignore[import-untyped]
        ClientConfiguration,
        Credentials,
        FilterExpression,
        SimpleConsumer,
    )

    settings = DatabaseSettings.from_env()
    engine = create_database_engine(
        settings.url, application_role=os.getenv("LAUNCHSCOPE_DB_ROLE", "launchscope_runtime")
    )
    sessions = session_factory(engine)
    topic = os.getenv("LAUNCHSCOPE_ROCKETMQ_TOPIC", "launchscope-evaluation-events-v1")
    consumer = SimpleConsumer(
        ClientConfiguration(os.environ["ROCKETMQ_ENDPOINTS"], Credentials()),
        os.getenv("LAUNCHSCOPE_ROCKETMQ_CONSUMER_GROUP", "launchscope-agentteams-bridge-v1"),
        {topic: FilterExpression("evaluation.task.ready.v1")}, await_duration=20,
    )
    directory = json.loads(os.environ["LAUNCHSCOPE_MATRIX_AGENT_DIRECTORY_JSON"])
    if not isinstance(directory, dict):
        raise RuntimeError("LAUNCHSCOPE_MATRIX_AGENT_DIRECTORY_JSON must be an object")
    agent_mxids = {str(agent_code): str(mxid) for mxid, agent_code in directory.items()}
    matrix = MatrixHumanClient(
        os.environ["AGENTTEAMS_MATRIX_URL"], os.environ["AGENTTEAMS_HUMAN_ACCESS_TOKEN"],
        agent_mxids,
    )
    consumer.startup()
    try:
        while True:
            try:
                batch = consumer.receive(16, 30)
            except Exception as exc:  # noqa: BLE001
                # An idle RocketMQ long-poll surfaces as DEADLINE_EXCEEDED / no-new-message.
                # That is normal back-pressure, not a dispatch failure: keep consuming.
                if not _is_transient_receive_error(exc):
                    raise
                print(f"dispatch-bridge: idle receive ({type(exc).__name__}); continuing", file=sys.stderr)
                time.sleep(1)
                continue
            for message in batch:
                event = _event(json.loads(message.body))
                scope = TenantScope(UUID(str(event.tenant_id)))
                with sessions() as session:
                    def handle(db_session: Session, current: EventEnvelope) -> None:
                        assigned_room_id, _ = matrix.send_assignment(current)
                        leader_room_id = (
                            assigned_room_id
                            if current.payload.get("agent_code") == "evaluation-manager"
                            else os.environ["AGENTTEAMS_LEADER_ROOM_ID"]
                        )
                        db_session.execute(update(agentteams_run_binding).where(
                            agentteams_run_binding.c.tenant_id == current.tenant_id,
                            agentteams_run_binding.c.run_id == current.run_id,
                        ).values(
                            team_room_id=os.environ["AGENTTEAMS_TEAM_ROOM_ID"], leader_room_id=leader_room_id,
                            binding_status="TASKS_DISPATCHED",
                            updated_at=datetime.now().astimezone(),
                        ))

                    InboxConsumer(session, "agentteams-dispatch-bridge").consume_once(event, handle, scope=scope)
                consumer.ack(message)
    except KeyboardInterrupt:
        pass
    finally:
        consumer.shutdown()
        engine.dispose()


def _sync(base_url: str, token: str, since: str | None) -> dict[str, object]:
    query = urllib.parse.urlencode({"timeout": "20000", **({"since": since} if since else {})})
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/_matrix/client/v3/sync?{query}",
        headers={"Authorization": f"Bearer {token}"}, method="GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read(1_048_577))


def _handoff_content(content: object) -> dict[str, object] | None:
    if not isinstance(content, dict):
        return None
    candidate = content.get("launchscope_handoff")
    required = ("tenant_id", "run_id", "task_id", "agent_code", "status")
    if (
        isinstance(candidate, dict)
        and candidate.get("schema_version") == "1.0"
        and all(candidate.get(key) for key in required)
    ):
        return candidate
    body = content.get("body")
    if isinstance(body, str):
        candidate_body = body.strip()
        fenced_blocks = re.findall(r"```(?:json)?\s*\n([\s\S]*?)\n```", candidate_body, flags=re.IGNORECASE)
        if len(fenced_blocks) == 1:
            candidate_body = fenced_blocks[0].strip()
        if candidate_body.startswith("```"):
            lines = candidate_body.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                candidate_body = "\n".join(lines[1:-1]).strip()
        if not candidate_body.startswith("{"):
            return None
        try:
            parsed = json.loads(candidate_body)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict) and parsed.get("status") == "COMPLETED":
            parsed["status"] = "SUCCEEDED"
        if isinstance(parsed, dict) and parsed.get("risk") not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
            parsed["risk"] = "HIGH"
        if isinstance(parsed, dict) and isinstance(parsed.get("claims"), list):
            for claim in parsed["claims"]:
                if isinstance(claim, dict) and not claim.get("hypothesis") and not claim.get("evidence_ids"):
                    claim["hypothesis"] = True
        if isinstance(parsed, dict):
            expected_dimensions = {
                "product-engineering": "PRODUCT_IMPLEMENTATION",
                "user-evidence": "USER_USAGE",
                "business-investment": "BUSINESS_INVESTMENT",
                "geo-policy-trend": "GEO_POLICY_TREND",
                "evidence-auditor": "EVIDENCE_AUDIT",
            }
            expected_dimension = expected_dimensions.get(str(parsed.get("agent_code", "")))
            if expected_dimension is not None:
                parsed["dimension"] = expected_dimension
            if parsed.get("agent_code") == "geo-policy-trend" and isinstance(parsed.get("claims"), list):
                for claim in parsed["claims"]:
                    if isinstance(claim, dict):
                        for key in ("region", "fetched_at", "valid_until", "trend_signal"):
                            if not claim.get(key):
                                claim[key] = "UNKNOWN"
        return (
            parsed
            if isinstance(parsed, dict)
            and parsed.get("schema_version") == "1.0"
            and all(parsed.get(key) for key in required)
            else None
        )
    return None


def matrix_listener() -> None:
    base_url = os.environ["AGENTTEAMS_MATRIX_URL"]
    matrix_token = os.environ["AGENTTEAMS_HUMAN_ACCESS_TOKEN"]
    bridge_token = os.environ["LAUNCHSCOPE_AGENTTEAMS_BRIDGE_TOKEN"]
    ingress = os.getenv(
        "LAUNCHSCOPE_AGENTTEAMS_INGRESS_URL",
        "http://127.0.0.1:8100/api/v1/internal/agentteams/matrix-events",
    )
    cursor_path = Path(os.getenv("LAUNCHSCOPE_MATRIX_CURSOR_FILE", ".demo/run/matrix-next-batch.txt")).resolve()
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    since = cursor_path.read_text(encoding="utf-8").strip() if cursor_path.exists() else None
    while True:
        try:
            result = _sync(base_url, matrix_token, since)
            rooms = result.get("rooms", {})
            joined = rooms.get("join", {}) if isinstance(rooms, dict) else {}
            for room_id, room in joined.items() if isinstance(joined, dict) else ():
                timeline = room.get("timeline", {}) if isinstance(room, dict) else {}
                events = timeline.get("events", []) if isinstance(timeline, dict) else []
                for event in events if isinstance(events, list) else []:
                    if not isinstance(event, dict):
                        continue
                    handoff = _handoff_content(event.get("content"))
                    if handoff is None:
                        continue
                    tenant_id = str(handoff.get("tenant_id") or event.get("tenant_id") or "")
                    run_id, task_id = str(handoff.get("run_id", "")), str(handoff.get("task_id", ""))
                    if not tenant_id or not run_id or not task_id:
                        raise ValueError("Matrix handoff lacks tenant/run/task routing metadata")
                    body = json.dumps({
                        "event_id": event.get("event_id"), "room_id": room_id,
                        "sender": event.get("sender"), "content": handoff,
                    }, separators=(",", ":")).encode("utf-8")
                    request = urllib.request.Request(
                        ingress, data=body, method="POST",
                        headers={
                            "Authorization": f"Bearer {bridge_token}", "Content-Type": "application/json",
                            "X-LaunchScope-Tenant-Id": tenant_id, "X-LaunchScope-Run-Id": run_id,
                            "X-LaunchScope-Task-Id": task_id,
                        },
                    )
                    try:
                        with urllib.request.urlopen(request, timeout=30) as response:
                            response.read(65_537)
                    except urllib.error.HTTPError as exc:
                        if exc.code == 404:
                            print(
                                f"Skipping Matrix event {event.get('event_id')}: Run/Task no longer exists",
                                file=sys.stderr,
                            )
                            continue
                        if exc.code not in {400, 422}:
                            raise
                        failure = {
                            "schema_version": "1.0",
                            "tenant_id": tenant_id,
                            "run_id": run_id,
                            "task_id": task_id,
                            "agent_code": str(handoff.get("agent_code", "")),
                            "status": "BLOCKED",
                            "dimension": str(handoff.get("dimension", "CONTROL")),
                            "claims": [],
                            "evidence_refs": [],
                            "risk": "HIGH",
                            "confidence": 0.0,
                            "needs_human_approval": True,
                            "failure_class": "VALIDATION",
                            "next_action": (
                                "Agent output failed the frozen handoff contract; "
                                "inspect the immutable Matrix event."
                            ),
                            "audit_results": [],
                        }
                        failure_body = json.dumps({
                            "event_id": event.get("event_id"), "room_id": room_id,
                            "sender": event.get("sender"), "content": failure,
                        }, separators=(",", ":")).encode("utf-8")
                        failure_request = urllib.request.Request(
                            ingress, data=failure_body, method="POST", headers=dict(request.headers)
                        )
                        try:
                            with urllib.request.urlopen(failure_request, timeout=30) as response:
                                response.read(65_537)
                        except urllib.error.HTTPError as failure_exc:
                            if failure_exc.code == 404:
                                print(
                                    f"Skipping invalid Matrix event {event.get('event_id')}: Run/Task no longer exists",
                                    file=sys.stderr,
                                )
                                continue
                            raise
                        print(
                            f"Matrix event {event.get('event_id')} was persisted as structured VALIDATION failure",
                            file=sys.stderr,
                        )
            next_batch = str(result.get("next_batch", ""))
            if not next_batch:
                raise RuntimeError("Matrix sync returned no next_batch cursor")
            cursor_path.write_text(next_batch, encoding="utf-8")
            since = next_batch
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("dispatch-bridge", "matrix-listener"))
    args = parser.parse_args()
    dispatch_bridge() if args.mode == "dispatch-bridge" else matrix_listener()


if __name__ == "__main__":
    main()
