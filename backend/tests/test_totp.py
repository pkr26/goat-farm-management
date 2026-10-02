"""TOTP second factor (HUM-1 follow-up, 2026-09-16): enrollment lifecycle,
login challenge flow, single-use/replay protections, throttling, and at-rest
encryption of the shared secret."""

import asyncio
import base64
import hashlib
import json
import logging
import runpy
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import func, select, update

from app import security
from app.api import auth as auth_api
from app.core.config import get_settings
from app.db import get_sessionmaker
from app.models import TotpRecoveryCode, User
from app.ratelimit import auth_limiter
from app.security import (
    TOTP_ENVELOPE_PREFIX,
    TOTP_LEGACY_AAD,
    TOTP_STEP_SECONDS,
    _totp_code_for_step,
    decrypt_totp_secret,
    decrypt_totp_secret_with_metadata,
    encrypt_totp_secret,
    generate_totp_secret_b32,
    issue_token,
    verify_totp_code,
)
from app.utils import utcnow

from .conftest import OWNER_PW, owner_with_farm, register

TEST_TOTP_KEY = "VFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFQ"
# Resolved at import (sync) time: ruff ASYNC240 forbids Path method calls
# inside async tests, and both rekey tests need this same absolute path.
_REKEY_SCRIPT_PATH = str(Path(__file__).resolve().parents[1] / "scripts" / "rekey_totp_secrets.py")

TEST_TOTP_PREVIOUS_KEY = "UFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFA"


def test_rekey_dry_run_with_outstanding_rows_is_not_a_cutover_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A counted raw row must never look like a successful JWT-cutover gate."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "rekey_totp_secrets.py"
    namespace = runpy.run_path(str(script), run_name="rekey_totp_test")
    totals = namespace["RekeyTotals"](scanned=3, rekeyed=2, unchanged=1)

    def fake_run(coroutine: Any) -> object:
        coroutine.close()
        return totals

    module_globals = namespace["main"].__globals__
    monkeypatch.setitem(
        module_globals, "_parse_args", lambda: SimpleNamespace(apply=False, batch_size=250)
    )
    monkeypatch.setitem(module_globals, "asyncio", SimpleNamespace(run=fake_run))

    assert namespace["main"]() == 2
    captured = capsys.readouterr()
    assert "rekeyed=2" in captured.out
    assert "Do not rotate or retire the active JWT signer" in captured.err


def test_rekey_totals_keep_unavailable_diagnostics_bounded() -> None:
    """A large broken population must not defeat the script's batch bound."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "rekey_totp_secrets.py"
    namespace = runpy.run_path(str(script), run_name="rekey_totp_totals_test")
    totals = namespace["RekeyTotals"]()
    preview_limit = namespace["UNAVAILABLE_PREVIEW_LIMIT"]

    for user_id in range(preview_limit + 7):
        totals.record_unavailable(user_id)

    assert totals.unavailable_count == preview_limit + 7
    assert totals.unavailable_preview_ids == list(range(preview_limit))


# ---------------------------------------------------------------------------
# Break-glass reset (lost authenticator AND lost recovery codes): the script
# the README runbook points at, exercised against the real database — the
# production path runs it through the one-shot `migrate` Compose service
# because the production stack has no `db` service and no psql binary.
# ---------------------------------------------------------------------------
_BREAKGLASS_SCRIPT = str(Path(__file__).resolve().parents[1] / "scripts" / "totp_breakglass.py")


def _load_breakglass() -> dict[str, Any]:
    return runpy.run_path(_BREAKGLASS_SCRIPT, run_name="totp_breakglass_test")


async def _breakglass_user(email: str) -> User:
    async with get_sessionmaker()() as db:
        return (await db.execute(select(User).where(User.email == email))).scalar_one()


async def test_breakglass_dry_run_reports_work_and_changes_nothing(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dry run that finds a second factor must exit nonzero (automation
    must never mistake a counted reset for a completed one) and leave every
    credential untouched."""
    script = _load_breakglass()
    headers = await register(client, "breakglass-dry@farm.in")
    await _enroll_and_activate(client, headers)
    before = await _breakglass_user("breakglass-dry@farm.in")

    rc = await script["_breakglass"]("breakglass-dry@farm.in", apply=False)

    assert rc == 2
    after = await _breakglass_user("breakglass-dry@farm.in")
    assert after.totp_state == before.totp_state == "ACTIVE"
    assert after.totp_secret_enc == before.totp_secret_enc
    assert after.token_version == before.token_version
    out = capsys.readouterr()
    assert "user_id=" in out.out
    assert "unused_recovery_codes=10" in out.out
    assert "--apply" in out.err
    login = await client.post(
        "/api/auth/login",
        json={"email": "breakglass-dry@farm.in", "password": OWNER_PW},
    )
    assert login.status_code == 200, login.text
    assert login.json()["mfa_token"]


