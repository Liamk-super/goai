"""Generation-v4 requirement normalization and the single supervisor chat write boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    evaluation_run,
    material_selection,
    product_profile,
    product_version,
    requirement_brief,
    requirement_change,
    run_manifest,
    run_status_history,
    supervisor_chat_message,
    task,
    user_validation_script,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.infrastructure.object_store import (
    ObjectStoreConfigurationError,
    ObjectStoreIntegrityError,
    S3QuarantineObjectStore,
)
from launchscope_api.modules.evaluation.intake_application import IntakeValidationError
from launchscope_api.modules.identity_tenant.application import Actor, NotFoundError
from launchscope_api.modules.project_dossier.material_analysis import material_routing_enabled
from launchscope_domain.value_objects import TenantScope

from .baseline_application import report_v2_enabled, report_v3_enabled
from .generation import is_supervisor_generation

_EVALUATION_MODES = frozenset({"FULL_POTENTIAL", "INVESTMENT_REVIEW", "LAUNCH_REVIEW", "USER_VALIDATION"})
_CRITICAL_FACTS = {
    "FULL_POTENTIAL": frozenset({"target_user", "region", "validation_goal"}),
    "INVESTMENT_REVIEW": frozenset({"region", "validation_goal"}),
    "LAUNCH_REVIEW": frozenset({"target_user", "region", "stage"}),
    "USER_VALIDATION": frozenset({"target_user", "validation_goal"}),
}
_QUESTION_TEXT = {
    "target_user": "Who is the primary target user for this review?",
    "region": "Which country or region should the review cover?",
    "validation_goal": "What decision should this review help you make?",
    "stage": "What is the product's current delivery stage?",
}
_CONFIRMED_PROFILE_FIELDS = (
    "one_line_value_claim",
    "target_user",
    "payer",
    "region",
    "stage",
    "validation_goal",
)


class BriefObjectStore(Protocol):
    def put_private(self, object_key: str, payload: bytes, mime_type: str) -> str: ...

    def get_private(self, object_key: str, *, max_bytes: int = 2_000_000) -> bytes: ...


def supervisor_1p4_enabled() -> bool:
    return os.getenv("LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED", "false").strip().lower() in {"1", "true", "yes"}


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _explicit_validation_goal(content: str) -> str | None:
    markers = ("本轮评审要判断", "本轮最想验证", "请判断", "验证目标", "decision this review")
    for sentence in re.split(r"(?<=[。！？!?])", content):
        candidate = sentence.strip()
        if candidate and any(marker in candidate.casefold() for marker in markers):
            return candidate
    return None


@dataclass(frozen=True, slots=True)
class NormalizedRequirement:
    document: dict[str, Any]
    questions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SupervisorChatResult:
    message_id: UUID
    brief_id: UUID
    brief_revision: int
    interaction_state: str
    confirmation_required: bool
    questions: tuple[str, ...]
    duplicate: bool = False


class ConfirmedProfileBriefBuilder:
    """Create one planning-ready brief from already-confirmed durable inputs."""

    def __init__(self, sessions: sessionmaker[Session], objects: BriefObjectStore | None = None) -> None:
        self._sessions = sessions
        self._objects = objects

    def ensure_ready(self, actor: Actor, run_id: UUID) -> UUID:
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            run = (
                session.execute(
                    select(evaluation_run)
                    .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if run is None:
                raise NotFoundError("run was not found")
            if run["status"] not in {"PLANNED", "RUNNING"}:
                raise IntakeValidationError("automatic planning requires a PLANNED or already RUNNING Run")
            state_flags = dict(run["state_flags"] or {})
            evaluation_mode = str(state_flags.get("evaluation_mode", "FULL_POTENTIAL"))
            if evaluation_mode not in _EVALUATION_MODES:
                raise IntakeValidationError("the planned Run has an unsupported evaluation mode")
            latest_brief = (
                session.execute(
                    select(requirement_brief)
                    .where(
                        requirement_brief.c.tenant_id == actor.tenant_id,
                        requirement_brief.c.product_version_id == run["product_version_id"],
                    )
                    .order_by(requirement_brief.c.revision.desc())
                )
                .mappings()
                .first()
            )
            if latest_brief is not None:
                if latest_brief["status"] == "READY_FOR_PLANNING":
                    return latest_brief["id"]
                if latest_brief["status"] == "WAITING_FOR_USER":
                    raise IntakeValidationError(
                        "a pending RequirementBrief still requires confirmation; automatic planning remains closed"
                    )

            profile = (
                session.execute(
                    select(product_profile)
                    .where(
                        product_profile.c.tenant_id == actor.tenant_id,
                        product_profile.c.product_version_id == run["product_version_id"],
                        product_profile.c.confirmation_status == "CONFIRMED",
                    )
                    .order_by(product_profile.c.confirmed_at.desc(), product_profile.c.created_at.desc())
                )
                .mappings()
                .first()
            )
            if profile is None:
                raise IntakeValidationError("the Product Profile has not been confirmed")
            fields = profile["confirmed_fields"]
            if not isinstance(fields, dict):
                raise IntakeValidationError("the confirmed Product Profile is invalid")
            explicit_facts: dict[str, str] = {}
            for field in _CONFIRMED_PROFILE_FIELDS:
                value = fields.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise IntakeValidationError(f"the confirmed Product Profile is missing {field}")
                explicit_facts[field] = value.strip()

            script = (
                session.execute(
                    select(user_validation_script)
                    .where(
                        user_validation_script.c.tenant_id == actor.tenant_id,
                        user_validation_script.c.product_version_id == run["product_version_id"],
                    )
                    .order_by(user_validation_script.c.revision.desc())
                )
                .mappings()
                .first()
            )
            if script is None:
                raise IntakeValidationError("the Product Validation Script has not been confirmed")
            if self._objects is None:
                self._objects = S3QuarantineObjectStore.from_env()
            try:
                script_body = self._objects.get_private(script["object_key"], max_bytes=1_000_000)
            except (ObjectStoreConfigurationError, ObjectStoreIntegrityError) as exc:
                raise IntakeValidationError(
                    "the confirmed Product Validation Script private object is unavailable"
                ) from exc
            if hashlib.sha256(script_body).hexdigest() != script["sha256"]:
                raise IntakeValidationError("the Product Validation Script digest does not match its durable record")
            try:
                script_document = json.loads(script_body)
            except (TypeError, ValueError) as exc:
                raise IntakeValidationError("the Product Validation Script object is invalid JSON") from exc
            tasks = script_document.get("tasks") if isinstance(script_document, dict) else None
            if not isinstance(tasks, list) or len(tasks) != script["task_count"] or not 1 <= len(tasks) <= 5:
                raise IntakeValidationError("the Product Validation Script task count is invalid")
            outcomes: list[str] = []
            for item in tasks:
                outcome = item.get("expected_observable_outcome") if isinstance(item, dict) else None
                if not isinstance(outcome, str) or not outcome.strip():
                    raise IntakeValidationError("every validation task requires an observable outcome")
                outcomes.append(outcome.strip())
            if script_document.get("product_tasks_hash") != script["product_tasks_sha256"]:
                raise IntakeValidationError("the Product Validation Script task hash does not match its durable record")

            snapshot = {
                "schema_version": "1.0",
                "evaluation_mode": evaluation_mode,
                "product_profile": {
                    "id": str(profile["id"]),
                    "confirmed_fields": explicit_facts,
                    "confirmed_at": profile["confirmed_at"].isoformat(),
                },
                "user_validation_script": {
                    "id": str(script["id"]),
                    "revision": script["revision"],
                    "sha256": script["sha256"],
                    "product_tasks_sha256": script["product_tasks_sha256"],
                    "tasks": tasks,
                },
            }
            material_v2 = material_routing_enabled()
            if material_v2:
                selection = (
                    session.execute(
                        select(material_selection)
                        .where(
                            material_selection.c.tenant_id == actor.tenant_id,
                            material_selection.c.product_version_id == run["product_version_id"],
                        )
                        .order_by(material_selection.c.revision.desc())
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
                if selection is None:
                    raise IntakeValidationError(
                        "a confirmed MaterialSelectionSnapshot is required before RequirementBrief freezing"
                    )
                snapshot["material_selection"] = {
                    "id": str(selection["id"]),
                    "revision": int(selection["revision"]),
                    "object_key": str(selection["object_key"]),
                    "sha256": str(selection["sha256"]),
                    "confirmed_by": str(selection["confirmed_by"]),
                    "confirmed_at": selection["confirmed_at"].isoformat(),
                }
            raw = _canonical(snapshot)
            raw_sha = hashlib.sha256(raw).hexdigest()
            object_key = (
                f"tenants/{actor.tenant_id}/product-versions/{run['product_version_id']}"
                f"/requirement-inputs/{raw_sha}.json"
            )
            try:
                stored_sha = self._objects.put_private(object_key, raw, "application/json")
            except (ObjectStoreConfigurationError, ObjectStoreIntegrityError) as exc:
                raise IntakeValidationError("the confirmed requirement snapshot could not be stored privately") from exc
            if stored_sha != raw_sha:
                raise RuntimeError("object store did not preserve the confirmed requirement snapshot digest")

            revision = (
                int(
                    session.execute(
                        select(func.coalesce(func.max(requirement_brief.c.revision), 0)).where(
                            requirement_brief.c.tenant_id == actor.tenant_id,
                            requirement_brief.c.product_version_id == run["product_version_id"],
                        )
                    ).scalar_one()
                )
                + 1
            )
            brief_id = uuid4()
            document = {
                "schema_version": "1.0",
                "brief_id": str(brief_id),
                "tenant_id": str(actor.tenant_id),
                "product_version_id": str(run["product_version_id"]),
                "revision": revision,
                "raw_input_ref": {"object_key": object_key, "sha256": raw_sha},
                "normalized_goal": explicit_facts["validation_goal"],
                "evaluation_mode": evaluation_mode,
                "requested_deliverables": [
                    "initial_validation_report"
                    if evaluation_mode == "USER_VALIDATION"
                    else "potential_evaluation_report"
                ],
                "constraints": (
                    [
                        "This is an early-stage preliminary prediction. Do not present it as a full-potential score; "
                        "prioritize assumptions, evidence gaps, and next validation actions."
                    ]
                    if evaluation_mode == "USER_VALIDATION"
                    else []
                ),
                "success_criteria": list(dict.fromkeys(outcomes)),
                "validation_tasks": tasks,
                "explicit_facts": explicit_facts,
                "assumptions": [],
                "unknowns": [],
                "confidence": {
                    "overall": 1.0,
                    "fields": {field: 1.0 for field in _CONFIRMED_PROFILE_FIELDS},
                },
                "confirmation_required": False,
                "confirmation_reasons": [],
                "change_classification": "INITIAL",
            }
            session.execute(
                requirement_brief.insert().values(
                    id=brief_id,
                    tenant_id=actor.tenant_id,
                    product_version_id=run["product_version_id"],
                    revision=revision,
                    schema_version="1.0",
                    raw_input_object_key=object_key,
                    raw_input_sha256=raw_sha,
                    document=document,
                    confirmation_required=False,
                    status="READY_FOR_PLANNING",
                    created_by=actor.actor_id,
                    created_at=now,
                    confirmed_at=now,
                )
            )
            state_flags = dict(run.get("state_flags") or {})
            state_flags["architecture_generation"] = (
                "supervisor-1p4-report-v3"
                if report_v3_enabled()
                else "supervisor-1p4-report-v22"
                if report_v2_enabled()
                else "supervisor-1p4-material-routing-v2"
                if material_v2
                else "supervisor-1p4-v1"
            )
            session.execute(
                update(evaluation_run)
                .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                .values(state_flags=state_flags, updated_at=now)
            )
            return brief_id


class RequirementBriefNormalizer:
    """Convert one bounded Intake Model proposal into an evidence-grounded RequirementBriefV1."""

    def normalize(
        self,
        *,
        tenant_id: UUID,
        product_version_id: UUID,
        raw_content: str,
        raw_object_key: str,
        raw_sha256: str,
        revision: int,
        model_output: dict[str, Any],
        brief_id: UUID | None = None,
    ) -> NormalizedRequirement:
        content = raw_content.strip()
        if not content or len(content) > 30_000:
            raise IntakeValidationError("supervisor intake message must contain 1 to 30000 characters")
        mode = str(model_output.get("evaluation_mode", ""))
        if mode not in _EVALUATION_MODES:
            raise IntakeValidationError("Intake Model must return a supported evaluation_mode")
        goal = str(model_output.get("normalized_goal", "")).strip()
        if not goal or _normalized_text(goal) not in _normalized_text(content):
            raise IntakeValidationError("normalized_goal must be an exact user-expressed span")
        explicit_facts = model_output.get("explicit_facts", {})
        if not isinstance(explicit_facts, dict) or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or not value.strip()
            or _normalized_text(value) not in _normalized_text(content)
            for key, value in explicit_facts.items()
        ):
            raise IntakeValidationError("every explicit fact must be grounded in an exact user-expressed span")
        explicit_facts = dict(explicit_facts)
        if "validation_goal" not in explicit_facts:
            validation_goal = _explicit_validation_goal(content)
            if validation_goal is not None:
                explicit_facts["validation_goal"] = validation_goal
        confidence_fields = model_output.get("confidence_fields", {})
        if not isinstance(confidence_fields, dict):
            raise IntakeValidationError("field confidence must be an object")
        confidence = {
            str(key): float(value)
            for key, value in confidence_fields.items()
            if isinstance(value, (int, float)) and 0 <= float(value) <= 1
        }
        if "validation_goal" in explicit_facts and "validation_goal" not in confidence:
            confidence["validation_goal"] = 0.9
        overall = float(model_output.get("confidence_overall", 0))
        if not 0 <= overall <= 1:
            raise IntakeValidationError("overall confidence must be between 0 and 1")
        assumptions = model_output.get("assumptions", [])
        if not isinstance(assumptions, list) or any(
            not isinstance(item, dict)
            or not isinstance(item.get("field"), str)
            or not isinstance(item.get("value"), str)
            or not isinstance(item.get("material"), bool)
            for item in assumptions
        ):
            raise IntakeValidationError("assumptions must use the RequirementBriefV1 shape")
        unknowns = model_output.get("unknowns", [])
        if not isinstance(unknowns, list) or any(not isinstance(item, str) for item in unknowns):
            raise IntakeValidationError("unknowns must be field identifiers")
        missing_critical = sorted(_CRITICAL_FACTS[mode].difference(explicit_facts))
        low_confidence = sorted(
            field for field in _CRITICAL_FACTS[mode] if field in explicit_facts and confidence.get(field, 0) < 0.8
        )
        material_assumptions = [item for item in assumptions if item["material"]]
        reasons = []
        if missing_critical or low_confidence or overall < 0.8:
            reasons.append("CRITICAL_AMBIGUITY")
        if material_assumptions:
            reasons.append("MODEL_ASSUMPTION")
        if model_output.get("scope_changed") is True:
            reasons.append("SCOPE_CHANGE")
        if model_output.get("cost_changed") is True:
            reasons.append("COST_CHANGE")
        if model_output.get("permission_changed") is True:
            reasons.append("NEW_PERMISSION")
        questions = [_QUESTION_TEXT[field] for field in dict.fromkeys((*missing_critical, *low_confidence))]
        questions.extend(
            f"Please confirm or correct the assumption for {item['field']}: {item['value']}"
            for item in material_assumptions
        )
        document = {
            "schema_version": "1.0",
            "brief_id": str(brief_id or uuid4()),
            "tenant_id": str(tenant_id),
            "product_version_id": str(product_version_id),
            "revision": revision,
            "raw_input_ref": {"object_key": raw_object_key, "sha256": raw_sha256},
            "normalized_goal": goal,
            "evaluation_mode": mode,
            "requested_deliverables": list(
                model_output.get("requested_deliverables") or ["potential_evaluation_report"]
            ),
            "constraints": list(model_output.get("constraints") or []),
            "success_criteria": list(model_output.get("success_criteria") or ["produce a traceable recommendation"]),
            "explicit_facts": {str(key): str(value).strip() for key, value in explicit_facts.items()},
            "assumptions": assumptions,
            "unknowns": [item for item in dict.fromkeys(unknowns) if item not in explicit_facts],
            "confidence": {"overall": overall, "fields": confidence},
            "confirmation_required": bool(reasons),
            "confirmation_reasons": list(dict.fromkeys(reasons)),
            "change_classification": str(model_output.get("change_classification", "INITIAL")),
        }
        return NormalizedRequirement(document, tuple(questions))


class SupervisorChatApplication:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        objects: S3QuarantineObjectStore,
        *,
        normalizer: RequirementBriefNormalizer | None = None,
    ) -> None:
        self._sessions = sessions
        self._objects = objects
        self._normalizer = normalizer or RequirementBriefNormalizer()

    def submit_requirement(
        self,
        actor: Actor,
        project_id: UUID,
        product_version_id: UUID,
        *,
        message: str,
        model_output: dict[str, Any],
        idempotency_key: str,
        correlation_id: UUID,
    ) -> SupervisorChatResult:
        if not supervisor_1p4_enabled():
            raise IntakeValidationError("supervisor 1+4 generation is disabled")
        request_payload = {"message": message, "model_output": model_output}
        request_sha = hashlib.sha256(_canonical(request_payload)).hexdigest()
        raw = message.strip().encode()
        raw_sha = hashlib.sha256(raw).hexdigest()
        object_key = f"tenant/{actor.tenant_id}/product-version/{product_version_id}/supervisor-chat/{raw_sha}.txt"
        stored_sha = self._objects.put_private(object_key, raw, "text/plain; charset=utf-8")
        if stored_sha != raw_sha:
            raise RuntimeError("object store did not preserve the supervisor message digest")
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            duplicate = self._duplicate(session, actor.tenant_id, idempotency_key, request_sha)
            if duplicate is not None:
                return duplicate
            exists = session.execute(
                select(product_version.c.id).where(
                    product_version.c.tenant_id == actor.tenant_id,
                    product_version.c.id == product_version_id,
                    product_version.c.project_id == project_id,
                )
            ).scalar_one_or_none()
            if exists is None:
                raise NotFoundError("product version was not found")
            revision = (
                int(
                    session.execute(
                        select(func.coalesce(func.max(requirement_brief.c.revision), 0)).where(
                            requirement_brief.c.tenant_id == actor.tenant_id,
                            requirement_brief.c.product_version_id == product_version_id,
                        )
                    ).scalar_one()
                )
                + 1
            )
            brief_id = uuid4()
            normalized = self._normalizer.normalize(
                tenant_id=actor.tenant_id,
                product_version_id=product_version_id,
                raw_content=message,
                raw_object_key=object_key,
                raw_sha256=raw_sha,
                revision=revision,
                model_output=model_output,
                brief_id=brief_id,
            )
            state = "WAITING_FOR_USER" if normalized.document["confirmation_required"] else "LEADER_PLANNING"
            status = "WAITING_FOR_USER" if normalized.document["confirmation_required"] else "READY_FOR_PLANNING"
            session.execute(
                requirement_brief.insert().values(
                    id=brief_id,
                    tenant_id=actor.tenant_id,
                    product_version_id=product_version_id,
                    revision=revision,
                    schema_version="1.0",
                    raw_input_object_key=object_key,
                    raw_input_sha256=raw_sha,
                    document=normalized.document,
                    confirmation_required=normalized.document["confirmation_required"],
                    status=status,
                    created_by=actor.actor_id,
                    created_at=now,
                    confirmed_at=None if normalized.document["confirmation_required"] else now,
                )
            )
            message_id = uuid4()
            classification = str(normalized.document["change_classification"])
            session.execute(
                supervisor_chat_message.insert().values(
                    id=message_id,
                    tenant_id=actor.tenant_id,
                    product_version_id=product_version_id,
                    brief_id=brief_id,
                    role="USER",
                    message_kind=(
                        "REQUIREMENT"
                        if revision == 1
                        else "CHANGE"
                        if classification == "REQUIREMENT_CHANGE"
                        else "SUPPLEMENT"
                    ),
                    object_key=object_key,
                    sha256=raw_sha,
                    request_sha256=request_sha,
                    idempotency_key=idempotency_key,
                    correlation_id=correlation_id,
                    interaction_state=state,
                    created_by=actor.actor_id,
                    created_at=now,
                )
            )
            if normalized.questions:
                self._persist_questions(
                    session,
                    actor,
                    product_version_id,
                    brief_id,
                    normalized.questions,
                    request_sha,
                    idempotency_key,
                    correlation_id,
                    now,
                )
            self._sync_planned_run(
                session,
                actor,
                project_id,
                product_version_id,
                waiting=bool(normalized.document["confirmation_required"]),
                now=now,
            )
            self._record_runtime_change(
                session,
                actor,
                project_id,
                product_version_id,
                brief_id,
                normalized.document,
                now,
            )
            return SupervisorChatResult(
                message_id,
                brief_id,
                revision,
                state,
                bool(normalized.document["confirmation_required"]),
                normalized.questions,
            )

    @staticmethod
    def _sync_planned_run(
        session: Session,
        actor: Actor,
        project_id: UUID,
        product_version_id: UUID,
        *,
        waiting: bool,
        now: datetime,
    ) -> None:
        run = (
            session.execute(
                select(evaluation_run)
                .where(
                    evaluation_run.c.tenant_id == actor.tenant_id,
                    evaluation_run.c.project_id == project_id,
                    evaluation_run.c.product_version_id == product_version_id,
                    evaluation_run.c.status.in_(("PLANNED", "WAITING_FOR_USER")),
                )
                .order_by(evaluation_run.c.created_at.desc(), evaluation_run.c.id.desc())
                .limit(1)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if run is None:
            return
        target = "WAITING_FOR_USER" if waiting else "PLANNED"
        if run["status"] == target:
            return
        flags = dict(run["state_flags"] or {})
        flags["waiting_for_user"] = waiting
        session.execute(
            update(evaluation_run)
            .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run["id"])
            .values(
                status=target,
                current_stage="WAITING_FOR_USER" if waiting else None,
                state_flags=flags,
                updated_at=now,
            )
        )
        session.execute(
            run_status_history.insert().values(
                id=uuid4(),
                tenant_id=actor.tenant_id,
                run_id=run["id"],
                from_status=run["status"],
                to_status=target,
                reason="Supervisor intake requires user information" if waiting else "Requirement brief confirmed",
                failure_class=None,
                occurred_at=now,
            )
        )

    @staticmethod
    def _record_runtime_change(
        session: Session,
        actor: Actor,
        project_id: UUID,
        product_version_id: UUID,
        brief_id: UUID,
        brief: dict[str, Any],
        now: datetime,
    ) -> None:
        run = (
            session.execute(
                select(evaluation_run, run_manifest.c.frozen_config)
                .join(
                    run_manifest,
                    (run_manifest.c.tenant_id == evaluation_run.c.tenant_id)
                    & (run_manifest.c.run_id == evaluation_run.c.id),
                )
                .where(
                    evaluation_run.c.tenant_id == actor.tenant_id,
                    evaluation_run.c.project_id == project_id,
                    evaluation_run.c.product_version_id == product_version_id,
                    evaluation_run.c.status == "RUNNING",
                )
                .order_by(evaluation_run.c.created_at.desc(), evaluation_run.c.id.desc())
                .limit(1)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if run is None or not is_supervisor_generation(run["frozen_config"].get("architecture_generation")):
            return
        affected = [
            str(value)
            for value in session.execute(
                select(task.c.id).where(
                    task.c.tenant_id == actor.tenant_id,
                    task.c.run_id == run["id"],
                    task.c.status.in_(("PENDING", "READY", "BLOCKED")),
                    task.c.agent_identity_ref.like("%@4.0"),
                )
            ).scalars()
        ]
        reasons = set(brief.get("confirmation_reasons") or [])
        scope_changed = "SCOPE_CHANGE" in reasons
        cost_changed = "COST_CHANGE" in reasons
        permission_changed = "NEW_PERMISSION" in reasons
        material = scope_changed or cost_changed or permission_changed
        classification = str(brief.get("change_classification") or "SUPPLEMENT")
        if classification == "INITIAL":
            classification = "SUPPLEMENT"
        document = {
            "schema_version": "1.0",
            "change_id": str(uuid4()),
            "run_id": str(run["id"]),
            "brief_id": str(brief_id),
            "classification": classification,
            "affected_task_ids": affected,
            "scope_changed": scope_changed,
            "cost_changed": cost_changed,
            "permission_changed": permission_changed,
            "confirmation_required": material,
            "reason": "Runtime message recorded for only the not-started generation-v4 tasks.",
        }
        session.execute(
            requirement_change.insert().values(
                id=UUID(str(document["change_id"])),
                tenant_id=actor.tenant_id,
                run_id=run["id"],
                brief_id=brief_id,
                document=document,
                status="PROPOSED" if material else "APPLIED",
                created_by=actor.actor_id,
                created_at=now,
            )
        )
        if not material:
            return
        flags = dict(run["state_flags"] or {})
        flags["waiting_for_approval"] = True
        session.execute(
            update(evaluation_run)
            .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run["id"])
            .values(status="WAITING_FOR_APPROVAL", state_flags=flags, updated_at=now)
        )
        session.execute(
            run_status_history.insert().values(
                id=uuid4(),
                tenant_id=actor.tenant_id,
                run_id=run["id"],
                from_status="RUNNING",
                to_status="WAITING_FOR_APPROVAL",
                reason="Material runtime requirement change requires confirmation",
                failure_class=None,
                occurred_at=now,
            )
        )

    def _duplicate(
        self, session: Session, tenant_id: UUID, idempotency_key: str, request_sha: str
    ) -> SupervisorChatResult | None:
        row = (
            session.execute(
                select(
                    supervisor_chat_message.c.id,
                    supervisor_chat_message.c.brief_id,
                    supervisor_chat_message.c.request_sha256,
                    supervisor_chat_message.c.interaction_state,
                    requirement_brief.c.revision,
                    requirement_brief.c.confirmation_required,
                    requirement_brief.c.document,
                )
                .join(
                    requirement_brief,
                    (requirement_brief.c.tenant_id == supervisor_chat_message.c.tenant_id)
                    & (requirement_brief.c.id == supervisor_chat_message.c.brief_id),
                )
                .where(
                    supervisor_chat_message.c.tenant_id == tenant_id,
                    supervisor_chat_message.c.idempotency_key == idempotency_key,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        if row["request_sha256"] != request_sha:
            raise ValueError("IDEMPOTENCY_CONFLICT")
        questions = tuple(f"Please resolve {reason}." for reason in row["document"].get("confirmation_reasons", []))
        return SupervisorChatResult(
            row["id"],
            row["brief_id"],
            row["revision"],
            row["interaction_state"],
            row["confirmation_required"],
            questions,
            duplicate=True,
        )

    def _persist_questions(
        self,
        session: Session,
        actor: Actor,
        product_version_id: UUID,
        brief_id: UUID,
        questions: tuple[str, ...],
        request_sha: str,
        idempotency_key: str,
        correlation_id: UUID,
        now: datetime,
    ) -> None:
        body = _canonical({"brief_id": str(brief_id), "questions": list(questions)})
        digest = hashlib.sha256(body).hexdigest()
        key = f"tenant/{actor.tenant_id}/product-version/{product_version_id}/supervisor-chat/{digest}.json"
        if self._objects.put_private(key, body, "application/json") != digest:
            raise RuntimeError("object store did not preserve the clarification digest")
        session.execute(
            supervisor_chat_message.insert().values(
                id=uuid4(),
                tenant_id=actor.tenant_id,
                product_version_id=product_version_id,
                brief_id=brief_id,
                role="SUPERVISOR",
                message_kind="CLARIFICATION",
                object_key=key,
                sha256=digest,
                request_sha256=request_sha,
                idempotency_key=f"{idempotency_key}:clarification",
                correlation_id=correlation_id,
                interaction_state="WAITING_FOR_USER",
                created_by="evaluation-manager",
                created_at=now,
            )
        )


__all__ = [
    "NormalizedRequirement",
    "RequirementBriefNormalizer",
    "SupervisorChatApplication",
    "SupervisorChatResult",
    "supervisor_1p4_enabled",
]
