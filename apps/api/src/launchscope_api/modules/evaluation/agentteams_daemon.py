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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import create_engine, insert, select, update
from sqlalchemy.orm import Session

from launchscope_api.infrastructure.db.schema import (
    agentteams_run_binding,
    evaluation_run,
    run_execution_control,
    run_manifest,
    run_status_history,
    task,
)
from launchscope_api.infrastructure.db.session import (
    DatabaseSettings,
    create_database_engine,
    normalize_database_url,
    session_factory,
)
from launchscope_api.infrastructure.messaging.inbox import InboxConsumer
from launchscope_domain.events import EventEnvelope
from launchscope_domain.value_objects import TenantScope
from launchscope_orchestrator.agentteams_bridge import AgentTeamsBridge

from .agentteams_delivery import (
    AgentWorkerBusy,
    drain_worker_lease_for_delivery,
    due_worker_lease_renewal_ids,
    fail_worker_lease,
    mark_pause_stop_sent,
    pending_pause_stops,
    physical_worker_name,
    prepare_worker_lease,
    reconcile_expired_task_deliveries,
    reconcile_expired_undelivered_tasks,
    reconcile_stale_preparing_worker_leases,
    record_task_delivery,
    renew_worker_lease_credential,
)
from .agentteams_usage import configured_usage_reader
from .execution_control import RunExecutionPausedError, assert_run_active
from .model_capability import issue_delivery_capability
from .task_dispatch import provider_usage_required

_TRANSIENT_RECEIVE_MARKERS = (
    "DEADLINE_EXCEEDED",
    "no new message",
    "NO_NEW_MESSAGE",
    "MESSAGE_NOT_FOUND",
    "No topic route info in name server",
    "UNAVAILABLE",
    "Stream removed",
    "ReceiveMessageActivity.receiveMessage",
)

# ADR 0004 adds the additive minor 1.1 (status NEEDS_INPUT + information_requests[]).
# 1.0 producers stay accepted unchanged.
_HANDOFF_SCHEMA_VERSIONS = frozenset({"1.0", "1.1"})
_V4_MESSAGE_TYPES = frozenset(
    {
        "ManagerPlanV1",
        "ManagerPlanV2",
        "AgentHandoffV3",
        "AgentHandoffV4",
        "AuditResultV3",
        "AuditResultV4",
        "ManagerSynthesisV1",
        "ManagerSynthesisV2",
    }
)


def _is_transient_receive_error(exc: BaseException) -> bool:
    """An idle or briefly unavailable RocketMQ long-poll must not kill the daemon."""
    text = f"{type(exc).__name__}: {exc}"
    return any(marker.lower() in text.lower() for marker in _TRANSIENT_RECEIVE_MARKERS)