async def test_breakglass_apply_drops_the_factor_and_signs_out_every_session(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    """--apply performs exactly the documented reset: factor gone, recovery
    codes wiped, token_version bumped — and a challenge token minted BEFORE
    the reset dies with it."""
    script = _load_breakglass()
    headers = await register(client, "breakglass-apply@farm.in")
    await _enroll_and_activate(client, headers)
    before = await _breakglass_user("breakglass-apply@farm.in")
    login = await client.post(
        "/api/auth/login",
        json={"email": "breakglass-apply@farm.in", "password": OWNER_PW},
    )
    stale_mfa_token = login.json()["mfa_token"]

    # Case-insensitive email match, as documented.
    rc = await script["_breakglass"]("BREAKGLASS-APPLY@farm.in", apply=True)

    assert rc == 0
    after = await _breakglass_user("breakglass-apply@farm.in")
    assert after.totp_state is None
    assert after.totp_secret_enc is None
    assert after.totp_last_step is None
    assert after.token_version == before.token_version + 1
    async with get_sessionmaker()() as db:
        remaining_codes = (
            await db.execute(
                select(func.count())
                .select_from(TotpRecoveryCode)
                .where(TotpRecoveryCode.user_id == after.id)
            )
        ).scalar_one()
    assert remaining_codes == 0
    out = capsys.readouterr()
    assert "recovery codes deleted=10" in out.out
    # The pre-reset challenge is refused (token_version moved under it)…
    stale = await client.post(
        "/api/auth/totp/challenge", json={"mfa_token": stale_mfa_token, "code": "000000"}
    )
    assert stale.status_code == 401, stale.text
    # …and a fresh password login needs no second factor any more.
    fresh = await client.post(
        "/api/auth/login",
        json={"email": "breakglass-apply@farm.in", "password": OWNER_PW},
    )
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["mfa_token"] is None
    assert fresh.json()["access_token"]


async def test_breakglass_refuses_unknown_and_tombstoned_accounts(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    """The script recovers exactly one live account: unknown emails and
    tombstoned accounts are refused with an explanation and no side effects.
    (Tombstones are created directly: the profile-scrubbing CHECK constraint
    means a deleted row can never still carry an ACTIVE second factor.)"""
    script = _load_breakglass()
    assert await script["_breakglass"]("nobody@farm.in", apply=True) == 2
    assert "No account matches" in capsys.readouterr().err

    async with get_sessionmaker()() as db:
        # Tombstone rows are profile-scrubbed by the schema itself
        # (ck_users_deleted_profile_scrubbed forces deleted-%@deleted.invalid),
        # so mirror the sanctioned test pattern from test_account_tombstones.
        db.add(
            User(
                email="deleted-breakglass-0001@deleted.invalid",
                password_hash="argon2-placeholder",
                deleted_at=utcnow(),
            )
        )
        await db.commit()

    assert await script["_breakglass"]("deleted-breakglass-0001@deleted.invalid", apply=True) == 2
    assert "No account matches" in capsys.readouterr().err


async def test_breakglass_without_a_second_factor_is_a_clean_success(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing to break: exit 0 with an explicit message, not an error."""
    script = _load_breakglass()
    await register(client, "breakglass-nofactor@farm.in")

    rc = await script["_breakglass"]("breakglass-nofactor@farm.in", apply=True)

    assert rc == 0
    assert "No second factor present" in capsys.readouterr().out


def test_breakglass_main_plumbs_arguments_and_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() passes the parsed email/--apply through to _breakglass and
    propagates its exit code (the rekey-style plumbing contract)."""
    script = _load_breakglass()
    seen: dict[str, Any] = {}

    async def fake_breakglass(email: str, *, apply: bool) -> int:
        seen.update(email=email, apply=apply)
        return 2

    monkeypatch.setitem(script["main"].__globals__, "_breakglass", fake_breakglass)

    assert script["main"](["--email", "owner@example.in", "--apply"]) == 2
    assert seen == {"email": "owner@example.in", "apply": True}

    with pytest.raises(SystemExit):
        script["_parse_args"]([])


@pytest.fixture(autouse=True)
def _fresh_totp_rate_limits() -> Iterator[None]:
    """Drop all auth-limiter state around every test in this module.

    The database truncates per test (RESTART IDENTITY → every test's first
    user is id 1 again) while the in-memory limiter survives the whole
    process, so a test that exhausts a TOTP scope for "user 1" would 429 the
    next test's enroll/confirm before it even starts — the throttle tests
    here are written to be self-contained, and this keeps them that way
    regardless of file order. Same clear() hook test_auth_extended uses."""
    auth_limiter.clear()
    yield
    auth_limiter.clear()


@pytest.fixture
def stable_totp_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Use a fresh stable-key Settings snapshot without leaking it to tests."""
    monkeypatch.setenv("GOATFARM_TOTP_ENCRYPTION_KEY", TEST_TOTP_KEY)
    monkeypatch.delenv("GOATFARM_TOTP_ENCRYPTION_PREVIOUS_KEYS", raising=False)
    get_settings.cache_clear()
    security._reset_totp_key_cache_for_tests()
    yield
    security._reset_totp_key_cache_for_tests()
    get_settings.cache_clear()


def _legacy_ciphertext(secret: str) -> bytes:
    nonce = b"\x00" * 12
    return nonce + AESGCM(security._legacy_totp_encryption_key()).encrypt(
        nonce, secret.encode("ascii"), TOTP_LEGACY_AAD
    )


def _current_code(secret_b32: str, *, drift: int = 0) -> tuple[str, int]:
    step = int(datetime.now(UTC).timestamp() // TOTP_STEP_SECONDS) + drift
    return _totp_code_for_step(secret_b32, step), step


async def _enroll_and_activate(
    client: httpx.AsyncClient, headers: dict, *, password: str = OWNER_PW
) -> tuple[str, list[str]]:
    enroll = await client.post(
        "/api/auth/totp/enroll",
        json={"current_password": password},
        headers=headers,
    )
    assert enroll.status_code == 200, enroll.text
    secret = enroll.json()["secret"]
    code, _step = _current_code(secret)
    confirm = await client.post("/api/auth/totp/confirm", json={"code": code}, headers=headers)
    # Activation mints the one-time recovery codes (ITEM 7).
    assert confirm.status_code == 200, confirm.text
    codes = confirm.json()["codes"]
    assert len(codes) == 10
    return secret, codes


async def test_totp_unit_engine_roundtrip_and_replay_rejection() -> None:
    secret = generate_totp_secret_b32()
    assert len(base64.b32decode(secret)) == 20
    code, step = _current_code(secret)
    assert verify_totp_code(secret, code, at=datetime.now(UTC), last_used_step=None) == step
    # A used step never re-validates (single-use codes).
    assert verify_totp_code(secret, code, at=datetime.now(UTC), last_used_step=step) is None
    # Malformed shapes never authenticate.
    for bad in ("12345", "1234567", "12345x", "123 45"):
        assert verify_totp_code(secret, bad, at=datetime.now(UTC), last_used_step=None) is None


def test_stable_totp_ciphertext_is_versioned_and_round_trips(stable_totp_key: None) -> None:
    secret = generate_totp_secret_b32()
    encrypted = encrypt_totp_secret(secret)

    assert encrypted.startswith(TOTP_ENVELOPE_PREFIX)
    decoded = decrypt_totp_secret_with_metadata(encrypted)
    assert decoded.secret == secret
    assert decoded.needs_rewrap is False


def test_legacy_totp_ciphertext_rewraps_after_stable_key_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-v2 row needs no old JWT private key beyond this migration window."""
    monkeypatch.delenv("GOATFARM_TOTP_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("GOATFARM_TOTP_ENCRYPTION_PREVIOUS_KEYS", raising=False)
    get_settings.cache_clear()
    security._reset_totp_key_cache_for_tests()
    secret = generate_totp_secret_b32()
    legacy = _legacy_ciphertext(secret)

    monkeypatch.setenv("GOATFARM_TOTP_ENCRYPTION_KEY", TEST_TOTP_KEY)
    get_settings.cache_clear()
    decoded = decrypt_totp_secret_with_metadata(legacy)
    assert decoded.secret == secret
    assert decoded.needs_rewrap is True

    rewrapped = encrypt_totp_secret(decoded.secret)
    assert rewrapped.startswith(TOTP_ENVELOPE_PREFIX)
    assert decrypt_totp_secret_with_metadata(rewrapped).needs_rewrap is False
    security._reset_totp_key_cache_for_tests()
    get_settings.cache_clear()


def test_previous_stable_totp_key_decrypts_then_marks_for_rewrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOATFARM_TOTP_ENCRYPTION_KEY", TEST_TOTP_KEY)
    monkeypatch.delenv("GOATFARM_TOTP_ENCRYPTION_PREVIOUS_KEYS", raising=False)
    get_settings.cache_clear()
    secret = generate_totp_secret_b32()
    old_ciphertext = encrypt_totp_secret(secret)

    monkeypatch.setenv("GOATFARM_TOTP_ENCRYPTION_KEY", TEST_TOTP_PREVIOUS_KEY)
    monkeypatch.setenv("GOATFARM_TOTP_ENCRYPTION_PREVIOUS_KEYS", json.dumps([TEST_TOTP_KEY]))
    get_settings.cache_clear()
    decoded = decrypt_totp_secret_with_metadata(old_ciphertext)
    assert decoded.secret == secret
    assert decoded.needs_rewrap is True
    security._reset_totp_key_cache_for_tests()
    get_settings.cache_clear()


async def test_totp_secret_is_encrypted_at_rest(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "totp-crypt@farm.in")
    secret, _codes = await _enroll_and_activate(client, headers)
    async with get_sessionmaker()() as db:
        row = (
            await db.execute(select(User).where(User.email == "totp-crypt@farm.in"))
        ).scalar_one()
        assert row.totp_state == "ACTIVE"
        stored = bytes(row.totp_secret_enc)
        assert secret.encode() not in stored  # never plaintext
        assert len(stored) > 12
        assert decrypt_totp_secret(stored) == secret
        # And the ciphertext is not merely a naive hash of the secret either
        # (the property the previous line's tautological self-comparison
        # intended to pin — 2026-09-17 audit L-30).
        assert stored != hashlib.sha256(secret.encode()).digest()


async def test_successful_challenge_lazily_rewraps_legacy_totp_ciphertext(
    client: httpx.AsyncClient,
    stable_totp_key: None,
) -> None:
    headers = await register(client, "totp-lazy-rekey@farm.in")
    secret, _codes = await _enroll_and_activate(client, headers)
    async with get_sessionmaker()() as db:
        row = (
            await db.execute(select(User).where(User.email == "totp-lazy-rekey@farm.in"))
        ).scalar_one()
        row.totp_secret_enc = _legacy_ciphertext(secret)
        await db.commit()

    login = await client.post(
        "/api/auth/login",
        json={"email": "totp-lazy-rekey@farm.in", "password": OWNER_PW},
    )
    assert login.status_code == 200, login.text
    code, _step = _current_code(secret, drift=1)
    challenge = await client.post(
        "/api/auth/totp/challenge",
        json={"mfa_token": login.json()["mfa_token"], "code": code},
    )
    assert challenge.status_code == 200, challenge.text

    async with get_sessionmaker()() as db:
        row = (
            await db.execute(select(User).where(User.email == "totp-lazy-rekey@farm.in"))
        ).scalar_one()
        assert bytes(row.totp_secret_enc).startswith(TOTP_ENVELOPE_PREFIX)
        assert decrypt_totp_secret_with_metadata(bytes(row.totp_secret_enc)).needs_rewrap is False


async def test_undecryptable_totp_secret_fails_closed_without_500(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "totp-unavailable@farm.in")
    await _enroll_and_activate(client, headers)
    async with get_sessionmaker()() as db:
        row = (
            await db.execute(select(User).where(User.email == "totp-unavailable@farm.in"))
        ).scalar_one()
        # Correct framing but an invalid AEAD tag exercises the operational
        # failure path rather than the request schema boundary.
        row.totp_secret_enc = b"\x00" * 12 + b"\x00" * 16
        await db.commit()

    login = await client.post(
        "/api/auth/login",
        json={"email": "totp-unavailable@farm.in", "password": OWNER_PW},
    )
    assert login.status_code == 200, login.text
    challenge = await client.post(
        "/api/auth/totp/challenge",
        json={"mfa_token": login.json()["mfa_token"], "code": "000000"},
    )
    assert challenge.status_code == 503
    assert challenge.json()["detail"] == (
        "Two-factor authentication is temporarily unavailable. Contact an administrator."
    )


async def test_full_totp_login_challenge_flow(client: httpx.AsyncClient) -> None:
    headers = await register(client, "totp-full@farm.in")
    secret, _codes = await _enroll_and_activate(client, headers)
    me = await client.get("/api/auth/me", headers=headers)
    assert me.json()["totp_state"] == "ACTIVE"

    login = await client.post(
        "/api/auth/login",
        json={"email": "totp-full@farm.in", "password": OWNER_PW},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["mfa_token"] and body["access_token"] is None and body["user"] is None

    # The confirm step already consumed the current code (codes are
    # single-use); the next step's code is inside the ±1 drift window.
    code, _step = _current_code(secret, drift=1)
    challenge = await client.post(
        "/api/auth/totp/challenge",
        json={"mfa_token": body["mfa_token"], "code": code},
    )
    assert challenge.status_code == 200, challenge.text
    tokens = challenge.json()
    assert tokens["access_token"] and tokens["user"]["email"] == "totp-full@farm.in"
    # The exchanged access token authenticates like any session.
    authed = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert authed.status_code == 200


async def test_pending_enrollment_does_not_gate_login(client: httpx.AsyncClient) -> None:
    headers = await register(client, "totp-pending@farm.in")
    enroll = await client.post(
        "/api/auth/totp/enroll", json={"current_password": OWNER_PW}, headers=headers
    )
    assert enroll.status_code == 200
    login = await client.post(
        "/api/auth/login",
        json={"email": "totp-pending@farm.in", "password": OWNER_PW},
    )
    assert login.status_code == 200
    assert login.json()["access_token"]  # no challenge for unconfirmed enrollment


async def test_challenge_rejects_wrong_code_then_accepts_right(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "totp-wrong@farm.in")
    secret, _codes = await _enroll_and_activate(client, headers)
    login = await client.post(
        "/api/auth/login", json={"email": "totp-wrong@farm.in", "password": OWNER_PW}
    )
    mfa_token = login.json()["mfa_token"]

    wrong = await client.post(
        "/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": "000000"}
    )
    assert wrong.status_code == 401
    # The token stays usable until consumed by a SUCCESSFUL challenge. The
    # confirm step consumed the current code; use the next step's.
    code, _step = _current_code(secret, drift=1)
    right = await client.post(
        "/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": code}
    )
    assert right.status_code == 200, right.text


async def test_challenge_token_is_single_use(client: httpx.AsyncClient) -> None:
    headers = await register(client, "totp-replay@farm.in")
    secret, _codes = await _enroll_and_activate(client, headers)
    login = await client.post(
        "/api/auth/login", json={"email": "totp-replay@farm.in", "password": OWNER_PW}
    )
    mfa_token = login.json()["mfa_token"]
    code, _step = _current_code(secret, drift=1)
    first = await client.post(
        "/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": code}
    )
    assert first.status_code == 200
    replay = await client.post(
        "/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": code}
    )
    assert replay.status_code == 401


async def test_challenge_replay_guard_is_durable_across_a_restart(
    client: httpx.AsyncClient,
) -> None:
    """The consumed-jti marker lives in the database, not process memory.

    Pre-fix (2026-10-01 audit, 01-3) a restart inside the 300 s challenge
    TTL wiped the in-memory cache and permitted exactly one replay of an
    already-consumed challenge. With the consumed_mfa_challenges claim there
    is no process state to lose: the row IS the restart. Decided 2026-10-02
    per RFC 6238's verifier-must-detect-replay requirement.
    """
    from sqlalchemy import select

    from app.db import get_sessionmaker
    from app.models import ConsumedMfaChallenge

    headers = await register(client, "totp-durable@farm.in")
    secret, _codes = await _enroll_and_activate(client, headers)
    login = await client.post(
        "/api/auth/login", json={"email": "totp-durable@farm.in", "password": OWNER_PW}
    )
    mfa_token = login.json()["mfa_token"]
    code, _step = _current_code(secret, drift=1)
    ok = await client.post("/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": code})
    assert ok.status_code == 200

    # The single-use claim is committed to storage — the restart equivalent.
    async with get_sessionmaker()() as db:
        rows = list((await db.execute(select(ConsumedMfaChallenge))).scalars())
    assert len(rows) == 1
    # Naive-UTC house convention for stored datetimes.
    assert rows[0].expires_at > datetime.now(UTC).replace(tzinfo=None)

    # ...and that stored claim, not any process state, refuses the replay.
    replay = await client.post(
        "/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": code}
    )
    assert replay.status_code == 401


