"""A direct browser POST admits one byte and rejects a zero-byte allowance."""

import base64
import json

import pytest

from app.services.screening.s3 import ScreeningStorage, ScreeningStorageError

from .test_screening import _cycle_settings


def test_one_byte_object_allowance_produces_a_usable_signed_browser_policy() -> None:
    storage = ScreeningStorage(_cycle_settings())
    try:
        signed = storage.presign_post(
            "raw/1/one-byte.jpg", content_type="image/jpeg", upload_token="o" * 32, max_bytes=1
        )
    except (ValueError, ScreeningStorageError) as exc:
        pytest.fail(f"The positive native upload allowance must admit a one-byte object: {exc}")
    policy = json.loads(base64.b64decode(signed.fields["policy"]))
    assert ["content-length-range", 1, 65_537] in policy["conditions"]
    assert {"Content-Type": "image/jpeg"} in policy["conditions"]
    assert {"x-amz-meta-screening-token": "o" * 32} in policy["conditions"]


def test_zero_byte_object_allowance_cannot_mint_a_browser_upload_form() -> None:
    storage = ScreeningStorage(_cycle_settings())
    try:
        storage.presign_post(
            "raw/1/no-allowance.jpg", content_type="image/jpeg", upload_token="z" * 32, max_bytes=0
        )
    except ValueError as exc:
        assert str(exc) == "max_bytes must be positive"
    else:
        pytest.fail("A browser upload form requires a positive object-byte allowance")
