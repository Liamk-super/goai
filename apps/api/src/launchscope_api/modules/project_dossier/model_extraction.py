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
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from launchscope_api.modules.evaluation.intake_application import IntakeValidationError

PROFILE_FIELDS = (
    "problem",
    "core_features",
    "target_user",
    "payer",
    "stage",
    "validation_goal",
    "region",
    "inspectable_materials",
)


def _decode_json_object(message: object) -> dict[str, Any]:
    """Decode the first complete JSON object from an OpenAI-compatible text response."""
    if not isinstance(message, str) or not message.strip():
        raise ValueError("model response content is empty or not text")
    stripped = message.strip()
    candidates = [stripped]
    candidates.extend(match.group(1).strip() for match in re.finditer(r"```(?:json)?\s*(.*?)```", stripped, re.I | re.S))
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
        self.base_url = (base_url or os.getenv("AGENTTEAMS_MODEL_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or os.getenv("AGENTTEAMS_MODEL_API_KEY") or ""
        self.model_id = model_id or os.getenv("AGENTTEAMS_MODEL_ID") or ""

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
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc or not self.api_key or not self.model_id:
            raise IntakeValidationError("the intake model provider is not safely configured")
        prompt = (
            "Extract a product profile from the material. Return one JSON object only. "
            f"Allowed keys: {', '.join(PROFILE_FIELDS)}. Values must be concise strings or null. "
            "Do not infer facts not present in the material; use null when unknown.\n\nMATERIAL:\n"
            + content
        )
        payload = json.dumps(
            {
                "model": self.model_id,
                "temperature": 0.1,
                "max_tokens": 800,
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
                document: dict[str, Any] = json.load(response)
            message = document["choices"][0]["message"]["content"]
            extracted = _decode_json_object(message)
        except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise IntakeValidationError("the configured model could not produce a valid extraction draft") from exc
        if not isinstance(extracted, dict):
            raise IntakeValidationError("the configured model returned a non-object extraction draft")
        fields = {
            field: (str(extracted[field]).strip()[:2000] if extracted.get(field) not in (None, "") else None)
            for field in PROFILE_FIELDS
        }
        return ExtractionDraft(fields, self.model_id)
