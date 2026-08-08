#!/usr/bin/env bash
# Restore one verified custom-format backup into an EMPTY PostgreSQL database.
# This script deliberately refuses to clean or overwrite an existing schema.

set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "Usage: GOATFARM_RESTORE_CONFIRM=<database> $0 BACKUP.dump TARGET_DATABASE_URL" >&2
    exit 2
fi

BACKUP_PATH="$1"
TARGET_URL="${2/postgresql+asyncpg:\/\//postgresql://}"
TARGET_WITHOUT_QUERY="${TARGET_URL%%\?*}"
TARGET_DB="${TARGET_WITHOUT_QUERY##*/}"

if [[ -z "${TARGET_DB}" || "${GOATFARM_RESTORE_CONFIRM:-}" != "${TARGET_DB}" ]]; then
    echo "Refusing restore: set GOATFARM_RESTORE_CONFIRM exactly to '${TARGET_DB}'" >&2
    exit 2
fi
if [[ ! -f "${BACKUP_PATH}" ]]; then
    echo "Backup does not exist: ${BACKUP_PATH}" >&2
    exit 2
fi
if [[ "${BACKUP_PATH}" == *.gpg ]]; then
    echo "Decrypt the backup to a protected temporary .dump file before restore" >&2
    exit 2
fi
if [[ -f "${BACKUP_PATH}.sha256" ]]; then
    if command -v sha256sum >/dev/null 2>&1; then
        (cd "$(dirname "${BACKUP_PATH}")" && sha256sum --check "$(basename "${BACKUP_PATH}").sha256")
    else
        expected_checksum="$(awk '{print $1}' "${BACKUP_PATH}.sha256")"
        actual_checksum="$(shasum -a 256 "${BACKUP_PATH}" | awk '{print $1}')"
        [[ "${actual_checksum}" == "${expected_checksum}" ]] || {
            echo "Backup checksum mismatch" >&2
            exit 2
        }
    fi
fi

pg_restore --list "${BACKUP_PATH}" >/dev/null
public_tables="$(
    psql "${TARGET_URL}" --no-psqlrc --tuples-only --no-align \
        --command "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
)"
if [[ "${public_tables}" != "0" ]]; then
    echo "Refusing restore: target database '${TARGET_DB}' is not empty" >&2
    exit 2
fi

pg_restore --exit-on-error --no-owner --dbname="${TARGET_URL}" "${BACKUP_PATH}"
restored_revision="$(
    psql "${TARGET_URL}" --no-psqlrc --tuples-only --no-align \
        --command "SELECT version_num FROM alembic_version"
)"
echo "Restore complete: ${TARGET_DB}, Alembic revision ${restored_revision}"
