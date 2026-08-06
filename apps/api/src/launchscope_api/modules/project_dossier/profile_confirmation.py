"""Draft-versus-confirmed ProductProfile facts.

An intake draft is explicitly a model inference.  It is not persisted as a
confirmed profile or usable for planning until a human acknowledges and
confirms the answer set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID, uuid4


class ProfileStatus(StrEnum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"


@dataclass
class ProductProfileDraft:
    draft_id: UUID
    product_version_id: UUID
    inferred_fields: dict[str, str | None]
    source: str = "MODEL_INFERENCE"
    status: ProfileStatus = ProfileStatus.DRAFT
    answers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def create(cls, product_version_id: UUID, inferred_fields: dict[str, str | None]) -> ProductProfileDraft:
        return cls(draft_id=uuid4(), product_version_id=product_version_id, inferred_fields=inferred_fields)

    def response_view(self) -> dict[str, object]:
        return {
            "draft_id": str(self.draft_id),
            "source": self.source,
            "status": self.status.value,
            "inferred_fields": self.inferred_fields,
            "user_confirmed_fields": self.answers if self.status is ProfileStatus.CONFIRMED else {},
        }


@dataclass(frozen=True, slots=True)
class ConfirmedProductProfile:
    profile_id: UUID
    product_version_id: UUID
    confirmed_by: str
    fields: dict[str, str]


__all__ = ["ConfirmedProductProfile", "ProductProfileDraft", "ProfileStatus"]
