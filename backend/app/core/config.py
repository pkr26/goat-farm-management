"""Application settings (pydantic-settings). All values overridable via env
vars prefixed GOATFARM_, e.g. GOATFARM_DATABASE_URL."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

# asyncpg `ssl` connect-arg values (same names as libpq's sslmode).
DbSslMode = Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]


class Settings(BaseSettings):
    # env_file resolved against backend/ so launching uvicorn/alembic from
    # the repo root (or a Docker WORKDIR) still picks it up.
    model_config = SettingsConfigDict(
        env_prefix="GOATFARM_", env_file=BACKEND_DIR / ".env", extra="ignore"
    )

    # Deployment environment. "production" turns on fail-closed validation
    # (see _production_safety) and disables the interactive docs/OpenAPI UI.
    environment: Literal["development", "production"] = "development"

    # Database (asyncpg driver). `goatfarm_test` is used by the test suite.
    database_url: str = "postgresql+asyncpg://localhost:5432/goatfarm"

    # TLS for the database wire, mapped to asyncpg's `ssl` connect arg.
    # "require" (or stricter) for any remote/production database.
    db_sslmode: DbSslMode = "disable"

    # Connection pool + per-statement guardrails. The statement timeout is a
    # backstop against runaway queries (a full-table scan must not hold a
    # connection forever); 30 s is far above anything the suite or a small
    # SaaS workload legitimately runs.
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_pool_timeout: int = Field(default=30, ge=1)  # seconds to wait for a free connection
    db_statement_timeout_ms: int = Field(default=30_000, ge=1)  # asyncpg server_settings

    # JWT: RS256 keypair lives in backend/keys/ (generated on first run,
    # gitignored). Access token travels in the Authorization header; the
    # refresh token in an httpOnly SameSite=Lax cookie.
    jwt_private_key_path: Path = BACKEND_DIR / "keys" / "jwt_private.pem"
    jwt_public_key_path: Path = BACKEND_DIR / "keys" / "jwt_public.pem"
    jwt_algorithm: Literal["RS256"] = "RS256"
    access_token_ttl_seconds: int = Field(default=30 * 60, ge=1)
    refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=1)
    refresh_cookie_name: str = "goatfarm_refresh"

    # Argon2id parameters (protected: only changeable via env, never at runtime).
    argon2_time_cost: int = Field(default=3, ge=1)
    argon2_memory_cost: int = Field(default=65536, ge=1)  # KiB (64 MiB)
    argon2_parallelism: int = Field(default=4, ge=1)
    argon2_hash_len: int = Field(default=32, ge=1)

    # Frontend dev server origins allowed to call the API with credentials.
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # Refresh-token cookie flags (httpOnly + SameSite=Lax always on).
    cookie_secure: bool = False  # set True behind HTTPS in production

    min_password_length: int = Field(default=8, ge=1)

    # Auth endpoints rate limiting: in-memory sliding window, per process.
    # Login counts FAILED attempts only (a success resets the count) and is
    # keyed per (client IP, email); register counts every attempt per IP.
    auth_rate_limit_enabled: bool = True
    auth_rate_limit_max_attempts: int = Field(default=10, ge=1)
    auth_rate_limit_window_seconds: int = Field(default=300, ge=1)

    # Comma-separated IPs/CIDRs of trusted reverse proxies (e.g.
    # "127.0.0.1,10.0.0.0/8"). When non-empty, X-Forwarded-For from those
    # hosts restores the real client IP (rate limiting, logs). Default ""
    # trusts nothing: a spoofable header must never steer the limiter.
    trusted_proxy_hosts: str = ""

    # A single account may own at most this many farms.
    max_farms_per_user: int = Field(default=10, ge=1)

    @model_validator(mode="after")
    def _production_safety(self) -> Settings:
        """Fail-closed: refuse to boot a production deployment that is
        trivially insecure — an HTTP refresh-cookie, localhost CORS origins,
        or a plaintext database connection are always operator mistakes,
        never valid production config."""
        if self.environment != "production":
            return self
        problems: list[str] = []
        if not self.cookie_secure:
            problems.append(
                "GOATFARM_COOKIE_SECURE must be true in production "
                "(the refresh JWT travels in a cookie; over plain HTTP it leaks)"
            )
        localhost = [
            origin for origin in self.cors_origins if "localhost" in origin or "127.0.0.1" in origin
        ]
        if localhost:
            problems.append(
                f"GOATFARM_CORS_ORIGINS must not include dev origins in production: {localhost}"
            )
        if not self.cors_origins:
            problems.append(
                "GOATFARM_CORS_ORIGINS must not be empty in production "
                "(the credentialed SPA needs at least one HTTPS origin)"
            )
        if self.db_sslmode in {"disable", "allow", "prefer"}:
            problems.append(
                f"GOATFARM_DB_SSLMODE={self.db_sslmode!r} is unsafe in production — "
                "use 'require', 'verify-ca', or 'verify-full'"
            )
        if problems:
            raise ValueError("Refusing to boot: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
