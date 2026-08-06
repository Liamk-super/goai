# Retention and deletion runbook

Default retention is 7 days for temporary uploads, 90 days for web/screenshot
evidence, 30 days for sensitive trace bodies, and one year for aggregate
metrics and security audit metadata. Tenant overrides are stored in
PostgreSQL and remain bounded by the configuration schema.

Deletion is a maintenance operation, never a tenant request transaction. The
operator must identify one tenant plus one Project or Run, record an actor and
reason, and execute through `RetentionApplication` with the database-owner
maintenance connection. The job deletes exact private object keys, clears
database bodies, vector/RAG retrieval records and trace-derived indexes, then
adds a tombstone containing only target hash, actor, reason and per-store
result counts. It does not retain material, evidence, report, prompt or private
reasoning bodies.

The runtime RLS role cannot activate the append-only retention exception. If
any object deletion fails, stop before database redaction and record the job as
failed; do not claim complete deletion. Re-run only after proving the failed
object was not deleted or after an operator reviews the partial result.
