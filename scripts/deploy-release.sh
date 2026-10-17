#!/usr/bin/env bash
# Infra is the only release owner; applications only publish images.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

# Usage: deploy-release.sh VERSION [AI_TAG [WEB_TAG]]
# Run after all application builds finish. Explicit sha-* pins are also accepted.
backend_tag=${1:?Usage: deploy-release.sh VERSION [AI_TAG [WEB_TAG]]}
ai_tag=${2:-$backend_tag}
web_tag=${3:-$backend_tag}
version_pattern='(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-rc\.[1-9][0-9]*)?'
for tag in "$backend_tag" "$ai_tag" "$web_tag"; do
  [[ "$tag" =~ ^${version_pattern}$ || "$tag" =~ ^sha-[0-9a-f]{40}$ ]] || { echo 'Use X.Y.Z, X.Y.Z-rc.N or sha-FULL_COMMIT' >&2; exit 2; }
done
[[ $# -le 3 ]] || { echo 'Expected backend, AI and web image tags only' >&2; exit 2; }
# Compose gives shell variables precedence over --env-file; enforce selected tags.
export BACKEND_IMAGE_TAG="$backend_tag" AI_IMAGE_TAG="$ai_tag" WEB_IMAGE_TAG="$web_tag"
[[ -f .env.docker ]] || { echo 'Missing infra/.env.docker' >&2; exit 2; }
# The SSH workflow holds the same lock while installing configuration.
if [[ "${DEPLOY_LOCK_HELD:-0}" != 1 ]]; then
  exec 9>.deploy.lock
  flock -w "${DEPLOY_LOCK_TIMEOUT_SECONDS:-600}" 9
fi

candidate=$(mktemp .release.env.XXXXXX)
trap 'rm -f -- "$candidate"' EXIT
printf 'BACKEND_IMAGE_TAG=%s\nAI_IMAGE_TAG=%s\nWEB_IMAGE_TAG=%s\n' \
  "$backend_tag" "$ai_tag" "$web_tag" > "$candidate"
base=(docker compose --env-file .env.docker --env-file "$candidate")
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
mv -- "$candidate" .release.env
echo "Released backend=$backend_tag ai=$ai_tag web=$web_tag"
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
