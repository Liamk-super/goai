from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from launchscope_api.main import create_app
from launchscope_api.modules.identity_tenant.application import Actor
from launchscope_api.modules.project_dossier.model_extraction import IntakeModelExtractor
from launchscope_api.modules.supervisor.intake_application import SupervisorChatApplication, SupervisorChatResult


def test_supervisor_chat_api_passes_a_bounded_model_proposal_to_the_control_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, version_id = uuid4(), uuid4()
    supervisor = object.__new__(SupervisorChatApplication)
    captured: dict[str, object] = {}

    def extract(
        _self: IntakeModelExtractor, content: str, *, allow_external_processing: bool
    ) -> dict[str, object]:
        captured["extraction"] = (content, allow_external_processing)
        return {"normalized_goal": content}

    def submit(
        _self: SupervisorChatApplication,
        actor: Actor,
        selected_project_id: UUID,
        selected_version_id: UUID,
        **values: object,
    ) -> SupervisorChatResult:
        captured["submission"] = (actor, selected_project_id, selected_version_id, values)
        return SupervisorChatResult(
            uuid4(), uuid4(), 1, "WAITING_FOR_USER", True, ("主要目标用户是谁？",)
        )

    monkeypatch.setattr(IntakeModelExtractor, "extract_requirement", extract)
    monkeypatch.setattr(SupervisorChatApplication, "submit_requirement", submit)
    app = create_app(SimpleNamespace(supervisor=supervisor))  # type: ignore[arg-type]

    response = TestClient(app).post(
        f"/api/v1/projects/{project_id}/versions/{version_id}/supervisor/messages",
        headers={
            "X-Tenant-Id": str(uuid4()),
            "X-Actor-Id": "recorded-api-test",
            "X-Correlation-Id": str(uuid4()),
            "Idempotency-Key": "supervisor-message-1",
        },
        json={"message": "帮我验证这个想法", "allow_external_processing": True},
    )

    assert response.status_code == 202
    assert response.json()["interaction_state"] == "WAITING_FOR_USER"
    assert response.json()["questions"] == ["主要目标用户是谁？"]
    assert captured["extraction"] == ("帮我验证这个想法", True)
    assert captured["submission"][1:3] == (project_id, version_id)  # type: ignore[index]
