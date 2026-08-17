"""Model-matrix aggregation and Prompt/RAG/Skill regression comparison."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from .catalog import BenchmarkCatalog, BenchmarkValidationError
from .scoring import score_run


def compare_model_runs(catalog: BenchmarkCatalog, manifests: list[dict[str, Any]]) -> dict[str, Any]:
    matrix = catalog.model_matrix()
    expected_cases = matrix["case_ids"]
    expected_repeats = set(range(1, matrix["minimum_independent_repeats"] + 1))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for manifest in manifests:
        if manifest["suite_id"] != matrix["suite_id"] or manifest["case_ids"] != expected_cases:
            raise BenchmarkValidationError("every model run must use the exact formal Case set")
        if manifest["execution"]["lane"] != "MODEL_API" or manifest["execution"]["cache_allowed"]:
            raise BenchmarkValidationError("formal model runs must be uncached MODEL_API runs")
        requested_models = set(manifest["runtime_identity"]["requested"].values())
        if len(requested_models) != 1:
            raise BenchmarkValidationError("each model run must request exactly one model")
        grouped[next(iter(requested_models))].append(manifest)
    if set(grouped) != set(matrix["candidates"]):
        raise BenchmarkValidationError("model comparison is missing an approved candidate")
    model_rows = []
    for model in matrix["candidates"]:
        runs = grouped[model]
        if {run["repeat_index"] for run in runs} != expected_repeats or len(runs) != len(expected_repeats):
            raise BenchmarkValidationError(f"model {model} lacks the required independent repeats")
        reports = [score_run(catalog, run).document for run in runs]
        model_rows.append(
            {
                "model": model,
                "repeats": len(runs),
                "mean_automated_pass_rate": sum(
                    report["summary"]["automated_pass_rate"] for report in reports
                ) / len(reports),
                "critical_violations": sum(report["summary"]["critical_violations"] for report in reports),
                "runtime_identity_verified": all(
                    report["runtime_identity"]["status"] == "VERIFIED" for report in reports
                ),
                "input_tokens": sum(run["usage"]["input_tokens"] for run in runs),
                "output_tokens": sum(run["usage"]["output_tokens"] for run in runs),
                "cost_amount": round(sum(float(run["usage"]["cost_amount"]) for run in runs), 6),
                "mean_latency_ms": sum(run["usage"]["latency_ms"] for run in runs) / len(runs),
            }
        )
    eligible = [
        row
        for row in model_rows
        if row["critical_violations"] == 0 and row["runtime_identity_verified"]
    ]
    if not eligible:
        recommendation = {
            "status": "NO_ELIGIBLE_MODEL",
            "model": None,
            "reason": "Every candidate has a critical or runtime-model identity violation.",
        }
    else:
        ordered = sorted(
            eligible,
            key=lambda row: (
                -row["mean_automated_pass_rate"],
                row["mean_latency_ms"],
                row["cost_amount"],
                row["model"],
            ),
        )
        winner = ordered[0]
        recommendation = {
            "status": "SELECTED",
            "model": winner["model"],
            "reason": "Highest deterministic pass rate; latency and cost break otherwise eligible ties.",
        }
    result = {
        "schema_version": "1.0",
        "suite_id": matrix["suite_id"],
        "case_ids": expected_cases,
        "minimum_repeats": matrix["minimum_independent_repeats"],
        "models": model_rows,
        "recommendation": recommendation,
    }
    _validate_result(catalog, "benchmarks/schemas/model-comparison-report.schema.json", result)
    return result


def compare_regression_runs(
    catalog: BenchmarkCatalog,
    baseline_manifest: dict[str, Any],
    candidate_manifest: dict[str, Any],
) -> dict[str, Any]:
    if baseline_manifest["suite_id"] != candidate_manifest["suite_id"]:
        raise BenchmarkValidationError("regression runs must use the same suite")
    if baseline_manifest["case_ids"] != candidate_manifest["case_ids"]:
        raise BenchmarkValidationError("regression runs must use the same ordered Case set")
    baseline = score_run(catalog, baseline_manifest).document
    candidate = score_run(catalog, candidate_manifest).document
    baseline_cases = {case["case_id"]: case for case in baseline["cases"]}
    candidate_cases = {case["case_id"]: case for case in candidate["cases"]}
    rows = []
    for case_id in baseline_manifest["case_ids"]:
        before = baseline_cases[case_id]
        after = candidate_cases[case_id]
        rows.append(
            {
                "case_id": case_id,
                "baseline_passed": before["automated_passed"],
                "candidate_passed": after["automated_passed"],
                "regressed": before["automated_passed"] and not after["automated_passed"],
            }
        )
    result = {
        "schema_version": "1.0",
        "suite_id": baseline_manifest["suite_id"],
        "baseline_run_id": baseline_manifest["run_id"],
        "candidate_run_id": candidate_manifest["run_id"],
        "summary": {
            "baseline_pass_rate": baseline["summary"]["automated_pass_rate"],
            "candidate_pass_rate": candidate["summary"]["automated_pass_rate"],
            "new_critical_violations": max(
                0,
                candidate["summary"]["critical_violations"] - baseline["summary"]["critical_violations"],
            ),
            "regressed_cases": sum(row["regressed"] for row in rows),
        },
        "cases": rows,
    }
    _validate_result(catalog, "benchmarks/schemas/regression-report.schema.json", result)
    return result


def _validate_result(catalog: BenchmarkCatalog, schema_path: str, result: dict[str, Any]) -> None:
    errors = sorted(
        Draft202012Validator(catalog.load_json(schema_path)).iter_errors(result),
        key=lambda item: item.json_path,
    )
    if errors:
        raise BenchmarkValidationError(f"internal comparison report is invalid: {errors[0].message}")


__all__ = ["compare_model_runs", "compare_regression_runs"]
