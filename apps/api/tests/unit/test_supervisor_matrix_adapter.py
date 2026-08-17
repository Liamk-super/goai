from types import SimpleNamespace
from uuid import uuid4

import pytest

from launchscope_api.modules.identity_tenant.application import Actor
from launchscope_api.modules.supervisor.matrix_adapter import SupervisorMatrixAdapter


class Directory:
    def agent_for_mxid(self, mxid: str) -> str | None:
        return {
            "@manager:local": "evaluation-manager",
            "@user:local": "user-evidence",
            "@auditor:local": "evidence-auditor",
        }.get(mxid)


class Receipts:
    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    def seen(self, _actor: Actor, event_id: str, digest: str, _run_id: object) -> bool:
        prior = self.items.get(event_id)
        if prior is not None and prior != digest:
            raise ValueError("payload mismatch")
        return prior is not None

    def record(self, _actor: Actor, **values: object) -> None:
        self.items[str(values["matrix_event_id"])] = str(values["payload_sha256"])


class Planning:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def accept_and_materialize(self, *args: object) -> None:
        self.calls.append(args)


class Audit:
    def __init__(self) -> None:
        self.handoffs: list[tuple[object, ...]] = []
        self.audits: list[tuple[object, ...]] = []

    def ingest_domain_handoff(self, *args: object, **kwargs: object) -> SimpleNamespace:
        self.handoffs.append((*args, kwargs))
        return SimpleNamespace(state="DOMAIN_REVIEW")

    def submit_audit_results(self, *args: object, **kwargs: object) -> SimpleNamespace:
        self.audits.append((*args, kwargs))
        return SimpleNamespace(state="DETERMINISTIC_SCORING")


class Completion:
    def __init__(self) -> None:
        self.prepared: list[tuple[object, ...]] = []
        self.committed: list[tuple[object, ...]] = []

    def prepare_scoring(self, *args: object) -> None:
        self.prepared.append(args)

    def commit_synthesis_report(self, *args: object) -> SimpleNamespace:
        self.committed.append(args)
        return SimpleNamespace(report_id=uuid4())


def event(
    event_id: str,
    sender: str,
    message_type: str,
    *,
    document: object,
    specialist_report: dict[str, object] | None = None,
) -> dict[str, object]:
    content_key = "documents" if message_type in {"AuditResultV3", "AuditResultV4"} else "document"
    content = {"message_type": message_type, content_key: document}
    if specialist_report is not None:
        content["specialist_report"] = specialist_report
    return {
        "event_id": event_id,
        "room_id": "!review:local",
        "sender": sender,
        "content": content,
    }


def test_generation_v4_messages_route_to_existing_control_plane_handlers() -> None:
    planning, audit, completion, receipts = Planning(), Audit(), Completion(), Receipts()
    adapter = SupervisorMatrixAdapter(planning, audit, completion, Directory(), receipts)  # type: ignore[arg-type]
    actor, run_id = Actor(uuid4(), "matrix-bridge"), uuid4()

    plan_task = uuid4()
    plan = adapter.consume(
        actor,
        event("$plan", "@manager:local", "ManagerPlanV1", document={"plan_id": str(uuid4())}),
        run_id=run_id,
        task_id=plan_task,
    )
    assert plan.task_status == "SUCCEEDED" and plan.run_status == "DOMAIN_REVIEW"
    assert planning.calls[0][2] == plan_task

    domain_task = uuid4()
    handoff = adapter.consume(
        actor,
        event(
            "$handoff",
            "@user:local",
            "AgentHandoffV3",
            document={"agent_code": "user-evidence"},
        ),
        run_id=run_id,
        task_id=domain_task,
    )
    assert handoff.task_status == "DOMAIN_REVIEW"
    assert audit.handoffs[0][2] == domain_task

    audit_task = uuid4()
    audit_result = adapter.consume(
        actor,
        event("$audit", "@auditor:local", "AuditResultV3", document=[{"audit_id": str(uuid4())}]),
        run_id=run_id,
        task_id=audit_task,
    )
    assert audit_result.run_status == "SUPERVISOR_SYNTHESIS"
    assert audit.audits[0][-1] == {"task_id": audit_task}
    assert completion.prepared[0][1] == run_id

    synthesis_task = uuid4()
    synthesis = adapter.consume(
        actor,
        event(
            "$synthesis",
            "@manager:local",
            "ManagerSynthesisV1",
            document={"synthesis_id": str(uuid4())},
        ),
        run_id=run_id,
        task_id=synthesis_task,
    )
    assert synthesis.task_status == "SUCCEEDED" and synthesis.run_status == "COMPLETED"
    assert synthesis.report_id is not None


