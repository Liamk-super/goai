"""Official MCP SDK Streamable-HTTP servers for LaunchScope read-only tools."""

from __future__ import annotations

import hmac
import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from uuid import UUID

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from launchscope_domain.value_objects import TenantScope

from .infrastructure.db.schema import task
from .infrastructure.db.session import (
    DatabaseSettings,
    create_database_engine,
    session_factory,
    set_context_on_session,
)
from .infrastructure.object_store import S3QuarantineObjectStore
from .modules.evaluation.execution_control import assert_run_active
from .modules.evidence.mcp_application import McpEvidenceApplication, configured_browser_domains
from .modules.evidence.task_capability import verify_task_capability
from .modules.identity_tenant.application import Actor
from .modules.user_validation.application import UserValidationApplication
from .modules.user_validation.runner import NodeUserValidationRunner


@dataclass(frozen=True)
class _Routing:
    tenant_id: UUID
    actor_id: str
    run_id: UUID
    task_id: UUID
    agent_code: str | None = None


_routing_context: ContextVar[_Routing | None] = ContextVar("launchscope_mcp_routing", default=None)


@lru_cache(maxsize=1)
def application() -> McpEvidenceApplication:
    settings = DatabaseSettings.from_env()
    engine = create_database_engine(
        settings.url,
        application_role=os.getenv("LAUNCHSCOPE_DB_ROLE", "launchscope_runtime"),
    )
    return McpEvidenceApplication(
        session_factory(engine), S3QuarantineObjectStore.from_env(),
        allowed_browser_domains=configured_browser_domains(),
    )


@lru_cache(maxsize=1)
def user_validation_application() -> UserValidationApplication:
    settings = DatabaseSettings.from_env()
    engine = create_database_engine(
        settings.url,
        application_role=os.getenv("LAUNCHSCOPE_DB_ROLE", "launchscope_runtime"),
    )
    return UserValidationApplication(
        session_factory(engine), S3QuarantineObjectStore.from_env(), NodeUserValidationRunner()
    )


def _routing(context_token: str = "") -> _Routing:
    value = _routing_context.get()
    if value is not None:
        _assert_route_active(value, expected_epoch=None)
        return value
    if os.getenv("LAUNCHSCOPE_DEMO_MODE", "").lower() == "true":
        capability = verify_task_capability(context_token)
        route = _Routing(
            capability.tenant_id,
            f"agent:{capability.agent_code}",
            capability.run_id,
            capability.task_id,
            capability.agent_code,
        )
        _assert_route_active(route, expected_epoch=capability.control_epoch)
        return route
    raise RuntimeError("MCP routing context is unavailable")


def _assert_route_active(route: _Routing, *, expected_epoch: int | None) -> None:
    with application()._sessions() as session, session.begin():
        set_context_on_session(
            session,
            TenantScope(route.tenant_id),
            actor_id=route.actor_id,
        )
        assert_run_active(session, route.tenant_id, route.run_id, expected_epoch=expected_epoch)
        identity = session.execute(
            select(task.c.agent_identity_ref).where(
                task.c.tenant_id == route.tenant_id,
                task.c.run_id == route.run_id,
                task.c.id == route.task_id,
                task.c.status == "RUNNING",
            )
        ).scalar_one_or_none()
        if identity is None or (
            route.agent_code is not None and str(identity).split("@", 1)[0] != route.agent_code
        ):
            raise RuntimeError("MCP task capability is no longer bound to active delivery work")


