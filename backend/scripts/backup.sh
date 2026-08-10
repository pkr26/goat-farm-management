#!/usr/bin/env bash
# Nightly PostgreSQL backup for Goat Farm Management.
#
# Usage: ./backend/scripts/backup.sh /var/backups/goatfarm
#
# A backup is published only after pg_dump completes, pg_restore can parse the
# archive, optional authenticated encryption succeeds, and its exact checksum
# sidecar has been written. A kernel advisory lock on a persistent destination
# inode serializes every producer using the same destination.

set -euo pipefail
umask 077

DEST_DIR="${1:-./backups}"
KEEP="${GOATFARM_BACKUP_KEEP:-30}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON_BIN="python3"
if [[ -x "${SCRIPT_DIR}/../.venv/bin/python" ]]; then
    PYTHON_BIN="${SCRIPT_DIR}/../.venv/bin/python"
fi

# GOATFARM_ENVIRONMENT and GOATFARM_DB_SSLMODE are *application* settings that
# gate TLS and mandatory GPG below.  A job that exports only
# GOATFARM_DATABASE_URL (the one variable this script reports as missing) would
# otherwise silently fall back to the development defaults on a production
# host: a plaintext, unsigned dump streamed over a connection this script then
# forces to `disable`.  Read backend/.env — the same file the application reads
# — for any value the caller did not export; an explicit export still wins.
app_setting() {
    local key="$1" env_file="${SCRIPT_DIR}/../.env"
    [[ -r "${env_file}" ]] || return 3
    "${PYTHON_BIN}" "${SCRIPT_DIR}/dotenv_value.py" "${env_file}" "${key}"
}

if [[ ${GOATFARM_DB_SSLMODE+defined} == defined ]]; then
    DB_SSLMODE="${GOATFARM_DB_SSLMODE}"
elif DB_SSLMODE="$(app_setting GOATFARM_DB_SSLMODE)"; then
    :
elif (( $? == 3 )); then
    DB_SSLMODE="disable"
else
    echo "Cannot safely load GOATFARM_DB_SSLMODE from backend/.env" >&2
    exit 2
fi
if [[ ${GOATFARM_ENVIRONMENT+defined} == defined ]]; then
    ENVIRONMENT="${GOATFARM_ENVIRONMENT}"
elif ENVIRONMENT="$(app_setting GOATFARM_ENVIRONMENT)"; then
    :
elif (( $? == 3 )); then
    ENVIRONMENT="development"
else
    echo "Cannot safely load GOATFARM_ENVIRONMENT from backend/.env" >&2
    exit 2
fi
S3_URI="${GOATFARM_BACKUP_S3_URI:-}"
GPG_RECIPIENT="${GOATFARM_BACKUP_GPG_RECIPIENT:-}"
GPG_SIGNER="${GOATFARM_BACKUP_GPG_SIGNER_FINGERPRINT:-}"

WORK_DIR=""
FLOCK_PATH="${DEST_DIR}/.goatfarm-backup.flock"
LEGACY_LOCK_DIR="${DEST_DIR}/.goatfarm-backup.lock"
LEGACY_LOCK_HELD=0
LEGACY_OWNER_TOKEN="$$-${RANDOM}-${RANDOM}"
LEGACY_CLAIM_DIR=""
LEGACY_CLAIM_SNAPSHOT=""
LEGACY_PUBLISHED_SNAPSHOT=""
FINAL_ARCHIVE=""
FINAL_CHECKSUM=""
PUBLISH_STARTED=0
LOCAL_PUBLISHED=0
REMOTE_ARCHIVE=""
REMOTE_CHECKSUM=""
REMOTE_ARCHIVE_UPLOADED=0
REMOTE_CHECKSUM_UPLOADED=0
REMOTE_COMPLETE=0

