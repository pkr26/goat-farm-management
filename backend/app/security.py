"""Password hashing (Argon2id) and JWT issuing/verification.

Argon2id via argon2-cffi with parameters from Settings (protected — env-only).
A legacy pbkdf2 verifier keeps users migrated from the v1 SQLite database
able to log in; their hash is transparently upgraded to Argon2id on success.

JWTs are RS256 (PyJWT + cryptography). The dev keypair is generated on first
use into backend/keys/ (gitignored); production supplies real keys via
GOATFARM_JWT_PRIVATE_KEY_PATH / GOATFARM_JWT_PUBLIC_KEY_PATH. Key text is
read from disk once and cached in memory (thread-safe); first-boot
generation writes temp files and os.replaces them into place so concurrent
readers never see a half-written key, and holds an flock on a sibling lock
file so two first-booting PROCESSES can't interleave writes into a
mismatched keypair (which would 401 every token).

Platform note: flock makes this module Unix-only (Linux/macOS); Windows
contributors would need an msvcrt fallback — not needed for deployment.
"""

import fcntl
import hashlib
import hmac
import os
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

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


def prime_dummy_password_hash() -> str:
    """Compute the throwaway Argon2 hash the login path uses to equalize
    unknown-email timing. Called from create_app() at boot so the first
    unknown-email login pays no cold-start cost."""
    return hash_password("dummy-password-for-timing-equalization")


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
# Guards first-boot keypair generation and the read-once cache below.
_key_lock = threading.Lock()
_key_cache: dict[Path, str] = {}


def _write_atomic(path: Path, data: bytes, mode: int | None = None) -> None:
    """Temp file + os.replace: readers only ever see the old or the new file,
    never a half-written one. Permissions are set before the rename so the
    private key never exists at its final path with a looser mode."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    if mode is not None:
        tmp.chmod(mode)
    os.replace(tmp, path)


def _generate_keypair(priv: Path, pub: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _write_atomic(
        priv,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        mode=0o600,
    )
    _write_atomic(
        pub,
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
    )


def _ensure_keypair() -> None:
    """Generate the dev keypair on first use. Caller must hold _key_lock."""
    s = get_settings()
    priv, pub = s.jwt_private_key_path, s.jwt_public_key_path
    if priv.exists() and pub.exists():
        return
    priv.parent.mkdir(parents=True, exist_ok=True)
    # _key_lock is per-process: two first-booting PROCESSES could still
    # interleave the atomic writes and leave a mismatched pair on disk (every
    # token would then fail verification). Serialize generation across
    # processes with an exclusive flock on a sibling lock file, and re-check
    # under it so the loser adopts the winner's pair instead of regenerating.
    lock_path = priv.parent / ".jwt_keygen.lock"
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        if not (priv.exists() and pub.exists()):
            _generate_keypair(priv, pub)


def _read(path: Path) -> str:
    """Key text, read from disk once and cached in memory (thread-safe):
    signing and verification must not hit the filesystem per token."""
    cached = _key_cache.get(path)
    if cached is not None:
        return cached
    with _key_lock:
        if path not in _key_cache:  # re-check under the lock
            _ensure_keypair()
            _key_cache[path] = path.read_text()
        return _key_cache[path]


def issue_token(subject: int, kind: str, ttl_seconds: int, jti: str | None = None) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(subject),
        "kind": kind,
        "jti": jti or uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, _read(s.jwt_private_key_path), algorithm=s.jwt_algorithm)


def issue_access_token(user_id: int) -> str:
    return issue_token(user_id, "access", get_settings().access_token_ttl_seconds)


def issue_refresh_token(user_id: int, jti: str | None = None) -> str:
    # The caller picks the jti when it must persist it (refresh_sessions row).
    return issue_token(user_id, "refresh", get_settings().refresh_token_ttl_seconds, jti=jti)


class RefreshClaims(NamedTuple):
    """Decoded, verified refresh-token claims needed for session tracking."""

    user_id: int
    jti: str
    expires_at: datetime  # naive UTC, like every stored datetime


def _decode_payload(token: str, expected_kind: str) -> dict[str, Any] | None:
    s = get_settings()
    try:
        # leeway=60s: absorb reasonable clock skew between the API server and
        # any load balancer / auth companion — a token issued microseconds ago
        # must not 401 because our clock ticked backwards by 200 ms.
        payload: dict[str, Any] = jwt.decode(
            token, _read(s.jwt_public_key_path), algorithms=[s.jwt_algorithm], leeway=60
        )
    except jwt.PyJWTError:
        return None
    if payload.get("kind") != expected_kind:
        return None
    return payload


def decode_token(token: str, expected_kind: str) -> int | None:
    """Return the user id, or None when invalid/expired/wrong kind."""
    payload = _decode_payload(token, expected_kind)
    if payload is None:
        return None
    try:
        return int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None


def decode_refresh_claims(token: str) -> RefreshClaims | None:
    """Refresh-token user id + jti + expiry, or None when invalid/expired.

    jwt.decode already rejects expired tokens; expires_at comes back for the
    session row's expiry cross-check.
    """
    payload = _decode_payload(token, "refresh")
    if payload is None:
        return None
    try:
        user_id = int(payload["sub"])
        jti = str(payload["jti"])
        expires_at = datetime.fromtimestamp(float(payload["exp"]), UTC).replace(tzinfo=None)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None
    return RefreshClaims(user_id=user_id, jti=jti, expires_at=expires_at)
