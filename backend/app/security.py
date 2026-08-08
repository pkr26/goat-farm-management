"""Password hashing (Argon2id) and JWT issuing/verification.

Argon2id via argon2-cffi with parameters from Settings (protected — env-only).
A legacy pbkdf2 verifier keeps users migrated from the v1 SQLite database
able to log in; their hash is transparently upgraded to Argon2id on success.

JWTs are RS256 (PyJWT + cryptography). The dev keypair is generated on first
use into backend/keys/ (gitignored); production supplies real keys via
GOATFARM_JWT_PRIVATE_KEY_PATH / GOATFARM_JWT_PUBLIC_KEY_PATH. Newly issued
tokens identify that pair with a deterministic ``kid``. During rotation,
GOATFARM_JWT_PREVIOUS_PUBLIC_KEY_PATHS keeps a bounded verification-only
keyring so old tokens remain valid. Key text is read from disk once and cached
in memory (thread-safe); first-boot
generation writes temp files and os.replaces them into place so concurrent
readers never see a half-written key, and holds an flock on a sibling lock
file so two first-booting PROCESSES can't interleave writes into a
mismatched keypair (which would 401 every token).

Platform note: flock makes this module Unix-only (Linux/macOS); Windows
contributors would need an msvcrt fallback — not needed for deployment.
"""

import base64
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
# Guards first-boot keypair generation and the read-once keyring below.
_key_lock = threading.RLock()
_key_cache: dict[Path, str] = {}


class _JwtKeyring(NamedTuple):
    """Immutable process snapshot of the configured signing/verification keys."""

    configured_paths: tuple[Path, ...]
    signing_private_key: str
    active_kid: str
    verification_by_kid: dict[str, str]
    legacy_verification_keys: tuple[str, ...]


_jwt_keyring: _JwtKeyring | None = None


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
    """Generate the development keypair on first use. Production must mount
    stable key material and therefore fails closed instead of generating an
    ephemeral identity. Caller must hold _key_lock."""
    s = get_settings()
    priv, pub = s.jwt_private_key_path, s.jwt_public_key_path
    if priv.exists() and pub.exists():
        return
    if s.environment == "production":
        raise RuntimeError(
            "Production JWT keypair is missing. Mount stable private/public PEM files and set "
            "GOATFARM_JWT_PRIVATE_KEY_PATH / GOATFARM_JWT_PUBLIC_KEY_PATH."
        )
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


