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

import asyncio
import base64
import fcntl
import hashlib
import hmac
import logging
import os
import stat
import tempfile
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import lru_cache
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

from .core.config import BACKEND_DIR, get_settings

_logger = logging.getLogger(__name__)

LEGACY_PBKDF2_PREFIX = "pbkdf2_sha256"
# Explicit compatibility boundary for hashes copied verbatim from the retired
# SQLite application. The old verifier had no ceiling, while the history that
# remains in this repository exercises 2,600 and 50,000 iterations. One million
# keeps higher-cost external imports usable (and covers modern PBKDF2 costs)
# without allowing a corrupt/hostile database value to choose unbounded CPU.
# Deployers must audit imported hashes above this ceiling before rollout.
LEGACY_PBKDF2_MAX_ITERATIONS = 1_000_000

REQUIRED_JWT_CLAIMS = ("sub", "kind", "jti", "iat", "exp", "iss", "aud")
RESERVED_JWT_CLAIMS = frozenset(REQUIRED_JWT_CLAIMS)


class PasswordWorkCapacityError(RuntimeError):
    """The bounded Argon2 worker pool is already fully occupied."""


_password_worker_count = get_settings().argon2_worker_threads
_password_executor = ThreadPoolExecutor(
    max_workers=_password_worker_count,
    thread_name_prefix="goatfarm-argon2",
)
_password_slots = threading.BoundedSemaphore(_password_worker_count)


async def _run_password_work[ResultT](work: Callable[[], ResultT]) -> ResultT:
    """Run CPU/memory-hard password work off-loop with no unbounded queue.

    A cancelled HTTP request does not free its slot until the native Argon2
    call actually finishes; otherwise repeated disconnects could enqueue an
    arbitrary number of 64-MiB jobs behind the fixed worker count.
    """
    slots = _password_slots
    if not slots.acquire(blocking=False):
        raise PasswordWorkCapacityError("Password service is busy")
    release_lock = threading.Lock()
    released = False

    def release_slot_once() -> None:
        nonlocal released
        with release_lock:
            if released:
                return
            released = True
        slots.release()

    def run_and_release() -> ResultT:
        # Release in the executor thread itself. An asyncio Future completion
        # callback needs its originating event loop to remain alive; test
        # harnesses and graceful shutdown can close that loop while native
        # Argon work is still finishing, permanently leaking a process-global
        # semaphore slot into the next loop otherwise.
        try:
            return work()
        finally:
            release_slot_once()

    try:
        future = asyncio.get_running_loop().run_in_executor(_password_executor, run_and_release)
    except BaseException:
        # Executor shutdown and loop teardown can reject a submission after
        # admission. There is no future whose callback could return the slot in
        # that path, so release synchronously or every later password request
        # eventually sees a permanently exhausted pool.
        release_slot_once()
        raise
    return await asyncio.shield(future)


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


async def hash_password_async(password: str) -> str:
    return await _run_password_work(lambda: hash_password(password))


@lru_cache(maxsize=1)
def prime_dummy_password_hash() -> str:
    """Compute the throwaway Argon2 hash the login path uses to equalize
    unknown-email timing. Called at boot and cached so the first rejected
    login reuses the already-computed hash instead of exposing a cold-start
    timing difference."""
    return hash_password("dummy-password-for-timing-equalization")


def _legacy_pbkdf2_parts(stored: str) -> tuple[int, bytes, str] | None:
    try:
        algo, raw_iterations, salt_hex, digest_hex = stored.split("$")
        # int() tolerates the iteration spellings real importers produced
        # ('+50000', ' 50000', '50_000'); those hashes verified in the v1
        # application and must keep verifying here.
        iterations = int(raw_iterations)
        salt = bytes.fromhex(salt_hex)
        bytes.fromhex(digest_hex)
    except (ValueError, TypeError):
        return None
    if algo != LEGACY_PBKDF2_PREFIX or not salt or not digest_hex:
        return None
    if not 1 <= iterations <= LEGACY_PBKDF2_MAX_ITERATIONS:
        # Without this signal an over-ceiling import is indistinguishable
        # from a wrong password: the account is permanently locked out and
        # nobody learns why. Log the anomaly (never the hash or identity).
        _logger.warning(
            "Rejecting legacy pbkdf2 hash outside the supported iteration "
            "range (iterations=%d, ceiling=%d); the account cannot log in "
            "until its credential is reset or re-imported within the ceiling",
            iterations,
            LEGACY_PBKDF2_MAX_ITERATIONS,
        )
        return None
    return iterations, salt, digest_hex


