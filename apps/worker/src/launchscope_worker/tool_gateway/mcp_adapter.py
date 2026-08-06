"""MCP transport adapter; it cannot relax the contract enforced by ToolGateway."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from .contract import AdapterResult, ToolContract, ToolGatewayError

McpCall = Callable[[str, Mapping[str, object]], Mapping[str, object]]


class McpAdapter:
    def __init__(self, call: McpCall) -> None:
        self.call = call

    def invoke(self, tool_id: str, parameters: Mapping[str, object], contract: ToolContract) -> AdapterResult:
        payload = self.call(tool_id, parameters)
        if payload.get("submission_state_known") is not True or payload.get("cost_state_known") is not True:
            return AdapterResult({}, submission_state_known=False, cost_state_known=False)
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise ToolGatewayError("MCP adapter returned no structured result")
        evidence = payload.get("evidence")
        if evidence is not None and not isinstance(evidence, Mapping):
            raise ToolGatewayError("MCP adapter evidence must be structured")
        return AdapterResult(dict(result), dict(evidence) if evidence else None)
