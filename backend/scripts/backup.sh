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
#     GOATFARM_BACKUP_GPG_RECIPIENT optional GPG recipient; when set, only the
#                             encrypted dump is retained/uploaded
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

if [[ -n "${GOATFARM_BACKUP_GPG_RECIPIENT:-}" ]]; then
    gpg --batch --yes --encrypt --recipient "${GOATFARM_BACKUP_GPG_RECIPIENT}" \
        --output "${OUT}.gpg" "${OUT}"
    rm -f "${OUT}"
    OUT="${OUT}.gpg"
fi

if command -v sha256sum >/dev/null 2>&1; then
    checksum="$(sha256sum "${OUT}" | awk '{print $1}')"
else
    checksum="$(shasum -a 256 "${OUT}" | awk '{print $1}')"
fi
printf '%s  %s\n' "${checksum}" "$(basename "${OUT}")" > "${OUT}.sha256"

# Optional S3 upload.
if [[ -n "${GOATFARM_BACKUP_S3_URI:-}" ]]; then
    echo "[$(date -Iseconds)] uploading to ${GOATFARM_BACKUP_S3_URI%/}/"
    aws s3 cp "${OUT}" "${GOATFARM_BACKUP_S3_URI%/}/"
    aws s3 cp "${OUT}.sha256" "${GOATFARM_BACKUP_S3_URI%/}/"
fi

# Retention: keep the newest N files, delete the rest.
echo "[$(date -Iseconds)] pruning to newest ${KEEP} dumps"
shopt -s nullglob
backup_files=("${DEST_DIR}"/goatfarm-*.dump "${DEST_DIR}"/goatfarm-*.dump.gpg)
if (( ${#backup_files[@]} > KEEP )); then
    while IFS= read -r old_dump; do
        rm -f -- "${old_dump}" "${old_dump}.sha256"
    done < <(ls -1t -- "${backup_files[@]}" | tail -n +$((KEEP + 1)))
fi

echo "[$(date -Iseconds)] done"