async def test_concurrent_challenge_exchange_succeeds_exactly_once(
    client: httpx.AsyncClient,
) -> None:
    """Two simultaneous exchanges of one challenge token: one winner.

    Both racers present DIFFERENT still-valid codes (the step before and
    after the one enrollment's confirm consumed — the ±1 drift window), so
    code verification passes for both and the consumed_mfa_challenges unique
    jti is the only arbiter: INSERT ... ON CONFLICT DO NOTHING lets exactly
    one transaction claim the token; the loser blocks on the jti until the
    winner commits, then sees the conflict and takes the generic 401.
    """
    from .test_concurrency import second_client

    headers = await register(client, "totp-race@farm.in")
    secret, _codes = await _enroll_and_activate(client, headers)
    login = await client.post(
        "/api/auth/login", json={"email": "totp-race@farm.in", "password": OWNER_PW}
    )
    mfa_token = login.json()["mfa_token"]
    # Confirm consumed the current step N; N-1 and N+1 are both inside the
    # ±TOTP_DRIFT_STEPS window and neither has been used.
    earlier, _s1 = _current_code(secret, drift=-1)
    later, _s2 = _current_code(secret, drift=1)
    async with second_client() as other:
        # Warm the cold client (app/router/connection init) with a cheap
        # unauthenticated probe so both racers reach the jti claim together.
        await other.get("/api/auth/worker-roster", params={"farm_id": 1})
        first, second = await asyncio.gather(
            client.post("/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": earlier}),
            other.post("/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": later}),
        )
    assert sorted([first.status_code, second.status_code]) == [200, 401], (
        first.status_code,
        second.status_code,
    )


