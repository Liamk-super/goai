"""Task-attributable usage snapshots for dedicated AgentTeams Workers."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True)
class AgentUsageSnapshot:
    input_tokens: int
    output_tokens: int
    call_count: int

    def __post_init__(self) -> None:
        if min(self.input_tokens, self.output_tokens, self.call_count) < 0:
            raise ValueError("Agent usage counters must be non-negative")

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "call_count": self.call_count,
        }

    @classmethod
    def from_dict(cls, value: object) -> AgentUsageSnapshot:
        if not isinstance(value, dict):
            raise ValueError("Agent usage snapshot is missing")
        return cls(
            input_tokens=int(value["input_tokens"]),
            output_tokens=int(value["output_tokens"]),
            call_count=int(value["call_count"]),
        )


@dataclass(frozen=True, slots=True)
class AgentUsageReceipt:
    receipt_id: str
    input_tokens: int
    output_tokens: int
    call_count: int
    cost_usd: Decimal | None


class AgentUsageReader(Protocol):
    def snapshot(self, agent_code: str) -> AgentUsageSnapshot: ...


class HttpAgentUsageReader:
    def __init__(self, endpoints: dict[str, str]) -> None:
        if not endpoints:
            raise ValueError("Agent usage endpoint map is empty")
        self._endpoints = endpoints

    @classmethod
    def from_env(cls) -> HttpAgentUsageReader:
        raw = json.loads(os.getenv("LAUNCHSCOPE_AGENT_USAGE_ENDPOINTS_JSON", "{}"))
        if not isinstance(raw, dict) or not all(
            isinstance(key, str) and isinstance(value, str) and value.startswith(("http://", "https://"))
            for key, value in raw.items()
        ):
            raise ValueError("LAUNCHSCOPE_AGENT_USAGE_ENDPOINTS_JSON must map Agent codes to HTTP URLs")
        return cls(raw)

    def snapshot(self, agent_code: str) -> AgentUsageSnapshot:
        try:
            endpoint = self._endpoints[agent_code]
        except KeyError as exc:
            raise ValueError(f"No usage endpoint is configured for Agent {agent_code}") from exc
        request = urllib.request.Request(endpoint, method="GET", headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read(1_048_577))
        return AgentUsageSnapshot(
            input_tokens=int(payload["total_prompt_tokens"]),
            output_tokens=int(payload["total_completion_tokens"]),
            call_count=int(payload["total_calls"]),
        )


def configured_usage_reader() -> AgentUsageReader | None:
    if not os.getenv("LAUNCHSCOPE_AGENT_USAGE_ENDPOINTS_JSON", "").strip():
        return None
    return HttpAgentUsageReader.from_env()


def usage_delta(
    baseline: AgentUsageSnapshot,
    terminal: AgentUsageSnapshot,
    *,
    task_key: str,
    input_usd_per_million: Decimal | None = None,
    output_usd_per_million: Decimal | None = None,
) -> AgentUsageReceipt:
    input_tokens = terminal.input_tokens - baseline.input_tokens
    output_tokens = terminal.output_tokens - baseline.output_tokens
    call_count = terminal.call_count - baseline.call_count
    if min(input_tokens, output_tokens, call_count) < 0:
        raise ValueError("Agent usage counter moved backwards during the Task")
    if call_count == 0 or input_tokens + output_tokens == 0:
        raise ValueError("Agent usage interval contains no model call")
    canonical = json.dumps(
        {"task_key": task_key, "baseline": baseline.to_dict(), "terminal": terminal.to_dict()},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if (input_usd_per_million is None) != (output_usd_per_million is None):
        raise ValueError("Both model prices must be supplied together")
    cost = None
    if input_usd_per_million is not None and output_usd_per_million is not None:
        cost = (
            Decimal(input_tokens) * input_usd_per_million
            + Decimal(output_tokens) * output_usd_per_million
        ) / Decimal(1_000_000)
    return AgentUsageReceipt(
        receipt_id=hashlib.sha256(canonical).hexdigest(),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        call_count=call_count,
        cost_usd=cost.quantize(Decimal("0.000001")) if cost is not None else None,
    )


__all__ = [
    "AgentUsageReader",
    "AgentUsageReceipt",
    "AgentUsageSnapshot",
    "HttpAgentUsageReader",
    "configured_usage_reader",
    "usage_delta",
]
