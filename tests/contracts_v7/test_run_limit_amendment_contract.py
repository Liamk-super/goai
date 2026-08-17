from pathlib import Path

import yaml

CONTRACT = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "contracts"
    / "openapi"
    / "run-limit-amendment.v1.yaml"
)


def test_run_limit_amendment_is_additive_bounded_and_exact_event_scoped() -> None:
    document = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    operation = document["paths"]["/api/v1/runs/{run_id}/limit-amendments"]["post"]
    assert operation["operationId"] == "amendDemoRunModelLimits"
    request = document["components"]["schemas"]["RunLimitAmendmentRequest"]
    assert request["additionalProperties"] is False
    assert {
        "task_id",
        "matrix_event_id",
        "expected_control_epoch",
        "expected_dispatch_epoch",
        "expected_amendment_version",
    } <= set(request["required"])
    assert request["properties"]["model_calls"]["maximum"] == 4096
    assert request["properties"]["input_tokens"]["maximum"] == 200_000_000
    assert request["properties"]["output_tokens"]["maximum"] == 20_000_000
