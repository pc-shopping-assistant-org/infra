#!/usr/bin/env bash
# One server-side lock serializes releases from independent application repos.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

component=${1:?Usage: deploy-release.sh backend|ai|frontend sha-COMMIT}
image_tag=${2:?An immutable sha-COMMIT tag is required}
[[ "$image_tag" =~ ^sha-[0-9a-f]{40}$ ]] || { echo 'Invalid image tag' >&2; exit 2; }
case "$component" in
  backend)
    tag_key=BACKEND_IMAGE_TAG
    services=(discovery-server identity-service catalog-service order-service payment-service promotion-service search-service media-service sync-service api-gateway)
    ;;
  ai) tag_key=AI_IMAGE_TAG; services=(ai-migrate ai-service) ;;
  frontend) tag_key=WEB_IMAGE_TAG; services=(web-frontend) ;;
  *) echo 'Unknown release component' >&2; exit 2 ;;
esac
[[ -f .env.docker ]] || { echo 'Missing infra/.env.docker' >&2; exit 2; }
exec 9>.deploy.lock
flock -w "${DEPLOY_LOCK_TIMEOUT_SECONDS:-600}" 9

candidate=$(mktemp .release.env.XXXXXX)
trap 'rm -f -- "$candidate"' EXIT
if [[ -f .release.env ]]; then
  awk -F= -v key="$tag_key" '$1 != key' .release.env > "$candidate"
fi
printf '%s=%s\n' "$tag_key" "$image_tag" >> "$candidate"
compose=(docker compose --env-file .env.docker --env-file "$candidate" --profile apps)
"${compose[@]}" config --quiet
"${compose[@]}" pull "${services[@]}"
if [[ "$component" == ai ]]; then
  # Stop the old executor before migrating; a failed migration requires operator action.
  "${compose[@]}" stop ai-service
  "${compose[@]}" run --rm --no-deps ai-migrate
  services=(ai-service)
fi
"${compose[@]}" up -d --no-deps --wait --wait-timeout "${DEPLOY_WAIT_SECONDS:-300}" "${services[@]}"
mv -- "$candidate" .release.env
echo "Released $component at $image_tag"
