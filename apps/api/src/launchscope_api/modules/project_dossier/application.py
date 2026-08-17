"""T5 project and product-version control-plane orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from launchscope_api.modules.evaluation.intake_application import GapQuestion, IntakeApplication, IntakeValidationError
from launchscope_api.modules.identity_tenant.application import (
    Actor,
    AuthorizationError,
    IdentityTenantApplication,
    NotFoundError,
    WorkspaceRole,
)
from launchscope_api.modules.supervisor.stage_admission import StageAdmissionError, evaluation_mode_for_stage
from launchscope_domain import EvaluationRun

from .material_ingestion import MaterialIngestionApplication, MaterialRecord, UploadInitiation
from .profile_confirmation import ConfirmedProductProfile, ProductProfileDraft


@dataclass(frozen=True, slots=True)
class Project:
    project_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class ProductVersion:
    product_version_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    project_id: UUID
    version_number: int
    label: str


@dataclass
class ProjectDossierApplication:
    identity: IdentityTenantApplication
    materials: MaterialIngestionApplication = field(default_factory=MaterialIngestionApplication)
    intake: IntakeApplication = field(default_factory=IntakeApplication)
    projects: dict[UUID, Project] = field(default_factory=dict)
    versions: dict[UUID, ProductVersion] = field(default_factory=dict)

    def create_project(self, actor: Actor, workspace_id: UUID, name: str) -> Project:
        self.identity.require_workspace_role(actor, workspace_id, WorkspaceRole.EDITOR)
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
            raise ValueError("project name must be a non-empty string up to 200 characters")
        project = Project(uuid4(), actor.tenant_id, workspace_id, name.strip())
        self.projects[project.project_id] = project
        return project

    def create_version(self, actor: Actor, project_id: UUID, label: str) -> ProductVersion:
        project = self._project(actor, project_id)
        self.identity.require_workspace_role(actor, project.workspace_id, WorkspaceRole.EDITOR)
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 100:
            raise ValueError("version label must be a non-empty string up to 100 characters")
        existing_numbers = [item.version_number for item in self.versions.values() if item.project_id == project_id]
        next_number = 1 + max(existing_numbers, default=0)
        version = ProductVersion(uuid4(), actor.tenant_id, project.workspace_id, project_id, next_number, label.strip())
        self.versions[version.product_version_id] = version
        return version

    def initiate_material(self, actor: Actor, version_id: UUID, **metadata: object) -> UploadInitiation:
        version = self._version(actor, version_id)
        self.identity.require_workspace_role(actor, version.workspace_id, WorkspaceRole.EDITOR)
        return self.materials.initiate(
            actor,
            workspace_id=version.workspace_id,
            project_id=version.project_id,
            product_version_id=version.product_version_id,
            **metadata,  # type: ignore[arg-type]
        )

    def complete_material(self, actor: Actor, material_id: UUID) -> MaterialRecord:
        record = self.materials.materials.get(material_id)
        if record is None:
            raise NotFoundError("material was not found")
        if record.tenant_id != actor.tenant_id:
            raise AuthorizationError("material is outside the caller tenant")
        self.identity.require_workspace_role(actor, record.workspace_id, WorkspaceRole.EDITOR)
        return self.materials.complete(actor, material_id)

    def diagnose_gaps(
        self, actor: Actor, version_id: UUID, correlation_id: UUID
    ) -> tuple[ProductProfileDraft, tuple[GapQuestion, ...]]:
        version = self._version(actor, version_id)
        self.identity.require_workspace_role(actor, version.workspace_id, WorkspaceRole.EDITOR)
        return self.intake.generate_draft_and_questions(
            actor,
            version_id,
            has_validated_material=bool(self.materials.list_validated(actor, version_id)),
            correlation_id=correlation_id,
        )

    def answer_gaps(
        self, actor: Actor, version_id: UUID, correlation_id: UUID, answers: dict[str, str]
    ) -> ProductProfileDraft:
        version = self._version(actor, version_id)
        self.identity.require_workspace_role(actor, version.workspace_id, WorkspaceRole.EDITOR)
        return self.intake.answer_questions(version_id, correlation_id, answers)

    def confirm_profile(
        self, actor: Actor, version_id: UUID, acknowledge_model_inference: bool
    ) -> ConfirmedProductProfile:
        version = self._version(actor, version_id)
        self.identity.require_workspace_role(actor, version.workspace_id, WorkspaceRole.EDITOR)
        return self.intake.confirm_profile(actor, version_id, acknowledge_model_inference=acknowledge_model_inference)

    def plan(
        self,
        actor: Actor,
        version_id: UUID,
        correlation_id: UUID,
        *,
        locale: str = "zh-CN",
        evaluation_mode: str | None = None,
    ) -> EvaluationRun:
        version = self._version(actor, version_id)
        self.identity.require_workspace_role(actor, version.workspace_id, WorkspaceRole.EDITOR)
        profile = self.intake.confirmed.get(version.product_version_id)
        if profile is not None and evaluation_mode is not None:
            try:
                evaluation_mode_for_stage(profile.fields["stage"], requested_mode=evaluation_mode)
            except StageAdmissionError as exc:
                raise IntakeValidationError(str(exc)) from exc
        return self.intake.enter_planned(
            actor,
            workspace_id=version.workspace_id,
            project_id=version.project_id,
            product_version_id=version.product_version_id,
            correlation_id=correlation_id,
        )

    def _project(self, actor: Actor, project_id: UUID) -> Project:
        project = self.projects.get(project_id)
        if project is None or project.tenant_id != actor.tenant_id:
            raise NotFoundError("project was not found")
        return project

    def _version(self, actor: Actor, version_id: UUID) -> ProductVersion:
        version = self.versions.get(version_id)
        if version is None or version.tenant_id != actor.tenant_id:
            raise NotFoundError("product version was not found")
        return version


__all__ = ["ProductVersion", "Project", "ProjectDossierApplication"]
