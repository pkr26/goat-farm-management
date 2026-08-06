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


@lru_cache
def get_settings() -> Settings:
    return Settings()
