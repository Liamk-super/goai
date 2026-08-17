"""Provider-neutral local exports for optional evaluation adapters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .catalog import BenchmarkCatalog, BenchmarkValidationError
from .runner import render_case_prompt


def export_promptfoo_tests(
    catalog: BenchmarkCatalog,
    suite_id: str,
    output: Path,
    *,
    case_ids: list[str] | None = None,
) -> int:
    suite = catalog.suite(suite_id)
    if suite["benchmark_type"] not in {"MODEL", "AGENT"}:
        raise BenchmarkValidationError("Promptfoo export supports only model and single-Agent suites")
    selected = suite["cases"]
    if case_ids is not None:
        by_id = {case["id"]: case for case in selected}
        unknown = set(case_ids) - set(by_id)
        if unknown:
            raise BenchmarkValidationError(f"Promptfoo export cites unknown cases: {sorted(unknown)}")
        selected = [by_id[case_id] for case_id in case_ids]
    tests = [
        {
            "description": case["id"],
            "vars": {"case_id": case["id"], "prompt": render_case_prompt(catalog, case)},
            "assert": _promptfoo_assertions(case),
        }
        for case in selected
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(tests, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(tests)


def _promptfoo_assertions(case: dict[str, Any]) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = [{"type": "is-json"}]
    for item in case["rubric"]["automated"]:
        path = item["path"]
        if not path.startswith("$."):
            continue
        lookup = "".join(f"[{json.dumps(part)}]" for part in path[2:].split("."))
        if item["type"] == "EQUALS":
            expected = json.dumps(item.get("expected"), ensure_ascii=False)
            expression = (
                f"const value = JSON.parse(output){lookup}; "
                f"return JSON.stringify(value) === JSON.stringify({expected});"
            )
        elif item["type"] == "CONTAINS_ALL":
            values = json.dumps(item.get("values", []), ensure_ascii=False)
            expression = (
                f"const value = JSON.parse(output){lookup}; "
                f"return Array.isArray(value) && {values}.every((item) => value.includes(item));"
            )
        else:
            continue
        assertions.append({"type": "javascript", "value": expression})
    return assertions


def export_agentloop_dataset(catalog: BenchmarkCatalog, suite_id: str, output: Path, *, local_only: bool) -> int:
    if not local_only:
        raise BenchmarkValidationError("AgentLoop export is disabled by default; only explicit local export is allowed")
    suite = catalog.suite(suite_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in suite["cases"]:
        rows.append(
            json.dumps(
                {
                    "case_id": case["id"],
                    "input": case["input"],
                    "oracle_mode": case["oracle"]["mode"],
                    "rule_refs": case["oracle"]["rule_refs"],
                    "rubric": case["rubric"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    output.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return len(rows)


__all__ = ["export_agentloop_dataset", "export_promptfoo_tests"]
