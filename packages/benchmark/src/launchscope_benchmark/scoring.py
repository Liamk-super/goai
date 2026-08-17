"""Deterministic rubric scoring without an LLM judge."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from .catalog import BenchmarkCatalog, BenchmarkValidationError


@dataclass(frozen=True, slots=True)
class ScoreReport:
    document: dict[str, Any]

    @property
    def critical_violations(self) -> int:
        return int(self.document["summary"]["critical_violations"])


def score_run(catalog: BenchmarkCatalog, manifest: dict[str, Any]) -> ScoreReport:
    schema = catalog.load_json("benchmarks/schemas/run-manifest.schema.json")
    errors = sorted(Draft202012Validator(schema).iter_errors(manifest), key=lambda item: item.json_path)
    if errors:
        raise BenchmarkValidationError(f"run manifest violates schema at {errors[0].json_path}: {errors[0].message}")
    suite = catalog.suite(manifest["suite_id"])
    if manifest["suite_version"] != suite["version"]:
        raise BenchmarkValidationError("run suite version differs from canonical suite")
    if manifest["source_lock_sha256"] != catalog.source_lock_sha256():
        raise BenchmarkValidationError("run source-lock digest differs from canonical source locks")
    _validate_execution_boundary(suite["benchmark_type"], manifest)
    suite_cases = {case["id"]: case for case in suite["cases"]}
    unknown_cases = set(manifest["case_ids"]) - set(suite_cases)
    if unknown_cases:
        raise BenchmarkValidationError(f"run cites unknown suite cases: {sorted(unknown_cases)}")
    if set(manifest["outputs"]) != set(manifest["case_ids"]):
        raise BenchmarkValidationError("run outputs must exactly match run case_ids")
    cases = {case_id: suite_cases[case_id] for case_id in manifest["case_ids"]}
    results: list[dict[str, Any]] = []
    passed_weight = 0.0
    total_weight = 0.0
    identity = manifest["runtime_identity"]
    runtime_identity_violations = int(
        manifest["execution"]["mode"] == "LIVE_AUTHORIZED" and identity["status"] != "VERIFIED"
    )
    critical_violations = runtime_identity_violations
    human_pending = 0
    for case_id in manifest["case_ids"]:
        case = cases[case_id]
        output = manifest["outputs"][case_id]
        assertion_results = []
        for assertion in case["rubric"]["automated"]:
            passed, message = _evaluate(assertion, output, catalog.root)
            weight = float(assertion["weight"])
            total_weight += weight
            if passed:
                passed_weight += weight
            elif assertion["critical"]:
                critical_violations += 1
            assertion_results.append(
                {"id": assertion["id"], "passed": passed, "critical": assertion["critical"], "message": message}
            )
        pending = sum(1 for item in case["rubric"]["human"] if item["required"])
        human_pending += pending
        results.append(
            {
                "case_id": case_id,
                "automated_passed": all(item["passed"] for item in assertion_results),
                "assertions": assertion_results,
                "human_reviews_pending": pending,
                "oracle_mode": case["oracle"]["mode"],
            }
        )
    rate = passed_weight / total_weight if total_weight else 1.0
    report = {
        "schema_version": "1.0",
        "run_id": manifest["run_id"],
        "suite_id": suite["id"],
        "execution_lane": manifest["execution"]["lane"],
        "runtime_identity": identity,
        "summary": {
            "case_count": len(results),
            "automated_pass_rate": rate,
            "critical_violations": critical_violations,
            "runtime_identity_violations": runtime_identity_violations,
            "human_reviews_pending": human_pending,
        },
        "cases": results,
    }
    report_schema = catalog.load_json("benchmarks/schemas/score-report.schema.json")
    report_errors = list(Draft202012Validator(report_schema).iter_errors(report))
    if report_errors:
        raise BenchmarkValidationError(f"internal score report is invalid: {report_errors[0].message}")
    return ScoreReport(report)


def _validate_execution_boundary(benchmark_type: str, manifest: dict[str, Any]) -> None:
    execution = manifest["execution"]
    identity = manifest["runtime_identity"]
    expected_lane = {"MODEL": "MODEL_API", "AGENT": "SINGLE_AGENT", "SYSTEM_E2E": "AGENTTEAMS_E2E"}
    if execution["mode"] == "HARNESS_SELF_TEST":
        if execution["lane"] != "HARNESS_SELF_TEST" or execution["network_allowed"]:
            raise BenchmarkValidationError("harness self-test must be zero-network and explicitly labelled")
        return
    if execution["lane"] != expected_lane[benchmark_type]:
        raise BenchmarkValidationError(
            f"{benchmark_type} suite requires execution lane {expected_lane[benchmark_type]}"
        )
    if execution["mode"] == "LIVE_AUTHORIZED" and not execution["network_allowed"]:
        raise BenchmarkValidationError("live authorized run must record network_allowed=true")
    if execution["lane"] == "MODEL_API" and identity["source"] not in {"PROVIDER_RESPONSE", "RECORDED_METADATA"}:
        raise BenchmarkValidationError("MODEL_API identity must come from the provider response or recorded metadata")
    if execution["lane"] == "AGENTTEAMS_E2E" and identity["source"] not in {
        "AGENTTEAMS_WORKER_STATUS",
        "RECORDED_METADATA",
    }:
        raise BenchmarkValidationError("AgentTeams E2E identity must come from live Worker status or recorded metadata")
    requested = identity["requested"]
    observed = identity["observed"]
    actual_status = "VERIFIED" if requested == observed and observed else "MISMATCH" if observed else "UNVERIFIED"
    if identity["status"] != actual_status:
        raise BenchmarkValidationError("runtime identity status does not match requested and observed models")


def _evaluate(assertion: dict[str, Any], output: dict[str, Any], root: Path) -> tuple[bool, str]:
    found, value = _json_path(output, assertion["path"])
    assertion_type = assertion["type"]
    if assertion_type == "EXISTS":
        return found, "path exists" if found else "path is missing"
    if not found:
        return False, "path is missing"
    if assertion_type == "EQUALS":
        passed = value == assertion.get("expected")
    elif assertion_type == "CONTAINS_ALL":
        values = assertion.get("values", [])
        passed = isinstance(value, (list, str)) and all(item in value for item in values)
    elif assertion_type == "NOT_CONTAINS_ANY":
        values = assertion.get("values", [])
        passed = isinstance(value, (list, str)) and not any(item in value for item in values)
    elif assertion_type == "COUNT_BETWEEN":
        passed = hasattr(value, "__len__") and assertion.get("minimum", 0) <= len(value) <= assertion["maximum"]
    elif assertion_type == "JSON_SCHEMA":
        schema_path = (root / assertion["schema_path"]).resolve()
        try:
            schema_path.relative_to(root)
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            raise BenchmarkValidationError(f"invalid assertion schema_path {assertion['schema_path']}") from exc
        passed = not list(Draft202012Validator(schema).iter_errors(value))
    else:
        raise BenchmarkValidationError(f"unsupported assertion type {assertion_type}")
    return passed, "passed" if passed else f"{assertion_type} assertion failed"


def _json_path(document: dict[str, Any], path: str) -> tuple[bool, Any]:
    if path == "$":
        return True, document
    current: Any = document
    for part in path[2:].split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


__all__ = ["ScoreReport", "score_run"]
