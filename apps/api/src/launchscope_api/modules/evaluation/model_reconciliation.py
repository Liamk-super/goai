"""Delivery-level model usage reconciliation and exactly-once financial posting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, func, insert, select, update
from sqlalchemy.orm import Session

from launchscope_api.infrastructure.db.schema import (
    agentteams_task_delivery,
    budget_reservation,
    model_invocation,
    model_usage_reconciliation,
    run_manifest,
    usage_record,
)

from .agentteams_usage import AgentUsageReader, AgentUsageSnapshot, usage_delta
from .limit_amendment_application import effective_run_limits


@dataclass(frozen=True, slots=True)
class GatewayReconciliationOutcome:
    handled: bool
    failure_class: str | None = None
    reason: str | None = None


def reconcile_gateway_delivery_usage(
    session: Session | Connection,
    *,
    tenant_id: UUID,
    run_id: UUID,
    task_id: UUID,
    dispatch_epoch: int,
    agent_code: str,
    now: datetime,
    usage_reader: AgentUsageReader | None,
) -> GatewayReconciliationOutcome:
    delivery = session.execute(
        select(agentteams_task_delivery)
        .where(
            agentteams_task_delivery.c.tenant_id == tenant_id,
            agentteams_task_delivery.c.run_id == run_id,
            agentteams_task_delivery.c.task_id == task_id,
            agentteams_task_delivery.c.dispatch_epoch == dispatch_epoch,
        )
        .with_for_update()
    ).mappings().one_or_none()
    if delivery is None or delivery["accounting_mode"] != "GATEWAY_DELIVERY":
        return GatewayReconciliationOutcome(False)

    existing = session.execute(
        select(model_usage_reconciliation).where(
            model_usage_reconciliation.c.tenant_id == tenant_id,
            model_usage_reconciliation.c.delivery_id == delivery["id"],
        ).with_for_update()
    ).mappings().one_or_none()
    if existing is not None and existing["posted_at"] is not None:
        return GatewayReconciliationOutcome(True)

    invocations = session.execute(
        select(model_invocation)
        .where(
            model_invocation.c.tenant_id == tenant_id,
            model_invocation.c.delivery_id == delivery["id"],
        )
        .order_by(model_invocation.c.invocation_seq)
        .with_for_update()
    ).mappings().all()
    unresolved = [
        row
        for row in invocations
        if row["status"] in {"STARTED", "SUBMITTED", "SUBMISSION_UNKNOWN"}
        or row["delivery_status"] in {"TERMINAL_SEEN", "DELIVERY_UNKNOWN"}
    ]
    if unresolved:
        reason = "delivery contains an active or uncertain model invocation; automatic retry prohibited"
        _store_reconciliation(
            session,
            existing=existing,
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=task_id,
            delivery_id=delivery["id"],
            state="UNKNOWN",
            invocation_ids=[str(row["id"]) for row in invocations],
            gateway_usage={},
            copaw_baseline=delivery["usage_baseline"],
            copaw_terminal=None,
            usage_record_ids=[],
            difference_reason=reason,
            now=now,
        )
        return GatewayReconciliationOutcome(True, "SUBMISSION_UNKNOWN", reason)

    settled = [row for row in invocations if row["status"] == "SETTLED"]
    if not settled or any(row["prompt_tokens"] is None or row["completion_tokens"] is None for row in settled):
        reason = "delivery has no complete gateway usage receipt; automatic retry prohibited"
        _store_reconciliation(
            session,
            existing=existing,
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=task_id,
            delivery_id=delivery["id"],
            state="UNKNOWN",
            invocation_ids=[str(row["id"]) for row in invocations],
            gateway_usage={},
            copaw_baseline=delivery["usage_baseline"],
            copaw_terminal=None,
            usage_record_ids=[],
            difference_reason=reason,
            now=now,
        )
        return GatewayReconciliationOutcome(True, "SUBMISSION_UNKNOWN", reason)

    input_tokens = sum(int(row["prompt_tokens"]) for row in settled)
    output_tokens = sum(int(row["completion_tokens"]) for row in settled)
    call_count = len(settled)
    manifest = session.execute(select(run_manifest.c.frozen_config).where(
        run_manifest.c.tenant_id == tenant_id,
        run_manifest.c.run_id == run_id,
    )).scalar_one()
    pricing = manifest.get("model_pricing", {})
    cost_mode = str(pricing.get("cost_mode") or "TOKEN_ONLY").upper()
    if cost_mode not in {"EXACT", "TOKEN_ONLY"}:
        return GatewayReconciliationOutcome(True, "POLICY", "Run Manifest has an unsupported cost mode")
    cost = None
    if cost_mode == "EXACT":
        try:
            cost = (
                Decimal(input_tokens) * Decimal(str(pricing["input_usd_per_million_tokens"]))
                + Decimal(output_tokens) * Decimal(str(pricing["output_usd_per_million_tokens"]))
            ) / Decimal(1_000_000)
        except (KeyError, TypeError, ValueError, ArithmeticError):
            return GatewayReconciliationOutcome(True, "BILLING_UNKNOWN", "frozen model pricing is incomplete")

    gateway_usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "call_count": call_count,
        **({"cost_usd": str(cost)} if cost is not None else {}),
    }
    copaw_terminal: dict[str, object] | None = None
    difference_reason = None
    if usage_reader is not None and delivery["usage_baseline"] is not None:
        try:
            terminal = usage_reader.snapshot(agent_code)
            copaw_terminal = dict(terminal.to_dict())
            delta = usage_delta(
                AgentUsageSnapshot.from_dict(delivery["usage_baseline"]),
                terminal,
                task_key=f"{task_id}:{dispatch_epoch}",
            )
            if (
                delta.input_tokens != input_tokens
                or delta.output_tokens != output_tokens
                or delta.call_count != call_count
            ):
                difference_reason = "CoPaw cumulative counters do not match the gateway delivery ledger"
        except Exception as exc:  # noqa: BLE001 - gateway facts remain postable when advisory telemetry fails.
            difference_reason = f"CoPaw reconciliation unavailable: {type(exc).__name__}"
    else:
        difference_reason = "CoPaw reconciliation snapshot is unavailable"

    usage_ids = _post_delivery_usage_once(
        session,
        tenant_id=tenant_id,
        run_id=run_id,
        task_id=task_id,
        delivery_id=delivery["id"],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        call_count=call_count,
        cost=cost,
        now=now,
    )
    _store_reconciliation(
        session,
        existing=existing,
        tenant_id=tenant_id,
        run_id=run_id,
        task_id=task_id,
        delivery_id=delivery["id"],
        state="MATCHED" if difference_reason is None else "MISMATCH",
        invocation_ids=[str(row["id"]) for row in invocations],
        gateway_usage=gateway_usage,
        copaw_baseline=delivery["usage_baseline"],
        copaw_terminal=copaw_terminal,
        usage_record_ids=[str(value) for value in usage_ids],
        difference_reason=difference_reason,
        now=now,
    )

    limits = effective_run_limits(
        session,
        tenant_id,
        run_id,
        manifest_limits=manifest.get("limits", {}),
    )
    run_tokens = session.execute(select(func.coalesce(func.sum(usage_record.c.quantity), 0)).where(
        usage_record.c.tenant_id == tenant_id,
        usage_record.c.run_id == run_id,
        usage_record.c.category == "model",
    )).scalar_one()
    run_calls = session.execute(select(func.coalesce(func.sum(usage_record.c.quantity), 0)).where(
        usage_record.c.tenant_id == tenant_id,
        usage_record.c.run_id == run_id,
        usage_record.c.category == "model_calls",
    )).scalar_one()
    if int(run_calls) > int(limits.get("model_calls", 0)):
        return GatewayReconciliationOutcome(True, "BUDGET", "actual model calls exceeded the frozen Run limit")
    if int(run_tokens) > int(limits.get("input_tokens", 0)) + int(limits.get("output_tokens", 0)):
        return GatewayReconciliationOutcome(True, "BUDGET", "actual model Tokens exceeded the frozen Run limit")
    if cost is not None:
        reservation = session.execute(select(budget_reservation).where(
            budget_reservation.c.tenant_id == tenant_id,
            budget_reservation.c.run_id == run_id,
            budget_reservation.c.category == "run_total",
        ).with_for_update()).mappings().one()
        if reservation["consumed_amount"] + cost > reservation["reserved_amount"]:
            return GatewayReconciliationOutcome(True, "BUDGET", "actual provider cost exceeded the reserved budget")
        session.execute(update(budget_reservation).where(
            budget_reservation.c.id == reservation["id"],
            budget_reservation.c.tenant_id == tenant_id,
        ).values(
            consumed_amount=reservation["consumed_amount"] + cost,
            status="CONSUMED",
            updated_at=now,
        ))
    return GatewayReconciliationOutcome(True)


def _post_delivery_usage_once(
    session: Session | Connection,
    *,
    tenant_id: UUID,
    run_id: UUID,
    task_id: UUID,
    delivery_id: UUID,
    input_tokens: int,
    output_tokens: int,
    call_count: int,
    cost: Decimal | None,
    now: datetime,
) -> list[UUID]:
    key = f"delivery-model:{delivery_id}"
    existing = list(session.execute(select(usage_record.c.id, usage_record.c.idempotency_key).where(
        usage_record.c.tenant_id == tenant_id,
        usage_record.c.idempotency_key.in_((key, f"{key}:calls", f"{key}:cost-unavailable")),
    )).all())
    if existing:
        expected_keys = {key, f"{key}:calls"}
        if cost is None:
            expected_keys.add(f"{key}:cost-unavailable")
        if {str(row[1]) for row in existing} != expected_keys:
            raise RuntimeError("delivery usage posting is incomplete; automatic repair is prohibited")
        return [UUID(str(row[0])) for row in existing]
    rows = [
        {
            "id": uuid4(), "tenant_id": tenant_id, "run_id": run_id, "task_id": task_id,
            "category": "model", "quantity": input_tokens + output_tokens, "cost": cost or 0,
            "idempotency_key": key, "created_at": now,
        },
        {
            "id": uuid4(), "tenant_id": tenant_id, "run_id": run_id, "task_id": task_id,
            "category": "model_calls", "quantity": call_count, "cost": 0,
            "idempotency_key": f"{key}:calls", "created_at": now,
        },
    ]
    if cost is None:
        rows.append({
            "id": uuid4(), "tenant_id": tenant_id, "run_id": run_id, "task_id": task_id,
            "category": "model_cost_unavailable", "quantity": 1, "cost": 0,
            "idempotency_key": f"{key}:cost-unavailable", "created_at": now,
        })
    session.execute(insert(usage_record), rows)
    return [UUID(str(row["id"])) for row in rows]


def _store_reconciliation(
    session: Session | Connection,
    *,
    existing: Any,
    tenant_id: UUID,
    run_id: UUID,
    task_id: UUID,
    delivery_id: UUID,
    state: str,
    invocation_ids: list[str],
    gateway_usage: dict[str, object],
    copaw_baseline: dict[str, object] | None,
    copaw_terminal: dict[str, object] | None,
    usage_record_ids: list[str],
    difference_reason: str | None,
    now: datetime,
) -> None:
    values = {
        "state": state,
        "invocation_ids": invocation_ids,
        "gateway_usage": gateway_usage,
        "copaw_baseline": copaw_baseline,
        "copaw_terminal": copaw_terminal,
        "usage_record_ids": usage_record_ids,
        "difference_reason": difference_reason,
        "reconciled_at": now,
        "posted_at": now if state in {"MATCHED", "MISMATCH"} else None,
        "updated_at": now,
    }
    if existing is None:
        session.execute(insert(model_usage_reconciliation).values(
            id=uuid4(),
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=task_id,
            delivery_id=delivery_id,
            created_at=now,
            **values,
        ))
    else:
        session.execute(update(model_usage_reconciliation).where(
            model_usage_reconciliation.c.id == existing["id"],
            model_usage_reconciliation.c.tenant_id == tenant_id,
        ).values(**values))


__all__ = ["GatewayReconciliationOutcome", "reconcile_gateway_delivery_usage"]
