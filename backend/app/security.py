"""Password hashing (Argon2id) and JWT issuing/verification.

Argon2id via argon2-cffi with parameters from Settings (protected — env-only).
A legacy pbkdf2 verifier keeps users migrated from the v1 SQLite database
able to log in; their hash is transparently upgraded to Argon2id on success.

JWTs are RS256 (PyJWT + cryptography). The dev keypair is generated on first
use into backend/keys/ (gitignored); production supplies real keys via
GOATFARM_JWT_PRIVATE_KEY_PATH / GOATFARM_JWT_PUBLIC_KEY_PATH.
"""

import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import (
    Argon2Error,
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .core.config import get_settings

LEGACY_PBKDF2_PREFIX = "pbkdf2_sha256"


def _password_hasher() -> PasswordHasher:
    s = get_settings()
    return PasswordHasher(
        time_cost=s.argon2_time_cost,
        memory_cost=s.argon2_memory_cost,
        parallelism=s.argon2_parallelism,
        hash_len=s.argon2_hash_len,
    )


def password_policy_error(password: str) -> str | None:
    """None if acceptable, else a user-facing error message."""
    minimum = get_settings().min_password_length
    if len(password or "") < minimum:
        return f"Password must be at least {minimum} characters."
    if not password.strip():
        return "Password cannot be only whitespace."
    return None


def hash_password(password: str) -> str:
    return _password_hasher().hash(password)


def _verify_legacy_pbkdf2(password: str, stored: str) -> bool:
    """v1 hashes: 'pbkdf2_sha256$iterations$salt_hex$digest_hex'."""
    try:
        _algo, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def verify_password(password: str, stored: str) -> tuple[bool, bool]:
    """(verified, needs_rehash). Legacy pbkdf2 hashes verify but flag for
    upgrade so callers can store a fresh Argon2id hash."""
    if stored.startswith(LEGACY_PBKDF2_PREFIX + "$"):
        return _verify_legacy_pbkdf2(password, stored), True
    try:
        ok = _password_hasher().verify(stored, password)
    except (VerifyMismatchError, VerificationError, Argon2Error, InvalidHashError):
        # InvalidHashError (not an Argon2Error subclass) covers stored hashes
        # that aren't recognizable Argon2 at all — botched migrations, manual
        # DB edits, bcrypt leftovers. Unverifiable means invalid credentials,
        # never a 500.
        return False, False
    return ok, ok and _password_hasher().check_needs_rehash(stored)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
def _ensure_keypair() -> None:
    s = get_settings()
    priv, pub = s.jwt_private_key_path, s.jwt_public_key_path
    if priv.exists() and pub.exists():
        return
    priv.parent.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    priv.chmod(0o600)
    pub.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )


def _read(path: Path) -> str:
    _ensure_keypair()
    return path.read_text()


def issue_token(subject: int, kind: str, ttl_seconds: int) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(subject),
        "kind": kind,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, _read(s.jwt_private_key_path), algorithm=s.jwt_algorithm)


def issue_access_token(user_id: int) -> str:
    return issue_token(user_id, "access", get_settings().access_token_ttl_seconds)


def issue_refresh_token(user_id: int) -> str:
    return issue_token(user_id, "refresh", get_settings().refresh_token_ttl_seconds)


def decode_token(token: str, expected_kind: str) -> int | None:
    """Return the user id, or None when invalid/expired/wrong kind."""
    s = get_settings()
    try:
        payload = jwt.decode(token, _read(s.jwt_public_key_path), algorithms=[s.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    if payload.get("kind") != expected_kind:
        return None
    try:
        return int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None
