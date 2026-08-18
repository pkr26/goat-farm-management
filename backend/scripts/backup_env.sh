# Shared application-settings loading for backup.sh and restore.sh (sourced).
#
# GOATFARM_ENVIRONMENT and GOATFARM_DB_SSLMODE jointly gate TLS and mandatory
# GPG in both scripts. Resolve both from one descriptor-pinned backend/.env
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

# Resolve the two settings that jointly decide whether plaintext transport or
# an unsigned artifact is allowed. They must come from one .env snapshot: two
# independent helper calls could observe an atomic file replacement/removal in
# between and combine old verify-full with a defaulted development environment.
# Explicit non-empty shell/environment values still win independently.
load_app_safety_settings() {
    local env_file="${SCRIPT_DIR}/../.env"
    local snapshot="" status=0 file_sslmode="" file_environment="" remainder=""

    if [[ ${GOATFARM_DB_SSLMODE:+defined} == defined \
        && ${GOATFARM_ENVIRONMENT:+defined} == defined ]]; then
        DB_SSLMODE="${GOATFARM_DB_SSLMODE}"
        ENVIRONMENT="${GOATFARM_ENVIRONMENT}"
        return 0
    fi

    snapshot="$(
        "${PYTHON_BIN}" "${SCRIPT_DIR}/dotenv_value.py" "${env_file}" \
            --snapshot GOATFARM_DB_SSLMODE disable \
            GOATFARM_ENVIRONMENT development
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
    if [[ "${remainder}" == *$'\n'* ]]; then
        echo "Deployment settings helper returned invalid metadata" >&2
        exit 2
    fi
    file_environment="${remainder}"

    DB_SSLMODE="${GOATFARM_DB_SSLMODE:-${file_sslmode}}"
    ENVIRONMENT="${GOATFARM_ENVIRONMENT:-${file_environment}}"
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
}
