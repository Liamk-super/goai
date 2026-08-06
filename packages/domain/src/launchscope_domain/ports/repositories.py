"""Persistence ports owned by the domain, with no persistence implementation."""

from __future__ import annotations

from typing import Protocol, TypeVar
from uuid import UUID

from ..aggregates import DecisionReport, EvaluationRun, EvidenceReview, ProjectDossier
from ..value_objects import TenantScope

AggregateT = TypeVar("AggregateT", contravariant=False)


class Repository(Protocol[AggregateT]):
    """Minimal scope-aware repository port."""

    def get(self, resource_id: UUID, scope: TenantScope) -> AggregateT | None: ...

    def save(self, aggregate: AggregateT) -> None: ...


class ProjectDossierRepository(Repository[ProjectDossier], Protocol):
    """Port for the ProjectDossier application owner."""


class EvaluationRunRepository(Repository[EvaluationRun], Protocol):
    """Port for the Evaluation application owner."""


class EvidenceReviewRepository(Repository[EvidenceReview], Protocol):
    """Port for the Evidence application owner."""


class DecisionReportRepository(Repository[DecisionReport], Protocol):
    """Port for the Decision & Report application owner."""
