"""Mutation-campaign gap tests for the dev JWT keypair bootstrap (2026-09-30).

The surviving mutants here were all in unasserted filesystem/security
hygiene: the generated key's size and exponent, the public-key and keygen
lock file permission bits, the legacy key-directory permission repair
arithmetic, and the half-published-pair completeness check.
"""

import logging
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import get_settings


@pytest.fixture
def tmp_jwt_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    """Point the JWT settings at a fresh keypair location; restore both the
    settings cache and the in-memory key cache afterwards."""
    from app import security

    key_dir = tmp_path / "keys"
    priv = key_dir / "jwt_private.pem"
    pub = key_dir / "jwt_public.pem"
    monkeypatch.setenv("GOATFARM_JWT_PRIVATE_KEY_PATH", str(priv))
    monkeypatch.setenv("GOATFARM_JWT_PUBLIC_KEY_PATH", str(pub))
    get_settings.cache_clear()
    security._key_cache.clear()
    yield priv, pub
    security._key_cache.clear()
    get_settings.cache_clear()


def _load_private(path: Path) -> rsa.RSAPrivateKey:
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    assert isinstance(key, rsa.RSAPrivateKey)
    return key


def _load_public(path: Path) -> rsa.RSAPublicKey:
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_public_key(path.read_bytes())
    assert isinstance(key, rsa.RSAPublicKey)
    return key


def test_generated_keypair_shape_and_modes(tmp_jwt_keys: tuple[Path, Path]) -> None:
    """First boot generates an RSA-2048 / e=65537 pair with exact file modes."""
    priv, pub = tmp_jwt_keys
    from app import security

    security._ensure_keypair()

    private_key = _load_private(priv)
    assert isinstance(private_key, rsa.RSAPrivateKey)
    assert private_key.key_size == 2048
    assert private_key.public_key().public_numbers().e == 65537
    # Private key 0600, public key 0600's readable sibling 0644, lock 0600.
    assert stat.S_IMODE(priv.stat().st_mode) == 0o600
    assert stat.S_IMODE(pub.stat().st_mode) == 0o644
    lock = priv.parent / ".jwt_keygen.lock"
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600


def test_legacy_keys_dir_repair_preserves_high_bits_and_adds_owner_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The legacy BACKEND_DIR/keys repair keeps setuid/setgid/sticky bits and
    forces owner-only rwx: (mode | 0o700) & ~0o077 — pinned on a sticky-only
    directory, where every arithmetic drift shows up in the result."""
    from app import security

    key_dir = tmp_path / "keys"
    key_dir.mkdir()
    key_dir.chmod(0o1000)  # sticky bit only
    monkeypatch.setattr(security, "BACKEND_DIR", tmp_path)
    monkeypatch.setenv("GOATFARM_JWT_PRIVATE_KEY_PATH", str(key_dir / "jwt_private.pem"))
    monkeypatch.setenv("GOATFARM_JWT_PUBLIC_KEY_PATH", str(key_dir / "jwt_public.pem"))
    get_settings.cache_clear()
    security._key_cache.clear()
    try:
        security._ensure_keypair()
        assert stat.S_IMODE(key_dir.stat().st_mode) == 0o1700
    finally:
        security._key_cache.clear()
        get_settings.cache_clear()


def test_configured_shared_parent_is_never_chmodded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A configured parent outside BACKEND_DIR/keys is left exactly as found."""
    from app import security

    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o755)
    monkeypatch.setattr(security, "BACKEND_DIR", tmp_path / "elsewhere")
    monkeypatch.setenv("GOATFARM_JWT_PRIVATE_KEY_PATH", str(shared / "jwt_private.pem"))
    monkeypatch.setenv("GOATFARM_JWT_PUBLIC_KEY_PATH", str(shared / "jwt_public.pem"))
    get_settings.cache_clear()
    security._key_cache.clear()
    try:
        security._ensure_keypair()
        assert stat.S_IMODE(shared.stat().st_mode) == 0o755
    finally:
        security._key_cache.clear()
        get_settings.cache_clear()


def test_half_published_pair_is_completed_not_repaired_warning(
    tmp_jwt_keys: tuple[Path, Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A lone private file (app-managed marker present) is NOT a 'complete'
    pair: the completeness check is `priv.exists() and pub.exists()`, so a
    half-published generation regenerates both sides without the torn-pair
    warning — that warning is reserved for both-present-but-mismatched."""
    from app import security

    priv, pub = tmp_jwt_keys
    priv.parent.mkdir(parents=True)
    other_priv = priv.parent / "other-priv.pem"
    security._generate_keypair(other_priv, pub.parent / "unused-pub.pem")
    # Publish ONLY the private side, then leave the app-managed lock marker.
    priv.write_bytes(other_priv.read_bytes())
    (priv.parent / ".jwt_keygen.lock").touch(mode=0o600)
    assert not pub.exists()

    with caplog.at_level(logging.WARNING, logger="app.security"):
        security._ensure_keypair()

    assert "Repairing torn application-managed development JWT keypair" not in caplog.text
    # Both sides were regenerated as one consistent pair.
    assert pub.exists()
    private_key = _load_private(priv)
    public_key = _load_public(pub)
    assert private_key.public_key().public_numbers() == public_key.public_numbers()


def test_torn_pair_repair_warns(
    tmp_jwt_keys: tuple[Path, Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both files present but mismatched IS a torn publication: warn + repair."""
    from app import security

    priv, pub = tmp_jwt_keys
    priv.parent.mkdir(parents=True)
    security._generate_keypair(priv, pub.parent / "true-pub.pem")
    security._generate_keypair(priv.parent / "other-priv.pem", pub)
    (priv.parent / ".jwt_keygen.lock").touch(mode=0o600)

    with caplog.at_level(logging.WARNING, logger="app.security"):
        security._ensure_keypair()

    assert "Repairing torn application-managed development JWT keypair" in caplog.text
