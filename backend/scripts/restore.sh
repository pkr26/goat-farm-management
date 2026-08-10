#!/usr/bin/env bash
# Restore one verified custom-format backup into an EMPTY PostgreSQL database.
# This script never cleans or overwrites an existing user schema.

set -euo pipefail
umask 077

if [[ $# -ne 1 ]]; then
    echo "Usage: GOATFARM_RESTORE_DATABASE_URL=<url> GOATFARM_RESTORE_CONFIRM=<database> $0 BACKUP.dump" >&2
    exit 2
fi
if [[ -z "${GOATFARM_RESTORE_DATABASE_URL:-}" ]]; then
    echo "GOATFARM_RESTORE_DATABASE_URL is required" >&2
    exit 2
fi

BACKUP_SOURCE="$1"
RAW_TARGET_URL="${GOATFARM_RESTORE_DATABASE_URL}"
# Never pass the credential-bearing target URL to a child process.  It is an
# environment input rather than a positional argument so it is absent from the
# restore process argv too.
unset GOATFARM_RESTORE_DATABASE_URL GOATFARM_DATABASE_URL
unset GOATFARM_MIGRATION_DATABASE_URL PGPASSWORD PGSERVICE PGSERVICEFILE
EXPECTED_SIGNER="${GOATFARM_RESTORE_GPG_SIGNER_FINGERPRINT:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON_BIN="python3"
if [[ -x "${SCRIPT_DIR}/../.venv/bin/python" ]]; then
    PYTHON_BIN="${SCRIPT_DIR}/../.venv/bin/python"
fi

# Settings resolution and validation live in backup_env.sh, shared verbatim
# with backup.sh so the two scripts cannot drift apart on the safety gates
# (TLS and the signed-and-encrypted artifact requirement below).
source "${SCRIPT_DIR}/backup_env.sh"
load_app_setting DB_SSLMODE GOATFARM_DB_SSLMODE disable
load_app_setting ENVIRONMENT GOATFARM_ENVIRONMENT development
TMP_ROOT="${TMPDIR:-/tmp}"
TMP_DIR=""

cleanup() {
    local status=$?
    trap - EXIT
    if [[ -n "${TMP_DIR}" ]]; then
        case "${TMP_DIR}" in
            "${TMP_ROOT}"/goatfarm-restore.*) rm -rf -- "${TMP_DIR}" ;;
            *) echo "Refusing to clean unexpected restore directory: ${TMP_DIR}" >&2 ;;
        esac
    fi
    exit "${status}"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

verify_checksum() {
    local archive="$1"
    local sidecar="${archive}.sha256"
    local expected_name="${archive##*/}"
    local checksum_line=""
    local line=""
    local line_count=0
    local expected_checksum=""
    local checksum_output=""
    local actual_checksum=""
    local sidecar_size=""
    local checksum_pattern='^([0-9A-Fa-f]{64})  ([^/[:space:]]+)$'

    if [[ ! -f "${sidecar}" || -L "${sidecar}" ]]; then
        echo "Refusing restore: checksum is missing or not a regular file: ${sidecar}" >&2
        exit 2
    fi
    sidecar_size="$(wc -c < "${sidecar}")"
    sidecar_size="${sidecar_size//[[:space:]]/}"
    if [[ ! "${sidecar_size}" =~ ^[0-9]+$ ]]; then
        echo "Refusing restore: checksum sidecar has an invalid size" >&2
        exit 2
    fi
    if (( sidecar_size < 1 || sidecar_size > 512 )); then
        echo "Refusing restore: checksum sidecar has an invalid size" >&2
        exit 2
    fi
    while IFS= read -r line || [[ -n "${line}" ]]; do
        line_count=$((line_count + 1))
        checksum_line="${line}"
    done < "${sidecar}"
    if (( line_count != 1 )); then
        echo "Refusing restore: checksum sidecar must contain exactly one record" >&2
        exit 2
    fi
    if [[ ! "${checksum_line}" =~ ${checksum_pattern} ]]; then
        echo "Refusing restore: checksum record has an invalid format" >&2
        exit 2
    fi
    expected_checksum="${BASH_REMATCH[1]}"
    if [[ "${BASH_REMATCH[2]}" != "${expected_name}" ]]; then
        echo "Refusing restore: checksum record does not name ${expected_name}" >&2
        exit 2
    fi

    if command -v sha256sum >/dev/null 2>&1; then
        checksum_output="$(sha256sum "${archive}")"
    else
        checksum_output="$(shasum -a 256 "${archive}")"
    fi
    actual_checksum="${checksum_output%%[[:space:]]*}"
    expected_checksum="$(printf '%s' "${expected_checksum}" | tr 'A-F' 'a-f')"
    actual_checksum="$(printf '%s' "${actual_checksum}" | tr 'A-F' 'a-f')"
    if [[ ! "${actual_checksum}" =~ ^[0-9a-f]{64}$ \
        || "${actual_checksum}" != "${expected_checksum}" ]]; then
        echo "Refusing restore: backup checksum mismatch" >&2
        exit 2
    fi
}

validate_app_settings
if [[ "${ENVIRONMENT}" == "production" && "${DB_SSLMODE}" != "verify-full" ]]; then
    echo "Production restores require GOATFARM_DB_SSLMODE=verify-full" >&2
    exit 2
fi
if [[ ! -f "${BACKUP_SOURCE}" || -L "${BACKUP_SOURCE}" ]]; then
    echo "Backup does not exist or is not a regular file: ${BACKUP_SOURCE}" >&2
    exit 2
fi
if [[ ! -f "${BACKUP_SOURCE}.sha256" || -L "${BACKUP_SOURCE}.sha256" ]]; then
    echo "Refusing restore: checksum is missing or not a regular file: ${BACKUP_SOURCE}.sha256" >&2
    exit 2
fi
if [[ "${ENVIRONMENT}" == "production" && "${BACKUP_SOURCE}" != *.gpg ]]; then
    echo "Production restores require an authenticated GPG backup" >&2
    exit 2
fi
if [[ "${BACKUP_SOURCE}" == *.gpg ]]; then
    if [[ -z "${EXPECTED_SIGNER}" ]]; then
        echo "Encrypted restores require GOATFARM_RESTORE_GPG_SIGNER_FINGERPRINT" >&2
        exit 2
    fi
    if [[ ! "${EXPECTED_SIGNER}" =~ ^[0-9A-Fa-f]{40}([0-9A-Fa-f]{24})?$ ]]; then
        echo "GOATFARM_RESTORE_GPG_SIGNER_FINGERPRINT must be a 40- or 64-hex fingerprint" >&2
        exit 2
    fi
fi

if [[ ! -d "${TMP_ROOT}" ]]; then
    echo "Restore temporary root does not exist: ${TMP_ROOT}" >&2
    exit 2
fi
TMP_DIR="$(mktemp -d "${TMP_ROOT}/goatfarm-restore.XXXXXX")"
chmod 0700 "${TMP_DIR}"
PGPASSFILE="${TMP_DIR}/pgpass"
SOURCE_DIR="${TMP_DIR}/source"
mkdir "${SOURCE_DIR}"
chmod 0700 "${SOURCE_DIR}"

# Work only from a private snapshot.  The source can be on removable/shared
# storage; reopening it after verification would let a replacement race swap
# different bytes into GPG or pg_restore.
BACKUP_PATH="${SOURCE_DIR}/${BACKUP_SOURCE##*/}"
cp -- "${BACKUP_SOURCE}" "${BACKUP_PATH}"
cp -- "${BACKUP_SOURCE}.sha256" "${BACKUP_PATH}.sha256"
chmod 0600 "${BACKUP_PATH}" "${BACKUP_PATH}.sha256"

# The URL travels over stdin to keep its password out of helper and libpq-tool
# arguments.  Also remove any application URL inherited from the caller before
# spawning database tools.
DB_METADATA="$(printf '%s' "${RAW_TARGET_URL}" | "${PYTHON_BIN}" "${SCRIPT_DIR}/libpq_url.py" "${PGPASSFILE}")"
unset RAW_TARGET_URL
TARGET_URL="${DB_METADATA%%$'\n'*}"
TARGET_DB="${DB_METADATA#*$'\n'}"
if [[ -z "${TARGET_URL}" || -z "${TARGET_DB}" || "${TARGET_DB}" == *$'\n'* ]]; then
    echo "Database URL helper returned invalid metadata" >&2
    exit 2
fi
unset DB_METADATA
if [[ "${GOATFARM_RESTORE_CONFIRM:-}" != "${TARGET_DB}" ]]; then
    echo "Refusing restore: set GOATFARM_RESTORE_CONFIRM exactly to '${TARGET_DB}'" >&2
    exit 2
fi

verify_checksum "${BACKUP_PATH}"

RESTORE_ARCHIVE="${BACKUP_PATH}"
if [[ "${BACKUP_PATH}" == *.gpg ]]; then
    GPG_STATUS="${TMP_DIR}/gpg.status"
    RESTORE_ARCHIVE="${TMP_DIR}/restore.dump"
    gpg --batch --yes --status-fd 3 --decrypt \
        --output "${RESTORE_ARCHIVE}" "${BACKUP_PATH}" 3> "${GPG_STATUS}"

    valid_signatures=0
    matching_signatures=0
    bad_signature=0
    while IFS=' ' read -r marker keyword fingerprint _rest; do
        [[ "${marker}" == "[GNUPG:]" ]] || continue
        case "${keyword}" in
            VALIDSIG)
                valid_signatures=$((valid_signatures + 1))
                normalized_fingerprint="$(printf '%s' "${fingerprint}" | tr 'a-f' 'A-F')"
                normalized_expected="$(printf '%s' "${EXPECTED_SIGNER}" | tr 'a-f' 'A-F')"
                primary_fingerprint="${_rest##* }"
                normalized_primary=""
                if [[ "${primary_fingerprint}" =~ ^[0-9A-Fa-f]{40}([0-9A-Fa-f]{24})?$ ]]; then
                    normalized_primary="$(printf '%s' "${primary_fingerprint}" | tr 'a-f' 'A-F')"
                fi
                if [[ "${normalized_fingerprint}" == "${normalized_expected}" \
                    || "${normalized_primary}" == "${normalized_expected}" ]]; then
                    matching_signatures=$((matching_signatures + 1))
                fi
                ;;
            BADSIG|ERRSIG|NO_PUBKEY|EXPSIG|EXPKEYSIG|REVKEYSIG|NODATA|FAILURE)
                bad_signature=1
                ;;
        esac
    done < "${GPG_STATUS}"
    if (( bad_signature != 0 || valid_signatures != 1 || matching_signatures != 1 )); then
        echo "Refusing restore: GPG signature is missing, invalid, or from an unexpected signer" >&2
        exit 2
    fi
    if [[ ! -s "${RESTORE_ARCHIVE}" ]]; then
        echo "Refusing restore: GPG produced an empty archive" >&2
        exit 2
    fi
    chmod 0600 "${RESTORE_ARCHIVE}"
