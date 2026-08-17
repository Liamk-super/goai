"""ADR 0004: the Agent-initiated clarification loop.

An executing specialist may discover that one user-owned fact is missing.  It
returns ``NEEDS_INPUT`` with structured ``information_requests`` instead of
inventing a hypothesis or falling into the operator-owned ``NEEDS_ATTENTION``
state.

The user answer never reaches an Agent prompt directly.  It is written into the
durable ProductProfile draft, after which the Manager decides which Tasks the
new fact actually affects.  Only those Tasks are re-dispatched; Tasks that
already succeeded keep their result and their paid model work is not repeated.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    clarification_impact_assessment,
    evaluation_run,
    evidence,
    information_request,
    information_request_answer,
    product_profile_draft,
    project,
    run_status_history,
    task,
    workspace_member,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.infrastructure.messaging import IdempotencyConflict
from launchscope_api.infrastructure.object_store import S3QuarantineObjectStore
from launchscope_api.modules.evidence.source_locator import (
    SourceLocatorRepository,
    internal_material_source_locator,
)
from launchscope_api.modules.identity_tenant.application import (
    Actor,
    AuthorizationError,
    NotFoundError,
    WorkspaceRole,
)
from launchscope_domain.enums import RunStatus, TaskStatus
from launchscope_domain.errors import InvalidTransitionError
from launchscope_domain.services.run_state_machine import RunStateMachine, RunTransitionContext
from launchscope_domain.services.task_dag import TaskStateMachine
from launchscope_domain.value_objects import MAX_CLARIFICATION_ANSWER_CHARS, TenantScope
from launchscope_orchestrator.agentteams_bridge import InformationRequestV1

from .task_dispatch import enqueue_ready_tasks

# The database bounds these columns; reject oversize input instead of silently
# storing a different value than the one that was hashed (ADR 0004).  The limit
# is the single domain-owned calibre, so REST validation, the persisted column
# and the Agent contract cannot drift apart.
_MAX_ANSWER_CHARS = MAX_CLARIFICATION_ANSWER_CHARS
_MAX_IDEMPOTENCY_KEY_CHARS = 200
_EDITOR_ROLES = frozenset({WorkspaceRole.EDITOR.value, WorkspaceRole.OWNER.value})

# The task table predates the domain enum and persists a dispatchable task as
# ``READY`` where the domain models it as ``PENDING``.  Renaming the column
# vocabulary would touch every dispatch path, so translate at this boundary
# instead: the guards must run against domain statuses, not stored strings.
_STORED_TO_DOMAIN_TASK_STATUS: dict[str, TaskStatus] = {"READY": TaskStatus.PENDING}


def _domain_task_status(stored: str) -> TaskStatus:
    mapped = _STORED_TO_DOMAIN_TASK_STATUS.get(stored)
    if mapped is not None:
        return mapped
    try:
        return TaskStatus(stored)
    except ValueError as exc:  # pragma: no cover - would mean an unknown DB value
        raise ClarificationError(f"unknown persisted task status {stored!r}") from exc


def _assert_task_transition(
    stored_current: str,
    stored_target: str,
    *,
    unanswered_information_request: bool = False,
    information_requests_answered: bool = False,
) -> None:
    """Run the ADR 0004 Task guards before any UPDATE touches the row."""

    check = TaskStateMachine.check(
        _domain_task_status(stored_current),
        _domain_task_status(stored_target),
        unanswered_information_request=unanswered_information_request,
        information_requests_answered=information_requests_answered,
    )
    if not check.allowed:
        raise ClarificationError(f"{check.reason} ({check.code})")


def _assert_run_transition(
    stored_current: str, stored_target: str, context: RunTransitionContext
) -> None:
    """Run the ADR 0004 Run guards; a clarification is never a failure."""

    try:
        RunStateMachine.transition(
            RunStatus(stored_current), RunStatus(stored_target), context
        )
    except InvalidTransitionError as exc:
        raise ClarificationError(str(exc)) from exc


class ClarificationError(ValueError):
    """A clarification request or answer violates the ADR 0004 loop."""


@dataclass(frozen=True, slots=True)
class OpenQuestion:
    request_id: UUID
    task_id: UUID
    agent_code: str
    profile_field: str
    question: str
    why_blocking: str
    impact_dimension: str


@dataclass(frozen=True, slots=True)
class ResumeResult:
    run_status: str
    affected_task_ids: tuple[UUID, ...]
    unaffected_task_ids: tuple[UUID, ...]
    dispatched: int


def record_information_requests(
    session: Session,
    tenant_id: UUID,
    run_id: UUID,
    task_id: UUID,
    agent_identity_ref: str,
    requests: list[InformationRequestV1],
    now: datetime,
) -> int:
    """Persist an Agent's questions and park the Task, inside the caller's transaction."""

    if not requests:
        raise ClarificationError("NEEDS_INPUT requires at least one information request")
    existing = set(
        session.execute(
            select(information_request.c.profile_field).where(
                information_request.c.tenant_id == tenant_id,
                information_request.c.task_id == task_id,
                information_request.c.status == "OPEN",
            )
        ).scalars()
    )
    created = 0
    for item in requests:
        if item.field in existing:
            # The same Task asking the same field twice is a duplicate, not a new question.
            continue
        session.execute(
            information_request.insert().values(
                id=uuid4(),
                tenant_id=tenant_id,
                run_id=run_id,
                task_id=task_id,
                agent_identity_ref=agent_identity_ref,
                profile_field=item.field,
                question=item.question,
                why_blocking=item.why_blocked,
                impact_dimension=item.dimension,
                answer_kind="PROFILE_FIELD",
                status="OPEN",
                created_at=now,
                updated_at=now,
            )
        )
        created += 1
    current = session.execute(
        select(task.c.status).where(task.c.tenant_id == tenant_id, task.c.id == task_id)
    ).scalar_one()
    # The durable InformationRequest rows above are what make this transition
    # legal, so the guard is evaluated after they are written.
    _assert_task_transition(
        str(current),
        "NEEDS_INPUT",
        unanswered_information_request=created > 0 or _has_open_request(session, tenant_id, task_id),
    )
    # Keep the status in the WHERE clause as well so two concurrent handoffs for
    # the same Task cannot both believe they parked it.
    parked: Any = session.execute(
        update(task)
        .where(
            task.c.tenant_id == tenant_id,
            task.c.id == task_id,
            task.c.status == current,
        )
        .values(status="NEEDS_INPUT", updated_at=now)
    )
    if parked.rowcount != 1:
        raise ClarificationError("task is no longer in a state that can ask for input")
    return created


def _has_open_request(session: Session, tenant_id: UUID, task_id: UUID) -> bool:
    """True when a durable unanswered question already exists for this Task."""

    return bool(
        session.execute(
            select(information_request.c.id)
            .where(
                information_request.c.tenant_id == tenant_id,
                information_request.c.task_id == task_id,
                information_request.c.status == "OPEN",
            )
            .limit(1)
        ).first()
    )


def pause_run_for_clarification(
    session: Session, tenant_id: UUID, run_id: UUID, now: datetime, reason: str
) -> None:
    """Move the Run to WAITING_FOR_USER without a failure class (ADR 0004)."""

    current = session.execute(
        select(evaluation_run.c.status).where(
            evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id
        )
    ).scalar_one()
    if current == "WAITING_FOR_USER":
        return
    _assert_run_transition(
        current,
        "WAITING_FOR_USER",
        RunTransitionContext(unanswered_information_request=True),
    )
    session.execute(
        update(evaluation_run)
        .where(
            evaluation_run.c.tenant_id == tenant_id,
            evaluation_run.c.id == run_id,
            evaluation_run.c.status == current,
        )
        .values(status="WAITING_FOR_USER", updated_at=now)
    )
    session.execute(
        run_status_history.insert().values(
            id=uuid4(),
            tenant_id=tenant_id,
            run_id=run_id,
            from_status=current,
            to_status="WAITING_FOR_USER",
            reason=reason,
            failure_class=None,
            occurred_at=now,
        )
    )


class ClarificationApplication:
    """Read open questions, commit answers, and resume only the affected Tasks."""

    def __init__(
        self, sessions: sessionmaker[Session], objects: S3QuarantineObjectStore | None = None
    ) -> None:
        self._sessions = sessions
        self._objects = objects

    @staticmethod
    def _visible_run(actor: Actor, run_id: UUID, *, minimum: frozenset[str]) -> Select[Any]:
        """Tenant RLS is not workspace authorization: join through project membership."""

        return (
            select(evaluation_run)
            .join(
                project,
                (project.c.tenant_id == evaluation_run.c.tenant_id)
                & (project.c.id == evaluation_run.c.project_id),
            )
            .join(
                workspace_member,
                (workspace_member.c.tenant_id == project.c.tenant_id)
                & (workspace_member.c.workspace_id == project.c.workspace_id)
                & (workspace_member.c.actor_id == actor.actor_id),
            )
            .where(
                evaluation_run.c.tenant_id == actor.tenant_id,
                evaluation_run.c.id == run_id,
                workspace_member.c.role.in_(tuple(minimum)),
            )
        )

    def open_questions(self, actor: Actor, run_id: UUID) -> tuple[OpenQuestion, ...]:
        viewer = frozenset(
            {WorkspaceRole.VIEWER.value, WorkspaceRole.EDITOR.value, WorkspaceRole.OWNER.value}
        )
        with tenant_transaction(
            self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id
        ) as session:
            visible = session.execute(self._visible_run(actor, run_id, minimum=viewer)).first()
            if visible is None:
                raise NotFoundError("run was not found")
            rows = (
                session.execute(
                    select(information_request)
                    .where(
                        information_request.c.tenant_id == actor.tenant_id,
                        information_request.c.run_id == run_id,
                        information_request.c.status == "OPEN",
                    )
                    .order_by(information_request.c.created_at, information_request.c.id)
                )
                .mappings()
                .all()
            )
            return tuple(
                OpenQuestion(
                    request_id=row["id"],
                    task_id=row["task_id"],
                    agent_code=str(row["agent_identity_ref"]).split("@", 1)[0],
                    profile_field=row["profile_field"],
                    question=row["question"],
                    why_blocking=row["why_blocking"],
                    impact_dimension=row["impact_dimension"],
                )
                for row in rows
            )

    def answer(
        self,
        actor: Actor,
        run_id: UUID,
        answers: dict[UUID, str],
        *,
        correlation_id: str,
        idempotency_key: str,
    ) -> ResumeResult:
        if not answers:
            raise ClarificationError("at least one answer is required")
        key = idempotency_key.strip()
        if not key:
            raise ClarificationError("Idempotency-Key is required")
        if len(key) > _MAX_IDEMPOTENCY_KEY_CHARS:
            raise ClarificationError(
                f"Idempotency-Key cannot exceed {_MAX_IDEMPOTENCY_KEY_CHARS} characters"
            )
        cleaned: dict[UUID, str] = {}
        for request_id, text in answers.items():
            value = text.strip()
            if not value:
                raise ClarificationError("an answer cannot be empty")
            if len(value) > _MAX_ANSWER_CHARS:
                raise ClarificationError(
                    f"an answer cannot exceed {_MAX_ANSWER_CHARS} characters"
                )
            cleaned[request_id] = value
        now = datetime.now(UTC)
        with tenant_transaction(
            self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id
        ) as session:
            # Authorize before disclosing or mutating anything; answering is an
            # EDITOR-level write because it changes the ProductProfile.
            authorized = session.execute(
                self._visible_run(actor, run_id, minimum=_EDITOR_ROLES).with_for_update(
                    of=evaluation_run
                )
            ).first()
            if authorized is None:
                unauthorized = session.execute(
                    select(evaluation_run.c.id).where(
                        evaluation_run.c.tenant_id == actor.tenant_id,
                        evaluation_run.c.id == run_id,
                    )
                ).first()
                if unauthorized is None:
                    raise NotFoundError("Run was not found")
                raise AuthorizationError("actor lacks the required workspace role")
            run = (
                session.execute(
                    select(evaluation_run).where(
                        evaluation_run.c.tenant_id == actor.tenant_id,
                        evaluation_run.c.id == run_id,
                    )
                )
                .mappings()
                .first()
            )
            if run is None:
                raise NotFoundError("Run was not found")
            submission_sha256 = self._submission_digest(cleaned)
            replay = self._replay(session, actor, run_id, cleaned, key, submission_sha256)
            if replay is not None:
                return replay
            if run["status"] != "WAITING_FOR_USER":
                raise ClarificationError(
                    f"Run {run_id} is {run['status']}; only a clarification pause accepts answers"
                )

            open_rows = {
                row["id"]: row
                for row in session.execute(
                    select(information_request)
                    .where(
                        information_request.c.tenant_id == actor.tenant_id,
                        information_request.c.run_id == run_id,
                        information_request.c.status == "OPEN",
                    )
                    .with_for_update()
                )
                .mappings()
                .all()
            }
            unknown = set(cleaned) - set(open_rows)
            if unknown:
                raise ClarificationError("answers reference unknown or already answered requests")

            # 1. Structure the answers into the durable ProductProfile draft.
            answered_fields = self._write_profile_answers(
                session, actor, run["product_version_id"], open_rows, cleaned, now
            )

            # 2. Record every answer as an append-only, hashed audit row backed by a
            # first-class Evidence record, so a conclusion that rests on a user
            # statement is traceable to it (E3 = user-declared, not independently
            # verified).
            for request_id, value in cleaned.items():
                row = open_rows[request_id]
                evidence_id = self._persist_answer_evidence(
                    session, actor, run_id, row, value, now
                )
                session.execute(
                    information_request_answer.insert().values(
                        id=uuid4(),
                        tenant_id=actor.tenant_id,
                        information_request_id=request_id,
                        run_id=run_id,
                        answer_text=value,
                        answer_sha256=hashlib.sha256(value.encode("utf-8")).hexdigest(),
                        evidence_id=evidence_id,
                        supersedes_id=None,
                        answered_by=actor.actor_id,
                        correlation_id=correlation_id,
                        idempotency_key=key,
                        submission_sha256=submission_sha256,
                        created_at=now,
                    )
                )
                session.execute(
                    update(information_request)
                    .where(
                        information_request.c.tenant_id == actor.tenant_id,
                        information_request.c.id == request_id,
                    )
                    .values(status="ANSWERED", answered_at=now, updated_at=now)
                )

            remaining = session.execute(
                select(func.count())
                .select_from(information_request)
                .where(
                    information_request.c.tenant_id == actor.tenant_id,
                    information_request.c.run_id == run_id,
                    information_request.c.status == "OPEN",
                )
            ).scalar_one()
            if remaining:
                # Still waiting on the user; do not resume a partially answered Run.
                return ResumeResult("WAITING_FOR_USER", (), (), 0)

            # 3. The impact rule is owned by the control plane, not delegated to an
            # Agent.  ADR 0004 scopes re-execution to exactly the Tasks that chose
            # to park themselves.  Assigning this to a hypothetical Manager task
            # would be more flexible but is deliberately deferred: doing so would
            # require dispatching that Manager task, awaiting its handoff, and
            # building a real skill for impact assessment, which is out of scope for
            # the ask-then-answer MVP.  The assessed_by field acknowledges the real
            # decision-maker: the LaunchScope control plane itself.
            affected, unaffected = self._assess_impact(
                session, actor.tenant_id, run_id, answered_fields
            )
            session.execute(
                clarification_impact_assessment.insert().values(
                    id=uuid4(),
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    assessed_by_agent_ref="launchscope-control-plane",
                    answered_request_ids=sorted(str(value) for value in cleaned),
                    affected_task_ids=[str(value) for value in affected],
                    unaffected_task_ids=[str(value) for value in unaffected],
                    rationale=(
                        "Answered fields "
                        + ", ".join(sorted(answered_fields))
                        + " affect the listed Tasks; other Tasks keep their durable result."
                    )[:2000],
                    created_at=now,
                )
            )

            # 4. Resume only the affected Tasks.  Every question on a resumed Task
            #    must be answered first, so ask the domain guard, not just SQL.
            for task_id in affected:
                _assert_task_transition(
                    "NEEDS_INPUT",
                    "READY",
                    information_requests_answered=not _has_open_request(
                        session, actor.tenant_id, task_id
                    ),
                )
                released: Any = session.execute(
                    update(task)
                    .where(
                        task.c.tenant_id == actor.tenant_id,
                        task.c.id == task_id,
                        task.c.status == "NEEDS_INPUT",
                    )
                    .values(
                        status="READY",
                        dispatch_epoch=task.c.dispatch_epoch + 1,
                        updated_at=now,
                    )
                )
                if released.rowcount != 1:
                    raise ClarificationError("task is no longer waiting for user input")
            _assert_run_transition(
                "WAITING_FOR_USER",
                "RUNNING",
                RunTransitionContext(
                    information_requests_answered=True,
                    clarification_impact_assessed=True,
                    human_resume=True,
                ),
            )
            resumed: Any = session.execute(
                update(evaluation_run)
                .where(
                    evaluation_run.c.tenant_id == actor.tenant_id,
                    evaluation_run.c.id == run_id,
                    evaluation_run.c.status == "WAITING_FOR_USER",
                )
                .values(status="RUNNING", updated_at=now)
            )
            if resumed.rowcount != 1:
                raise ClarificationError("run is no longer waiting for user input")
            session.execute(
                run_status_history.insert().values(
                    id=uuid4(),
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    from_status="WAITING_FOR_USER",
                    to_status="RUNNING",
                    reason=(
                        f"User answered {len(cleaned)} clarification(s); "
                        f"{len(affected)} Task(s) re-dispatched, {len(unaffected)} preserved"
                    ),
                    failure_class=None,
                    occurred_at=now,
                )
            )

            stages = {
                str(row)
                for row in session.execute(
                    select(task.c.stage_code).where(
                        task.c.tenant_id == actor.tenant_id,
                        task.c.run_id == run_id,
                        task.c.id.in_(affected or [UUID(int=0)]),
                    )
                ).scalars()
            }
            dispatched = 0
            for stage_code in sorted(stages):
                dispatched += enqueue_ready_tasks(session, actor.tenant_id, run_id, stage_code)
            return ResumeResult("RUNNING", tuple(affected), tuple(unaffected), dispatched)

    @staticmethod
    def _submission_digest(cleaned: dict[UUID, str]) -> str:
        """Hash the whole submission, independent of the order fields arrived in."""

        canonical = json.dumps(
            {str(request_id): value for request_id, value in cleaned.items()},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _replay(
        session: Session,
        actor: Actor,
        run_id: UUID,
        cleaned: dict[UUID, str],
        idempotency_key: str,
        submission_sha256: str,
    ) -> ResumeResult | None:
        """Return the original outcome when this exact submission was already committed.

        A resubmitted answer must not double-apply or fail; it replays.  A reused
        Idempotency-Key carrying a different submission, or a reused request ID
        carrying different text, is a genuine conflict.
        """

        reused = (
            session.execute(
                select(information_request_answer.c.submission_sha256).where(
                    information_request_answer.c.tenant_id == actor.tenant_id,
                    information_request_answer.c.run_id == run_id,
                    information_request_answer.c.idempotency_key == idempotency_key,
                )
            )
            .scalars()
            .all()
        )
        if reused and any(digest != submission_sha256 for digest in reused):
            # Same key, different body: the frozen control-plane rule is to reject,
            # never to silently apply the second meaning.
            raise IdempotencyConflict(
                "this Idempotency-Key was already used for a different set of answers"
            )
        rows = (
            session.execute(
                select(
                    information_request_answer.c.information_request_id,
                    information_request_answer.c.answer_sha256,
                ).where(
                    information_request_answer.c.tenant_id == actor.tenant_id,
                    information_request_answer.c.run_id == run_id,
                    information_request_answer.c.information_request_id.in_(tuple(cleaned)),
                )
            )
            .mappings()
            .all()
        )
        if not rows:
            return None
        stored = {row["information_request_id"]: row["answer_sha256"] for row in rows}
        for request_id, value in cleaned.items():
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
            prior = stored.get(request_id)
            if prior is not None and prior != digest:
                raise ClarificationError(
                    "this information request was already answered with different text"
                )
        if len(stored) != len(cleaned):
            raise ClarificationError(
                "this submission mixes already-answered and new requests; resubmit only the new ones"
            )
        latest = (
            session.execute(
                select(clarification_impact_assessment)
                .where(
                    clarification_impact_assessment.c.tenant_id == actor.tenant_id,
                    clarification_impact_assessment.c.run_id == run_id,
                )
                .order_by(clarification_impact_assessment.c.created_at.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        status = session.execute(
            select(evaluation_run.c.status).where(
                evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id
            )
        ).scalar_one()
        if latest is None:
            return ResumeResult(str(status), (), (), 0)
        return ResumeResult(
            str(status),
            tuple(UUID(str(value)) for value in latest["affected_task_ids"]),
            tuple(UUID(str(value)) for value in latest["unaffected_task_ids"]),
            0,
        )

    def _persist_answer_evidence(
        self,
        session: Session,
        actor: Actor,
        run_id: UUID,
        request_row: RowMapping,
        value: str,
        now: datetime,
    ) -> UUID | None:
        """Store the user's statement as immutable, attributable Evidence."""

        if self._objects is None:
            return None
        evidence_id = uuid4()
        field = str(request_row["profile_field"])
        body = json.dumps(
            {
                "schema": "launchscope.clarification_answer.v1",
                "run_id": str(run_id),
                "information_request_id": str(request_row["id"]),
                "task_id": str(request_row["task_id"]),
                "asked_by_agent": str(request_row["agent_identity_ref"]),
                "profile_field": field,
                "question": str(request_row["question"]),
                "answer": value,
                "answered_by": actor.actor_id,
                "answered_at": now.isoformat(),
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
        key = f"tenant/{actor.tenant_id}/run/{run_id}/clarification/{evidence_id}.json"
        digest = self._objects.put_private(key, body, "application/json")
        session.execute(
            evidence.insert().values(
                id=evidence_id,
                tenant_id=actor.tenant_id,
                run_id=run_id,
                task_id=request_row["task_id"],
                material_id=None,
                # A user statement is declared, not independently verified.
                source_type="DERIVED",
                object_key=key,
                sha256=digest,
                size_bytes=len(body),
                mime_type="application/json",
                evidence_level="E3",
                trust_level="E3",
                summary=f"User clarification for '{field}': {value}"[:4000],
                published_at=None,
                fetched_at=now,
                valid_from=now,
                valid_until=None,
                region=None,
                simulated=False,
                supersedes_id=None,
                created_at=now,
            )
        )
        SourceLocatorRepository().append(
            session,
            tenant_id=actor.tenant_id,
            run_id=run_id,
            evidence_id=evidence_id,
            locators=(
                internal_material_source_locator(
                    display_name=f"用户补充：{field}",
                    fetched_at=now,
                    content_sha256=digest,
                    locator={"profile_field": field},
                ),
            ),
        )
        return evidence_id

    @staticmethod
    def _write_profile_answers(
        session: Session,
        actor: Actor,
        product_version_id: UUID,
        open_rows: Mapping[UUID, RowMapping],
        answers: dict[UUID, str],
        now: datetime,
    ) -> set[str]:
        draft = (
            session.execute(
                select(product_profile_draft)
                .where(
                    product_profile_draft.c.tenant_id == actor.tenant_id,
                    product_profile_draft.c.product_version_id == product_version_id,
                )
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if draft is None:
            raise ClarificationError("the Run has no ProductProfile draft to update")
        merged = dict(draft["answered_fields"])
        fields: set[str] = set()
        for request_id, text in answers.items():
            row = open_rows[request_id]
            field = row["profile_field"]
            merged[field] = text
            fields.add(field)
        session.execute(
            update(product_profile_draft)
            .where(
                product_profile_draft.c.tenant_id == actor.tenant_id,
                product_profile_draft.c.id == draft["id"],
            )
            .values(answered_fields=merged)
        )
        return fields

    @staticmethod
    def _assess_impact(
        session: Session,
        tenant_id: UUID,
        run_id: UUID,
        answered_fields: set[str],
    ) -> tuple[list[UUID], list[UUID]]:
        """Manager rule for the ask-then-answer path: resume exactly the parked Tasks.

        A Task that already SUCCEEDED keeps its durable result and its paid model
        work is never repeated here.  In this first version the only way new facts
        enter a running Run is an Agent-initiated question, so the blast radius is
        bounded by the Tasks that chose to stop and ask.  Invalidating completed
        conclusions belongs to the later user-initiated supplement path, which is
        deliberately out of scope (see ADR 0004).
        """

        rows = (
            session.execute(
                select(task.c.id, task.c.status).where(
                    task.c.tenant_id == tenant_id, task.c.run_id == run_id
                )
            )
            .mappings()
            .all()
        )
        affected: list[UUID] = []
        unaffected: list[UUID] = []
        for row in rows:
            if row["status"] == "NEEDS_INPUT":
                affected.append(row["id"])
            else:
                unaffected.append(row["id"])
        return affected, unaffected


__all__ = [
    "ClarificationApplication",
    "ClarificationError",
    "OpenQuestion",
    "ResumeResult",
    "pause_run_for_clarification",
    "record_information_requests",
]
