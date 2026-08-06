"""RocketMQ dispatch-to-Matrix and Matrix result listener processes."""

from __future__ import annotations

import argparse
import json
import os
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


class MatrixHumanClient:
    def __init__(self, base_url: str, room_id: str, access_token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.room_id = room_id
        self.access_token = access_token

    def send_assignment(self, event: EventEnvelope) -> tuple[str, str]:
        assignment = AgentTeamsBridge().assignment_from_dispatch(event.to_dict())
        transaction_id = urllib.parse.quote(str(event.event_id), safe="")
        room = urllib.parse.quote(self.room_id, safe="")
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
        return self.room_id, event_id


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
        {topic: FilterExpression("evaluation.run.dispatched.v1")}, await_duration=20,
    )
    matrix = MatrixHumanClient(
        os.environ["AGENTTEAMS_MATRIX_URL"], os.environ["AGENTTEAMS_TEAM_ROOM_ID"],
        os.environ["AGENTTEAMS_HUMAN_ACCESS_TOKEN"],
    )
    consumer.startup()
    try:
        while True:
            for message in consumer.receive(16, 30):
                event = _event(json.loads(message.body))
                scope = TenantScope(UUID(str(event.tenant_id)))
                with sessions() as session:
                    def handle(db_session: Session, current: EventEnvelope) -> None:
                        room_id, _ = matrix.send_assignment(current)
                        db_session.execute(update(agentteams_run_binding).where(
                            agentteams_run_binding.c.tenant_id == current.tenant_id,
                            agentteams_run_binding.c.run_id == current.run_id,
                        ).values(
                            team_room_id=room_id, binding_status="MANAGER_DISPATCHED",
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
    if isinstance(candidate, dict):
        return candidate
    body = content.get("body")
    if isinstance(body, str) and body.lstrip().startswith("{"):
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) and parsed.get("schema_version") == "1.0" else None
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
                    with urllib.request.urlopen(request, timeout=30) as response:
                        response.read(65_537)
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
