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
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from .infrastructure.db.session import DatabaseSettings, create_database_engine, session_factory
from .infrastructure.object_store import S3QuarantineObjectStore
from .modules.evidence.mcp_application import McpEvidenceApplication, configured_browser_domains
from .modules.evidence.task_capability import verify_task_capability
from .modules.identity_tenant.application import Actor


@dataclass(frozen=True)
class _Routing:
    tenant_id: UUID
    actor_id: str
    run_id: UUID
    task_id: UUID


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


def _routing(context_token: str = "") -> _Routing:
    value = _routing_context.get()
    if value is not None:
        return value
    if os.getenv("LAUNCHSCOPE_DEMO_MODE", "").lower() == "true":
        capability = verify_task_capability(context_token)
        return _Routing(
            capability.tenant_id,
            f"agent:{capability.agent_code}",
            capability.run_id,
            capability.task_id,
        )
    raise RuntimeError("MCP routing context is unavailable")


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


def _transport_app(server: MCPServer[Any]) -> Starlette:
    return server.streamable_http_app(
        streamable_http_path="/", json_response=True, stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


_context_app = _transport_app(context_server)
_browser_app = _transport_app(browser_server)
_search_app = _transport_app(search_server)


@asynccontextmanager
async def _lifespan(_: Starlette) -> AsyncIterator[None]:
    async with AsyncExitStack() as stack:
        for child in (_context_app, _browser_app, _search_app):
            await stack.enter_async_context(child.router.lifespan_context(child))
        yield


app = _ConsumerCredentialMiddleware(Starlette(lifespan=_lifespan, routes=[
    Mount("/mcp/context", app=_context_app),
    Mount("/mcp/browser-audit", app=_browser_app),
    Mount("/mcp/public-research-search", app=_search_app),
]))

__all__ = ["app", "application", "browser_server", "context_server", "search_server"]
