"""Fail-closed runners and runtime-model identity capture for Benchmark V1."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from .catalog import BenchmarkCatalog, BenchmarkValidationError


class SubmissionUnknownError(RuntimeError):
    """A provider request may have been accepted, so retry is forbidden."""


@dataclass(frozen=True, slots=True)
class ModelResponse:
    output: dict[str, Any]
    observed_model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class OpenAICompatibleClient:
    def __init__(self, *, base_url: str, api_key: str, timeout_seconds: int = 120) -> None:
        if not base_url.startswith(("http://", "https://")) or not api_key:
            raise BenchmarkValidationError("model base URL and API key are required")
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def complete(self, *, model: str, prompt: str, max_output_tokens: int) -> ModelResponse:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "Return exactly one JSON object. Do not use Markdown or add unsupported facts.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": max_output_tokens,
        }
        request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                document = json.loads(response.read(4_194_305))
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                raise BenchmarkValidationError(f"provider rejected request with HTTP {exc.code}") from exc
            raise SubmissionUnknownError(f"provider submission outcome is unknown after HTTP {exc.code}") from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise SubmissionUnknownError("provider submission outcome is unknown; do not retry or fail over") from exc
        latency_ms = round((time.perf_counter() - started) * 1000)
        try:
            observed_model = str(document["model"])
            content = document["choices"][0]["message"]["content"]
            output = json.loads(content)
            usage = document.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens", 0))
            output_tokens = int(usage.get("completion_tokens", 0))
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BenchmarkValidationError("provider response lacks a model identity or valid JSON output") from exc
        if not isinstance(output, dict):
            raise BenchmarkValidationError("provider output must be a JSON object")
        return ModelResponse(output, observed_model, input_tokens, output_tokens, latency_ms)


def render_case_prompt(catalog: BenchmarkCatalog, case: dict[str, Any]) -> str:
    rule_by_id = {rule["id"]: rule for rule in catalog.rules()["rules"]}
    rules = [
        {
            "id": rule_id,
            "statement": rule_by_id[rule_id]["statement"],
            "source_id": rule_by_id[rule_id]["source_id"],
            "locator": rule_by_id[rule_id]["locator"],
        }
        for rule_id in case["oracle"]["rule_refs"]
    ]
    source = case["input"]
    scenario: object
    if source["kind"] == "REFERENCE_FILE":
        scenario = (catalog.root / source["path"]).read_text(encoding="utf-8")
    else:
        scenario = source["payload"]
    required_fields = sorted(
        {
            assertion["path"].removeprefix("$.").split(".", 1)[0]
            for assertion in case["rubric"]["automated"]
            if assertion["path"] != "$"
        }
        | {"rule_citations"}
    )
    return json.dumps(
        {
            "benchmark_case_id": case["id"],
            "scenario": scenario,
            "applicable_rules": rules,
            "instructions": {
                "required_top_level_fields": required_fields,
                "rule_citations": "Cite only applicable rule IDs actually used.",
                "external_facts": "Use HUMAN_VERIFICATION_REQUIRED when a claim needs current external truth.",
            },
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def run_model_api(
    catalog: BenchmarkCatalog,
    *,
    client: OpenAICompatibleClient,
    model: str,
    provider: str,
    case_ids: list[str],
    repeat_index: int,
    authorized: bool,
    input_usd_per_million: Decimal,
    output_usd_per_million: Decimal,
    max_output_tokens: int = 500,
) -> dict[str, Any]:
    if not authorized:
        raise BenchmarkValidationError("paid provider execution requires --authorize-paid-calls")
    suite = catalog.suite(catalog.model_matrix()["suite_id"])
    cases = {case["id"]: case for case in suite["cases"]}
    if case_ids != catalog.model_matrix()["case_ids"]:
        raise BenchmarkValidationError("model comparison must use the exact formal Case set in declared order")
    outputs: dict[str, dict[str, Any]] = {}
    observed: dict[str, str] = {}
    input_tokens = 0
    output_tokens = 0
    latency_ms = 0
    started_at = _now()
    for case_id in case_ids:
        response = client.complete(
            model=model,
            prompt=render_case_prompt(catalog, cases[case_id]),
            max_output_tokens=max_output_tokens,
        )
        outputs[case_id] = response.output
        observed[case_id] = response.observed_model
        input_tokens += response.input_tokens
        output_tokens += response.output_tokens
        latency_ms += response.latency_ms
    requested = dict.fromkeys(case_ids, model)
    identity_status = "VERIFIED" if requested == observed else "MISMATCH"
    cost = (
        Decimal(input_tokens) * input_usd_per_million
        + Decimal(output_tokens) * output_usd_per_million
    ) / Decimal(1_000_000)
    return {
        "schema_version": "1.0",
        "run_id": f"model-api:{model}:repeat-{repeat_index}:{int(time.time())}",
        "suite_id": suite["id"],
        "suite_version": suite["version"],
        "case_ids": case_ids,
        "execution": {
            "mode": "LIVE_AUTHORIZED",
            "lane": "MODEL_API",
            "provider": provider,
            "network_allowed": True,
            "cache_allowed": False,
        },
        "runtime_identity": {
            "status": identity_status,
            "requested": requested,
            "observed": observed,
            "source": "PROVIDER_RESPONSE",
            "details": "Observed from each chat-completions response model field; no alias substitution accepted.",
        },
        "implementation": {
            "git_commit": "WORKTREE",
            "dirty": True,
            "prompt_sha256": None,
            "agent_sha256": None,
            "skill_sha256": None,
        },
        "source_lock_sha256": catalog.source_lock_sha256(),
        "started_at": started_at,
        "finished_at": _now(),
        "repeat_index": repeat_index,
        "outputs": outputs,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_amount": float(cost.quantize(Decimal("0.000001"))),
            "currency": "USD",
            "usage_known": input_tokens + output_tokens > 0,
            "latency_ms": latency_ms,
        },
    }


def worker_runtime_identity(snapshot: object, *, expected_model: str) -> dict[str, Any]:
    if isinstance(snapshot, dict) and isinstance(snapshot.get("workers"), list):
        workers = snapshot["workers"]
    elif isinstance(snapshot, list):
        workers = snapshot
    else:
        raise BenchmarkValidationError("AgentTeams Worker snapshot must contain a workers array")
    selected = [
        item
        for item in workers
        if isinstance(item, dict) and str(item.get("name", "")).startswith("launchscope-")
    ]
    if len(selected) != 6:
        raise BenchmarkValidationError(f"expected six LaunchScope Workers, observed {len(selected)}")
    observed: dict[str, str] = {}
    for worker in selected:
        phase = worker.get("phase") or (worker.get("status") or {}).get("phase")
        if phase != "Running":
            raise BenchmarkValidationError(f"Worker {worker['name']} is not Running")
        spec = worker.get("spec") or {}
        status = worker.get("status") or {}
        model = worker.get("model") or spec.get("model") or status.get("model") or status.get("activeModel")
        if not isinstance(model, str) or not model:
            raise BenchmarkValidationError(f"Worker {worker['name']} does not expose its active model")
        observed[str(worker["name"])] = model
    requested = dict.fromkeys(sorted(observed), expected_model)
    observed = dict(sorted(observed.items()))
    return {
        "status": "VERIFIED" if requested == observed else "MISMATCH",
        "requested": requested,
        "observed": observed,
        "source": "AGENTTEAMS_WORKER_STATUS",
        "details": "Captured from six Running AgentTeams Worker resources.",
    }


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "ModelResponse",
    "OpenAICompatibleClient",
    "SubmissionUnknownError",
    "render_case_prompt",
    "run_model_api",
    "worker_runtime_identity",
]
