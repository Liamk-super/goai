from __future__ import annotations

import io
import json

import pytest

from launchscope_api.modules.evaluation.intake_application import IntakeValidationError
from launchscope_api.modules.project_dossier.model_extraction import IntakeModelExtractor


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_model_extraction_is_draft_only_and_preserves_unknowns(monkeypatch) -> None:
    captured_request = None
    captured_timeout = None
    payload = {"choices": [{"message": {"content": json.dumps({"problem": "Slow onboarding", "payer": None})}}]}

    def respond(request, *, timeout):
        nonlocal captured_request, captured_timeout
        captured_request = request
        captured_timeout = timeout
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr(
        "launchscope_api.modules.project_dossier.model_extraction.urlopen",
        respond,
    )
    result = IntakeModelExtractor(base_url="https://model.example/v1", api_key="secret", model_id="qwen").extract(
        "A product that fixes slow onboarding", allow_external_processing=True
    )
    assert result.fields["problem"] == "Slow onboarding"
    assert result.fields["payer"] is None
    assert "target_user" in result.missing_fields
    assert captured_request is not None
    assert captured_timeout == 120
    assert "response_format" not in json.loads(captured_request.data)


def test_model_extraction_timeout_is_configurable_and_bounded(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_INTAKE_MODEL_TIMEOUT_SECONDS", "999")
    assert IntakeModelExtractor._timeout_seconds() == 180
    monkeypatch.setenv("LAUNCHSCOPE_INTAKE_MODEL_TIMEOUT_SECONDS", "invalid")
    assert IntakeModelExtractor._timeout_seconds() == 120


@pytest.mark.parametrize(
    "content",
    (
        '```json\n{"problem":"Slow onboarding"}\n```',
        '提取结果如下：\n{"problem":"Slow onboarding"}\n请核对。',
    ),
)
def test_model_extraction_accepts_a_json_object_wrapped_in_provider_text(monkeypatch, content: str) -> None:
    payload = {"choices": [{"message": {"content": content}}]}
    monkeypatch.setattr(
        "launchscope_api.modules.project_dossier.model_extraction.urlopen",
        lambda *_args, **_kwargs: _Response(json.dumps(payload).encode()),
    )
    result = IntakeModelExtractor(base_url="https://model.example/v1", api_key="secret", model_id="qwen").extract(
        "A product that fixes slow onboarding", allow_external_processing=True
    )
    assert result.fields["problem"] == "Slow onboarding"


def test_model_extraction_requires_explicit_external_processing_consent() -> None:
    with pytest.raises(IntakeValidationError, match="explicit user confirmation"):
        IntakeModelExtractor(base_url="https://model.example/v1", api_key="secret", model_id="qwen").extract(
            "material", allow_external_processing=False
        )
