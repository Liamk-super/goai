from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

from launchscope_api.modules.supervisor.material_routing import persist_task_scopes


def test_persisted_scope_ids_are_control_plane_owned_across_tasks() -> None:
    session = Mock()
    supplied_scope_id = uuid4()
    scope = {
        "scope_id": str(supplied_scope_id),
        "material_id": str(uuid4()),
        "analysis_id": str(uuid4()),
        "authorized_unit_ids": [],
        "authorized_unit_refs": [],
        "reason": "test",
        "required": True,
        "scope_sha256": "a" * 64,
    }
    tenant_id = uuid4()
    run_id = uuid4()
    plan_id = uuid4()

    persist_task_scopes(session, tenant_id, run_id, uuid4(), plan_id, (scope,), datetime.now(UTC))
    persist_task_scopes(session, tenant_id, run_id, uuid4(), plan_id, (scope,), datetime.now(UTC))

    persisted_ids = [call.args[0].compile().params["id"] for call in session.execute.call_args_list]
    assert len(set(persisted_ids)) == 2
    assert supplied_scope_id not in persisted_ids
