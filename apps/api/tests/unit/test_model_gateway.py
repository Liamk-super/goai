from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from launchscope_api.model_gateway import (
    SSEEventDecoder,
    _cancelled_stream_settlement,
    _upstream_rejection_error,
    create_model_gateway,
    issue_intake_token,
    model_request_timeout_seconds,
    verify_intake_token,
)
from launchscope_api.modules.evaluation.execution_control import RunExecutionPausedError
from launchscope_api.modules.evaluation.model_capability import (
    delivery_scoped_tokens_enabled,
    delivery_token_digest,
    issue_delivery_capability,
    model_usage_ledger_mode,
)


def test_model_request_timeout_defaults_to_one_hour_and_is_bounded(monkeypatch) -> None:
    monkeypatch.delenv("LAUNCHSCOPE_MODEL_REQUEST_TIMEOUT_SECONDS", raising=False)
    assert model_request_timeout_seconds() == 3600.0

    monkeypatch.setenv("LAUNCHSCOPE_MODEL_REQUEST_TIMEOUT_SECONDS", "5400")
    assert model_request_timeout_seconds() == 5400.0

    monkeypatch.setenv("LAUNCHSCOPE_MODEL_REQUEST_TIMEOUT_SECONDS", "30")
    with pytest.raises(ValueError, match="between 60 and 7200"):
        model_request_timeout_seconds()


def test_delivery_scoped_accounting_is_the_only_default_for_new_runs(monkeypatch) -> None:
    monkeypatch.delenv("DELIVERY_SCOPED_MODEL_TOKEN_ENABLED", raising=False)
    monkeypatch.delenv("MODEL_USAGE_LEDGER_MODE", raising=False)

    assert delivery_scoped_tokens_enabled() is True
    assert model_usage_ledger_mode() == "GATEWAY_DELIVERY"

    monkeypatch.setenv("MODEL_USAGE_LEDGER_MODE", "COPAW_TASK_DELTA")
    with pytest.raises(RuntimeError, match="must be GATEWAY_DELIVERY"):
        model_usage_ledger_mode()


def test_intake_gateway_token_is_scoped_and_tamper_evident(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_MODEL_GATEWAY_SECRET", "model-gateway-unit-secret-with-entropy")
    monkeypatch.setattr("launchscope_api.model_gateway.time.time", lambda: 1_000)
    token = issue_intake_token()

    verify_intake_token(token)
    with pytest.raises(ValueError, match="invalid"):
        verify_intake_token(f"{token}0")


@pytest.mark.parametrize("model", ["qwen-intake", "qwen-vision"])
def test_intake_gateway_forwards_one_non_streaming_request(monkeypatch, model: str) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_MODEL_GATEWAY_SECRET", "model-gateway-unit-secret-with-entropy")
    monkeypatch.setenv("MODEL_EGRESS_GATE_ENFORCED", "true")
    monkeypatch.setenv("LAUNCHSCOPE_INTAKE_MODEL_ID", "qwen-intake")
    monkeypatch.setenv("LAUNCHSCOPE_VISION_MODEL_ID", "qwen-vision")
    monkeypatch.setenv("LAUNCHSCOPE_MODEL_UPSTREAM_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("LAUNCHSCOPE_MODEL_UPSTREAM_API_KEY", "upstream-secret")
    captured: dict[str, object] = {}

    class FakeAsyncClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, url, *, headers, json):
            captured.update(url=url, headers=headers, json=json)
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "id": "intake-1",
                    "choices": [{"message": {"content": '{"problem":"Slow onboarding"}'}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                },
            )

    monkeypatch.setattr("launchscope_api.model_gateway.httpx.AsyncClient", FakeAsyncClient)
    token = issue_intake_token()
    response = TestClient(create_model_gateway()).post(
        "/v1/intake/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": model, "messages": [{"role": "user", "content": "material"}]},
    )

    assert response.status_code == 200
    assert response.json()["id"] == "intake-1"
    assert captured["url"] == "https://model.example/v1/chat/completions"
    assert captured["headers"] == {
        "Authorization": "Bearer upstream-secret",
        "Content-Type": "application/json",
    }


