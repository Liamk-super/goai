from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from launchscope_api.main import ControlPlane, create_app


def test_run_limit_amendment_route_forwards_versioned_exact_event_command() -> None:
    observed = {}
    run_id = uuid4()
    task_id = uuid4()
    amendment_id = uuid4()
    tenant_id = uuid4()

    class AmendmentApplication:
        def amend(self, actor, requested_run_id, **values):
            observed.update(actor=actor, run_id=requested_run_id, **values)
            return SimpleNamespace(
                to_dict=lambda: {
                    "amendment_id": str(amendment_id),
                    "run_id": str(run_id),
                    "task_id": str(task_id),
                    "amendment_version": 2,
                    "control_epoch": 7,
                    "dispatch_epoch": 19,
                    "matrix_event_id": "$exact-result",
                    "effective_limits": {
                        "model_calls": 4096,
                        "input_tokens": 200_000_000,
                        "output_tokens": 20_000_000,
                    },
                }
            )

    app = create_app(ControlPlane.create())
    app.state.run_limit_amendment_application = AmendmentApplication()
    correlation_id = uuid4()
    response = TestClient(app).post(
        f"/api/v1/runs/{run_id}/limit-amendments",
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Actor-Id": "local-demo:test",
            "X-Correlation-Id": str(correlation_id),
            "Idempotency-Key": "amendment-api-test",
        },
        json={
            "task_id": str(task_id),
            "matrix_event_id": "$exact-result",
            "expected_control_epoch": 7,
            "expected_dispatch_epoch": 19,
            "expected_amendment_version": 1,
            "model_calls": 4096,
            "input_tokens": 200_000_000,
            "output_tokens": 20_000_000,
            "reason": "Product owner authorized more company API capacity",
        },
    )

    assert response.status_code == 202
    assert response.json()["amendment_version"] == 2
    assert observed["run_id"] == run_id
    assert observed["task_id"] == task_id
    assert observed["matrix_event_id"] == "$exact-result"
    assert observed["expected_control_epoch"] == 7
    assert observed["expected_dispatch_epoch"] == 19
    assert observed["expected_amendment_version"] == 1
    assert observed["idempotency_key"] == "amendment-api-test"
    assert observed["correlation_id"] == correlation_id