def _public_key_der(key: rsa.RSAPublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _rsa_key_id(key: rsa.RSAPublicKey) -> str:
    """Stable, non-secret ID: base64url(SHA-256(SPKI DER)), without padding."""
    digest = hashlib.sha256(_public_key_der(key)).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _configured_key_paths() -> tuple[Path, ...]:
    s = get_settings()
    return (
        s.jwt_private_key_path,
        s.jwt_public_key_path,
        *s.jwt_previous_public_key_paths,
    )


def _load_jwt_keyring_locked() -> _JwtKeyring:
    """Read and validate the complete configured keyring.

    Caller holds ``_key_lock``. The snapshot is published only after every
    key validates, so a bad rotation configuration can never be partly used.
    """
    global _jwt_keyring

    s = get_settings()
    _ensure_keypair()
    try:
        private_text = s.jwt_private_key_path.read_text()
        active_public_text = s.jwt_public_key_path.read_text()
        private_key = serialization.load_pem_private_key(private_text.encode(), password=None)
        active_public_key = serialization.load_pem_public_key(active_public_text.encode())
        if not isinstance(private_key, rsa.RSAPrivateKey) or not isinstance(
            active_public_key, rsa.RSAPublicKey
        ):
            raise ValueError("RS256 requires RSA private and public keys")
        if private_key.key_size < 2048:
            raise ValueError("RSA signing key must be at least 2048 bits")
        if active_public_key.key_size < 2048:
            raise ValueError("RSA verification key must be at least 2048 bits")
        if not hmac.compare_digest(
            _public_key_der(private_key.public_key()), _public_key_der(active_public_key)
        ):
            raise ValueError("private/public keys do not match")

        verification_entries: list[tuple[Path, str, rsa.RSAPublicKey]] = [
            (s.jwt_public_key_path, active_public_text, active_public_key)
        ]
        for path in s.jwt_previous_public_key_paths:
            public_text = path.read_text()
            public_key = serialization.load_pem_public_key(public_text.encode())
            if not isinstance(public_key, rsa.RSAPublicKey):
                raise ValueError(f"previous verification key {path} is not an RSA public key")
            if public_key.key_size < 2048:
                raise ValueError(f"previous RSA verification key {path} must be at least 2048 bits")
            verification_entries.append((path, public_text, public_key))

        verification_by_kid: dict[str, str] = {}
        cached_text: dict[Path, str] = {s.jwt_private_key_path: private_text}
        for path, public_text, public_key in verification_entries:
            kid = _rsa_key_id(public_key)
            if kid in verification_by_kid:
                raise ValueError(
                    f"duplicate JWT key id {kid!r}; active and previous public keys must be unique"
                )
            verification_by_kid[kid] = public_text
            cached_text[path] = public_text

        active_kid = _rsa_key_id(active_public_key)
        keyring = _JwtKeyring(
            configured_paths=_configured_key_paths(),
            signing_private_key=private_text,
            active_kid=active_kid,
            verification_by_kid=verification_by_kid,
            legacy_verification_keys=tuple(entry[1] for entry in verification_entries),
        )
    except (OSError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid JWT signing keypair or rotation key: {exc}") from exc

    # Replace rather than grow the old path cache: even test/config reloads
    # retain at most one bounded keyring in memory.
    _key_cache.clear()
    _key_cache.update(cached_text)
    _jwt_keyring = keyring
    return keyring


def validate_jwt_keypair() -> None:
    """Validate and cache the active pair plus all previous public keys.

    Missing, unreadable, malformed, weak, mismatched or duplicate production
    keys fail startup before the process accepts traffic.
    """
    with _key_lock:
        _load_jwt_keyring_locked()


def _get_jwt_keyring() -> _JwtKeyring:
    """Return the read-once keyring, loading lazily for direct unit callers."""
    configured_paths = _configured_key_paths()
    cached = _jwt_keyring
    if cached is not None and cached.configured_paths == configured_paths:
        return cached
    with _key_lock:
        cached = _jwt_keyring
        if cached is None or cached.configured_paths != configured_paths:
            cached = _load_jwt_keyring_locked()
        return cached


def issue_token(
    subject: int,
    kind: str,
    ttl_seconds: int,
    jti: str | None = None,
    *,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    s = get_settings()
    keyring = _get_jwt_keyring()
    now = issued_at or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    expiry = expires_at or now + timedelta(seconds=ttl_seconds)
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    payload = {
        "sub": str(subject),
        "kind": kind,
        "jti": jti or uuid.uuid4().hex,
        "iat": now,
        "exp": expiry,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(
        payload,
        keyring.signing_private_key,
        algorithm=s.jwt_algorithm,
        headers={"kid": keyring.active_kid},
    )


def issue_access_token(user_id: int, token_version: int = 0) -> str:
    return issue_token(
        user_id,
        "access",
        get_settings().access_token_ttl_seconds,
        extra_claims={"ver": token_version},
    )


def issue_refresh_token(
    user_id: int,
    jti: str | None = None,
    *,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> str:
    # The caller picks the jti when it must persist it (refresh_sessions row).
    return issue_token(
        user_id,
        "refresh",
        get_settings().refresh_token_ttl_seconds,
        jti=jti,
        issued_at=issued_at,
        expires_at=expires_at,
    )


class AccessClaims(NamedTuple):
    user_id: int
    token_version: int


class RefreshClaims(NamedTuple):
    """Decoded, verified refresh-token claims needed for session tracking."""

    user_id: int
    jti: str
    expires_at: datetime  # naive UTC, like every stored datetime


def _decode_payload(token: str, expected_kind: str) -> dict[str, Any] | None:
    s = get_settings()
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        return None
    # Never allow the untrusted header to select an algorithm. ``kid`` is a
    # dictionary lookup only — it is never interpreted as a path or URL.
    if header.get("alg") != s.jwt_algorithm:
        return None

    keyring = _get_jwt_keyring()
    candidates: tuple[str, ...]
    if "kid" in header:
        kid = header["kid"]
        if not isinstance(kid, str):
            return None
        key = keyring.verification_by_kid.get(kid)
        if key is None:
            return None
        candidates = (key,)
    else:
        # Tokens issued before ``kid`` deployment remain usable during the
        # migration window. The configured previous-key cap bounds this to at
        # most four RSA verifications (active + three previous).
        candidates = keyring.legacy_verification_keys

    payload: dict[str, Any] | None = None
    for key in candidates:
        try:
            # leeway=60s: absorb reasonable clock skew between API instances.
            payload = jwt.decode(token, key, algorithms=[s.jwt_algorithm], leeway=60)
            break
        except jwt.PyJWTError:
            continue
    if payload is None:
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


def decode_access_claims(token: str) -> AccessClaims | None:
    """Verified access identity plus its server-checked revocation version.

    Tokens issued before versioning omitted ``ver``; treating those as version
    zero gives a bounded rollout path while the normal access TTL expires them.
    """
    payload = _decode_payload(token, "access")
    if payload is None:
        return None
    try:
        user_id = int(payload["sub"])
        token_version = int(payload.get("ver", 0))
    except (KeyError, TypeError, ValueError):
        return None
    if token_version < 0:
        return None
    return AccessClaims(user_id=user_id, token_version=token_version)


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
