#!/usr/bin/env bash
# Infra is the only release owner; applications only publish images.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

[[ $# -eq 0 ]] || { echo 'Set image versions in .release.env, then run deploy-release.sh without arguments' >&2; exit 2; }
[[ -f .env.docker ]] || { echo 'Missing infra/.env.docker' >&2; exit 2; }
# The SSH workflow holds the same lock while installing configuration.
if [[ "${DEPLOY_LOCK_HELD:-0}" != 1 ]]; then
  exec 9>.deploy.lock
  flock -w "${DEPLOY_LOCK_TIMEOUT_SECONDS:-600}" 9
fi

# Validated file values override ambient shell variables and .env.docker defaults.
pins=$(python3 scripts/release_images.py)
while IFS='=' read -r key value; do
  export "$key=$value"
done <<< "$pins"
base=(docker compose --env-file .env.docker --env-file .release.env)
apps=("${base[@]}" --profile apps)
wait_seconds=${DEPLOY_WAIT_SECONDS:-300}
backend_services=(discovery-server identity-service catalog-service order-service payment-service promotion-service search-service media-service sync-service api-gateway)
application_services=("${backend_services[@]}" ai-service web-frontend)

"${apps[@]}" config --quiet
images=$("${apps[@]}" config --format json | python3 -c '
import json, sys
services = json.load(sys.stdin)["services"]
print("\n".join(sorted({services[name]["image"] for name in sys.argv[1:]})))
' "${application_services[@]}")
while IFS= read -r image; do
  echo "Checking $image"
  docker manifest inspect "$image" >/dev/null
done <<< "$images"
# Capture only this stack's old app images; never prune unrelated server images.
old_image_ids=$("${apps[@]}" images -q "${application_services[@]}" | sort -u)
# Pull validates platform availability too, before changing running services.
"${apps[@]}" pull
"${base[@]}" up -d --wait --wait-timeout "$wait_seconds"
"${apps[@]}" run --rm --no-deps catalog-seed
"${apps[@]}" up -d --wait --wait-timeout "$wait_seconds" "${backend_services[@]}"
# Stop the old executor before migrating; failure requires operator action.
"${apps[@]}" stop ai-service
"${apps[@]}" run --rm --no-deps ai-migrate
"${apps[@]}" up -d --no-deps --wait --wait-timeout "$wait_seconds" ai-service web-frontend
echo "Released backend=$BACKEND_IMAGE_TAG ai=$AI_IMAGE_TAG web=$WEB_IMAGE_TAG"
if current_image_ids=$(./compose.sh --profile apps images -q "${application_services[@]}" | sort -u); then
  while IFS= read -r image_id; do
    [[ -n "$image_id" ]] || continue
    grep -Fxq "$image_id" <<< "$current_image_ids" && continue
    # No --force: images referenced by other containers must remain available.
    if ! docker image rm "$image_id"; then
      echo "Kept old image $image_id (still referenced or cleanup failed)" >&2
    fi
  done <<< "$old_image_ids"
else
  echo 'Deployment succeeded; could not inspect current images, skipped cleanup' >&2
fi