def _verify_legacy_pbkdf2(password: str, stored: str) -> bool:
    """v1 hashes: 'pbkdf2_sha256$iterations$salt_hex$digest_hex'."""
    parts = _legacy_pbkdf2_parts(stored)
    if parts is None:
        return False
    iterations, salt, digest_hex = parts
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return hmac.compare_digest(digest.hex(), digest_hex)


def _pad_rejected_login_pbkdf2(password: str, stored: str) -> None:
    # Every rejected login pays this budget in addition to one Argon2
    # verification: legacy accounts spend part of it checking their real hash
    # and the remainder on padding; Argon2/unknown/malformed accounts spend
    # the whole budget on padding. The budget is a deployment setting sized
    # to the legacy corpus actually present — pegging it to the 1M validity
    # ceiling made every rejection ~20x costlier than any real hash requires
    # and handed attackers a matching executor-saturation amplifier.
    parts = _legacy_pbkdf2_parts(stored)
    used_iterations = parts[0] if parts is not None else 0
    remaining = max(get_settings().rejected_login_pbkdf2_work_budget - used_iterations, 0)
    if remaining:
        hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            b"goatfarm-login-timing-padding",
            remaining,
        )


def _complete_rejected_login_timing(
    password: str, stored: str, dummy_hash: str, did_argon_work: bool
) -> None:
    if not did_argon_work:
        # The initial legacy/malformed verification did no Argon2 work.
        verify_password(password, dummy_hash)
    _pad_rejected_login_pbkdf2(password, stored)


async def complete_rejected_login_timing_async(
    password: str, stored: str, dummy_hash: str, did_argon_work: bool
) -> None:
    """Finish one rejected login's fixed work in one executor submission.

    The initial real/dummy verification is submission one. This is always
    submission two, including legacy rows, so executor contention cannot
    reveal an account's hash generation through a 2-vs-3 scheduling pattern.
    """
    await _run_password_work(
        lambda: _complete_rejected_login_timing(password, stored, dummy_hash, did_argon_work)
    )


def _verify_password_with_work(password: str, stored: str) -> tuple[bool, bool, bool]:
    """Return verified, needs-rehash, and whether real Argon2 work ran."""
    if stored.startswith(LEGACY_PBKDF2_PREFIX + "$"):
        return _verify_legacy_pbkdf2(password, stored), True, False
    hasher = _password_hasher()
    try:
        ok = hasher.verify(stored, password)
    except VerifyMismatchError:
        # Reaching the password comparison proves libargon2 paid the encoded
        # memory/time cost, even though the credentials did not match.
        return False, False, True
    except (VerificationError, Argon2Error, InvalidHashError):
        # InvalidHashError (not an Argon2Error subclass) covers stored hashes
        # that aren't recognizable Argon2 at all — botched migrations, manual
        # DB edits, bcrypt leftovers. Unverifiable means invalid credentials,
        # never a 500.
        return False, False, False
    return ok, ok and hasher.check_needs_rehash(stored), True


def verify_password(password: str, stored: str) -> tuple[bool, bool]:
    """(verified, needs_rehash). Legacy hashes flag for an Argon2 upgrade."""
    verified, needs_rehash, _did_argon_work = _verify_password_with_work(password, stored)
    return verified, needs_rehash


async def verify_password_async(password: str, stored: str) -> tuple[bool, bool]:
    return await _run_password_work(lambda: verify_password(password, stored))


async def verify_password_with_work_async(password: str, stored: str) -> tuple[bool, bool, bool]:
    """Login-only verification with timing-work evidence."""
    return await _run_password_work(lambda: _verify_password_with_work(password, stored))


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
# Guards first-boot keypair generation and the read-once keyring below.
_key_lock = threading.RLock()
_key_cache: dict[Path, str] = {}
_MAX_JWT_KEY_BYTES = 1_048_576


