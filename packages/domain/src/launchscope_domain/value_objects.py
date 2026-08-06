"""Immutable, validated value objects for the LaunchScope domain."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Final
from uuid import UUID

from .enums import DomainStrEnum, EvidenceLevel, EvidenceSourceType
from .errors import BudgetError, ValidationError

_IDEMPOTENCY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_VERSION_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9]+\.[0-9]+$")
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F]{64}$")


def _uuid(value: UUID | str, field_name: str) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (AttributeError, ValueError, TypeError) as exc:
        raise ValidationError(f"{field_name} must be a UUID", details={"field": field_name}) from exc


def _text(value: str, field_name: str, *, max_length: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a non-empty string", details={"field": field_name})
    normalized = value.strip()
    if max_length is not None and len(normalized) > max_length:
        raise ValidationError(
            f"{field_name} exceeds its maximum length",
            details={"field": field_name, "max_length": max_length},
        )
    return normalized


def _aware(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field_name} must be timezone-aware", details={"field": field_name})
    return value.astimezone(UTC)


def _sha256(value: str, field_name: str = "sha256") -> str:
    normalized = _text(value, field_name).lower()
    if _SHA256_PATTERN.fullmatch(normalized) is None:
        raise ValidationError(f"{field_name} must be a 64-character SHA-256 hex digest", details={"field": field_name})
    return normalized


def _decimal(value: Decimal | int | float | str, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValidationError(f"{field_name} must be numeric", details={"field": field_name})
    try:
        normalized = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationError(f"{field_name} must be numeric", details={"field": field_name}) from exc
    if not normalized.is_finite():
        raise ValidationError(f"{field_name} must be finite", details={"field": field_name})
    if normalized < 0:
        raise ValidationError(f"{field_name} must not be negative", details={"field": field_name})
    return normalized


def _enum_value(value: DomainStrEnum | str, enum_type: type[DomainStrEnum], field_name: str) -> str:
    try:
        return enum_type(value).value
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"{field_name} is not a valid {enum_type.__name__}", details={"field": field_name}
        ) from exc


@dataclass(frozen=True, slots=True)
class TenantScope:
    """The tenant boundary carried by every domain resource."""

    tenant_id: UUID
    workspace_id: UUID | None = None
    project_id: UUID | None = None
    product_version_id: UUID | None = None
    run_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", _uuid(self.tenant_id, "tenant_id"))
        for field_name in ("workspace_id", "project_id", "product_version_id", "run_id"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _uuid(value, field_name))
        if self.project_id is not None and self.workspace_id is None:
            raise ValidationError("project_id requires workspace_id", details={"field": "project_id"})
        if self.product_version_id is not None and self.project_id is None:
            raise ValidationError("product_version_id requires project_id", details={"field": "product_version_id"})
        if self.run_id is not None and self.project_id is None:
            raise ValidationError("run_id requires project_id", details={"field": "run_id"})

    def with_product_version(self, product_version_id: UUID | str) -> TenantScope:
        return replace(self, product_version_id=_uuid(product_version_id, "product_version_id"))

    def with_run(self, run_id: UUID | str) -> TenantScope:
        return replace(self, run_id=_uuid(run_id, "run_id"))

    def contains(self, other: TenantScope) -> bool:
        """Return whether ``other`` stays inside this scope."""

        return (
            self.tenant_id == other.tenant_id
            and (self.workspace_id is None or self.workspace_id == other.workspace_id)
            and (self.project_id is None or self.project_id == other.project_id)
            and (self.product_version_id is None or self.product_version_id == other.product_version_id)
            and (self.run_id is None or self.run_id == other.run_id)
        )


@dataclass(frozen=True, slots=True)
class CorrelationContext:
    """Correlation and idempotency identifiers for a command or event."""

    correlation_id: UUID
    causation_id: UUID | None = None
    idempotency_key: str = "domain-event"
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        object.__setattr__(self, "correlation_id", _uuid(self.correlation_id, "correlation_id"))
        if self.causation_id is not None:
            object.__setattr__(self, "causation_id", _uuid(self.causation_id, "causation_id"))
        key = _text(self.idempotency_key, "idempotency_key", max_length=200)
        if _IDEMPOTENCY_PATTERN.fullmatch(key) is None:
            raise ValidationError(
                "idempotency_key has invalid characters",
                details={"field": "idempotency_key"},
            )
        object.__setattr__(self, "idempotency_key", key)
        version = _text(self.schema_version, "schema_version")
        if _VERSION_PATTERN.fullmatch(version) is None:
            raise ValidationError(
                "schema_version must use MAJOR.MINOR form",
                details={"field": "schema_version"},
            )
        object.__setattr__(self, "schema_version", version)


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """Metadata needed to locate and verify an evidence object."""

    evidence_id: UUID
    object_key: str
    sha256: str
    mime_type: str
    source_type: str
    trust_level: str
    size_bytes: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _uuid(self.evidence_id, "evidence_id"))
        object_key = _text(self.object_key, "object_key", max_length=1024)
        if object_key.startswith("/") or ".." in object_key.split("/"):
            raise ValidationError("object_key must be a relative, traversal-free key", details={"field": "object_key"})
        object.__setattr__(self, "object_key", object_key)
        object.__setattr__(self, "sha256", _sha256(self.sha256))
        mime_type = _text(self.mime_type, "mime_type", max_length=255)
        if "/" not in mime_type:
            raise ValidationError("mime_type must contain a type/subtype", details={"field": "mime_type"})
        object.__setattr__(self, "mime_type", mime_type)
        object.__setattr__(
            self,
            "source_type",
            _enum_value(self.source_type, EvidenceSourceType, "source_type"),
        )
        object.__setattr__(self, "trust_level", _enum_value(self.trust_level, EvidenceLevel, "trust_level"))
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ValidationError("size_bytes must be a non-negative integer", details={"field": "size_bytes"})


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    """A non-negative, run-scoped budget reservation."""

    run_id: UUID
    category: str
    limit: Decimal
    reserved: Decimal
    consumed: Decimal = Decimal("0")
    currency: str = "unit"

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _uuid(self.run_id, "run_id"))
        object.__setattr__(self, "category", _text(self.category, "category", max_length=100))
        limit = _decimal(self.limit, "limit")
        reserved = _decimal(self.reserved, "reserved")
        consumed = _decimal(self.consumed, "consumed")
        if reserved > limit:
            raise BudgetError("reserved budget exceeds limit", details={"category": self.category})
        if consumed > reserved:
            raise BudgetError("consumed budget exceeds reservation", details={"category": self.category})
        object.__setattr__(self, "limit", limit)
        object.__setattr__(self, "reserved", reserved)
        object.__setattr__(self, "consumed", consumed)
        object.__setattr__(self, "currency", _text(self.currency, "currency", max_length=20))

    @property
    def remaining(self) -> Decimal:
        return self.reserved - self.consumed

    @property
    def is_exhausted(self) -> bool:
        return self.remaining == Decimal("0")

    def can_consume(self, amount: Decimal | int | float | str) -> bool:
        return _decimal(amount, "amount") <= self.remaining

    def consume(self, amount: Decimal | int | float | str) -> BudgetReservation:
        normalized = _decimal(amount, "amount")
        if normalized > self.remaining:
            raise BudgetError(
                "budget consumption exceeds reservation",
                details={"category": self.category, "remaining": str(self.remaining)},
            )
        return replace(self, consumed=self.consumed + normalized)

    def release(self) -> BudgetReservation:
        return replace(self, reserved=self.consumed)


@dataclass(frozen=True, slots=True)
class ApprovalBinding:
    """A one-time approval bound to one run, tool and parameter hash."""

    run_id: UUID
    tool_id: str
    parameters_sha256: str
    expires_at: datetime
    one_time_token_id: UUID
    used: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _uuid(self.run_id, "run_id"))
        object.__setattr__(self, "tool_id", _text(self.tool_id, "tool_id", max_length=200))
        object.__setattr__(self, "parameters_sha256", _sha256(self.parameters_sha256, "parameters_sha256"))
        object.__setattr__(self, "expires_at", _aware(self.expires_at, "expires_at"))
        object.__setattr__(self, "one_time_token_id", _uuid(self.one_time_token_id, "one_time_token_id"))
        if not isinstance(self.used, bool):
            raise ValidationError("used must be boolean", details={"field": "used"})

    def is_valid(self, at: datetime | None = None) -> bool:
        now = _aware(at, "at") if at is not None else datetime.now(UTC)
        return not self.used and now < self.expires_at

    def binds(self, parameters_sha256: str, *, at: datetime | None = None) -> bool:
        try:
            digest = _sha256(parameters_sha256, "parameters_sha256")
        except ValidationError:
            return False
        return digest == self.parameters_sha256 and self.is_valid(at)

    def consume(self, *, at: datetime | None = None) -> ApprovalBinding:
        if not self.is_valid(at):
            raise ValidationError("approval binding is expired or already used", details={"field": "approval"})
        return replace(self, used=True)


@dataclass(frozen=True, slots=True)
class TimeScope:
    """Publication, fetch, validity and regional applicability metadata."""

    published_at: datetime | None = None
    fetched_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    region: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("published_at", "fetched_at", "valid_from", "valid_until"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _aware(value, field_name))
        if self.valid_from is not None and self.valid_until is not None and self.valid_until < self.valid_from:
            raise ValidationError("valid_until must not precede valid_from", details={"field": "valid_until"})
        if self.region is not None:
            object.__setattr__(self, "region", _text(self.region, "region", max_length=100))

    def is_applicable(self, *, at: datetime | None = None, region: str | None = None) -> bool:
        moment = _aware(at, "at") if at is not None else datetime.now(UTC)
        if self.region is not None and region is not None and self.region != region:
            return False
        if self.region is not None and region is None:
            return False
        if self.valid_from is not None and moment < self.valid_from:
            return False
        return not (self.valid_until is not None and moment > self.valid_until)