def test_delivery_gateway_token_is_opaque_256_bit_and_only_its_digest_is_persistable() -> None:
    capability = issue_delivery_capability(ttl_seconds=600)

    assert capability.token.startswith("lsmg.v2.")
    assert delivery_token_digest(capability.token) == capability.sha256
    assert capability.token not in capability.sha256

    try:
        delivery_token_digest("lsmg.v2.dG9vLXNob3J0")
    except ValueError as exc:
        assert "256 bits" in str(exc)
    else:
        raise AssertionError("short delivery capability was accepted")


def test_cancelled_stream_is_settled_when_exact_usage_was_already_received() -> None:
    assert _cancelled_stream_settlement(123, 45) == (
        "SETTLED",
        "DELIVERY_UNKNOWN",
        "stream client disconnected before terminal delivery was confirmed",
    )
    assert _cancelled_stream_settlement(123, None) == (
        "SUBMISSION_UNKNOWN",
        "DELIVERY_UNKNOWN",
        "stream client disconnected before settlement",
    )


def test_upstream_rejection_error_keeps_bounded_status_code_and_provider_detail() -> None:
    assert _upstream_rejection_error(
        429,
        b'{"error":{"code":"rate_limit","message":"please slow down"}}',
    ) == "HTTP 429 rate_limit: please slow down"
    assert _upstream_rejection_error(502, b"not-json") == "HTTP 502 provider rejected the request"


def test_sse_decoder_preserves_split_frames_and_detects_terminal_event() -> None:
    decoder = SSEEventDecoder()

    assert decoder.feed(b'data: {"id":"one","usage":') == []
    frames = decoder.feed(b'{"prompt_tokens":2,"completion_tokens":3}}\r\n\r\ndata: [DO')
    assert len(frames) == 1
    assert frames[0].data.startswith('{"id":"one"')
    assert frames[0].terminal is False

    terminal = decoder.feed(b'NE]\n\n')
    assert len(terminal) == 1
    assert terminal[0].terminal is True
    assert terminal[0].raw == b"data: [DONE]\n\n"


def test_sse_decoder_joins_multiline_data_according_to_sse_framing() -> None:
    decoder = SSEEventDecoder()
    frames = decoder.feed(b"event: message\ndata: first\ndata: second\n\n")

    assert len(frames) == 1
    assert frames[0].data == "first\nsecond"


def test_generic_worker_credential_is_rejected_before_any_session_or_upstream_client_is_opened(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_EGRESS_GATE_ENFORCED", "true")
    monkeypatch.setenv("DELIVERY_SCOPED_MODEL_TOKEN_ENABLED", "false")
    opened = False

    def forbidden_sessions():
        raise AssertionError("a generic Worker credential must not query Run state")

    def forbidden_client(*_args, **_kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("upstream client must not open for a generic Worker credential")

    monkeypatch.setattr("launchscope_api.model_gateway._sessions", forbidden_sessions)
    monkeypatch.setattr("launchscope_api.model_gateway.httpx.AsyncClient", forbidden_client)

    response = TestClient(create_model_gateway()).post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer lsmg.v1.product-engineering.9999999999.legacy"},
        json={"model": "qwen3.8-max", "messages": [{"role": "user", "content": "must not leave"}]},
    )

    assert response.status_code == 423
    assert opened is False


def test_paused_delivery_is_rejected_before_any_upstream_client_is_opened(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_EGRESS_GATE_ENFORCED", "true")
    monkeypatch.setattr("launchscope_api.model_gateway._sessions", lambda: object())

    async def paused_delivery(*_args, **_kwargs):
        raise RunExecutionPausedError("RUN_PAUSED")

    opened = False

    def forbidden_client(*_args, **_kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("upstream client must not open for a paused delivery")

    monkeypatch.setattr("launchscope_api.model_gateway._active_delivery_route", paused_delivery)
    monkeypatch.setattr("launchscope_api.model_gateway.httpx.AsyncClient", forbidden_client)
    token = issue_delivery_capability().token

    response = TestClient(create_model_gateway()).post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "qwen3.8-max", "messages": [{"role": "user", "content": "must not leave"}]},
    )

    assert response.status_code == 423
    assert opened is False
