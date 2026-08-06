"""Explicit Worker runtime boundary.

This policy is input to the deployment sandbox.  It deliberately exposes no
shell, subprocess, credential mount, or ambient network capability.  Tool
Gateway is the only component allowed to request narrowly scoped egress.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class SandboxViolation(PermissionError):
    """The Worker attempted an operation outside its immutable sandbox."""


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    read_roots: tuple[Path, ...] = ()
    network_enabled: bool = False
    allow_subprocess: bool = False
    allow_secret_mounts: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "read_roots", tuple(path.resolve() for path in self.read_roots))

    @classmethod
    def for_repository(cls, root: Path) -> SandboxPolicy:
        return cls(read_roots=(root,), network_enabled=False, allow_subprocess=False, allow_secret_mounts=False)

    def require_network_gateway(self, tool_id: str) -> None:
        if not self.network_enabled:
            raise SandboxViolation(f"{tool_id} requires the egress Tool Gateway; ambient Worker network is disabled")

    def resolve_read_path(self, value: str | Path) -> Path:
        candidate = Path(value)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            if len(self.read_roots) != 1:
                raise SandboxViolation("relative paths require exactly one repository read root")
            resolved = (self.read_roots[0] / candidate).resolve()
        if not any(resolved.is_relative_to(root) for root in self.read_roots):
            raise SandboxViolation("repository path escapes the Worker read-only mount")
        return resolved

    def require_no_subprocess(self) -> None:
        if not self.allow_subprocess:
            raise SandboxViolation("Worker sandbox prohibits subprocess and repository script execution")
