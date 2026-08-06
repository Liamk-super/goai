"""In-process adapter preserving the transport-neutral Tool Gateway port."""

from __future__ import annotations

from collections.abc import Mapping

from .contract import AdapterResult, ToolAdapter, ToolContract, ToolGatewayError


class InternalAdapter:
    def __init__(self, handlers: Mapping[str, ToolAdapter]) -> None:
        self.handlers = dict(handlers)

    def invoke(self, tool_id: str, parameters: Mapping[str, object], contract: ToolContract) -> AdapterResult:
        handler = self.handlers.get(tool_id)
        if handler is None:
            raise ToolGatewayError("no internal adapter is registered for Tool Contract")
        return handler(parameters, contract)
