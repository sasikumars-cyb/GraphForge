#!/usr/bin/env bash
# Cheap, cron-driven Postgres backup for the single-box demo stack (no RDS,
# so no automated snapshots exist otherwise). Dumps to a local directory and
# keeps the last 7 days - EBS storage only, no extra AWS service.
#
# Install: crontab -e, then add:
#   0 3 * * * /path/to/repo/docs/deployment/demo/backup-postgres.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
BACKUP_DIR="$HOME/graphforge-backups"
mkdir -p "$BACKUP_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
docker compose -f "$ROOT_DIR/docker/docker-compose.ec2-demo.yml" exec -T db \
  pg_dump -U graphforge graphforge | gzip > "$BACKUP_DIR/graphforge-$STAMP.sql.gz"

find "$BACKUP_DIR" -name 'graphforge-*.sql.gz' -mtime +7 -delete
