"""Load and fail-closed validate the canonical Benchmark V1 catalog."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from referencing import Registry, Resource


class BenchmarkValidationError(ValueError):
    """The benchmark catalog is malformed, drifted, or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class ValidationSummary:
    suites: int
    cases: int
    rules: int
    source_locks: int
    counts: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "suites": self.suites,
            "cases": self.cases,
            "rules": self.rules,
            "source_locks": self.source_locks,
            "counts": self.counts,
        }


def repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


class BenchmarkCatalog:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or repository_root()).resolve()
        self.benchmark_root = self.root / "benchmarks"

    def load_json(self, relative_path: str | Path) -> dict[str, Any]:
        path = self.root / relative_path
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BenchmarkValidationError(f"cannot load JSON document {path}") from exc
        if not isinstance(document, dict):
            raise BenchmarkValidationError(f"document must be an object: {path}")
        return document

    def source_locks(self) -> dict[str, Any]:
        return self.load_json("benchmarks/registry/source-locks.v1.json")

    def rules(self) -> dict[str, Any]:
        return self.load_json("benchmarks/registry/rules.v1.json")

    def policy(self) -> dict[str, Any]:
        return self.load_json("benchmarks/policy.v1.json")

    def model_matrix(self) -> dict[str, Any]:
        return self.load_json("benchmarks/model-matrix.v1.json")

    def suites(self) -> list[dict[str, Any]]:
        paths = sorted((self.benchmark_root / "suites").rglob("*.json"))
        return [self.load_json(path.relative_to(self.root)) for path in paths]

    def suite(self, suite_id: str) -> dict[str, Any]:
        matches = [suite for suite in self.suites() if suite["id"] == suite_id]
        if len(matches) != 1:
            raise BenchmarkValidationError(f"suite id must resolve exactly once: {suite_id}")
        return matches[0]

    def source_lock_sha256(self) -> str:
        path = self.benchmark_root / "registry" / "source-locks.v1.json"
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def validate(self, *, verify_hashes: bool = True) -> ValidationSummary:
        case_schema = self.load_json("benchmarks/schemas/case.schema.json")
        suite_schema = self.load_json("benchmarks/schemas/suite.schema.json")
        rule_schema = self.load_json("benchmarks/schemas/rule-registry.schema.json")
        lock_schema = self.load_json("benchmarks/schemas/source-locks.schema.json")
        holdout_schema = self.load_json("benchmarks/schemas/holdout-manifest.schema.json")
        policy_schema = self.load_json("benchmarks/schemas/policy.schema.json")
        coverage_schema = self.load_json("benchmarks/schemas/coverage.schema.json")
        model_matrix_schema = self.load_json("benchmarks/schemas/model-matrix.schema.json")
        blind_schema = self.load_json("benchmarks/schemas/blind-manifest.schema.json")
        registry = Registry().with_resource(case_schema["$id"], Resource.from_contents(case_schema))
        locks = self.source_locks()
        rules_document = self.rules()
        _validate_schema(lock_schema, locks, "source locks")
        _validate_schema(rule_schema, rules_document, "rule registry")
        _validate_schema(policy_schema, self.policy(), "benchmark policy")
        coverage = self.load_json("benchmarks/coverage.v1.json")
        _validate_schema(coverage_schema, coverage, "coverage map")
        model_matrix = self.model_matrix()
        _validate_schema(model_matrix_schema, model_matrix, "model matrix")
        blind = self.load_json("benchmarks/blind/manifest.v1.json")
        _validate_schema(blind_schema, blind, "blind review manifest")
        _validate_schema(
            holdout_schema,
            self.load_json("benchmarks/holdout/manifest.v1.json"),
            "holdout manifest",
        )
        lock_by_id = _unique_by(locks["files"], "id", "source lock")
        rule_by_id = _unique_by(rules_document["rules"], "id", "rule")
        if verify_hashes:
            self._verify_hashes(locks["files"])
        for rule in rules_document["rules"]:
            source = lock_by_id.get(rule["source_id"])
            if source is None or source["purpose"] not in {"architecture", "agent_knowledge"}:
                raise BenchmarkValidationError(f"rule {rule['id']} has unknown or invalid source {rule['source_id']}")

        suites = self.suites()
        seen_ids: set[str] = set()
        counts: dict[str, int] = {"MODEL": 0, "AGENT": 0, "SYSTEM_E2E": 0}
        agent_counts: dict[str, int] = {}
        for suite in suites:
            _validate_schema(suite_schema, suite, f"suite {suite.get('id', '<unknown>')}", registry=registry)
            if suite["expected_case_count"] != len(suite["cases"]):
                raise BenchmarkValidationError(f"suite {suite['id']} expected_case_count does not match cases")
            for case in suite["cases"]:
                _validate_schema(case_schema, case, f"case {case.get('id', '<unknown>')}")
                case_id = case["id"]
                if case_id in seen_ids:
                    raise BenchmarkValidationError(f"duplicate case id: {case_id}")
                seen_ids.add(case_id)
                if case["suite"] != suite["id"] or case["benchmark_type"] != suite["benchmark_type"]:
                    raise BenchmarkValidationError(f"case {case_id} does not match its suite identity")
                refs = case["oracle"]["rule_refs"]
                if case["oracle"]["mode"] != "HUMAN_VERIFICATION_REQUIRED" and not refs:
                    raise BenchmarkValidationError(f"derived case {case_id} must cite at least one rule")
                unknown_refs = sorted(set(refs) - set(rule_by_id))
                if unknown_refs:
                    raise BenchmarkValidationError(f"case {case_id} cites unknown rules: {unknown_refs}")
                self._validate_rule_trace_assertion(case)
                if case["oracle"]["mode"] == "HUMAN_VERIFICATION_REQUIRED":
                    self._validate_human_truth_case(case, lock_by_id)
                if case["partition"] == "HOLDOUT":
                    raise BenchmarkValidationError("sealed holdout case bodies must not be committed to the repository")
                counts[case["benchmark_type"]] += 1
                agent = case["metadata"]["agent"]
                if case["benchmark_type"] == "AGENT" and isinstance(agent, str):
                    agent_counts[agent] = agent_counts.get(agent, 0) + 1

        self._validate_policy_counts(counts, agent_counts, len(seen_ids))
        self._validate_coverage(coverage, suites, seen_ids, set(lock_by_id))
        self._validate_model_matrix(model_matrix, suites)
        self._validate_blind_manifest(blind, seen_ids)
        self._validate_holdout()
        return ValidationSummary(len(suites), len(seen_ids), len(rule_by_id), len(lock_by_id), counts | agent_counts)

    def _verify_hashes(self, locks: list[dict[str, Any]]) -> None:
        for item in locks:
            path = (self.root / item["path"]).resolve()
            try:
                path.relative_to(self.root)
            except ValueError as exc:
                raise BenchmarkValidationError(f"source lock escapes repository: {item['path']}") from exc
            if not path.is_file():
                raise BenchmarkValidationError(f"source lock is missing: {item['path']}")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != item["sha256"]:
                raise BenchmarkValidationError(f"source hash mismatch: {item['path']}")

    def _validate_human_truth_case(self, case: dict[str, Any], locks: dict[str, dict[str, Any]]) -> None:
        required_human = [item for item in case["rubric"]["human"] if item["required"]]
        if not required_human:
            raise BenchmarkValidationError(f"human-verification case {case['id']} lacks a required human rubric")
        if "HUMAN_VERIFICATION_REQUIRED" not in case["oracle"]["expected"].values():
            raise BenchmarkValidationError(f"human-verification case {case['id']} lacks the explicit sentinel")
        forbidden_paths = {"$.ranking", "$.rankings", "$.factual_accuracy"}
        scored_paths = {item["path"] for item in case["rubric"]["automated"]}
        if forbidden_paths & scored_paths:
            raise BenchmarkValidationError(f"human-verification case {case['id']} automates an external fact label")
        source = case["input"]
        if source["kind"] == "REFERENCE_FILE":
            matching = [item for item in locks.values() if item["path"] == source["path"]]
            if len(matching) != 1 or matching[0]["sha256"] != source["sha256"]:
                raise BenchmarkValidationError(f"anchor case {case['id']} does not match its source lock")

    @staticmethod
    def _validate_rule_trace_assertion(case: dict[str, Any]) -> None:
        refs = case["oracle"]["rule_refs"]
        if not refs:
            return
        traces = [
            item
            for item in case["rubric"]["automated"]
            if item["path"] == "$.rule_citations" and item["type"] == "CONTAINS_ALL"
        ]
        if len(traces) != 1 or set(traces[0].get("values", [])) != set(refs) or not traces[0]["critical"]:
            raise BenchmarkValidationError(f"case {case['id']} lacks a critical exact rule-trace assertion")

    def _validate_policy_counts(self, counts: dict[str, int], agent_counts: dict[str, int], total: int) -> None:
        expected = self.policy()["expected_counts"]
        actual = {"model": counts["MODEL"], "system_e2e": counts["SYSTEM_E2E"]}
        for key, value in actual.items():
            if value != expected[key]:
                raise BenchmarkValidationError(f"{key} case count is {value}, expected {expected[key]}")
        if counts["AGENT"] != sum(expected["agents"].values()):
            raise BenchmarkValidationError(
                f"agent case count is {counts['AGENT']}, expected {sum(expected['agents'].values())}"
            )
        if total != expected["total"]:
            raise BenchmarkValidationError(f"total case count is {total}, expected {expected['total']}")
        if agent_counts != expected["agents"]:
            raise BenchmarkValidationError(f"agent case counts differ: {agent_counts}")

    def _validate_holdout(self) -> None:
        holdout = self.load_json("benchmarks/holdout/manifest.v1.json")
        if holdout["storage"] != "EXTERNAL_SEALED" or holdout["governance"]["repository_contains_inputs"]:
            raise BenchmarkValidationError("holdout must remain externally sealed")
        if sum(holdout["distribution"].values()) != holdout["planned_case_count"]:
            raise BenchmarkValidationError("holdout distribution does not match planned case count")

    @staticmethod
    def _validate_model_matrix(model_matrix: dict[str, Any], suites: list[dict[str, Any]]) -> None:
        matching = [suite for suite in suites if suite["id"] == model_matrix["suite_id"]]
        if len(matching) != 1 or matching[0]["benchmark_type"] != "MODEL":
            raise BenchmarkValidationError("model matrix must target exactly one MODEL suite")
        suite_case_ids = {case["id"] for case in matching[0]["cases"]}
        unknown = set(model_matrix["case_ids"]) - suite_case_ids
        if unknown:
            raise BenchmarkValidationError(f"model matrix cites unknown cases: {sorted(unknown)}")
        if set(model_matrix["candidates"]) != {"kimi-k3", "glm-5.2", "qwen3.8-max"}:
            raise BenchmarkValidationError("model matrix must compare the three approved candidate models")
        if model_matrix["minimum_independent_repeats"] != 3 or model_matrix.get("cache_allowed") is not False:
            raise BenchmarkValidationError("formal model matrix requires three uncached independent repeats")

    @staticmethod
    def _validate_blind_manifest(blind: dict[str, Any], case_ids: set[str]) -> None:
        unknown = set(blind["case_ids"]) - case_ids
        if unknown:
            raise BenchmarkValidationError(f"blind review manifest cites unknown cases: {sorted(unknown)}")
        required_hidden = {"provider", "model", "prompt_version", "agent_version", "run_order"}
        if set(blind["governance"]["hidden_fields"]) != required_hidden:
            raise BenchmarkValidationError("blind review must hide provider, model, versions and run order")

    @staticmethod
    def _validate_coverage(
        coverage: dict[str, Any],
        suites: list[dict[str, Any]],
        case_ids: set[str],
        source_lock_ids: set[str],
    ) -> None:
        suite_ids = {suite["id"] for suite in suites}
        for dimension, mapping in coverage["dimensions"].items():
            unknown_suites = set(mapping["suite_ids"]) - suite_ids
            unknown_cases = set(mapping["case_ids"]) - case_ids
            unknown_sources = set(mapping["source_lock_ids"]) - source_lock_ids
            if unknown_suites or unknown_cases or unknown_sources:
                raise BenchmarkValidationError(
                    f"coverage dimension {dimension} has unknown references: "
                    f"suites={sorted(unknown_suites)}, cases={sorted(unknown_cases)}, "
                    f"sources={sorted(unknown_sources)}"
                )


def _validate_schema(
    schema: dict[str, Any],
    value: object,
    label: str,
    *,
    registry: Registry | None = None,
) -> None:
    validator = Draft202012Validator(schema, registry=registry or Registry())
    errors = sorted(validator.iter_errors(value), key=lambda item: item.json_path)
    if errors:
        raise BenchmarkValidationError(f"{label} violates schema at {errors[0].json_path}: {errors[0].message}")


def _unique_by(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        value = item[key]
        if value in result:
            raise BenchmarkValidationError(f"duplicate {label} {value}")
        result[value] = item
    return result


__all__ = ["BenchmarkCatalog", "BenchmarkValidationError", "ValidationSummary", "repository_root"]
