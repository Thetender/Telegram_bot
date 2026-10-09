#!/usr/bin/env bash
# Nightly database dump for one environment. Keeps the last 14 days.
#   scripts/backup_db.sh prod
# Cron example (as the deploy user):
#   15 3 * * * /opt/thetender-telegram/prod/scripts/backup_db.sh prod >> /var/log/tg-backup.log 2>&1
set -euo pipefail
ENV_NAME="${1:?usage: backup_db.sh test|prod}"
BASE_DIR="${DEPLOY_BASE_DIR:-/opt/thetender-telegram}"
BACKUP_DIR="${BACKUP_DIR:-$BASE_DIR/backups/$ENV_NAME}"
mkdir -p "$BACKUP_DIR"
cd "$BASE_DIR/$ENV_NAME"
FILE="$BACKUP_DIR/thetender_tg_$(date +%Y%m%d_%H%M%S).sql.gz"
docker compose -p "tg-$ENV_NAME" exec -T db pg_dump -U thetender -d thetender_tg --no-owner | gzip > "$FILE"
find "$BACKUP_DIR" -name 'thetender_tg_*.sql.gz' -mtime +14 -delete
echo "Backup written: $FILE ($(du -h "$FILE" | cut -f1))"