def test_generation_v6_report_bodies_route_to_control_plane_persistence() -> None:
    planning, audit, completion, receipts = Planning(), Audit(), Completion(), Receipts()
    adapter = SupervisorMatrixAdapter(planning, audit, completion, Directory(), receipts)  # type: ignore[arg-type]
    actor, run_id = Actor(uuid4(), "matrix-bridge"), uuid4()
    report = {"schema_version": "2.0", "report_id": str(uuid4())}

    domain_task = uuid4()
    adapter.consume(
        actor,
        event(
            "$handoff-v6",
            "@user:local",
            "AgentHandoffV3",
            document={"agent_code": "user-evidence"},
            specialist_report=report,
        ),
        run_id=run_id,
        task_id=domain_task,
    )
    assert audit.handoffs[0][-1] == {"specialist_report": report}

    audit_task = uuid4()
    adapter.consume(
        actor,
        event(
            "$audit-v6",
            "@auditor:local",
            "AuditResultV4",
            document=[{"audit_id": str(uuid4())}],
            specialist_report=report,
        ),
        run_id=run_id,
        task_id=audit_task,
    )
    assert audit.audits[0][-1] == {"task_id": audit_task, "specialist_report": report}

    synthesis_task = uuid4()
    result = adapter.consume(
        actor,
        event(
            "$synthesis-v6",
            "@manager:local",
            "ManagerSynthesisV2",
            document={"synthesis_id": str(uuid4())},
        ),
        run_id=run_id,
        task_id=synthesis_task,
    )
    assert result.run_status == "COMPLETED"


def test_generation_v6_recovery_handoff_v4_routes_to_the_same_immutable_report_ingestion() -> None:
    planning, audit, completion, receipts = Planning(), Audit(), Completion(), Receipts()
    adapter = SupervisorMatrixAdapter(planning, audit, completion, Directory(), receipts)  # type: ignore[arg-type]
    actor, run_id, task_id = Actor(uuid4(), "matrix-bridge"), uuid4(), uuid4()
    report = {"schema_version": "2.0", "report_id": str(uuid4())}

    result = adapter.consume(
        actor,
        event(
            "$handoff-v6-recovery",
            "@user:local",
            "AgentHandoffV4",
            document={"schema_version": "4.0", "agent_code": "user-evidence", "dispatch_epoch": 2},
            specialist_report=report,
        ),
        run_id=run_id,
        task_id=task_id,
    )

    assert result.task_status == "DOMAIN_REVIEW"
    assert audit.handoffs[0][3]["schema_version"] == "4.0"
    assert audit.handoffs[0][-1] == {"specialist_report": report}


def test_manager_plan_v2_migrates_deprecated_context_tool_without_mutating_matrix_event() -> None:
    planning, receipts = Planning(), Receipts()
    adapter = SupervisorMatrixAdapter(planning, Audit(), Completion(), Directory(), receipts)  # type: ignore[arg-type]
    actor, run_id, task_id = Actor(uuid4(), "matrix-bridge"), uuid4(), uuid4()
    document = {
        "tasks": [
            {
                "target_agent": "user-evidence",
                "tool_policy": ["launchscope-context.get.v1", "browser-audit.v1"],
            }
        ]
    }
    raw = event("$plan-v2-tool-alias", "@manager:local", "ManagerPlanV2", document=document)

    adapter.consume(actor, raw, run_id=run_id, task_id=task_id)

    accepted_document = planning.calls[0][3]
    assert isinstance(accepted_document, dict)
    assert accepted_document["tasks"][0]["tool_policy"] == [
        "launchscope-context.get.v2",
        "browser-audit.v1",
    ]
    assert document["tasks"][0]["tool_policy"][0] == "launchscope-context.get.v1"


def test_generation_v4_matrix_replay_is_idempotent_before_business_handler() -> None:
    planning, receipts = Planning(), Receipts()
    adapter = SupervisorMatrixAdapter(planning, Audit(), Completion(), Directory(), receipts)  # type: ignore[arg-type]
    actor, run_id, task_id = Actor(uuid4(), "matrix-bridge"), uuid4(), uuid4()
    raw = event("$same", "@manager:local", "ManagerPlanV1", document={"plan_id": str(uuid4())})

    first = adapter.consume(actor, raw, run_id=run_id, task_id=task_id)
    replay = adapter.consume(actor, raw, run_id=run_id, task_id=task_id)

    assert first.duplicate is False and replay.duplicate is True
    assert len(planning.calls) == 1


