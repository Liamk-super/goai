"""Worker isolation and lease primitives."""

from typing import TYPE_CHECKING

from .lease import Lease, LeaseConflict, LeaseRegistry
from .sandbox import SandboxPolicy, SandboxViolation

if TYPE_CHECKING:
    from .dispatch import WorkerDispatcher, WorkerStateChangeRequest


def __getattr__(name: str) -> object:
    if name in {"WorkerDispatcher", "WorkerStateChangeRequest"}:
        from .dispatch import WorkerDispatcher, WorkerStateChangeRequest

        return {"WorkerDispatcher": WorkerDispatcher, "WorkerStateChangeRequest": WorkerStateChangeRequest}[name]
    raise AttributeError(name)

__all__ = [
    "Lease",
    "LeaseConflict",
    "LeaseRegistry",
    "SandboxPolicy",
    "SandboxViolation",
    "WorkerDispatcher",
    "WorkerStateChangeRequest",
]
