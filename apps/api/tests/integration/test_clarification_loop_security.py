"""ADR 0004: the safety properties of the clarification loop, on real PostgreSQL.

The unit tests cover the Manager's impact rule with a fake Session.  These cover
the four properties that only hold against the real database: workspace
authorization, idempotent replay, Evidence provenance for a user statement, and
rejection of oversized input.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select, text

from launchscope_api.infrastructure.db.schema import (
    evidence,
    information_request,
    information_request_answer,
    task,
)
from launchscope_api.infrastructure.db.session import session_factory, tenant_transaction
from launchscope_api.infrastructure.messaging import IdempotencyConflict
from launchscope_api.modules.evaluation.clarification_application import (
    ClarificationApplication,
    ClarificationError,
    pause_run_for_clarification,
    record_information_requests,
)
from launchscope_api.modules.evaluation.dispatch_application import DispatchApplication
from launchscope_api.modules.identity_tenant.application import (
    Actor,
    AuthorizationError,
    NotFoundError,
)
from launchscope_domain.value_objects import TenantScope
from launchscope_orchestrator.agentteams_bridge import InformationRequestV1


class _Objects:
    """Records the private object write so provenance can be asserted."""

    def __init__(self) -> None:
        self.written: dict[str, bytes] = {}

    def put_private(self, object_key: str, payload: bytes, mime_type: str) -> str:
        assert object_key.endswith(".json") and mime_type == "application/json" and payload
        self.written[object_key] = payload
        return hashlib.sha256(payload).hexdigest()




def _member(database: Engine, records: dict[str, object], actor_id: str, role: str) -> Actor:
    with database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO workspace_member (id, tenant_id, workspace_id, actor_id, role) "
                "VALUES (:id, :tenant_id, :workspace_id, :actor_id, :role)"
            ),
            {
                "id": uuid4(),
                "tenant_id": records["tenant_id"],
                "workspace_id": records["workspace_id"],
                "actor_id": actor_id,
                "role": role,
            },
        )
    return Actor(records["tenant_id"], actor_id)  # type: ignore[arg-type]


def _park_run_with_question(
    database: Engine, runtime_engine: Engine, records: dict[str, object]
) -> tuple[UUID, UUID, str]:
    """Put the Run in WAITING_FOR_USER with one parked Task and one OPEN question.

    The Tasks come from the real dispatcher so the row shape, stage gates and
    dependencies match production rather than a hand-written fixture.
    """

    tenant_id, run_id = records["tenant_id"], records["run_id"]
    with database.begin() as connection:
        connection.execute(
            text("UPDATE evaluation_run SET status='PLANNED' WHERE id=:id"), {"id": run_id}
        )
    sessions = session_factory(runtime_engine)
    DispatchApplication(sessions)._dispatch_legacy_for_historical_tests_only(
        Actor(tenant_id, "local-demo:dispatcher"),  # type: ignore[arg-type]
        run_id,
        idempotency_key=f"clarification-{run_id}",
    )
    with tenant_transaction(sessions, TenantScope(tenant_id)) as session:  # type: ignore[arg-type]
        # Only a dispatched (READY) Task can raise a question; later stages are
        # still BLOCKED behind their stage gate, so pick whatever the dispatcher
        # actually opened rather than assuming a specific agent.
        task_id, agent_ref = session.execute(
            select(task.c.id, task.c.agent_identity_ref)
            .where(
                task.c.tenant_id == tenant_id,
                task.c.run_id == run_id,
                task.c.status == "READY",
            )
            .order_by(task.c.created_at, task.c.id)
            .limit(1)
        ).one()

    now = datetime.now(UTC)
    with database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO product_profile_draft "
                "(id, tenant_id, product_version_id, source, inferred_fields, answered_fields) "
                "VALUES (:id, :tenant_id, :version_id, 'MODEL_INFERENCE', '{}'::jsonb, '{}'::jsonb) "
                "ON CONFLICT (tenant_id, product_version_id) DO NOTHING"
            ),
            {"id": uuid4(), "tenant_id": tenant_id, "version_id": records["version_id"]},
        )
    # Park through the production path, not raw SQL, so the domain guards on
    # Task and Run are actually exercised by every test in this module.
    with tenant_transaction(sessions, TenantScope(tenant_id)) as session:  # type: ignore[arg-type]
        created = record_information_requests(
            session,
            tenant_id,  # type: ignore[arg-type]
            run_id,  # type: ignore[arg-type]
            task_id,
            str(agent_ref),
            [
                InformationRequestV1(
                    field="payer",
                    question="Who signs the contract?",
                    why_blocked="Cannot grade willingness to pay.",
                    dimension="PRODUCT_IMPLEMENTATION",
                )
            ],
            now,
        )
        assert created == 1
        pause_run_for_clarification(
            session,
            tenant_id,  # type: ignore[arg-type]
            run_id,  # type: ignore[arg-type]
            now,
            "product-engineering needs user input",
        )
        request_id = session.execute(
            select(information_request.c.id).where(
                information_request.c.task_id == task_id,
                information_request.c.status == "OPEN",
            )
        ).scalar_one()
    return task_id, request_id, str(agent_ref)


def test_a_viewer_cannot_answer_and_a_non_member_cannot_even_read(
    database, runtime_engine, tenant_records
) -> None:
    """Tenant RLS is not workspace authorization: answering is an EDITOR-level write."""

    _, request_id, _agent_ref = _park_run_with_question(database, runtime_engine, tenant_records)
    run_id = tenant_records["run_id"]
    application = ClarificationApplication(session_factory(runtime_engine))

    viewer = _member(database, tenant_records, "local-demo:viewer", "VIEWER")
    with pytest.raises(AuthorizationError):
        application.answer(
            viewer,
            run_id,
            {request_id: "The VP of Ops signs."},
            correlation_id=str(uuid4()),
            idempotency_key=str(uuid4()),
        )
    # A VIEWER may still read the questions.
    assert len(application.open_questions(viewer, run_id)) == 1

    stranger = Actor(tenant_records["tenant_id"], "local-demo:stranger")


    with pytest.raises(NotFoundError):
        application.open_questions(stranger, run_id)

    # The question must still be OPEN: a rejected write changes nothing.
    with tenant_transaction(
        session_factory(runtime_engine), TenantScope(tenant_records["tenant_id"])
    ) as session:
        assert (
            session.execute(
                select(information_request.c.status).where(
                    information_request.c.id == request_id
                )
            ).scalar_one()
            == "OPEN"
        )


def test_an_answer_is_stored_as_e3_evidence_and_replays_idempotently(
    database, runtime_engine, tenant_records
) -> None:
    """A conclusion resting on a user statement must be traceable to it."""

    task_id, request_id, agent_ref = _park_run_with_question(
        database, runtime_engine, tenant_records
    )
    run_id, tenant_id = tenant_records["run_id"], tenant_records["tenant_id"]
    sessions = session_factory(runtime_engine)
    objects = _Objects()
    application = ClarificationApplication(sessions, objects)
    editor = _member(database, tenant_records, "local-demo:editor", "EDITOR")
    correlation_id = str(uuid4())
    answer = "The VP of Operations holds the budget and signs annually."

    key = str(uuid4())

    first = application.answer(
        editor,
        run_id,
        {request_id: answer},
        correlation_id=correlation_id,
        idempotency_key=key,
    )
    assert first.affected_task_ids == (task_id,)
    assert first.run_status == "RUNNING"

    # Replaying the identical submission must not double-write Evidence or re-dispatch.
    replay = application.answer(
        editor,
        run_id,
        {request_id: answer},
        correlation_id=correlation_id,
        idempotency_key=key,
    )
    assert replay.affected_task_ids == first.affected_task_ids
    assert replay.dispatched == 0

    # The same key carrying different text is a conflict, not a second answer.
    with pytest.raises(IdempotencyConflict):
        application.answer(
            editor,
            run_id,
            {request_id: "Actually the CFO signs."},
            correlation_id=correlation_id,
            idempotency_key=key,
        )

    with tenant_transaction(sessions, TenantScope(tenant_id)) as session:
        answers = (
            session.execute(
                select(information_request_answer)
                .where(information_request_answer.c.information_request_id == request_id)
            )
            .mappings()
            .all()
        )
        assert len(answers) == 1, "replay must not append a second answer row"
        evidence_id = answers[0]["evidence_id"]
        assert evidence_id is not None, "an answer must be backed by first-class Evidence"

        row = (
            session.execute(select(evidence).where(evidence.c.id == evidence_id))
            .mappings()
            .one()
        )
        # E3 = user-declared, not independently verified.
        assert row["evidence_level"] == "E3"
        assert row["run_id"] == run_id
        # The answer row digests the raw text; Evidence digests the stored JSON envelope.
        assert row["sha256"] == hashlib.sha256(objects.written[row["object_key"]]).hexdigest()
        assert answers[0]["answer_sha256"] == hashlib.sha256(answer.encode()).hexdigest()
        envelope = json.loads(objects.written[row["object_key"]])
        assert envelope["answer"] == answer
        assert envelope["answered_by"] == editor.actor_id
        assert envelope["asked_by_agent"] == str(agent_ref)

        assert (
            session.execute(
                select(task.c.status).where(task.c.id == task_id)
            ).scalar_one()
            == "READY"
        ), "the parked Task must be released once its question is answered"


def test_an_oversized_answer_is_rejected_before_any_write(
    database, runtime_engine, tenant_records
) -> None:
    """Unbounded free text must not reach the ProductProfile or the object store."""

    _, request_id, _agent_ref = _park_run_with_question(database, runtime_engine, tenant_records)
    run_id = tenant_records["run_id"]
    sessions = session_factory(runtime_engine)
    application = ClarificationApplication(sessions)
    editor = _member(database, tenant_records, "local-demo:editor2", "EDITOR")

    with pytest.raises(ClarificationError):
        application.answer(
            editor,
            run_id,
            {request_id: "x" * 20_001},
            correlation_id=str(uuid4()),
            idempotency_key=str(uuid4()),
        )
    with pytest.raises(ClarificationError):
        application.answer(
            editor,
            run_id,
            {request_id: "   "},
            correlation_id=str(uuid4()),
            idempotency_key=str(uuid4()),
        )
    # A missing Idempotency-Key is rejected at the application boundary too, so a
    # non-REST caller cannot bypass the header requirement.
    with pytest.raises(ClarificationError):
        application.answer(
            editor,
            run_id,
            {request_id: "The VP of Ops signs."},
            correlation_id=str(uuid4()),
            idempotency_key="  ",
        )

    with tenant_transaction(sessions, TenantScope(tenant_records["tenant_id"])) as session:
        assert (
            session.execute(
                select(information_request.c.status).where(
                    information_request.c.id == request_id
                )
            ).scalar_one()
            == "OPEN"
        )