def test_generation_v6_can_reprocess_a_canonical_event_after_a_synthetic_budget_receipt() -> None:
    amendment_id = uuid4()

    class SyntheticReceipt(Receipts):
        def __init__(self) -> None:
            super().__init__()
            self.items["$canonical"] = "synthetic-failure-digest"
            self.replays: list[dict[str, object]] = []

        def authorize_replay(self, _actor: Actor, **values: object) -> object:
            assert values["matrix_event_id"] == "$canonical"
            assert values["message_type"] == "AgentHandoffV4"
            return amendment_id

        def record_replay(self, _actor: Actor, **values: object) -> None:
            self.replays.append(values)

        def record(self, _actor: Actor, **values: object) -> None:
            raise AssertionError("canonical replay must preserve the synthetic receipt")

    class Settlement:
        def __init__(self) -> None:
            self.prepared = 0
            self.completed = 0

        def prepare(self, *_args: object) -> None:
            self.prepared += 1

        def complete(self, *_args: object) -> None:
            self.completed += 1

    receipts = SyntheticReceipt()
    settlement = Settlement()
    audit = Audit()
    adapter = SupervisorMatrixAdapter(
        Planning(), audit, Completion(), Directory(), receipts, settlement  # type: ignore[arg-type]
    )
    actor, run_id, task_id = Actor(uuid4(), "matrix-bridge"), uuid4(), uuid4()
    raw = event(
        "$canonical",
        "@user:local",
        "AgentHandoffV4",
        document={"schema_version": "4.0", "agent_code": "user-evidence", "dispatch_epoch": 19},
    )

    result = adapter.consume(actor, raw, run_id=run_id, task_id=task_id)

    assert result.duplicate is False and result.task_status == "DOMAIN_REVIEW"
    assert settlement.prepared == settlement.completed == 1
    assert receipts.replays[0]["amendment_id"] == amendment_id
    assert len(audit.handoffs) == 1


def test_generation_v6_can_reprocess_a_settled_canonical_audit_after_a_synthetic_receipt() -> None:
    recovery_id = uuid4()

    class SyntheticAuditReceipt(Receipts):
        def __init__(self) -> None:
            super().__init__()
            self.items["$canonical-audit"] = "synthetic-failure-digest"
            self.replays: list[dict[str, object]] = []

        def authorize_replay(self, _actor: Actor, **values: object) -> object:
            assert values["matrix_event_id"] == "$canonical-audit"
            assert values["message_type"] == "AuditResultV4"
            return recovery_id

        def record_replay(self, _actor: Actor, **values: object) -> None:
            self.replays.append(values)

        def record(self, _actor: Actor, **values: object) -> None:
            raise AssertionError("canonical audit replay must preserve the synthetic receipt")

    class Settlement:
        def prepare(self, *_args: object) -> None:
            pass

        def complete(self, *_args: object) -> None:
            pass

    receipts = SyntheticAuditReceipt()
    audit = Audit()
    completion = Completion()
    adapter = SupervisorMatrixAdapter(
        Planning(), audit, completion, Directory(), receipts, Settlement()  # type: ignore[arg-type]
    )
    actor, run_id, task_id = Actor(uuid4(), "matrix-bridge"), uuid4(), uuid4()

    result = adapter.consume(
        actor,
        event("$canonical-audit", "@auditor:local", "AuditResultV4", document=[]),
        run_id=run_id,
        task_id=task_id,
    )

    assert result.duplicate is False and result.run_status == "SUPERVISOR_SYNTHESIS"
    assert receipts.replays[0]["amendment_id"] == recovery_id
    assert len(audit.audits) == 1
    assert len(completion.prepared) == 1


def test_generation_v6_can_reprocess_a_settled_manager_synthesis_after_a_synthetic_receipt() -> None:
    recovery_id = uuid4()

    class SyntheticSynthesisReceipt(Receipts):
        def __init__(self) -> None:
            super().__init__()
            self.items["$canonical-synthesis"] = "synthetic-failure-digest"
            self.replays: list[dict[str, object]] = []

        def authorize_replay(self, _actor: Actor, **values: object) -> object:
            assert values["matrix_event_id"] == "$canonical-synthesis"
            assert values["message_type"] == "ManagerSynthesisV2"
            return recovery_id

        def record_replay(self, _actor: Actor, **values: object) -> None:
            self.replays.append(values)

        def record(self, _actor: Actor, **values: object) -> None:
            raise AssertionError("canonical synthesis replay must preserve the synthetic receipt")

    class Settlement:
        def prepare(self, *_args: object) -> None:
            pass

        def complete(self, *_args: object) -> None:
            pass

    receipts = SyntheticSynthesisReceipt()
    completion = Completion()
    adapter = SupervisorMatrixAdapter(
        Planning(), Audit(), completion, Directory(), receipts, Settlement()  # type: ignore[arg-type]
    )
    actor, run_id, task_id = Actor(uuid4(), "matrix-bridge"), uuid4(), uuid4()

    result = adapter.consume(
        actor,
        event(
            "$canonical-synthesis",
            "@manager:local",
            "ManagerSynthesisV2",
            document={"schema_version": "2.0"},
        ),
        run_id=run_id,
        task_id=task_id,
    )

    assert result.duplicate is False and result.run_status == "COMPLETED"
    assert result.report_id is not None
    assert receipts.replays[0]["amendment_id"] == recovery_id
    assert len(completion.committed) == 1


def test_generation_v4_matrix_sender_cannot_impersonate_another_role() -> None:
    adapter = SupervisorMatrixAdapter(Planning(), Audit(), Completion(), Directory(), Receipts())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="does not own"):
        adapter.consume(
            Actor(uuid4(), "matrix-bridge"),
            event("$bad", "@user:local", "ManagerPlanV1", document={}),
            run_id=uuid4(),
            task_id=uuid4(),
        )
