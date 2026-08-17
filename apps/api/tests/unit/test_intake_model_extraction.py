from __future__ import annotations

import io
import json
from urllib.error import HTTPError, URLError
from uuid import uuid4

import pytest

from launchscope_api.modules.evaluation.intake_application import IntakeValidationError
from launchscope_api.modules.identity_tenant.application import Actor
from launchscope_api.modules.project_dossier import api as dossier_api
from launchscope_api.modules.project_dossier.model_extraction import ExtractionDraft, IntakeModelExtractor


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_intake_endpoint_returns_the_extraction_after_audited_logging(monkeypatch) -> None:
    monkeypatch.setattr(dossier_api, "material_routing_enabled", lambda: False)
    monkeypatch.setattr(
        dossier_api.IntakeModelExtractor,
        "extract",
        lambda *_args, **_kwargs: ExtractionDraft(
            {"problem": "Slow onboarding", "timing": "Launch before 30 September"},
            "intake-model",
        ),
    )

    result = dossier_api.extract_intake(
        dossier_api.ExtractIntakeRequest(raw_content="Launch before 30 September", allow_external_processing=True),
        Actor(tenant_id=uuid4(), actor_id="browser-tester"),
        object(),
    )

    assert result["extracted_fields"]["timing"] == "Launch before 30 September"
    assert result["model_id"] == "intake-model"


def test_model_extraction_is_draft_only_and_preserves_unknowns(monkeypatch) -> None:
    captured_request = None
    captured_timeout = None
    payload = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "problem": "Slow onboarding",
                    "payer": None,
                    "team": "Two full-time founders",
                    "timing": "Launch before 30 September",
                })
            }
        }]
    }

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
    assert result.fields["team"] == "Two full-time founders"
    assert result.fields["timing"] == "Launch before 30 September"
    assert "target_user" in result.missing_fields
    assert captured_request is not None
    assert captured_timeout == 120
    request_payload = json.loads(captured_request.data)
    assert request_payload["max_tokens"] == 32768
    assert "team" in request_payload["messages"][1]["content"]
    assert "timing" in request_payload["messages"][1]["content"]


def test_model_extraction_distinguishes_provider_http_failure(monkeypatch) -> None:
    def respond(*_args, **_kwargs):
        raise HTTPError("https://model.example/v1/chat/completions", 429, "rate limited", {}, None)

    monkeypatch.setattr("launchscope_api.modules.project_dossier.model_extraction.urlopen", respond)

    with pytest.raises(IntakeValidationError, match="provider returned HTTP 429"):
        IntakeModelExtractor(base_url="https://model.example/v1", api_key="secret", model_id="qwen").extract(
            "material", allow_external_processing=True
        )


def test_model_extraction_distinguishes_provider_network_failure(monkeypatch) -> None:
    def respond(*_args, **_kwargs):
        raise URLError("connection reset")

    monkeypatch.setattr("launchscope_api.modules.project_dossier.model_extraction.urlopen", respond)

    with pytest.raises(IntakeValidationError, match="request failed before a usable response"):
        IntakeModelExtractor(base_url="https://model.example/v1", api_key="secret", model_id="qwen").extract(
            "material", allow_external_processing=True
        )


def test_model_extraction_identifies_truncated_provider_response(monkeypatch) -> None:
    payload = {"choices": [{"finish_reason": "length", "message": {"content": '{"problem":"incomplete'}}]}
    monkeypatch.setattr(
        "launchscope_api.modules.project_dossier.model_extraction.urlopen",
        lambda *_args, **_kwargs: _Response(json.dumps(payload).encode()),
    )

    with pytest.raises(IntakeValidationError, match="response was truncated"):
        IntakeModelExtractor(base_url="https://model.example/v1", api_key="secret", model_id="qwen").extract(
            "material", allow_external_processing=True
        )