cleanup() {
    local status=$?
    trap - EXIT

    if (( status != 0 && REMOTE_COMPLETE == 0 )); then
        if (( REMOTE_CHECKSUM_UPLOADED == 1 )); then
            aws s3 rm "${REMOTE_CHECKSUM}" >/dev/null 2>&1 || true
        fi
        if (( REMOTE_ARCHIVE_UPLOADED == 1 )); then
            aws s3 rm "${REMOTE_ARCHIVE}" >/dev/null 2>&1 || true
        fi
    fi
    if (( status != 0 && PUBLISH_STARTED == 1 && LOCAL_PUBLISHED == 0 )); then
        [[ -z "${FINAL_ARCHIVE}" ]] || rm -f -- "${FINAL_ARCHIVE}"
        [[ -z "${FINAL_CHECKSUM}" ]] || rm -f -- "${FINAL_CHECKSUM}"
    fi
    if [[ -n "${WORK_DIR}" ]]; then
        case "${WORK_DIR}" in
            "${DEST_DIR}"/.goatfarm-backup.*) rm -rf -- "${WORK_DIR}" ;;
            *) echo "Refusing to clean unexpected work directory: ${WORK_DIR}" >&2 ;;
        esac
    fi
    if (( LEGACY_LOCK_HELD == 1 )); then
        legacy_release_dir="${DEST_DIR}/.goatfarm-backup.legacy-release.$$-${RANDOM}"
        if ! "${PYTHON_BIN}" "${SCRIPT_DIR}/backup_legacy_lock.py" \
            release-verified "${LEGACY_LOCK_DIR}" "${legacy_release_dir}" \
            "${LEGACY_PUBLISHED_SNAPSHOT}"; then
            echo "Refusing to clean a legacy lock not owned by this run" >&2
        fi
    fi
    if [[ -n "${LEGACY_CLAIM_DIR}" ]]; then
        if [[ -n "${LEGACY_CLAIM_SNAPSHOT}" ]]; then
            if ! "${PYTHON_BIN}" "${SCRIPT_DIR}/backup_legacy_lock.py" \
                delete-verified "${LEGACY_CLAIM_DIR}" "${LEGACY_CLAIM_SNAPSHOT}"; then
                echo "Refusing to clean a private legacy claim whose identity changed" >&2
            fi
        else
            echo "Preserving incomplete private legacy claim after initialization failure" >&2
        fi
    fi
    exit "${status}"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ -z "${GOATFARM_DATABASE_URL:-}" ]]; then
    echo "GOATFARM_DATABASE_URL is required" >&2
    exit 2
fi
if [[ ! "${KEEP}" =~ ^[1-9][0-9]*$ ]]; then
    echo "GOATFARM_BACKUP_KEEP must be a positive integer" >&2
    exit 2
fi
case "${DB_SSLMODE}" in
    disable|allow|prefer|require|verify-ca|verify-full) ;;
    *)
        echo "GOATFARM_DB_SSLMODE is invalid: ${DB_SSLMODE}" >&2
        exit 2
        ;;
esac
case "${ENVIRONMENT}" in
    development|production) ;;
    *)
        echo "GOATFARM_ENVIRONMENT is invalid: ${ENVIRONMENT}" >&2
        exit 2
        ;;
esac
if [[ "${ENVIRONMENT}" == "production" && "${DB_SSLMODE}" != "verify-full" ]]; then
    echo "Production backups require GOATFARM_DB_SSLMODE=verify-full" >&2
    exit 2