fi

# Validate archive structure before connecting to the target database.
pg_restore --list "${RESTORE_ARCHIVE}" >/dev/null

export PGPASSFILE PGSSLMODE="${DB_SSLMODE}"
read -r -d '' EMPTY_DATABASE_SQL <<'SQL' || true
WITH user_namespaces AS (
    SELECT oid, nspname
    FROM pg_namespace
    WHERE nspname NOT IN ('pg_catalog', 'information_schema')
      AND nspname NOT LIKE 'pg_toast%'
      AND nspname NOT LIKE 'pg_temp_%'
), namespaced_objects AS (
    SELECT relnamespace AS namespace_oid FROM pg_class
    UNION ALL SELECT pronamespace FROM pg_proc
    UNION ALL SELECT typnamespace FROM pg_type
    UNION ALL SELECT oprnamespace FROM pg_operator
    UNION ALL SELECT collnamespace FROM pg_collation
    UNION ALL SELECT connamespace FROM pg_conversion
    UNION ALL SELECT opcnamespace FROM pg_opclass
    UNION ALL SELECT opfnamespace FROM pg_opfamily
    UNION ALL SELECT stxnamespace FROM pg_statistic_ext
    UNION ALL SELECT cfgnamespace FROM pg_ts_config
    UNION ALL SELECT dictnamespace FROM pg_ts_dict
    UNION ALL SELECT prsnamespace FROM pg_ts_parser
    UNION ALL SELECT tmplnamespace FROM pg_ts_template
    UNION ALL SELECT extnamespace FROM pg_extension
)
SELECT
    (SELECT count(*) FROM user_namespaces WHERE nspname <> 'public')
    +
    (SELECT count(*) FROM namespaced_objects o
     JOIN user_namespaces n ON n.oid = o.namespace_oid);
