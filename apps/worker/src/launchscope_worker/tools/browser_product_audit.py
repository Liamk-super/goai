"""Browser.read contract implemented as a public, snapshot-producing reader."""

from __future__ import annotations

from collections.abc import Mapping

from ..tool_gateway.contract import AdapterResult, ToolContract
from .public_research import PublicResearchClient


class BrowserProductAudit:
    def __init__(self, research: PublicResearchClient | None = None) -> None:
        self.research = research or PublicResearchClient()

    def read(self, parameters: Mapping[str, object], contract: ToolContract) -> AdapterResult:
        fetched = self.research.fetch({"url": parameters.get("url"), "method": "GET"}, contract)
        result = dict(fetched.result)
        result["url"] = result.pop("source_url")
        result["snapshot_sha256"] = result.pop("content_sha256")
        result.pop("content_type", None)
        result["summary"] = "Public product surface captured through the read-only browser contract"
        evidence = dict(fetched.evidence or {})
        evidence["snapshot_sha256"] = result["snapshot_sha256"]
        return AdapterResult(result, evidence)
