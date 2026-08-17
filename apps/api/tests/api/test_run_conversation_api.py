from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from launchscope_api.main import create_app
from launchscope_api.modules.identity_tenant.application import Actor
from launchscope_api.modules.project_dossier.model_extraction import IntakeModelExtractor
from launchscope_api.modules.supervisor.conversation_application import (
    ConversationReceipt,
    RunConversationApplication,
)


def _headers() -> dict[str, str]:
    return {
        "X-Tenant-Id": str(uuid4()),
        "X-Actor-Id": "run-conversation-api-test",
        "X-Correlation-Id": str(uuid4()),
        "Idempotency-Key": "run-conversation-message-1",
    }


def test_specialist_message_is_routed_without_calling_the_intake_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, message_id, task_id = uuid4(), uuid4(), uuid4()
    application = object.__new__(RunConversationApplication)
    captured: dict[str, object] = {}

    def forbidden_extract(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("specialist routing must not call the intake model")

    def submit(
        _self: RunConversationApplication,
        actor: Actor,
        selected_run_id: UUID,
        channel: str,
        **values: object,
    ) -> ConversationReceipt:
        captured["submission"] = (actor, selected_run_id, channel, values)
        return ConversationReceipt(message_id, run_id, channel, "ROUTED", (task_id,))

    monkeypatch.setattr(IntakeModelExtractor, "extract_requirement", forbidden_extract)
    monkeypatch.setattr(RunConversationApplication, "submit", submit)
    app = create_app(SimpleNamespace(run_conversations=application))  # type: ignore[arg-type]
    response = TestClient(app).post(
        f"/api/v1/runs/{run_id}/conversations/product-engineering/messages",
        headers=_headers(),
        json={"message": "补充：我们已有可运行原型", "allow_external_processing": False},
    )

    assert response.status_code == 202
    assert response.json() == {
        "message_id": str(message_id),
        "run_id": str(run_id),
        "channel": "product-engineering",
        "route_state": "ROUTED",
        "affected_task_ids": [str(task_id)],
        "questions": [],
        "duplicate": False,
    }
    assert captured["submission"][1:3] == (run_id, "product-engineering")  # type: ignore[index]


def test_supervisor_channel_passes_a_bounded_intake_proposal(monkeypatch: pytest.MonkeyPatch) -> None:
    run_id, message_id = uuid4(), uuid4()
    application = object.__new__(RunConversationApplication)
    captured: dict[str, object] = {}

    def extract(
        _self: IntakeModelExtractor,
        content: str,
        *,
        allow_external_processing: bool,
    ) -> dict[str, object]:
        captured["extraction"] = (content, allow_external_processing)
        return {"normalized_goal": content}

    def submit(
        _self: RunConversationApplication,
        _actor: Actor,
        _run_id: UUID,
        _channel: str,
        **values: object,
    ) -> ConversationReceipt:
        captured["proposal"] = values["supervisor_model_output"]
        return ConversationReceipt(message_id, run_id, "supervisor", "WAITING_FOR_USER", ())

    monkeypatch.setattr(IntakeModelExtractor, "extract_requirement", extract)
    monkeypatch.setattr(RunConversationApplication, "submit", submit)
    app = create_app(SimpleNamespace(run_conversations=application))  # type: ignore[arg-type]
    response = TestClient(app).post(
        f"/api/v1/runs/{run_id}/conversations/supervisor/messages",
        headers=_headers(),
        json={"message": "请调整本轮判断重点", "allow_external_processing": True},
    )

    assert response.status_code == 202
    assert response.json()["route_state"] == "WAITING_FOR_USER"
    assert captured["extraction"] == ("请调整本轮判断重点", True)
    assert captured["proposal"] == {"normalized_goal": "请调整本轮判断重点"}