async def test_used_code_does_not_authenticate_a_second_challenge(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "totp-codesingle@farm.in")
    secret, _codes = await _enroll_and_activate(client, headers)
    login1 = await client.post(
        "/api/auth/login", json={"email": "totp-codesingle@farm.in", "password": OWNER_PW}
    )
    code, _step = _current_code(secret, drift=1)
    ok = await client.post(
        "/api/auth/totp/challenge",
        json={"mfa_token": login1.json()["mfa_token"], "code": code},
    )
    assert ok.status_code == 200
    login2 = await client.post(
        "/api/auth/login", json={"email": "totp-codesingle@farm.in", "password": OWNER_PW}
    )
    again = await client.post(
        "/api/auth/totp/challenge",
        json={"mfa_token": login2.json()["mfa_token"], "code": code},
    )
    assert again.status_code == 401


async def test_challenge_dies_with_a_password_change(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "totp-ver@farm.in")
    secret, _codes = await _enroll_and_activate(client, headers)
    login = await client.post(
        "/api/auth/login", json={"email": "totp-ver@farm.in", "password": OWNER_PW}
    )
    mfa_token = login.json()["mfa_token"]
    changed = await client.post(
        "/api/auth/change-password",
        json={"current_password": OWNER_PW, "new_password": "New-Pass-12345!"},
        headers=headers,
    )
    assert changed.status_code == 200
    code, _step = _current_code(secret)
    stale = await client.post(
        "/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": code}
    )
    assert stale.status_code == 401


async def test_enroll_requires_the_current_password(client: httpx.AsyncClient) -> None:
    headers = await register(client, "totp-pw@farm.in")
    bad = await client.post(
        "/api/auth/totp/enroll", json={"current_password": "wrong-password"}, headers=headers
    )
    assert bad.status_code == 400
    ok = await client.post(
        "/api/auth/totp/enroll", json={"current_password": OWNER_PW}, headers=headers
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["secret"] and body["otpauth_uri"].startswith("otpauth://totp/Herdly:")


async def test_enroll_over_an_active_enrollment_is_refused(
    client: httpx.AsyncClient,
) -> None:
    """2026-09-17 re-audit: enroll used to silently replace an ACTIVE secret
    with password-only proof — a phished password could retire a second
    factor the thief could not satisfy. It must now refuse (409) and leave
    the ACTIVE enrollment fully intact."""
    headers = await register(client, "totp-nooverwrite@farm.in")
    secret, _codes = await _enroll_and_activate(client, headers)
    resp = await client.post(
        "/api/auth/totp/enroll", json={"current_password": OWNER_PW}, headers=headers
    )
    assert resp.status_code == 409, resp.text
    assert "secret" not in resp.json()  # no replacement secret was handed out
    # The ACTIVE enrollment is untouched: its secret still gates login (the
    # confirm step consumed the current code, so use the next step's).
    login = await client.post(
        "/api/auth/login",
        json={"email": "totp-nooverwrite@farm.in", "password": OWNER_PW},
    )
    assert login.status_code == 200
    code, _step = _current_code(secret, drift=1)
    challenge = await client.post(
        "/api/auth/totp/challenge",
        json={"mfa_token": login.json()["mfa_token"], "code": code},
    )
    assert challenge.status_code == 200, challenge.text


async def test_disable_requires_password_and_code_when_active(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "totp-off@farm.in")
    secret, _codes = await _enroll_and_activate(client, headers)
    # Confirm consumed the current step's code; disable needs a fresh one.
    code, _step = _current_code(secret, drift=1)
    bad_code = await client.post(
        "/api/auth/totp/disable",
        json={"current_password": OWNER_PW, "code": "000000"},
        headers=headers,
    )
    assert bad_code.status_code == 400
    bad_pw = await client.post(
        "/api/auth/totp/disable",
        json={"current_password": "wrong-password", "code": code},
        headers=headers,
    )
    assert bad_pw.status_code == 400
    ok = await client.post(
        "/api/auth/totp/disable",
        json={"current_password": OWNER_PW, "code": code},
        headers=headers,
    )
    assert ok.status_code == 204
    login = await client.post(
        "/api/auth/login", json={"email": "totp-off@farm.in", "password": OWNER_PW}
    )
    assert login.json()["access_token"]  # no second factor anymore


async def test_disable_pending_enrollment_needs_only_the_password(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "totp-offpending@farm.in")
    enroll = await client.post(
        "/api/auth/totp/enroll", json={"current_password": OWNER_PW}, headers=headers
    )
    assert enroll.status_code == 200
    ok = await client.post(
        "/api/auth/totp/disable",
        json={"current_password": OWNER_PW, "code": "123456"},  # code ignored pre-activation
        headers=headers,
    )
    assert ok.status_code == 204
    me = await client.get("/api/auth/me", headers=headers)
    assert me.json()["totp_state"] is None


async def test_confirm_without_enrollment_is_a_conflict(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "totp-noconfirm@farm.in")
    resp = await client.post("/api/auth/totp/confirm", json={"code": "123456"}, headers=headers)
    assert resp.status_code == 409


async def test_challenge_brute_force_is_throttled(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "auth_rate_limit_enabled", True)
    headers = await register(client, "totp-throttle@farm.in")
    secret, _codes = await _enroll_and_activate(client, headers)
    login = await client.post(
        "/api/auth/login", json={"email": "totp-throttle@farm.in", "password": OWNER_PW}
    )
    mfa_token = login.json()["mfa_token"]
    statuses = []
    for _ in range(6):
        resp = await client.post(
            "/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": "000000"}
        )
        statuses.append(resp.status_code)
        if resp.status_code == 429:
            break
    assert statuses[-1] == 429, statuses
    # The correct code is now locked out too until the window slides.
    code, _step = _current_code(secret)
    locked = await client.post(
        "/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": code}
    )
    assert locked.status_code == 429


async def test_challenge_requires_a_json_content_type(client: httpx.AsyncClient) -> None:
    # AUTH-2 parity (2026-09-28 audit, S5): the challenge mints the same
    # refresh cookie as its login-flow siblings, so a non-JSON body is refused
    # by the explicit content-type guard before any token is decoded.
    refused = await client.post(
        "/api/auth/totp/challenge",
        content=json.dumps({"mfa_token": "any", "code": "000000"}),
        headers={"Content-Type": "text/plain"},
    )
    assert refused.status_code == 415, refused.text
    assert "application/json" in refused.json()["detail"]


