"""Durable, user-safe conversation channels for generation-v4 Runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    evaluation_run,
    evidence,
    information_request,
    requirement_brief,
    requirement_change,
    run_conversation_message,
    run_manifest,
    task,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.modules.identity_tenant.application import Actor, NotFoundError
from launchscope_api.modules.user_validation.application import IdempotencyConflictError
from launchscope_domain.value_objects import TenantScope

from .generation import is_supervisor_generation
from .intake_application import SupervisorChatApplication

CONVERSATION_CHANNELS = (
    "supervisor",
    "user-evidence",
    "product-engineering",
    "business-investment",
)
_SPECIALIST_CHANNELS = frozenset(CONVERSATION_CHANNELS[1:])
_ROUTABLE_TASK_STATUSES = ("PENDING", "READY", "BLOCKED")


class ConversationObjectStore(Protocol):
    def put_private(self, object_key: str, payload: bytes, mime_type: str) -> str: ...

    def get_private(self, object_key: str, *, max_bytes: int = 2_000_000) -> bytes: ...


@dataclass(frozen=True, slots=True)
class ConversationReceipt:
    message_id: UUID
    run_id: UUID
    channel: str
    route_state: str
    affected_task_ids: tuple[UUID, ...]
    questions: tuple[str, ...] = ()
    duplicate: bool = False


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class RunConversationApplication:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        objects: ConversationObjectStore,
        supervisor: SupervisorChatApplication,
    ) -> None:
        self._sessions = sessions
        self._objects = objects
        self._supervisor = supervisor

    def list_conversations(
        self,
        actor: Actor,
        run_id: UUID,
        *,
        cursor: UUID | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            run = self._generation_v4_run(session, actor.tenant_id, run_id)
            query = select(run_conversation_message).where(
                run_conversation_message.c.tenant_id == actor.tenant_id,
                run_conversation_message.c.run_id == run_id,
            )
            if cursor is not None:
                cursor_row = session.execute(
                    select(
                        run_conversation_message.c.created_at,
                        run_conversation_message.c.id,
                    ).where(
                        run_conversation_message.c.tenant_id == actor.tenant_id,
                        run_conversation_message.c.run_id == run_id,
                        run_conversation_message.c.id == cursor,
                    )
                ).mappings().first()
                if cursor_row is None:
                    raise NotFoundError("conversation cursor was not found")
                query = query.where(
                    or_(
                        run_conversation_message.c.created_at > cursor_row["created_at"],
                        and_(
                            run_conversation_message.c.created_at == cursor_row["created_at"],
                            run_conversation_message.c.id > cursor_row["id"],
                        ),
                    )
                )
            rows = session.execute(
                query.order_by(run_conversation_message.c.created_at, run_conversation_message.c.id).limit(limit + 1)
            ).mappings().all()
            visible = rows[:limit]
            channels = self._channel_states(session, actor.tenant_id, run_id, str(run["status"]))

        messages = []
        for row in visible:
            body = self._objects.get_private(str(row["object_key"]), max_bytes=30_000)
            if hashlib.sha256(body).hexdigest() != str(row["sha256"]):
                raise RuntimeError("conversation message integrity check failed")
            messages.append(
                {
                    "message_id": str(row["id"]),
                    "channel": str(row["channel"]),
                    "role": str(row["role"]),
                    "kind": str(row["message_kind"]),
                    "text": body.decode("utf-8"),
                    "route_state": str(row["route_state"]),
                    "affected_task_ids": [str(value) for value in row["affected_task_ids"]],
                    "created_at": row["created_at"].isoformat(),
                }
            )
        return {
            "run_id": str(run_id),
            "channels": channels,
            "messages": messages,
            "next_cursor": str(visible[-1]["id"]) if len(rows) > limit and visible else None,
        }

    def submit(
        self,
        actor: Actor,
        run_id: UUID,
        channel: str,
        *,
        message: str,
        allow_external_processing: bool,
        idempotency_key: str,
        correlation_id: UUID,
        supervisor_model_output: dict[str, Any] | None = None,
    ) -> ConversationReceipt:
        channel = channel.strip().lower()
        if channel not in CONVERSATION_CHANNELS:
            raise ValueError("unsupported Run conversation channel")
        normalized_message = message.strip()
        request_sha = hashlib.sha256(
            _canonical(
                {
                    "run_id": str(run_id),
                    "channel": channel,
                    "message": normalized_message,
                    "allow_external_processing": allow_external_processing,
                }
            )
        ).hexdigest()
        duplicate = self._existing(actor, idempotency_key, request_sha)
        if duplicate is not None:
            return duplicate

        run = self._run_identity(actor, run_id)
        body = normalized_message.encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        message_id = uuid4()
        object_key = f"tenant/{actor.tenant_id}/run/{run_id}/conversation/{message_id}/{digest}.txt"
        if self._objects.put_private(object_key, body, "text/plain; charset=utf-8") != digest:
            raise RuntimeError("object store did not preserve the conversation message digest")
        if channel == "supervisor":
            if supervisor_model_output is None:
                raise ValueError("the Supervisor channel requires a bounded intake proposal")
            result = self._supervisor.submit_requirement(
                actor,
                UUID(str(run["project_id"])),
                UUID(str(run["product_version_id"])),
                message=normalized_message,
                model_output=supervisor_model_output,
                idempotency_key=f"run-conversation:{hashlib.sha256(idempotency_key.encode()).hexdigest()}",
                correlation_id=correlation_id,
            )
            route_state = "WAITING_FOR_USER" if result.confirmation_required else "ROUTED"
            affected = self._affected_for_brief(actor, run_id, result.brief_id)
            questions = result.questions
        else:
            route_state, affected = self._route_specialist(
                actor,
                run_id,
                channel,
                message_id_hint=message_id,
            )
            questions = ()

        response_row: dict[str, object] | None = None
        if questions:
            response_id = uuid4()
            response_body = "\n".join(questions).encode("utf-8")
            response_digest = hashlib.sha256(response_body).hexdigest()
            response_key = f"tenant/{actor.tenant_id}/run/{run_id}/conversation/{response_id}/{response_digest}.txt"
            if self._objects.put_private(response_key, response_body, "text/plain; charset=utf-8") != response_digest:
                raise RuntimeError("object store did not preserve the conversation response digest")
            response_row = {
                "id": response_id,
                "tenant_id": actor.tenant_id,
                "run_id": run_id,
                "channel": channel,
                "role": "SUPERVISOR",
                "message_kind": "QUESTION",
                "object_key": response_key,
                "sha256": response_digest,
                "request_sha256": response_digest,
                "idempotency_key": f"response:{message_id}",
                "correlation_id": correlation_id,
                "route_state": "WAITING_FOR_USER",
                "affected_task_ids": [],
                "response": {},
                "created_by": "control-plane:supervisor",
            }

        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            replay = self._duplicate(session, actor.tenant_id, idempotency_key, request_sha)
            if replay is not None:
                return replay
            self._generation_v4_run(session, actor.tenant_id, run_id)
            session.execute(
                run_conversation_message.insert().values(
                    id=message_id,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    channel=channel,
                    role="USER",
                    message_kind="MESSAGE",
                    object_key=object_key,
                    sha256=digest,
                    request_sha256=request_sha,
                    idempotency_key=idempotency_key,
                    correlation_id=correlation_id,
                    route_state=route_state,
                    affected_task_ids=[str(value) for value in affected],
                    response={"questions": list(questions)},
                    created_by=actor.actor_id,
                    created_at=now,
                )
            )
            if response_row is not None:
                session.execute(
                    run_conversation_message.insert().values(created_at=now + timedelta(microseconds=1), **response_row)
                )
        return ConversationReceipt(message_id, run_id, channel, route_state, affected, questions)

    def _route_specialist(
        self,
        actor: Actor,
        run_id: UUID,
        channel: str,
        *,
        message_id_hint: UUID | None,
    ) -> tuple[str, tuple[UUID, ...]]:
        if channel not in _SPECIALIST_CHANNELS:
            raise ValueError("only domain specialist channels can use specialist routing")
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            run = self._generation_v4_run(session, actor.tenant_id, run_id, lock=True)
            if str(run["status"]) == "NEEDS_ATTENTION":
                return "NEEDS_ATTENTION", ()
            brief_id = session.execute(
                select(requirement_brief.c.id)
                .where(
                    requirement_brief.c.tenant_id == actor.tenant_id,
                    requirement_brief.c.product_version_id == run["product_version_id"],
                )
                .order_by(requirement_brief.c.revision.desc())
                .limit(1)
            ).scalar_one_or_none()
            affected = tuple(
                session.execute(
                    select(task.c.id).where(
                        task.c.tenant_id == actor.tenant_id,
                        task.c.run_id == run_id,
                        task.c.agent_identity_ref.like(f"{channel}@%"),
                        task.c.status.in_(_ROUTABLE_TASK_STATUSES),
                    )
                ).scalars()
            )
            if brief_id is not None:
                session.execute(
                    requirement_change.insert().values(
                        id=uuid4(),
                        tenant_id=actor.tenant_id,
                        run_id=run_id,
                        brief_id=brief_id,
                        document={
                            "schema_version": "1.0",
                            "classification": "SUPPLEMENT",
                            "target_agent": channel,
                            "conversation_message_id": str(message_id_hint) if message_id_hint else None,
                            "affected_task_ids": [str(value) for value in affected],
                            "scope_changed": False,
                            "cost_changed": False,
                            "permission_changed": False,
                            "confirmation_required": False,
                            "reason": "User supplement routed only to matching not-yet-started generation-v4 tasks.",
                        },
                        status="APPLIED",
                        created_by=actor.actor_id,
                        created_at=now,
                    )
                )
            return ("ROUTED" if affected else "RECORDED"), affected

    def _run_identity(self, actor: Actor, run_id: UUID) -> dict[str, object]:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            row = self._generation_v4_run(session, actor.tenant_id, run_id)
            return dict(row)

    def _existing(self, actor: Actor, idempotency_key: str, request_sha: str) -> ConversationReceipt | None:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            return self._duplicate(session, actor.tenant_id, idempotency_key, request_sha)

    @staticmethod
    def _duplicate(
        session: Session,
        tenant_id: UUID,
        idempotency_key: str,
        request_sha: str,
    ) -> ConversationReceipt | None:
        row = session.execute(
            select(run_conversation_message).where(
                run_conversation_message.c.tenant_id == tenant_id,
                run_conversation_message.c.idempotency_key == idempotency_key,
            )
        ).mappings().first()
        if row is None:
            return None
        if str(row["request_sha256"]) != request_sha:
            raise IdempotencyConflictError("Run conversation Idempotency-Key was reused with a different payload")
        return ConversationReceipt(
            UUID(str(row["id"])),
            UUID(str(row["run_id"])),
            str(row["channel"]),
            str(row["route_state"]),
            tuple(UUID(str(value)) for value in row["affected_task_ids"]),
            tuple(str(value) for value in (row["response"] or {}).get("questions", [])),
            True,
        )

    def _affected_for_brief(self, actor: Actor, run_id: UUID, brief_id: UUID) -> tuple[UUID, ...]:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            row = session.execute(
                select(requirement_change.c.document)
                .where(
                    requirement_change.c.tenant_id == actor.tenant_id,
                    requirement_change.c.run_id == run_id,
                    requirement_change.c.brief_id == brief_id,
                )
                .order_by(requirement_change.c.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            return tuple(UUID(str(value)) for value in (row or {}).get("affected_task_ids", []))

    @staticmethod
    def _generation_v4_run(
        session: Session,
        tenant_id: UUID,
        run_id: UUID,
        *,
        lock: bool = False,
    ) -> Any:
        query = (
            select(evaluation_run, run_manifest.c.frozen_config)
            .outerjoin(
                run_manifest,
                (run_manifest.c.tenant_id == evaluation_run.c.tenant_id)
                & (run_manifest.c.run_id == evaluation_run.c.id),
            )
            .where(evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id)
        )
        if lock:
            query = query.with_for_update(of=evaluation_run)
        row = session.execute(query).mappings().first()
        if row is None:
            raise NotFoundError("Run was not found")
        generation = (row["frozen_config"] or {}).get("architecture_generation")
        flags = row["state_flags"] or {}
        if not is_supervisor_generation(generation) and not is_supervisor_generation(
            flags.get("architecture_generation")
        ):
            raise NotFoundError("Run conversations are unavailable for this historical generation")
        return row

    @staticmethod
    def _channel_states(
        session: Session,
        tenant_id: UUID,
        run_id: UUID,
        run_status: str,
    ) -> list[dict[str, object]]:
        tasks = session.execute(
            select(task.c.id, task.c.agent_identity_ref, task.c.status, task.c.evidence_requirement).where(
                task.c.tenant_id == tenant_id,
                task.c.run_id == run_id,
            )
        ).mappings().all()
        pending_rows = session.execute(
            select(information_request.c.agent_identity_ref, func.count().label("count"))
            .where(
                information_request.c.tenant_id == tenant_id,
                information_request.c.run_id == run_id,
                information_request.c.status == "OPEN",
            )
            .group_by(information_request.c.agent_identity_ref)
        ).all()
        pending = {str(agent).split("@", 1)[0]: int(count) for agent, count in pending_rows}
        evidence_rows = session.execute(
            select(task.c.agent_identity_ref, func.count(evidence.c.id).label("count"))
            .outerjoin(
                evidence,
                (evidence.c.tenant_id == task.c.tenant_id) & (evidence.c.task_id == task.c.id),
            )
            .where(task.c.tenant_id == tenant_id, task.c.run_id == run_id)
            .group_by(task.c.agent_identity_ref)
        ).all()
        evidence_count = {str(agent).split("@", 1)[0]: int(count) for agent, count in evidence_rows}

        channels: list[dict[str, object]] = []
        for channel in CONVERSATION_CHANNELS:
            if channel == "supervisor":
                channels.append(
                    {
                        "channel": channel,
                        "status": run_status,
                        "evidence_count": sum(evidence_count.values()),
                        "pending_count": sum(pending.values()),
                        "summary": "Coordinates scope, controlled routing, progress, and the final synthesis.",
                    }
                )
                continue
            selected = [item for item in tasks if str(item["agent_identity_ref"]).split("@", 1)[0] == channel]
            status = next(
                (str(item["status"]) for item in selected if item["status"] in {"NEEDS_INPUT", "RUNNING", "LEASED"}),
                str(selected[-1]["status"]) if selected else "PENDING",
            )
            summary = next(
                (str(item["evidence_requirement"]) for item in reversed(selected) if item["evidence_requirement"]),
                "",
            )
            channels.append(
                {
                    "channel": channel,
                    "status": "NEEDS_INPUT" if pending.get(channel, 0) else status,
                    "evidence_count": evidence_count.get(channel, 0),
                    "pending_count": pending.get(channel, 0),
                    "summary": summary,
                }
            )
        return channels


__all__ = ["CONVERSATION_CHANNELS", "ConversationReceipt", "RunConversationApplication"]
