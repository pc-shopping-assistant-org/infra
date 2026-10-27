#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
args=(--env-file .env.docker)
if [[ -f release.toml ]]; then
  while IFS='=' read -r key value; do export "$key=$value"; done < <(python3 scripts/release_images.py release.toml)
fi
exec docker compose "${args[@]}" "$@"
