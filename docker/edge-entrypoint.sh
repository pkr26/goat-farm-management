#!/bin/sh
# Runtime guard and launcher for the public nginx edge.
#
# The frontend image is intentionally deployment-neutral: release images are
# built once and cannot know a particular farm's public S3/MinIO origin.  CSP
# therefore belongs at the edge, where Compose supplies the deployment value
# at startup.  Validate before rendering the nginx template so an accidental
# value cannot turn into a looser (or syntactically broken) policy.

set -eu
set -f

fail() {
  echo "edge configuration error: $*" >&2
  exit 2
}

screening_enabled() {
  # Accept exactly the boolean spellings pydantic-settings accepts
  # (case-insensitively), so a value the API and worker boot with cannot
  # take down the edge — the single published listener.
  value=$(printf '%s' "${GOATFARM_SCREENING_ENABLED:-false}" | tr '[:upper:]' '[:lower:]')
  case "$value" in
    1|true|yes|on|t|y) return 0 ;;
    ''|0|false|no|off|f|n) return 1 ;;
    *) fail "GOATFARM_SCREENING_ENABLED must be a boolean" ;;
  esac
}

validate_origin_port() {
  name=$1
  port=$2
  protocol=$3

  case "$port" in
    *[!0-9]*|"") fail "$name contains a non-numeric $protocol port" ;;
  esac
  # Normalize leading zeroes without handing an attacker-controlled value to
  # shell arithmetic (which can overflow or use non-portable octal rules).
  [ "${#port}" -le 10 ] || fail "$name contains an out-of-range $protocol port"
  normalized_port=$port
  while [ -n "$normalized_port" ] && [ "${normalized_port#0}" != "$normalized_port" ]; do
    normalized_port=${normalized_port#0}
  done
  [ -n "$normalized_port" ] || normalized_port=0
  if [ "$normalized_port" = "0" ] \
    || [ "${#normalized_port}" -gt 5 ] \
    || { [ "${#normalized_port}" -eq 5 ] && [ "$normalized_port" \> "65535" ]; }; then
    fail "$name contains an out-of-range $protocol port"
  fi
}

validate_https_hostname() {
  name=$1
  hostname=$2

  # CSP source hosts are ASCII DNS names or IPv4 literals here. Rejecting
  # malformed labels is not just cosmetic: a browser silently drops an
  # invalid source expression, turning a successfully started screening edge
  # into a broken upload/display flow.
  case "$hostname" in
    ""|.*|*.) fail "$name contains an invalid HTTPS hostname" ;;
    *..*) fail "$name contains an invalid HTTPS hostname" ;;
    *[!A-Za-z0-9.-]*) fail "$name contains an invalid HTTPS hostname" ;;
  esac
  [ "${#hostname}" -le 253 ] || fail "$name contains an overlong HTTPS hostname"

  remaining_labels=$hostname
  while [ -n "$remaining_labels" ]; do
    label=${remaining_labels%%.*}
    case "$label" in
      ""|-*|*-) fail "$name contains an invalid HTTPS hostname label" ;;
    esac
    [ "${#label}" -le 63 ] || fail "$name contains an overlong HTTPS hostname label"
    if [ "$remaining_labels" = "$label" ]; then
      remaining_labels=""
    else
      remaining_labels=${remaining_labels#*.}
    fi
  done
}

validate_https_ipv6_literal() {
  name=$1
  literal=$2

  # A bracketed origin is an IPv6 literal, not an arbitrary bracketed token.
  # Keep the validator shell-only (the nginx image has no Python runtime),
  # but reject non-hex syntax and clearly impossible repeated-compression
  # forms before inserting it into a CSP header.
  case "$literal" in
    ""|*[!0-9A-Fa-f:.]*|*:::*|*::*::*) fail "$name contains an invalid HTTPS IPv6 literal" ;;
    *:*) ;;
    *) fail "$name contains an invalid HTTPS IPv6 literal" ;;
  esac
}

