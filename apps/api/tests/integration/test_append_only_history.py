"""Database-level append-only protection for conclusion history."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError

from launchscope_api.infrastructure.db.schema import decision, finding, report
from launchscope_api.infrastructure.db.session import session_factory, tenant_transaction


def test_finding_decision_report_cannot_be_overwritten(database, runtime_engine, tenant_records) -> None:
    ids = {"finding": uuid4(), "decision": uuid4(), "report": uuid4()}
    scope = tenant_records["scope"]
    with database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO finding "
                "(id, tenant_id, run_id, dimension_code, grade, statement, submitted_by) "
                "VALUES (:id, :tenant_id, :run_id, 'USER_USAGE', 'WEAK', 'append-only finding', 'agent-a')"
            ),
            {"id": ids["finding"], "tenant_id": scope.tenant_id, "run_id": scope.run_id},
        )
        connection.execute(
            text(
                "INSERT INTO decision "
                "(id, tenant_id, run_id, recommendation, standard_version, dimension_grades) "
                "VALUES (:id, :tenant_id, :run_id, 'PAUSE', '1.0', "
                "'{\"PRODUCT_IMPLEMENTATION\": \"WEAK\", \"USER_USAGE\": \"WEAK\", "
                "\"BUSINESS_INVESTMENT\": \"WEAK\", \"GEO_POLICY_TREND\": \"WEAK\"}'::jsonb)"
            ),
            {"id": ids["decision"], "tenant_id": scope.tenant_id, "run_id": scope.run_id},
        )
        connection.execute(
            text(
                "INSERT INTO report (id, tenant_id, run_id, decision_id, object_key, sha256) "
                "VALUES (:id, :tenant_id, :run_id, :decision_id, :object_key, :sha256)"
            ),
            {
                "id": ids["report"],
                "tenant_id": scope.tenant_id,
                "run_id": scope.run_id,
                "decision_id": ids["decision"],
                "object_key": (
                    f"{scope.tenant_id}/{scope.project_id}/{scope.product_version_id}/"
                    f"{scope.run_id}/report.json"
                ),
                "sha256": "1" * 64,
            },
        )

    factory = session_factory(runtime_engine)
    with pytest.raises(DBAPIError), tenant_transaction(factory, scope) as session:
        session.execute(update(finding).where(finding.c.id == ids["finding"]).values(statement="overwrite"))
    with pytest.raises(DBAPIError), tenant_transaction(factory, scope) as session:
        session.execute(delete(decision).where(decision.c.id == ids["decision"]))
    with pytest.raises(DBAPIError), tenant_transaction(factory, scope) as session:
        session.execute(update(report).where(report.c.id == ids["report"]).values(status="REPLACED"))
