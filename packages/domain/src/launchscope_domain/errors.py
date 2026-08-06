"""Errors raised by the LaunchScope domain kernel.

The domain layer deliberately exposes structured, deterministic errors.  An
application adapter can turn these errors into an HTTP or message error
without making the domain depend on that transport.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class DomainError(Exception):
    """Base class for an expected domain rejection."""

    code = "DOMAIN_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.code
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        """Return a transport-neutral structured error."""

        return {"code": self.code, "message": self.message, "details": dict(self.details)}


class ValidationError(DomainError):
    """A value or command does not satisfy its domain contract."""

    code = "VALIDATION_ERROR"


class InvariantViolation(DomainError):
    """An aggregate invariant would be broken."""

    code = "INVARIANT_VIOLATION"


class TenantScopeViolation(InvariantViolation):
    """A resource from another tenant or narrower scope was supplied."""

    code = "TENANT_SCOPE_VIOLATION"


class InvalidTransitionError(DomainError):
    """A state transition is not legal or its guard is not satisfied."""

    code = "INVALID_STATE_TRANSITION"

    def __init__(
        self,
        current: object,
        target: object,
        *,
        reason: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.current = current
        self.target = target
        self.reason = reason
        merged = {"current": str(current), "target": str(target), "reason": reason}
        merged.update(details or {})
        super().__init__(
            f"illegal transition {current} -> {target}: {reason}",
            details=merged,
        )


class AppendOnlyViolation(InvariantViolation):
    """An attempt was made to mutate or overwrite historical facts."""

    code = "APPEND_ONLY_VIOLATION"


class MissingEvidenceError(InvariantViolation):
    """A finding or decision is not sufficiently grounded in evidence."""

    code = "MISSING_EVIDENCE"


class FailClosedError(DomainError):
    """A failure has unknown side-effect or cost state and must stay frozen."""

    code = "FAIL_CLOSED"


class BudgetError(DomainError):
    """A budget reservation or consumption would exceed its limit."""

    code = "BUDGET_EXCEEDED"


class DagError(InvariantViolation):
    """The task dependency graph is invalid."""

    code = "INVALID_TASK_DAG"


class CycleDetectedError(DagError):
    """The task dependency graph contains a cycle."""

    code = "TASK_DAG_CYCLE"


class RetryNotPermittedError(DomainError):
    """A task failure is explicitly outside the retry policy."""

    code = "RETRY_NOT_PERMITTED"
