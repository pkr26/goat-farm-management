# Shared application-settings loading for backup.sh and restore.sh (sourced).
#
# GOATFARM_ENVIRONMENT and GOATFARM_DB_SSLMODE jointly gate TLS and mandatory
# GPG in both scripts. An optional private-CA path travels with them. Resolve
# all three from one descriptor-pinned backend/.env
# snapshot: independent lookups could observe different atomic replacements
# and combine a production value with a development default. The helper uses
# the application's python-dotenv grammar. Explicit non-empty exports still
# win independently, and empty values fall back to the file/default exactly
# like the historical ${VAR:-default} resolution did.
#
# One copy, sourced by both scripts: four hand-copied ladders drifted once and
# would again, and a drifted copy silently weakens one side's production gate.
#
# Requires SCRIPT_DIR and PYTHON_BIN to be set by the sourcing script.

_missing_env_file_classification() {
    echo "backend/.env is missing while GOATFARM_ENVIRONMENT/GOATFARM_DB_SSLMODE are unset;" >&2
    echo "export both explicitly to classify this run (production or development)" >&2
    exit 2
}

# Resolve the two settings that jointly decide whether plaintext transport or
# an unsigned artifact is allowed. They must come from one .env snapshot: two
# independent helper calls could observe an atomic file replacement/removal in
# between and combine old verify-full with a defaulted development environment.
# Explicit non-empty shell/environment values still win independently.
load_app_safety_settings() {
    local env_file="${SCRIPT_DIR}/../.env"
    local snapshot="" status=0 file_sslmode="" file_environment="" file_rootcert="" remainder=""

    if [[ ${GOATFARM_DB_SSLMODE:+defined} == defined \
        && ${GOATFARM_ENVIRONMENT:+defined} == defined ]]; then
        DB_SSLMODE="${GOATFARM_DB_SSLMODE}"
        ENVIRONMENT="${GOATFARM_ENVIRONMENT}"
        DB_SSLROOTCERT_PATH="${GOATFARM_DB_SSLROOTCERT_PATH:-}"
        return 0
    fi

    # An absent backend/.env must not silently classify the run as
    # development (plaintext local backups, no GPG, sslmode disable).  The
    # descriptor-pinned snapshot defends against split reads, not absence,
    # and a cron-run backup job never imports the application to notice the
    # missing file.  Require the operator to classify the run explicitly;
    # a present file that simply omits a key still falls back to the
    # documented defaults exactly like config.py does.  The sentinel default
    # catches a removal between the existence check and the pinned read.
    if [[ ! -f "${env_file}" ]]; then
        _missing_env_file_classification
    fi

    snapshot="$(
        "${PYTHON_BIN}" "${SCRIPT_DIR}/dotenv_value.py" "${env_file}" \
            --snapshot GOATFARM_DB_SSLMODE __ENV_FILE_ABSENT__ \
            GOATFARM_ENVIRONMENT __ENV_FILE_ABSENT__ \
            GOATFARM_DB_SSLROOTCERT_PATH __ENV_FILE_ABSENT__
    )" || status=$?
    if (( status != 0 )); then
        echo "Cannot safely load deployment settings from backend/.env" >&2
        exit 2
    fi
    if [[ "${snapshot}" != *$'\n'* ]]; then
        echo "Deployment settings helper returned invalid metadata" >&2
        exit 2
    fi
    file_sslmode="${snapshot%%$'\n'*}"
    remainder="${snapshot#*$'\n'}"
    if [[ "${remainder}" != *$'\n'* ]]; then
        echo "Deployment settings helper returned invalid metadata" >&2
        exit 2
    fi
    file_environment="${remainder%%$'\n'*}"
    file_rootcert="${remainder#*$'\n'}"
    if [[ "${file_rootcert}" == *$'\n'* ]]; then
        echo "Deployment settings helper returned invalid metadata" >&2
        exit 2
    fi

    # A sentinel means either the file vanished between the existence check
    # and this pinned read (fail closed) or a present file that legitimately
    # omits the key (keep the documented config.py defaults). Distinguish by
    # the file's presence now; a vanished file must never default to
    # development. Per-key fallback keeps a file that classifies only one of
    # the two settings honest.
    if [[ "${file_sslmode}" == __ENV_FILE_ABSENT__ ]]; then
        if [[ ! -f "${env_file}" ]]; then
            _missing_env_file_classification
        fi
        file_sslmode="disable"
    fi
    if [[ "${file_environment}" == __ENV_FILE_ABSENT__ ]]; then
        if [[ ! -f "${env_file}" ]]; then
            _missing_env_file_classification
        fi
        file_environment="development"
    fi
    if [[ "${file_rootcert}" == __ENV_FILE_ABSENT__ ]]; then
        # Unlike environment/sslmode this value is optional. Its sentinel is
        # a complete, pinned answer from the same snapshot even if a racing
        # replacement removes the pathname immediately afterward.
        file_rootcert=""
    fi

    DB_SSLMODE="${GOATFARM_DB_SSLMODE:-${file_sslmode}}"
    ENVIRONMENT="${GOATFARM_ENVIRONMENT:-${file_environment}}"
    DB_SSLROOTCERT_PATH="${GOATFARM_DB_SSLROOTCERT_PATH:-${file_rootcert}}"
}

# Validate the resolved DB_SSLMODE and ENVIRONMENT exactly like config.py's
# accepted sets; both scripts must agree on what a valid deployment looks like.
validate_app_settings() {
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
    if [[ -n "${DB_SSLROOTCERT_PATH:-}" ]]; then
        case "${DB_SSLMODE}" in
            verify-ca|verify-full) ;;
            *)
                echo "GOATFARM_DB_SSLROOTCERT_PATH requires GOATFARM_DB_SSLMODE=verify-ca or verify-full" >&2
                exit 2
                ;;
        esac
        if [[ ! -f "${DB_SSLROOTCERT_PATH}" || ! -r "${DB_SSLROOTCERT_PATH}" ]]; then
            echo "GOATFARM_DB_SSLROOTCERT_PATH must name a readable CA certificate file" >&2
            exit 2
        fi
    fi
}
