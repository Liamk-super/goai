"""Explicit, non-authoritative model extraction for intake material.

The provider response is returned as a draft only.  This module never writes a
ProductProfile and never advances an EvaluationRun.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from launchscope_api.modules.evaluation.intake_application import IntakeValidationError

PROFILE_FIELDS = (
    "one_line_value_claim",
    "problem",
    "core_features",
    "team",
    "target_user",
    "payer",
    "stage",
    "validation_goal",
    "region",
    "timing",
    "inspectable_materials",
)

_SENSITIVE_IDENTIFIER = "[sensitive identifier omitted]"


def _provider_url_is_safe(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme == "https" and parsed.netloc:
        return True
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}


def _redact_visual_output(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", _SENSITIVE_IDENTIFIER, text, flags=re.I)
    text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", _SENSITIVE_IDENTIFIER, text)
    text = re.sub(r"(?<![A-Z0-9])[0-9A-Z]{18}(?![A-Z0-9])", _SENSITIVE_IDENTIFIER, text, flags=re.I)
    labelled_identifier = re.compile(
        r"(?i)(barcode|licen[cs]e(?:\s+(?:number|no\.?))?|identity(?:\s+number)?|"
        r"social\s+credit\s+code|registration(?:\s+(?:number|no\.?))?|"
        r"条(?:形)?码|统一社会信用代码|许可证号|证照编号|身份证号|注册号)"
        r"(\s*[:：#]?\s*)\*?[A-Z0-9-]{6,}\*?"
    )
    return labelled_identifier.sub(lambda match: f"{match.group(1)}{match.group(2)}{_SENSITIVE_IDENTIFIER}", text)


def _decode_json_object(message: object) -> dict[str, Any]:
    """Decode the first complete JSON object from an OpenAI-compatible text response."""
    if not isinstance(message, str) or not message.strip():
        raise ValueError("model response content is empty or not text")
    stripped = message.strip()
    candidates = [stripped]
    candidates.extend(
        match.group(1).strip()
        for match in re.finditer(r"```(?:json)?\s*(.*?)```", stripped, re.I | re.S)
    )
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            for index, character in enumerate(candidate):
                if character != "{":
                    continue
                try:
                    value, _end = decoder.raw_decode(candidate, index)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    return value
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("model response does not contain a JSON object")


@dataclass(frozen=True, slots=True)
class ExtractionDraft:
    fields: dict[str, str | None]
    model_id: str

    @property
    def missing_fields(self) -> list[str]:
        return [field for field in PROFILE_FIELDS if not self.fields.get(field)]


class IntakeModelExtractor:
    """OpenAI-compatible adapter with a fixed environment-owned endpoint."""

    def __init__(self, *, base_url: str | None = None, api_key: str | None = None, model_id: str | None = None) -> None:
        self.base_url = (
            base_url or os.getenv("LAUNCHSCOPE_INTAKE_MODEL_BASE_URL") or os.getenv("AGENTTEAMS_MODEL_BASE_URL") or ""
        ).rstrip("/")
        self.api_key = (
            api_key or os.getenv("LAUNCHSCOPE_INTAKE_MODEL_API_KEY") or os.getenv("AGENTTEAMS_MODEL_API_KEY") or ""
        )
        self.model_id = model_id or os.getenv("LAUNCHSCOPE_INTAKE_MODEL_ID") or os.getenv("AGENTTEAMS_MODEL_ID") or ""

    @staticmethod
    def _timeout_seconds() -> float:
        try:
            configured = float(os.getenv("LAUNCHSCOPE_INTAKE_MODEL_TIMEOUT_SECONDS", "120"))
        except ValueError:
            configured = 120.0
        return min(max(configured, 5.0), 180.0)

    def extract(self, raw_content: str, *, allow_external_processing: bool) -> ExtractionDraft:
        content = raw_content.strip()
        if not allow_external_processing:
            raise IntakeValidationError("external model processing requires explicit user confirmation")
        if not content or len(content) > 30_000:
            raise IntakeValidationError("intake content must contain 1 to 30000 characters")
        if not _provider_url_is_safe(self.base_url) or not self.api_key or not self.model_id:
            raise IntakeValidationError("the intake model provider is not safely configured")
        prompt = (
            "Extract a product profile from the material. Return one JSON object only. "
            f"Allowed keys: {', '.join(PROFILE_FIELDS)}. Values must be concise strings or null. "
            "Put stated team composition or responsibilities in team, and stated launch dates, deadlines, or "
            "market windows in timing. "
            "Do not infer facts not present in the material; use null when unknown.\n\nMATERIAL:\n"
            + content
        )
        payload = json.dumps(
            {
                "model": self.model_id,
                "temperature": 0.1,
                "max_tokens": 32768,
                "messages": [
                    {
                        "role": "system",
                        "content": "You extract evidence-backed product intake fields. Never invent missing facts.",
                    },
                    {"role": "user", "content": prompt},
                ],
            }
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds()) as response:  # noqa: S310 - URL is environment-owned and HTTPS-checked
                raw_response = response.read()
        except HTTPError as exc:
            raise IntakeValidationError(f"the intake model provider returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise IntakeValidationError("the intake model provider request failed before a usable response") from exc
        try:
            document: dict[str, Any] = json.loads(raw_response)
            choice = document["choices"][0]
            message = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise IntakeValidationError("the intake model provider returned an invalid response envelope") from exc
        if choice.get("finish_reason") == "length":
            raise IntakeValidationError("the intake model response was truncated before a complete product profile")
        try:
            extracted = _decode_json_object(message)
        except ValueError as exc:
            raise IntakeValidationError(
                "the intake model response did not contain a complete product profile JSON object"
            ) from exc
        if not isinstance(extracted, dict):
            raise IntakeValidationError("the configured model returned a non-object extraction draft")
        fields = {
            field: (str(extracted[field]).strip()[:2000] if extracted.get(field) not in (None, "") else None)
            for field in PROFILE_FIELDS
        }
        return ExtractionDraft(fields, self.model_id)

    def analyze_visual_page(
        self,
        *,
        file_name: str,
        page_number: int,
        image_data_url: str,
        text_hint: str,
        allow_external_processing: bool,
        local_table_detected: bool = False,
    ) -> dict[str, Any]:
        if not allow_external_processing:
            raise IntakeValidationError("external model processing requires explicit user confirmation")
        if not 1 <= page_number <= 10_000 or len(file_name) > 255:
            raise IntakeValidationError("visual page metadata is invalid")
        if not image_data_url.startswith("data:image/jpeg;base64,") or len(image_data_url) > 4_000_000:
            raise IntakeValidationError("visual page image must be a bounded JPEG data URL")
        vision_model_id = os.getenv("LAUNCHSCOPE_VISION_MODEL_ID") or self.model_id
        if not _provider_url_is_safe(self.base_url) or not self.api_key or not vision_model_id:
            raise IntakeValidationError("the intake model provider is not safely configured")
        prompt = (
            f"Analyze {file_name}, page {page_number}. Return one JSON object only with keys recognition_type, "
            "summary, rotation_degrees, confidence, and table. recognition_type must be TEXT, TABLE, IMAGE, "
            "DIAGRAM, SCREENSHOT, SCAN, or MIXED. rotation_degrees must be 0, 90, 180, or 270. confidence must "
            "be a number from 0 to 1. table is null or an object with title, headers, and rows. Preserve visible "
            "numbers and percentages, but include at most 8 representative rows and 8 headers. For any visible "
            "table, name the most decision-relevant numbers or percentages in the summary even when table rows are "
            "also returned. Keep summary under 500 characters and each table cell under 160 characters. Describe "
            "architecture diagrams and product screenshots concretely. "
            "If the page is mostly one document photograph or scan with little text-layer content, inspect it at "
            "all four orientations and report the clockwise rotation needed to read the original page. Never "
            "repeat personal contact details, licence numbers, identity numbers, credentials, or signatures. "
            f"The PDF text layer, which may be incomplete, is:\n{text_hint[:4_000]}"
        )
        if local_table_detected:
            prompt += (
                "\nA local positional parser already preserved the structured table rows. Set table to null and do not "
                "transcribe the table. Only summarize its purpose, visual layout, and the most important visible "
                "numbers."
            )
        payload = json.dumps(
            {
                "model": vision_model_id,
                "temperature": 0,
                "max_tokens": 8192,
                "messages": [
                    {
                        "role": "system",
                        "content": "You inspect one private document page and return evidence-bounded JSON.",
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_data_url, "detail": "high"}},
                        ],
                    },
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        response = self._invoke_json(payload, "visual page")
        recognition_type = str(response.get("recognition_type") or "MIXED").upper()
        if recognition_type not in {"TEXT", "TABLE", "IMAGE", "DIAGRAM", "SCREENSHOT", "SCAN", "MIXED"}:
            recognition_type = "MIXED"
        try:
            rotation = int(response.get("rotation_degrees") or 0)
        except (TypeError, ValueError):
            rotation = 0
        if rotation not in {0, 90, 180, 270}:
            rotation = 0
        try:
            confidence = min(1.0, max(0.0, float(response.get("confidence") or 0)))
        except (TypeError, ValueError):
            confidence = 0.0
        raw_table = response.get("table") if isinstance(response.get("table"), dict) else None
        table = None
        if raw_table is not None:
            raw_headers = raw_table.get("headers") if isinstance(raw_table.get("headers"), list) else []
            raw_rows = raw_table.get("rows") if isinstance(raw_table.get("rows"), list) else []
            table = {
                "title": _redact_visual_output(raw_table.get("title"))[:500] or None,
                "headers": [_redact_visual_output(value)[:200] for value in raw_headers[:12]],
                "rows": [
                    [_redact_visual_output(value)[:200] for value in row[:12]]
                    for row in raw_rows[:20]
                    if isinstance(row, list)
                ],
            }
        return {
            "model_id": vision_model_id,
            "recognition_type": recognition_type,
            "summary": _redact_visual_output(response.get("summary"))[:4_000],
            "rotation_degrees": rotation,
            "confidence": confidence,
            "table": table,
        }

    def generate_validation_tasks(
        self,
        raw_content: str,
        *,
        allow_external_processing: bool,
        locale: str = "zh-CN",
    ) -> dict[str, Any]:
        content = raw_content.strip()
        if not allow_external_processing:
            raise IntakeValidationError("external model processing requires explicit user confirmation")
        if not content or len(content) > 30_000:
            raise IntakeValidationError("validation task context must contain 1 to 30000 characters")
        if not _provider_url_is_safe(self.base_url) or not self.api_key or not self.model_id:
            raise IntakeValidationError("the intake model provider is not safely configured")
        prompt = (
            "Generate 1 to 5 executable core user-validation task drafts from the material. Return one JSON object "
            "with a tasks array only. Each task must contain task_key, description, expected_observable_outcome, "
            "max_steps, rationale, and source_hints. task_key must be stable snake_case. description must state the "
            "user starting state and action. expected_observable_outcome must be visible in the page, generated "
            "asset, history, or durable state. max_steps must be 1 to 100. source_hints must be an array of brief "
            "file-name/page or URL references already present in MATERIAL. Cover the value proposition, the core "
            "material-to-generation path, output usability, and the highest-risk workflow/Skill/AI coworker or "
            "commercial assumption. Do not create low-value tasks such as merely browsing the home page. Do not "
            "repeat personal contact details, licence numbers, identity numbers, credentials, or signatures. Do "
            "not present declarations in application materials as verified facts.\n\nMATERIAL:\n"
            + content
        )
        output_language = (
            "natural English"
            if locale == "en"
            else "natural Simplified Chinese"
        )
        payload = json.dumps(
            {
                "model": self.model_id,
                "temperature": 0.1,
                "max_tokens": 8192,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You draft high-value, observable validation tasks without inventing evidence. "
                            f"Write description, expected_observable_outcome, and rationale in {output_language}."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        response = self._invoke_json(payload, "validation task")
        raw_tasks = response.get("tasks")
        if not isinstance(raw_tasks, list) or not 1 <= len(raw_tasks) <= 5:
            raise IntakeValidationError("the configured model did not return one to five validation tasks")
        tasks: list[dict[str, Any]] = []
        used_keys: set[str] = set()
        for index, value in enumerate(raw_tasks):
            if not isinstance(value, dict):
                raise IntakeValidationError("the configured model returned an invalid validation task")
            task_key = re.sub(r"[^a-z0-9_]+", "_", str(value.get("task_key") or "").lower()).strip("_")
            if not task_key:
                task_key = f"core_task_{index + 1}"
            if task_key in used_keys:
                task_key = f"{task_key}_{index + 1}"
            used_keys.add(task_key)
            description = str(value.get("description") or "").strip()
            expected = str(value.get("expected_observable_outcome") or "").strip()
            if not description or not expected:
                raise IntakeValidationError("the configured model returned an incomplete validation task")
            try:
                max_steps = min(100, max(1, int(value.get("max_steps") or 8)))
            except (TypeError, ValueError):
                max_steps = 8
            hints = value.get("source_hints")
            tasks.append(
                {
                    "task_key": task_key[:120],
                    "description": description[:2_000],
                    "expected_observable_outcome": expected[:2_000],
                    "max_steps": max_steps,
                    "rationale": str(value.get("rationale") or "").strip()[:1_000],
                    "source_hints": [str(item).strip()[:500] for item in hints[:10] if str(item).strip()]
                    if isinstance(hints, list)
                    else [],
                }
            )
        return {"model_id": self.model_id, "tasks": tasks}

    def _invoke_json(self, payload: bytes, operation: str) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds()) as response:  # noqa: S310
                raw_response = response.read()
        except HTTPError as exc:
            raise IntakeValidationError(f"the intake model provider returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise IntakeValidationError("the intake model provider request failed before a usable response") from exc
        try:
            document: dict[str, Any] = json.loads(raw_response)
            choice = document["choices"][0]
            message = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise IntakeValidationError("the intake model provider returned an invalid response envelope") from exc
        if choice.get("finish_reason") == "length":
            raise IntakeValidationError(f"the model response was truncated before a complete {operation} result")
        try:
            return _decode_json_object(message)
        except ValueError as exc:
            message = f"the model response did not contain a complete {operation} JSON object"
            raise IntakeValidationError(message) from exc

    def extract_requirement(self, raw_content: str, *, allow_external_processing: bool) -> dict[str, Any]:
        content = raw_content.strip()
        if not allow_external_processing:
            raise IntakeValidationError("external model processing requires explicit user confirmation")
        if not content or len(content) > 30_000:
            raise IntakeValidationError("intake content must contain 1 to 30000 characters")
        if not _provider_url_is_safe(self.base_url) or not self.api_key or not self.model_id:
            raise IntakeValidationError("the intake model provider is not safely configured")
        prompt = (
            "Normalize the requirement without adding facts. Return one JSON object only. "
            "Required keys are normalized_goal, evaluation_mode, requested_deliverables, constraints, "
            "success_criteria, explicit_facts, assumptions, unknowns, confidence_overall, confidence_fields, "
            "change_classification, scope_changed, cost_changed, and permission_changed. "
            "normalized_goal must be one exact contiguous span copied from MATERIAL; "
            "if uncertain, copy all of MATERIAL. "
            "requested_deliverables, constraints, and success_criteria must be JSON arrays of strings. "
            "explicit_facts must be an object using only applicable snake_case keys such as "
            "target_user, region, validation_goal, stage, and payer; every value must be an exact contiguous span "
            "copied from MATERIAL. If MATERIAL explicitly says what the review should judge, decide, or validate, "
            "validation_goal is required and must copy that exact span; do not put validation_goal in unknowns. "
            "assumptions must be a JSON array of {field,value,material}; material must be a "
            "JSON boolean, and the array must be empty when no inference is necessary. unknowns must be a JSON array "
            "of snake_case field identifiers, not prose. confidence_overall must be a JSON number from 0 to 1, and "
            "confidence_fields must map fact keys to JSON numbers from 0 to 1. evaluation_mode must be FULL_POTENTIAL, "
            "INVESTMENT_REVIEW, LAUNCH_REVIEW, or USER_VALIDATION. change_classification must be INITIAL, SUPPLEMENT, "
            "or REQUIREMENT_CHANGE. scope_changed, cost_changed, and permission_changed must be JSON booleans.\n\n"
            f"MATERIAL:\n{content}"
        )
        payload = json.dumps(
            {
                "model": self.model_id,
                "temperature": 0,
                "max_tokens": 32768,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a non-authoritative Intake Model. Extract exact spans and expose uncertainty."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            }
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds()) as response:  # noqa: S310
                raw_response = response.read()
        except HTTPError as exc:
            raise IntakeValidationError(f"the intake model provider returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise IntakeValidationError("the intake model provider request failed before a usable response") from exc
        try:
            document: dict[str, Any] = json.loads(raw_response)
            choice = document["choices"][0]
            message = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise IntakeValidationError("the intake model provider returned an invalid response envelope") from exc
        if choice.get("finish_reason") == "length":
            raise IntakeValidationError("the intake model response was truncated before a complete RequirementBrief")
        try:
            return _decode_json_object(message)
        except ValueError as exc:
            raise IntakeValidationError(
                "the intake model response did not contain a complete RequirementBrief JSON object"
            ) from exc
