# Legacy configuration

This page describes the previous database-only stack. For current microservices
use the [infra runbook](../../README.md); do not start both stacks on the same ports.

## Previous local staging data

This compose project starts only the data services needed by local
development:

- PostgreSQL on `127.0.0.1:5432` for the ecommerce backend.

Frontend and backend processes run on the host. There are no application or AI
containers in this stack.

## Start

```sh
cd infra/compose/staging
cp env/staging.env.example env/staging.env
# Change DB_PASSWORD and keep DATABASE_URL in sync if needed.
docker compose \
  --env-file env/staging.env \
  -f compose-file/compose.yaml \
  config
docker compose \
  --env-file env/staging.env \
  -f compose-file/compose.yaml \
  up -d
```

## Check and stop

```sh
docker compose --env-file env/staging.env -f compose-file/compose.yaml ps
docker compose --env-file env/staging.env -f compose-file/compose.yaml logs -f
docker compose --env-file env/staging.env -f compose-file/compose.yaml stop
```

`down` removes the container and network but keeps the named PostgreSQL volume.
The default volume is versioned as
`pc-shopping-assistant-staging-postgres-v2`, so a volume created with the old
schema is not attached accidentally. The old volume is left untouched for
backup or an explicitly planned data migration. Use `down -v` only when
intentionally resetting the current local database data.

The backend migration is applied separately after PostgreSQL becomes healthy.
For a fresh v2 local volume, start the backend and Flyway will apply the
canonical baseline `V1__init.sql` followed by incremental migrations. Existing
legacy volumes are deliberately not auto-migrated: the old schema is
structurally incompatible with the canonical account/profile/order model.
Back up any data that must be retained, then set `POSTGRES_VOLUME_NAME` to a
new volume (or use the v2 default) and let Flyway initialize it.
