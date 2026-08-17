from pathlib import Path

import yaml

CONTRACT = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "contracts"
    / "openapi"
    / "canonical-event-recovery.v1.yaml"
)


def test_canonical_event_recovery_is_additive_and_exact_event_scoped() -> None:
    document = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    operation = document["paths"]["/api/v1/runs/{run_id}/canonical-event-recoveries"]["post"]
    assert operation["operationId"] == "recoverSettledCanonicalMatrixEvent"
    request = document["components"]["schemas"]["CanonicalEventRecoveryRequest"]
    assert request["additionalProperties"] is False
    assert set(request["required"]) == {
        "task_id",
        "matrix_event_id",
        "expected_control_epoch",
        "expected_dispatch_epoch",
        "reason",
    }
    assert "model_calls" not in request["properties"]
    assert "input_tokens" not in request["properties"]
