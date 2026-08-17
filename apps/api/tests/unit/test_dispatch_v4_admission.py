from __future__ import annotations

import json
import os
from typing import Any, cast
from uuid import uuid4

import pytest

from launchscope_api.modules.evaluation.dispatch_application import DispatchApplication
from launchscope_api.modules.evaluation.intake_application import IntakeValidationError
from launchscope_api.modules.evaluation.runtime_mode import execution_runtime_unavailable_reason
from launchscope_api.modules.identity_tenant.application import Actor


def test_disabled_supervisor_admission_rejects_instead_of_falling_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED", "false")
    application = DispatchApplication(cast(Any, None))

    with pytest.raises(IntakeValidationError, match="SUPERVISOR_1P4_DISABLED"):
        application.dispatch(Actor(uuid4(), "admission-test"), uuid4(), idempotency_key="new-evaluation")


def test_recorded_mode_rejects_dispatch_before_opening_a_database_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED", "true")
    monkeypatch.setenv("LAUNCHSCOPE_EXECUTION_MODE", "RECORDED")
    application = DispatchApplication(cast(Any, None))

    with pytest.raises(IntakeValidationError, match="EXECUTION_RUNTIME_UNAVAILABLE"):
        application.dispatch(Actor(uuid4(), "admission-test"), uuid4(), idempotency_key="recorded-dispatch")


def test_live_readiness_accepts_a_verified_running_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    marker = tmp_path / "execution-readiness.json"
    marker.write_text(json.dumps({
        "mode": "LIVE",
        "dispatch_enabled": True,
        "processes": [{"name": "test-runtime", "pid": os.getpid()}],
    }), encoding="utf-8")
    monkeypatch.setenv("LAUNCHSCOPE_EXECUTION_MODE", "LIVE")
    monkeypatch.setenv("LAUNCHSCOPE_EXECUTION_READINESS_FILE", str(marker))

    assert execution_runtime_unavailable_reason() is None