class MatrixHumanClient:
    def __init__(
        self,
        base_url: str,
        access_token: str,
        agent_mxids: dict[str, str],
        agent_rooms: dict[str, str],
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.agent_mxids = agent_mxids
        self.agent_rooms = agent_rooms
        self._sender_mxid: str | None = None

    def _get(self, path: str) -> dict[str, object]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            method="GET",
            headers={"Authorization": f"Bearer {self.access_token}"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read(2_000_001)
        if len(payload) > 2_000_000:
            raise RuntimeError("Matrix response exceeded the 2 MB read limit")
        result = json.loads(payload)
        if not isinstance(result, dict):
            raise RuntimeError("Matrix returned a non-object response")
        return result

    def _authenticated_sender(self) -> str:
        if self._sender_mxid is None:
            sender_mxid = str(self._get("/_matrix/client/v3/account/whoami").get("user_id", ""))
            if not sender_mxid:
                raise RuntimeError("Matrix whoami returned no user_id")
            self._sender_mxid = sender_mxid
        return self._sender_mxid

    def _send_room_message(self, room_id: str, transaction_id: str, content: dict[str, object]) -> str:
        room = urllib.parse.quote(room_id, safe="")
        transaction = urllib.parse.quote(transaction_id, safe="")
        url = f"{self.base_url}/_matrix/client/v3/rooms/{room}/send/m.room.message/{transaction}"
        request = urllib.request.Request(
            url,
            data=json.dumps(content, separators=(",", ":")).encode("utf-8"),
            method="PUT",
            headers={"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read(65_537))
        event_id = str(result.get("event_id", ""))
        if not event_id:
            raise RuntimeError("Matrix send returned no event_id")
        return event_id

    def _clear_agent_session(self, room_id: str, agent_mxid: str, dispatch_event_id: UUID | str) -> None:
        clear_event_id = self._send_room_message(
            room_id,
            f"launchscope-clear-{dispatch_event_id}",
            {
                "msgtype": "m.text",
                "body": "/clear",
                "m.mentions": {"user_ids": [agent_mxid]},
                "launchscope_control": {
                    "command": "clear",
                    "session_id": f"matrix:{room_id}",
                    "dispatch_event_id": str(dispatch_event_id),
                },
            },
        )
        room = urllib.parse.quote(room_id, safe="")
        event = self._get(
            f"/_matrix/client/v3/rooms/{room}/event/{urllib.parse.quote(clear_event_id, safe='')}"
        )
        clear_timestamp = int(str(event.get("origin_server_ts", 0)))
        if clear_timestamp <= 0:
            raise AgentWorkerBusy("Matrix clear event has no durable timestamp")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            result = self._get(f"/_matrix/client/v3/rooms/{room}/messages?dir=b&limit=8")
            events = result.get("chunk")
            if isinstance(events, list):
                for candidate in events:
                    if not isinstance(candidate, dict):
                        continue
                    content = candidate.get("content")
                    if (
                        candidate.get("sender") == agent_mxid
                        and int(str(candidate.get("origin_server_ts", 0))) >= clear_timestamp
                        and isinstance(content, dict)
                        and str(content.get("body", "")).startswith("**History Cleared!**")
                    ):
                        return
            time.sleep(0.25)
        raise AgentWorkerBusy("Agent session isolation acknowledgement timed out")

    def send_assignment(self, event: EventEnvelope) -> tuple[str, str]:
        assignment = AgentTeamsBridge().assignment_from_dispatch(event.to_dict())
        agent_code = str(assignment.body["agent_code"])
        try:
            agent_mxid = self.agent_mxids[agent_code]
        except KeyError as exc:
            raise RuntimeError(f"no Matrix identity configured for Agent {agent_code}") from exc
        try:
            room_id = self.agent_rooms[agent_code]
        except KeyError as exc:
            raise RuntimeError(f"no provisioned Matrix room configured for Agent {agent_code}") from exc
        sender_mxid = self._authenticated_sender()
        room = urllib.parse.quote(room_id, safe="")
        joined = self._get(f"/_matrix/client/v3/rooms/{room}/joined_members").get("joined")
        members = set(joined) if isinstance(joined, dict) else set()
        expected_members = {sender_mxid, agent_mxid}
        if members != expected_members:
            raise RuntimeError(f"Matrix room membership mismatch for Agent {agent_code}")
        self._clear_agent_session(room_id, agent_mxid, event.event_id)
        assignment_json = json.dumps(assignment.body, separators=(",", ":"))
        event_id = self._send_room_message(
            room_id,
            str(event.event_id),
            {
                "msgtype": "m.text",
                "body": f"{agent_mxid}\n{assignment_json}",
                "m.mentions": {"user_ids": [agent_mxid]},
                "launchscope_assignment": assignment.body,
            },
        )
        return room_id, event_id

    def stop_task(
        self,
        room_id: str,
        delivery_id: str,
        task_id: str | None = None,
        dispatch_epoch: int | None = None,
    ) -> None:
        agent_code = next(
            (code for code, configured_room in self.agent_rooms.items() if configured_room == room_id),
            None,
        )
        if agent_code is None or agent_code not in self.agent_mxids:
            raise RuntimeError("Matrix stop room is not mapped to a configured Agent")
        agent_mxid = self.agent_mxids[agent_code]
        control = {
            "command": "stop",
            "session_id": f"matrix:{room_id}",
            "delivery_id": delivery_id,
            **({"task_id": task_id, "dispatch_epoch": dispatch_epoch} if task_id is not None else {}),
        }
        self._send_room_message(
            room_id,
            f"launchscope-stop-{delivery_id}",
            {
                "msgtype": "m.text",
                "body": "/stop",
                "m.mentions": {"user_ids": [agent_mxid]},
                "launchscope_control": control,
            },
        )


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


def _configure_worker_delivery(agent_code: str, token: str) -> None:
    try:
        endpoints = json.loads(os.environ["LAUNCHSCOPE_WORKER_CONSOLE_ENDPOINTS_JSON"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError("Worker console endpoint mapping is unavailable") from exc
    if not isinstance(endpoints, dict) or not str(endpoints.get(agent_code, "")).strip():
        raise RuntimeError(f"Worker console endpoint is unavailable for Agent {agent_code}")
    endpoint = str(endpoints[agent_code]).rstrip("/")
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Worker console endpoint must be an explicit local HTTP endpoint")
    gateway_port = int(os.getenv("LAUNCHSCOPE_MODEL_GATEWAY_PORT", "8092"))
    request = urllib.request.Request(
        f"{endpoint}/api/models/launchscope-model-egress/config",
        data=json.dumps({
            "api_key": token,
            "base_url": f"http://host.docker.internal:{gateway_port}/v1",
            "chat_model": "OpenAIChatModel",
            "generate_kwargs": {},
        }, separators=(",", ":")).encode("utf-8"),
        method="PUT",
        headers={"Content-Type": "application/json", "X-Agent-Id": "default"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status >= 300:
            raise RuntimeError(f"Worker model configuration failed with HTTP {response.status}")


def _mark_assignment_unknown(
    session: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    task_id: UUID,
    reason: str,
    now: datetime,
) -> None:
    current_status = session.execute(select(evaluation_run.c.status).where(
        evaluation_run.c.tenant_id == tenant_id,
        evaluation_run.c.id == run_id,
    ).with_for_update()).scalar_one()
    session.execute(update(task).where(
        task.c.tenant_id == tenant_id,
        task.c.id == task_id,
        task.c.status == "READY",
    ).values(
        status="NEEDS_ATTENTION",
        side_effect_started=True,
        last_failure_class="SUBMISSION_UNKNOWN",
        last_error=reason[:1000],
        updated_at=now,
    ))
    if current_status != "NEEDS_ATTENTION":
        session.execute(update(evaluation_run).where(
            evaluation_run.c.tenant_id == tenant_id,
            evaluation_run.c.id == run_id,
        ).values(
            status="NEEDS_ATTENTION",
            last_failure_class="SUBMISSION_UNKNOWN",
            attention_reason=reason[:1000],
            updated_at=now,
        ))
        session.execute(insert(run_status_history).values(
            id=uuid4(),
            tenant_id=tenant_id,
            run_id=run_id,
            from_status=current_status,
            to_status="NEEDS_ATTENTION",
            reason=reason[:1000],
            failure_class="SUBMISSION_UNKNOWN",
            occurred_at=now,
        ))


def _deliver_assignment(
    db_session: Session,
    current: EventEnvelope,
    matrix: MatrixHumanClient,
    usage_reader: Any | None,
    lease_engine: Any | None = None,
) -> bool:
    if current.task_id is None:
        raise RuntimeError("Task-ready event lacks task_id")
    tenant_id = UUID(str(current.tenant_id))
    run_id = UUID(str(current.run_id))
    task_id = UUID(str(current.task_id))
    candidate = db_session.execute(
        select(task.c.id).where(
            task.c.tenant_id == tenant_id,
            task.c.run_id == run_id,
            task.c.id == task_id,
            task.c.status == "READY",
        )
    ).mappings().one_or_none()
    if candidate is None:
        return False
    assigned = db_session.execute(select(
        task.c.timeout_seconds,
        task.c.dispatch_epoch,
    ).where(
        task.c.tenant_id == tenant_id,
        task.c.run_id == run_id,
        task.c.id == task_id,
        task.c.status == "READY",
    )).mappings().one_or_none()
    if assigned is None:
        return False
    if int(assigned["dispatch_epoch"]) != int(current.payload.get("dispatch_epoch", -1)):
        return False
    agent_code = str(current.payload.get("agent_code", ""))
    manifest = db_session.execute(select(run_manifest.c.frozen_config).where(
        run_manifest.c.tenant_id == tenant_id,
        run_manifest.c.run_id == run_id,
    )).scalar_one()
    resume_authorized_at = db_session.execute(select(run_execution_control.c.resumed_at).where(
        run_execution_control.c.tenant_id == tenant_id,
        run_execution_control.c.run_id == run_id,
    )).scalar_one_or_none()
    accounting_mode = _delivery_accounting_mode(manifest, resume_authorized_at=resume_authorized_at)
    runtime = current.payload.get("agent_runtime")
    max_iters = int(runtime.get("max_iters", 16)) if isinstance(runtime, dict) else 16
    max_model_calls = max_iters + 4
    capability = issue_delivery_capability()
    if lease_engine is None:
        lease = prepare_worker_lease(
            db_session,
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=task_id,
            dispatch_epoch=int(str(assigned["dispatch_epoch"])),
            control_epoch=int(current.payload.get("control_epoch", 0)),
            agent_code=agent_code,
            capability=capability,
        )
    else:
        with lease_engine.begin() as lease_connection:
            lease = prepare_worker_lease(
                lease_connection,
                tenant_id=tenant_id,
                run_id=run_id,
                task_id=task_id,
                dispatch_epoch=int(str(assigned["dispatch_epoch"])),
                control_epoch=int(current.payload.get("control_epoch", 0)),
                agent_code=agent_code,
                capability=capability,
            )
    try:
        _configure_worker_delivery(agent_code, capability.token)
    except Exception as exc:
        target = db_session
        if lease_engine is not None:
            with lease_engine.begin() as lease_connection:
                fail_worker_lease(
                    lease_connection,
                    lease.lease_id,
                    error="Worker delivery credential configuration failed before Matrix assignment",
                    now=datetime.now(UTC),
                )
        else:
            fail_worker_lease(
                target,
                lease.lease_id,
                error="Worker delivery credential configuration failed before Matrix assignment",
                now=datetime.now(UTC),
            )
        raise AgentWorkerBusy("Worker delivery credential could not be installed") from exc
    try:
        assert_run_active(
            db_session,
            tenant_id,
            run_id,
            expected_epoch=int(current.payload.get("control_epoch", 0)),
        )
        locked_assignment = db_session.execute(select(
            task.c.timeout_seconds,
            task.c.dispatch_epoch,
        ).where(
            task.c.tenant_id == tenant_id,
            task.c.run_id == run_id,
            task.c.id == task_id,
            task.c.status == "READY",
        ).with_for_update()).mappings().one_or_none()
        if locked_assignment is None or int(locked_assignment["dispatch_epoch"]) != int(assigned["dispatch_epoch"]):
            raise RunExecutionPausedError("Task dispatch changed during Worker lease preparation")
        assigned = locked_assignment
    except RunExecutionPausedError:
        if lease is not None and lease_engine is not None:
            with lease_engine.begin() as lease_connection:
                drain_worker_lease_for_delivery(lease_connection, lease.delivery_id, now=datetime.now(UTC))
        raise
    usage_baseline = None
    try:
        if usage_reader is not None:
            usage_baseline = usage_reader.snapshot(agent_code)
        elif provider_usage_required():
            raise RuntimeError("provider usage is required but no Agent usage endpoint is configured")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        if lease is not None:
            if lease_engine is not None:
                with lease_engine.begin() as lease_connection:
                    fail_worker_lease(
                        lease_connection,
                        lease.lease_id,
                        error=f"Agent usage endpoint unavailable before Matrix assignment: {type(exc).__name__}",
                        now=datetime.now(UTC),
                    )
            else:
                fail_worker_lease(
                    db_session,
                    lease.lease_id,
                    error=f"Agent usage endpoint unavailable before Matrix assignment: {type(exc).__name__}",
                    now=datetime.now(UTC),
                )
        raise AgentWorkerBusy("Agent usage endpoint is temporarily unavailable") from exc
    try:
        assigned_room_id, assignment_event_id = matrix.send_assignment(current)
    except Exception as exc:
        now = datetime.now(UTC)
        if lease is not None:
            if lease_engine is not None:
                with lease_engine.begin() as lease_connection:
                    fail_worker_lease(
                        lease_connection,
                        lease.lease_id,
                        error=f"Matrix assignment state is unknown: {type(exc).__name__}",
                        now=now,
                    )
            else:
                fail_worker_lease(
                    db_session,
                    lease.lease_id,
                    error=f"Matrix assignment state is unknown: {type(exc).__name__}",
                    now=now,
                )
        _mark_assignment_unknown(
            db_session,
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=task_id,
            reason="Matrix assignment response is unknown; automatic retry prohibited",
            now=now,
        )
        return True
    delivered_at = datetime.now(UTC)
    try:
        with db_session.begin_nested():
            record_task_delivery(
                db_session,
                tenant_id=tenant_id,
                run_id=run_id,
                task_id=task_id,
                dispatch_epoch=int(str(assigned["dispatch_epoch"])),
                agent_code=agent_code,
                room_id=assigned_room_id,
                assignment_event_id=assignment_event_id,
                usage_baseline=usage_baseline,
                delivered_at=delivered_at,
                timeout_seconds=int(str(assigned["timeout_seconds"])),
                delivery_id=lease.delivery_id if lease is not None else None,
                worker_name=lease.worker_name if lease is not None else physical_worker_name(agent_code),
                max_model_calls=max_model_calls if accounting_mode == "GATEWAY_DELIVERY" else 0,
                accounting_mode=accounting_mode,
                lease_id=lease.lease_id if lease is not None else None,
            )
    except Exception as exc:
        if lease is not None:
            if lease_engine is not None:
                with lease_engine.begin() as lease_connection:
                    fail_worker_lease(
                        lease_connection,
                        lease.lease_id,
                        error=f"Durable delivery activation failed after Matrix assignment: {type(exc).__name__}",
                        now=delivered_at,
                    )
            else:
                fail_worker_lease(
                    db_session,
                    lease.lease_id,
                    error=f"Durable delivery activation failed after Matrix assignment: {type(exc).__name__}",
                    now=delivered_at,
                )
        _mark_assignment_unknown(
            db_session,
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=task_id,
            reason="Matrix assignment was sent but durable delivery activation failed; automatic retry prohibited",
            now=delivered_at,
        )
        return True
    leader_room_id = (
        assigned_room_id
        if current.payload.get("agent_code") == "evaluation-manager"
        else os.environ["AGENTTEAMS_LEADER_ROOM_ID"]
    )
    db_session.execute(update(agentteams_run_binding).where(
        agentteams_run_binding.c.tenant_id == current.tenant_id,
        agentteams_run_binding.c.run_id == current.run_id,
    ).values(
        team_room_id=os.environ["AGENTTEAMS_TEAM_ROOM_ID"],
        leader_room_id=leader_room_id,
        binding_status="TASKS_DISPATCHED",
        updated_at=delivered_at,
    ))
    return True


def _delivery_accounting_mode(
    manifest: dict[str, object],
    *,
    resume_authorized_at: datetime | None,
) -> str:
    model_accounting = manifest.get("model_accounting", {})
    configured_mode = (
        str(model_accounting.get("mode") or "GATEWAY_DELIVERY")
        if isinstance(model_accounting, dict)
        else "GATEWAY_DELIVERY"
    )
    if configured_mode == "GATEWAY_DELIVERY":
        return configured_mode
    if resume_authorized_at is not None:
        return "GATEWAY_DELIVERY"
    raise RunExecutionPausedError("Task delivery requires an explicit delivery-scoped model restart")


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
    control_engine = create_engine(normalize_database_url(settings.url), pool_pre_ping=True)
    usage_reader = configured_usage_reader()
    next_deadline_scan = 0.0
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
    agent_rooms = json.loads(os.environ["LAUNCHSCOPE_MATRIX_AGENT_ROOMS_JSON"])
    if not isinstance(agent_rooms, dict):
        raise RuntimeError("LAUNCHSCOPE_MATRIX_AGENT_ROOMS_JSON must be an object")
    agent_rooms = {str(agent_code): str(room_id) for agent_code, room_id in agent_rooms.items()}
    if set(agent_rooms) != set(agent_mxids):
        raise RuntimeError("Matrix Agent room mapping must exactly match the Agent identity directory")
    matrix = MatrixHumanClient(
        os.environ["AGENTTEAMS_MATRIX_URL"], os.environ["AGENTTEAMS_HUMAN_ACCESS_TOKEN"],
        agent_mxids, agent_rooms,
    )
    consumer.startup()
    try:
        while True:
            if time.monotonic() >= next_deadline_scan:
                with control_engine.connect() as connection:
                    renewal_ids = due_worker_lease_renewal_ids(connection, now=datetime.now(UTC))
                for lease_id in renewal_ids:
                    try:
                        with control_engine.begin() as connection:
                            renew_worker_lease_credential(
                                connection,
                                lease_id,
                                now=datetime.now(UTC),
                                configure=_configure_worker_delivery,
                            )
                    except Exception as exc:  # noqa: BLE001 - renewal never reaches the model upstream.
                        print(
                            f"dispatch-bridge: delivery credential renewal deferred: {type(exc).__name__}",
                            file=sys.stderr,
                        )
                with control_engine.begin() as connection:
                    pause_stops = pending_pause_stops(connection)
                    undelivered_task_ids = reconcile_expired_undelivered_tasks(
                        connection,
                        now=datetime.now(UTC),
                    )
                    timed_out_rooms = reconcile_expired_task_deliveries(
                        connection,
                        now=datetime.now(UTC),
                        usage_reader=usage_reader,
                        require_provider_usage=provider_usage_required(),
                    )
                    stale_preparing_delivery_ids = reconcile_stale_preparing_worker_leases(
                        connection,
                        now=datetime.now(UTC),
                    )
                for room_id, delivery_id, task_id, dispatch_epoch in pause_stops:
                    try:
                        matrix.stop_task(room_id, delivery_id, task_id, dispatch_epoch)
                    except Exception as exc:  # noqa: BLE001 - local gates already deny external work.
                        print(f"dispatch-bridge: failed to stop paused Agent session: {exc}", file=sys.stderr)
                    else:
                        with control_engine.begin() as connection:
                            mark_pause_stop_sent(connection, delivery_id, now=datetime.now(UTC))
                for room_id, delivery_id in timed_out_rooms:
                    try:
                        matrix.stop_task(room_id, delivery_id)
                    except Exception as exc:  # noqa: BLE001 - timeout state is already durable.
                        print(f"dispatch-bridge: failed to stop expired Agent session: {exc}", file=sys.stderr)
                if timed_out_rooms:
                    print(
                        f"dispatch-bridge: reconciled {len(timed_out_rooms)} expired Task(s)",
                        file=sys.stderr,
                    )
                if undelivered_task_ids:
                    print(
                        f"dispatch-bridge: reconciled {len(undelivered_task_ids)} undelivered Task(s)",
                        file=sys.stderr,
                    )
                if stale_preparing_delivery_ids:
                    print(
                        f"dispatch-bridge: quarantined {len(stale_preparing_delivery_ids)} stale Worker lease(s)",
                        file=sys.stderr,
                    )
                next_deadline_scan = time.monotonic() + 5
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
                        if not _deliver_assignment(
                            db_session,
                            current,
                            matrix,
                            usage_reader,
                            lease_engine=control_engine,
                        ):
                            print(
                                f"dispatch-bridge: acknowledged stale Task dispatch {current.task_id} "
                                "without side effects",
                                file=sys.stderr,
                            )

                    try:
                        InboxConsumer(session, "agentteams-dispatch-bridge").consume_once(event, handle, scope=scope)
                    except AgentWorkerBusy as exc:
                        consumer.change_invisible_duration(message, 10)
                        print(f"dispatch-bridge: deferred assignment: {exc}", file=sys.stderr)
                        continue
                    except RunExecutionPausedError:
                        print(
                            f"dispatch-bridge: discarded paused or superseded Task dispatch {event.task_id}",
                            file=sys.stderr,
                        )
                consumer.ack(message)
    except KeyboardInterrupt:
        pass
    finally:
        consumer.shutdown()
        engine.dispose()
        control_engine.dispose()


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
        and candidate.get("schema_version") in _HANDOFF_SCHEMA_VERSIONS
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
            object_start = candidate_body.find("{")
            if object_start < 0:
                return None
            candidate_body = candidate_body[object_start:].strip()
        try:
            parsed = json.loads(candidate_body)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict) and parsed.get("message_type") == "LeaderBlockerV1":
            routing = ("tenant_id", "run_id", "task_id", "agent_code")
            if not all(parsed.get(key) for key in routing):
                return None
            candidate_error = parsed.get("error")
            error: dict[str, object] = candidate_error if isinstance(candidate_error, dict) else {}
            error_code = str(error.get("code", "AGENT_RUNTIME_BLOCKED"))
            detail = str(error.get("detail", parsed.get("note", "Agent runtime reported a blocker")))
            return {
                "schema_version": "1.0",
                **{key: parsed[key] for key in routing},
                "status": "BLOCKED",
                "dimension": "CONTROL",
                "claims": [],
                "evidence_refs": [],
                "risk": "HIGH",
                "confidence": 0.0,
                "needs_human_approval": True,
                "failure_class": "RUNTIME_UNAVAILABLE",
                "next_action": f"{error_code}: {detail}"[:2000],
                "audit_results": [],
            }
        if isinstance(parsed, dict) and parsed.get("message_type") in _V4_MESSAGE_TYPES:
            routing = ("tenant_id", "run_id", "task_id", "agent_code")
            payload_key = "documents" if parsed["message_type"] in {"AuditResultV3", "AuditResultV4"} else "document"
            return parsed if all(parsed.get(key) for key in routing) and payload_key in parsed else None
        if isinstance(parsed, dict) and parsed.get("status") == "COMPLETED":
            parsed["status"] = "SUCCEEDED"
        needs_input = isinstance(parsed, dict) and parsed.get("status") == "NEEDS_INPUT"
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
            # ADR 0004: a clarification carries no claims and no failure class, so the
            # geo-policy-trend claim backfill below must not invent placeholder facts.
            if (
                parsed.get("agent_code") == "geo-policy-trend"
                and not needs_input
                and isinstance(parsed.get("claims"), list)
            ):
                for claim in parsed["claims"]:
                    if isinstance(claim, dict):
                        for key in ("region", "fetched_at", "valid_until", "trend_signal"):
                            if not claim.get(key):
                                claim[key] = "UNKNOWN"
        return (
            parsed
            if isinstance(parsed, dict)
            and parsed.get("schema_version") in _HANDOFF_SCHEMA_VERSIONS
            and all(parsed.get(key) for key in required)
            else None
        )
    return None


def _contract_rejection_detail(error: urllib.error.HTTPError) -> str:
    raw = error.read(8_193)
    if not raw:
        return f"HTTP {error.code} returned no validation detail"
    try:
        payload = json.loads(raw[:8_192])
        detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
        rendered = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        rendered = raw[:8_192].decode("utf-8", errors="replace")
    return re.sub(r"\s+", " ", rendered).strip()[:600]


def _contract_failure_handoff(
    handoff: dict[str, object], next_action: str, *, failure_class: str = "VALIDATION"
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "tenant_id": str(handoff.get("tenant_id", "")),
        "run_id": str(handoff.get("run_id", "")),
        "task_id": str(handoff.get("task_id", "")),
        "dispatch_epoch": handoff.get("dispatch_epoch"),
        "agent_code": str(handoff.get("agent_code", "")),
        "status": "BLOCKED",
        "dimension": str(handoff.get("dimension", "CONTROL")),
        "claims": [],
        "evidence_refs": [],
        "risk": "HIGH",
        "confidence": 0.0,
        "needs_human_approval": True,
        "failure_class": failure_class,
        "next_action": next_action[:2000],
        "audit_results": [],
    }


def _canonical_handoff_routes(
    handoff: dict[str, object], event: dict[str, object]
) -> tuple[str, str, str]:
    raw_tenant_id = str(handoff.get("tenant_id") or event.get("tenant_id") or "")
    raw_run_id = str(handoff.get("run_id") or "")
    raw_task_id = str(handoff.get("task_id") or "")
    if not raw_tenant_id or not raw_run_id or not raw_task_id:
        raise ValueError("Matrix handoff lacks tenant/run/task routing metadata")
    try:
        return str(UUID(raw_tenant_id)), str(UUID(raw_run_id)), str(UUID(raw_task_id))
    except ValueError as exc:
        raise ValueError("Matrix handoff routing metadata must contain valid UUID values") from exc


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
                    tenant_id, run_id, task_id = _canonical_handoff_routes(handoff, event)
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
                            payload = response.read(65_537)
                        # A superseded handoff is accepted-and-discarded (202), never a
                        # contract violation, so it must not be turned into a synthetic
                        # VALIDATION failure that would blame the Agent and stall the run.
                        if b'"SUPERSEDED"' in payload:
                            print(
                                f"Discarding superseded Matrix event {event.get('event_id')}: "
                                "it answers an earlier dispatch of this Task",
                                file=sys.stderr,
                            )
                    except urllib.error.HTTPError as exc:
                        if exc.code == 404:
                            print(
                                f"Skipping Matrix event {event.get('event_id')}: Run/Task no longer exists",
                                file=sys.stderr,
                            )
                            continue
                        if exc.code not in {400, 422}:
                            raise
                        expected_contract = str(handoff.get("message_type") or "frozen handoff contract")
                        rejection_detail = _contract_rejection_detail(exc)
                        failure = _contract_failure_handoff(
                            handoff,
                            (
                                f"{expected_contract} rejected: {rejection_detail}. "
                                "The actual immutable Matrix event is preserved. Admin action: compare its document "
                                "with the expected contract, fix the Agent output constraint, then create a new Run."
                            ),
                            failure_class=(
                                "BUDGET" if rejection_detail == "model token limit reached" else "VALIDATION"
                            ),
                        )
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


def reconcile_undelivered_once() -> None:
    settings = DatabaseSettings.from_env()
    control_engine = create_engine(normalize_database_url(settings.url), pool_pre_ping=True)
    try:
        with control_engine.begin() as connection:
            task_ids = reconcile_expired_undelivered_tasks(connection, now=datetime.now(UTC))
        print(json.dumps({"reconciled_undelivered_task_ids": task_ids}, separators=(",", ":")))
    finally:
        control_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("dispatch-bridge", "matrix-listener", "reconcile-undelivered-once"))
    args = parser.parse_args()
    if args.mode == "dispatch-bridge":
        dispatch_bridge()
    elif args.mode == "matrix-listener":
        matrix_listener()
    else:
        reconcile_undelivered_once()


if __name__ == "__main__":
    main()