fi
if [[ -n "${S3_URI}" && ! "${S3_URI}" =~ ^s3://[^/]+(/.*)?$ ]]; then
    echo "GOATFARM_BACKUP_S3_URI must be an s3:// URI" >&2
    exit 2
fi
if [[ "${S3_URI}" == *$'\n'* || "${S3_URI}" == *$'\r'* ]]; then
    echo "GOATFARM_BACKUP_S3_URI contains a control character" >&2
    exit 2
fi
if [[ -n "${GPG_RECIPIENT}" && -z "${GPG_SIGNER}" \
    || -z "${GPG_RECIPIENT}" && -n "${GPG_SIGNER}" ]]; then
    echo "GPG backups require both recipient and signer fingerprint" >&2
    exit 2
fi
if [[ "${ENVIRONMENT}" == "production" || -n "${S3_URI}" ]]; then
    if [[ -z "${GPG_RECIPIENT}" || -z "${GPG_SIGNER}" ]]; then
        echo "Production and off-site backups require authenticated GPG encryption" >&2
        exit 2
    fi
    if [[ ! "${GPG_RECIPIENT}" =~ ^[0-9A-Fa-f]{40}([0-9A-Fa-f]{24})?$ ]]; then
        echo "Production and off-site GPG recipients must use a complete fingerprint" >&2
        exit 2
    fi
fi
if [[ -n "${GPG_SIGNER}" \
    && ! "${GPG_SIGNER}" =~ ^[0-9A-Fa-f]{40}([0-9A-Fa-f]{24})?$ ]]; then
    echo "GOATFARM_BACKUP_GPG_SIGNER_FINGERPRINT must be a 40- or 64-hex fingerprint" >&2
    exit 2
fi
if [[ "${GPG_RECIPIENT}" == *$'\n'* || "${GPG_RECIPIENT}" == *$'\r'* ]]; then
    echo "GOATFARM_BACKUP_GPG_RECIPIENT contains a control character" >&2
    exit 2
fi

# Keep the application URL out of every child process environment.  The helper
# reads it over stdin, removes the password from the libpq URL, and creates a
# mode-0600 PGPASSFILE inside this run's private work directory.
RAW_DB_URL="${GOATFARM_DATABASE_URL}"
unset GOATFARM_DATABASE_URL GOATFARM_MIGRATION_DATABASE_URL
unset PGPASSWORD PGSERVICE PGSERVICEFILE

install -d -m 0700 "${DEST_DIR}"
# FD 9 remains open in this shell and every database/encryption child. flock
# ownership therefore survives the tiny helper's exit and is released by the
# kernel only after the complete producer process tree exits, including after
# a kill or host crash. Never unlink or rename this persistent inode.
if [[ -L "${FLOCK_PATH}" || -e "${FLOCK_PATH}" && ! -f "${FLOCK_PATH}" ]]; then
    echo "Backup lock path is not a regular file: ${FLOCK_PATH}" >&2
    exit 2
fi
if ! exec 9>> "${FLOCK_PATH}"; then
    echo "Cannot open backup lock: ${FLOCK_PATH}" >&2
    exit 2
fi
if "${PYTHON_BIN}" "${SCRIPT_DIR}/backup_flock.py" 9 "${FLOCK_PATH}"; then
    :
else
    lock_status=$?
    if (( lock_status == 3 )); then
        echo "Backup already running (lock held: ${FLOCK_PATH})" >&2
        exit 3
    fi
    exit "${lock_status}"
fi

# One-release compatibility with the old mkdir lock. Inspect it only after the
# kernel lock is held, then atomically publish and hold a complete compatible
# claim for this run too. A dead old-format PID is not safe to steal: its shell
# may have died while a pg_dump child (which never inherited our new flock) is
# still running. Only a complete new-format claim proves all descendants were
# flock participants and can be reclaimed once FD 9 has been acquired.
legacy_stale_dir=""
legacy_stale_snapshot=""
if [[ -e "${LEGACY_LOCK_DIR}" || -L "${LEGACY_LOCK_DIR}" ]]; then
    if legacy_snapshot_output="$(
        "${PYTHON_BIN}" "${SCRIPT_DIR}/backup_legacy_lock.py" \
            snapshot "${LEGACY_LOCK_DIR}"
    )"; then
        :
    else
        echo "Legacy backup lock is malformed or has untrusted identity: ${LEGACY_LOCK_DIR}" >&2
        echo "Verify its PID and all old backup children are stopped, then remove it manually" >&2
        exit 3
    fi
    IFS=' ' read -r legacy_pid legacy_format legacy_snapshot legacy_extra \
        <<< "${legacy_snapshot_output}"
    unset legacy_snapshot_output
    if [[ ! "${legacy_pid}" =~ ^[1-9][0-9]*$ \
        || -z "${legacy_format}" || -z "${legacy_snapshot}" \
        || -n "${legacy_extra}" ]]; then
        echo "Legacy lock identity helper returned invalid metadata" >&2
        exit 3
    fi
    if legacy_pid_status="$(
        "${PYTHON_BIN}" "${SCRIPT_DIR}/backup_legacy_lock.py" \
            pid-status "${legacy_pid}"
    )"; then
        :
    else
        echo "Cannot safely determine legacy backup lock liveness" >&2
        exit 3
    fi
    if [[ "${legacy_pid_status}" == "live" ]]; then
        echo "Backup already running (live legacy lock: ${LEGACY_LOCK_DIR})" >&2
        exit 3
    fi
    if [[ "${legacy_pid_status}" != "dead" ]]; then
        echo "Legacy lock liveness helper returned invalid metadata" >&2
        exit 3
    fi
    if [[ "${legacy_format}" != "new" ]]; then
        echo "Dead old-format legacy backup lock requires manual verification: ${LEGACY_LOCK_DIR}" >&2
        echo "Verify PID ${legacy_pid} and all old backup children are stopped, then remove it manually" >&2
        exit 3
    fi
    echo "Reclaiming stale new-format legacy backup lock for dead PID ${legacy_pid}" >&2
    legacy_stale_dir="${DEST_DIR}/.goatfarm-backup.legacy-stale.$$-${RANDOM}"
    if [[ -e "${legacy_stale_dir}" || -L "${legacy_stale_dir}" ]]; then
        echo "Cannot reserve a private stale-lock path: ${legacy_stale_dir}" >&2
        exit 3
    fi
    if ! "${PYTHON_BIN}" "${SCRIPT_DIR}/backup_legacy_lock.py" \
        move-verified "${LEGACY_LOCK_DIR}" "${legacy_stale_dir}" \
        "${legacy_snapshot}"; then
        echo "Legacy backup lock identity changed during verified quarantine" >&2
        exit 3
    fi
    legacy_stale_snapshot="${legacy_snapshot}"
