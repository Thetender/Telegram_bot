#!/usr/bin/env bash
# Deploy one environment on the server.
#   scripts/deploy.sh test            # deploys origin/main to /opt/thetender-telegram/test
#   scripts/deploy.sh prod v0.1.0     # deploys tag v0.1.0 to /opt/thetender-telegram/prod
# Only touches /opt/thetender-telegram/<env> and its own Docker containers.
set -euo pipefail

ENV_NAME="${1:?usage: deploy.sh test|prod [git-ref]}"
REF="${2:-origin/main}"
case "$ENV_NAME" in test|prod) ;; *) echo "env must be test or prod" >&2; exit 2;; esac

BASE_DIR="${DEPLOY_BASE_DIR:-/opt/thetender-telegram}"
DIR="$BASE_DIR/$ENV_NAME"
PROJECT="tg-$ENV_NAME"

cd "$DIR"
[ -f .env ] || { echo "Missing $DIR/.env" >&2; exit 1; }

git fetch --tags --force origin
git checkout --force --detach "$REF"
VERSION="$(git describe --tags --always)"
echo "Deploying $ENV_NAME at $VERSION"

APP_VERSION="$VERSION" docker compose -p "$PROJECT" up -d --build --remove-orphans

PORT="$(grep -E '^APP_PORT=' .env | cut -d= -f2)"
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    echo "Healthy: $(curl -fsS "http://127.0.0.1:${PORT}/health")"
    docker image prune -f >/dev/null
    exit 0
  fi
  sleep 3
done
echo "Service did not become healthy. Recent logs:" >&2
docker compose -p "$PROJECT" logs --tail 100 app >&2
exit 1
