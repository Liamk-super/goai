from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from launchscope_benchmark.catalog import BenchmarkCatalog, BenchmarkValidationError
from launchscope_benchmark.cli import _oracle_manifest
from launchscope_benchmark.comparison import compare_model_runs, compare_regression_runs
from launchscope_benchmark.exporters import export_agentloop_dataset, export_promptfoo_tests
from launchscope_benchmark.runner import ModelResponse, render_case_prompt, run_model_api, worker_runtime_identity
from launchscope_benchmark.scoring import score_run
from launchscope_benchmark.telemetry import benchmark_trace_attributes


def test_catalog_validates_exact_v1_counts_and_sources() -> None:
    summary = BenchmarkCatalog().validate()

    assert summary.suites == 8
    assert summary.cases == 122
    assert summary.counts["MODEL"] == 30
    assert summary.counts["AGENT"] == 80
    assert summary.counts["SYSTEM_E2E"] == 12


def test_harness_self_test_scores_every_system_case() -> None:
    catalog = BenchmarkCatalog()
    manifest = _oracle_manifest(catalog, "system-e2e-v1")

    report = score_run(catalog, manifest)

    assert report.document["summary"]["case_count"] == 12
    assert report.document["summary"]["automated_pass_rate"] == 1.0
    assert report.critical_violations == 0
    assert report.document["summary"]["human_reviews_pending"] == 1


def test_scorer_fails_closed_when_an_output_is_missing() -> None:
    catalog = BenchmarkCatalog()
    manifest = _oracle_manifest(catalog, "system-e2e-v1")
    manifest["outputs"].pop("SYS-E2E-01")

    with pytest.raises(BenchmarkValidationError, match="outputs must exactly match run case_ids"):
        score_run(catalog, manifest)


def test_scorer_negative_control_trips_a_critical_gate() -> None:
    catalog = BenchmarkCatalog()
    manifest = _oracle_manifest(catalog, "system-e2e-v1")
    manifest["outputs"]["SYS-E2E-07"]["action"] = "RETRY_WITH_ANOTHER_PROVIDER"

    report = score_run(catalog, manifest)

    assert report.critical_violations >= 1
    result = next(item for item in report.document["cases"] if item["case_id"] == "SYS-E2E-07")
    assert result["automated_passed"] is False


def test_trace_projection_drops_prompt_and_business_bodies() -> None:
    attributes = benchmark_trace_attributes(
        run_id="run-1",
        suite_id="system-e2e-v1",
        provider="oracle",
        model="none",
        outcome="PASS",
        payload={"prompt": "secret prompt", "messages": ["private"], "report_body": "business body"},
    )

    assert "prompt" not in attributes
    assert "messages" not in attributes
    assert "report_body" not in attributes
    assert attributes["launchscope.payload.sha256"]


def test_exports_are_adapter_only_and_agentloop_is_opt_in(tmp_path: Path) -> None:
    catalog = BenchmarkCatalog()
    promptfoo_path = tmp_path / "promptfoo.json"
    assert export_promptfoo_tests(catalog, "model-benchmark-v1", promptfoo_path) == 30
    assert len(json.loads(promptfoo_path.read_text(encoding="utf-8"))) == 30

    with pytest.raises(BenchmarkValidationError, match="disabled by default"):
        export_agentloop_dataset(catalog, "system-e2e-v1", tmp_path / "agentloop.jsonl", local_only=False)
    assert export_agentloop_dataset(
        catalog, "system-e2e-v1", tmp_path / "agentloop.jsonl", local_only=True
    ) == 12


def test_adapters_are_pinned_private_and_disabled_by_default() -> None:
    root = BenchmarkCatalog().root
    package = json.loads(
        (root / "benchmarks/adapters/promptfoo/package.json").read_text(encoding="utf-8")
    )
    promptfoo_lock = yaml.safe_load(
        (root / "benchmarks/adapters/promptfoo/pnpm-lock.yaml").read_text(encoding="utf-8")
    )
    promptfoo = yaml.safe_load(
        (root / "benchmarks/adapters/promptfoo/promptfooconfig.yaml").read_text(encoding="utf-8")
    )
    studio = yaml.safe_load(
        (root / "benchmarks/adapters/observability/agentscope-studio.v1.yaml").read_text(encoding="utf-8")
    )
    agentloop = yaml.safe_load(
        (root / "benchmarks/adapters/observability/agentloop.v1.yaml").read_text(encoding="utf-8")
    )

    assert package["devDependencies"]["promptfoo"] == "0.121.19"
    assert package["engines"]["node"] == ">=24.0.0 <25"
    assert promptfoo_lock["importers"]["."]["devDependencies"]["promptfoo"]["specifier"] == "0.121.19"
    assert promptfoo["sharing"] is False
    assert promptfoo["commandLineOptions"]["cache"] is False
    assert promptfoo["providers"][0]["id"] == "file://provider.py"
    matrix = yaml.safe_load(
        (root / "benchmarks/adapters/promptfoo/model-matrix.yaml").read_text(encoding="utf-8")
    )
    assert matrix["commandLineOptions"]["repeat"] == 3
    assert [provider["label"] for provider in matrix["providers"]] == [
        "kimi-k3",
        "glm-5.2",
        "qwen3.8-max",
    ]
    assert studio["spec"]["enabled"] is False
    assert studio["spec"]["compatibility"]["uiTraceDisplayVerified"] is False
    assert agentloop["spec"]["enabled"] is False
    assert agentloop["spec"]["otlp"]["uploadEnabled"] is False