async def test_challenge_garbage_mfa_tokens_are_throttled_before_verification(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A garbage mfa_token costs one RS256 verify; the pre-verification budget
    (mirror of the refresh cookie's) refuses a repeat of the SAME failed token
    before PyJWT runs again, emits a security event per classified-invalid
    token, and never judges a valid challenge by a neighbour's garbage."""
    monkeypatch.setattr(get_settings(), "auth_rate_limit_enabled", True)
    headers = await register(client, "totp-preverify@farm.in")
    secret, _codes = await _enroll_and_activate(client, headers)
    login = await client.post(
        "/api/auth/login", json={"email": "totp-preverify@farm.in", "password": OWNER_PW}
    )
    valid_token = login.json()["mfa_token"]
    limit = get_settings().auth_rate_limit_max_attempts
    window = get_settings().auth_rate_limit_window_seconds

    real_decode = auth_api._decode_payload_result
    decode_calls = 0

    def counted_decode(token: str, expected_kind: str) -> object:
        nonlocal decode_calls
        decode_calls += 1
        return real_decode(token, expected_kind)

    monkeypatch.setattr(auth_api, "_decode_payload_result", counted_decode)
    with caplog.at_level(logging.INFO, logger="goatfarm.audit"):
        for _ in range(limit):
            resp = await client.post(
                "/api/auth/totp/challenge", json={"mfa_token": "garbage", "code": "000000"}
            )
            assert resp.status_code == 401
        assert decode_calls == limit
        # Each failed verification is an alertable event, not a silent 401.
        failures = [
            record.getMessage()
            for record in caplog.records
            if "event='auth.totp.challenge_failed'" in record.getMessage()
        ]
        assert len([m for m in failures if "failed verification" in m]) == limit, failures
        # Re-presenting the SAME garbage is refused before another RSA verify.
        repeat = await client.post(
            "/api/auth/totp/challenge", json={"mfa_token": "garbage", "code": "000000"}
        )
    assert repeat.status_code == 429
    assert repeat.headers["Retry-After"] == str(window)
    assert "Too many" in repeat.json()["detail"]
    assert decode_calls == limit, "a known-bad challenge token must not be verified again"
    assert auth_limiter.is_blocked(
        auth_api.TOTP_CHALLENGE_PREVERIFY_SCOPE,
        auth_api._mfa_token_key("garbage"),
        limit,
        window,
    )

    # ...but a different token from the same address is judged on its own
    # merits: the valid challenge still reaches PyJWT and completes the login.
    code, _step = _current_code(secret, drift=1)
    challenge = await client.post(
        "/api/auth/totp/challenge", json={"mfa_token": valid_token, "code": code}
    )
    assert challenge.status_code == 200, challenge.text


async def test_challenge_expired_but_genuine_mfa_token_is_never_charged(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A signature-valid but expired challenge is a returning client, not
    probing: it keeps getting a plain 401 and never fills the pre-verification
    budget — the ``_refresh_token_expired_but_genuine`` exemption mirrored on
    the mfa path."""
    monkeypatch.setattr(get_settings(), "auth_rate_limit_enabled", True)
    headers = await register(client, "totp-expired@farm.in")
    await _enroll_and_activate(client, headers)
    me = await client.get("/api/auth/me", headers=headers)
    limit = get_settings().auth_rate_limit_max_attempts
    window = get_settings().auth_rate_limit_window_seconds

    # Past the decoder's 60s leeway, so it classifies as expired-but-genuine.
    expired_token = issue_token(me.json()["id"], "mfa", -120, extra_claims={"ver": 0})
    for _ in range(limit + 2):
        resp = await client.post(
            "/api/auth/totp/challenge", json={"mfa_token": expired_token, "code": "000000"}
        )
        assert resp.status_code == 401, resp.text
        assert resp.json()["detail"] == "Invalid or expired challenge."
    assert not auth_limiter.is_blocked(
        auth_api.TOTP_CHALLENGE_PREVERIFY_SCOPE,
        auth_api._mfa_token_key(expired_token),
        limit,
        window,
    )
    assert not auth_limiter.has_attempts(auth_api.TOTP_CHALLENGE_INVALID_SCOPE, "127.0.0.1")


async def test_confirm_brute_force_is_throttled(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-09-17 re-audit: /totp/confirm must carry the same guess budget as
    the login challenge — a stolen access token gets no unthrottled 6-digit
    oracle here either."""
    monkeypatch.setattr(get_settings(), "auth_rate_limit_enabled", True)
    headers = await register(client, "totp-confirmthrottle@farm.in")
    enroll = await client.post(
        "/api/auth/totp/enroll", json={"current_password": OWNER_PW}, headers=headers
    )
    assert enroll.status_code == 200
    secret = enroll.json()["secret"]
    statuses = []
    for _ in range(6):
        resp = await client.post("/api/auth/totp/confirm", json={"code": "000000"}, headers=headers)
        statuses.append(resp.status_code)
        if resp.status_code == 429:
            break
    assert statuses[-1] == 429, statuses
    # The correct code is locked out too until the window slides.
    code, _step = _current_code(secret, drift=1)
    locked = await client.post("/api/auth/totp/confirm", json={"code": code}, headers=headers)
    assert locked.status_code == 429


async def test_disable_wrong_code_is_throttled(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-09-17 re-audit: /totp/disable's ACTIVE-state code check recorded
    nothing on a wrong code — an unthrottled 6-digit oracle for a stolen
    access token. It now carries the same guess budget as confirm, under its
    own scope so the two endpoints cannot lock each other out."""
    monkeypatch.setattr(get_settings(), "auth_rate_limit_enabled", True)
    from app import ratelimit

    ratelimit.drain_throttle_rejections()  # clear residues from other tests
    headers = await register(client, "totp-disablethrottle@farm.in")
    secret, _codes = await _enroll_and_activate(client, headers)
    statuses = []
    for _ in range(6):
        resp = await client.post(
            "/api/auth/totp/disable",
            json={"current_password": OWNER_PW, "code": "000000"},
            headers=headers,
        )
        statuses.append(resp.status_code)
        if resp.status_code == 429:
            break
    assert statuses[-1] == 429, statuses
    # The 429 came from the CODE throttle (not the shared password-confirm
    # budget, whose composite ceiling is far higher and whose scope would
    # surface as "totp-enroll" here).
    assert ratelimit.drain_throttle_rejections().get("totp-disable", 0) >= 1
    # The correct code is locked out too until the window slides.
    code, _step = _current_code(secret, drift=1)
    locked = await client.post(
        "/api/auth/totp/disable",
        json={"current_password": OWNER_PW, "code": code},
        headers=headers,
    )
    assert locked.status_code == 429


async def test_password_confirmation_budget_clears_on_successful_totp_commits(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-09-29 audit, L3: _record_account_password_attempt documents
    "successful commits clear the counters", and change_password and
    delete_account honor it — but enroll/disable/recovery-regenerate never
    reset, so a legitimate user toggling 2FA inside one rate-limit window
    crawled toward the composite password-confirm ceiling on correct
    passwords and could hit an early 429. Every successful commit now resets
    both scopes exactly like its siblings."""
    monkeypatch.setattr(get_settings(), "auth_rate_limit_enabled", True)
    # Shrink the per-key budget so the regression (and the control below) is
    # reachable in a handful of calls instead of the default five.
    monkeypatch.setattr(get_settings(), "auth_rate_limit_max_attempts", 2)
    headers = await register(client, "totp-budgetclear@farm.in")

    # Re-rolling a PENDING enrollment three times in one window: three
    # successful password-confirmed commits against a budget of two —
    # without the success-side reset the third would already 429.
    for attempt in range(3):
        enroll = await client.post(
            "/api/auth/totp/enroll", json={"current_password": OWNER_PW}, headers=headers
        )
        assert enroll.status_code == 200, (attempt, enroll.text)

    # The same contract on the disable and regenerate paths: each commits
    # with a proven password and must leave the budget cleared behind it.
    secret, _codes = await _enroll_and_activate(client, headers)
    code, _step = _current_code(secret, drift=1)
    disabled = await client.post(
        "/api/auth/totp/disable",
        json={"current_password": OWNER_PW, "code": code},
        headers=headers,
    )
    assert disabled.status_code == 204, disabled.text
    secret2, _codes2 = await _enroll_and_activate(client, headers)
    code2, _step2 = _current_code(secret2, drift=1)
    regen = await client.post(
        "/api/auth/totp/recovery/regenerate",
        json={"current_password": OWNER_PW, "code": code2},
        headers=headers,
    )
    assert regen.status_code == 200, regen.text

    # Control — the reset is success-only: wrong passwords still charge, and
    # two failures at the shrunk budget lock the composite key out again.
    for _ in range(2):
        bad = await client.post(
            "/api/auth/totp/enroll",
            json={"current_password": "not-the-password"},
            headers=headers,
        )
        assert bad.status_code == 400, bad.text
    locked = await client.post(
        "/api/auth/totp/enroll", json={"current_password": OWNER_PW}, headers=headers
    )
    assert locked.status_code == 429, locked.status_code


async def test_non_ascii_digit_codes_are_rejected_without_a_500(
    client: httpx.AsyncClient,
) -> None:
    """2026-09-17 re-audit: non-ASCII digits ('٥' and friends) pass
    str.isdigit(), and hmac.compare_digest then raises TypeError on the
    non-ASCII string — a 500 from a mere malformed guess. The schema's
    ASCII-only pattern answers 422 at the API edge, and verify_totp_code now
    rejects non-ASCII digit strings on its own (defense-in-depth for any
    direct caller)."""
    headers = await register(client, "totp-nonascii@farm.in")
    secret, _codes = await _enroll_and_activate(client, headers)
    login = await client.post(
        "/api/auth/login",
        json={"email": "totp-nonascii@farm.in", "password": OWNER_PW},
    )
    assert login.status_code == 200
    resp = await client.post(
        "/api/auth/totp/challenge",
        json={"mfa_token": login.json()["mfa_token"], "code": "12345٥"},
    )
    assert resp.status_code == 422, resp.status_code  # clean rejection, never a 500
    # Unit level: no TypeError escapes, the comparator simply never runs.
    now = datetime.now(UTC)
    for bad in ("12345٥", "١٢٣٤٥٦", "1234٥6"):
        assert verify_totp_code(secret, bad, at=now, last_used_step=None) is None


async def test_wrong_totp_codes_do_not_inflate_the_429_summary(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-09-17 re-audit: the periodic 'auth rate-limit 429 summary' counts
    only actual throttle decisions. A wrong code is a 401 answer, not a 429,
    and must stay out of the summary; the blocked answer must appear."""
    monkeypatch.setattr(get_settings(), "auth_rate_limit_enabled", True)
    headers = await register(client, "totp-summary@farm.in")
    secret, _codes = await _enroll_and_activate(client, headers)
    login = await client.post(
        "/api/auth/login", json={"email": "totp-summary@farm.in", "password": OWNER_PW}
    )
    mfa_token = login.json()["mfa_token"]

    from app import ratelimit

    ratelimit.drain_throttle_rejections()  # clear residues from other tests
    wrong = await client.post(
        "/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": "000000"}
    )
    assert wrong.status_code == 401
    assert "totp-challenge" not in ratelimit.drain_throttle_rejections()

    for _ in range(6):
        resp = await client.post(
            "/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": "000000"}
        )
        if resp.status_code == 429:
            break
    else:
        pytest.fail("challenge brute force was never throttled")
    summary = ratelimit.drain_throttle_rejections()
    assert summary.get("totp-challenge", 0) >= 1
    # The still-usable code proves only the throttle (not the token) burned.
    code, _step = _current_code(secret, drift=1)
    exhausted = await client.post(
        "/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": code}
    )
    assert exhausted.status_code == 429


async def test_owner_with_totp_full_app_flow_still_works(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="totp-owner@farm.in")
    secret, _codes = await _enroll_and_activate(client, headers)
    login = await client.post(
        "/api/auth/login", json={"email": "totp-owner@farm.in", "password": OWNER_PW}
    )
    code, _step = _current_code(secret, drift=1)
    tokens = (
        await client.post(
            "/api/auth/totp/challenge",
            json={"mfa_token": login.json()["mfa_token"], "code": code},
        )
    ).json()
    farm_headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    farms = await client.get("/api/auth/farms", headers=farm_headers)
    assert farms.status_code == 200 and len(farms.json()) == 1


def _previous_key_ciphertext(secret: str) -> bytes:
    """A v2 envelope sealed under the predecessor key (needs_rewrap=True)."""
    from app.core.config import decode_totp_encryption_key

    nonce = b"\x01" * 12
    key = decode_totp_encryption_key(TEST_TOTP_PREVIOUS_KEY, setting_name="TEST_TOTP_PREVIOUS_KEY")
    return (
        TOTP_ENVELOPE_PREFIX
        + nonce
        + AESGCM(key).encrypt(nonce, secret.encode("ascii"), security.TOTP_ENVELOPE_AAD)
    )


async def test_rekey_script_apply_path_runs_against_the_real_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute _rekey() itself — keyset batches, FOR UPDATE, apply commits.

    The mocked-exit-code tests cannot see a regression in transaction
    scoping, batching, or the apply/dry-run rollback (2026-09-18 audit
    M-4): this test inserts one row of each vintage into the real test
    database and runs the script's own coroutine end to end, including a
    one-row batch size so the apply pass spans multiple locked
    transactions exactly as an interrupted-and-resumed run would. The
    app-level key caches are pointed at the same keyring the script uses,
    because decrypt/encrypt helpers read the app Settings, not the script's
    get_settings parameter.
    """
    from app.core.config import Settings

    namespace = runpy.run_path(_REKEY_SCRIPT_PATH, run_name="rekey_apply_real_test")
    # runpy returns a *copy* of the execution globals; the functions keep
    # the original dict, so patches must target __globals__ directly.
    rekey_globals = namespace["_rekey"].__globals__

    test_settings = Settings(
        _env_file=None,
        totp_encryption_key=TEST_TOTP_KEY,
        totp_encryption_previous_keys=[TEST_TOTP_PREVIOUS_KEY],
    )
    monkeypatch.setitem(rekey_globals, "get_settings", lambda: test_settings)
    # The crypto helpers resolve the keyring through the app's own cached
    # Settings: configure the process identically for the test's duration.
    monkeypatch.setenv("GOATFARM_TOTP_ENCRYPTION_KEY", TEST_TOTP_KEY)
    monkeypatch.setenv(
        "GOATFARM_TOTP_ENCRYPTION_PREVIOUS_KEYS", json.dumps([TEST_TOTP_PREVIOUS_KEY])
    )
    get_settings.cache_clear()
    security._reset_totp_key_cache_for_tests()

    secret_legacy = generate_totp_secret_b32()
    secret_previous = generate_totp_secret_b32()
    secret_current = generate_totp_secret_b32()

    async with get_sessionmaker()() as db:
        for email, cipher in (
            ("rekey-legacy@farm.in", _legacy_ciphertext(secret_legacy)),
            ("rekey-previous@farm.in", _previous_key_ciphertext(secret_previous)),
            ("rekey-current@farm.in", encrypt_totp_secret(secret_current)),
            ("rekey-plain@farm.in", None),
        ):
            db.add(
                User(
                    email=email,
                    password_hash="not-a-login-password",
                    totp_secret_enc=cipher,
                    totp_state=None if cipher is None else "ACTIVE",
                )
            )
        await db.commit()

    # Dry run: counts the two stale rows, writes nothing.
    totals = await namespace["_rekey"](apply=False, batch_size=2)
    assert (totals.scanned, totals.rekeyed, totals.unchanged) == (3, 2, 1)
    async with get_sessionmaker()() as db:
        rows = {
            user.email: user.totp_secret_enc
            for user in (await db.execute(select(User).order_by(User.id))).scalars()
            if user.email.startswith("rekey-")
        }
    assert decrypt_totp_secret_with_metadata(rows["rekey-legacy@farm.in"]).needs_rewrap is True
    assert decrypt_totp_secret_with_metadata(rows["rekey-previous@farm.in"]).needs_rewrap is True
    assert decrypt_totp_secret_with_metadata(rows["rekey-current@farm.in"]).needs_rewrap is False

    # Apply at batch_size=1: three separate locked transactions, resumable
    # by construction after any interruption between batches.
    totals = await namespace["_rekey"](apply=True, batch_size=1)
    assert (totals.scanned, totals.rekeyed, totals.unchanged) == (3, 2, 1)
    async with get_sessionmaker()() as db:
        rows = {
            user.email: user.totp_secret_enc
            for user in (await db.execute(select(User).order_by(User.id))).scalars()
            if user.email.startswith("rekey-")
        }
    for email, secret in (
        ("rekey-legacy@farm.in", secret_legacy),
        ("rekey-previous@farm.in", secret_previous),
        ("rekey-current@farm.in", secret_current),
    ):
        decrypted = decrypt_totp_secret_with_metadata(rows[email])
        assert decrypted.secret == secret
        assert decrypted.needs_rewrap is False
    assert rows["rekey-plain@farm.in"] is None

    # Idempotent rerun: nothing left to do, so a cutover gate sees zeroes.
    totals = await namespace["_rekey"](apply=False, batch_size=250)
    assert (totals.scanned, totals.rekeyed, totals.unchanged, totals.unavailable_count) == (
        3,
        0,
        3,
        0,
    )


async def test_rekey_script_counts_undecryptable_rows_against_the_real_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuinely orphaned ciphertext is counted, not crashed on (audit L-5).

    Exercises the real per-row TotpSecretUnavailableError skip inside the
    real batch loop; the exit-code translation of unavailable>0 is pinned
    by the mocked-gate test below.
    """
    from app.core.config import Settings

    namespace = runpy.run_path(_REKEY_SCRIPT_PATH, run_name="rekey_unavailable_real_test")
    rekey_globals = namespace["_rekey"].__globals__
    test_settings = Settings(
        _env_file=None,
        totp_encryption_key=TEST_TOTP_KEY,
        totp_encryption_previous_keys=[TEST_TOTP_PREVIOUS_KEY],
    )
    monkeypatch.setitem(rekey_globals, "get_settings", lambda: test_settings)
    monkeypatch.setenv("GOATFARM_TOTP_ENCRYPTION_KEY", TEST_TOTP_KEY)
    monkeypatch.setenv(
        "GOATFARM_TOTP_ENCRYPTION_PREVIOUS_KEYS", json.dumps([TEST_TOTP_PREVIOUS_KEY])
    )
    get_settings.cache_clear()
    security._reset_totp_key_cache_for_tests()

    # Sealed under a key that is in neither the current slot nor the
    # predecessor ring: genuinely undecryptable by this deployment.
    stranger_key = bytes(range(32))
    nonce = b"\x02" * 12
    orphan = (
        security.TOTP_ENVELOPE_PREFIX
        + nonce
        + AESGCM(stranger_key).encrypt(
            nonce, generate_totp_secret_b32().encode("ascii"), security.TOTP_ENVELOPE_AAD
        )
    )
    async with get_sessionmaker()() as db:
        db.add(
            User(
                email="rekey-orphan@farm.in",
                password_hash="not-a-login-password",
                totp_secret_enc=orphan,
                totp_state="ACTIVE",
            )
        )
        await db.commit()

    totals = await namespace["_rekey"](apply=False, batch_size=250)
    assert totals.scanned == 1
    assert totals.rekeyed == 0
    assert totals.unavailable_count == 1

    # The dry run must leave the orphan untouched for investigation, and the
    # preview must name exactly that row. (All session use stays inside its
    # own context manager: a session reopened after close would park an
    # idle transaction that blocks the suite's table cleanup.)
    async with get_sessionmaker()() as verify_db:
        orphan_row = (
            await verify_db.execute(select(User).where(User.email == "rekey-orphan@farm.in"))
        ).scalar_one()
    assert totals.unavailable_preview_ids == [orphan_row.id]
    assert orphan_row.totp_secret_enc == orphan


def test_rekey_main_exit_gate_blocks_cutover_when_rows_are_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """main() must translate unavailable>0 into exit 2 even with rekeyed=0."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "rekey_totp_secrets.py"
    namespace = runpy.run_path(str(script), run_name="rekey_unavailable_gate_test")
    totals = namespace["RekeyTotals"](scanned=1, rekeyed=0, unchanged=0)
    totals.record_unavailable(4242)

    def fake_run(coro: Any) -> object:
        coro.close()
        return totals

    module_globals = namespace["main"].__globals__
    monkeypatch.setitem(
        module_globals, "_parse_args", lambda: SimpleNamespace(apply=False, batch_size=250)
    )
    monkeypatch.setitem(module_globals, "asyncio", SimpleNamespace(run=fake_run))

    assert namespace["main"]() == 2
    captured = capsys.readouterr()
    assert "unavailable=1" in captured.out
    assert "4242" in captured.err
    assert "Do not rotate or retire the active JWT signer" in captured.err


# --- ITEM 7 (2026-09-21 playbook): recovery codes ---------------------------


async def _mfa_login(client: httpx.AsyncClient, email: str) -> str:
    login = await client.post("/api/auth/login", json={"email": email, "password": OWNER_PW})
    assert login.status_code == 200, login.text
    mfa_token = login.json()["mfa_token"]
    assert mfa_token
    return mfa_token


async def _redeem(
    client: httpx.AsyncClient, email: str, mfa_token: str, code: str
) -> httpx.Response:
    return await client.post(
        "/api/auth/totp/challenge",
        json={"mfa_token": mfa_token, "code": code},
    )


async def test_recovery_code_redeems_the_login_challenge_single_use(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """A recovery code is a valid second factor exactly once — the break-glass
    path for a lost authenticator, with an audit event to alert on."""
    email = "totp-recovery@farm.in"
    headers = await register(client, email)
    _secret, codes = await _enroll_and_activate(client, headers)
    assert len(set(codes)) == 10
    assert all(len(code) == 11 and code[5] == "-" for code in codes)

    with caplog.at_level("INFO", logger="app.audit"):
        mfa_token = await _mfa_login(client, email)
        accepted = await _redeem(client, email, mfa_token, codes[0].lower())
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["access_token"]
    assert any("auth.totp.recovery_code_used" in record.message for record in caplog.records), (
        "the break-glass redemption must be a security event"
    )

    # Single-use: the SAME code never validates again, even case-shifted.
    mfa_token = await _mfa_login(client, email)
    replay = await _redeem(client, email, mfa_token, codes[0])
    assert replay.status_code == 401

    # But an unused code still works — the set is per-code, not global.
    mfa_token = await _mfa_login(client, email)
    second = await _redeem(client, email, mfa_token, codes[1])
    assert second.status_code == 200, second.text


async def test_recovery_codes_are_stored_as_argon2_hashes(
    client: httpx.AsyncClient,
) -> None:
    from app.db import get_sessionmaker
    from app.models import TotpRecoveryCode

    headers = await register(client, "totp-hashes@farm.in")
    _secret, _codes = await _enroll_and_activate(client, headers)
    async with get_sessionmaker()() as db:
        rows = list(
            (await db.execute(select(TotpRecoveryCode).order_by(TotpRecoveryCode.id))).scalars()
        )
    assert len(rows) == 10
    assert all(row.code_hash.startswith("$argon2") for row in rows)
    assert all(row.used_at is None for row in rows)


async def test_recovery_redemption_runs_under_a_password_work_reservation(
    client: httpx.AsyncClient,
) -> None:
    """2026-09-28 audit, S4: the up-to-ten sequential Argon2 verifies of one
    redemption hold the same one-per-account reservation as the other
    credential workflows — while a redemption is in flight, another attempt
    is a retryable busy 429, never a second unreserved Argon2 batch."""
    email = "totp-recovery-busy@farm.in"
    headers = await register(client, email)
    _secret, codes = await _enroll_and_activate(client, headers)
    async with get_sessionmaker()() as db:
        user_id = (await db.execute(select(User.id).where(User.email == email))).scalar_one()

    scope = auth_api.TOTP_RECOVERY_RESERVATION_SCOPE
    assert auth_limiter.try_reserve(scope, str(user_id))
    try:
        mfa_token = await _mfa_login(client, email)
        busy = await _redeem(client, email, mfa_token, codes[0])
    finally:
        auth_limiter.release(scope, str(user_id))
    assert busy.status_code == 429, busy.text
    assert "busy" in busy.json()["detail"].lower()

    # The busy refusal neither burned the challenge nor the code: with the
    # slot free again, the same code redeems normally.
    mfa_token = await _mfa_login(client, email)
    accepted = await _redeem(client, email, mfa_token, codes[0])
    assert accepted.status_code == 200, accepted.text


async def test_recovery_with_zero_unused_codes_still_pays_one_dummy_verify(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-09-28 audit, S4: an account with no unused recovery codes left
    still pays one dummy Argon2 verify before the generic 401, so the
    response time cannot reveal the remaining-code count."""
    email = "totp-recovery-parity@farm.in"
    headers = await register(client, email)
    _secret, codes = await _enroll_and_activate(client, headers)
    async with get_sessionmaker()() as db:
        user_id = (await db.execute(select(User.id).where(User.email == email))).scalar_one()
        await db.execute(
            update(TotpRecoveryCode)
            .where(TotpRecoveryCode.user_id == user_id)
            .values(used_at=utcnow())
        )
        await db.commit()

    verify_calls = 0
    real_verify = auth_api.verify_password_async

    async def counted_verify(password: str, password_hash: str) -> tuple[bool, bool]:
        nonlocal verify_calls
        verify_calls += 1
        return await real_verify(password, password_hash)

    monkeypatch.setattr(auth_api, "verify_password_async", counted_verify)
    mfa_token = await _mfa_login(client, email)
    refused = await _redeem(client, email, mfa_token, codes[0])
    assert refused.status_code == 401, refused.text
    assert refused.json()["detail"] == "Invalid or expired challenge."
    assert verify_calls == 1, "zero unused codes must still pay one dummy verify"


async def test_recovery_credential_change_mid_verify_cannot_ride_the_proof(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """2026-09-29 audit: a credential change that lands while a recovery code
    is mid-Argon2 must not ride the proof. The re-lock revalidates
    token_version (the worker_login pattern), answers the generic 401, and
    leaves the code unconsumed — the snapshot-verify-reload window is closed."""
    email = "totp-recovery-race@farm.in"
    headers = await register(client, email)
    _secret, codes = await _enroll_and_activate(client, headers)

    mfa_token = await _mfa_login(client, email)

    async def rotate_then_accept(password: str, password_hash: str) -> tuple[bool, bool]:
        # Simulate a concurrent password/PIN rotation landing INSIDE the
        # verification window: token_version bumps under the User lock on a
        # separate session, exactly like reset-password/reset-pin do.
        from app.db import get_sessionmaker

        async with get_sessionmaker()() as db:
            await db.execute(
                update(User).where(User.email == email).values(token_version=User.token_version + 1)
            )
            await db.commit()
        return True, False

    monkeypatch.setattr(auth_api, "verify_password_async", rotate_then_accept)
    with caplog.at_level("INFO", logger="app.audit"):
        refused = await _redeem(client, email, mfa_token, codes[0])
    assert refused.status_code == 401, refused.text
    assert refused.json()["detail"] == "Invalid or expired challenge."
    assert any(
        "account changed while a recovery code was being verified" in record.message
        for record in caplog.records
    ), "the race must leave a security event"

    # The proven code was NOT consumed: after re-login (fresh challenge bound
    # to the rotated token_version), the same code redeems normally.
    monkeypatch.undo()
    mfa_token = await _mfa_login(client, email)
    accepted = await _redeem(client, email, mfa_token, codes[0])
    assert accepted.status_code == 200, accepted.text


async def test_recovery_regenerate_requires_password_and_totp_and_revokes(
    client: httpx.AsyncClient,
) -> None:
    email = "totp-regen@farm.in"
    headers = await register(client, email)
    secret, codes = await _enroll_and_activate(client, headers)

    # Wrong password is refused before anything rotates.
    code, _step = _current_code(secret, drift=1)
    bad_pw = await client.post(
        "/api/auth/totp/recovery/regenerate",
        json={"current_password": "not-the-password", "code": code},
        headers=headers,
    )
    assert bad_pw.status_code == 400, bad_pw.text

    # Wrong TOTP code is refused too.
    bad_code = await client.post(
        "/api/auth/totp/recovery/regenerate",
        json={"current_password": OWNER_PW, "code": "000000"},
        headers=headers,
    )
    assert bad_code.status_code == 400, bad_code.text

    # A correct password + live code re-mints the whole set, once.
    regen = await client.post(
        "/api/auth/totp/recovery/regenerate",
        json={"current_password": OWNER_PW, "code": code},
        headers=headers,
    )
    assert regen.status_code == 200, regen.text
    fresh = regen.json()["codes"]
    assert len(fresh) == 10 and set(fresh).isdisjoint(codes)

    # Every prior code is revoked; every fresh code redeems.
    mfa_token = await _mfa_login(client, email)
    old = await _redeem(client, email, mfa_token, codes[2])
    assert old.status_code == 401
    mfa_token = await _mfa_login(client, email)
    new = await _redeem(client, email, mfa_token, fresh[0])
    assert new.status_code == 200, new.text


async def test_disabling_totp_purges_recovery_codes(
    client: httpx.AsyncClient,
) -> None:
    from app.db import get_sessionmaker
    from app.models import TotpRecoveryCode

    email = "totp-purge@farm.in"
    headers = await register(client, email)
    secret, _codes = await _enroll_and_activate(client, headers)
    code, _step = _current_code(secret, drift=1)
    disable = await client.post(
        "/api/auth/totp/disable",
        json={"current_password": OWNER_PW, "code": code},
        headers=headers,
    )
    assert disable.status_code == 204, disable.text
    async with get_sessionmaker()() as db:
        remaining = list((await db.execute(select(TotpRecoveryCode))).scalars())
    assert remaining == []


async def test_enroll_over_active_is_a_coded_lifecycle_conflict(
    client: httpx.AsyncClient,
) -> None:
    """2026-09-29 audit, L1: the MFA state 409s carry their conflict family
    code so localized clients branch on the code, not English prose."""
    headers = await register(client, "totp-coded409@farm.in")
    await _enroll_and_activate(client, headers)
    refused = await client.post(
        "/api/auth/totp/enroll", json={"current_password": OWNER_PW}, headers=headers
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "LIFECYCLE_CONFLICT"
    assert refused.json()["detail"].startswith("Two-factor is already enabled.")
