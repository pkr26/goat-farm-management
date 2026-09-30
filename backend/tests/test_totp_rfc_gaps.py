"""Mutation-campaign gap tests for the TOTP/HOTP core (2026-09-30).

The surviving mutants all sat inside `_totp_code_for_step` and the
high-water floor of `verify_totp_code`: the counter byte-width, the dynamic
truncation offset mask/window, the 31-bit mask, base32 casefolding, and the
`floor_step` origin. RFC 4226 test vectors pin every one of them exactly.
"""

import base64
from datetime import UTC, datetime

from app.security import TOTP_DRIFT_STEPS, TOTP_STEP_SECONDS, _totp_code_for_step, verify_totp_code

RFC_SECRET = base64.b32encode(b"12345678901234567890").decode("ascii")
# RFC 4226 Appendix D, steps 0..9 (HMAC-SHA1, 6 digits).
RFC_4226_CODES = [
    "755224",
    "287082",
    "359152",
    "969429",
    "338314",
    "254676",
    "287922",
    "162583",
    "399871",
    "520489",
]


def test_totp_matches_rfc4226_vectors() -> None:
    for step, expected in enumerate(RFC_4226_CODES):
        assert _totp_code_for_step(RFC_SECRET, step) == expected, f"step {step}"


def test_totp_decodes_lowercase_base32() -> None:
    # Provisioned secrets are ASCII base32, but a user pasting a lowercased
    # backup must still authenticate (casefold=True).
    assert _totp_code_for_step(RFC_SECRET.lower(), 0) == "755224"


def test_verify_matches_current_step_and_high_water() -> None:
    # Step 0 is the first 30 seconds after the epoch.
    at_step0 = datetime(1970, 1, 1, 0, 0, 5, tzinfo=UTC)
    code0 = _totp_code_for_step(RFC_SECRET, 0)
    code1 = _totp_code_for_step(RFC_SECRET, 1)

    # No history: step 0 matches (a floor_step origin of 1 would lock out the
    # very first code of the epoch and, by extension, misalign every window).
    assert verify_totp_code(RFC_SECRET, code0, at=at_step0, last_used_step=None) == 0

    # Drift: the neighbouring step is accepted and returns the MATCHED step.
    assert verify_totp_code(RFC_SECRET, code1, at=at_step0, last_used_step=None) == 1

    # Replay protection: once step 1 is the high-water mark, that same code
    # is refused (floor = 2).
    assert verify_totp_code(RFC_SECRET, code1, at=at_step0, last_used_step=1) is None
    assert verify_totp_code(RFC_SECRET, code0, at=at_step0, last_used_step=1) is None


def test_verify_rejects_malformed_codes_without_error() -> None:
    at_step0 = datetime(1970, 1, 1, 0, 0, 5, tzinfo=UTC)
    for bad in ("", "12345", "1234567", "abcdef", "12 45 6Ⅵ", "١٢٣٤٥٦"):
        assert verify_totp_code(RFC_SECRET, bad, at=at_step0, last_used_step=None) is None


def test_totp_constants() -> None:
    # 30 s steps, 6 digits, ±1 step drift — the provisioning contract the
    # authenticator apps and the frontend countdown agree on.
    assert TOTP_STEP_SECONDS == 30
    assert (TOTP_DRIFT_STEPS, TOTP_DRIFT_STEPS) == (1, 1)