def test_model_matrix_and_blind_review_are_canonical() -> None:
    catalog = BenchmarkCatalog()
    matrix = catalog.model_matrix()
    blind = catalog.load_json("benchmarks/blind/manifest.v1.json")

    assert matrix["case_ids"] == ["MODEL-EVD-01", "MODEL-EVD-02"]
    assert matrix["candidates"] == ["kimi-k3", "glm-5.2", "qwen3.8-max"]
    assert blind["assignment_storage"] == "EXTERNAL_SEALED"
    assert blind["governance"]["minimum_reviewers"] == 2


def test_model_prompt_contains_traceable_rules_without_oracle_answer() -> None:
    catalog = BenchmarkCatalog()
    case = next(case for case in catalog.suite("model-benchmark-v1")["cases"] if case["id"] == "MODEL-EVD-01")

    prompt = render_case_prompt(catalog, case)

    assert "EVD-MISSING-REFS-REJECT" in prompt
    assert "evidence_refs" in prompt
    assert '"expected"' not in prompt


def test_model_api_runner_requires_observed_runtime_model() -> None:
    class FakeClient:
        def complete(self, *, model: str, prompt: str, max_output_tokens: int) -> ModelResponse:
            assert prompt
            assert max_output_tokens == 500
            output = (
                {"action": "REJECT", "rule_citations": ["EVD-MISSING-REFS-REJECT"]}
                if "MODEL-EVD-01" in prompt
                else {
                    "action": "REQUEST_MORE_EVIDENCE",
                    "status": "BLOCKED",
                    "rule_citations": ["EVD-EMPTY-PACKAGE-BLOCK"],
                }
            )
            return ModelResponse(output, model, 10, 5, 20)

    catalog = BenchmarkCatalog()
    manifest = run_model_api(
        catalog,
        client=FakeClient(),  # type: ignore[arg-type]
        model="qwen3.8-max",
        provider="fake",
        case_ids=catalog.model_matrix()["case_ids"],
        repeat_index=1,
        authorized=True,
        input_usd_per_million=Decimal("2"),
        output_usd_per_million=Decimal("6"),
    )

    report = score_run(catalog, manifest)
    assert manifest["runtime_identity"]["status"] == "VERIFIED"
    assert report.document["execution_lane"] == "MODEL_API"
    assert report.document["summary"]["automated_pass_rate"] == 1


def test_live_model_identity_mismatch_is_a_critical_gate() -> None:
    catalog = BenchmarkCatalog()
    manifest = _live_model_manifest(catalog, "qwen3.8-max", 1, latency_ms=10)
    manifest["runtime_identity"]["status"] = "MISMATCH"
    manifest["runtime_identity"]["observed"] = dict.fromkeys(manifest["case_ids"], "kimi-k3")

    report = score_run(catalog, manifest)

    assert report.document["summary"]["runtime_identity_violations"] == 1
    assert report.critical_violations == 1


def test_worker_runtime_identity_requires_six_running_workers() -> None:
    snapshot = {
        "workers": [
            {"name": f"launchscope-worker-{index}", "phase": "Running", "spec": {"model": "qwen3.8-max"}}
            for index in range(6)
        ]
    }

    identity = worker_runtime_identity(snapshot, expected_model="qwen3.8-max")

    assert identity["status"] == "VERIFIED"
    assert identity["source"] == "AGENTTEAMS_WORKER_STATUS"


def test_three_model_comparison_uses_same_cases_repeats_and_identity() -> None:
    catalog = BenchmarkCatalog()
    latencies = {"kimi-k3": 30, "glm-5.2": 20, "qwen3.8-max": 10}
    manifests = [
        _live_model_manifest(catalog, model, repeat, latency_ms=latencies[model])
        for model in catalog.model_matrix()["candidates"]
        for repeat in range(1, 4)
    ]

    report = compare_model_runs(catalog, manifests)

    assert report["recommendation"]["model"] == "qwen3.8-max"
    assert all(row["repeats"] == 3 for row in report["models"])
    assert all(row["runtime_identity_verified"] for row in report["models"])


def test_regression_comparison_reports_new_critical_failure() -> None:
    catalog = BenchmarkCatalog()
    baseline = _live_model_manifest(catalog, "qwen3.8-max", 1, latency_ms=10)
    candidate = deepcopy(baseline)
    candidate["run_id"] = "candidate"
    candidate["outputs"]["MODEL-EVD-01"]["action"] = "ACCEPT"

    report = compare_regression_runs(catalog, baseline, candidate)

    assert report["summary"]["new_critical_violations"] == 1
    assert report["summary"]["regressed_cases"] == 1


def _live_model_manifest(
    catalog: BenchmarkCatalog,
    model: str,
    repeat: int,
    *,
    latency_ms: int,
) -> dict[str, object]:
    manifest = _oracle_manifest(catalog, "model-benchmark-v1")
    case_ids = catalog.model_matrix()["case_ids"]
    manifest["run_id"] = f"{model}:{repeat}"
    manifest["case_ids"] = case_ids
    manifest["outputs"] = {case_id: manifest["outputs"][case_id] for case_id in case_ids}
    manifest["execution"] = {
        "mode": "LIVE_AUTHORIZED",
        "lane": "MODEL_API",
        "provider": "test",
        "network_allowed": True,
        "cache_allowed": False,
    }
    manifest["runtime_identity"] = {
        "status": "VERIFIED",
        "requested": dict.fromkeys(case_ids, model),
        "observed": dict.fromkeys(case_ids, model),
        "source": "PROVIDER_RESPONSE",
        "details": "test fixture",
    }
    manifest["repeat_index"] = repeat
    manifest["usage"]["latency_ms"] = latency_ms
    return manifest
