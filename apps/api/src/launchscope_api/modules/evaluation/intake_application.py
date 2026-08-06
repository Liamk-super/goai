"""Gap questions and the confirmation-only gate into PLANNED.

This T5 service intentionally does not reserve budget or create Worker tasks.
Those operations belong to later stages and remain unreachable until the human
confirmation transition has completed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from launchscope_api.modules.identity_tenant.application import Actor
from launchscope_api.modules.project_dossier.profile_confirmation import (
    ConfirmedProductProfile,
    ProductProfileDraft,
    ProfileStatus,
)
from launchscope_domain import EvaluationRun, TenantScope


class IntakeValidationError(ValueError):
    """The product intake is not ready for its requested state change."""


_REQUIRED_FIELDS = ("target_user", "payer", "stage", "region", "validation_goal")
_QUESTION_TEXT = {
    "target_user": "Who is the primary target user?",
    "payer": "Who pays for the product or service?",
    "stage": "What is the current product stage?",
    "region": "Which region is this validation for?",
    "validation_goal": "What decision should this validation help you make?",
}


@dataclass(frozen=True, slots=True)
class GapQuestion:
    question_id: UUID
    product_version_id: UUID
    field: str
    question: str
    priority: int
    correlation_id: UUID


@dataclass
class IntakeApplication:
    drafts: dict[UUID, ProductProfileDraft] = field(default_factory=dict)
    questions: dict[UUID, tuple[GapQuestion, ...]] = field(default_factory=dict)
    confirmed: dict[UUID, ConfirmedProductProfile] = field(default_factory=dict)
    runs: dict[UUID, EvaluationRun] = field(default_factory=dict)

    def generate_draft_and_questions(
        self,
        actor: Actor,
        product_version_id: UUID,
        *,
        has_validated_material: bool,
        correlation_id: UUID,
    ) -> tuple[ProductProfileDraft, tuple[GapQuestion, ...]]:
        if not has_validated_material:
            raise IntakeValidationError("at least one validated quarantined material is required before gap diagnosis")
        draft = ProductProfileDraft.create(product_version_id, {field: None for field in _REQUIRED_FIELDS})
        questions = tuple(
            GapQuestion(uuid4(), product_version_id, field, _QUESTION_TEXT[field], priority, correlation_id)
            for priority, field in enumerate(_REQUIRED_FIELDS, start=1)
        )
        # Keep this invariant local and explicit even if mandatory fields later change.
        if not 3 <= len(questions) <= 5:
            raise RuntimeError("gap diagnosis must emit between 3 and 5 priority questions")
        self.drafts[product_version_id] = draft
        self.questions[product_version_id] = questions
        return draft, questions

    def answer_questions(
        self,
        product_version_id: UUID,
        correlation_id: UUID,
        answers: dict[str, str],
    ) -> ProductProfileDraft:
        draft = self._draft(product_version_id)
        expected = self.questions.get(product_version_id, ())
        if not expected or any(question.correlation_id != correlation_id for question in expected):
            raise IntakeValidationError("answers must use the active gap-question correlation_id")
        allowed = {question.field for question in expected}
        if not answers or set(answers) - allowed:
            raise IntakeValidationError("answers contain an unknown gap-question field")
        for question_field, answer in answers.items():
            if not isinstance(answer, str) or not answer.strip() or len(answer.strip()) > 2000:
                raise IntakeValidationError(
                    f"answer for {question_field} must be a non-empty string up to 2000 characters"
                )
            draft.answers[question_field] = answer.strip()
        return draft

    def confirm_profile(
        self,
        actor: Actor,
        product_version_id: UUID,
        *,
        acknowledge_model_inference: bool,
    ) -> ConfirmedProductProfile:
        draft = self._draft(product_version_id)
        if not acknowledge_model_inference:
            raise IntakeValidationError(
                "the user must acknowledge that the draft is model inference, not a confirmed fact"
            )
        missing = [field for field in _REQUIRED_FIELDS if not draft.answers.get(field)]
        if missing:
            raise IntakeValidationError(
                f"cannot confirm ProductProfile while required answers are missing: {', '.join(missing)}"
            )
        draft.status = ProfileStatus.CONFIRMED
        profile = ConfirmedProductProfile(uuid4(), product_version_id, actor.actor_id, dict(draft.answers))
        self.confirmed[product_version_id] = profile
        return profile

    def enter_planned(
        self,
        actor: Actor,
        *,
        workspace_id: UUID,
        project_id: UUID,
        product_version_id: UUID,
        correlation_id: UUID,
    ) -> EvaluationRun:
        if product_version_id not in self.confirmed:
            raise IntakeValidationError("ProductProfile must be user-confirmed before a run may enter PLANNED")
        run = EvaluationRun.create(
            TenantScope(
                tenant_id=actor.tenant_id,
                workspace_id=workspace_id,
                project_id=project_id,
                product_version_id=product_version_id,
            ),
            product_version_id,
        )
        run.start_intake()
        run.identify_gap()
        run.confirm_profile()
        self.runs[run.run_id] = run
        return run

    def _draft(self, product_version_id: UUID) -> ProductProfileDraft:
        draft = self.drafts.get(product_version_id)
        if draft is None:
            raise IntakeValidationError("gap diagnosis has not generated a ProductProfile draft")
        return draft


__all__ = ["GapQuestion", "IntakeApplication", "IntakeValidationError"]
