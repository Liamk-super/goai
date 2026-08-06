# LaunchScope Local / Demo infrastructure

The Compose profiles provide pinned PostgreSQL, MinIO, RocketMQ, Nacos,
Higress and OpenTelemetry infrastructure. They are a reproducible dependency
layer, not evidence that the LaunchScope business flow passed E2E.

Copy `.env.example` to an untracked local environment file and supply only
local, disposable credentials. Do not commit the resulting file. Validate the
configuration before startup:

```powershell
docker compose -f infra/compose/docker-compose.yml config
docker compose -f infra/compose/docker-compose.test.yml config
```

The API must receive `DATABASE_URL` and the private S3 settings through the
process environment. Runtime requests use `launchscope_runtime` so RLS cannot
be bypassed. Migration and retention jobs use a separate maintenance
connection; that connection must never be exposed to HTTP requests.

Images are digest-pinned. Local and Demo consume the same Nacos configuration
schema in `infra/nacos/config-schema.json`; only capacity and secret references
may differ.
