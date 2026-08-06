"""PostgreSQL adapter for the EvaluationRun aggregate and task graph."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from launchscope_domain.aggregates.evaluation_run import (
    EvaluationRun,
    RunManifest,
    RunTransitionRecord,
    Stage,
    Task,
)
from launchscope_domain.enums import STAGE_ORDER, FailureClass, RunStatus, StageCode, StageStatus, TaskStatus
from launchscope_domain.ports.repositories import EvaluationRunRepository as EvaluationRunPort
from launchscope_domain.value_objects import BudgetReservation, TenantScope

from ..db.schema import (
    evaluation_run,
    project,
    run_manifest,
    run_status_history,
    stage,
    task,
    task_dependency,
)
from .base import (
    assert_aggregate_scope,
    existing_row,
    insert_if_absent,
    json_value,
    require_scope_id,
    require_utc_datetime,
    utc_datetime,
)


def _stage_id(run_id: UUID, code: StageCode) -> UUID:
    return uuid5(NAMESPACE_URL, f"launchscope.stage:{run_id}:{code.value}")


def _manifest_budget(manifest: RunManifest) -> dict[str, object]:
    return {
        "reservations": [
            {
                "run_id": str(item.run_id),
                "category": item.category,
                "limit": str(item.limit),
                "reserved": str(item.reserved),
                "consumed": str(item.consumed),
                "currency": item.currency,
            }
            for item in manifest.budget_limits
        ]
    }


def _manifest_reservations(payload: object) -> tuple[BudgetReservation, ...]:
    if not isinstance(payload, dict):
        return ()
    values = payload.get("reservations", [])
    if not isinstance(values, list):
        return ()
    return tuple(
        BudgetReservation(
            run_id=item["run_id"],
            category=item["category"],
            limit=item["limit"],
            reserved=item["reserved"],
            consumed=item.get("consumed", "0"),
            currency=item.get("currency", "unit"),
        )
        for item in values
        if isinstance(item, dict)
    )


def _budget_slice_payload(value: BudgetReservation | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "run_id": str(value.run_id),
        "category": value.category,
        "limit": str(value.limit),
        "reserved": str(value.reserved),
        "consumed": str(value.consumed),
        "currency": value.currency,
    }


def _budget_slice_from_payload(value: object) -> BudgetReservation | None:
    if not isinstance(value, dict):
        return None
    return BudgetReservation(
        run_id=value["run_id"],
        category=value["category"],
        limit=value["limit"],
        reserved=value["reserved"],
        consumed=value.get("consumed", "0"),
        currency=value.get("currency", "unit"),
    )


class SqlAlchemyEvaluationRunRepository(EvaluationRunPort):
    """Persist Run state and graph facts without owning transaction commit."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, resource_id: UUID, scope: TenantScope) -> EvaluationRun | None:
        row = (
            self.session.execute(
                select(evaluation_run).where(
                    evaluation_run.c.id == resource_id,
                    evaluation_run.c.tenant_id == scope.tenant_id,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        project_row = self.session.execute(
            select(project.c.workspace_id).where(
                project.c.id == row["project_id"],
                project.c.tenant_id == scope.tenant_id,
            )
        ).first()
        if project_row is None:
            return None
        run_scope = TenantScope(
            tenant_id=scope.tenant_id,
            workspace_id=project_row[0],
            project_id=row["project_id"],
            product_version_id=row["product_version_id"],
            run_id=row["id"],
        )
        stage_rows = (
            self.session.execute(
                select(stage)
                .where(stage.c.tenant_id == scope.tenant_id, stage.c.run_id == resource_id)
                .order_by(stage.c.ordinal)
            )
            .mappings()
            .all()
        )
        task_rows = (
            self.session.execute(select(task).where(task.c.tenant_id == scope.tenant_id, task.c.run_id == resource_id))
            .mappings()
            .all()
        )
        tasks: dict[UUID, Task] = {}
        task_ids_by_stage: dict[StageCode, list[UUID]] = {}
        for task_row in task_rows:
            dependencies = task_row["dependencies"] if isinstance(task_row["dependencies"], list) else []
            success_condition = task_row["success_condition"]
            if isinstance(success_condition, dict):
                success_condition = success_condition.get("description", "schema_valid_and_success_condition")
            failure = task_row["last_failure_class"]
            tasks[task_row["id"]] = Task(
                task_id=task_row["id"],
                run_id=resource_id,
                stage_code=StageCode(task_row["stage_code"]),
                agent_identity_ref=task_row["agent_identity_ref"],
                skill_ref=task_row["skill_ref"],
                skill_version=task_row["skill_version"],
                dependencies=tuple(UUID(str(value)) for value in dependencies),
                tool_allowlist=tuple(task_row["tool_allowlist"] or []),
                budget_slice=_budget_slice_from_payload(task_row["budget_slice"]),
                timeout_seconds=task_row["timeout_seconds"],
                success_condition=str(success_condition),
                evidence_requirement=task_row["evidence_requirement"],
                required=task_row["required"],
                idempotency_key=task_row["idempotency_key"],
                lease_token=task_row["lease_token"],
                status=TaskStatus(task_row["status"]),
                correction_attempts=task_row["correction_attempts"],
                transient_retries=task_row["transient_retries"],
                last_failure_class=FailureClass(failure) if failure else None,
                last_error=task_row["last_error"],
                side_effect_started=task_row["side_effect_started"],
            )
            task_ids_by_stage.setdefault(StageCode(task_row["stage_code"]), []).append(task_row["id"])

        stages: dict[StageCode, Stage] = {}
        for stage_row in stage_rows:
            code = StageCode(stage_row["code"])
            stages[code] = Stage(
                code=code,
                status=StageStatus(stage_row["status"]),
                task_ids=tuple(task_ids_by_stage.get(code, [])),
                started_at=utc_datetime(stage_row["started_at"]),
                completed_at=utc_datetime(stage_row["completed_at"]),
            )

        history_rows = (
            self.session.execute(
                select(run_status_history)
                .where(run_status_history.c.tenant_id == scope.tenant_id, run_status_history.c.run_id == resource_id)
                .order_by(run_status_history.c.occurred_at, run_status_history.c.id)
            )
            .mappings()
            .all()
        )
        history = [
            RunTransitionRecord(
                from_status=RunStatus(item["from_status"]),
                to_status=RunStatus(item["to_status"]),
                reason=item["reason"],
                occurred_at=require_utc_datetime(item["occurred_at"]),
                failure_class=FailureClass(item["failure_class"]) if item["failure_class"] else None,
            )
            for item in history_rows
        ]

        manifest_row = (
            self.session.execute(
                select(run_manifest).where(
                    run_manifest.c.tenant_id == scope.tenant_id,
                    run_manifest.c.run_id == resource_id,
                )
            )
            .mappings()
            .first()
        )
        manifest = None
        if manifest_row is not None:
            configuration = manifest_row["frozen_config"] if isinstance(manifest_row["frozen_config"], dict) else {}
            security_policy = (
                manifest_row["security_policy"] if isinstance(manifest_row["security_policy"], dict) else {}
            )
            manifest = RunManifest(
                standard_version=row["standard_version"],
                material_ids=tuple(configuration.get("material_ids", [])),
                budget_limits=_manifest_reservations(manifest_row["budget"]),
                permissions=tuple(security_policy.get("permissions", [])),
                timeout_seconds=int(configuration.get("timeout_seconds", 900)),
                security_policy_version=str(security_policy.get("version", "1.0")),
                configuration=configuration,
                frozen=True,
                manifest_sha256=manifest_row["manifest_sha256"],
            )
        flags = row["state_flags"] if isinstance(row["state_flags"], dict) else {}
        return EvaluationRun(
            run_id=row["id"],
            scope=run_scope,
            product_version_id=row["product_version_id"],
            standard_version=row["standard_version"],
            status=RunStatus(row["status"]),
            current_stage=StageCode(row["current_stage"]) if row["current_stage"] else None,
            manifest=manifest,
            budget_reservations=_manifest_reservations(manifest_row["budget"]) if manifest_row else (),
            stages=stages,
            tasks=tasks,
            status_history=history,
            **{key: bool(flags.get(key, False)) for key in _FLAG_NAMES},
            last_failure_class=FailureClass(row["last_failure_class"]) if row["last_failure_class"] else None,
            attention_reason=row["attention_reason"],
        )

    def save(self, aggregate: EvaluationRun) -> None:
        scope = aggregate.scope
        assert_aggregate_scope(aggregate, scope)
        project_id = require_scope_id(scope, "project_id")
        version_id = require_scope_id(scope, "product_version_id")
        now = datetime.now(UTC)
        row = existing_row(self.session, evaluation_run, aggregate.run_id, scope)
        flags = {name: bool(getattr(aggregate, name)) for name in _FLAG_NAMES}
        values = {
            "id": aggregate.run_id,
            "tenant_id": scope.tenant_id,
            "project_id": project_id,
            "product_version_id": version_id,
            "status": aggregate.status.value,
            "current_stage": aggregate.current_stage.value if aggregate.current_stage else None,
            "state_flags": flags,
            "standard_version": aggregate.standard_version,
            "correlation_id": aggregate.run_id,
            "idempotency_key": f"run:{aggregate.run_id}",
            "last_failure_class": aggregate.last_failure_class.value if aggregate.last_failure_class else None,
            "attention_reason": aggregate.attention_reason,
            "created_at": now,
            "updated_at": now,
        }
        if row is None:
            self.session.execute(evaluation_run.insert().values(**values))
        else:
            self.session.execute(
                update(evaluation_run)
                .where(evaluation_run.c.id == aggregate.run_id, evaluation_run.c.tenant_id == scope.tenant_id)
                .values(**{key: value for key, value in values.items() if key not in {"id", "tenant_id", "created_at"}})
            )

        if aggregate.manifest is not None:
            manifest = aggregate.manifest
            manifest_values = {
                "run_id": aggregate.run_id,
                "tenant_id": scope.tenant_id,
                "manifest_sha256": manifest.run_manifest_sha256,
                "frozen_config": json_value(
                    {
                        **dict(manifest.configuration),
                        "material_ids": [str(value) for value in manifest.material_ids],
                        "timeout_seconds": manifest.timeout_seconds,
                    }
                ),
                "budget": json_value(_manifest_budget(manifest)),
                "security_policy": json_value(
                    {"version": manifest.security_policy_version, "permissions": manifest.permissions}
                ),
                "created_at": now,
            }
            if (
                self.session.execute(
                    select(run_manifest.c.run_id).where(
                        run_manifest.c.tenant_id == scope.tenant_id,
                        run_manifest.c.run_id == aggregate.run_id,
                    )
                ).first()
                is None
            ):
                self.session.execute(run_manifest.insert().values(**manifest_values))
            else:
                existing_manifest = self.session.execute(
                    select(run_manifest.c.manifest_sha256).where(
                        run_manifest.c.tenant_id == scope.tenant_id,
                        run_manifest.c.run_id == aggregate.run_id,
                    )
                ).scalar_one()
                if existing_manifest != manifest.run_manifest_sha256:
                    raise ValueError("RunManifest is immutable after first persistence")

        stage_ids: dict[StageCode, UUID] = {}
        for ordinal, code in enumerate(STAGE_ORDER, start=1):
            stage_value = aggregate.stages.get(code, Stage(code))
            stage_id = _stage_id(aggregate.run_id, code)
            stage_ids[code] = stage_id
            stage_values = {
                "id": stage_id,
                "tenant_id": scope.tenant_id,
                "run_id": aggregate.run_id,
                "code": code.value,
                "ordinal": ordinal,
                "status": stage_value.status.value,
                "started_at": stage_value.started_at,
                "completed_at": stage_value.completed_at,
            }
            if self.session.execute(select(stage.c.id).where(stage.c.id == stage_id)).first() is None:
                self.session.execute(stage.insert().values(**stage_values))
            else:
                self.session.execute(
                    update(stage)
                    .where(stage.c.id == stage_id, stage.c.tenant_id == scope.tenant_id)
                    .values(
                        status=stage_value.status.value,
                        started_at=stage_value.started_at,
                        completed_at=stage_value.completed_at,
                    )
                )

        for task_value in aggregate.tasks.values():
            task_values = {
                "id": task_value.task_id,
                "tenant_id": scope.tenant_id,
                "run_id": aggregate.run_id,
                "stage_id": stage_ids[task_value.stage_code],
                "agent_identity_id": None,
                "skill_version_id": None,
                "stage_code": task_value.stage_code.value,
                "agent_identity_ref": task_value.agent_identity_ref,
                "skill_ref": task_value.skill_ref,
                "skill_version": task_value.skill_version,
                "status": task_value.status.value,
                "lease_token": task_value.lease_token,
                "idempotency_key": task_value.idempotency_key,
                "dependencies": json_value(task_value.dependencies),
                "tool_allowlist": json_value(task_value.tool_allowlist),
                "budget_slice": _budget_slice_payload(task_value.budget_slice),
                "timeout_seconds": task_value.timeout_seconds,
                "success_condition": {"description": task_value.success_condition},
                "evidence_requirement": task_value.evidence_requirement,
                "required": task_value.required,
                "correction_attempts": task_value.correction_attempts,
                "transient_retries": task_value.transient_retries,
                "last_failure_class": task_value.last_failure_class.value if task_value.last_failure_class else None,
                "last_error": task_value.last_error,
                "side_effect_started": task_value.side_effect_started,
                "created_at": now,
                "updated_at": now,
            }
            if self.session.execute(select(task.c.id).where(task.c.id == task_value.task_id)).first() is None:
                self.session.execute(task.insert().values(**task_values))
            else:
                self.session.execute(
                    update(task)
                    .where(task.c.id == task_value.task_id, task.c.tenant_id == scope.tenant_id)
                    .values(
                        **{
                            key: value
                            for key, value in task_values.items()
                            if key not in {"id", "tenant_id", "created_at"}
                        }
                    )
                )
            for dependency_id in task_value.dependencies:
                dependency_row_id = uuid5(
                    NAMESPACE_URL,
                    f"launchscope.task-dependency:{task_value.task_id}:{dependency_id}",
                )
                insert_if_absent(
                    self.session,
                    task_dependency,
                    {
                        "id": dependency_row_id,
                        "tenant_id": scope.tenant_id,
                        "task_id": task_value.task_id,
                        "depends_on_task_id": dependency_id,
                        "created_at": now,
                    },
                    resource_id=dependency_row_id,
                )

        for index, transition in enumerate(aggregate.status_history):
            history_id = uuid5(
                NAMESPACE_URL,
                f"launchscope.run-history:{aggregate.run_id}:{index}:{transition.occurred_at.isoformat()}",
            )
            insert_if_absent(
                self.session,
                run_status_history,
                {
                    "id": history_id,
                    "tenant_id": scope.tenant_id,
                    "run_id": aggregate.run_id,
                    "from_status": transition.from_status.value,
                    "to_status": transition.to_status.value,
                    "reason": transition.reason,
                    "failure_class": transition.failure_class.value if transition.failure_class else None,
                    "occurred_at": transition.occurred_at,
                },
                resource_id=history_id,
            )


_FLAG_NAMES = (
    "gap_identified",
    "profile_confirmed",
    "material_profile_complete",
    "budget_reserved",
    "required_tasks_terminal",
    "audit_ready",
    "approval_valid",
    "decision_committed",
    "report_committed",
    "dossier_committed",
    "reconciliation_complete",
)

EvaluationRunRepositoryAdapter = SqlAlchemyEvaluationRunRepository

__all__ = ["EvaluationRunRepositoryAdapter", "SqlAlchemyEvaluationRunRepository"]
