from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from launchscope_api.main import ControlPlane, create_app


def test_database_failure_is_a_cors_visible_fail_closed_error(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_CORS_ORIGINS", "http://127.0.0.1:3000")
    plane = ControlPlane.create()

    def stale_schema(*_args, **_kwargs):
        raise SQLAlchemyError("internal schema detail must not be exposed")

    plane.dossier.plan = stale_schema  # type: ignore[method-assign]
    client = TestClient(create_app(plane), raise_server_exceptions=False)
    correlation_id = str(uuid4())
    response = client.post(
        f"/api/v1/product-versions/{uuid4()}/plan",
        headers={
            "Origin": "http://127.0.0.1:3000",
            "X-Tenant-Id": str(uuid4()),
            "X-Actor-Id": "local-demo:test",
            "X-Correlation-Id": correlation_id,
        },
    )

    assert response.status_code == 503
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
    assert response.json() == {
        "error_code": "DATABASE_UNAVAILABLE",
        "message": "Database unavailable or schema is outdated. Run the Demo database migration, then retry.",
        "correlation_id": correlation_id,
        "retryable": False,
        "details": {},
    }
