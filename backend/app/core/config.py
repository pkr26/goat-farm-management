"""Application settings (pydantic-settings). All values overridable via env
vars prefixed GOATFARM_, e.g. GOATFARM_DATABASE_URL."""

from __future__ import annotations

import ipaddress
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


def _dev_key_dir() -> Path:
    """Default dev-key directory outside the repository working tree.

    Existing repo-local keys (backend/keys/) keep working when present, so
    current development setups are not invalidated; new generations land in
    the user cache where folder zips and ad-hoc backups cannot reach them.
    """
    repo_keys = BACKEND_DIR / "keys"
    if (repo_keys / "jwt_private.pem").exists():
        return repo_keys
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "goatfarm" / "keys"


MAX_PREVIOUS_JWT_PUBLIC_KEYS = 3
MAX_PREVIOUS_IDEMPOTENCY_HMAC_SECRETS = 3
MIN_IDEMPOTENCY_HMAC_SECRET_LENGTH = 32
DEVELOPMENT_IDEMPOTENCY_HMAC_SECRET = "development-only-idempotency-hmac-secret-change-me"
HOST_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
COOKIE_NAME_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
DEVELOPMENT_REFRESH_COOKIE_NAME = "goatfarm_refresh"

# GOATFARM_* process-env names that are deliberately NOT application
# settings: the backup/restore scripts and the compose edge read them from a
# descriptor-pinned .env snapshot of their own, and the test suite's database
# selector. They may legitimately coexist in the API process environment.
NON_APP_ENV_VARS = frozenset(
    {
        "GOATFARM_TEST_DB",
        # backup.sh / restore.sh / backup_env.sh
        "GOATFARM_BACKUP_KEEP",
        "GOATFARM_BACKUP_GPG_RECIPIENT",
        "GOATFARM_BACKUP_GPG_SIGNER_FINGERPRINT",
        "GOATFARM_BACKUP_S3_URI",
        "GOATFARM_RESTORE_DATABASE_URL",
        "GOATFARM_RESTORE_GPG_SIGNER_FINGERPRINT",
        "GOATFARM_RESTORE_CONFIRM",
        # docker-compose edge service
        "GOATFARM_EDGE_BIND_HOST",
        "GOATFARM_EDGE_PUBLIC_SCHEME",
        "GOATFARM_EDGE_PROXY_IP",
        "GOATFARM_DOCKER_SUBNET",
        "GOATFARM_DOCKER_DATA_SUBNET",
        "GOATFARM_ALLOW_DEV_PUBLIC_BIND",
    }
)


def unknown_goatfarm_env_vars(known_fields: frozenset[str] | set[str] | None = None) -> set[str]:
    """GOATFARM_* process-env names that are neither Settings fields nor
    known script/edge-only variables (RT-M2-1).

    ``extra="forbid"`` only rejects unknown keys inside ``backend/.env`` and
    direct construction — production injects config through process
    environment variables, where a typo silently reverts the knob to its
    default. This scan closes that gap.
    """
    if known_fields is None:
        return set()
    known = {f"GOATFARM_{name.upper()}" for name in known_fields}
    known |= NON_APP_ENV_VARS
    return {name for name in os.environ if name.startswith("GOATFARM_")} - known


PRODUCTION_REFRESH_COOKIE_NAME = "__Host-goatfarm_refresh"

# asyncpg `ssl` connect-arg values (same names as libpq's sslmode).
DbSslMode = Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]


class MigrationSettings(BaseSettings):
    """Minimal settings surface for the privileged Alembic release job.

    A migration must enforce the production database transport invariant, but
    it neither serves cookies/CORS nor signs tokens. Keeping this projection
    separate avoids injecting unrelated API secrets into the one-shot DDL
    container merely to make production validation run.
    """

    model_config = SettingsConfigDict(
        env_prefix="GOATFARM_",
        env_file=BACKEND_DIR / ".env",
        # backend/.env also contains API-only GOATFARM_ fields. They are
        # validated by Settings in the API process and intentionally ignored
        # by this least-privilege projection.
        extra="ignore",
    )

    environment: Literal["development", "production"] = "development"
    database_url: str = "postgresql+asyncpg://localhost:5432/goatfarm"
    migration_database_url: str | None = None
    db_sslmode: DbSslMode = "disable"
    migration_statement_timeout_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _production_tls(self) -> MigrationSettings:
        if self.environment == "production" and self.db_sslmode != "verify-full":
            raise ValueError(
                f"Refusing migration: GOATFARM_DB_SSLMODE={self.db_sslmode!r} is unsafe "
                "in production — use 'verify-full'"
            )
        return self


