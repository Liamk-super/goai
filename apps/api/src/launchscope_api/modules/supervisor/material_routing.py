from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from launchscope_api.infrastructure.db.schema import (
    agent_plan,
    evaluation_run,
    material_selection,
    material_selection_item,
    material_unit,
    task,
    task_material_scope,
)

_UNIT_REF = re.compile(r"^material-unit:([0-9a-f-]{36})@([a-f0-9]{64})$")


class MaterialRoutingValidationError(ValueError):
    pass


def validate_material_scopes(
    session: Session,
    tenant_id: UUID,
    product_version_id: UUID,
    document: Mapping[str, object],
) -> dict[str, tuple[dict[str, object], ...]]:
    selection_id = session.execute(
        select(material_selection.c.id)
        .where(
            material_selection.c.tenant_id == tenant_id,
            material_selection.c.product_version_id == product_version_id,
        )
        .order_by(material_selection.c.revision.desc())
        .limit(1)
    ).scalar_one_or_none()
    if selection_id is None:
        raise MaterialRoutingValidationError("generation-v5 planning requires a frozen material selection")
    included = _included_materials(session, tenant_id, selection_id)
    tasks = document.get("tasks")
    if not isinstance(tasks, list):
        raise MaterialRoutingValidationError("manager plan tasks are invalid")
    resolved: dict[str, tuple[dict[str, object], ...]] = {}
    for task_document in tasks:
        if not isinstance(task_document, Mapping):
            raise MaterialRoutingValidationError("manager plan task is invalid")
        task_key = str(task_document.get("task_key") or "")
        scopes = task_document.get("material_scope")
        if not isinstance(scopes, list) or not scopes:
            raise MaterialRoutingValidationError(f"{task_key} requires at least one material scope")
        task_scopes: list[dict[str, object]] = []
        seen_scope_ids: set[UUID] = set()
        for scope in scopes:
            if not isinstance(scope, Mapping):
                raise MaterialRoutingValidationError("material scope must be an object")
            scope_id = UUID(str(scope.get("scope_id")))
            material_id = UUID(str(scope.get("material_id")))
            if scope_id in seen_scope_ids:
                raise MaterialRoutingValidationError("material scope ids must be unique within a Task")
            seen_scope_ids.add(scope_id)
            analysis_id = included.get(material_id)
            if analysis_id is None:
                raise MaterialRoutingValidationError("material scope references excluded or unavailable material")
            unit_refs = scope.get("unit_refs")
            if not isinstance(unit_refs, list) or not 1 <= len(unit_refs) <= 50:
                raise MaterialRoutingValidationError("material scope must contain 1 to 50 unit refs")
            declared_ids: list[UUID] = []
            declared_refs: list[str] = []
            for value in unit_refs:
                match = _UNIT_REF.fullmatch(str(value))
                if match is None:
                    raise MaterialRoutingValidationError("material scope contains an invalid unit ref")
                unit_id = UUID(match.group(1))
                row = session.execute(
                    select(material_unit.c.id, material_unit.c.sha256).where(
                        material_unit.c.tenant_id == tenant_id,
                        material_unit.c.id == unit_id,
                        material_unit.c.material_id == material_id,
                        material_unit.c.analysis_id == analysis_id,
                        material_unit.c.product_version_id == product_version_id,
                    )
                ).first()
                if row is None or row.sha256 != match.group(2):
                    raise MaterialRoutingValidationError("material scope unit integrity or ownership validation failed")
                declared_ids.append(unit_id)
                declared_refs.append(str(value))
            child_rows = session.execute(
                select(material_unit.c.id, material_unit.c.sha256).where(
                    material_unit.c.tenant_id == tenant_id,
                    material_unit.c.analysis_id == analysis_id,
                    material_unit.c.parent_unit_id.in_(declared_ids),
                )
            ).all()
            authorized_ids = [*declared_ids, *(UUID(str(row.id)) for row in child_rows)]
            authorized_refs = [*declared_refs, *(f"material-unit:{row.id}@{row.sha256}" for row in child_rows)]
            normalized = {
                "scope_id": str(scope_id),
                "material_id": str(material_id),
                "analysis_id": str(analysis_id),
                "declared_unit_refs": declared_refs,
                "authorized_unit_ids": [str(value) for value in authorized_ids],
                "authorized_unit_refs": authorized_refs,
                "reason": str(scope.get("reason") or "")[:1000],
                "required": bool(scope.get("required")),
            }
            normalized["scope_sha256"] = hashlib.sha256(_canonical(normalized)).hexdigest()
            task_scopes.append(normalized)
        resolved[task_key] = tuple(task_scopes)
    return _complete_selected_material_coverage(
        session,
        tenant_id,
        product_version_id,
        included,
        resolved,
    )


def _included_materials(session: Session, tenant_id: UUID, selection_id: UUID) -> dict[UUID, UUID]:
    return {
        UUID(str(row.material_id)): UUID(str(row.analysis_id))
        for row in session.execute(
            select(material_selection_item.c.material_id, material_selection_item.c.analysis_id).where(
                material_selection_item.c.tenant_id == tenant_id,
                material_selection_item.c.selection_id == selection_id,
                material_selection_item.c.decision.in_(("INCLUDE", "INCLUDE_PARTIAL")),
            )
        )
    }


