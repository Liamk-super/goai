"""Command-line entry point for Benchmark V1."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .catalog import BenchmarkCatalog, BenchmarkValidationError
from .comparison import compare_model_runs, compare_regression_runs
from .exporters import export_agentloop_dataset, export_promptfoo_tests
from .runner import OpenAICompatibleClient, SubmissionUnknownError, run_model_api, worker_runtime_identity
from .scoring import score_run


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="launchscope-benchmark")
    parser.add_argument("--root", type=Path, default=None, help="repository root; defaults to the installed workspace")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="validate schemas, counts, rules and source hashes")
    validate.add_argument("--skip-hashes", action="store_true", help="schema-only diagnostics; never a release gate")
    score = commands.add_parser("score", help="score a canonical Run Manifest")
    score.add_argument("manifest", type=Path)
    score.add_argument("--output", type=Path)
    self_test = commands.add_parser("self-test", help="exercise the full harness with an explicit oracle provider")
    self_test.add_argument("--suite", default="system-e2e-v1")
    self_test.add_argument("--output", type=Path)
    promptfoo = commands.add_parser("export-promptfoo", help="export canonical model or Agent cases for Promptfoo")
    promptfoo_source = promptfoo.add_mutually_exclusive_group(required=True)
    promptfoo_source.add_argument("--suite")
    promptfoo_source.add_argument("--case-set", choices=["formal-model-selection"])
    promptfoo.add_argument("--output", type=Path, required=True)
    agentloop = commands.add_parser("export-agentloop-dataset", help="create a local AgentLoop-compatible JSONL draft")
    agentloop.add_argument("--suite", required=True)
    agentloop.add_argument("--output", type=Path, required=True)
    agentloop.add_argument("--allow-local-export", action="store_true")
    run_api = commands.add_parser("run-model-api", help="run the approved formal model Case set without retries")
    run_api.add_argument("--model", required=True)
    run_api.add_argument("--provider", default="openai-compatible")
    run_api.add_argument("--repeat-index", type=int, required=True)
    run_api.add_argument("--output", type=Path, required=True)
    run_api.add_argument("--authorize-paid-calls", action="store_true")
    run_api.add_argument("--base-url-env", default="AGENTTEAMS_MODEL_BASE_URL")
    run_api.add_argument("--api-key-env", default="AGENTTEAMS_MODEL_API_KEY")
    run_api.add_argument("--input-rate-env", default="LAUNCHSCOPE_MODEL_INPUT_USD_PER_MILLION")
    run_api.add_argument("--output-rate-env", default="LAUNCHSCOPE_MODEL_OUTPUT_USD_PER_MILLION")
    run_api.add_argument("--timeout-seconds", type=int, default=120)
    model_compare = commands.add_parser("compare-models", help="aggregate the complete three-model formal matrix")
    model_compare.add_argument("manifests", type=Path, nargs="+")
    model_compare.add_argument("--output", type=Path, required=True)
    regression = commands.add_parser("compare-regression", help="compare two runs for Prompt/RAG/Skill regressions")
    regression.add_argument("baseline", type=Path)
    regression.add_argument("candidate", type=Path)
    regression.add_argument("--output", type=Path, required=True)
    runtime = commands.add_parser("verify-worker-runtime", help="verify six live AgentTeams Worker model identities")
    runtime.add_argument("snapshot", type=Path)
    runtime.add_argument("--expected-model", required=True)
    runtime.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    catalog = BenchmarkCatalog(args.root)
    try:
        if args.command == "validate":
            summary = catalog.validate(verify_hashes=not args.skip_hashes)
            _emit({"status": "PASS", **summary.as_dict()}, None)
            return 0
        if args.command == "score":
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            report = score_run(catalog, manifest)
            _emit(report.document, args.output)
            return 1 if report.critical_violations else 0
        if args.command == "self-test":
            catalog.validate()
            manifest = _oracle_manifest(catalog, args.suite)
            report = score_run(catalog, manifest)
            result = {"manifest": manifest, "score_report": report.document, "acceptance_scope": "HARNESS_ONLY"}
            _emit(result, args.output)
            return 1 if report.critical_violations else 0
        if args.command == "export-promptfoo":
            catalog.validate()
            if args.case_set == "formal-model-selection":
                matrix = catalog.model_matrix()
                suite_id = matrix["suite_id"]
                case_ids = matrix["case_ids"]
            else:
                suite_id = args.suite
                case_ids = None
            count = export_promptfoo_tests(catalog, suite_id, args.output, case_ids=case_ids)
            _emit({"status": "PASS", "exported": count, "output": str(args.output)}, None)
            return 0
        if args.command == "run-model-api":
            catalog.validate()
            client = OpenAICompatibleClient(
                base_url=_required_env(args.base_url_env),
                api_key=_required_env(args.api_key_env),
                timeout_seconds=args.timeout_seconds,
            )
            manifest = run_model_api(
                catalog,
                client=client,
                model=args.model,
                provider=args.provider,
                case_ids=catalog.model_matrix()["case_ids"],
                repeat_index=args.repeat_index,
                authorized=bool(args.authorize_paid_calls),
                input_usd_per_million=Decimal(_required_env(args.input_rate_env)),
                output_usd_per_million=Decimal(_required_env(args.output_rate_env)),
            )
            _emit(manifest, args.output)
            return 1 if score_run(catalog, manifest).critical_violations else 0
        if args.command == "compare-models":
            manifests = [json.loads(path.read_text(encoding="utf-8")) for path in args.manifests]
            _emit(compare_model_runs(catalog, manifests), args.output)
            return 0
        if args.command == "compare-regression":
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
            regression_report = compare_regression_runs(catalog, baseline, candidate)
            _emit(regression_report, args.output)
            return 1 if regression_report["summary"]["new_critical_violations"] else 0
        if args.command == "verify-worker-runtime":
            snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
            identity = worker_runtime_identity(snapshot, expected_model=args.expected_model)
            _emit(identity, args.output)
            return 0 if identity["status"] == "VERIFIED" else 1
        if args.command == "export-agentloop-dataset":
            catalog.validate()
            count = export_agentloop_dataset(
                catalog, args.suite, args.output, local_only=bool(args.allow_local_export)
            )
            _emit(
                {"status": "LOCAL_EXPORT_ONLY", "exported": count, "output": str(args.output), "uploaded": False},
                None,
            )
            return 0
    except SubmissionUnknownError as exc:
        print(json.dumps({"status": "SUBMISSION_UNKNOWN", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3
    except (BenchmarkValidationError, OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    raise AssertionError(f"unhandled command {args.command}")


def _oracle_manifest(catalog: BenchmarkCatalog, suite_id: str) -> dict[str, Any]:
    suite = catalog.suite(suite_id)
    outputs = {}
    for case in suite["cases"]:
        outputs[case["id"]] = case["oracle"]["expected"] | {"rule_citations": case["oracle"]["rule_refs"]}
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": "1.0",
        "run_id": f"harness-self-test:{suite_id}",
        "suite_id": suite_id,
        "suite_version": suite["version"],
        "case_ids": [case["id"] for case in suite["cases"]],
        "execution": {
            "mode": "HARNESS_SELF_TEST",
            "lane": "HARNESS_SELF_TEST",
            "provider": "canonical-oracle-provider",
            "network_allowed": False,
            "cache_allowed": False,
        },
        "runtime_identity": {
            "status": "VERIFIED",
            "requested": {"harness": "none"},
            "observed": {"harness": "none"},
            "source": "HARNESS",
            "details": "No model is used by the deterministic harness self-test.",
        },
        "implementation": {
            "git_commit": "WORKTREE",
            "dirty": True,
            "prompt_sha256": None,
            "agent_sha256": None,
            "skill_sha256": None,
        },
        "source_lock_sha256": catalog.source_lock_sha256(),
        "started_at": now,
        "finished_at": now,
        "repeat_index": 1,
        "outputs": outputs,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_amount": 0,
            "currency": "USD",
            "usage_known": True,
            "latency_ms": 0,
        },
    }


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise BenchmarkValidationError(f"required environment variable is missing: {name}")
    return value


def _emit(document: dict[str, Any], output: Path | None) -> None:
    encoded = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if output is None:
        print(encoded, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(encoded, encoding="utf-8")
    print(json.dumps({"status": "PASS", "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
