"""Integration ports; adapters stay outside the pure domain package."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from ..events import DomainEvent


class EventPublisher(Protocol):
    """Publish already-validated domain events."""

    def publish(self, event: DomainEvent) -> None: ...


class StateChangeRequester(Protocol):
    """Receive structured requests without owning business state."""

    def request_state_change(self, request: Mapping[str, object]) -> None: ...


class EvidenceObjectMetadataReader(Protocol):
    """Read metadata through an adapter without exposing object-store SDKs."""

    def read_metadata(self, object_key: str) -> Mapping[str, object]: ...


class ApprovalVerifier(Protocol):
    """Verify one-time approval bindings at an application boundary."""

    def verify(self, approval_request_id: str, parameters_sha256: str) -> bool: ...


class ScopeFilter(Protocol):
    """Apply tenant/project/version/time/permission filters before retrieval."""

    def filter(
        self, records: Sequence[Mapping[str, object]], scope: Mapping[str, object]
    ) -> Sequence[Mapping[str, object]]: ...