def _automatic_scope(
    session: Session,
    tenant_id: UUID,
    product_version_id: UUID,
    material_id: UUID,
    analysis_id: UUID,
) -> dict[str, object]:
    rows = session.execute(
        select(material_unit.c.id, material_unit.c.sha256)
        .where(
            material_unit.c.tenant_id == tenant_id,
            material_unit.c.product_version_id == product_version_id,
            material_unit.c.material_id == material_id,
            material_unit.c.analysis_id == analysis_id,
            (material_unit.c.parent_unit_id.is_(None)) | (material_unit.c.unit_type == "SECTION"),
        )
        .order_by(material_unit.c.ordinal, material_unit.c.id)
        .limit(50)
    ).all()
    if not rows:
        raise MaterialRoutingValidationError("included material has no readable top-level units")
    unit_ids = [str(row.id) for row in rows]
    unit_refs = [f"material-unit:{row.id}@{row.sha256}" for row in rows]
    normalized: dict[str, object] = {
        "scope_id": str(uuid4()),
        "material_id": str(material_id),
        "analysis_id": str(analysis_id),
        "declared_unit_refs": unit_refs,
        "authorized_unit_ids": unit_ids,
        "authorized_unit_refs": unit_refs,
        "reason": "control-plane coverage for a user-included material",
        "required": True,
    }
    normalized["scope_sha256"] = hashlib.sha256(_canonical(normalized)).hexdigest()
    return normalized


def _complete_selected_material_coverage(
    session: Session,
    tenant_id: UUID,
    product_version_id: UUID,
    included: Mapping[UUID, UUID],
    resolved: dict[str, tuple[dict[str, object], ...]],
) -> dict[str, tuple[dict[str, object], ...]]:
    completed: dict[str, tuple[dict[str, object], ...]] = {}
    for task_key, scopes in resolved.items():
        existing = {UUID(str(scope["material_id"])) for scope in scopes}
        additions = tuple(
            _automatic_scope(session, tenant_id, product_version_id, material_id, analysis_id)
            for material_id, analysis_id in included.items()
            if material_id not in existing
        )
        completed[task_key] = (*scopes, *additions)
    return completed


def repair_recovered_task_material_routes(
    session: Session,
    tenant_id: UUID,
    run_id: UUID,
    task_ids: tuple[UUID, ...],
    now: datetime,
) -> None:
    run = session.execute(
        select(evaluation_run.c.product_version_id).where(
            evaluation_run.c.tenant_id == tenant_id,
            evaluation_run.c.id == run_id,
        )
    ).one()
    selection_id = session.execute(
        select(material_selection.c.id)
        .where(
            material_selection.c.tenant_id == tenant_id,
            material_selection.c.product_version_id == run.product_version_id,
        )
        .order_by(material_selection.c.revision.desc())
        .limit(1)
    ).scalar_one_or_none()
    if selection_id is None:
        return
    included = _included_materials(session, tenant_id, selection_id)
    plan_id = session.execute(
        select(agent_plan.c.id)
        .where(
            agent_plan.c.tenant_id == tenant_id,
            agent_plan.c.run_id == run_id,
            agent_plan.c.status == "ACCEPTED",
        )
        .order_by(agent_plan.c.plan_version.desc())
        .limit(1)
    ).scalar_one_or_none()
    if plan_id is None:
        return
    rows = session.execute(
        select(task.c.id, task.c.agent_identity_ref, task.c.tool_allowlist).where(
            task.c.tenant_id == tenant_id,
            task.c.run_id == run_id,
            task.c.id.in_(task_ids),
            task.c.stage_code == "DOMAIN_REVIEW",
        )
    ).mappings().all()
    for row in rows:
        agent = str(row["agent_identity_ref"]).split("@", 1)[0]
        if agent not in {"user-evidence", "product-engineering", "business-investment"}:
            continue
        tools = list(dict.fromkeys([*(row["tool_allowlist"] or []), "material.read.v1"]))
        session.execute(update(task).where(task.c.id == row["id"]).values(tool_allowlist=tools, updated_at=now))
        existing = set(
            session.execute(
                select(task_material_scope.c.material_id).where(
                    task_material_scope.c.tenant_id == tenant_id,
                    task_material_scope.c.run_id == run_id,
                    task_material_scope.c.task_id == row["id"],
                )
            ).scalars()
        )
        additions = tuple(
            _automatic_scope(
                session,
                tenant_id,
                UUID(str(run.product_version_id)),
                material_id,
                analysis_id,
            )
            for material_id, analysis_id in included.items()
            if material_id not in existing
        )
        persist_task_scopes(session, tenant_id, run_id, UUID(str(row["id"])), plan_id, additions, now)


def scopes_for_task_key(
    resolved: dict[str, tuple[dict[str, object], ...]],
    task_key: str,
) -> tuple[dict[str, object], ...]:
    return resolved.get(task_key, ())


def persist_task_scopes(
    session: Session,
    tenant_id: UUID,
    run_id: UUID,
    task_id: UUID,
    plan_id: UUID,
    scopes: tuple[dict[str, object], ...],
    now: object,
) -> None:
    for scope in scopes:
        session.execute(
            task_material_scope.insert().values(
                id=uuid4(),
                tenant_id=tenant_id,
                run_id=run_id,
                task_id=task_id,
                plan_id=plan_id,
                material_id=UUID(str(scope["material_id"])),
                analysis_id=UUID(str(scope["analysis_id"])),
                unit_ids=scope["authorized_unit_ids"],
                unit_refs=scope["authorized_unit_refs"],
                reason=scope["reason"],
                required=scope["required"],
                scope_sha256=scope["scope_sha256"],
                created_at=now,
            )
        )


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


__all__ = [
    "MaterialRoutingValidationError",
    "persist_task_scopes",
    "repair_recovered_task_material_routes",
    "scopes_for_task_key",
    "validate_material_scopes",
]
