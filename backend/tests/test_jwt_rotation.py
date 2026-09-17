"""Focused zero-downtime JWT signing-key rotation regressions."""

import base64
import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

from app import security
from app.core.config import MAX_PREVIOUS_JWT_PUBLIC_KEYS, Settings, get_settings
from app.security import decode_access_claims_result, issue_access_token, validate_jwt_keypair


def _uid(claims: object) -> int:
    from app.security import AccessClaims

    assert isinstance(claims, AccessClaims)
    return claims.user_id



@pytest.fixture(autouse=True)
def _reset_keyring() -> Iterator[None]:
    """Keep environment/keyring changes isolated from the rest of the suite."""
    get_settings.cache_clear()
    security._key_cache.clear()
    security._jwt_keyring = None
    yield
    security._key_cache.clear()
    security._jwt_keyring = None
    get_settings.cache_clear()


def _write_pair(directory: Path, name: str, *, key_size: int = 2048) -> tuple[Path, Path]:
    private_path = directory / f"{name}-private.pem"
    public_path = directory / f"{name}-public.pem"
    key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    private_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_path, public_path


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    private_path: Path,
    public_path: Path,
    previous: list[Path] | None = None,
) -> None:
    monkeypatch.setenv("GOATFARM_JWT_PRIVATE_KEY_PATH", str(private_path))
    monkeypatch.setenv("GOATFARM_JWT_PUBLIC_KEY_PATH", str(public_path))
    monkeypatch.setenv(
        "GOATFARM_JWT_PREVIOUS_PUBLIC_KEY_PATHS",
        json.dumps([str(path) for path in previous or []]),
    )
    get_settings.cache_clear()
    security._key_cache.clear()
    security._jwt_keyring = None
    validate_jwt_keypair()


def _legacy_token(private_path: Path, subject: int = 42, *, kid: object = ...) -> str:
    now = datetime.now(UTC)
    headers = None if kid is ... else {"kid": kid}
    return jwt.encode(
        {
            "sub": str(subject),
            "kind": "access",
            "jti": "legacy-jti",
            "iat": now,
            "exp": now + timedelta(minutes=10),
            "iss": get_settings().jwt_issuer,
            "aud": get_settings().jwt_audience,
            "ver": 0,
        },
        private_path.read_text(),
        algorithm="RS256",
        headers=headers,
    )


def _expected_kid(public_path: Path) -> str:
    key = serialization.load_pem_public_key(public_path.read_bytes())
    assert isinstance(key, rsa.RSAPublicKey)
    der = key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.urlsafe_b64encode(hashlib.sha256(der).digest()).rstrip(b"=").decode("ascii")


def test_issued_tokens_have_deterministic_active_key_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_path, public_path = _write_pair(tmp_path, "active")
    _configure(monkeypatch, private_path, public_path)

    first = jwt.get_unverified_header(issue_access_token(1))["kid"]
    second = jwt.get_unverified_header(issue_access_token(2))["kid"]
    assert first == second == _expected_kid(public_path)


def test_rotation_accepts_active_previous_and_legacy_no_kid_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    old_private, old_public = _write_pair(tmp_path, "old")
    new_private, new_public = _write_pair(tmp_path, "new")

    _configure(monkeypatch, old_private, old_public)
    old_keyed = issue_access_token(41)
    old_legacy = _legacy_token(old_private, 42)

    _configure(monkeypatch, new_private, new_public, [old_public])
    new_keyed = issue_access_token(43)
    assert jwt.get_unverified_header(new_keyed)["kid"] == _expected_kid(new_public)
    assert _uid(decode_access_claims_result(new_keyed).claims) == 43
    assert _uid(decode_access_claims_result(old_keyed).claims) == 41
    assert _uid(decode_access_claims_result(old_legacy).claims) == 42


def test_known_kid_uses_only_that_key_and_retirement_rejects_old_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    old_private, old_public = _write_pair(tmp_path, "old")
    new_private, new_public = _write_pair(tmp_path, "new")
    _configure(monkeypatch, new_private, new_public, [old_public])

    # A supplied but unknown kid must not fall back across the legacy keyring,
    # even when a listed previous key signed the token.
    unknown_kid = _legacy_token(old_private, 44, kid="not-a-configured-key")
    assert decode_access_claims_result(unknown_kid).claims is None

    old_keyed = jwt.encode(
        jwt.decode(_legacy_token(old_private, 45), options={"verify_signature": False}),
        old_private.read_text(),
        algorithm="RS256",
        headers={"kid": _expected_kid(old_public)},
    )
    old_legacy = _legacy_token(old_private, 46)
    assert _uid(decode_access_claims_result(old_keyed).claims) == 45
    assert _uid(decode_access_claims_result(old_legacy).claims) == 46

    # Removing the old public key after the overlap retires both keyed and
    # pre-kid tokens signed by it without changing the active pair.
    _configure(monkeypatch, new_private, new_public)
    assert decode_access_claims_result(old_keyed).claims is None
    assert decode_access_claims_result(old_legacy).claims is None


def test_rotation_list_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings(
            jwt_previous_public_key_paths=[
                Path(f"/keys/previous-{index}.pem")
                for index in range(MAX_PREVIOUS_JWT_PUBLIC_KEYS + 1)
            ]
        )


def test_startup_rejects_duplicate_key_ids(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    private_path, public_path = _write_pair(tmp_path, "active")
    duplicate_path = tmp_path / "duplicate-public.pem"
    duplicate_path.write_bytes(public_path.read_bytes())

    with pytest.raises(RuntimeError, match="duplicate JWT key id"):
        _configure(monkeypatch, private_path, public_path, [duplicate_path])


def test_startup_rejects_weak_previous_rsa_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_path, public_path = _write_pair(tmp_path, "active")
    _weak_private, weak_public = _write_pair(tmp_path, "weak", key_size=1024)

    with pytest.raises(RuntimeError, match="at least 2048 bits"):
        _configure(monkeypatch, private_path, public_path, [weak_public])