class _JwtKeyring(NamedTuple):
    """Immutable process snapshot of the configured signing/verification keys."""

    configured_paths: tuple[Path, ...]
    signing_private_key: str
    active_kid: str
    verification_by_kid: dict[str, str]
    legacy_verification_keys: tuple[str, ...]


_jwt_keyring: _JwtKeyring | None = None


def _read_pinned_key_text(path: Path) -> str:
    """Read one bounded regular key-file snapshot without reopening its name.

    O_NONBLOCK lets fstat reject a raced FIFO without hanging startup. Symlinks
    remain intentional here: Kubernetes and other secret mounts commonly expose
    versioned regular files through stable symlink paths.
    """
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"JWT key path {path} is not a regular file")
        if before.st_size > _MAX_JWT_KEY_BYTES:
            raise ValueError(f"JWT key path {path} exceeds {_MAX_JWT_KEY_BYTES} bytes")
        raw = bytearray()
        while len(raw) <= _MAX_JWT_KEY_BYTES:
            chunk = os.read(fd, min(64 * 1024, _MAX_JWT_KEY_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if len(raw) > _MAX_JWT_KEY_BYTES:
        raise ValueError(f"JWT key path {path} exceeds {_MAX_JWT_KEY_BYTES} bytes")
    if (
        len(raw) != before.st_size
        or (before.st_dev, before.st_ino, before.st_size)
        != (after.st_dev, after.st_ino, after.st_size)
        or (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns)
    ):
        raise ValueError(f"JWT key path {path} changed while it was read")
    try:
        return bytes(raw).decode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"JWT key path {path} is not valid UTF-8") from exc


def _write_atomic(path: Path, data: bytes, mode: int | None = None) -> None:
    """Temp file + os.replace: readers only ever see the old or the new file,
    never a half-written one.

    ``NamedTemporaryFile`` creates its inode mode 0600 before any byte is
    written.  That ordering matters for private PEM material: ``Path.write_bytes``
    would otherwise create the predictable ``.tmp`` file subject to the
    process umask (commonly 0644), leaving a short local-read window before a
    later chmod.  A caller can deliberately widen the final mode for public
    material, but private bytes are never written to such a file.
    """
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            tmp = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            if mode is not None:
                # Apply the requested final mode only after the complete
                # payload has been written; a private PEM stays 0600 here.
                os.fchmod(stream.fileno(), mode)
        os.replace(tmp, path)
    except BaseException:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                # Preserve the original write/replace failure. The temporary
                # file remains 0600, so failed cleanup cannot disclose keys.
                pass
        raise


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
        mode=0o644,
    )


def _development_keypair_is_valid(priv: Path, pub: Path) -> bool:
    """Whether an application-managed development pair is complete/matching.

    A generator crash can leave both names present when one side predated the
    attempted repair. The persistent sibling lock identifies pairs managed by
    this generator, so a later boot may safely repair that torn publication;
    fully pre-provisioned pairs without the marker still fail closed in the
    ordinary keyring validator instead of being overwritten.
    """
    try:
        private_key = serialization.load_pem_private_key(
            _read_pinned_key_text(priv).encode(),
            password=None,
        )
        public_key = serialization.load_pem_public_key(_read_pinned_key_text(pub).encode())
    except (OSError, TypeError, ValueError):
        return False
    return (
        isinstance(private_key, rsa.RSAPrivateKey)
        and isinstance(public_key, rsa.RSAPublicKey)
        and private_key.key_size >= 2048
        and public_key.key_size >= 2048
        and hmac.compare_digest(
            _public_key_der(private_key.public_key()),
            _public_key_der(public_key),
        )
    )


