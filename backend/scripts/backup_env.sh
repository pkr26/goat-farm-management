# Shared application-settings loading for backup.sh and restore.sh (sourced).
#
# GOATFARM_ENVIRONMENT and GOATFARM_DB_SSLMODE are *application* settings that
# gate TLS and mandatory GPG in both scripts. A job that exports only its
# database URL would otherwise silently fall back to the development defaults
# on a production host: a plaintext, unsigned dump streamed over a connection
# these scripts then force to `disable`. Read backend/.env — the same file the
# application reads, via the application's own python-dotenv grammar — for any
# value the caller did not export; an explicit non-empty export still wins,
# and an empty value (exported or in the file) falls back to the default
# exactly like the historical ${VAR:-default} resolution did.
#
# One copy, sourced by both scripts: four hand-copied ladders drifted once and
# would again, and a drifted copy silently weakens one side's production gate.
#
# Requires SCRIPT_DIR and PYTHON_BIN to be set by the sourcing script.

app_setting() {
    local key="$1" env_file="${SCRIPT_DIR}/../.env"
    # No shell readability pre-check here: dotenv_value.py distinguishes an
    # absent file (status 3 — defaults apply) from an unreadable/unparseable
    # one (status 2 — fail closed). A `-r` test conflated the two and let an
    # unreadable production .env silently degrade to development defaults.
    "${PYTHON_BIN}" "${SCRIPT_DIR}/dotenv_value.py" "${env_file}" "${key}"
}

# load_app_setting VAR KEY DEFAULT — resolve KEY into VAR: non-empty exported
# KEY wins; else backend/.env; else DEFAULT. Exits 2 whenever backend/.env
# exists but cannot be read safely.
load_app_setting() {
    local var="$1" key="$2" default="$3" value="" status=0
    if [[ ${!key:+defined} == defined ]]; then
        printf -v "${var}" '%s' "${!key}"
        return 0
    fi
    value="$(app_setting "${key}")" || status=$?
    if (( status == 0 )); then
        printf -v "${var}" '%s' "${value}"
    elif (( status == 3 )); then
        printf -v "${var}" '%s' "${default}"
    else
        echo "Cannot safely load ${key} from backend/.env" >&2
        exit 2
    fi
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
