"""PostgreSQL-backed budget reservation with fail-closed unknown-cost handling."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    audit_event,
    budget_reservation,
    evaluation_run,
    usage_record,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_domain.value_objects import TenantScope


class BudgetExceeded(RuntimeError):
    """The requested reservation/consumption exceeds the frozen limit."""


class BudgetUnknown(RuntimeError):
    """The provider may have charged but the amount is unknown; the Run is frozen."""


@dataclass(frozen=True, slots=True)
class Reservation:
    reservation_id: UUID
    run_id: UUID
    category: str
    limit: Decimal
    reserved: Decimal
    consumed: Decimal
    released: Decimal
    currency: str
    status: str


class BudgetApplication:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def reserve(
        self,
        scope: TenantScope,
        *,
        category: str,
        limit: Decimal,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
        actor_id: str,
    ) -> Reservation:
        if scope.run_id is None:
            raise ValueError("run scope is required")
        if amount < 0 or limit < 0 or amount > limit:
            self._mark_waiting_for_budget(scope, actor_id, category, amount, limit)
            raise BudgetExceeded("budget reservation exceeds the frozen category limit")
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, scope, actor_id=actor_id) as session:
            session.execute(
                select(evaluation_run.c.id)
                .where(evaluation_run.c.tenant_id == scope.tenant_id, evaluation_run.c.id == scope.run_id)
                .with_for_update()
            ).scalar_one()
            existing = self._by_key(session, scope.tenant_id, idempotency_key)
            if existing is not None:
                if (
                    existing.category != category
                    or existing.limit != limit
                    or existing.reserved != amount
                    or existing.currency != currency
                ):
                    raise ValueError("idempotency key was reused with different budget parameters")
                return existing
            reservation_id = uuid4()
            session.execute(
                budget_reservation.insert().values(
                    id=reservation_id,
                    tenant_id=scope.tenant_id,
                    run_id=scope.run_id,
                    category=category,
                    currency=currency,
                    limit_amount=limit,
                    reserved_amount=amount,
                    consumed_amount=Decimal("0"),
                    released_amount=Decimal("0"),
                    status="RESERVED",
                    idempotency_key=idempotency_key,
                    created_at=now,
                    updated_at=now,
                )
            )
            self._audit(
                session, scope, actor_id, "budget.reserved", "SUCCESS", {"category": category, "amount": str(amount)}
            )
            return Reservation(
                reservation_id, scope.run_id, category, limit, amount, Decimal("0"), Decimal("0"), currency, "RESERVED"
            )

    def consume(
        self,
        scope: TenantScope,
        reservation_id: UUID,
        *,
        amount: Decimal | None,
        idempotency_key: str,
        actor_id: str,
    ) -> Reservation:
        if scope.run_id is None:
            raise ValueError("run scope is required")
        failure: Exception | None = None
        result: Reservation | None = None
        with tenant_transaction(self._sessions, scope, actor_id=actor_id) as session:
            row = self._locked(session, scope, reservation_id)
            if amount is None:
                self._freeze_unknown(session, scope, actor_id, row.category)
                failure = BudgetUnknown("cost status is unknown; run frozen without retry or settlement")
            elif amount < 0 or row.consumed + row.released + amount > row.reserved:
                self._freeze_budget(session, scope, actor_id, row.category)
                failure = BudgetExceeded("budget consumption exceeds the reserved amount")
            else:
                duplicate = session.execute(
                    select(usage_record.c.id).where(
                        usage_record.c.tenant_id == scope.tenant_id,
                        usage_record.c.idempotency_key == idempotency_key,
                    )
                ).scalar_one_or_none()
                if duplicate is None:
                    session.execute(
                        usage_record.insert().values(
                            id=uuid4(),
                            tenant_id=scope.tenant_id,
                            run_id=scope.run_id,
                            task_id=None,
                            category=row.category,
                            quantity=amount,
                            cost=amount,
                            idempotency_key=idempotency_key,
                            created_at=datetime.now(UTC),
                        )
                    )
                    session.execute(
                        update(budget_reservation)
                        .where(
                            budget_reservation.c.id == reservation_id, budget_reservation.c.tenant_id == scope.tenant_id
                        )
                        .values(consumed_amount=row.consumed + amount, status="CONSUMED", updated_at=datetime.now(UTC))
                    )
                    self._audit(
                        session,
                        scope,
                        actor_id,
                        "budget.consumed",
                        "SUCCESS",
                        {"category": row.category, "amount": str(amount)},
                    )
                result = self._locked(session, scope, reservation_id)
        if failure is not None:
            raise failure
        assert result is not None
        return result

    def release(self, scope: TenantScope, reservation_id: UUID, *, actor_id: str) -> Reservation:
        with tenant_transaction(self._sessions, scope, actor_id=actor_id) as session:
            row = self._locked(session, scope, reservation_id)
            remaining = row.reserved - row.consumed - row.released
            session.execute(
                update(budget_reservation)
                .where(budget_reservation.c.id == reservation_id, budget_reservation.c.tenant_id == scope.tenant_id)
                .values(released_amount=row.released + remaining, status="RELEASED", updated_at=datetime.now(UTC))
            )
            self._audit(
                session,
                scope,
                actor_id,
                "budget.released",
                "SUCCESS",
                {"category": row.category, "amount": str(remaining)},
            )
            return self._locked(session, scope, reservation_id)

    def _mark_waiting_for_budget(
        self, scope: TenantScope, actor_id: str, category: str, amount: Decimal, limit: Decimal
    ) -> None:
        if scope.run_id is None:
            return
        with tenant_transaction(self._sessions, scope, actor_id=actor_id) as session:
            session.execute(
                update(evaluation_run)
                .where(evaluation_run.c.tenant_id == scope.tenant_id, evaluation_run.c.id == scope.run_id)
                .values(
                    status="WAITING_FOR_BUDGET",
                    last_failure_class="BUDGET",
                    attention_reason="budget reservation unavailable",
                    updated_at=datetime.now(UTC),
                )
            )
            self._audit(
                session,
                scope,
                actor_id,
                "budget.reservation_denied",
                "DENIED",
                {"category": category, "amount": str(amount), "limit": str(limit)},
            )

    def _freeze_budget(self, session: Session, scope: TenantScope, actor_id: str, category: str) -> None:
        self._freeze(session, scope, actor_id, category, "BUDGET", "budget consumption exceeded reservation")

    def _freeze_unknown(self, session: Session, scope: TenantScope, actor_id: str, category: str) -> None:
        self._freeze(
            session,
            scope,
            actor_id,
            category,
            "SUBMISSION_UNKNOWN",
            "provider cost status unknown; automatic action frozen",
        )

    def _freeze(
        self, session: Session, scope: TenantScope, actor_id: str, category: str, failure: str, reason: str
    ) -> None:
        session.execute(
            update(evaluation_run)
            .where(evaluation_run.c.tenant_id == scope.tenant_id, evaluation_run.c.id == scope.run_id)
            .values(
                status="NEEDS_ATTENTION",
                last_failure_class=failure,
                attention_reason=reason,
                updated_at=datetime.now(UTC),
            )
        )
        self._audit(
            session, scope, actor_id, "budget.frozen", "FROZEN", {"category": category, "failure_class": failure}
        )

    @staticmethod
    def _audit(
        session: Session, scope: TenantScope, actor_id: str, action: str, outcome: str, metadata: dict[str, str]
    ) -> None:
        digest = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
        session.execute(
            audit_event.insert().values(
                id=uuid4(),
                tenant_id=scope.tenant_id,
                run_id=scope.run_id,
                actor_type=actor_id,
                action=action,
                outcome=outcome,
                payload_sha256=digest,
                metadata=metadata,
                occurred_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _by_key(session: Session, tenant_id: UUID, key: str) -> Reservation | None:
        row = (
            session.execute(
                select(budget_reservation).where(
                    budget_reservation.c.tenant_id == tenant_id, budget_reservation.c.idempotency_key == key
                )
            )
            .mappings()
            .first()
        )
        return BudgetApplication._record(dict(row)) if row else None

    @staticmethod
    def _locked(session: Session, scope: TenantScope, reservation_id: UUID) -> Reservation:
        row = (
            session.execute(
                select(budget_reservation)
                .where(budget_reservation.c.tenant_id == scope.tenant_id, budget_reservation.c.id == reservation_id)
                .with_for_update()
            )
            .mappings()
            .one()
        )
        return BudgetApplication._record(dict(row))

    @staticmethod
    def _record(row: Mapping[str, Any]) -> Reservation:
        return Reservation(
            row["id"],
            row["run_id"],
            row["category"],
            row["limit_amount"],
            row["reserved_amount"],
            row["consumed_amount"],
            row["released_amount"],
            row["currency"],
            row["status"],
        )