def _ensure_keypair() -> None:
    """Generate the development keypair on first use. Production must mount
    stable key material and therefore fails closed instead of generating an
    ephemeral identity. Caller must hold _key_lock."""
    s = get_settings()
    priv, pub = s.jwt_private_key_path, s.jwt_public_key_path
    if s.environment == "production":
        if priv.exists() and pub.exists():
            return
        raise RuntimeError(
            "Production JWT keypair is missing. Mount stable private/public PEM files and set "
            "GOATFARM_JWT_PRIVATE_KEY_PATH / GOATFARM_JWT_PUBLIC_KEY_PATH."
        )
    # ``mode`` applies when this application-owned leaf is first created. Do
    # not chmod an existing configured parent: it may be a deliberately shared
    # or externally managed directory. Individual private files are still
    # created 0600 by _write_atomic.
    key_dir = priv.parent
    key_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    # The default development directory belongs to this application, so repair
    # an older world-searchable instance as well. Configured existing parents
    # are deliberately left alone: they may be shared or externally managed,
    # and private files themselves remain 0600.
    if key_dir == BACKEND_DIR / "keys":
        existing_mode = key_dir.stat().st_mode & 0o7777
        private_mode = (existing_mode | 0o700) & ~0o077
        if private_mode != existing_mode:
            key_dir.chmod(private_mode)
    # _key_lock is per-process: two first-booting PROCESSES could still
    # interleave the atomic writes and leave a mismatched pair on disk (every
    # token would then fail verification). Serialize generation across
    # processes with an exclusive flock on a sibling lock file, and re-check
    # under it so the loser adopts the winner's pair instead of regenerating.
    #
    # A completed first boot leaves the lock file in place intentionally. If a
    # process starts while another generator has published the private key but
    # not yet the matching public key (or vice versa), both target names can be
    # present briefly when one side was pre-existing. Checking only the two PEM
    # names would let that reader bypass flock and validate a torn pair. Once a
    # generation lock exists, every later development reader crosses it before
    # loading either file. A fully pre-provisioned pair with no application lock
    # remains read-only friendly and needs no generation coordination.
    lock_path = priv.parent / ".jwt_keygen.lock"
    if priv.exists() and pub.exists() and not lock_path.exists():
        return
    lock_flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        lock_fd = os.open(lock_path, lock_flags, 0o600)
    except OSError as exc:
        raise RuntimeError(f"Cannot safely open JWT key-generation lock: {exc}") from exc
    try:
        opened = os.fstat(lock_fd)
        if not stat.S_ISREG(opened.st_mode):
            raise RuntimeError("JWT key-generation lock is not a regular file")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        named = os.lstat(lock_path)
        if not stat.S_ISREG(named.st_mode) or (opened.st_dev, opened.st_ino) != (
            named.st_dev,
            named.st_ino,
        ):
            raise RuntimeError("JWT key-generation lock path changed during acquisition")
        os.fchmod(lock_fd, 0o600)
        pair_complete = priv.exists() and pub.exists()
        pair_valid = pair_complete and _development_keypair_is_valid(priv, pub)
        if pair_complete and not pair_valid:
            _logger.warning("Repairing torn application-managed development JWT keypair")
        if not pair_valid:
            _generate_keypair(priv, pub)
        named_after = os.lstat(lock_path)
        if not stat.S_ISREG(named_after.st_mode) or (opened.st_dev, opened.st_ino) != (
            named_after.st_dev,
            named_after.st_ino,
        ):
            raise RuntimeError("JWT key-generation lock path changed during generation")
    finally:
        os.close(lock_fd)


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
        private_text = _read_pinned_key_text(s.jwt_private_key_path)
        active_public_text = _read_pinned_key_text(s.jwt_public_key_path)
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
            public_text = _read_pinned_key_text(path)
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
        "iss": s.jwt_issuer,
        "aud": s.jwt_audience,
    }
    if extra_claims:
        collision = RESERVED_JWT_CLAIMS.intersection(extra_claims)
        if collision:
            raise ValueError(
                f"extra_claims cannot override reserved JWT claims: {sorted(collision)}"
            )
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
    family_id: str | None = None,
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
        extra_claims={"fid": family_id} if family_id is not None else None,
    )


class AccessClaims(NamedTuple):
    user_id: int
    token_version: int


class AccessDecodeResult(NamedTuple):
    """Access claims plus a non-abusive, signature-verified expiry signal."""

    claims: AccessClaims | None
    expired: bool


class RefreshClaims(NamedTuple):
    """Decoded, verified refresh-token claims needed for session tracking."""

    user_id: int
    jti: str
    family_id: str | None
    expires_at: datetime  # naive UTC, like every stored datetime