fi

# Prepare every claim file privately on the destination filesystem before the
# canonical name becomes visible. The exclusive rename is the cross-version
# atomic claim: old mkdir either wins and this run fails, or sees our already
# complete directory and fails. A kill or I/O failure during preparation can
# leave only a non-canonical private directory, never a permanent partial lock.
LEGACY_CLAIM_DIR="$(mktemp -d "${DEST_DIR}/.goatfarm-backup.legacy-claim.XXXXXX")"
chmod 0700 "${LEGACY_CLAIM_DIR}"
printf '%s\n' "$$" > "${LEGACY_CLAIM_DIR}/pid"
printf '%s\n' "${LEGACY_OWNER_TOKEN}" > "${LEGACY_CLAIM_DIR}/.flock-owner"
chmod 0600 "${LEGACY_CLAIM_DIR}/pid" "${LEGACY_CLAIM_DIR}/.flock-owner"
if claim_snapshot_output="$(
    "${PYTHON_BIN}" "${SCRIPT_DIR}/backup_legacy_lock.py" \
        snapshot "${LEGACY_CLAIM_DIR}"
)"; then
    :
else
    echo "Cannot verify prepared legacy backup claim" >&2
    exit 3
fi
IFS=' ' read -r claim_pid claim_format LEGACY_CLAIM_SNAPSHOT claim_extra \
    <<< "${claim_snapshot_output}"
unset claim_snapshot_output
if [[ "${claim_pid}" != "$$" || "${claim_format}" != "new" \
    || -z "${LEGACY_CLAIM_SNAPSHOT}" || -n "${claim_extra}" ]]; then
    echo "Prepared legacy claim helper returned invalid metadata" >&2
    exit 3
fi
LEGACY_PUBLISHED_SNAPSHOT="${LEGACY_CLAIM_SNAPSHOT}"
if ! "${PYTHON_BIN}" "${SCRIPT_DIR}/backup_legacy_lock.py" \
    move-verified "${LEGACY_CLAIM_DIR}" "${LEGACY_LOCK_DIR}" \
    "${LEGACY_CLAIM_SNAPSHOT}"; then
    if [[ -n "${legacy_stale_dir}" ]]; then
        "${PYTHON_BIN}" "${SCRIPT_DIR}/backup_legacy_lock.py" \
            delete-verified "${legacy_stale_dir}" "${legacy_stale_snapshot}" || true
    fi
    echo "Backup already running (legacy lock claim was won concurrently)" >&2
    exit 3
