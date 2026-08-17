"""Privacy-preserving Benchmark trace projection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from launchscope_observability import payload_sha256, safe_trace_attributes


def benchmark_trace_attributes(
    *, run_id: str, suite_id: str, provider: str, model: str, outcome: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    return safe_trace_attributes(
        {
            "launchscope.run.id": run_id,
            "launchscope.stage.code": f"benchmark:{suite_id}",
            "launchscope.tool.code": provider,
            "launchscope.model.id": model,
            "launchscope.outcome": outcome,
            "launchscope.payload.sha256": payload_sha256(payload),
            "prompt": payload.get("prompt"),
            "messages": payload.get("messages"),
            "report_body": payload.get("report_body"),
        }
    )


__all__ = ["benchmark_trace_attributes"]