def _decode_payload_result(
    token: str,
    expected_kind: str,
) -> tuple[dict[str, Any] | None, bool]:
    s = get_settings()
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        return None, False
    # Never allow the untrusted header to select an algorithm. ``kid`` is a
    # dictionary lookup only — it is never interpreted as a path or URL.
    if header.get("alg") != s.jwt_algorithm:
        return None, False

    keyring = _get_jwt_keyring()
    candidates: tuple[str, ...]
    if "kid" in header:
        kid = header["kid"]
        if not isinstance(kid, str):
            return None, False
        key = keyring.verification_by_kid.get(kid)
        if key is None:
            return None, False
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
            payload = jwt.decode(
                token,
                key,
                algorithms=[s.jwt_algorithm],
                audience=s.jwt_audience,
                issuer=s.jwt_issuer,
                leeway=60,
                # Expiry is checked immediately below after every other
                # signature/claim validation succeeds. Keeping the authentic
                # expired state distinct prevents normal clients racing to
                # refresh from being charged to the attacker-invalid limiter.
                options={
                    "require": list(REQUIRED_JWT_CLAIMS),
                    "verify_exp": False,
                },
            )
            break
        except jwt.PyJWTError:
            continue
    if payload is None:
        return None, False
    if payload.get("kind") != expected_kind:
        return None, False
    subject = payload.get("sub")
    jti = payload.get("jti")
    # PyJWT verifies these registered claim types too, but keep the identity
    # contract explicit: user ids are canonical positive decimal strings and
    # session ids are non-empty bounded strings.
    if (
        not isinstance(subject, str)
        or len(subject) > 20
        or not subject.isascii()
        or not subject.isdecimal()
    ):
        return None, False
    subject_id = int(subject)
    if subject != str(subject_id) or subject_id <= 0:
        return None, False
    if not isinstance(jti, str) or not jti or len(jti) > 128:
        return None, False
    try:
        # Mirror PyJWT's registered-claim rule: it converts exp with int() and
        # expires at ``exp <= now - leeway``.
        expires_at = int(payload["exp"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None, False
    expired = expires_at <= datetime.now(UTC).timestamp() - 60
    return payload, expired


def _decode_payload(token: str, expected_kind: str) -> dict[str, Any] | None:
    payload, expired = _decode_payload_result(token, expected_kind)
    return None if expired else payload


def decode_token(token: str, expected_kind: str) -> int | None:
    """Return the user id, or None when invalid/expired/wrong kind."""
    payload = _decode_payload(token, expected_kind)
    if payload is None:
        return None
    try:
        return int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None


def decode_access_claims_result(token: str) -> AccessDecodeResult:
    """Verified access identity plus its server-checked revocation version.

    ``ver`` is mandatory so a malformed/legacy token cannot silently opt out
    of the server-side password-change and membership revocation check.
    """
    payload, expired = _decode_payload_result(token, "access")
    if payload is None:
        return AccessDecodeResult(claims=None, expired=False)
    try:
        user_id = int(payload["sub"])
        token_version = payload["ver"]
    except (KeyError, TypeError, ValueError):
        return AccessDecodeResult(claims=None, expired=False)
    if isinstance(token_version, bool) or not isinstance(token_version, int) or token_version < 0:
        return AccessDecodeResult(claims=None, expired=False)
    if expired:
        return AccessDecodeResult(claims=None, expired=True)
    return AccessDecodeResult(
        claims=AccessClaims(user_id=user_id, token_version=token_version),
        expired=False,
    )


def decode_access_claims(token: str) -> AccessClaims | None:
    """Compatibility projection for callers that need only live claims."""
    return decode_access_claims_result(token).claims


def decode_refresh_claims(token: str) -> RefreshClaims | None:
    """Refresh-token user id + jti + expiry, or None when invalid/expired.

    The shared decoder rejects expired tokens; expires_at comes back for the
    session row's expiry cross-check.
    """
    payload = _decode_payload(token, "refresh")
    if payload is None:
        return None
    try:
        user_id = int(payload["sub"])
        jti = payload["jti"]
        family_id = payload.get("fid")
        expires_at = datetime.fromtimestamp(float(payload["exp"]), UTC).replace(tzinfo=None)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None
    if not isinstance(jti, str) or (
        family_id is not None and (not isinstance(family_id, str) or not 1 <= len(family_id) <= 64)
    ):
        return None
    return RefreshClaims(
        user_id=user_id,
        jti=jti,
        family_id=family_id,
        expires_at=expires_at,
    )
