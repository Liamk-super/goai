"""Run-aware OpenAI-compatible model egress gateway with zero automatic retries."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import create_engine, select

from launchscope_api.infrastructure.db.schema import (
    agentteams_task_delivery,
    model_invocation,
    physical_worker_execution_lease,
    run_execution_control,
    run_manifest,
    task,
)
from launchscope_api.infrastructure.db.session import DatabaseSettings, normalize_database_url, session_factory
from launchscope_api.modules.evaluation.execution_control import (
    ExecutionControlApplication,
    ModelAdmissionRejected,
    RunExecutionPausedError,
    admit_model_invocation,
    mark_model_invocation_submitted,
)
from launchscope_api.modules.evaluation.model_capability import delivery_token_digest

_MAX_BODY_BYTES = 2_000_000
_MAX_INTAKE_BODY_BYTES = 4_500_000


def model_request_timeout_seconds() -> float:
    value = float(os.getenv("LAUNCHSCOPE_MODEL_REQUEST_TIMEOUT_SECONDS", "3600"))
    if not 60 <= value <= 7200:
        raise ValueError("LAUNCHSCOPE_MODEL_REQUEST_TIMEOUT_SECONDS must be between 60 and 7200")
    return value


@dataclass(frozen=True, slots=True)
class DeliveryRoute:
    tenant_id: UUID
    run_id: UUID
    task_id: UUID
    delivery_id: UUID
    agent_code: str
    control_epoch: int
    dispatch_epoch: int


@dataclass(frozen=True, slots=True)
class SSEEvent:
    raw: bytes
    data: str
    terminal: bool


class SSEEventDecoder:
    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, chunk: bytes) -> list[SSEEvent]:
        self._buffer += chunk
        frames: list[SSEEvent] = []
        while True:
            lf = self._buffer.find(b"\n\n")
            crlf = self._buffer.find(b"\r\n\r\n")
            candidates = [(lf, 2), (crlf, 4)]
            candidates = [(position, length) for position, length in candidates if position >= 0]
            if not candidates:
                return frames
            position, separator_length = min(candidates, key=lambda value: value[0])
            raw = self._buffer[: position + separator_length]
            self._buffer = self._buffer[position + separator_length :]
            data_lines = []
            for line in raw.splitlines():
                if line == b"data":
                    data_lines.append("")
                elif line.startswith(b"data:"):
                    data_lines.append(line[5:].lstrip(b" ").decode("utf-8", errors="replace"))
            data = "\n".join(data_lines)
            frames.append(SSEEvent(raw=raw, data=data, terminal=data.strip() == "[DONE]"))

    def flush(self) -> list[SSEEvent]:
        if not self._buffer:
            return []
        raw = self._buffer
        self._buffer = b""
        data_lines = []
        for line in raw.splitlines():
            if line.startswith(b"data:"):
                data_lines.append(line[5:].lstrip(b" ").decode("utf-8", errors="replace"))
        data = "\n".join(data_lines)
        return [SSEEvent(raw=raw, data=data, terminal=data.strip() == "[DONE]")]


def model_egress_enforced() -> bool:
    return os.getenv("MODEL_EGRESS_GATE_ENFORCED", "true").strip().lower() in {"1", "true", "yes"}


def issue_intake_token(*, ttl_seconds: int | None = None) -> str:
    ttl = ttl_seconds if ttl_seconds is not None else int(
        os.getenv("LAUNCHSCOPE_MODEL_GATEWAY_CREDENTIAL_TTL_SECONDS", "43200")
    )
    if not 300 <= ttl <= 86_400:
        raise ValueError("intake gateway credential lifetime must be between 5 minutes and 24 hours")
    expires_at = int(time.time()) + ttl
    signed = f"intake.{expires_at}"
    signature = hmac.new(_gateway_secret(), signed.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"lsmg.intake.v1.{expires_at}.{signature}"


def verify_intake_token(token: str) -> None:
    parts = token.split(".")
    if len(parts) != 5 or parts[:3] != ["lsmg", "intake", "v1"]:
        raise ValueError("intake gateway credential is malformed")
    try:
        expires_at = int(parts[3])
    except ValueError as exc:
        raise ValueError("intake gateway credential expiry is malformed") from exc
    signed = f"intake.{expires_at}"
    expected = hmac.new(_gateway_secret(), signed.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(parts[4], expected):
        raise ValueError("intake gateway credential is invalid")
    now = int(time.time())
    if expires_at <= now:
        raise ValueError("intake gateway credential has expired")
    if expires_at > now + 86_400:
        raise ValueError("intake gateway credential lifetime is invalid")


def _gateway_secret() -> bytes:
    value = os.getenv("LAUNCHSCOPE_MODEL_GATEWAY_SECRET", "")
    if len(value) < 32:
        raise RuntimeError("LAUNCHSCOPE_MODEL_GATEWAY_SECRET must contain at least 32 characters")
    return value.encode("utf-8")


def _upstream() -> tuple[str, str]:
    base_url = (
        os.getenv("LAUNCHSCOPE_MODEL_UPSTREAM_BASE_URL")
        or os.getenv("AGENTTEAMS_MODEL_BASE_URL")
        or ""
    ).rstrip("/")
    api_key = os.getenv("LAUNCHSCOPE_MODEL_UPSTREAM_API_KEY") or ""
    parsed = urlparse(base_url)
    local = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "host.docker.internal"}
    if not base_url or not api_key or not (parsed.scheme == "https" or local):
        raise RuntimeError("model upstream must be configured as HTTPS or an explicit local endpoint")
    return f"{base_url}/chat/completions", api_key


def _sessions():
    settings = DatabaseSettings.from_env()
    engine = create_engine(normalize_database_url(settings.url), pool_pre_ping=True)
    return session_factory(engine)


async def _active_delivery_route(
    sessions: Any,
    credential_sha256: str,
    *,
    wait_seconds: float = 5.0,
) -> DeliveryRoute:
    deadline = time.monotonic() + wait_seconds
    while True:
        with sessions() as session, session.begin():
            lease = session.execute(
                select(physical_worker_execution_lease)
                .where(physical_worker_execution_lease.c.credential_sha256 == credential_sha256)
                .limit(1)
            ).mappings().one_or_none()
            if lease is None:
                raise RunExecutionPausedError("delivery model credential is unknown or revoked")
            expires_at = lease["credential_expires_at"]
            if expires_at is None or expires_at <= datetime.now(UTC):
                raise RunExecutionPausedError("delivery model credential has expired")
            if lease["state"] == "ACTIVE":
                route = session.execute(
                    select(
                        agentteams_task_delivery.c.status,
                        task.c.status.label("task_status"),
                        run_execution_control.c.state.label("control_state"),
                        run_execution_control.c.control_epoch,
                    )
                    .select_from(
                        agentteams_task_delivery.join(
                            task,
                            (task.c.tenant_id == agentteams_task_delivery.c.tenant_id)
                            & (task.c.id == agentteams_task_delivery.c.task_id),
                        ).join(
                            run_execution_control,
                            (run_execution_control.c.tenant_id == agentteams_task_delivery.c.tenant_id)
                            & (run_execution_control.c.run_id == agentteams_task_delivery.c.run_id),
                        )
                    )
                    .where(
                        agentteams_task_delivery.c.tenant_id == lease["tenant_id"],
                        agentteams_task_delivery.c.id == lease["delivery_id"],
                        agentteams_task_delivery.c.run_id == lease["run_id"],
                        agentteams_task_delivery.c.task_id == lease["task_id"],
                        agentteams_task_delivery.c.dispatch_epoch == lease["dispatch_epoch"],
                    )
                ).mappings().one_or_none()
                if (
                    route is None
                    or route["status"] != "DELIVERED"
                    or route["task_status"] != "RUNNING"
                    or route["control_state"] != "ACTIVE"
                    or int(route["control_epoch"]) != int(lease["control_epoch"])
                ):
                    raise RunExecutionPausedError("delivery model credential is not bound to active work")
                return DeliveryRoute(
                    tenant_id=UUID(str(lease["tenant_id"])),
                    run_id=UUID(str(lease["run_id"])),
                    task_id=UUID(str(lease["task_id"])),
                    delivery_id=UUID(str(lease["delivery_id"])),
                    agent_code=str(lease["agent_code"]),
                    control_epoch=int(lease["control_epoch"]),
                    dispatch_epoch=int(lease["dispatch_epoch"]),
                )
            if lease["state"] != "PREPARING":
                raise RunExecutionPausedError("delivery model credential is no longer active")
        if time.monotonic() >= deadline:
            raise RunExecutionPausedError("delivery model credential did not become active")
        await asyncio.sleep(0.1)


def _output_token_cap(document: dict[str, Any]) -> int:
    configured = int(os.getenv("LAUNCHSCOPE_MODEL_MAX_OUTPUT_TOKENS", "32768"))
    if not 1 <= configured <= 131_072:
        raise RuntimeError("LAUNCHSCOPE_MODEL_MAX_OUTPUT_TOKENS must be between 1 and 131072")
    requested = document.get("max_completion_tokens", document.get("max_tokens"))
    cap = min(configured, int(requested)) if requested is not None else configured
    if "max_completion_tokens" in document:
        document["max_completion_tokens"] = cap
    else:
        document["max_tokens"] = cap
    return cap


def _projected_input_tokens(document: Mapping[str, Any]) -> int:
    encoded = json.dumps(document.get("messages", []), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return max(1, (len(encoded) + 3) // 4)


def _budget_hold(projected_input: int, projected_output: int) -> Decimal:
    input_price = os.getenv("LAUNCHSCOPE_MODEL_INPUT_USD_PER_MILLION", "").strip()
    output_price = os.getenv("LAUNCHSCOPE_MODEL_OUTPUT_USD_PER_MILLION", "").strip()
    if not input_price or not output_price:
        return Decimal("0")
    return (
        Decimal(projected_input) * Decimal(input_price)
        + Decimal(projected_output) * Decimal(output_price)
    ) / Decimal(1_000_000)


def _usage(value: object) -> tuple[int | None, int | None]:
    if not isinstance(value, dict):
        return None, None
    prompt = value.get("prompt_tokens", value.get("input_tokens"))
    completion = value.get("completion_tokens", value.get("output_tokens"))
    return int(prompt) if prompt is not None else None, int(completion) if completion is not None else None


def _cancelled_stream_settlement(
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> tuple[str, str, str]:
    if prompt_tokens is not None and completion_tokens is not None:
        return (
            "SETTLED",
            "DELIVERY_UNKNOWN",
            "stream client disconnected before terminal delivery was confirmed",
        )
    return "SUBMISSION_UNKNOWN", "DELIVERY_UNKNOWN", "stream client disconnected before settlement"


def _upstream_rejection_error(status_code: int, payload: bytes) -> str:
    detail = "provider rejected the request"
    try:
        document = json.loads(payload[:32_768])
    except (UnicodeDecodeError, json.JSONDecodeError):
        document = None
    if isinstance(document, dict):
        error = document.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "").strip()
            message = str(error.get("message") or error.get("detail") or "").strip()
            detail = f"{code}: {message}".strip(": ") or detail
        elif isinstance(error, str) and error.strip():
            detail = error.strip()
        else:
            candidate = document.get("detail", document.get("message"))
            if isinstance(candidate, str) and candidate.strip():
                detail = candidate.strip()
    return f"HTTP {status_code} {' '.join(detail.split())}"[:1000]


def _settle(
    sessions: Any,
    invocation_id: UUID,
    *,
    status: str,
    request_id: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    delivery_status: str | None = None,
    terminal_seen_at: datetime | None = None,
    usage_received_at: datetime | None = None,
    failure_class: str | None = None,
    error: str | None = None,
) -> None:
    with sessions() as session, session.begin():
        cost = (
            _frozen_model_cost(session, invocation_id, prompt_tokens, completion_tokens)
            if status == "SETTLED"
            else None
        )
        ExecutionControlApplication.settle_invocation(
            session,
            invocation_id,
            status=status,
            upstream_request_id=request_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=cost,
            delivery_status=delivery_status,
            terminal_seen_at=terminal_seen_at,
            usage_received_at=usage_received_at,
            failure_class=failure_class,
            error=error,
        )


def _mark_delivery(
    sessions: Any,
    invocation_id: UUID,
    *,
    delivery_status: str,
    error: str | None = None,
) -> None:
    with sessions() as session, session.begin():
        ExecutionControlApplication.mark_invocation_delivery(
            session,
            invocation_id,
            delivery_status=delivery_status,
            error=error,
        )


def _frozen_model_cost(
    session: Any,
    invocation_id: UUID,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> Decimal | None:
    if prompt_tokens is None or completion_tokens is None:
        return None
    manifest = session.execute(
        select(run_manifest.c.frozen_config)
        .select_from(
            model_invocation.join(
                run_manifest,
                (run_manifest.c.tenant_id == model_invocation.c.tenant_id)
                & (run_manifest.c.run_id == model_invocation.c.run_id),
            )
        )
        .where(model_invocation.c.id == invocation_id)
    ).scalar_one_or_none()
    if manifest is None:
        return None
    pricing = manifest.get("model_pricing", {})
    if str(pricing.get("cost_mode") or "TOKEN_ONLY").upper() != "EXACT":
        return None
    input_price = pricing.get("input_usd_per_million_tokens")
    output_price = pricing.get("output_usd_per_million_tokens")
    if input_price is None or output_price is None:
        return None
    return (
        Decimal(prompt_tokens) * Decimal(str(input_price))
        + Decimal(completion_tokens) * Decimal(str(output_price))
    ) / Decimal(1_000_000)


def create_model_gateway() -> FastAPI:
    app = FastAPI(title="LaunchScope Model Egress Gateway", version="1.0.0")

    @app.get("/healthz")
    def health() -> dict[str, object]:
        return {"status": "ok", "egress_gate_enforced": model_egress_enforced()}

    @app.post("/v1/intake/chat/completions", response_model=None)
    async def intake_chat_completions(
        request: Request,
        authorization: str = Header(alias="Authorization"),
    ) -> JSONResponse:
        if not model_egress_enforced():
            raise HTTPException(status_code=503, detail="strict model egress gate is disabled")
        try:
            verify_intake_token(authorization.removeprefix("Bearer "))
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        body = await request.body()
        if not body or len(body) > _MAX_INTAKE_BODY_BYTES:
            raise HTTPException(status_code=413, detail="intake model request body is empty or exceeds 4.5 MB")
        try:
            document = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="intake model request body must be JSON") from exc
        if not isinstance(document, dict):
            raise HTTPException(status_code=400, detail="intake model request body must be an object")
        if document.get("stream"):
            raise HTTPException(status_code=400, detail="intake model requests must not stream")
        model = str(document.get("model") or "")
        configured_models = {
            value
            for value in (
                os.getenv("LAUNCHSCOPE_INTAKE_MODEL_ID") or os.getenv("AGENTTEAMS_MODEL_ID"),
                os.getenv("LAUNCHSCOPE_VISION_MODEL_ID"),
            )
            if value
        }
        if not configured_models:
            raise HTTPException(status_code=503, detail="the intake model is not configured")
        if model not in configured_models:
            raise HTTPException(status_code=403, detail="the requested model is not the configured intake model")
        _output_token_cap(document)
        try:
            upstream_url, upstream_key = _upstream()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="model upstream is unavailable") from exc
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(model_request_timeout_seconds(), connect=20.0)
            ) as client:
                response = await client.post(
                    upstream_url,
                    headers={"Authorization": f"Bearer {upstream_key}", "Content-Type": "application/json"},
                    json=document,
                )
        except httpx.RequestError:
            return JSONResponse(
                {"error": {"code": "SUBMISSION_UNKNOWN", "message": "intake model submission requires reconciliation"}},
                status_code=502,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="the intake model upstream returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail="the intake model upstream returned an invalid envelope")
        return JSONResponse(payload, status_code=response.status_code)

    @app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(
        request: Request,
        authorization: str = Header(alias="Authorization"),
        x_stainless_retry_count: str | None = Header(default=None, alias="x-stainless-retry-count"),
    ) -> JSONResponse | StreamingResponse:
        if not model_egress_enforced():
            raise HTTPException(status_code=503, detail="strict model egress gate is disabled")
        if x_stainless_retry_count is not None:
            try:
                retry_count = int(x_stainless_retry_count)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="SDK retry count header is invalid") from exc
            if retry_count > 0:
                raise HTTPException(status_code=409, detail="SDK_RETRY_PROHIBITED: automatic retry is disabled")
        bearer = authorization.removeprefix("Bearer ").strip()
        if not bearer.startswith("lsmg.v2."):
            raise HTTPException(status_code=423, detail="delivery-scoped model credential is required")
        try:
            sessions = _sessions()
            delivery_route = await _active_delivery_route(sessions, delivery_token_digest(bearer))
            agent_code = delivery_route.agent_code
            tenant_id = delivery_route.tenant_id
            run_id = delivery_route.run_id
            task_id = delivery_route.task_id
            control_epoch = delivery_route.control_epoch
        except (ValueError, RunExecutionPausedError) as exc:
            raise HTTPException(status_code=423, detail=str(exc)) from exc
        body = await request.body()
        if not body or len(body) > _MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="model request body is empty or exceeds 2 MB")
        try:
            document = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="model request body must be JSON") from exc
        if not isinstance(document, dict):
            raise HTTPException(status_code=400, detail="model request body must be an object")
        model = str(document.get("model") or "")
        if not model:
            raise HTTPException(status_code=400, detail="model is required")
        streaming = bool(document.get("stream"))
        if streaming:
            stream_options = document.get("stream_options") or {}
            if not isinstance(stream_options, dict):
                raise HTTPException(status_code=400, detail="stream_options must be an object")
            document["stream_options"] = {**stream_options, "include_usage": True}
        output_cap = _output_token_cap(document)
        projected_input = _projected_input_tokens(document)
        request_hash = hashlib.sha256(
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        try:
            with sessions() as session, session.begin():
                admission = admit_model_invocation(
                    session,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    task_id=task_id,
                    agent_code=agent_code,
                    expected_epoch=control_epoch,
                    model=model,
                    request_sha256=request_hash,
                    delivery_id=delivery_route.delivery_id if delivery_route is not None else None,
                    dispatch_epoch=delivery_route.dispatch_epoch if delivery_route is not None else None,
                    projected_input_tokens=projected_input,
                    projected_output_tokens=output_cap,
                    budget_hold_amount=_budget_hold(projected_input, output_cap),
                )
        except RunExecutionPausedError as exc:
            raise HTTPException(status_code=423, detail=str(exc)) from exc
        except ModelAdmissionRejected as exc:
            status_code = 429 if "LIMIT" in exc.code else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

        try:
            upstream_url, upstream_key = _upstream()
        except RuntimeError as exc:
            _settle(
                sessions,
                admission.invocation_id,
                status="REJECTED",
                request_id=None,
                prompt_tokens=0,
                completion_tokens=0,
                delivery_status="DELIVERED",
                failure_class="UPSTREAM_UNAVAILABLE_BEFORE_SUBMISSION",
                error=f"{type(exc).__name__}: {exc}"[:1000],
            )
            raise HTTPException(status_code=503, detail="model upstream is unavailable") from exc
        headers = {"Authorization": f"Bearer {upstream_key}", "Content-Type": "application/json"}
        try:
            with sessions() as session, session.begin():
                mark_model_invocation_submitted(session, admission.invocation_id, streaming=streaming)
        except RunExecutionPausedError as exc:
            _settle(
                sessions,
                admission.invocation_id,
                status="REJECTED",
                request_id=None,
                prompt_tokens=0,
                completion_tokens=0,
                delivery_status="DELIVERED",
                failure_class="PAUSED_BEFORE_SUBMISSION",
                error=str(exc),
            )
            raise HTTPException(status_code=423, detail=str(exc)) from exc

        if streaming:
            async def frames() -> AsyncIterator[bytes]:
                prompt_tokens = None
                completion_tokens = None
                request_id = None
                usage_received_at = None
                terminal_seen = False
                delivery_confirmed = False
                settlement_recorded = False
                decoder = SSEEventDecoder()

                def observe(event: SSEEvent) -> None:
                    nonlocal prompt_tokens, completion_tokens, request_id, usage_received_at
                    if not event.data or event.terminal:
                        return
                    try:
                        item = json.loads(event.data)
                    except json.JSONDecodeError:
                        return
                    if not isinstance(item, dict):
                        return
                    request_id = str(item.get("id") or request_id or "") or None
                    observed_prompt, observed_completion = _usage(item.get("usage"))
                    if observed_prompt is not None:
                        prompt_tokens = observed_prompt
                    if observed_completion is not None:
                        completion_tokens = observed_completion
                    if observed_prompt is not None and observed_completion is not None:
                        usage_received_at = datetime.now(UTC)

                try:
                    async with (
                        httpx.AsyncClient(
                            timeout=httpx.Timeout(model_request_timeout_seconds(), connect=20.0)
                        ) as client,
                        client.stream("POST", upstream_url, headers=headers, json=document) as response,
                    ):
                        request_id = response.headers.get("x-request-id")
                        if response.status_code >= 400:
                            payload = await response.aread()
                            _settle(
                                sessions,
                                admission.invocation_id,
                                status="REJECTED",
                                request_id=request_id,
                                prompt_tokens=0,
                                completion_tokens=0,
                                delivery_status="DELIVERED",
                                failure_class="UPSTREAM_REJECTED",
                                error=_upstream_rejection_error(response.status_code, payload),
                            )
                            yield payload
                            return
                        async for chunk in response.aiter_bytes():
                            for event in decoder.feed(chunk):
                                observe(event)
                                if not event.terminal:
                                    yield event.raw
                                    continue
                                terminal_seen = True
                                terminal_time = datetime.now(UTC)
                                if prompt_tokens is None or completion_tokens is None:
                                    _settle(
                                        sessions,
                                        admission.invocation_id,
                                        status="SUBMISSION_UNKNOWN",
                                        request_id=request_id,
                                        prompt_tokens=prompt_tokens,
                                        completion_tokens=completion_tokens,
                                        delivery_status="DELIVERY_UNKNOWN",
                                        terminal_seen_at=terminal_time,
                                        usage_received_at=usage_received_at,
                                        failure_class="USAGE_UNKNOWN",
                                        error="provider terminal event lacked an exact usage receipt",
                                    )
                                    settlement_recorded = True
                                    return
                                _settle(
                                    sessions,
                                    admission.invocation_id,
                                    status="SETTLED",
                                    request_id=request_id,
                                    prompt_tokens=prompt_tokens,
                                    completion_tokens=completion_tokens,
                                    delivery_status="TERMINAL_SEEN",
                                    terminal_seen_at=terminal_time,
                                    usage_received_at=usage_received_at,
                                )
                                settlement_recorded = True
                                yield event.raw
                                _mark_delivery(
                                    sessions,
                                    admission.invocation_id,
                                    delivery_status="DELIVERED",
                                )
                                delivery_confirmed = True
                                return
                        for event in decoder.flush():
                            observe(event)
                            if not event.terminal:
                                yield event.raw
                                continue
                            terminal_seen = True
                            terminal_time = datetime.now(UTC)
                            if prompt_tokens is None or completion_tokens is None:
                                _settle(
                                    sessions,
                                    admission.invocation_id,
                                    status="SUBMISSION_UNKNOWN",
                                    request_id=request_id,
                                    prompt_tokens=prompt_tokens,
                                    completion_tokens=completion_tokens,
                                    delivery_status="DELIVERY_UNKNOWN",
                                    terminal_seen_at=terminal_time,
                                    usage_received_at=usage_received_at,
                                    failure_class="USAGE_UNKNOWN",
                                    error="provider terminal event lacked an exact usage receipt",
                                )
                                settlement_recorded = True
                                return
                            _settle(
                                sessions,
                                admission.invocation_id,
                                status="SETTLED",
                                request_id=request_id,
                                prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens,
                                delivery_status="TERMINAL_SEEN",
                                terminal_seen_at=terminal_time,
                                usage_received_at=usage_received_at,
                            )
                            settlement_recorded = True
                            yield event.raw
                            _mark_delivery(
                                sessions,
                                admission.invocation_id,
                                delivery_status="DELIVERED",
                            )
                            delivery_confirmed = True
                            return
                    if prompt_tokens is not None and completion_tokens is not None:
                        _settle(
                            sessions,
                            admission.invocation_id,
                            status="SETTLED",
                            request_id=request_id,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            delivery_status="DELIVERY_UNKNOWN",
                            usage_received_at=usage_received_at,
                            failure_class="MODEL_DELIVERY_UNKNOWN",
                            error="provider stream ended without a terminal event",
                        )
                    else:
                        _settle(
                            sessions,
                            admission.invocation_id,
                            status="SUBMISSION_UNKNOWN",
                            request_id=request_id,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            delivery_status="DELIVERY_UNKNOWN",
                            usage_received_at=usage_received_at,
                            failure_class="SUBMISSION_UNKNOWN",
                            error="provider stream ended without terminal usage settlement",
                        )
                    settlement_recorded = True
                except asyncio.CancelledError as exc:
                    if terminal_seen and settlement_recorded and not delivery_confirmed:
                        _mark_delivery(
                            sessions,
                            admission.invocation_id,
                            delivery_status="DELIVERY_UNKNOWN",
                            error="stream client disconnected before terminal delivery confirmation",
                        )
                    elif not settlement_recorded:
                        status, delivery_status, error = _cancelled_stream_settlement(
                            prompt_tokens, completion_tokens
                        )
                        _settle(
                            sessions,
                            admission.invocation_id,
                            status=status,
                            request_id=request_id,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            delivery_status=delivery_status,
                            usage_received_at=usage_received_at,
                            failure_class=(
                                "MODEL_DELIVERY_UNKNOWN"
                                if status == "SETTLED"
                                else "SUBMISSION_UNKNOWN"
                            ),
                            error=error,
                        )
                    raise exc
                except Exception as exc:
                    if not settlement_recorded:
                        status = (
                            "SETTLED"
                            if prompt_tokens is not None and completion_tokens is not None
                            else "SUBMISSION_UNKNOWN"
                        )
                        _settle(
                            sessions,
                            admission.invocation_id,
                            status=status,
                            request_id=request_id,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            delivery_status="DELIVERY_UNKNOWN",
                            usage_received_at=usage_received_at,
                            failure_class=(
                                "MODEL_DELIVERY_UNKNOWN"
                                if status == "SETTLED"
                                else "SUBMISSION_UNKNOWN"
                            ),
                            error=f"{type(exc).__name__}: {exc}"[:1000],
                        )
                    return

            return StreamingResponse(frames(), media_type="text/event-stream")

        request_id = None
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(model_request_timeout_seconds(), connect=20.0)
            ) as client:
                response = await client.post(upstream_url, headers=headers, json=document)
            request_id = response.headers.get("x-request-id")
            if response.status_code >= 400:
                try:
                    rejected_payload = response.json()
                except ValueError:
                    rejected_payload = {
                        "error": {"code": "UPSTREAM_REJECTED", "message": "provider rejected the request"}
                    }
                payload = rejected_payload if isinstance(rejected_payload, dict) else {
                    "error": {"code": "UPSTREAM_REJECTED", "message": "provider rejected the request"}
                }
                prompt_tokens, completion_tokens = _usage(payload.get("usage"))
                _settle(
                    sessions,
                    admission.invocation_id,
                    status="REJECTED",
                    request_id=str(payload.get("id") or request_id or "") or None,
                    prompt_tokens=prompt_tokens or 0,
                    completion_tokens=completion_tokens or 0,
                    delivery_status="DELIVERED",
                    usage_received_at=datetime.now(UTC) if prompt_tokens is not None else None,
                    failure_class="UPSTREAM_REJECTED",
                    error=_upstream_rejection_error(response.status_code, response.content),
                )
                return JSONResponse(payload, status_code=response.status_code)
            payload = response.json()
            prompt_tokens, completion_tokens = _usage(payload.get("usage"))
            if prompt_tokens is None or completion_tokens is None:
                raise RuntimeError("provider response lacks an exact usage receipt")
            _settle(
                sessions,
                admission.invocation_id,
                status="SETTLED",
                request_id=str(payload.get("id") or request_id or "") or None,
                prompt_tokens=prompt_tokens or 0,
                completion_tokens=completion_tokens or 0,
                delivery_status="DELIVERED",
                usage_received_at=datetime.now(UTC) if prompt_tokens is not None else None,
            )
            return JSONResponse(payload, status_code=response.status_code)
        except Exception as exc:
            _settle(
                sessions,
                admission.invocation_id,
                status="SUBMISSION_UNKNOWN",
                request_id=request_id,
                prompt_tokens=None,
                completion_tokens=None,
                delivery_status="DELIVERY_UNKNOWN",
                failure_class="SUBMISSION_UNKNOWN",
                error=f"{type(exc).__name__}: {exc}"[:1000],
            )
            return JSONResponse(
                {"error": {"code": "SUBMISSION_UNKNOWN", "message": "model submission requires reconciliation"}},
                status_code=502,
            )

    return app


app = create_model_gateway()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("issue-intake-token",))
    args = parser.parse_args()
    if args.command == "issue-intake-token":
        print(issue_intake_token())


if __name__ == "__main__":
    main()
