#!/usr/bin/env bash
# Restore a dump into an environment's database (DESTROYS current data there).
#   scripts/restore_db.sh test /opt/thetender-telegram/backups/prod/thetender_tg_....sql.gz
set -euo pipefail
ENV_NAME="${1:?usage: restore_db.sh test|prod FILE}"
FILE="${2:?dump file}"
BASE_DIR="${DEPLOY_BASE_DIR:-/opt/thetender-telegram}"
cd "$BASE_DIR/$ENV_NAME"
read -r -p "This will REPLACE all data in tg-$ENV_NAME. Type the env name to confirm: " c
[ "$c" = "$ENV_NAME" ] || { echo "Cancelled"; exit 1; }
docker compose -p "tg-$ENV_NAME" stop app
docker compose -p "tg-$ENV_NAME" exec -T db psql -U thetender -d postgres -c "DROP DATABASE IF EXISTS thetender_tg WITH (FORCE);" -c "CREATE DATABASE thetender_tg OWNER thetender;"
gunzip -c "$FILE" | docker compose -p "tg-$ENV_NAME" exec -T db psql -U thetender -d thetender_tg -q
docker compose -p "tg-$ENV_NAME" start app
echo "Restored $FILE into tg-$ENV_NAME"