validate_csp_sources() {
  name=$1
  sources=$2

  # CSP source expressions here are deliberately limited to space-separated
  # HTTP(S) origins. This keeps the value safe to splice into a
  # quoted nginx header and avoids accepting a path/query that browsers would
  # silently interpret differently.  Commas are rejected: CSP grammar uses
  # whitespace, not CSV.
  case "$sources" in
    *','*) fail "$name must be space-separated origins, not comma-separated" ;;
  esac
  # An all-space value would otherwise split into zero source expressions:
  # when screening is on that silently produces a CSP without the bucket.
  # Empty is allowed only while screening is disabled; whitespace alone is
  # never a useful source list.
  case "$sources" in
    ""|*[![:space:]]*) ;;
    *) fail "$name must not contain only whitespace" ;;
  esac

  # A command substitution removes trailing newlines. Check non-literal-space
  # whitespace with a printable sentinel *before* the unsafe-character check,
  # so a source ending in a newline cannot disappear from ``unsafe`` and turn
  # into an extra nginx configuration line during sed rendering. The C locale
  # keeps this an ASCII-only origin contract; tabs, CR/LF, other controls, and
  # non-ASCII whitespace all fail closed.
  # Use a tab sentinel because the filter removes every printable character
  # (including a tempting ``X`` sentinel); command substitution preserves the
  # final tab while it would otherwise trim a final newline from ``sources``.
  literal_space_sentinel=$(printf '\t')
  nonliteral_space=$(printf '%s\t' "$sources" | LC_ALL=C tr -d '[:graph:] ')
  if [ "$nonliteral_space" != "$literal_space_sentinel" ]; then
    fail "$name must use literal ASCII spaces between origins"
  fi

  # Permit only ASCII origin syntax plus literal spaces as separators. In
  # particular, reject sed replacement metacharacters (& and |), newlines,
  # quotes and shell/config delimiters before the template is touched.
  unsafe=$(printf '%s' "$sources" | LC_ALL=C tr -d '[:alnum:].:/_[]- ')
  if [ -n "$unsafe" ]; then
    fail "$name contains a character unsafe for an nginx header"
  fi

  for source in $sources; do
    case "$source" in
      https://*)
        authority=${source#https://}
        [ -n "$authority" ] || fail "$name contains an empty HTTPS authority"
        case "$authority" in
          */*|*\?*|*\#*|*@*) fail "$name entries must be origins, not URLs with paths or credentials" ;;
        esac
        case "$authority" in
          \[*\]:*)
            host=${authority#\[}
            host=${host%%]*}
            port=${authority##*:}
            ;;
          \[*\])
            host=${authority#\[}
            host=${host%\]}
            port=""
            ;;
          *:*)
            host=${authority%:*}
            port=${authority##*:}
            ;;
          *)
            host=$authority
            port=""
            ;;
        esac
        [ -n "$host" ] || fail "$name contains an empty HTTPS hostname"
        case "$authority" in
          \[*\]) validate_https_ipv6_literal "$name" "$host" ;;
          \[*\]:*) validate_https_ipv6_literal "$name" "$host" ;;
          *) validate_https_hostname "$name" "$host" ;;
        esac
        if [ -n "$port" ]; then
          validate_origin_port "$name" "$port" HTTPS
        fi
        ;;
      http://localhost|http://127.0.0.1|http://\[::1\])
        # Explicit loopback is useful for a local MinIO/LocalStack setup;
        # all non-loopback origins must use HTTPS.
        ;;
      http://localhost:*|http://127.0.0.1:*|http://\[::1\]:*)
        loopback_authority=${source#http://}
        case "$loopback_authority" in
          */*|*\?*|*\#*|*@*) fail "$name entries must be origins, not URLs with paths or credentials" ;;
        esac
        loopback_port=${loopback_authority##*:}
        validate_origin_port "$name" "$loopback_port" HTTP
        ;;
      *) fail "$name contains a non-HTTPS/non-loopback origin: $source" ;;
    esac
  done
}

validate_edge_body_size() {
  size=$1
  # nginx accepts a positive decimal byte count or a single k/m/g suffix.
  # This value is rendered into the template alongside CSP sources, so do not
  # treat a malformed operator value as a harmless nginx startup error.
  case "$size" in
    ""|*[!0-9kKmMgG]*) fail "GOATFARM_EDGE_MAX_BODY_SIZE must be a positive nginx byte size" ;;
  esac
  case "$size" in
    *[kKmMgG]) number=${size%?} ;;
    *) number=$size ;;
  esac
  case "$number" in
    ""|0|*[!0-9]*) fail "GOATFARM_EDGE_MAX_BODY_SIZE must be a positive nginx byte size" ;;
  esac
}

