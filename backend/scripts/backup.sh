#!/usr/bin/env bash
# Nightly PostgreSQL backup for Goat Farm Management.
#
# Usage (from cron, systemd timer, or `docker compose run`):
#     ./backend/scripts/backup.sh /var/backups/goatfarm
#
# Environment:
#     GOATFARM_DATABASE_URL   asyncpg-style URL (as read by the app)
#                             e.g. postgresql+asyncpg://user:pw@host:5432/db
#     GOATFARM_BACKUP_KEEP    number of daily dumps to keep (default 30)
#     GOATFARM_BACKUP_S3_URI  optional s3:// destination, uploaded via `aws s3 cp`
#
# RTO/RPO commitment (see README "Backups"):
#     RPO = 24 h  (nightly full dumps)
#     RTO ≤ 4 h   (a fresh `pg_restore` against a clean instance)
#     For tighter RPO enable WAL archiving in postgresql.conf and
#     configure `archive_command` to ship to the same s3 prefix.

set -euo pipefail

DEST_DIR="${1:-./backups}"
KEEP="${GOATFARM_BACKUP_KEEP:-30}"

if [[ -z "${GOATFARM_DATABASE_URL:-}" ]]; then
    echo "GOATFARM_DATABASE_URL is required" >&2
    exit 2
fi

# Strip the SQLAlchemy driver prefix so pg_dump / libpq accept the URL.
PG_URL="${GOATFARM_DATABASE_URL/postgresql+asyncpg:\/\//postgresql://}"

mkdir -p "${DEST_DIR}"
TIMESTAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
OUT="${DEST_DIR}/goatfarm-${TIMESTAMP}.dump"

echo "[$(date -Iseconds)] dumping to ${OUT}"
# --format=custom is compressible and restore-friendly (pg_restore -j).
pg_dump --format=custom --no-owner --dbname="${PG_URL}" --file="${OUT}"

# Optional S3 upload.
if [[ -n "${GOATFARM_BACKUP_S3_URI:-}" ]]; then
    echo "[$(date -Iseconds)] uploading to ${GOATFARM_BACKUP_S3_URI%/}/"
    aws s3 cp "${OUT}" "${GOATFARM_BACKUP_S3_URI%/}/"
fi

# Retention: keep the newest N files, delete the rest.
echo "[$(date -Iseconds)] pruning to newest ${KEEP} dumps"
ls -1t "${DEST_DIR}"/goatfarm-*.dump 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

echo "[$(date -Iseconds)] done"
