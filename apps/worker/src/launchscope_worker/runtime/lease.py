"""Short-lived, compare-and-swap task leases for isolated Workers."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID


class LeaseConflict(RuntimeError):
    """A lease is absent, expired, or owned by another Worker."""


@dataclass(frozen=True, slots=True)
class Lease:
    task_id: UUID
    worker_id: str
    token: str
    expires_at: datetime

    def active(self, *, now: datetime | None = None) -> bool:
        instant = now or datetime.now(UTC)
        return instant < self.expires_at


class LeaseRegistry:
    """In-memory CAS model; production storage must enforce the same predicate."""

    def __init__(self) -> None:
        self._leases: dict[UUID, Lease] = {}

    def acquire(self, task_id: UUID, worker_id: str, *, ttl_seconds: int, now: datetime | None = None) -> Lease:
        if ttl_seconds <= 0 or ttl_seconds > 900:
            raise LeaseConflict("lease ttl must be between one second and fifteen minutes")
        instant = now or datetime.now(UTC)
        existing = self._leases.get(task_id)
        if existing is not None and existing.active(now=instant):
            raise LeaseConflict("task already has an active lease")
        lease = Lease(task_id, worker_id, secrets.token_urlsafe(32), instant + timedelta(seconds=ttl_seconds))
        self._leases[task_id] = lease
        return lease

    def require_active(self, task_id: UUID, token: str, *, now: datetime | None = None) -> Lease:
        lease = self._leases.get(task_id)
        if lease is None or lease.token != token or not lease.active(now=now):
            raise LeaseConflict("task lease is missing, expired, or does not match")
        return lease

    def release(self, task_id: UUID, token: str) -> None:
        self.require_active(task_id, token)
        del self._leases[task_id]
