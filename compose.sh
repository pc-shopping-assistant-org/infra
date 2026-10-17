#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
args=(--env-file .env.docker)
if [[ -f .release.env ]]; then args+=(--env-file .release.env); fi
exec docker compose "${args[@]}" "$@"
