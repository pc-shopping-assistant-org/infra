#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
exec "$root/compose.sh" --profile setup run --rm --no-deps catalog-seed --seed-demo-stock
