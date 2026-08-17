from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

from .locale_validation import generated_locale_matches

_ROOT = Path(__file__).resolve().parents[6]


class SpecialistReportV3Error(ValueError):
    pass


class SpecialistReportV3Adapter:
    def __init__(self) -> None:
        self._schema = json.loads(
            (_ROOT / "packages/contracts/reports/specialist-report.v3.json").read_text(encoding="utf-8")
        )

    def adapt(self, document: dict[str, Any], *, locale: str) -> dict[str, Any]:
        if document.get("schema_version") != "2.0" or locale not in {"zh-CN", "en"}:
            raise SpecialistReportV3Error("the adapter requires one SpecialistReportDocumentV2 and a frozen locale")
        result = copy.deepcopy(document)
        result["schema_version"] = "3.0"
        result["locale"] = locale
        result["domain_payload"] = self._domain_payload(result, locale)
        self._localize_action_owners(result["actions"], locale)
        weakened_claim_ids = self._align_claim_strength(result["claims"], result["citations"])
        if not result["risks"]:
            result["risks"] = [
                claim["claim_id"] for claim in result["claims"] if claim["status"] != "VERIFIED"
            ]
        else:
            result["risks"] = list(dict.fromkeys([*result["risks"], *weakened_claim_ids]))
        generated_prose = [
            *[item["text"] for item in result["claims"]],
            result["domain_payload"],
            *[
                {
                    "title": item["title"],
                    "owner": item["owner"],
                    "success_criteria": item["success_criteria"],
                    "failure_triggers": item["failure_triggers"],
                    "required_evidence": item["required_evidence"],
                }
                for item in result["actions"]
            ],
        ]
        if not generated_locale_matches(locale, generated_prose):
            raise SpecialistReportV3Error("generated report prose does not match the frozen locale")
        result["source_directory"] = self._visible_sources(result["source_directory"], result["citations"])
        self._validate_claim_strength(result["claims"], result["citations"])
        errors = sorted(
            Draft202012Validator(self._schema, format_checker=FormatChecker()).iter_errors(result),
            key=lambda item: item.json_path,
        )
        if errors:
            raise SpecialistReportV3Error(
                f"SpecialistReportDocumentV3 contract violation at {errors[0].json_path}: {errors[0].message}"
            )
        return result

    @staticmethod
    def _localize_action_owners(actions: list[dict[str, Any]], locale: str) -> None:
        owner_names = {
            "zh-CN": {
                "evaluation-manager": "项目负责人",
                "user-evidence": "目标用户专家",
                "product-engineering": "产品经理",
                "business-investment": "投资人",
                "evidence-auditor": "证据校准专家",
            },
            "en": {
                "evaluation-manager": "Evaluation manager",
                "user-evidence": "User evidence specialist",
                "product-engineering": "Product engineering specialist",
                "business-investment": "Business investment specialist",
                "evidence-auditor": "Evidence auditor",
            },
        }[locale]
        for action in actions:
            action["owner"] = owner_names.get(str(action["owner"]), action["owner"])

    def _domain_payload(self, document: dict[str, Any], locale: str) -> dict[str, Any]:
        agent_code = str(document["agent_code"])
        payload = dict(document.get("domain_payload") or {})
        claims = [str(item["text"]) for item in document["claims"]]
        actions = [str(item["title"]) for item in document["actions"]]
        fallback = "待补充可复核证据" if locale == "zh-CN" else "Requires auditable evidence"
        if agent_code == "user-evidence":
            return {
                "kind": "USER_EVIDENCE",
                "target_segments": self._strings(
                    payload, ("target_segments", "segments", "target_users"), claims, fallback
                ),
                "jobs_and_scenarios": self._strings(
                    payload, ("jobs_and_scenarios", "scenarios", "jobs"), claims, fallback
                ),
                "behavioral_evidence": self._strings(
                    payload, ("behavioral_evidence", "behaviors", "observations"), claims, fallback
                ),
                "retention_and_payment": self._strings(
                    payload, ("retention_and_payment", "retention", "payment"), (), fallback
                ),
                "validation_plan": self._strings(payload, ("validation_plan",), actions, fallback),
            }
        if agent_code == "product-engineering":
            stage = str(payload.get("stage") or fallback)
            return {
                "kind": "PRODUCT_ENGINEERING",
                "stage_gate": stage,
                "core_flows": self._strings(payload, ("core_flows",), claims, fallback),
                "delivery_and_reliability": self._strings(
                    payload, ("delivery_and_reliability", "delivery_risks", "reliability"), claims, fallback
                ),
                "dependencies_and_security": self._strings(
                    payload, ("dependencies_and_security", "dependencies", "security", "bus_factor"), (), fallback
                ),
                "retest_gates": self._strings(payload, ("retest_gates", "stage_gates"), actions, fallback),
            }
        if agent_code == "business-investment":
            return {
                "kind": "BUSINESS_INVESTMENT",
                "business_model": self._strings(payload, ("business_model",), claims, fallback),
                "unit_economics": self._strings(payload, ("unit_economics",), (), fallback),
                "competition_and_market": self._strings(
                    payload, ("competition_and_market", "competition", "market"), (), fallback
                ),
                "investment_gates": self._strings(payload, ("investment_gates",), actions, fallback),
                "compliance_scope": self._strings(payload, ("compliance_scope", "compliance"), (), fallback),
            }
        if agent_code == "evidence-auditor":
            metrics = [f"{item['label']}: {item['value']}" for item in document["metrics"]]
            independence = len({str(item["independence_group"]) for item in document["source_directory"]})
            independence_text = (
                f"独立来源组：{independence}" if locale == "zh-CN" else f"Independent source groups: {independence}"
            )
            return {
                "kind": "EVIDENCE_AUDIT",
                "coverage_by_dimension": metrics or [fallback],
                "source_independence": [independence_text],
                "conflicts": self._strings(payload, ("conflicts",), (), fallback),
                "calibration_decisions": self._strings(payload, ("calibration_decisions",), claims, fallback),
                "evidence_gaps": self._strings(payload, ("evidence_gaps",), actions, fallback),
            }
        raise SpecialistReportV3Error("the specialist Agent code is unsupported")

    @staticmethod
    def _strings(
        payload: dict[str, Any],
        keys: tuple[str, ...],
        default: list[str] | tuple[str, ...],
        fallback: str,
    ) -> list[str]:
        values: list[str] = []
        for key in keys:
            if key not in payload or payload[key] in (None, "", [], {}):
                continue
            raw = payload[key]
            items = raw if isinstance(raw, list) else [raw]
            for item in items:
                if isinstance(item, (dict, list)):
                    value = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                else:
                    value = str(item).strip()
                if value:
                    values.append(value)
        if not values:
            values = [str(item).strip() for item in default if str(item).strip()]
        return list(dict.fromkeys(values)) or [fallback]

    @staticmethod
    def _visible_sources(
        source_directory: list[dict[str, Any]], citations: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        cited = {
            str(item["source_locator_id"])
            for item in citations
            if item.get("source_locator_id") is not None and item["audit_status"] in {"VERIFIED", "DOWNGRADED"}
        }
        unique: dict[str, dict[str, Any]] = {}
        for source in source_directory:
            if str(source["source_locator_id"]) not in cited:
                continue
            key = str(source.get("canonical_url") or "").strip().lower()
            if not key:
                key = f"{source['source_kind']}:{source['title']}:{source['content_sha256']}"
            unique.setdefault(key, copy.deepcopy(source))
        return list(unique.values())

    @staticmethod
    def _align_claim_strength(claims: list[dict[str, Any]], citations: list[dict[str, Any]]) -> list[str]:
        citation_by_id = {str(item["citation_id"]): item for item in citations}
        weakened: list[str] = []
        for claim in claims:
            attached = [citation_by_id[item] for item in claim["citation_ids"] if item in citation_by_id]
            supports = [item for item in attached if item["support_role"] == "SUPPORT"]
            status = claim["status"]
            supported = (
                status == "PENDING_VALIDATION"
                or (status == "VERIFIED" and any(item["audit_status"] == "VERIFIED" for item in supports))
                or (
                    status == "DOWNGRADED"
                    and any(item["audit_status"] in {"VERIFIED", "DOWNGRADED"} for item in supports)
                )
            )
            if supported:
                continue
            claim["status"] = "PENDING_VALIDATION"
            claim["score_bearing"] = False
            weakened.append(str(claim["claim_id"]))
        return weakened

    @staticmethod
    def _validate_claim_strength(claims: list[dict[str, Any]], citations: list[dict[str, Any]]) -> None:
        citation_by_id = {str(item["citation_id"]): item for item in citations}
        for claim in claims:
            attached = [citation_by_id[item] for item in claim["citation_ids"] if item in citation_by_id]
            supports = [item for item in attached if item["support_role"] == "SUPPORT"]
            if claim["status"] == "VERIFIED" and not any(item["audit_status"] == "VERIFIED" for item in supports):
                raise SpecialistReportV3Error("Claim strength is stronger than its supporting Citation strength")
            if claim["status"] == "DOWNGRADED" and not any(
                item["audit_status"] in {"VERIFIED", "DOWNGRADED"} for item in supports
            ):
                raise SpecialistReportV3Error("Claim strength is stronger than its supporting Citation strength")


__all__ = ["SpecialistReportV3Adapter", "SpecialistReportV3Error"]