validate_edge_auth_rate() {
  rate=$1
  # limit_req_zone accepts "<positive integer>r/s" or "<positive integer>r/m".
  # The value is spliced into the rendered config, so anything outside that
  # grammar must fail here rather than surface as a loopback nginx boot
  # error that takes the single published listener down.
  case "$rate" in
    ""|*[!0-9r/sm]*) fail "GOATFARM_EDGE_AUTH_RATE must be '<positive integer>r/s' or '<positive integer>r/m' (e.g. 5r/s)" ;;
    *r/s|*r/m) ;;
    *) fail "GOATFARM_EDGE_AUTH_RATE must end in r/s or r/m" ;;
  esac
  number=${rate%r/*}
  case "$number" in
    ""|0|*[!0-9]*) fail "GOATFARM_EDGE_AUTH_RATE must be a positive request rate" ;;
  esac
}

validate_edge_auth_burst() {
  burst=$1
  case "$burst" in
    ""|*[!0-9]*) fail "GOATFARM_EDGE_AUTH_BURST must be a non-negative integer" ;;
  esac
}

environment=${GOATFARM_ENVIRONMENT:?GOATFARM_ENVIRONMENT must be supplied by Compose}
public_scheme=${GOATFARM_EDGE_PUBLIC_SCHEME:-http}
bind_host=${GOATFARM_EDGE_BIND_HOST:-127.0.0.1}
edge_max_body_size=${GOATFARM_EDGE_MAX_BODY_SIZE:-1m}
edge_auth_rate=${GOATFARM_EDGE_AUTH_RATE:-5r/s}
edge_auth_burst=${GOATFARM_EDGE_AUTH_BURST:-20}
connect_sources=${GOATFARM_CSP_CONNECT_ORIGINS:-}
image_sources=${GOATFARM_CSP_IMG_ORIGINS:-}
# Test-only path overrides keep the production defaults immutable while the
# artifact suite exercises the exact sed rendering without a Docker daemon.
template_path=${EDGE_TEMPLATE_PATH:-/edge-proxy.template}
rendered_config_path=${EDGE_RENDERED_CONFIG_PATH:-/etc/nginx/conf.d/default.conf}

case "$environment" in
  development|production) ;;
  *) fail "GOATFARM_ENVIRONMENT must be development or production" ;;
esac

case "$public_scheme" in
  http|https) ;;
  *) fail "GOATFARM_EDGE_PUBLIC_SCHEME must be http or https" ;;
esac
validate_edge_body_size "$edge_max_body_size"
validate_edge_auth_rate "$edge_auth_rate"
validate_edge_auth_burst "$edge_auth_burst"

if [ "$environment" = "production" ] && [ "$public_scheme" != "https" ]; then
  fail "Refusing production edge without an asserted HTTPS terminator (set GOATFARM_EDGE_PUBLIC_SCHEME=https)."
fi

case "$bind_host" in
  127.0.0.1|localhost|::1) ;;
  *)
    if [ "$environment" != "production" ] && [ "${GOATFARM_ALLOW_DEV_PUBLIC_BIND:-false}" != "true" ]; then
      fail "Refusing to bind the edge to $bind_host while GOATFARM_ENVIRONMENT is '$environment': set production behind TLS, or GOATFARM_ALLOW_DEV_PUBLIC_BIND=true for isolated same-machine testing."
    fi
    ;;
esac

validate_csp_sources GOATFARM_CSP_CONNECT_ORIGINS "$connect_sources"
validate_csp_sources GOATFARM_CSP_IMG_ORIGINS "$image_sources"
if screening_enabled && { [ -z "$connect_sources" ] || [ -z "$image_sources" ]; }; then
  fail "screening is enabled but GOATFARM_CSP_CONNECT_ORIGINS and GOATFARM_CSP_IMG_ORIGINS must both name the public S3/MinIO origin"
fi

if [ "${GOATFARM_EDGE_VALIDATE_ONLY:-false}" = "true" ]; then
  exit 0
fi

# Values have been restricted above (no quotes, semicolons, substitutions or
# sed metacharacters), so replacement cannot alter nginx syntax.  The config
# is generated into a tmpfs; the Compose-supplied template stays read-only.
sed \
  -e "s|__GOATFARM_EDGE_PUBLIC_SCHEME__|$public_scheme|g" \
  -e "s|__GOATFARM_EDGE_MAX_BODY_SIZE__|$edge_max_body_size|g" \
  -e "s|__GOATFARM_EDGE_AUTH_RATE__|$edge_auth_rate|g" \
  -e "s|__GOATFARM_EDGE_AUTH_BURST__|$edge_auth_burst|g" \
  -e "s|__GOATFARM_CSP_CONNECT_ORIGINS__|$connect_sources|g" \
  -e "s|__GOATFARM_CSP_IMG_ORIGINS__|$image_sources|g" \
  "$template_path" > "$rendered_config_path"

exec nginx -g 'daemon off;'
