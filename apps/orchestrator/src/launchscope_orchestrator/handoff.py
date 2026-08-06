"""The deliberately narrow Matrix payload allowed to cross Agent boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from launchscope_domain import FailureClass, TaskStatus

_ALLOWED_KINDS = frozenset({"FINDING", "STATE_CHANGE_REQUEST"})
_PROHIBITED_PAYLOAD_KEYS = frozenset(
    {
        "report",
        "full_report",
        "chat",
        "chat_history",
        "messages",
        "thought",
        "reasoning",
        "chain_of_thought",
        "memory",
        "run",
    }
)


class HandoffValidationError(ValueError):
    """A Matrix handoff exceeds the intentionally minimal data contract."""


@dataclass(frozen=True, slots=True)
class MatrixHandoff:
    task_id: UUID
    sender_agent: str
    kind: str
    structured_result: Mapping[str, object]
    evidence_uris: tuple[str, ...]
    risk: str
    confidence: float
    approval_required: bool
    failure_class: FailureClass | None
    requested_status: TaskStatus | None

    def __post_init__(self) -> None:
        if self.kind not in _ALLOWED_KINDS:
            raise HandoffValidationError("Agents may submit only a Finding or a state-change request")
        if not isinstance(self.sender_agent, str) or not self.sender_agent.strip():
            raise HandoffValidationError("sender_agent is required")
        if not isinstance(self.structured_result, Mapping):
            raise HandoffValidationError("structured_result must be an object")
        _assert_safe_result(self.structured_result)
        object.__setattr__(self, "structured_result", MappingProxyType(dict(self.structured_result)))
        if not isinstance(self.evidence_uris, tuple) or any(
            not _is_evidence_uri(value) for value in self.evidence_uris
        ):
            raise HandoffValidationError("evidence_uris must contain only evidence references")
        if self.kind == "FINDING" and not self.evidence_uris:
            raise HandoffValidationError("a Finding handoff requires at least one Evidence URI")
        if not isinstance(self.risk, str) or not self.risk.strip():
            raise HandoffValidationError("risk is required")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not 0 <= self.confidence <= 1
        ):
            raise HandoffValidationError("confidence must be between 0 and 1")
        if self.kind == "FINDING" and self.requested_status is not None:
            raise HandoffValidationError("a Finding cannot carry a state change")
        if self.kind == "STATE_CHANGE_REQUEST" and self.requested_status is None:
            raise HandoffValidationError("a state-change request requires requested_status")


def _is_evidence_uri(value: object) -> bool:
    return isinstance(value, str) and (value.startswith("evidence://") or value.startswith("object://evidence/"))


def _assert_safe_result(value: Mapping[str, object]) -> None:
    for key, item in value.items():
        if not isinstance(key, str) or key.lower() in _PROHIBITED_PAYLOAD_KEYS:
            raise HandoffValidationError(
                "Matrix handoff may not carry reports, chat logs, private reasoning, Memory, or Run data"
            )
        if isinstance(item, Mapping):
            _assert_safe_result(item)
        elif isinstance(item, list) and any(isinstance(child, Mapping) for child in item):
            for child in item:
                if isinstance(child, Mapping):
                    _assert_safe_result(child)


__all__ = ["HandoffValidationError", "MatrixHandoff"]
