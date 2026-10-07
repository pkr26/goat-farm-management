"""The native sortable tag helper accepts its literal fifty-character cap."""

import secrets

import pytest

from app.services.purchases import _purchase_batch_tag


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Typed integer/string tag inputs require no database connection."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """The helper's arbitrary string nonce contract has no storage writes."""


@pytest.mark.parametrize(("batch_id", "length"), [(12, 49), (123, 50), (1234, 51)])
def test_native_purchase_tag_accepts_fifty_characters_and_rejects_above(
    batch_id: int, length: int
) -> None:
    # A real forty-character cryptographic nonce is a valid string argument.
    # The current API chooses a shorter nonce, but this typed native helper
    # declares no twelve-character precondition. Do not change API entropy.
    nonce = secrets.token_hex(20)
    expected = f"B{batch_id}-{nonce}-0001"
    assert len(expected) == length
    if length == 51:
        with pytest.raises(ValueError, match="exceeds 50 characters"):
            _purchase_batch_tag(batch_id, nonce, 1)
    else:
        try:
            tag = _purchase_batch_tag(batch_id, nonce, 1)
        except ValueError as error:
            pytest.fail(f"native tag at its valid length cap must succeed: {error!r}")
        assert tag == expected
