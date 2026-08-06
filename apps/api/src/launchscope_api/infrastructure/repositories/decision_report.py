"""PostgreSQL adapter for append-only Decision and Report history."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from launchscope_domain.aggregates.decision_report import Decision, DecisionReport, Report
from launchscope_domain.enums import DimensionCode, FindingGrade, Recommendation
from launchscope_domain.ports.repositories import DecisionReportRepository as DecisionReportPort
from launchscope_domain.value_objects import TenantScope

from ..db.schema import decision, decision_finding, report
from .base import assert_aggregate_scope, insert_if_absent, json_value, require_scope_id, require_utc_datetime


class SqlAlchemyDecisionReportRepository(DecisionReportPort):
    """Store report bodies as object metadata; never put report text in SQL."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, resource_id: UUID, scope: TenantScope) -> DecisionReport | None:
        run_id = require_scope_id(scope, "run_id")
        decision_rows = (
            self.session.execute(
                select(decision)
                .where(decision.c.tenant_id == scope.tenant_id, decision.c.run_id == run_id)
                .order_by(decision.c.created_at)
            )
            .mappings()
            .all()
        )
        report_rows = (
            self.session.execute(
                select(report)
                .where(report.c.tenant_id == scope.tenant_id, report.c.run_id == run_id)
                .order_by(report.c.created_at)
            )
            .mappings()
            .all()
        )
        if not decision_rows and not report_rows:
            return None
        decisions = []
        for row in decision_rows:
            grades = row["dimension_grades"] if isinstance(row["dimension_grades"], dict) else {}
            links = (
                self.session.execute(
                    select(decision_finding.c.finding_id).where(
                        decision_finding.c.tenant_id == scope.tenant_id,
                        decision_finding.c.decision_id == row["id"],
                    )
                )
                .scalars()
                .all()
            )
            hard_blocks = row["hard_blocks"] if isinstance(row["hard_blocks"], list) else []
            decisions.append(
                Decision(
                    decision_id=row["id"],
                    scope=scope,
                    run_id=run_id,
                    standard_version=row["standard_version"],
                    recommendation=Recommendation(row["recommendation"]),
                    dimension_grades={DimensionCode(key): FindingGrade(value) for key, value in grades.items()},
                    finding_ids=tuple(links),
                    blocking_reasons=tuple(str(value) for value in hard_blocks),
                    created_at=require_utc_datetime(row["created_at"]),
                    supersedes_id=row["supersedes_id"],
                )
            )
        reports = [
            Report(
                report_id=row["id"],
                scope=scope,
                run_id=run_id,
                decision_id=row["decision_id"],
                # Report body is intentionally not loaded from PostgreSQL.
                # A delivery/application layer resolves this object_key from
                # the private object store before presenting it to a user.
                explanation=f"[object-store:{row['object_key']}]",
                action_items=tuple(row["action_items"] or []),
                created_at=require_utc_datetime(row["created_at"]),
                supersedes_id=row["supersedes_id"],
            )
            for row in report_rows
        ]
        committed = next((row["id"] for row in reversed(report_rows) if row["status"] == "COMMITTED"), None)
        return DecisionReport(
            scope=scope,
            standard_version=decisions[-1].standard_version if decisions else "1.0",
            decisions=decisions,
            reports=reports,
            committed_report_id=committed,
        )

    def save(self, aggregate: DecisionReport) -> None:
        scope = aggregate.scope
        run_id = require_scope_id(scope, "run_id")
        assert_aggregate_scope(aggregate, scope)
        for decision_item in aggregate.decision_history:
            insert_if_absent(
                self.session,
                decision,
                {
                    "id": decision_item.decision_id,
                    "tenant_id": scope.tenant_id,
                    "run_id": run_id,
                    "recommendation": decision_item.recommendation.value,
                    "standard_version": decision_item.standard_version,
                    "dimension_grades": json_value(decision_item.dimension_grades),
                    "hard_blocks": json_value(decision_item.blocking_reasons),
                    "supersedes_id": decision_item.supersedes_id,
                    "created_at": decision_item.created_at,
                },
                resource_id=decision_item.decision_id,
            )
            for finding_id in decision_item.finding_ids:
                link_exists = self.session.execute(
                    select(decision_finding.c.decision_id).where(
                        decision_finding.c.tenant_id == scope.tenant_id,
                        decision_finding.c.decision_id == decision_item.decision_id,
                        decision_finding.c.finding_id == finding_id,
                    )
                ).first()
                if link_exists is None:
                    self.session.execute(
                        decision_finding.insert().values(
                            tenant_id=scope.tenant_id,
                            decision_id=decision_item.decision_id,
                            finding_id=finding_id,
                            role="INFORMS",
                        )
                    )
        for report_item in aggregate.report_history:
            object_key = (
                f"{scope.tenant_id}/{scope.project_id}/{scope.product_version_id}/{run_id}/"
                f"reports/{report_item.report_id}.json"
            )
            digest = hashlib.sha256(report_item.explanation.encode("utf-8")).hexdigest()
            status = "COMMITTED" if aggregate.committed_report_id == report_item.report_id else "RENDERED"
            insert_if_absent(
                self.session,
                report,
                {
                    "id": report_item.report_id,
                    "tenant_id": scope.tenant_id,
                    "run_id": run_id,
                    "decision_id": report_item.decision_id,
                    "object_key": object_key,
                    "sha256": digest,
                    "status": status,
                    "action_items": json_value(report_item.action_items),
                    "supersedes_id": report_item.supersedes_id,
                    "created_at": report_item.created_at or datetime.now(UTC),
                },
                resource_id=report_item.report_id,
            )


DecisionReportRepositoryAdapter = SqlAlchemyDecisionReportRepository

__all__ = ["DecisionReportRepositoryAdapter", "SqlAlchemyDecisionReportRepository"]
