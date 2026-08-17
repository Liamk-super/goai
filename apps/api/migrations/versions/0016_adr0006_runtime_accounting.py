"""ADR 0006: AgentTeams delivery deadlines and task-attributable accounting."""

from __future__ import annotations

from alembic import op

from migrations._common import enable_tenant_rls, grant_runtime

revision = "0016_adr0006_runtime_accounting"
down_revision = "0015_adr0004_information_request"
branch_labels = None
depends_on = None

_RUNTIME_SKILLS = (
    (
        "launchscope-evaluation-manager-handoff-v1",
        "461ef871f0d699f941c38fda21fe3f0c01cab376337341f0d10af0350d418201",
    ),
    (
        "launchscope-geo-policy-trend-handoff-v1",
        "a382ac5f5ea6ce56dea4ac866a6299ae56d38fe9723d6b594549d6afe53e64ce",
    ),
)


def upgrade() -> None:
    for code, digest in _RUNTIME_SKILLS:
        op.execute(
            f"""
            INSERT INTO skill_version
                (id, skill_code, version, manifest_sha256, input_schema, output_schema)
            VALUES
                (gen_random_uuid(), '{code}', '1.0', '{digest}', '{{}}'::jsonb, '{{}}'::jsonb)
            ON CONFLICT (skill_code, version) DO NOTHING;
            """
        )
    op.execute(
        """
        CREATE TABLE agentteams_task_delivery (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid NOT NULL,
            dispatch_epoch integer NOT NULL CHECK (dispatch_epoch >= 0),
            agent_code varchar(120) NOT NULL,
            room_id varchar(255) NOT NULL,
            assignment_event_id varchar(255) NOT NULL,
            status varchar(32) NOT NULL
                CHECK (status IN ('DELIVERED', 'COMPLETED', 'TIMED_OUT')),
            usage_baseline jsonb,
            delivered_at timestamptz NOT NULL,
            deadline_at timestamptz NOT NULL,
            completed_at timestamptz,
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, task_id, dispatch_epoch),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, run_id, task_id) REFERENCES task(tenant_id, run_id, id),
            CHECK (deadline_at > delivered_at),
            CHECK ((status = 'DELIVERED') = (completed_at IS NULL))
        );
        CREATE INDEX ix_agentteams_task_delivery_deadline
            ON agentteams_task_delivery (status, deadline_at);
        """
    )
    enable_tenant_rls(op, ("agentteams_task_delivery",))
    grant_runtime(op, ("agentteams_task_delivery",))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agentteams_task_delivery CASCADE;")
    for code, digest in _RUNTIME_SKILLS:
        op.execute(
            f"DELETE FROM skill_version WHERE skill_code = '{code}' AND version = '1.0' "
            f"AND manifest_sha256 = '{digest}';"
        )
