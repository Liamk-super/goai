"""ADR 0020: stable report baselines, citations, public Demo access, and exports."""

from __future__ import annotations

from alembic import op

from migrations._common import enable_tenant_rls, grant_runtime, install_append_only_trigger

revision = "0031_report_v22"
down_revision = "0030_material_routing_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE evaluation_run
            DROP CONSTRAINT evaluation_run_recheck_baseline,
            ADD COLUMN input_snapshot_sha256 varchar(64)
                CHECK (input_snapshot_sha256 IS NULL OR input_snapshot_sha256 ~ '^[0-9a-f]{64}$'),
            ADD COLUMN content_fingerprint_sha256 varchar(64)
                CHECK (content_fingerprint_sha256 IS NULL OR content_fingerprint_sha256 ~ '^[0-9a-f]{64}$'),
            ADD COLUMN report_profile_ref varchar(160),
            ADD CONSTRAINT evaluation_run_recheck_baseline
                CHECK (run_kind <> 'USER_EVIDENCE_RECHECK' OR baseline_run_id IS NOT NULL);
        CREATE INDEX ix_evaluation_run_report_baseline
            ON evaluation_run (tenant_id, project_id, status, created_at DESC)
            WHERE run_kind = 'FULL_EVALUATION';
        CREATE INDEX ix_evaluation_run_content_fingerprint
            ON evaluation_run (tenant_id, project_id, content_fingerprint_sha256, created_at DESC)
            WHERE content_fingerprint_sha256 IS NOT NULL;

        INSERT INTO skill_version
            (id, skill_code, version, manifest_sha256, input_schema, output_schema)
        VALUES
            (gen_random_uuid(), 'user-validation-designer', '1.1.0',
             '2fd4d2d965d9277cc484560f989c402e9ddbbf8e65e4b5c3032c7f56071174e6',
             '{"$ref":"https://launchscope.local/skills/user-validation-designer/input.v1.json","sha256":"676b1d0968b1337bae5aa60dd148a17b94f885b874eddbb68cc1ea3ab816ce05"}'::jsonb,
             '{"$ref":"https://launchscope.local/contracts/reports/specialist-report.v2.json","sha256":"c4962bab2a7c99a1a94486c698c1be554b66f1d199b58183538e611b611e2c39"}'::jsonb),
            (gen_random_uuid(), 'product-technical-audit', '1.0.0',
             'a749c234c8d2b71bb8610761b8302b9cb34512fd6646c95ce9ff2f8eacc3cea3',
             '{"$ref":"https://launchscope.local/skills/product-technical-audit/input.v1.json","sha256":"27775e4696fe790810de91afd5d613c818c3cc77a238e3742f3421ba00459bb0"}'::jsonb,
             '{"$ref":"https://launchscope.local/contracts/reports/specialist-report.v2.json","sha256":"c4962bab2a7c99a1a94486c698c1be554b66f1d199b58183538e611b611e2c39"}'::jsonb),
            (gen_random_uuid(), 'business-investment-assessment', '2.0.0',
             'd07e3f61f00981e280611d4cb39cbfd00c4e6aba7570bb0713bac6a3daa2ec00',
             '{"$ref":"https://launchscope.local/skills/business-investment-assessment/input.v2.json","sha256":"d1ba4e45a8a198ca992f39c76b659c2b6e5f0783ec5d9f453e522566f0f6684f"}'::jsonb,
             '{"$ref":"https://launchscope.local/contracts/reports/specialist-report.v2.json","sha256":"c4962bab2a7c99a1a94486c698c1be554b66f1d199b58183538e611b611e2c39"}'::jsonb),
            (gen_random_uuid(), 'evidence-grounding-audit', '2.1.0',
             '33584a3f59b5de86088bc874c7c5f5c604f4f19119cc6d7c6aefe0f55eb8dd89',
             '{"$ref":"https://launchscope.local/contracts/audit/audit-request.v2.json","sha256":"d1160a64b52e30d9f02fcd2f849483faa4afb02b9e21d8d5806608e329dcb502"}'::jsonb,
             '{"$ref":"https://launchscope.local/skills/evidence-grounding-audit/output.v2.1.json","sha256":"3769c3af624c12d0fcbc17579457ba3a14bca8aa75650a3aae4dd2a5b3ec3c71"}'::jsonb),
            (gen_random_uuid(), 'evidence-grounding-audit', '2.2.0',
             'bf44a352a0e56c9e4cd86a615f9a081b7fc0c40fbd9b11bf5729b0f4dcb1973f',
             '{"$ref":"https://launchscope.local/skills/evidence-grounding-audit/input.v1.json","sha256":"ac9d018f2526138e710e16d81c3e11ae0c7d2432be6697006e37e73a0385d300"}'::jsonb,
             '{"$ref":"https://launchscope.local/contracts/reports/specialist-report.v2.json","sha256":"c4962bab2a7c99a1a94486c698c1be554b66f1d199b58183538e611b611e2c39"}'::jsonb)
        ON CONFLICT (skill_code, version) DO NOTHING;

        CREATE TABLE evidence_source_locator (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            evidence_id uuid NOT NULL,
            ordinal integer NOT NULL CHECK (ordinal > 0),
            source_kind varchar(32) NOT NULL CHECK (
                source_kind IN ('PUBLIC_URL','SEARCH_RESULT','INTERNAL_MATERIAL')
            ),
            canonical_url varchar(2048),
            title varchar(1000) NOT NULL,
            publisher varchar(500),
            published_at timestamptz,
            fetched_at timestamptz NOT NULL,
            locator jsonb NOT NULL DEFAULT '{}'::jsonb,
            region varchar(100),
            independence_group varchar(500) NOT NULL,
            content_sha256 varchar(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
            screenshot_sha256 varchar(64)
                CHECK (screenshot_sha256 IS NULL OR screenshot_sha256 ~ '^[0-9a-f]{64}$'),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, evidence_id, ordinal),
            FOREIGN KEY (tenant_id, evidence_id) REFERENCES evidence(tenant_id, id),
            CHECK (source_kind = 'INTERNAL_MATERIAL' OR canonical_url IS NOT NULL)
        );
        CREATE INDEX ix_evidence_source_locator_evidence
            ON evidence_source_locator (tenant_id, evidence_id, ordinal);
        CREATE INDEX ix_evidence_source_locator_independence
            ON evidence_source_locator (tenant_id, independence_group);

        CREATE TABLE report_claim_citation (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            report_id uuid NOT NULL,
            claim_id varchar(160) NOT NULL,
            citation_id varchar(160) NOT NULL,
            evidence_id uuid NOT NULL,
            source_locator_id uuid,
            support_role varchar(32) NOT NULL CHECK (support_role IN ('SUPPORT','COUNTER','BACKGROUND')),
            audit_status varchar(32) NOT NULL CHECK (
                audit_status IN ('VERIFIED','DOWNGRADED','REJECTED','NEEDS_MORE')
            ),
            label integer NOT NULL CHECK (label > 0),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, report_id, citation_id),
            UNIQUE (tenant_id, report_id, claim_id, label),
            FOREIGN KEY (tenant_id, report_id) REFERENCES report(tenant_id, id),
            FOREIGN KEY (tenant_id, evidence_id) REFERENCES evidence(tenant_id, id),
            FOREIGN KEY (tenant_id, source_locator_id) REFERENCES evidence_source_locator(tenant_id, id)
        );
        CREATE INDEX ix_report_claim_citation_report
            ON report_claim_citation (tenant_id, report_id, claim_id, label);
        CREATE INDEX ix_report_claim_citation_evidence
            ON report_claim_citation (tenant_id, evidence_id);

        CREATE TABLE public_demo_disclosure_acceptance (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            project_id uuid NOT NULL,
            product_version_id uuid NOT NULL,
            run_id uuid,
            actor_id varchar(255) NOT NULL,
            policy_version varchar(120) NOT NULL,
            accepted_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, project_id) REFERENCES project(tenant_id, id),
            FOREIGN KEY (tenant_id, product_version_id) REFERENCES product_version(tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id)
        );
        CREATE UNIQUE INDEX uq_report_v22_disclosure_policy
            ON public_demo_disclosure_acceptance (tenant_id, product_version_id, policy_version);

        CREATE TABLE public_demo_share (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            report_id uuid NOT NULL,
            token_sha256 varchar(64) NOT NULL CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
            status varchar(32) NOT NULL CHECK (status IN ('ACTIVE','REVOKED')),
            include_agent_reports boolean NOT NULL DEFAULT true,
            include_evidence boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            revoked_at timestamptz,
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, report_id) REFERENCES report(tenant_id, id),
            CHECK ((status = 'REVOKED') = (revoked_at IS NOT NULL))
        );
        CREATE UNIQUE INDEX uq_report_v22_public_token ON public_demo_share (token_sha256);
        CREATE INDEX ix_public_demo_share_run ON public_demo_share (tenant_id, run_id, status);

        CREATE OR REPLACE FUNCTION launchscope_resolve_public_demo_share(p_token_sha256 varchar)
        RETURNS TABLE (
            tenant_id uuid,
            share_id uuid,
            run_id uuid,
            report_id uuid,
            include_agent_reports boolean,
            include_evidence boolean
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT s.tenant_id, s.id, s.run_id, s.report_id, s.include_agent_reports, s.include_evidence
            FROM public_demo_share AS s
            JOIN report AS r
              ON r.tenant_id = s.tenant_id AND r.id = s.report_id AND r.run_id = s.run_id
            JOIN evaluation_run AS er
              ON er.tenant_id = s.tenant_id AND er.id = s.run_id
            WHERE s.token_sha256 = p_token_sha256
              AND s.status = 'ACTIVE'
              AND s.revoked_at IS NULL
              AND r.status = 'COMMITTED'
            LIMIT 1
        $$;
        REVOKE ALL ON FUNCTION launchscope_resolve_public_demo_share(varchar) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION launchscope_resolve_public_demo_share(varchar) TO launchscope_runtime;

        CREATE TABLE report_export_artifact (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            report_id uuid NOT NULL,
            agent_code varchar(120),
            kind varchar(32) NOT NULL CHECK (kind IN ('SUPERVISOR','SPECIALIST','PACKAGE')),
            view varchar(16) NOT NULL CHECK (view IN ('SUMMARY','FULL')),
            locale varchar(20) NOT NULL,
            include_evidence boolean NOT NULL DEFAULT false,
            renderer_version varchar(80) NOT NULL,
            source_sha256 varchar(64) NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
            idempotency_key varchar(255) NOT NULL,
            request_sha256 varchar(64) NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
            status varchar(32) NOT NULL CHECK (status IN ('PENDING','RENDERING','COMPLETED','FAILED')),
            object_key varchar(1024),
            sha256 varchar(64) CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
            size_bytes bigint CHECK (size_bytes IS NULL OR size_bytes >= 0),
            error_code varchar(120),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            completed_at timestamptz,
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, report_id) REFERENCES report(tenant_id, id),
            CHECK (kind <> 'SPECIALIST' OR agent_code IS NOT NULL),
            CHECK (kind = 'SPECIALIST' OR agent_code IS NULL)
        );
        CREATE UNIQUE INDEX uq_report_v22_export_idempotency
            ON report_export_artifact (tenant_id, idempotency_key);
        CREATE UNIQUE INDEX uq_report_v22_export_cache
            ON report_export_artifact (
                tenant_id, report_id, COALESCE(agent_code, ''), kind, view, locale, include_evidence,
                renderer_version, source_sha256
            );
        CREATE INDEX ix_report_export_artifact_status
            ON report_export_artifact (tenant_id, status, created_at);
        """
    )
    tables = (
        "evidence_source_locator",
        "report_claim_citation",
        "public_demo_disclosure_acceptance",
        "public_demo_share",
        "report_export_artifact",
    )
    enable_tenant_rls(op, tables)
    grant_runtime(op, tables)
    for table in (
        "evidence_source_locator",
        "report_claim_citation",
    ):
        install_append_only_trigger(op, table)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION launchscope_bind_disclosure_run_once()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND OLD.run_id IS NULL
               AND NEW.run_id IS NOT NULL
               AND (to_jsonb(NEW) - 'run_id') = (to_jsonb(OLD) - 'run_id')
               AND EXISTS (
                   SELECT 1 FROM evaluation_run AS r
                   WHERE r.tenant_id = OLD.tenant_id
                     AND r.id = NEW.run_id
                     AND r.product_version_id = OLD.product_version_id
               )
            THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'append-only fact cannot be updated or deleted: public_demo_disclosure_acceptance'
                USING ERRCODE = '55000';
        END;
        $$;
        CREATE TRIGGER trg_public_demo_disclosure_bind_once
        BEFORE UPDATE OR DELETE ON public_demo_disclosure_acceptance
        FOR EACH ROW EXECUTE FUNCTION launchscope_bind_disclosure_run_once();
        """
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        DROP TABLE IF EXISTS report_export_artifact CASCADE;
        DROP FUNCTION IF EXISTS launchscope_resolve_public_demo_share(varchar);
        DROP TABLE IF EXISTS public_demo_share CASCADE;
        DROP TABLE IF EXISTS public_demo_disclosure_acceptance CASCADE;
        DROP FUNCTION IF EXISTS launchscope_bind_disclosure_run_once();
        DROP TABLE IF EXISTS report_claim_citation CASCADE;
        DROP TABLE IF EXISTS evidence_source_locator CASCADE;
        DROP INDEX IF EXISTS ix_evaluation_run_content_fingerprint;
        DROP INDEX IF EXISTS ix_evaluation_run_report_baseline;
        ALTER TABLE evaluation_run
            DROP CONSTRAINT evaluation_run_recheck_baseline,
            DROP COLUMN report_profile_ref,
            DROP COLUMN content_fingerprint_sha256,
            DROP COLUMN input_snapshot_sha256,
            ADD CONSTRAINT evaluation_run_recheck_baseline
                CHECK ((run_kind = 'USER_EVIDENCE_RECHECK') = (baseline_run_id IS NOT NULL));
        """
    )