SQL
user_object_count="$(
    psql --no-psqlrc --no-password --set=ON_ERROR_STOP=1 \
        --tuples-only --no-align --dbname="${TARGET_URL}" \
        --command "${EMPTY_DATABASE_SQL}"
)"
user_object_count="${user_object_count//[[:space:]]/}"
if [[ ! "${user_object_count}" =~ ^[0-9]+$ || "${user_object_count}" != "0" ]]; then
    echo "Refusing restore: target database '${TARGET_DB}' contains user schema objects" >&2
    exit 2
fi

# One transaction guarantees that any restore error rolls the target back to
# its validated empty state.
pg_restore --single-transaction --exit-on-error --no-owner --no-password \
    --dbname="${TARGET_URL}" "${RESTORE_ARCHIVE}"

# A completed archive is not considered usable unless it restored exactly one
# syntactically valid Alembic revision marker.
read -r -d '' ALEMBIC_SANITY_SQL <<'SQL' || true
SELECT CASE
    WHEN count(*) = 1 AND min(version_num) ~ '^[0-9a-f]{12}$'
    THEN min(version_num)
    ELSE ''
END
FROM alembic_version;
SQL
restored_revision="$(
    psql --no-psqlrc --no-password --set=ON_ERROR_STOP=1 \
        --tuples-only --no-align --dbname="${TARGET_URL}" \
        --command "${ALEMBIC_SANITY_SQL}"
)"
restored_revision="${restored_revision//[[:space:]]/}"
if [[ ! "${restored_revision}" =~ ^[0-9a-f]{12}$ ]]; then
    echo "Restore completed without a valid Alembic revision" >&2
    exit 1
fi

rm -f -- "${PGPASSFILE}"
unset PGPASSFILE
echo "Restore complete: ${TARGET_DB}, Alembic revision ${restored_revision}"
