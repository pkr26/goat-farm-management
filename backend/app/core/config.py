"""Application settings (pydantic-settings). All values overridable via env
vars prefixed GOATFARM_, e.g. GOATFARM_DATABASE_URL."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import os
import re
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import dotenv_values
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


def _default_screening_worker_heartbeat_path() -> Path:
    """Container workers use Python's secure temporary directory by default.

    The heartbeat writer itself creates an exclusive 0600 file and atomically
    replaces this target; operators can still mount and configure a different
    private path when their runtime requires it.

    Resolved lazily via ``default_factory``: ``tempfile.gettempdir()`` probes
    by writing a file, so evaluating it at import time crashes one-shot
    least-privilege services (the compose ``config-guard`` runs on a
    read-only root filesystem with no writable temporary directory) before
    they can do any work.
    """
    return Path(tempfile.gettempdir()) / "goatfarm-screening-worker.json"


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
MAX_PREVIOUS_TOTP_ENCRYPTION_KEYS = 3
MIN_IDEMPOTENCY_HMAC_SECRET_LENGTH = 32
TOTP_ENCRYPTION_KEY_BYTES = 32
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
        "GOATFARM_EDGE_MAX_BODY_SIZE",
        "GOATFARM_DOCKER_SUBNET",
        "GOATFARM_DOCKER_DATA_SUBNET",
        "GOATFARM_ALLOW_DEV_PUBLIC_BIND",
        "GOATFARM_CSP_CONNECT_ORIGINS",
        "GOATFARM_CSP_IMG_ORIGINS",
        # docker-compose.production.yml interpolation/secret-mount inputs.
        # The preflight guard validates their spelling before Compose starts;
        # they are deliberately never forwarded into the API/worker process.
        "GOATFARM_BACKEND_IMAGE_REPOSITORY",
        "GOATFARM_BACKEND_IMAGE_DIGEST",
        "GOATFARM_FRONTEND_IMAGE_REPOSITORY",
        "GOATFARM_FRONTEND_IMAGE_DIGEST",
        "GOATFARM_DB_CA_FILE",
        "GOATFARM_JWT_SECRET_DIR",
        "GOATFARM_COMPOSE_ENV_FILE",
    }
)


def _known_goatfarm_env_names(known_fields: frozenset[str] | set[str] | None) -> set[str]:
    if known_fields is None:
        return set()
    return {f"GOATFARM_{name.upper()}" for name in known_fields} | NON_APP_ENV_VARS


def unknown_goatfarm_env_vars(known_fields: frozenset[str] | set[str] | None = None) -> set[str]:
    """Unknown GOATFARM_* names in the process environment (RT-M2-1).

    ``extra="forbid"`` only rejects unknown keys inside ``backend/.env`` and
    direct construction — production injects config through process
    environment variables, where a typo silently reverts the knob to its
    default. This scan closes that gap.
    """
    if known_fields is None:
        return set()
    known = _known_goatfarm_env_names(known_fields)
    return {name for name in os.environ if name.startswith("GOATFARM_")} - known


def unknown_goatfarm_dotenv_vars(
    known_fields: frozenset[str] | set[str] | None = None,
    path: Path | None = None,
) -> set[str]:
    """Unknown GOATFARM_* names in the shared backend dotenv file.

    The migration/worker projections intentionally use ``extra=ignore`` so
    one shared ``backend/.env`` can contain API-only secrets. Pydantic would
    otherwise also ignore a misspelt safety knob there. Scan names separately
    against the union of all recognised Settings fields, using the same
    case-insensitive spelling Pydantic accepts.
    """
    if known_fields is None:
        return set()
    dotenv_path = path or (BACKEND_DIR / ".env")
    if not dotenv_path.is_file():
        return set()
    known = _known_goatfarm_env_names(known_fields)
    values = dotenv_values(dotenv_path)
    return {
        name.upper() for name in values if name is not None and name.upper().startswith("GOATFARM_")
    } - known


PRODUCTION_REFRESH_COOKIE_NAME = "__Host-goatfarm_refresh"

# asyncpg `ssl` connect-arg values (same names as libpq's sslmode).
DbSslMode = Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]
_DB_SSL_MODES = frozenset({"disable", "allow", "prefer", "require", "verify-ca", "verify-full"})
_ASYNCPG_DATABASE_URL_SCHEME = "postgresql+asyncpg"


def _normalize_database_url(value: str, *, sslmode: DbSslMode, setting_name: str) -> str:
    """Validate the asyncpg URL contract and remove a compatible libpq mode.

    SQLAlchemy's asyncpg dialect forwards URL query keys directly to
    ``asyncpg.connect``. ``sslmode`` is a libpq key, not an asyncpg one, so a
    conventional ``...?sslmode=verify-full`` URL otherwise crashes only when
    the pool opens (``TypeError: unexpected keyword argument 'sslmode'``).
    TLS is deliberately configured by the explicit GOATFARM_DB_* settings,
    which let the DB layer construct a private-CA-aware SSLContext. Accept a
    matching legacy URL mode solely for safe migration, then strip it before
    it reaches the driver; reject a conflict or any other URL-level SSL knob
    rather than weakening or bypassing that explicit policy.
    """
    if value != value.strip() or any(character.isspace() for character in value):
        raise ValueError(f"{setting_name} must not contain whitespace")
    try:
        parsed = urlsplit(value)
        # Accessing these properties validates malformed ports and IPv6 hosts.
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{setting_name} must be a valid postgresql+asyncpg URL") from exc
    if parsed.scheme != _ASYNCPG_DATABASE_URL_SCHEME:
        raise ValueError(
            f"{setting_name} must use {_ASYNCPG_DATABASE_URL_SCHEME}://, not {parsed.scheme!r}"
        )
    if host is None or not parsed.netloc:
        raise ValueError(f"{setting_name} must include a database host")
    if port == 0:
        raise ValueError(f"{setting_name} must use a valid database port")
    if parsed.path in {"", "/"}:
        raise ValueError(f"{setting_name} must include a database name")
    if parsed.fragment:
        raise ValueError(f"{setting_name} must not contain a URL fragment")

    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    sslmode_values = [
        query_value for query_key, query_value in query_pairs if query_key.casefold() == "sslmode"
    ]
    if len(sslmode_values) > 1:
        raise ValueError(f"{setting_name} must not declare sslmode more than once")
    if sslmode_values:
        url_sslmode = sslmode_values[0].casefold()
        if url_sslmode not in _DB_SSL_MODES:
            raise ValueError(f"{setting_name} has an unsupported sslmode={sslmode_values[0]!r}")
        if url_sslmode != sslmode:
            raise ValueError(
                f"{setting_name} sslmode={sslmode_values[0]!r} conflicts with "
                f"GOATFARM_DB_SSLMODE={sslmode!r}; configure TLS with GOATFARM_DB_SSLMODE"
            )

    # ``ssl=...`` is an asyncpg option, but it would compete with the
    # explicit SSLContext passed by app.db. The rest are libpq-only variants
    # that asyncpg cannot consume. Reject all URL-level SSL configuration
    # except the matching, normalized compatibility ``sslmode`` above.
    forbidden_tls_keys = sorted(
        {
            query_key
            for query_key, _query_value in query_pairs
            if query_key.casefold() != "sslmode"
            and (query_key.casefold() == "ssl" or query_key.casefold().startswith("ssl"))
        }
    )
    if forbidden_tls_keys:
        raise ValueError(
            f"{setting_name} must not use URL TLS parameter(s) "
            f"{', '.join(forbidden_tls_keys)}; configure GOATFARM_DB_SSLMODE "
            "and GOATFARM_DB_SSLROOTCERT_PATH instead"
        )

    if not sslmode_values:
        return value
    return urlunsplit(
        parsed._replace(
            query=urlencode(
                [
                    (query_key, query_value)
                    for query_key, query_value in query_pairs
                    if query_key.casefold() != "sslmode"
                ],
                doseq=True,
            )
        )
    )


# Hosts exempt from the https:// requirement everywhere: local model
# gateways / S3-compatible stores bound to loopback for development.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _https_or_loopback_url(value: str, *, setting: str) -> str:
    """Reject URLs that are not https:// to a real host (loopback excepted).

    Model credentials and SigV4 secrets ride every one of these URLs, so a
    plaintext URL must fail at boot. A scheme-only string (``https://`` or
    ``https://:443``) has no hostname and can never carry those credentials
    anywhere useful — it previously passed the scheme check and surfaced
    only as recurring per-request provider/S3 errors. Requiring a host
    keeps the failure inside this module's fail-fast contract.
    """
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if not host:
        raise ValueError(f"{setting} {value!r} must include a host, not just a scheme")
    if parsed.scheme != "https" and host not in _LOOPBACK_HOSTS:
        raise ValueError(f"{setting} {value!r} must use https:// (or an explicit loopback host)")
    try:
        # urlsplit defers port parsing to this property; an out-of-range or
        # non-numeric port ("https://host:99999") raises here, not at split.
        # Reading it converts a boot-clean-but-broken URL into a boot failure
        # (2026-09-20 audit P2-12 — sibling validators already read .port).
        _port: int | None = parsed.port
    except ValueError as exc:
        raise ValueError(f"{setting} {value!r} has an invalid port: {exc}") from exc
    del _port  # read for validation only
    return value.rstrip("/")


def _readable_db_root_certificate(value: Path | None) -> Path | None:
    """Validate an optional private-CA bundle before a pool is created.

    asyncpg's string ``verify-full`` mode looks only in a PostgreSQL-home
    location, which does not exist for the non-root container user. The DB
    layer builds a standard-library SSLContext instead; this setting supplies
    an explicitly mounted private CA when the system trust store is not enough.
    """
    if value is None:
        return None
    if not value.is_file():
        raise ValueError("GOATFARM_DB_SSLROOTCERT_PATH must name a readable CA certificate file")
    try:
        with value.open("rb") as certificate:
            certificate.read(1)
    except OSError as exc:
        raise ValueError(
            "GOATFARM_DB_SSLROOTCERT_PATH must name a readable CA certificate file"
        ) from exc
    return value


def _invalid_db_ca_mode(path: Path | None, sslmode: DbSslMode) -> str | None:
    if path is not None and sslmode not in {"verify-ca", "verify-full"}:
        return "GOATFARM_DB_SSLROOTCERT_PATH requires GOATFARM_DB_SSLMODE verify-ca or verify-full"
    return None


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
    db_sslrootcert_path: Path | None = None
    migration_statement_timeout_ms: int = Field(default=0, ge=0)

    @field_validator("db_sslrootcert_path")
    @classmethod
    def _valid_db_root_certificate(cls, value: Path | None) -> Path | None:
        return _readable_db_root_certificate(value)

    @model_validator(mode="after")
    def _production_tls(self) -> MigrationSettings:
        self.database_url = _normalize_database_url(
            self.database_url,
            sslmode=self.db_sslmode,
            setting_name="GOATFARM_DATABASE_URL",
        )
        if self.migration_database_url is not None:
            self.migration_database_url = _normalize_database_url(
                self.migration_database_url,
                sslmode=self.db_sslmode,
                setting_name="GOATFARM_MIGRATION_DATABASE_URL",
            )
        # ``extra=ignore`` is intentional for a shared backend .env: this
        # least-privilege process must not require API cookie/JWT settings.
        # Process-environment typos are different, though — a misspelled
        # GOATFARM_ENVIRONMENT would otherwise silently select development
        # defaults and bypass this privileged job's production TLS gate.
        known_fields = frozenset(type(self).model_fields) | frozenset(Settings.model_fields)
        unknown = unknown_goatfarm_env_vars(known_fields) | unknown_goatfarm_dotenv_vars(
            known_fields
        )
        if unknown:
            raise ValueError(
                "Refusing migration: unknown GOATFARM_* environment variable(s): "
                f"{', '.join(sorted(unknown))}"
            )
        if self.environment == "production" and self.db_sslmode != "verify-full":
            raise ValueError(
                f"Refusing migration: GOATFARM_DB_SSLMODE={self.db_sslmode!r} is unsafe "
                "in production — use 'verify-full'"
            )
        # Alembic is the only component intentionally allowed to receive the
        # DDL-capable database identity.  Falling back to DATABASE_URL here
        # would make a direct production invocation either run migrations as
        # the API role or tempt an operator to grant that long-lived role DDL
        # privileges.  Development retains the ergonomic single-URL fallback.
        if self.environment == "production" and not self.migration_database_url:
            raise ValueError(
                "Refusing migration: GOATFARM_MIGRATION_DATABASE_URL is required in production"
            )
        if problem := _invalid_db_ca_mode(self.db_sslrootcert_path, self.db_sslmode):
            raise ValueError(problem)
        return self


class ScreeningRotationProvider(BaseModel):
    """One entry of GOATFARM_SCREENING_PROVIDER_ROTATION.

    JSON array of these, e.g.
    [{"kind":"anthropic","name":"claude","model":"claude-sonnet-4-5","api_key":"…"},
     {"kind":"openai_compatible","name":"glm","base_url":"https://open.bigmodel.cn/api/paas/v4",
      "model":"glm-4.6v","api_key":"…"}]
    """

    kind: Literal["anthropic", "openai_compatible"]
    name: str = Field(min_length=1, max_length=40)
    base_url: str | None = None
    api_key: SecretStr
    model: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def _slug_name(cls, value: str) -> str:
        """The name lands in screening_runs.provider (bounded text) and in
        worker logs; keep it a stable ASCII slug so reports group correctly."""
        normalized = value.strip().lower()
        if not normalized or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", normalized):
            raise ValueError(f"provider name {value!r} must be a short slug (a-z, 0-9, -, _)")
        return normalized

    @field_validator("base_url")
    @classmethod
    def _https_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _https_or_loopback_url(value, setting="provider base_url")


class ScreeningRuntimeSettings(Protocol):
    """The deliberately small configuration surface used by screening code.

    The API and its worker have different secret requirements.  Keeping the
    common, structural contract here lets the worker run without receiving
    cookie/JWT/idempotency secrets that are irrelevant to image processing.
    """

    environment: Literal["development", "production"]
    database_url: str
    db_sslmode: DbSslMode
    db_sslrootcert_path: Path | None
    db_pool_size: int
    db_max_overflow: int
    db_pool_timeout: int
    db_statement_timeout_ms: int
    screening_enabled: bool
    s3_endpoint_url: str | None
    s3_region: str
    s3_bucket: str | None
    s3_access_key_id: SecretStr | None
    s3_secret_access_key: SecretStr | None
    screening_s3_prefix: str
    screening_poll_interval_seconds: int
    screening_max_images_per_cycle: int
    screening_daily_call_budget_per_farm: int
    screening_image_max_edge_px: int
    screening_crop_detection_enabled: bool
    screening_max_crops_per_image: int
    screening_presign_expiry_seconds: int
    screening_provider: Literal["anthropic", "openai_compatible"]
    screening_anthropic_base_url: str
    screening_anthropic_api_key: SecretStr | None
    screening_anthropic_model: str
    screening_openai_base_url: str
    screening_openai_api_key: SecretStr | None
    screening_openai_model: str
    screening_provider_rotation: list[ScreeningRotationProvider]
    screening_provider_timeout_seconds: int
    # PROCESSING rows whose image row has not been touched for this long are
    # treated as crashed claims and re-claimed (the pipeline touches the row
    # after every completed stage, so this bounds genuine progress, not just
    # process liveness — the heartbeat covers that).
    screening_stale_processing_after_seconds: int


def _has_nonblank_secret(value: SecretStr | None) -> bool:
    """Whether an operator-supplied secret has usable non-whitespace bytes.

    ``SecretStr(\" \")`` is truthy, so checking the wrapper object itself
    accepts a whitespace-only environment value and leaves a worker that can
    never authenticate to S3 or its selected model provider.  Do the check in
    one place for both the API and least-privilege worker projections.
    """
    return value is not None and bool(value.get_secret_value().strip())


def decode_totp_encryption_key(value: SecretStr | str, *, setting_name: str) -> bytes:
    """Decode one canonical, unpadded base64url AES-256 TOTP key.

    The format is intentionally strict so the configured current key and its
    verification-only predecessors have one stable representation. That makes
    duplicate checks meaningful and prevents an operator from believing two
    spellings name different keys during a rotation.
    """
    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
    if not raw or raw != raw.strip():
        raise ValueError(f"{setting_name} must be a non-blank unpadded base64url key")
    try:
        decoded = base64.b64decode(raw + ("=" * (-len(raw) % 4)), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"{setting_name} must be a base64url-encoded key") from exc
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if raw != canonical or len(decoded) != TOTP_ENCRYPTION_KEY_BYTES:
        raise ValueError(
            f"{setting_name} must be exactly {TOTP_ENCRYPTION_KEY_BYTES} bytes encoded as "
            "canonical unpadded base64url"
        )
    return decoded


def _screening_configuration_problems(settings: ScreeningRuntimeSettings) -> list[str]:
    """Return missing screening prerequisites without leaking secret values."""
    if not settings.screening_enabled:
        return []
    missing: list[str] = []
    if not settings.s3_bucket or not settings.s3_bucket.strip():
        missing.append("GOATFARM_S3_BUCKET")
    if not _has_nonblank_secret(settings.s3_access_key_id) or not _has_nonblank_secret(
        settings.s3_secret_access_key
    ):
        missing.append("GOATFARM_S3_ACCESS_KEY_ID / GOATFARM_S3_SECRET_ACCESS_KEY")
    if settings.screening_provider_rotation:
        names = [entry.name for entry in settings.screening_provider_rotation]
        if len(set(names)) != len(names):
            missing.append("GOATFARM_SCREENING_PROVIDER_ROTATION with duplicate provider names")
        blank_keys = [
            entry.name
            for entry in settings.screening_provider_rotation
            if not entry.api_key.get_secret_value().strip()
        ]
        if blank_keys:
            missing.append(
                f"GOATFARM_SCREENING_PROVIDER_ROTATION blank api_key for: {', '.join(blank_keys)}"
            )
    else:
        if settings.screening_provider == "anthropic" and not _has_nonblank_secret(
            settings.screening_anthropic_api_key
        ):
            missing.append("GOATFARM_SCREENING_ANTHROPIC_API_KEY")
        if settings.screening_provider == "openai_compatible" and not _has_nonblank_secret(
            settings.screening_openai_api_key
        ):
            missing.append("GOATFARM_SCREENING_OPENAI_API_KEY")
    # The stale-claim horizon must cover one worst-case silent window:
    # detection chain + one crop's gate chain + specialists + cross-check ≈
    # (2 × providers + 6) provider timeouts.  A horizon shorter than that
    # lets a second worker stale-reclaim a live row mid-cascade — duplicate
    # runs, duplicate findings, double provider billing (2026-09-20 audit
    # P2-10).  Leased row locks narrow the window but only this invariant
    # removes the legal-but-pathological configurations outright.
    provider_count = max(1, len(settings.screening_provider_rotation))
    worst_cascade_seconds = (2 * provider_count + 6) * settings.screening_provider_timeout_seconds
    if settings.screening_stale_processing_after_seconds < worst_cascade_seconds:
        missing.append(
            f"GOATFARM_SCREENING_STALE_PROCESSING_AFTER_SECONDS="
            f"{settings.screening_stale_processing_after_seconds}s is shorter than one "
            f"worst-case cascade ({worst_cascade_seconds}s for {provider_count} "
            f"provider(s) at {settings.screening_provider_timeout_seconds}s timeout) — "
            "raise the horizon or lower the timeout/rotation"
        )
    return missing


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

    # Optional private CA bundle for a managed/self-hosted PostgreSQL endpoint.
    # With no bundle, verify-{ca,full} uses the image's system trust store.
    db_sslrootcert_path: Path | None = None

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
    # TOTP secrets deliberately use an independent, stable AES-256 key rather
    # than JWT signing material. Production requires it; development can omit
    # it temporarily so existing raw v1 ciphertext remains readable while it
    # is migrated. New ciphertext under a configured key is versioned.
    totp_encryption_key: SecretStr | None = None
    # Verification/decryption-only predecessors for a deliberate TOTP-key
    # rotation. New writes always use totp_encryption_key. Keep this bounded
    # so each login does a finite amount of authenticated-decryption work.
    totp_encryption_previous_keys: list[SecretStr] = Field(
        default_factory=list, max_length=MAX_PREVIOUS_TOTP_ENCRYPTION_KEYS
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
    # Inactive memberships still count because they retain a User row and can be
    # reactivated without another capacity check.
    max_team_members_per_farm: int = Field(default=200, ge=1, le=10_000)
    max_roles_per_farm: int = Field(default=50, ge=1, le=1_000)
    max_simulation_scenarios_per_farm: int = Field(default=25, ge=1, le=500)
    max_planner_plans_per_farm: int = Field(default=25, ge=1, le=500)
    # Distinct Idempotency-Key values must not let a compromised task creator
    # grow the actionable queue without bound. Completed/skipped history and
    # authoritative generated workflow duties do not consume this allowance.
    max_pending_manual_tasks_per_farm: int = Field(default=5_000, ge=1, le=100_000)

    # --- Disease screening (daily S3 photo batches → gate model) -----------
    # The whole feature is off until explicitly enabled, so a deployment
    # without an S3 bucket or model credentials boots exactly as before.
    # Images arrive as daily uploads under
    #   <prefix>/<farm_id>/<YYYY-MM-DD>/<filename>
    # and are screened by a single gate model; healthy verdicts stop there
    # (the cascade), flagged images record findings for review.
    screening_enabled: bool = False
    # S3-compatible storage. endpoint_url unset means AWS S3 proper; an
    # explicit endpoint (MinIO, R2, LocalStack) is how dev/test run.
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str | None = None
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    # Only keys under this prefix are ever listed or downloaded.
    screening_s3_prefix: str = Field(default="raw", min_length=1, max_length=100)
    # The poll loop's cadence and per-cycle intake bound. One cycle lists the
    # prefix and screens at most this many new keys; a large backlog drains
    # over successive cycles instead of one unbounded batch.
    screening_poll_interval_seconds: int = Field(default=300, ge=30)
    screening_max_images_per_cycle: int = Field(default=50, ge=1, le=1_000)
    # ITEM 6 (2026-09-21 playbook): per-farm daily provider-call budget. The
    # claim path measures spend as the farm's ScreeningRun rows for the
    # current UTC day (every provider call records exactly one run) and
    # leaves over-budget farms' photos PENDING until the next day — a stuck
    # provider loop or a runaway backlog can never produce an unbounded
    # bill. Counted per call, not per photo: a multi-crop cascade costs its
    # real size.
    screening_daily_call_budget_per_farm: int = Field(default=400, ge=1, le=100_000)
    # --- Notifications (ITEM 4, 2026-09-21 playbook) --------------------------
    # Off by default: without provider credentials the feature stays inert
    # (same fail-closed posture as screening). When enabled in production the
    # validator below refuses to boot without the MSG91 credentials.
    notifications_enabled: bool = False
    notifications_provider: Literal["console", "msg91"] = "console"
    msg91_auth_key: SecretStr | None = None
    msg91_sender_id: str = "HERDLY"
    # Optional DLT template id for the MSG91 flow API; the template-free
    # dlt_manual route is used when unset.
    msg91_template_id: str | None = None
    # Farm-local morning hour (and minute) at which the daily digest fires.
    notifications_digest_hour: int = Field(default=6, ge=0, le=23)
    notifications_digest_minute: int = Field(default=30, ge=0, le=59)
    # Per-farm SMS cap per local day across every alert class.
    notifications_farm_daily_cap: int = Field(default=50, ge=1, le=1000)
    # Quiet hours (farm-local): no sends inside [start, end).
    notifications_quiet_start_hour: int = Field(default=21, ge=0, le=23)
    notifications_quiet_end_hour: int = Field(default=6, ge=0, le=23)
    # Transport-failure retry: attempts per send (1 disables retrying) and
    # the linear backoff step between them.
    notifications_send_retry_attempts: int = Field(default=2, ge=1, le=5)
    notifications_send_retry_backoff_seconds: float = Field(default=2.0, ge=0.0, le=60.0)
    # Bounded-batch ceiling for the farm scans inside the notifications
    # loop (same template as the cleanup loops; 20-farm deployments never
    # notice it, a misconfigured multitenant box cannot loop unbounded).
    notifications_loop_batch_size: int = Field(default=100, ge=1, le=1000)

    # --- Worker tablet PIN login (ITEM 2, 2026-09-21 playbook) ---------------
    # Short numeric PINs are a convenience credential for a shared farm
    # tablet, scoped to one membership — never a replacement for the account
    # password. Production demands the longer floor below.
    worker_pin_min_length: int = Field(default=4, ge=4, le=12)
    worker_pin_rate_limit_max_attempts: int = Field(default=10, ge=1, le=100)
    worker_pin_rate_limit_window_seconds: int = Field(default=300, ge=30, le=3600)
    # VLM cost scales with pixels: normalize every image to this longest-edge
    # before it is ever sent to a provider.
    screening_image_max_edge_px: int = Field(default=1_568, ge=256, le=4_096)
    # Phase 3 multi-goat detection: one detection call per photo, then the
    # cascade runs per detected goat. Zero boxes (or a failed detection
    # call) falls back to screening the whole photo, so nothing is ever
    # left un-screened by a detection miss.
    screening_crop_detection_enabled: bool = True
    # Upper bound on goats screened from a single photo; extra boxes are
    # ignored (a photo claiming 30 goats is a detection hallucination).
    screening_max_crops_per_image: int = Field(default=8, ge=1, le=20)
    # Presigned image URLs handed to the review UI live this long.
    screening_presign_expiry_seconds: int = Field(default=900, ge=60, le=86_400)
    # Phase 1 ships a single gate provider. "anthropic" calls the Messages
    # API; "openai_compatible" covers every OpenAI-shaped endpoint (GLM, GPT,
    # local gateways) and is the seam the Phase 2 round-robin rotates over.
    screening_provider: Literal["anthropic", "openai_compatible"] = "anthropic"
    screening_anthropic_base_url: str = "https://api.anthropic.com"
    screening_anthropic_api_key: SecretStr | None = None
    screening_anthropic_model: str = "claude-sonnet-4-5"
    screening_openai_base_url: str = "https://api.openai.com/v1"
    screening_openai_api_key: SecretStr | None = None
    screening_openai_model: str = "gpt-5"
    # Phase 2 round-robin: ordered provider list as a JSON array. The day's
    # ordinal picks the primary (date.toordinal() % len) — Monday Claude,
    # Tuesday GLM falls out of the list order with zero stored state. The
    # next entry serves the flagged-image cross-check and the primary's
    # failure fallback. An empty list keeps Phase 1's single-provider
    # behavior (built from the screening_provider fields above).
    screening_provider_rotation: list[ScreeningRotationProvider] = Field(
        default_factory=list, max_length=8
    )
    # A gate call that exceeds this is abandoned and retried next cycle.
    screening_provider_timeout_seconds: int = Field(default=120, ge=10, le=600)
    # A PROCESSING claim whose image row has not been touched for this long
    # is treated as crashed and re-claimed. The pipeline refreshes the row
    # after every completed stage (detection, each crop's gate, each finished
    # crop), so the default comfortably exceeds one worst-case cascade while
    # staying far below a permanent zombie claim.
    screening_stale_processing_after_seconds: int = Field(default=1_800, ge=600, le=86_400)
    # Used only by the worker process, but recognized by the API settings so
    # a shared operator .env cannot be rejected as an unknown variable.
    screening_worker_heartbeat_path: Path = Field(
        default_factory=_default_screening_worker_heartbeat_path
    )
    screening_worker_health_max_age_seconds: int = Field(default=900, ge=60, le=86_400)
    # A permanently failing worker must eventually exit so the orchestrator
    # can replace it. Individual image/provider errors are recorded by the
    # pipeline and do not count here; this limit is only for whole-cycle
    # exceptions such as lost database or object-store connectivity.
    screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=1, le=100)

    @field_validator("db_sslrootcert_path")
    @classmethod
    def _valid_db_root_certificate(cls, value: Path | None) -> Path | None:
        return _readable_db_root_certificate(value)

    @field_validator("screening_s3_prefix")
    @classmethod
    def _valid_screening_prefix(cls, value: str) -> str:
        """A prefix with a leading slash or '..' escapes the intended key
        space when concatenated into list/download calls; reject it here
        rather than trusting every call site to sanitize."""
        normalized = value.strip("/")
        if not normalized or ".." in normalized.split("/"):
            raise ValueError(
                "GOATFARM_SCREENING_S3_PREFIX must be a simple path prefix "
                "(no leading slash, no '..' segments)"
            )
        return normalized

    @field_validator("screening_anthropic_base_url", "screening_openai_base_url")
    @classmethod
    def _https_provider_base_url(cls, value: str) -> str:
        """Model credentials ride every one of these requests; an http://
        base URL would broadcast them to the network path. Loopback
        exceptions keep local gateways (ollama, LiteLLM) usable in dev.
        Shared helper with the worker projection — see
        ``_https_or_loopback_url``."""
        return _https_or_loopback_url(value, setting="model provider base URL")

    @field_validator("s3_endpoint_url")
    @classmethod
    def _https_s3_endpoint_url(cls, value: str | None) -> str | None:
        """Every screening S3 request carries the SigV4 signature of
        GOATFARM_S3_SECRET_ACCESS_KEY, and the photo payloads are farm data:
        a plaintext endpoint broadcast one and exposed the other (2026-09-17
        audit L-5). Unset means AWS S3 proper and is unaffected."""
        if value is None or value == "":
            return None
        return _https_or_loopback_url(value, setting="s3_endpoint_url")

    @field_validator("jwt_issuer", "jwt_audience")
    @classmethod
    def _nonblank_token_binding(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("totp_encryption_key", mode="before")
    @classmethod
    def _empty_totp_encryption_key_is_unset(cls, value: object) -> object:
        # Compose's optional interpolation yields an empty string. Treat only
        # that exact value as absent in development so older local stacks keep
        # their legacy reader; whitespace remains an explicit invalid secret.
        return None if value == "" else value

    @field_validator("totp_encryption_key")
    @classmethod
    def _valid_totp_encryption_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            decode_totp_encryption_key(value, setting_name="GOATFARM_TOTP_ENCRYPTION_KEY")
        return value

    @field_validator("totp_encryption_previous_keys", mode="before")
    @classmethod
    def _empty_previous_keys_are_unset(cls, value: object) -> object:
        # Compose's optional interpolation yields an empty string for list
        # settings exactly as it does for the single key; treat only that
        # exact value as an empty list so ``VAR=`` boots instead of dying
        # on a JSON parse error. Whitespace remains invalid JSON.
        return [] if value == "" else value

    @field_validator("totp_encryption_previous_keys")
    @classmethod
    def _valid_previous_totp_encryption_keys(cls, value: list[SecretStr]) -> list[SecretStr]:
        for index, key in enumerate(value):
            decode_totp_encryption_key(
                key,
                setting_name=f"GOATFARM_TOTP_ENCRYPTION_PREVIOUS_KEYS[{index}]",
            )
        return value

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
        self.database_url = _normalize_database_url(
            self.database_url,
            sslmode=self.db_sslmode,
            setting_name="GOATFARM_DATABASE_URL",
        )
        if self.migration_database_url is not None:
            self.migration_database_url = _normalize_database_url(
                self.migration_database_url,
                sslmode=self.db_sslmode,
                setting_name="GOATFARM_MIGRATION_DATABASE_URL",
            )
        # Argon2 requires at least 8 KiB per lane. Validate this relationship
        # before startup primes the dummy hash, yielding an actionable config
        # error rather than a native hashing failure during boot.
        if self.argon2_memory_cost < 8 * self.argon2_parallelism:
            raise ValueError(
                "GOATFARM_ARGON2_MEMORY_COST must be at least 8 * GOATFARM_ARGON2_PARALLELISM"
            )
        if problem := _invalid_db_ca_mode(self.db_sslrootcert_path, self.db_sslmode):
            raise ValueError(problem)
        # Keep local HTTP development ergonomic, but make the production
        # default host-bound. A __Host- cookie cannot carry Domain, must be
        # Secure and must use Path=/; the response helper enforces the latter
        # two attributes. Custom production names must retain that guarantee.
        # Production floor for tablet PINs: the 4-digit development default
        # auto-tightens (same precedent as the refresh-cookie name), while an
        # operator who EXPLICITLY configures a shorter floor gets a refusal —
        # silently overriding a deliberate choice is worse than failing it.
        if (
            self.environment == "production"
            and self.notifications_enabled
            and (self.notifications_provider != "msg91" or self.msg91_auth_key is None)
        ):
            raise ValueError(
                "GOATFARM_NOTIFICATIONS_ENABLED=true in production requires "
                "GOATFARM_NOTIFICATIONS_PROVIDER=msg91 and GOATFARM_MSG91_AUTH_KEY"
            )
        if self.environment == "production" and self.worker_pin_min_length < 6:
            if "worker_pin_min_length" in self.model_fields_set:
                raise ValueError(
                    "GOATFARM_WORKER_PIN_MIN_LENGTH must be at least 6 in production — "
                    "4-digit PINs are only a development convenience"
                )
            self.worker_pin_min_length = 6
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
        # Screening must be fully configured or fully off — in every
        # environment, not just production. Share the check with the worker
        # projection so neither process can accept a credential the other
        # would reject (including whitespace-only secret values).
        if missing := _screening_configuration_problems(self):
            raise ValueError(
                "GOATFARM_SCREENING_ENABLED=true but incomplete screening config: "
                f"{', '.join(missing)} — supply the values or leave screening disabled"
            )
        if self.screening_worker_health_max_age_seconds < self.screening_poll_interval_seconds + 30:
            raise ValueError(
                "GOATFARM_SCREENING_WORKER_HEALTH_MAX_AGE_SECONDS must exceed "
                "GOATFARM_SCREENING_POLL_INTERVAL_SECONDS by at least 30 seconds"
            )
        if self.totp_encryption_key is None and self.totp_encryption_previous_keys:
            raise ValueError(
                "GOATFARM_TOTP_ENCRYPTION_PREVIOUS_KEYS requires a current "
                "GOATFARM_TOTP_ENCRYPTION_KEY"
            )
        if self.environment != "production":
            return self
        problems: list[str] = []
        current_hmac_secret = self.idempotency_request_hmac_secret.get_secret_value()
        previous_hmac_secrets = [
            secret.get_secret_value() for secret in self.idempotency_request_hmac_previous_secrets
        ]
        current_totp_key = self.totp_encryption_key
        previous_totp_keys = [
            secret.get_secret_value() for secret in self.totp_encryption_previous_keys
        ]
        if current_totp_key is None:
            problems.append(
                "GOATFARM_TOTP_ENCRYPTION_KEY is required in production; generate an independent "
                "32-byte base64url key and rekey legacy TOTP ciphertext before JWT cutover"
            )
        elif current_totp_key.get_secret_value() in previous_totp_keys:
            problems.append(
                "GOATFARM_TOTP_ENCRYPTION_KEY must not also appear in "
                "GOATFARM_TOTP_ENCRYPTION_PREVIOUS_KEYS"
            )
        if len(set(previous_totp_keys)) != len(previous_totp_keys):
            problems.append("GOATFARM_TOTP_ENCRYPTION_PREVIOUS_KEYS must not contain duplicates")
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


class ScreeningWorkerSettings(BaseSettings):
    """Least-privilege settings projection for the screening worker.

    The worker needs database, object-storage and provider credentials.  It
    must not require (or receive) browser-cookie, JWT, CORS or idempotency
    secrets simply because it shares an image with the API.  ``extra=ignore``
    intentionally permits an operator's shared backend ``.env`` file; direct
    process-environment typos are still rejected by the validator below.
    """

    model_config = SettingsConfigDict(
        env_prefix="GOATFARM_", env_file=BACKEND_DIR / ".env", extra="ignore"
    )

    environment: Literal["development", "production"] = "development"
    database_url: str = "postgresql+asyncpg://localhost:5432/goatfarm"
    db_sslmode: DbSslMode = "disable"
    db_sslrootcert_path: Path | None = None
    db_pool_size: int = Field(default=2, ge=1)
    db_max_overflow: int = Field(default=2, ge=0)
    db_pool_timeout: int = Field(default=30, ge=1)
    db_statement_timeout_ms: int = Field(default=30_000, ge=1)

    screening_enabled: bool = False
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str | None = None
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    screening_s3_prefix: str = Field(default="raw", min_length=1, max_length=100)
    screening_poll_interval_seconds: int = Field(default=300, ge=30)
    screening_max_images_per_cycle: int = Field(default=50, ge=1, le=1_000)
    # ITEM 6 (2026-09-21 playbook): per-farm daily provider-call budget. The
    # claim path measures spend as the farm's ScreeningRun rows for the
    # current UTC day (every provider call records exactly one run) and
    # leaves over-budget farms' photos PENDING until the next day — a stuck
    # provider loop or a runaway backlog can never produce an unbounded
    # bill. Counted per call, not per photo: a multi-crop cascade costs its
    # real size.
    screening_daily_call_budget_per_farm: int = Field(default=400, ge=1, le=100_000)
    screening_image_max_edge_px: int = Field(default=1_568, ge=256, le=4_096)
    screening_crop_detection_enabled: bool = True
    screening_max_crops_per_image: int = Field(default=8, ge=1, le=20)
    screening_presign_expiry_seconds: int = Field(default=900, ge=60, le=86_400)
    screening_provider: Literal["anthropic", "openai_compatible"] = "anthropic"
    screening_anthropic_base_url: str = "https://api.anthropic.com"
    screening_anthropic_api_key: SecretStr | None = None
    screening_anthropic_model: str = "claude-sonnet-4-5"
    screening_openai_base_url: str = "https://api.openai.com/v1"
    screening_openai_api_key: SecretStr | None = None
    screening_openai_model: str = "gpt-5"
    screening_provider_rotation: list[ScreeningRotationProvider] = Field(
        default_factory=list, max_length=8
    )
    screening_provider_timeout_seconds: int = Field(default=120, ge=10, le=600)
    # A PROCESSING claim whose image row has not been touched for this long
    # is treated as crashed and re-claimed. The pipeline refreshes the row
    # after every completed stage (detection, each crop's gate, each finished
    # crop), so the default comfortably exceeds one worst-case cascade while
    # staying far below a permanent zombie claim.
    screening_stale_processing_after_seconds: int = Field(default=1_800, ge=600, le=86_400)

    # A local, atomic heartbeat powers the container health check. It carries
    # no customer data and stays inside the worker container's writable /tmp.
    screening_worker_heartbeat_path: Path = Field(
        default_factory=_default_screening_worker_heartbeat_path
    )
    screening_worker_health_max_age_seconds: int = Field(default=900, ge=60, le=86_400)
    # After this many unhandled whole-cycle failures, exit nonzero so
    # ``restart: unless-stopped`` replaces a live-but-broken worker. Per-image
    # verdict/provider errors remain durable pipeline results, not crashes.
    screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=1, le=100)

    @field_validator("db_sslrootcert_path")
    @classmethod
    def _valid_db_root_certificate(cls, value: Path | None) -> Path | None:
        return _readable_db_root_certificate(value)

    @field_validator("screening_s3_prefix")
    @classmethod
    def _valid_screening_prefix(cls, value: str) -> str:
        normalized = value.strip("/")
        if not normalized or ".." in normalized.split("/"):
            raise ValueError(
                "GOATFARM_SCREENING_S3_PREFIX must be a simple path prefix "
                "(no leading slash, no '..' segments)"
            )
        return normalized

    @field_validator("screening_anthropic_base_url", "screening_openai_base_url")
    @classmethod
    def _https_provider_base_url(cls, value: str) -> str:
        return _https_or_loopback_url(value, setting="model provider base URL")

    @field_validator("s3_endpoint_url")
    @classmethod
    def _https_s3_endpoint_url(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        return _https_or_loopback_url(value, setting="s3_endpoint_url")

    @model_validator(mode="after")
    def _worker_safety(self) -> ScreeningWorkerSettings:
        self.database_url = _normalize_database_url(
            self.database_url,
            sslmode=self.db_sslmode,
            setting_name="GOATFARM_DATABASE_URL",
        )
        known_fields = frozenset(type(self).model_fields) | frozenset(Settings.model_fields)
        unknown = unknown_goatfarm_env_vars(known_fields) | unknown_goatfarm_dotenv_vars(
            known_fields
        )
        if unknown:
            raise ValueError(
                "Refusing worker boot: unknown GOATFARM_* environment variable(s): "
                f"{', '.join(sorted(unknown))}"
            )
        if self.environment == "production" and self.db_sslmode != "verify-full":
            raise ValueError(
                f"GOATFARM_DB_SSLMODE={self.db_sslmode!r} is unsafe in production — "
                "use 'verify-full'"
            )
        if problem := _invalid_db_ca_mode(self.db_sslrootcert_path, self.db_sslmode):
            raise ValueError(problem)
        if self.screening_worker_health_max_age_seconds < self.screening_poll_interval_seconds + 30:
            raise ValueError(
                "GOATFARM_SCREENING_WORKER_HEALTH_MAX_AGE_SECONDS must exceed "
                "GOATFARM_SCREENING_POLL_INTERVAL_SECONDS by at least 30 seconds"
            )
        if problems := _screening_configuration_problems(self):
            raise ValueError(
                "GOATFARM_SCREENING_ENABLED=true but incomplete screening config: "
                f"{', '.join(problems)} — supply the values or leave screening disabled"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_screening_worker_settings() -> ScreeningWorkerSettings:
    return ScreeningWorkerSettings()


@lru_cache
def get_migration_settings() -> MigrationSettings:
    return MigrationSettings()