class Settings(BaseSettings):
    # env_file resolved against backend/ so launching uvicorn/alembic from
    # the repo root (or a Docker WORKDIR) still picks it up.
    # Fail fast on misspelled keys in backend/.env and direct Settings(...)
    # construction. Silently ignoring a security setting is more dangerous
    # than refusing to boot with an actionable validation error.
    model_config = SettingsConfigDict(
        env_prefix="GOATFARM_", env_file=BACKEND_DIR / ".env", extra="forbid"
    )

    # Deployment environment. "production" turns on fail-closed validation
    # (see _production_safety) and disables the interactive docs/OpenAPI UI.
    environment: Literal["development", "production"] = "development"

    # Database (asyncpg driver). `goatfarm_test` is used by the test suite.
    database_url: str = "postgresql+asyncpg://localhost:5432/goatfarm"

    # Optional separately privileged URL used only by Alembic. Production
    # deployments should keep DDL rights away from the long-running API
    # credential and run migrations as an explicit release job.
    migration_database_url: str | None = None

    # TLS for the database wire, mapped to asyncpg's `ssl` connect arg.
    # Production requires "verify-full": encryption without certificate and
    # hostname verification does not authenticate the database server.
    db_sslmode: DbSslMode = "disable"

    # Connection pool + per-statement guardrails. The statement timeout is a
    # backstop against runaway queries (a full-table scan must not hold a
    # connection forever); 30 s is far above anything the suite or a small
    # SaaS workload legitimately runs.
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_pool_timeout: int = Field(default=30, ge=1)  # seconds to wait for a free connection
    db_statement_timeout_ms: int = Field(default=30_000, ge=1)  # asyncpg server_settings

    # Migrations get their own budget: a table rewrite or a CREATE INDEX
    # CONCURRENTLY (which additionally waits for every concurrent transaction
    # to drain) legitimately runs far longer than any request ever may, so the
    # OLTP backstop above would cancel it and abort the release. 0 disables the
    # per-statement cap for the Alembic connection; lock_timeout still bounds
    # how long DDL may queue behind live traffic.
    migration_statement_timeout_ms: int = Field(default=0, ge=0)

    # Successful Idempotency-Key results are replayable for this window.
    # Expired records are removed by a bounded background cleanup job.
    idempotency_retention_hours: int = Field(default=24 * 7, ge=1, le=24 * 90)
    idempotency_cleanup_interval_seconds: int = Field(default=3600, ge=60)
    idempotency_cleanup_batch_size: int = Field(default=500, ge=1, le=10_000)
    idempotency_cleanup_max_batches: int = Field(default=10, ge=1, le=100)
    # Per-actor ceiling on live (unexpired) idempotency records. The purge
    # loop is throughput-capped, so without this a client minting a fresh key
    # per request could grow the shared table faster than cleanup drains it.
    idempotency_max_open_records_per_actor: int = Field(default=1_000, ge=1, le=100_000)
    # Sensitive request fingerprints must not be usable as an offline password
    # verifier by a database/backup reader. This key is deliberately distinct
    # from JWT signing keys and must remain stable across replicas/restarts.
    idempotency_request_hmac_secret: SecretStr = SecretStr(DEVELOPMENT_IDEMPOTENCY_HMAC_SECRET)
    # Verification-only keys support a bounded, rolling rotation. New records
    # are always signed with the current key.
    idempotency_request_hmac_previous_secrets: list[SecretStr] = Field(
        default_factory=list,
        max_length=MAX_PREVIOUS_IDEMPOTENCY_HMAC_SECRETS,
    )

    # Legacy tenant repairs run only after readiness and claim finite batches
    # with SKIP LOCKED, so rolling deploys never lock every farm at startup.
    legacy_repair_interval_seconds: int = Field(default=3600, ge=60)
    legacy_repair_farm_batch_size: int = Field(default=25, ge=1, le=500)
    legacy_repair_task_batch_size: int = Field(default=500, ge=1, le=10_000)
    legacy_repair_max_batches: int = Field(default=10, ge=1, le=100)

    # Animal lifecycle exits hide linked pending work immediately. Cleanup is
    # then converged in finite lock-skipping batches outside the status request
    # so one goat with an extreme task history cannot make retirement time out.
    inactive_animal_task_cleanup_interval_seconds: int = Field(default=60, ge=10)
    inactive_animal_task_cleanup_batch_size: int = Field(default=500, ge=1, le=10_000)
    inactive_animal_task_cleanup_max_batches: int = Field(default=10, ge=1, le=100)

    # User tombstones revoke access synchronously; their retained membership
    # audit anchors are deactivated afterward in finite lock-skipping batches.
    deleted_membership_cleanup_interval_seconds: int = Field(default=60, ge=10)
    deleted_membership_cleanup_batch_size: int = Field(default=500, ge=1, le=10_000)
    deleted_membership_cleanup_max_batches: int = Field(default=10, ge=1, le=100)

    # Recurring husbandry duties are materialized by a short-interval
    # background sweep (GET /api/tasks is read-only). The sweep pages the
    # tenant list in finite keyset batches; the per-farm advisory lock inside
    # the cadence service serializes any overlap with a concurrent sweep.
    cadence_materialization_interval_seconds: int = Field(default=300, ge=10)
    cadence_materialization_farm_batch_size: int = Field(default=100, ge=1, le=1000)
    cadence_materialization_max_batches: int = Field(default=10, ge=1, le=100)

    # Reject oversized JSON/form bodies before Starlette buffers/parses them.
    # This is an application backstop; the edge proxy should enforce the same
    # or a smaller limit before traffic reaches uvicorn.
    max_request_body_bytes: int = Field(default=1_048_576, ge=1_024, le=20_971_520)
    max_request_target_bytes: int = Field(default=8_192, ge=256, le=65_536)

    # JWT: RS256 keypair. Development generates its pair under the user's
    # cache directory (OUTSIDE the repository working tree — zips, cloud-sync
    # folders and `cp -r` backups of the repo must never pick up the live
    # signing key); production supplies real keys via explicitly configured
    # paths. Access token travels in the Authorization header; the refresh
    # token in an httpOnly SameSite=Lax cookie.
    jwt_private_key_path: Path = _dev_key_dir() / "jwt_private.pem"
    jwt_public_key_path: Path = _dev_key_dir() / "jwt_public.pem"
    # Verification-only public keys retained from an old pair or pre-staged
    # for the next pair. Keeping this list small bounds legacy-token
    # verification work; the environment value is a JSON array of paths.
    jwt_previous_public_key_paths: list[Path] = Field(
        default_factory=list, max_length=MAX_PREVIOUS_JWT_PUBLIC_KEYS
    )
    jwt_algorithm: Literal["RS256"] = "RS256"
    # Bind signed tokens to this service/client pair. Signature validity alone
    # is not enough when the same key might ever be used by another service.
    jwt_issuer: str = Field(default="goatfarm-api", min_length=1, max_length=200)
    jwt_audience: str = Field(default="goatfarm-web", min_length=1, max_length=200)
    access_token_ttl_seconds: int = Field(default=30 * 60, ge=1, le=60 * 60 * 24)
    refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=1, le=60 * 60 * 24 * 365)
    # Long-running processes periodically remove expired server-side refresh
    # sessions; relying on restarts alone lets the table grow without bound.
    refresh_session_cleanup_interval_seconds: int = Field(default=3600, ge=60)
    refresh_session_cleanup_batch_size: int = Field(default=500, ge=1, le=10_000)
    refresh_session_cleanup_max_batches: int = Field(default=10, ge=1, le=100)
    # Bound both successful-login family creation and refresh rotation. These
    # limits make password revocation/account deletion finite even under a
    # valid-credential attacker; signed family claims retain replay detection
    # after old consumed-token rows are compacted.
    refresh_max_families_per_user: int = Field(default=10, ge=1, le=50)
    refresh_max_sessions_per_family: int = Field(default=1024, ge=2, le=4096)
    # Only simultaneous browser-tab replays get an idempotent successor.
    # Anything later remains a refresh-token theft signal.
    refresh_reuse_grace_seconds: int = Field(default=3, ge=0, le=30)
    refresh_cookie_name: str = DEVELOPMENT_REFRESH_COOKIE_NAME

    # Argon2id parameters (protected: only changeable via env, never at runtime).
    argon2_time_cost: int = Field(default=3, ge=1, le=6)
    argon2_memory_cost: int = Field(default=65536, ge=8, le=131_072)  # KiB (64 MiB)
    argon2_parallelism: int = Field(default=4, ge=1, le=8)
    argon2_hash_len: int = Field(default=32, ge=16, le=64)
    # Password work is CPU/memory hard and must never execute on the asyncio
    # event loop.  A small dedicated pool bounds both memory (workers ×
    # memory_cost) and queued work; excess requests fail fast with HTTP 429.
    argon2_worker_threads: int = Field(default=2, ge=1, le=4)
    # PBKDF2 padding spent on every rejected login so aggregate work cannot
    # reveal which kind of stored hash was looked up. Must cover the highest
    # PBKDF2 iteration count actually present among imported legacy hashes
    # (this repository's own history tops out at 50,000); deployments that
    # import costlier hashes must raise it together with their import audit.
    rejected_login_pbkdf2_work_budget: int = Field(default=50_000, ge=0, le=10_000_000)

    # Frontend dev server origins allowed to call the API with credentials.
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # HTTP Host header allowlist. This is independent from CORS: CORS limits
    # which browsers may read credentialed responses, while Host validation
    # rejects requests addressed to an unexpected virtual host before routing.
    # Production must replace these local/test defaults with its API hosts.
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "test", "testserver"]

    # Refresh-token cookie flags (httpOnly + SameSite=Lax always on).
    cookie_secure: bool = False  # set True behind HTTPS in production

    min_password_length: int = Field(default=8, ge=1, le=128)

    # Password/auth rate limiting: in-memory sliding window, per process.
    # Login counts FAILED attempts only (a success resets the count) and is
    # keyed per (client IP, email); register counts every attempt per IP.
    # Authenticated worker-create/reset Argon work shares a per-owner budget so
    # switching endpoints or farms cannot monopolize the global password pool.
    auth_rate_limit_enabled: bool = True
    auth_rate_limit_max_attempts: int = Field(default=10, ge=1)
    auth_rate_limit_window_seconds: int = Field(default=300, ge=1)

    # Storage seam behind the sliding-window limiter (app/ratelimit.py). Only
    # the process-local "memory" backend exists today; the knob exists so a
    # multi-replica deployment fails startup loudly instead of silently
    # multiplying every limit per process (see README's single-worker section).
    rate_limit_backend: str = "memory"

    # Prometheus /metrics endpoint and metric collection. The endpoint is
    # unauthenticated by design: the compose edge routes only /api/ to the
    # backend, so /metrics stays on the internal network (see README's
    # observability section before exposing it anywhere else).
    metrics_enabled: bool = True

    # Comma-separated IPs/CIDRs of trusted reverse proxies (e.g.
    # "127.0.0.1,10.0.0.0/8"). When non-empty, X-Forwarded-For from those
    # hosts restores the real client IP (rate limiting, logs). Default ""
    # trusts nothing: a spoofable header must never steer the limiter.
    # Validated by _validated_trusted_proxy_hosts: uvicorn silently treats "*"
    # as always-trust and silently ignores anything that is not an IP/CIDR.
    trusted_proxy_hosts: str = ""

    # A single account may own at most this many farms.
    max_farms_per_user: int = Field(default=10, ge=1)
    # Legacy/imported identities may predate today's one-farm provisioning
    # consent guard. Bound list/export hydration instead of trusting that
    # historical affiliation cardinality is small.
    max_account_affiliations_per_response: int = Field(default=500, ge=1, le=10_000)

    # Tenant resource ceilings prevent a compromised team manager from
    # provisioning an unbounded number of global accounts or custom roles.
    # Inactive memberships still count because they retain a User row and can
    # be reactivated without another capacity check.
    max_team_members_per_farm: int = Field(default=200, ge=1, le=10_000)
    max_roles_per_farm: int = Field(default=50, ge=1, le=1_000)
    max_simulation_scenarios_per_farm: int = Field(default=25, ge=1, le=500)
    max_planner_plans_per_farm: int = Field(default=25, ge=1, le=500)
    # Distinct Idempotency-Key values must not let a compromised task creator
    # grow the actionable queue without bound. Completed/skipped history and
    # authoritative generated workflow duties do not consume this allowance.
    max_pending_manual_tasks_per_farm: int = Field(default=5_000, ge=1, le=100_000)

    @field_validator("jwt_issuer", "jwt_audience")
    @classmethod
    def _nonblank_token_binding(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("allowed_hosts")
    @classmethod
    def _canonical_allowed_hosts(cls, value: list[str]) -> list[str]:
        """Validate and store the form TrustedHostMiddleware compares against.

        Starlette matches the Host header byte-exactly, while browsers and
        proxies always send a lower-cased, dot-free authority. Validating a
        normalized copy and then installing the raw string lets "API.Example.com"
        pass the fail-closed production gate and then reject 100% of traffic —
        including through a container probe that sends the same raw value and
        therefore keeps reporting healthy. Normalize once, here.
        Starlette also asserts at middleware construction when a wildcard is
        not exactly ``*.domain``. Mirror that grammar here and validate the
        underlying DNS/IP name so a configuration that passes Settings cannot
        later crash application construction or reject every normal Host.
        """
        canonical: list[str] = []
        invalid: list[str] = []
        for raw_host in value:
            host = raw_host.strip().lower()
            # A single final dot is the DNS absolute-name spelling. Browsers
            # omit it, so store the byte form Starlette will receive. More than
            # one final dot remains invalid instead of being normalized away.
            if host.endswith("."):
                host = host[:-1]

            if host == "*":
                canonical.append(host)
                continue

            wildcard = host.startswith("*.")
            if "*" in host and (not wildcard or "*" in host[2:]):
                invalid.append(raw_host)
                continue
            candidate = host[2:] if wildcard else host

            # TrustedHostMiddleware splits the authority on ':', so IPv6 and
            # port-bearing patterns cannot be represented safely in its
            # allowlist. Exact IPv4 addresses and ordinary DNS names are fine.
            valid = bool(candidate) and ":" not in candidate and len(candidate) <= 253
            is_ip = False
            if valid:
                try:
                    address = ipaddress.ip_address(candidate)
                except ValueError:
                    labels = candidate.split(".")
                    valid = all(HOST_LABEL_PATTERN.fullmatch(label) for label in labels)
                    # A dotted all-numeric value that is not a canonical IPv4
                    # address is ambiguous across clients/proxies; reject it.
                    if valid and len(labels) > 1 and all(label.isdigit() for label in labels):
                        valid = False
                else:
                    is_ip = True
                    valid = address.version == 4
            if wildcard and is_ip:
                valid = False
            if not valid:
                invalid.append(raw_host)
                continue
            canonical.append(host)

        if invalid:
            raise ValueError(
                "GOATFARM_ALLOWED_HOSTS entries must be IPv4 addresses or valid DNS names "
                "with an optional leading '*.' wildcard (never a URL, port, or partial "
                f"wildcard): {invalid}"
            )
        if len(set(canonical)) != len(canonical):
            raise ValueError("GOATFARM_ALLOWED_HOSTS must not contain duplicate host patterns")
        return canonical

    @field_validator("rate_limit_backend")
    @classmethod
    def _known_rate_limit_backend(cls, value: str) -> str:
        """Only the process-local backend is implemented; anything else must
        fail startup with the single-replica constraint spelled out, not boot
        into silently multiplied limits."""
        if value != "memory":
            raise ValueError(
                f"GOATFARM_RATE_LIMIT_BACKEND={value!r} is not implemented; only 'memory' "
                "exists. The memory backend is per-process — running more than one "
                "backend replica multiplies every auth limit per process. See the "
                "single-worker / single-replica section of README.md before scaling."
            )
        return value

    @field_validator("refresh_cookie_name")
    @classmethod
    def _valid_refresh_cookie_name(cls, value: str) -> str:
        """Reject names that a browser or Cookie header parser can reinterpret."""
        if not COOKIE_NAME_PATTERN.fullmatch(value):
            raise ValueError("GOATFARM_REFRESH_COOKIE_NAME must be a valid cookie token")
        return value

    @field_validator("cors_origins")
    @classmethod
    def _canonical_cors_origins(cls, value: list[str]) -> list[str]:
        """Store the exact origin bytes a browser sends.

        CORSMiddleware matches with ``origin in allow_origins``, but the
        production check below validates urlsplit components, which are already
        lower-cased and drop the default port. Canonicalize so a value that
        passes validation is a value the middleware can match. Entries that do
        not parse as a plain origin are returned untouched so the production
        gate still reports them verbatim.
        """
        canonical: list[str] = []
        unicode_hosts: list[str] = []
        for origin in value:
            candidate = origin.strip()
            try:
                parsed = urlsplit(candidate)
                port = parsed.port
            except ValueError:
                canonical.append(origin)
                continue
            host = parsed.hostname
            if host is not None and not host.isascii():
                # WHATWG URL serialization sends an internationalized hostname
                # in ASCII/Punycode in the browser's Origin header. Keeping the
                # Unicode spelling here therefore passes production validation
                # but can never match CORSMiddleware or the refresh-cookie
                # origin guard. Python's built-in ``idna`` codec implements the
                # older IDNA-2003 mapping (not browser UTS-46), so fail with an
                # actionable instruction instead of canonicalizing differently
                # from the client for names such as ``faß.de``.
                unicode_hosts.append(origin)
                continue
            if not parsed.scheme or not host or parsed.path or parsed.query or parsed.fragment:
                canonical.append(origin)
                continue
            if parsed.username is not None or parsed.password is not None:
                canonical.append(origin)
                continue
            authority = f"[{host}]" if ":" in host else host
            default_port = {"http": 80, "https": 443}.get(parsed.scheme)
            if port is not None and port != default_port:
                authority = f"{authority}:{port}"
            canonical.append(f"{parsed.scheme}://{authority}")
        if unicode_hosts:
            raise ValueError(
                "GOATFARM_CORS_ORIGINS hostnames must use their browser-serialized "
                f"ASCII/Punycode spelling, not Unicode: {unicode_hosts}"
            )
        return canonical

    @field_validator("trusted_proxy_hosts")
    @classmethod
    def _validated_trusted_proxy_hosts(cls, value: str) -> str:
        """Refuse anything uvicorn would turn into always-trust or a no-op.

        ``_TrustedHosts`` treats "*" (and any prefix-length-0 network) as
        "every peer is a proxy", which makes X-Forwarded-For — and therefore
        every per-IP auth ceiling and every throttling log line — attacker
        controlled. Conversely it drops entries that are neither an IP nor a
        CIDR into a literal set that can never match a peer address, so a
        hostname silently trusts nothing. Both failures are silent at runtime;
        fail closed at boot instead.
        """
        entries = [entry.strip() for entry in value.split(",") if entry.strip()]
        invalid: list[str] = []
        wildcards: list[str] = []
        for entry in entries:
            try:
                # uvicorn parses a "/"-bearing entry with ip_network (strict)
                # and everything else with ip_address; mirror that exactly.
                network = (
                    ipaddress.ip_network(entry)
                    if "/" in entry
                    else ipaddress.ip_network(ipaddress.ip_address(entry))
                )
            except ValueError:
                invalid.append(entry)
                continue
            if network.prefixlen == 0:
                wildcards.append(entry)
        if invalid or wildcards:
            raise ValueError(
                "must be a comma-separated list of proxy IPs or CIDRs "
                f"(never a hostname or a wildcard): {invalid + wildcards}"
            )
        return ",".join(entries)

    @model_validator(mode="after")
    def _production_safety(self) -> Settings:
        """Fail-closed: refuse to boot a production deployment that is
        trivially insecure — an HTTP refresh-cookie, localhost CORS origins,
        or a plaintext database connection are always operator mistakes,
        never valid production config."""
        # Argon2 requires at least 8 KiB per lane. Validate this relationship
        # before startup primes the dummy hash, yielding an actionable config
        # error rather than a native hashing failure during boot.
        if self.argon2_memory_cost < 8 * self.argon2_parallelism:
            raise ValueError(
                "GOATFARM_ARGON2_MEMORY_COST must be at least 8 * GOATFARM_ARGON2_PARALLELISM"
            )
        # Keep local HTTP development ergonomic, but make the production
        # default host-bound. A __Host- cookie cannot carry Domain, must be
        # Secure and must use Path=/; the response helper enforces the latter
        # two attributes. Custom production names must retain that guarantee.
        if (
            self.environment == "production"
            and self.refresh_cookie_name == DEVELOPMENT_REFRESH_COOKIE_NAME
        ):
            self.refresh_cookie_name = PRODUCTION_REFRESH_COOKIE_NAME
        if self.refresh_cookie_name.startswith("__Host-") and not self.cookie_secure:
            raise ValueError(
                "GOATFARM_COOKIE_SECURE must be true when GOATFARM_REFRESH_COOKIE_NAME "
                "uses the '__Host-' prefix"
            )
        # RT-M2-1: refuse unknown GOATFARM_* process-env variables in every
        # environment. A misspelled knob here silently reverts to its default
        # — the exact failure extra=forbid exists to prevent for .env keys.
        unknown_env = unknown_goatfarm_env_vars(frozenset(type(self).model_fields))
        if unknown_env:
            raise ValueError(
                "Refusing to boot: unknown GOATFARM_* environment variable(s): "
                f"{', '.join(sorted(unknown_env))} — check the spelling against "
                "backend/.env.example"
            )
        if self.environment != "production":
            return self
        problems: list[str] = []
        current_hmac_secret = self.idempotency_request_hmac_secret.get_secret_value()
        previous_hmac_secrets = [
            secret.get_secret_value() for secret in self.idempotency_request_hmac_previous_secrets
        ]
        if (
            len(current_hmac_secret) < MIN_IDEMPOTENCY_HMAC_SECRET_LENGTH
            or current_hmac_secret == DEVELOPMENT_IDEMPOTENCY_HMAC_SECRET
        ):
            problems.append(
                "GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET must be an externally supplied "
                f"secret of at least {MIN_IDEMPOTENCY_HMAC_SECRET_LENGTH} characters in production"
            )
        if any(
            len(secret) < MIN_IDEMPOTENCY_HMAC_SECRET_LENGTH for secret in previous_hmac_secrets
        ):
            problems.append(
                "every GOATFARM_IDEMPOTENCY_REQUEST_HMAC_PREVIOUS_SECRETS entry must be "
                f"at least {MIN_IDEMPOTENCY_HMAC_SECRET_LENGTH} characters"
            )
        if DEVELOPMENT_IDEMPOTENCY_HMAC_SECRET in previous_hmac_secrets:
            problems.append(
                "GOATFARM_IDEMPOTENCY_REQUEST_HMAC_PREVIOUS_SECRETS must not contain "
                "the known development fallback in production"
            )
        if len(set(previous_hmac_secrets)) != len(previous_hmac_secrets):
            problems.append(
                "GOATFARM_IDEMPOTENCY_REQUEST_HMAC_PREVIOUS_SECRETS must not contain duplicates"
            )
        if current_hmac_secret in previous_hmac_secrets:
            problems.append(
                "GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET must not also appear in "
                "GOATFARM_IDEMPOTENCY_REQUEST_HMAC_PREVIOUS_SECRETS"
            )
        if not self.cookie_secure:
            problems.append(
                "GOATFARM_COOKIE_SECURE must be true in production "
                "(the refresh JWT travels in a cookie; over plain HTTP it leaks)"
            )
        if not self.refresh_cookie_name.startswith("__Host-"):
            problems.append("GOATFARM_REFRESH_COOKIE_NAME must start with '__Host-' in production")
        if not self.cors_origins:
            problems.append(
                "GOATFARM_CORS_ORIGINS must not be empty in production "
                "(the credentialed SPA needs at least one HTTPS origin)"
            )
        invalid_origins: list[str] = []
        for origin in self.cors_origins:
            try:
                parsed = urlsplit(origin)
                host = parsed.hostname
                # Reading .port validates malformed/out-of-range ports.
                _port = parsed.port
                is_loopback = False
                if host:
                    try:
                        is_loopback = ipaddress.ip_address(host).is_loopback
                    except ValueError:
                        is_loopback = host.lower() == "localhost"
                valid = (
                    origin != "*"
                    and parsed.scheme == "https"
                    and host is not None
                    and parsed.username is None
                    and parsed.password is None
                    and parsed.path == ""
                    and parsed.query == ""
                    and parsed.fragment == ""
                    and not is_loopback
                )
            except ValueError:
                valid = False
            if not valid:
                invalid_origins.append(origin)
        if invalid_origins:
            problems.append(
                "GOATFARM_CORS_ORIGINS must contain exact non-loopback HTTPS origins "
                f"(no wildcard, credentials, path, query or fragment): {invalid_origins}"
            )
        invalid_hosts: list[str] = []
        for host in self.allowed_hosts:  # already canonicalized by the field validator
            candidate = host[2:] if host.startswith("*.") else host
            is_loopback = False
            try:
                is_loopback = ipaddress.ip_address(candidate).is_loopback
            except ValueError:
                is_loopback = candidate == "localhost"
            if (
                not host
                or host == "*"
                or not candidate
                or any(character in host for character in "/:@?#")
                or any(character.isspace() for character in host)
                or is_loopback
            ):
                invalid_hosts.append(host)
        if not self.allowed_hosts or invalid_hosts:
            problems.append(
                "GOATFARM_ALLOWED_HOSTS must contain non-loopback hostnames "
                f"(exact names or '*.example.com', never '*'): {invalid_hosts}"
            )
        if self.min_password_length < 12:
            problems.append("GOATFARM_MIN_PASSWORD_LENGTH must be at least 12 in production")
        if self.argon2_time_cost < 2:
            problems.append("GOATFARM_ARGON2_TIME_COST must be at least 2 in production")
        if self.argon2_memory_cost < 19 * 1024:
            problems.append("GOATFARM_ARGON2_MEMORY_COST must be at least 19456 KiB in production")
        if self.argon2_hash_len < 32:
            problems.append("GOATFARM_ARGON2_HASH_LEN must be at least 32 in production")
        if self.db_sslmode != "verify-full":
            problems.append(
                f"GOATFARM_DB_SSLMODE={self.db_sslmode!r} is unsafe in production — "
                "use 'verify-full' so both the certificate chain and database hostname are verified"
            )
        # RT-M-6: every in-memory control (auth rate limiter, simulation CPU
        # budget, worker-idempotency gates) is per-process. A multi-worker
        # production launch silently multiplies every one of those budgets —
        # refuse instead of warning (the compose image pins --workers 1).
        for worker_env in ("UVICORN_WORKERS", "WEB_CONCURRENCY"):
            raw_value = os.environ.get(worker_env)
            if raw_value is None:
                continue
            try:
                workers = int(raw_value)
            except ValueError:
                continue
            if workers > 1:
                problems.append(
                    f"{worker_env}={workers} is unsupported: the in-memory auth rate "
                    "limiter and CPU budgets are per-process — run exactly one "
                    "uvicorn worker per deployment"
                )
        if problems:
            raise ValueError("Refusing to boot: " + "; ".join(problems))
        # RT-M2-2: the unauthenticated /metrics endpoint is force-disabled in
        # production, mirroring /docs and /openapi.json. The compose topology
        # keeps it unreachable publicly, but any port/edge misdeployment the
        # validator family exists to catch would otherwise expose route and
        # throttle telemetry. (Set metrics_enabled=false in every production
        # deployment; there is no opt-in until metrics gains authentication.)
        if self.metrics_enabled:
            self.metrics_enabled = False
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_migration_settings() -> MigrationSettings:
    return MigrationSettings()