class _ConsumerCredentialMiddleware:
    """Authenticate the service principal and bind tenant/run/task routing headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if os.getenv("LAUNCHSCOPE_DEMO_MODE", "").lower() == "true":
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope["headers"]}
        expected = os.getenv("LAUNCHSCOPE_MCP_CONSUMER_TOKEN", "")
        authorization = headers.get("authorization", "")
        supplied = authorization.removeprefix("Bearer ")
        if not expected or not hmac.compare_digest(expected, supplied):
            await JSONResponse({"detail": "valid MCP consumer credential required"}, status_code=401)(
                scope, receive, send
            )
            return
        try:
            routing = _Routing(
                tenant_id=UUID(headers["x-launchscope-tenant-id"]),
                actor_id=headers["x-launchscope-actor-id"],
                run_id=UUID(headers["x-launchscope-run-id"]),
                task_id=UUID(headers["x-launchscope-task-id"]),
            )
        except (KeyError, ValueError):
            await JSONResponse({"detail": "valid tenant, actor, Run, and Task headers required"}, status_code=400)(
                scope, receive, send
            )
            return
        token = _routing_context.set(routing)
        try:
            await self.app(scope, receive, send)
        finally:
            _routing_context.reset(token)


def _server(name: str) -> MCPServer[Any]:
    return MCPServer(
        name=name,
        version="1.0.0",
        description="LaunchScope bounded read-only evidence capability",
    )


context_server = _server("launchscope-context")


@context_server.tool(name="launchscope-context.get.v1", structured_output=True)
def context_get(context_token: str) -> dict[str, object]:
    route = _routing(context_token)
    return application().context_get(Actor(route.tenant_id, route.actor_id), route.run_id, route.task_id)


@context_server.tool(name="launchscope-context.get.v2", structured_output=True)
def context_get_v2(context_token: str) -> dict[str, object]:
    route = _routing(context_token)
    return application().context_get_v2(Actor(route.tenant_id, route.actor_id), route.run_id, route.task_id)


material_server = _server("material")


@material_server.tool(name="material.read.v1", structured_output=True)
def material_read(context_token: str, unit_refs: list[str], purpose: str) -> dict[str, object]:
    route = _routing(context_token)
    return application().material_read(
        Actor(route.tenant_id, route.actor_id), route.run_id, route.task_id, unit_refs, purpose
    )


browser_server = _server("browser-audit")


@browser_server.tool(name="browser-audit.v1", structured_output=True)
def browser_audit(url: str, context_token: str) -> dict[str, object]:
    route = _routing(context_token)
    return application().browser_audit(Actor(route.tenant_id, route.actor_id), route.run_id, route.task_id, url)


search_server = _server("public-research-search")


@search_server.tool(name="public-research-search.v1", structured_output=True)
def public_research_search(
    query: str, context_token: str, region: str = "GLOBAL", max_results: int = 5, days: int | None = None
) -> dict[str, object]:
    route = _routing(context_token)
    return application().public_research_search(
        Actor(route.tenant_id, route.actor_id), route.run_id, route.task_id,
        query=query, region=region, max_results=max_results, days=days,
    )


user_validation_server = _server("user-validation-designer")


def _bound_route(context_token: str, run_id: str, task_id: str, tool_id: str) -> _Routing:
    route = _routing(context_token)
    capability = verify_task_capability(context_token)
    if str(route.run_id) != run_id or str(route.task_id) != task_id:
        raise ValueError("tool input does not match the task capability route")
    if (capability.tenant_id, capability.run_id, capability.task_id) != (
        route.tenant_id, route.run_id, route.task_id
    ):
        raise ValueError("task capability does not match the authenticated route")
    if tool_id not in capability.allowed_tools:
        raise ValueError("task capability does not authorize this tool")
    return route


@user_validation_server.tool(name="user-validation-designer.start.v1", structured_output=True)
def user_validation_start(
    run_id: str,
    task_id: str,
    expected_revision: int,
    checkpoint_sha256: str,
    idempotency_key: str,
    correlation_id: str,
    context_token: str,
) -> dict[str, object]:
    route = _bound_route(context_token, run_id, task_id, "user-validation-designer.start.v1")
    return user_validation_application().start(
        Actor(route.tenant_id, route.actor_id),
        route.run_id,
        route.task_id,
        expected_revision=expected_revision,
        checkpoint_sha256=checkpoint_sha256,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )


@user_validation_server.tool(name="user-validation-designer.submit-step.v1", structured_output=True)
def user_validation_submit_step(
    run_id: str,
    task_id: str,
    execution_id: str,
    expected_revision: int,
    checkpoint_sha256: str,
    step_id: str,
    attempt: int,
    output: dict[str, object],
    idempotency_key: str,
    correlation_id: str,
    context_token: str,
) -> dict[str, object]:
    route = _bound_route(context_token, run_id, task_id, "user-validation-designer.submit-step.v1")
    return user_validation_application().submit_step(
        Actor(route.tenant_id, route.actor_id),
        UUID(execution_id),
        expected_revision=expected_revision,
        checkpoint_sha256=checkpoint_sha256,
        step_id=step_id,
        attempt=attempt,
        output=output,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )


@user_validation_server.tool(name="user-validation-designer.resume.v1", structured_output=True)
def user_validation_resume(
    run_id: str,
    task_id: str,
    execution_id: str,
    expected_revision: int,
    checkpoint_sha256: str,
    idempotency_key: str,
    correlation_id: str,
    context_token: str,
) -> dict[str, object]:
    route = _bound_route(context_token, run_id, task_id, "user-validation-designer.resume.v1")
    return user_validation_application().resume(
        Actor(route.tenant_id, route.actor_id),
        UUID(execution_id),
        expected_revision=expected_revision,
        checkpoint_sha256=checkpoint_sha256,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )


audit_context_server = _server("user-validation-audit-context")


@audit_context_server.tool(name="user-validation-audit-context.get.v1", structured_output=True)
def user_validation_audit_context(
    run_id: str,
    task_id: str,
    skill_result_ref: str,
    context_token: str,
    section: str = "summary",
    cursor: str | None = None,
) -> dict[str, object]:
    route = _bound_route(context_token, run_id, task_id, "user-validation-audit-context.get.v1")
    return user_validation_application().audit_context(
        Actor(route.tenant_id, route.actor_id),
        route.run_id,
        route.task_id,
        UUID(skill_result_ref),
        section=section,
        cursor=cursor,
    )


def _transport_app(server: MCPServer[Any]) -> Starlette:
    return server.streamable_http_app(
        streamable_http_path="/", json_response=True, stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


_context_app = _transport_app(context_server)
_material_app = _transport_app(material_server)
_browser_app = _transport_app(browser_server)
_search_app = _transport_app(search_server)
_user_validation_app = _transport_app(user_validation_server)
_audit_context_app = _transport_app(audit_context_server)


@asynccontextmanager
async def _lifespan(_: Starlette) -> AsyncIterator[None]:
    async with AsyncExitStack() as stack:
        for child in (_context_app, _material_app, _browser_app, _search_app, _user_validation_app, _audit_context_app):
            await stack.enter_async_context(child.router.lifespan_context(child))
        yield


app = _ConsumerCredentialMiddleware(Starlette(lifespan=_lifespan, routes=[
    Mount("/mcp/context", app=_context_app),
    Mount("/mcp/material", app=_material_app),
    Mount("/mcp/browser-audit", app=_browser_app),
    Mount("/mcp/public-research-search", app=_search_app),
    Mount("/mcp/user-validation-designer", app=_user_validation_app),
    Mount("/mcp/user-validation-audit-context", app=_audit_context_app),
]))

__all__ = [
    "app",
    "application",
    "audit_context_server",
    "browser_server",
    "context_server",
    "material_server",
    "search_server",
    "user_validation_application",
    "user_validation_server",
]
