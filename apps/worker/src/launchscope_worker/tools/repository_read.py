"""Read-only repository access with no subprocess, network, or path escape."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

from ..runtime.sandbox import SandboxPolicy, SandboxViolation
from ..tool_gateway.contract import AdapterResult, ToolContract, ToolGatewayError


class RepositoryReader:
    def __init__(self, root: Path) -> None:
        self.sandbox = SandboxPolicy.for_repository(root)

    def read(self, parameters: Mapping[str, object], contract: ToolContract) -> AdapterResult:
        if contract.network_level != "NONE":
            raise ToolGatewayError("repository.read must run without network")
        path = parameters.get("path")
        max_bytes = parameters.get("max_bytes", 262144)
        if not isinstance(path, str) or not isinstance(max_bytes, int) or max_bytes <= 0 or max_bytes > 1048576:
            raise ToolGatewayError("repository.read requires a relative path and bounded max_bytes")
        try:
            resolved = self.sandbox.resolve_read_path(path)
        except SandboxViolation as exc:
            raise ToolGatewayError(str(exc)) from exc
        if not resolved.is_file() or resolved.is_symlink():
            raise ToolGatewayError("repository path must be a regular non-symlink file")
        payload = resolved.read_bytes()
        if len(payload) > max_bytes:
            raise ToolGatewayError("repository file exceeds frozen byte budget")
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolGatewayError("repository.read only returns UTF-8 text evidence") from exc
        relative = resolved.relative_to(self.sandbox.read_roots[0]).as_posix()
        digest = hashlib.sha256(payload).hexdigest()
        return AdapterResult(
            {"path": relative, "sha256": digest, "content": content},
            {"path": relative, "sha256": digest, "source_type": "REPOSITORY"},
        )
