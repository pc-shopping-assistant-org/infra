# Legacy configuration

This page describes the previous monolith stack. For current microservices use
the [infra runbook](../../README.md), one compose and its optional apps profile.

## Previous production compose

This stack runs the currently available infrastructure:

- `ai-service`: FastAPI/PydanticAI image pulled from GitHub Container Registry;
- `postgres`: PostgreSQL for the ecommerce core and persistent cart/order data;
- `qdrant`: private vector store reserved for retrieval.

PostgreSQL and Qdrant are intentionally not published to the host. The AI
service is bound to `127.0.0.1:8000` by default so a host reverse proxy can
terminate TLS and expose the public endpoint.

## First deployment

```sh
cd infra/compose/prod
cp env/prod.env.example env/prod.env
# Edit env/prod.env and replace POSTGRES_PASSWORD and all deployment values,
# including AI_BACKEND_API_URL (it must include the backend /api/v1 root).
docker compose \
  --env-file env/prod.env \
  -f compose-file/compose.yaml \
  config
docker compose \
  --env-file env/prod.env \
  -f compose-file/compose.yaml \
  pull ai-service
docker compose \
  --env-file env/prod.env \
  -f compose-file/compose.yaml \
  up -d
```

## Operations

```sh
docker compose --env-file env/prod.env -f compose-file/compose.yaml ps
docker compose --env-file env/prod.env -f compose-file/compose.yaml logs -f ai-service
docker compose --env-file env/prod.env -f compose-file/compose.yaml pull
docker compose --env-file env/prod.env -f compose-file/compose.yaml up -d
```

The database schema/migrations are not mounted automatically because this
stack does not run the backend application. Apply the canonical backend
Flyway migration from
`backend-api/server/src/main/resources/db/migration/V1__init.sql` through the
backend deployment process before enabling application traffic.

The compose project owns two named volumes. Back up PostgreSQL and Qdrant
before upgrades or destructive maintenance.
