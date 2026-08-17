"""PostgreSQL-backed T5 intake control plane used by the runtime API.

The in-memory T5 applications remain useful test doubles, but are not a
runtime source of truth.  This adapter commits every command in a tenant RLS
transaction and records the planned-run transition plus Outbox fact together.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    evaluation_run,
    intake_gap_question,
    material,
    material_selection,
    material_selection_item,
    product_profile,
    product_profile_draft,
    product_version,
    project,
    public_demo_disclosure_acceptance,
    run_status_history,
    tenant,
    user_validation_script,
    workspace,
    workspace_member,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.infrastructure.messaging.outbox import OutboxRepository
from launchscope_api.modules.evaluation.intake_application import GapQuestion, IntakeValidationError
from launchscope_api.modules.identity_tenant.application import (
    Actor,
    AuthorizationError,
    NotFoundError,
    Tenant,
    Workspace,
    WorkspaceRole,
)
from launchscope_api.modules.project_dossier.application import ProductVersion, Project
from launchscope_api.modules.project_dossier.material_analysis import (
    MaterialAnalysisApplication,
    MaterialObjectStore,
    material_routing_enabled,
)
from launchscope_api.modules.project_dossier.material_ingestion import (
    MaterialRecord,
    PersistentMaterialIngestionApplication,
    QuarantineObjectStore,
    UploadInitiation,
)
from launchscope_api.modules.project_dossier.profile_confirmation import (
    ConfirmedProductProfile,
    ProductProfileDraft,
    ProfileStatus,
)
from launchscope_api.modules.supervisor.baseline_application import (
    REPORT_STANDARD_VERSION,
    content_fingerprint_sha256,
    input_snapshot_sha256,
    report_profile_ref,
    report_v2_enabled,
    report_v3_enabled,
    select_baseline,
)
from launchscope_api.modules.supervisor.stage_admission import StageAdmissionError, evaluation_mode_for_stage
from launchscope_domain.enums import EventType, RunStatus
from launchscope_domain.events import EventEnvelope
from launchscope_domain.value_objects import CorrelationContext, TenantScope

_REQUIRED_FIELDS = ("one_line_value_claim", "target_user", "payer", "stage", "region", "validation_goal")
PUBLIC_DEMO_DISCLOSURE_POLICY_VERSION = "public-demo-evidence-v1"
_QUESTION_TEXT = {
    "one_line_value_claim": "What is the product's one-sentence value proposition?",
    "target_user": "Who is the primary target user?",
    "payer": "Who pays for the product or service?",
    "stage": "What is the current product stage?",
    "region": "Which region is this validation for?",
    "validation_goal": "What decision should this validation help you make?",
}


def _required(value: str, name: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise ValueError(f"{name} must be a non-empty string up to {max_length} characters")
    return value.strip()


@dataclass(frozen=True, slots=True)
class PersistentRun:
    run_id: UUID
    status: RunStatus


class PersistentIdentityTenantApplication:
    """Identity and workspace authorization against PostgreSQL plus RLS."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def create_tenant(self, slug: str, owner_id: str, workspace_name: str) -> tuple[Tenant, Workspace]:
        tenant_id, workspace_id = uuid4(), uuid4()
        normalized_slug = _required(slug, "slug", 120)
        normalized_owner = _required(owner_id, "owner_id", 255)
        normalized_workspace = _required(workspace_name, "workspace_name", 200)
        scope = TenantScope(tenant_id, workspace_id=workspace_id)
        with tenant_transaction(self._sessions, scope, actor_id=normalized_owner) as session:
            session.execute(tenant.insert().values(id=tenant_id, slug=normalized_slug, status="ACTIVE"))
            session.execute(
                workspace.insert().values(
                    id=workspace_id, tenant_id=tenant_id, name=normalized_workspace, status="ACTIVE"
                )
            )
            session.execute(
                workspace_member.insert().values(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    actor_id=normalized_owner,
                    role=WorkspaceRole.OWNER.value,
                )
            )
        return Tenant(tenant_id, normalized_slug), Workspace(workspace_id, tenant_id, normalized_workspace)

    def create_workspace(self, actor: Actor, name: str) -> Workspace:
        normalized_name = _required(name, "name", 200)
        workspace_id = uuid4()
        with self._transaction(actor) as session:
            self._require_tenant_member(session, actor)
            session.execute(
                workspace.insert().values(
                    id=workspace_id, tenant_id=actor.tenant_id, name=normalized_name, status="ACTIVE"
                )
            )
            session.execute(
                workspace_member.insert().values(
                    id=uuid4(),
                    tenant_id=actor.tenant_id,
                    workspace_id=workspace_id,
                    actor_id=actor.actor_id,
                    role=WorkspaceRole.OWNER.value,
                )
            )
        return Workspace(workspace_id, actor.tenant_id, normalized_name)

    def require_workspace_role(
        self, actor: Actor, workspace_id: UUID, minimum: WorkspaceRole = WorkspaceRole.EDITOR
    ) -> Workspace:
        with self._transaction(actor) as session:
            return self.require_workspace_role_in_session(session, actor, workspace_id, minimum)

    def require_workspace_role_in_session(
        self, session: Session, actor: Actor, workspace_id: UUID, minimum: WorkspaceRole = WorkspaceRole.EDITOR
    ) -> Workspace:
        row = (
            session.execute(
                select(workspace.c.id, workspace.c.name, workspace_member.c.role)
                .outerjoin(
                    workspace_member,
                    (workspace_member.c.tenant_id == workspace.c.tenant_id)
                    & (workspace_member.c.workspace_id == workspace.c.id)
                    & (workspace_member.c.actor_id == actor.actor_id),
                )
                .where(workspace.c.id == workspace_id, workspace.c.tenant_id == actor.tenant_id)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFoundError("workspace was not found")
        allowed = {
            WorkspaceRole.VIEWER: {WorkspaceRole.VIEWER.value, WorkspaceRole.EDITOR.value, WorkspaceRole.OWNER.value},
            WorkspaceRole.EDITOR: {WorkspaceRole.EDITOR.value, WorkspaceRole.OWNER.value},
            WorkspaceRole.OWNER: {WorkspaceRole.OWNER.value},
        }
        if row["role"] not in allowed[minimum]:
            raise AuthorizationError("actor lacks the required workspace role")
        return Workspace(workspace_id, actor.tenant_id, row["name"])

    def _require_tenant_member(self, session: Session, actor: Actor) -> None:
        member = session.execute(
            select(workspace_member.c.id)
            .where(
                workspace_member.c.tenant_id == actor.tenant_id,
                workspace_member.c.actor_id == actor.actor_id,
            )
            .limit(1)
        ).first()
        if member is None:
            raise AuthorizationError("actor is not a member of this tenant")

    def _transaction(self, actor: Actor) -> AbstractContextManager[Session]:
        return tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id)


class PersistentProjectDossierApplication:
    """T5 commands persisted in the tables already introduced by T4--T6."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        identity: PersistentIdentityTenantApplication,
        store: QuarantineObjectStore,
    ) -> None:
        self._sessions = sessions
        self.identity = identity
        self.materials = PersistentMaterialIngestionApplication(sessions, store)
        self.material_analysis = MaterialAnalysisApplication(sessions, cast(MaterialObjectStore, store))

    def create_project(self, actor: Actor, workspace_id: UUID, name: str) -> Project:
        normalized_name = _required(name, "project name", 200)
        project_id = uuid4()
        with self._transaction(actor) as session:
            self.identity.require_workspace_role_in_session(session, actor, workspace_id)
            session.execute(
                project.insert().values(
                    id=project_id,
                    tenant_id=actor.tenant_id,
                    workspace_id=workspace_id,
                    name=normalized_name,
                    dossier_status="ACTIVE",
                )
            )
        return Project(project_id, actor.tenant_id, workspace_id, normalized_name)

    def create_version(self, actor: Actor, project_id: UUID, label: str) -> ProductVersion:
        normalized_label = _required(label, "version label", 100)
        version_id = uuid4()
        with self._transaction(actor) as session:
            project_row = self._project(session, actor, project_id)
            self.identity.require_workspace_role_in_session(session, actor, project_row["workspace_id"])
            next_number = (
                int(
                    session.execute(
                        select(func.coalesce(func.max(product_version.c.version_number), 0)).where(
                            product_version.c.tenant_id == actor.tenant_id,
                            product_version.c.project_id == project_id,
                        )
                    ).scalar_one()
                )
                + 1
            )
            session.execute(
                product_version.insert().values(
                    id=version_id,
                    tenant_id=actor.tenant_id,
                    project_id=project_id,
                    version_number=next_number,
                    label=normalized_label,
                    stage="DRAFT",
                    status="DRAFT",
                )
            )
        return ProductVersion(
            version_id, actor.tenant_id, project_row["workspace_id"], project_id, next_number, normalized_label
        )

    def initiate_material(self, actor: Actor, version_id: UUID, **metadata: object) -> UploadInitiation:
        with self._transaction(actor) as session:
            version = self._version(session, actor, version_id)
            self.identity.require_workspace_role_in_session(session, actor, version.workspace_id)
        return self.materials.initiate(
            actor,
            workspace_id=version.workspace_id,
            project_id=version.project_id,
            product_version_id=version.product_version_id,
            **metadata,  # type: ignore[arg-type]
        )

    def complete_material(self, actor: Actor, material_id: UUID) -> MaterialRecord:
        return self.materials.complete(actor, material_id)

    def public_demo_disclosure(self, actor: Actor, version_id: UUID) -> dict[str, object]:
        with self._transaction(actor) as session:
            version = self._version(session, actor, version_id)
            self.identity.require_workspace_role_in_session(session, actor, version.workspace_id)
            row = (
                session.execute(
                    select(public_demo_disclosure_acceptance).where(
                        public_demo_disclosure_acceptance.c.tenant_id == actor.tenant_id,
                        public_demo_disclosure_acceptance.c.product_version_id == version_id,
                        public_demo_disclosure_acceptance.c.policy_version == PUBLIC_DEMO_DISCLOSURE_POLICY_VERSION,
                    )
                )
                .mappings()
                .first()
            )
            return {
                "product_version_id": str(version_id),
                "policy_version": PUBLIC_DEMO_DISCLOSURE_POLICY_VERSION,
                "accepted": row is not None,
                "acceptance_id": str(row["id"]) if row is not None else None,
                "accepted_at": row["accepted_at"].isoformat() if row is not None else None,
            }

    def accept_public_demo_disclosure(
        self,
        actor: Actor,
        version_id: UUID,
        *,
        idempotency_key: str,
        correlation_id: UUID,
    ) -> dict[str, object]:
        if not idempotency_key.strip():
            raise ValueError("Idempotency-Key is required")
        now = datetime.now(UTC)
        with self._transaction(actor) as session:
            version = self._version(session, actor, version_id)
            self.identity.require_workspace_role_in_session(session, actor, version.workspace_id)
            existing = (
                session.execute(
                    select(public_demo_disclosure_acceptance).where(
                        public_demo_disclosure_acceptance.c.tenant_id == actor.tenant_id,
                        public_demo_disclosure_acceptance.c.product_version_id == version_id,
                        public_demo_disclosure_acceptance.c.policy_version == PUBLIC_DEMO_DISCLOSURE_POLICY_VERSION,
                    )
                )
                .mappings()
                .first()
            )
            if existing is None:
                acceptance_id = uuid4()
                accepted_at = now
                session.execute(
                    public_demo_disclosure_acceptance.insert().values(
                        id=acceptance_id,
                        tenant_id=actor.tenant_id,
                        project_id=version.project_id,
                        product_version_id=version_id,
                        run_id=None,
                        actor_id=actor.actor_id,
                        policy_version=PUBLIC_DEMO_DISCLOSURE_POLICY_VERSION,
                        accepted_at=accepted_at,
                        created_at=now,
                    )
                )
            else:
                acceptance_id = existing["id"]
                accepted_at = existing["accepted_at"]
        return {
            "product_version_id": str(version_id),
            "policy_version": PUBLIC_DEMO_DISCLOSURE_POLICY_VERSION,
            "accepted": True,
            "acceptance_id": str(acceptance_id),
            "accepted_at": accepted_at.isoformat(),
            "correlation_id": str(correlation_id),
        }

    def diagnose_gaps(
        self, actor: Actor, version_id: UUID, correlation_id: UUID
    ) -> tuple[ProductProfileDraft, tuple[GapQuestion, ...]]:
        with self._transaction(actor) as session:
            version = self._version(session, actor, version_id)
            self.identity.require_workspace_role_in_session(session, actor, version.workspace_id)
            has_material = session.execute(
                select(material.c.id)
                .where(
                    material.c.tenant_id == actor.tenant_id,
                    material.c.product_version_id == version_id,
                    material.c.ingest_status == "VALIDATED",
                )
                .limit(1)
            ).first()
            if has_material is None:
                raise IntakeValidationError(
                    "at least one validated quarantined material is required before gap diagnosis"
                )
            draft_row = (
                session.execute(
                    select(product_profile_draft).where(
                        product_profile_draft.c.tenant_id == actor.tenant_id,
                        product_profile_draft.c.product_version_id == version_id,
                    )
                )
                .mappings()
                .first()
            )
            if draft_row is not None and draft_row["status"] == ProfileStatus.CONFIRMED.value:
                return (
                    ProductProfileDraft(
                        draft_row["id"],
                        version_id,
                        dict(draft_row["inferred_fields"]),
                        source=draft_row["source"],
                        status=ProfileStatus.CONFIRMED,
                        answers=dict(draft_row["answered_fields"]),
                    ),
                    (),
                )
            draft_id = draft_row["id"] if draft_row is not None else uuid4()
            inferred: dict[str, str | None] = {field: None for field in _REQUIRED_FIELDS}
            if draft_row is None:
                session.execute(
                    product_profile_draft.insert().values(
                        id=draft_id,
                        tenant_id=actor.tenant_id,
                        product_version_id=version_id,
                        source="MODEL_INFERENCE",
                        inferred_fields=inferred,
                        answered_fields={},
                        status=ProfileStatus.DRAFT.value,
                    )
                )
            else:
                session.execute(
                    update(product_profile_draft)
                    .where(product_profile_draft.c.id == draft_id, product_profile_draft.c.tenant_id == actor.tenant_id)
                    .values(
                        inferred_fields=inferred,
                        answered_fields={},
                        status=ProfileStatus.DRAFT.value,
                        confirmed_at=None,
                    )
                )
                session.execute(
                    intake_gap_question.delete().where(
                        intake_gap_question.c.tenant_id == actor.tenant_id,
                        intake_gap_question.c.product_version_id == version_id,
                    )
                )
            questions = tuple(
                GapQuestion(uuid4(), version_id, field, _QUESTION_TEXT[field], priority, correlation_id)
                for priority, field in enumerate(_REQUIRED_FIELDS, start=1)
            )
            for question in questions:
                session.execute(
                    intake_gap_question.insert().values(
                        id=question.question_id,
                        tenant_id=actor.tenant_id,
                        product_version_id=version_id,
                        draft_id=draft_id,
                        correlation_id=correlation_id,
                        field=question.field,
                        question=question.question,
                        priority=question.priority,
                    )
                )
        return ProductProfileDraft(draft_id, version_id, inferred), questions

    def answer_gaps(
        self, actor: Actor, version_id: UUID, correlation_id: UUID, answers: dict[str, str]
    ) -> ProductProfileDraft:
        with self._transaction(actor) as session:
            version = self._version(session, actor, version_id)
            self.identity.require_workspace_role_in_session(session, actor, version.workspace_id)
            draft = self._draft(session, actor, version_id)
            questions = (
                session.execute(
                    select(intake_gap_question)
                    .where(
                        intake_gap_question.c.tenant_id == actor.tenant_id,
                        intake_gap_question.c.product_version_id == version_id,
                    )
                    .order_by(intake_gap_question.c.priority)
                )
                .mappings()
                .all()
            )
            if not questions or any(row["correlation_id"] != correlation_id for row in questions):
                raise IntakeValidationError("answers must use the active gap-question correlation_id")
            allowed = {row["field"] for row in questions}
            if not answers or set(answers) - allowed:
                raise IntakeValidationError("answers contain an unknown gap-question field")
            merged = dict(draft["answered_fields"])
            for field, answer in answers.items():
                normalized = _required(answer, f"answer for {field}", 2000)
                merged[field] = normalized
                session.execute(
                    update(intake_gap_question)
                    .where(
                        intake_gap_question.c.tenant_id == actor.tenant_id,
                        intake_gap_question.c.product_version_id == version_id,
                        intake_gap_question.c.field == field,
                    )
                    .values(answer=normalized, answered_by=actor.actor_id, answered_at=func.now())
                )
            session.execute(
                update(product_profile_draft)
                .where(product_profile_draft.c.id == draft["id"], product_profile_draft.c.tenant_id == actor.tenant_id)
                .values(answered_fields=merged)
            )
        return ProductProfileDraft(draft["id"], version_id, dict(draft["inferred_fields"]), answers=merged)

    def confirm_profile(
        self, actor: Actor, version_id: UUID, acknowledge_model_inference: bool
    ) -> ConfirmedProductProfile:
        with self._transaction(actor) as session:
            version = self._version(session, actor, version_id)
            self.identity.require_workspace_role_in_session(session, actor, version.workspace_id)
            draft = self._draft(session, actor, version_id)
            if not acknowledge_model_inference:
                raise IntakeValidationError(
                    "the user must acknowledge that the draft is model inference, not a confirmed fact"
                )
            answers = dict(draft["answered_fields"])
            missing = [field for field in _REQUIRED_FIELDS if not answers.get(field)]
            if missing:
                raise IntakeValidationError(
                    f"cannot confirm ProductProfile while required answers are missing: {', '.join(missing)}"
                )
            existing = (
                session.execute(
                    select(product_profile)
                    .where(
                        product_profile.c.tenant_id == actor.tenant_id,
                        product_profile.c.product_version_id == version_id,
                    )
                    .order_by(product_profile.c.confirmed_at.desc(), product_profile.c.id.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if existing is not None:
                return ConfirmedProductProfile(
                    existing["id"], version_id, existing["confirmed_by"], dict(existing["confirmed_fields"])
                )
            profile_id = uuid4()
            session.execute(
                product_profile.insert().values(
                    id=profile_id,
                    tenant_id=actor.tenant_id,
                    product_version_id=version_id,
                    confirmed_fields=answers,
                    confirmation_status="CONFIRMED",
                    confirmed_by=actor.actor_id,
                )
            )
            session.execute(
                update(product_profile_draft)
                .where(product_profile_draft.c.id == draft["id"], product_profile_draft.c.tenant_id == actor.tenant_id)
                .values(status=ProfileStatus.CONFIRMED.value, confirmed_at=func.now())
            )
        return ConfirmedProductProfile(profile_id, version_id, actor.actor_id, answers)

    def plan(
        self,
        actor: Actor,
        version_id: UUID,
        correlation_id: UUID,
        *,
        locale: str = "zh-CN",
        evaluation_mode: str | None = None,
    ) -> PersistentRun:
        key = f"plan:{version_id}:{correlation_id}"
        with self._transaction(actor) as session:
            version = self._version(session, actor, version_id)
            self.identity.require_workspace_role_in_session(session, actor, version.workspace_id)
            confirmed = (
                session.execute(
                    select(product_profile)
                    .where(
                        product_profile.c.tenant_id == actor.tenant_id,
                        product_profile.c.product_version_id == version_id,
                        product_profile.c.confirmation_status == "CONFIRMED",
                    )
                    .order_by(product_profile.c.confirmed_at.desc(), product_profile.c.id.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if confirmed is None:
                raise IntakeValidationError("ProductProfile must be user-confirmed before a run may enter PLANNED")
            stage = str((confirmed["confirmed_fields"] or {}).get("stage") or "")
            try:
                mode = evaluation_mode_for_stage(stage, requested_mode=evaluation_mode)
            except StageAdmissionError as exc:
                raise IntakeValidationError(str(exc)) from exc
            material_v2 = material_routing_enabled()
            selection = None
            if material_v2:
                selection = (
                    session.execute(
                        select(material_selection)
                        .where(
                            material_selection.c.tenant_id == actor.tenant_id,
                            material_selection.c.product_version_id == version_id,
                        )
                        .order_by(material_selection.c.revision.desc())
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
                if selection is None:
                    raise IntakeValidationError(
                        "MaterialSelectionSnapshot must be confirmed before a material-routing Run may enter PLANNED"
                    )
            existing = (
                session.execute(
                    select(evaluation_run.c.id, evaluation_run.c.status).where(
                        evaluation_run.c.tenant_id == actor.tenant_id,
                        evaluation_run.c.idempotency_key == key,
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                return PersistentRun(existing["id"], RunStatus(existing["status"]))
            if material_v2 and selection is not None:
                material_rows = (
                    session.execute(
                        select(material.c.id, material.c.sha256)
                        .join(
                            material_selection_item,
                            (material_selection_item.c.tenant_id == material.c.tenant_id)
                            & (material_selection_item.c.material_id == material.c.id),
                        )
                        .where(
                            material_selection_item.c.tenant_id == actor.tenant_id,
                            material_selection_item.c.selection_id == selection["id"],
                            material_selection_item.c.decision.in_(("INCLUDE", "INCLUDE_PARTIAL")),
                        )
                        .order_by(material.c.sha256, material.c.id)
                    )
                    .mappings()
                    .all()
                )
            else:
                material_rows = (
                    session.execute(
                        select(material.c.id, material.c.sha256)
                        .where(
                            material.c.tenant_id == actor.tenant_id,
                            material.c.product_version_id == version_id,
                            material.c.ingest_status == "VALIDATED",
                        )
                        .order_by(material.c.sha256, material.c.id)
                    )
                    .mappings()
                    .all()
                )
            script = (
                session.execute(
                    select(
                        user_validation_script.c.id,
                        user_validation_script.c.revision,
                        user_validation_script.c.sha256,
                    )
                    .where(
                        user_validation_script.c.tenant_id == actor.tenant_id,
                        user_validation_script.c.product_version_id == version_id,
                    )
                    .order_by(user_validation_script.c.revision.desc(), user_validation_script.c.id.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            snapshot = {
                "project_id": str(version.project_id),
                "product_version_id": str(version_id),
                "confirmed_product_profile": dict(confirmed["confirmed_fields"]),
                "material_selection": (
                    {
                        "selection_id": str(selection["id"]),
                        "revision": int(selection["revision"]),
                        "sha256": str(selection["sha256"]),
                    }
                    if selection is not None
                    else None
                ),
                "included_materials": [
                    {"material_id": str(row["id"]), "sha256": str(row["sha256"])} for row in material_rows
                ],
                "user_validation_script": (
                    {
                        "script_id": str(script["id"]),
                        "revision": int(script["revision"]),
                        "sha256": str(script["sha256"]),
                    }
                    if script is not None
                    else None
                ),
                "evaluation_mode": mode,
            }
            report_v2 = report_v2_enabled()
            active_report_profile = report_profile_ref()
            snapshot_sha = input_snapshot_sha256(snapshot) if report_v2 else None
            fingerprint_sha = content_fingerprint_sha256(snapshot) if report_v2 else None
            standard_version = REPORT_STANDARD_VERSION if report_v2 else "1.0"
            binding = (
                select_baseline(
                    session,
                    tenant_id=actor.tenant_id,
                    project_id=version.project_id,
                    candidate_content_fingerprint_sha256=fingerprint_sha,
                    candidate_standard_version=standard_version,
                    candidate_report_profile_ref=active_report_profile,
                )
                if report_v2 and fingerprint_sha is not None
                else None
            )
            run_id = uuid4()
            scope = TenantScope(
                actor.tenant_id,
                workspace_id=version.workspace_id,
                project_id=version.project_id,
                product_version_id=version_id,
                run_id=run_id,
            )
            session.execute(
                evaluation_run.insert().values(
                    id=run_id,
                    tenant_id=actor.tenant_id,
                    project_id=version.project_id,
                    product_version_id=version_id,
                    status=RunStatus.PLANNED.value,
                    current_stage=None,
                    state_flags={
                        "gap_identified": True,
                        "profile_confirmed": True,
                        "architecture_generation": (
                            "supervisor-1p4-report-v3"
                            if report_v3_enabled()
                            else "supervisor-1p4-report-v22"
                            if report_v2
                            else "supervisor-1p4-material-routing-v2"
                            if material_v2
                            else "supervisor-1p4-v1"
                        ),
                        "locale": "en" if locale == "en" else "zh-CN",
                        "audience": "student",
                        "tone": "clear_concise_practical",
                        "evaluation_mode": mode,
                        "report_comparison_status": binding.status if binding is not None else None,
                    },
                    standard_version=standard_version,
                    correlation_id=correlation_id,
                    idempotency_key=key,
                    baseline_run_id=binding.baseline_run_id if binding is not None else None,
                    input_snapshot_sha256=snapshot_sha,
                    content_fingerprint_sha256=fingerprint_sha,
                    report_profile_ref=active_report_profile if report_v2 else None,
                )
            )
            session.execute(
                update(public_demo_disclosure_acceptance)
                .where(
                    public_demo_disclosure_acceptance.c.tenant_id == actor.tenant_id,
                    public_demo_disclosure_acceptance.c.product_version_id == version_id,
                    public_demo_disclosure_acceptance.c.policy_version == PUBLIC_DEMO_DISCLOSURE_POLICY_VERSION,
                    public_demo_disclosure_acceptance.c.run_id.is_(None),
                )
                .values(run_id=run_id)
            )
            for from_status, to_status, reason in (
                (RunStatus.DRAFT.value, RunStatus.INTAKE.value, "authorized intake"),
                (RunStatus.INTAKE.value, RunStatus.WAITING_FOR_USER.value, "gap identified"),
                (RunStatus.WAITING_FOR_USER.value, RunStatus.PLANNED.value, "profile confirmed"),
            ):
                session.execute(
                    run_status_history.insert().values(
                        id=uuid4(),
                        tenant_id=actor.tenant_id,
                        run_id=run_id,
                        from_status=from_status,
                        to_status=to_status,
                        reason=reason,
                    )
                )
            event = EventEnvelope.create(
                EventType.EVALUATION_RUN_STARTED,
                scope,
                CorrelationContext(correlation_id, idempotency_key=key),
                {
                    "project_id": str(version.project_id),
                    "product_version_id": str(version_id),
                    "status": RunStatus.PLANNED.value,
                },
            )
            OutboxRepository(session).enqueue(event, aggregate_id=run_id, aggregate_type="evaluation_run", scope=scope)
        return PersistentRun(run_id, RunStatus.PLANNED)

    def _project(self, session: Session, actor: Actor, project_id: UUID) -> RowMapping:
        row = (
            session.execute(select(project).where(project.c.id == project_id, project.c.tenant_id == actor.tenant_id))
            .mappings()
            .first()
        )
        if row is None:
            raise NotFoundError("project was not found")
        return row

    def _version(self, session: Session, actor: Actor, version_id: UUID) -> ProductVersion:
        row = (
            session.execute(
                select(
                    product_version.c.id,
                    product_version.c.project_id,
                    product_version.c.version_number,
                    product_version.c.label,
                    project.c.workspace_id,
                )
                .join(
                    project,
                    (project.c.id == product_version.c.project_id)
                    & (project.c.tenant_id == product_version.c.tenant_id),
                )
                .where(product_version.c.id == version_id, product_version.c.tenant_id == actor.tenant_id)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFoundError("product version was not found")
        return ProductVersion(
            row["id"], actor.tenant_id, row["workspace_id"], row["project_id"], row["version_number"], row["label"]
        )

    def _draft(self, session: Session, actor: Actor, version_id: UUID) -> RowMapping:
        row = (
            session.execute(
                select(product_profile_draft).where(
                    product_profile_draft.c.tenant_id == actor.tenant_id,
                    product_profile_draft.c.product_version_id == version_id,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise IntakeValidationError("gap diagnosis has not generated a ProductProfile draft")
        return row

    def _transaction(self, actor: Actor) -> AbstractContextManager[Session]:
        return tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id)


__all__ = [
    "PUBLIC_DEMO_DISCLOSURE_POLICY_VERSION",
    "PersistentIdentityTenantApplication",
    "PersistentProjectDossierApplication",
    "PersistentRun",
]
