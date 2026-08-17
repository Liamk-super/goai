from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from launchscope_api.main import ControlPlane, create_app


def test_recover_run_route_forwards_demo_force_command() -> None:
    observed = {}
    run_id = uuid4()
    tenant_id = uuid4()

    class RecoveryApplication:
        def recover(self, actor, requested_run_id, **values):
            observed.update(actor=actor, run_id=requested_run_id, **values)
            return SimpleNamespace(
                to_dict=lambda: {
                    "run_id": str(run_id),
                    "run_status": "RUNNING",
                    "execution_control": {
                        "state": "ACTIVE",
                        "control_epoch": 4,
                        "usage_settlement_status": "NONE",
                        "in_flight_count": 0,
                    },
                    "recovered_task_ids": [str(uuid4())],
                    "preserved_task_ids": [],
                    "dispatched_task_count": 1,
                }
            )

    app = create_app(ControlPlane.create())
    app.state.execution_control_application = RecoveryApplication()
    correlation_id = uuid4()
    response = TestClient(app).post(
        f"/api/v1/runs/{run_id}/recover",
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Actor-Id": "local-demo:test",
            "X-Correlation-Id": str(correlation_id),
            "Idempotency-Key": "recover-api-test",
        },
        json={"expected_control_epoch": 3, "force": True},
    )

    assert response.status_code == 202
    assert response.json()["run_status"] == "RUNNING"
    assert observed["run_id"] == run_id
    assert observed["actor"].tenant_id == tenant_id
    assert observed["expected_control_epoch"] == 3
    assert observed["force"] is True
    assert observed["idempotency_key"] == "recover-api-test"
    assert observed["correlation_id"] == correlation_id