fi
LEGACY_LOCK_HELD=1
LEGACY_CLAIM_DIR=""
if [[ -n "${legacy_stale_dir}" ]]; then
    if ! "${PYTHON_BIN}" "${SCRIPT_DIR}/backup_legacy_lock.py" \
        delete-verified "${legacy_stale_dir}" "${legacy_stale_snapshot}"; then
        echo "Refusing to delete a quarantined legacy lock whose identity changed" >&2
        exit 3
    fi
fi

WORK_DIR="$(mktemp -d "${DEST_DIR}/.goatfarm-backup.XXXXXX")"
chmod 0700 "${WORK_DIR}"
PGPASSFILE="${WORK_DIR}/pgpass"
DB_METADATA="$(printf '%s' "${RAW_DB_URL}" | "${PYTHON_BIN}" "${SCRIPT_DIR}/libpq_url.py" "${PGPASSFILE}")"
unset RAW_DB_URL
PG_URL="${DB_METADATA%%$'\n'*}"
DB_NAME="${DB_METADATA#*$'\n'}"
if [[ -z "${PG_URL}" || -z "${DB_NAME}" || "${DB_NAME}" == *$'\n'* ]]; then
    echo "Database URL helper returned invalid metadata" >&2
    exit 2
fi
unset DB_METADATA DB_NAME
export PGPASSFILE PGSSLMODE="${DB_SSLMODE}"

TIMESTAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
if [[ ! "${TIMESTAMP}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}Z$ ]]; then
    echo "date returned an unsafe backup timestamp" >&2
    exit 1
fi
BASE_NAME="goatfarm-${TIMESTAMP}.dump"
if [[ -n "${GPG_RECIPIENT}" ]]; then
    BASE_NAME="${BASE_NAME}.gpg"
fi
FINAL_ARCHIVE="${DEST_DIR}/${BASE_NAME}"
FINAL_CHECKSUM="${FINAL_ARCHIVE}.sha256"
if [[ -e "${FINAL_ARCHIVE}" || -e "${FINAL_CHECKSUM}" ]]; then
    echo "Refusing to overwrite existing backup for timestamp ${TIMESTAMP}" >&2
    exit 2
fi

PLAIN_TMP="${WORK_DIR}/backup.dump"
echo "[$(date -Iseconds)] creating validated backup ${BASE_NAME}"
pg_dump --format=custom --no-owner --no-password \
    --dbname="${PG_URL}" --file="${PLAIN_TMP}"
rm -f -- "${PGPASSFILE}"
unset PGPASSFILE
if [[ ! -s "${PLAIN_TMP}" ]]; then
    echo "pg_dump produced an empty archive" >&2
    exit 1
fi
chmod 0600 "${PLAIN_TMP}"

# A successful pg_dump exit is insufficient: reject truncated or malformed
# custom archives before encryption or publication.
pg_restore --list "${PLAIN_TMP}" >/dev/null

ARTIFACT_TMP="${PLAIN_TMP}"
if [[ -n "${GPG_RECIPIENT}" ]]; then
    ARTIFACT_TMP="${WORK_DIR}/backup.dump.gpg"
    gpg --batch --yes --local-user "${GPG_SIGNER}" \
        --recipient "${GPG_RECIPIENT}" --sign --encrypt \
        --output "${ARTIFACT_TMP}" "${PLAIN_TMP}"
    if [[ ! -s "${ARTIFACT_TMP}" ]]; then
        echo "GPG produced an empty backup" >&2
        exit 1
    fi
    chmod 0600 "${ARTIFACT_TMP}"
    rm -f -- "${PLAIN_TMP}"
fi

if command -v sha256sum >/dev/null 2>&1; then
    checksum_output="$(sha256sum "${ARTIFACT_TMP}")"
else
    checksum_output="$(shasum -a 256 "${ARTIFACT_TMP}")"
fi
checksum="${checksum_output%%[[:space:]]*}"
unset checksum_output
if [[ ! "${checksum}" =~ ^[0-9A-Fa-f]{64}$ ]]; then
    echo "Checksum utility returned an invalid SHA-256 digest" >&2
    exit 1
fi
checksum="$(printf '%s' "${checksum}" | tr 'A-F' 'a-f')"
CHECKSUM_TMP="${WORK_DIR}/backup.sha256"
printf '%s  %s\n' "${checksum}" "${BASE_NAME}" > "${CHECKSUM_TMP}"
chmod 0600 "${CHECKSUM_TMP}"

# Publish the sidecar first.  Until the archive rename, restore sees no
# candidate; if the second rename fails the EXIT trap removes the sidecar.
PUBLISH_STARTED=1
mv "${CHECKSUM_TMP}" "${FINAL_CHECKSUM}"
mv "${ARTIFACT_TMP}" "${FINAL_ARCHIVE}"
LOCAL_PUBLISHED=1

if [[ -n "${S3_URI}" ]]; then
    REMOTE_ARCHIVE="${S3_URI%/}/${BASE_NAME}"
    REMOTE_CHECKSUM="${REMOTE_ARCHIVE}.sha256"
    s3_location="${S3_URI#s3://}"
    s3_bucket="${s3_location%%/*}"
    if [[ "${s3_location}" == */* ]]; then
        s3_prefix="${s3_location#*/}"
        s3_prefix="${s3_prefix%/}"
    else
        s3_prefix=""
    fi
    remote_key="${BASE_NAME}"
    if [[ -n "${s3_prefix}" ]]; then
        remote_key="${s3_prefix}/${BASE_NAME}"
    fi
    # Never overwrite (or later clean up) an object from another producer.
    # Listing the exact archive-key prefix also catches a pre-existing
    # sidecar. An AWS/auth/network failure exits under `set -e`, fail closed.
    remote_collision="$(
        aws s3api list-objects-v2 --bucket "${s3_bucket}" --prefix "${remote_key}" \
            --max-keys 1 --query 'Contents[].Key' --output text
    )"
    if [[ -n "${remote_collision}" && "${remote_collision}" != "None" ]]; then
        echo "Refusing to overwrite existing remote backup key: ${remote_key}" >&2
        exit 2
    fi
    echo "[$(date -Iseconds)] uploading authenticated backup to ${S3_URI%/}/"
    aws s3 cp --sse AES256 "${FINAL_ARCHIVE}" "${REMOTE_ARCHIVE}"
    REMOTE_ARCHIVE_UPLOADED=1
    aws s3 cp --sse AES256 "${FINAL_CHECKSUM}" "${REMOTE_CHECKSUM}"
    REMOTE_CHECKSUM_UPLOADED=1
    REMOTE_COMPLETE=1
fi

echo "[$(date -Iseconds)] pruning to newest ${KEEP} dumps"
shopt -s nullglob
backup_files=("${DEST_DIR}"/goatfarm-*.dump "${DEST_DIR}"/goatfarm-*.dump.gpg)
if (( ${#backup_files[@]} > KEEP )); then
    # Generated timestamps are lexically sortable.  Sort the quoted array in
    # Bash so even a hostile newline-bearing filename can never become a
    # second deletion target through line-oriented `ls` parsing.
    for ((i = 0; i < ${#backup_files[@]}; i++)); do
        for ((j = i + 1; j < ${#backup_files[@]}; j++)); do
            if [[ "${backup_files[j]}" > "${backup_files[i]}" ]]; then
                swap="${backup_files[i]}"
                backup_files[i]="${backup_files[j]}"
                backup_files[j]="${swap}"
            fi
        done
    done
    for ((i = KEEP; i < ${#backup_files[@]}; i++)); do
        rm -f -- "${backup_files[i]}" "${backup_files[i]}.sha256"
    done
fi

echo "[$(date -Iseconds)] backup complete: ${FINAL_ARCHIVE}"
