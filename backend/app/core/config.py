"""Application settings (pydantic-settings). All values overridable via env
vars prefixed GOATFARM_, e.g. GOATFARM_DATABASE_URL."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GOATFARM_", env_file=".env", extra="ignore")

    # Database (asyncpg driver). `goatfarm_test` is used by the test suite.
    database_url: str = "postgresql+asyncpg://localhost:5432/goatfarm"

    # Connection pool + per-statement guardrails. The statement timeout is a
    # backstop against runaway queries (a full-table scan must not hold a
    # connection forever); 30 s is far above anything the suite or a small
    # SaaS workload legitimately runs.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30  # seconds to wait for a free connection
    db_statement_timeout_ms: int = 30_000  # asyncpg server_settings

    # JWT: RS256 keypair lives in backend/keys/ (generated on first run,
    # gitignored). Access token travels in the Authorization header; the
    # refresh token in an httpOnly SameSite=Lax cookie.
    jwt_private_key_path: Path = BACKEND_DIR / "keys" / "jwt_private.pem"
    jwt_public_key_path: Path = BACKEND_DIR / "keys" / "jwt_public.pem"
    jwt_algorithm: str = "RS256"
    access_token_ttl_seconds: int = 30 * 60
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14
    refresh_cookie_name: str = "goatfarm_refresh"

    # Argon2id parameters (protected: only changeable via env, never at runtime).
    argon2_time_cost: int = 3
    argon2_memory_cost: int = 65536  # KiB (64 MiB)
    argon2_parallelism: int = 4
    argon2_hash_len: int = 32

    # Frontend dev server origins allowed to call the API with credentials.
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # Refresh-token cookie flags (httpOnly + SameSite=Lax always on).
    cookie_secure: bool = False  # set True behind HTTPS in production

    min_password_length: int = 8

    # Auth endpoints rate limiting: in-memory sliding window, per process.
    # Login counts FAILED attempts only (a success resets the count) and is
    # keyed per (client IP, email); register counts every attempt per IP.
    auth_rate_limit_enabled: bool = True
    auth_rate_limit_max_attempts: int = 10
    auth_rate_limit_window_seconds: int = 300

    # Comma-separated IPs/CIDRs of trusted reverse proxies (e.g.
    # "127.0.0.1,10.0.0.0/8"). When non-empty, X-Forwarded-For from those
    # hosts restores the real client IP (rate limiting, logs). Default ""
    # trusts nothing: a spoofable header must never steer the limiter.
    trusted_proxy_hosts: str = ""

    # A single account may own at most this many farms.
    max_farms_per_user: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