def test_model_extraction_timeout_is_configurable_and_bounded(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_INTAKE_MODEL_TIMEOUT_SECONDS", "999")
    assert IntakeModelExtractor._timeout_seconds() == 180
    monkeypatch.setenv("LAUNCHSCOPE_INTAKE_MODEL_TIMEOUT_SECONDS", "invalid")
    assert IntakeModelExtractor._timeout_seconds() == 120


def test_model_extraction_prefers_dedicated_intake_model_configuration(monkeypatch) -> None:
    monkeypatch.setenv("AGENTTEAMS_MODEL_BASE_URL", "https://agent.example/v1")
    monkeypatch.setenv("AGENTTEAMS_MODEL_API_KEY", "agent-key")
    monkeypatch.setenv("AGENTTEAMS_MODEL_ID", "kimi-k3")
    monkeypatch.setenv("LAUNCHSCOPE_INTAKE_MODEL_BASE_URL", "https://intake.example/v1")
    monkeypatch.setenv("LAUNCHSCOPE_INTAKE_MODEL_API_KEY", "intake-key")
    monkeypatch.setenv("LAUNCHSCOPE_INTAKE_MODEL_ID", "deepseek-v4-flash-0731")

    extractor = IntakeModelExtractor()

    assert extractor.base_url == "https://intake.example/v1"
    assert extractor.api_key == "intake-key"
    assert extractor.model_id == "deepseek-v4-flash-0731"


def test_model_extraction_accepts_the_loopback_intake_gateway(monkeypatch) -> None:
    payload = {"choices": [{"message": {"content": json.dumps({"problem": "Slow onboarding"})}}]}
    captured_request = None

    def respond(request, **_kwargs):
        nonlocal captured_request
        captured_request = request
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr("launchscope_api.modules.project_dossier.model_extraction.urlopen", respond)

    result = IntakeModelExtractor(
        base_url="http://127.0.0.1:8092/v1/intake",
        api_key="signed-intake-capability",
        model_id="qwen",
    ).extract("A product that fixes slow onboarding", allow_external_processing=True)

    assert result.fields["problem"] == "Slow onboarding"
    assert captured_request is not None
    assert captured_request.full_url == "http://127.0.0.1:8092/v1/intake/chat/completions"


def test_model_extraction_rejects_plain_http_outside_the_loopback_gateway() -> None:
    with pytest.raises(IntakeValidationError, match="provider is not safely configured"):
        IntakeModelExtractor(
            base_url="http://model.example/v1",
            api_key="secret",
            model_id="qwen",
        ).extract("material", allow_external_processing=True)


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


def test_requirement_extraction_reserves_enough_output_for_reasoning_models(monkeypatch) -> None:
    captured_request = None
    payload = {"choices": [{"message": {"content": '{"evaluation_mode":"FULL_POTENTIAL"}'}}]}

    def respond(request, *, timeout):
        nonlocal captured_request
        captured_request = request
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr("launchscope_api.modules.project_dossier.model_extraction.urlopen", respond)

    extractor = IntakeModelExtractor(base_url="https://model.example/v1", api_key="secret", model_id="qwen")
    result = extractor.extract_requirement("Evaluate the product", allow_external_processing=True)

    assert result["evaluation_mode"] == "FULL_POTENTIAL"
    assert captured_request is not None
    request_payload = json.loads(captured_request.data)
    assert request_payload["max_tokens"] == 32768
    prompt = request_payload["messages"][1]["content"]
    assert "target_user, region, validation_goal, stage" in prompt
    assert "confidence_overall must be a JSON number from 0 to 1" in prompt
    assert "material must be a JSON boolean" in prompt
    assert "INITIAL, SUPPLEMENT, or REQUIREMENT_CHANGE" in prompt


def test_requirement_extraction_identifies_truncated_provider_response(monkeypatch) -> None:
    payload = {"choices": [{"finish_reason": "length", "message": {"content": '{"normalized_goal":"incomplete'}}]}
    monkeypatch.setattr(
        "launchscope_api.modules.project_dossier.model_extraction.urlopen",
        lambda *_args, **_kwargs: _Response(json.dumps(payload).encode()),
    )

    extractor = IntakeModelExtractor(base_url="https://model.example/v1", api_key="secret", model_id="qwen")
    with pytest.raises(IntakeValidationError, match="truncated before a complete RequirementBrief"):
        extractor.extract_requirement("Evaluate the product", allow_external_processing=True)


def test_validation_task_generation_requires_observable_complete_tasks(monkeypatch) -> None:
    payload = {
        "choices": [{"message": {"content": json.dumps({"tasks": [{
            "task_key": "Generate Product Asset",
            "description": "From a signed-in workspace, upload a safe generic product image and start generation.",
            "expected_observable_outcome": "The completed image appears in history and the asset library.",
            "max_steps": 7,
            "rationale": "Exercises the core material-to-generation loop.",
            "source_hints": ["计划书.pdf / 第 10 页", "https://creatrades.com"],
        }]})}}]
    }
    monkeypatch.setattr(
        "launchscope_api.modules.project_dossier.model_extraction.urlopen",
        lambda *_args, **_kwargs: _Response(json.dumps(payload).encode()),
    )
    result = IntakeModelExtractor(
        base_url="https://model.example/v1", api_key="secret", model_id="qwen"
    ).generate_validation_tasks("CreaTrades material", allow_external_processing=True)
    assert result["tasks"][0]["task_key"] == "generate_product_asset"
    assert result["tasks"][0]["max_steps"] == 7
    assert result["tasks"][0]["source_hints"][0] == "计划书.pdf / 第 10 页"


def test_validation_task_generation_binds_output_language(monkeypatch) -> None:
    captured: list[dict[str, object]] = []
    payload = {
        "choices": [{"message": {"content": json.dumps({"tasks": [{
            "task_key": "inspect_product",
            "description": "Inspect the product.",
            "expected_observable_outcome": "The product page is visible.",
            "max_steps": 3,
            "rationale": "Checks the product.",
            "source_hints": ["https://example.test"],
        }]})}}]
    }

    def respond(request, *, timeout):
        captured.append(json.loads(request.data))
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr("launchscope_api.modules.project_dossier.model_extraction.urlopen", respond)
    extractor = IntakeModelExtractor(base_url="https://model.example/v1", api_key="secret", model_id="qwen")
    extractor.generate_validation_tasks("中文材料", allow_external_processing=True, locale="zh-CN")
    extractor.generate_validation_tasks("English material", allow_external_processing=True, locale="en")

    zh_prompt = captured[0]["messages"][0]["content"]
    en_prompt = captured[1]["messages"][0]["content"]
    assert "Write description, expected_observable_outcome, and rationale in natural Simplified Chinese" in zh_prompt
    assert "Write description, expected_observable_outcome, and rationale in natural English" in en_prompt


def test_visual_page_analysis_preserves_table_numbers_and_rotation(monkeypatch) -> None:
    payload = {
        "choices": [{"message": {"content": json.dumps({
            "recognition_type": "SCAN",
            "summary": (
                "A rotated business licence scan; barcode *443756116* and social credit code "
                "91441900MADEMO1234 are visible."
            ),
            "rotation_degrees": 90,
            "confidence": 0.91,
            "table": {
                "title": "Evidence table",
                "headers": [f"header-{index}" for index in range(20)],
                "rows": [
                    [
                        "owner@example.test" if row == 0 and column == 0 else f"cell-{row}-{column}"
                        for column in range(20)
                    ]
                    for row in range(30)
                ],
            },
        })}}]
    }
    captured = None

    def respond(request, *, timeout):
        nonlocal captured
        captured = json.loads(request.data)
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr("launchscope_api.modules.project_dossier.model_extraction.urlopen", respond)
    result = IntakeModelExtractor(
        base_url="https://model.example/v1", api_key="secret", model_id="qwen-vl"
    ).analyze_visual_page(
        file_name="report.pdf",
        page_number=10,
        image_data_url="data:image/jpeg;base64," + "a" * 200,
        text_hint="",
        allow_external_processing=True,
        local_table_detected=True,
    )
    assert result["recognition_type"] == "SCAN"
    assert result["rotation_degrees"] == 90
    assert captured["max_tokens"] == 8192
    assert captured["messages"][1]["content"][1]["type"] == "image_url"
    assert "inspect it at all four orientations" in captured["messages"][1]["content"][0]["text"]
    assert "name the most decision-relevant numbers or percentages" in captured["messages"][1]["content"][0]["text"]
    assert "Set table to null" in captured["messages"][1]["content"][0]["text"]
    assert len(result["table"]["headers"]) == 12
    assert len(result["table"]["rows"]) == 20
    assert all(len(row) == 12 for row in result["table"]["rows"])
    assert "443756116" not in result["summary"]
    assert "91441900MADEMO1234" not in result["summary"]
    assert result["summary"].count("[sensitive identifier omitted]") == 2
    assert result["table"]["rows"][0][0] == "[sensitive identifier omitted]"
