from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from launchscope_api.main import ControlPlane, create_app


def test_canonical_event_recovery_route_forwards_exact_settled_event_command() -> None:
    observed = {}
    run_id = uuid4()
    task_id = uuid4()
    recovery_id = uuid4()
    tenant_id = uuid4()

    class RecoveryApplication:
        def recover(self, actor, requested_run_id, **values):
            observed.update(actor=actor, run_id=requested_run_id, **values)
            return SimpleNamespace(
                to_dict=lambda: {
                    "recovery_id": str(recovery_id),
                    "run_id": str(run_id),
                    "task_id": str(task_id),
                    "control_epoch": 33,
                    "dispatch_epoch": 0,
                    "matrix_event_id": "$exact-settled-auditor-result",
                }
            )

    app = create_app(ControlPlane.create())
    app.state.canonical_event_recovery_application = RecoveryApplication()
    correlation_id = uuid4()
    response = TestClient(app).post(
        f"/api/v1/runs/{run_id}/canonical-event-recoveries",
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Actor-Id": "local-demo:test",
            "X-Correlation-Id": str(correlation_id),
            "Idempotency-Key": "canonical-recovery-api-test",
        },
        json={
            "task_id": str(task_id),
            "matrix_event_id": "$exact-settled-auditor-result",
            "expected_control_epoch": 33,
            "expected_dispatch_epoch": 0,
            "reason": "Use the already settled result without another model call",
        },
    )

    assert response.status_code == 202
    assert response.json()["recovery_id"] == str(recovery_id)
    assert observed["run_id"] == run_id
    assert observed["task_id"] == task_id
    assert observed["matrix_event_id"] == "$exact-settled-auditor-result"
    assert observed["expected_control_epoch"] == 33
    assert observed["expected_dispatch_epoch"] == 0
    assert observed["idempotency_key"] == "canonical-recovery-api-test"
    assert observed["correlation_id"] == correlation_id
