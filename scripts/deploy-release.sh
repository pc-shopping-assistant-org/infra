#!/usr/bin/env bash
# Infra is the only release owner; applications only publish images.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

# Usage: deploy-release.sh [BACKEND_TAG [AI_TAG [WEB_TAG]]]
# Run after all application builds finish. Use sha-* tags for a fixed release.
backend_tag=${1:-release}
ai_tag=${2:-release}
web_tag=${3:-release}
for tag in "$backend_tag" "$ai_tag" "$web_tag"; do
  [[ "$tag" == release || "$tag" =~ ^sha-[0-9a-f]{40}$ ]] || { echo 'Invalid image tag' >&2; exit 2; }
done
[[ $# -le 3 ]] || { echo 'Expected backend, AI and web image tags only' >&2; exit 2; }
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

"${apps[@]}" config --quiet
# Fail on missing images before changing running services.
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
