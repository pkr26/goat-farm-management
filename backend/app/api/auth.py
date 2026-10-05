"""Auth: register, login (JWT pair), refresh (rotating httpOnly cookie backed
by a server-side session row with reuse detection), logout (revokes the
presented session), change-password, and farm listing/creation."""

import asyncio
import hashlib
import logging
import urllib.parse
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Annotated, Any, NoReturn, cast
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import metrics
from ..audit import note_transient_security_signal, security_event
from ..core.config import get_settings
from ..deps import (
    INVALID_ACCESS_TOKEN_SCOPE,
    INVALID_LOGOUT_REFRESH_TOKEN_SCOPE,
    CurrentFarm,
    CurrentMembership,
    CurrentPerms,
    CurrentUser,
    DbSession,
    accessible_farms,
    active_membership,
    guard_invalid_token_verification_budget,
    invalid_token_rate_error,
    record_invalid_token_verification,
    require_live_authenticated_session,
    revoke_session_family,
    revoke_user_sessions,
    single_bearer_token,
)
from ..models import (
    ConsumedMfaChallenge,
    Farm,
    FarmMembership,
    RefreshSession,
    Role,
    TotpRecoveryCode,
    User,
)
from ..models.idempotency import CREATE_FARM_IDEMPOTENCY_OPERATION
from ..ratelimit import SlidingWindowRateLimiter, auth_limiter
from ..schemas.auth import (
    AccountDeleteIn,
    AccountExportOut,
    AccountIdentityExport,
    ChangePasswordIn,
    FarmCreateIn,
    FarmOut,
    FarmOwnershipTransferIn,
    LoginIn,
    LoginOut,
    MembershipExport,
    OwnedFarmExport,
    PermissionsOut,
    RegisterIn,
    TokenOut,
    TotpChallengeIn,
    TotpCodeIn,
    TotpDisableIn,
    TotpEnrollIn,
    TotpEnrollOut,
    TotpRecoveryCodesOut,
    TotpRecoveryRegenerateIn,
    UserOut,
    WorkerLoginIn,
    WorkerRosterEntryOut,
    WorkerRosterOut,
)
from ..schemas.common import (
    COMMON_ERROR_RESPONSES,
    JSON_CONTENT_TYPE_ERROR_RESPONSE,
    MAX_INT32_ID,
    lifecycle_conflict,
    stale_state_conflict,
    standing_quota,
)
from ..security import (
    LEGACY_PBKDF2_PREFIX,
    PASSWORD_SESSION_SCOPE,
    TOTP_CHALLENGE_TTL_SECONDS,
    TOTP_RECOVERY_CODE_COUNT,
    DecryptedTotpSecret,
    PasswordWorkCapacityError,
    SessionScope,
    TotpSecretUnavailableError,
    _decode_payload_result,
    _decode_session_scope,
    complete_rejected_login_timing_async,
    decode_access_claims_result,
    decode_refresh_claims,
    decrypt_totp_secret_with_metadata,
    encrypt_totp_secret,
    generate_totp_recovery_code,
    generate_totp_secret_b32,
    hash_password_async,
    hash_totp_recovery_codes_async,
    issue_access_token,
    issue_refresh_token,
    issue_token,
    looks_like_totp_recovery_code,
    normalize_totp_recovery_code,
    password_policy_error,
    prime_dummy_password_hash,
    verify_password_async,
    verify_password_with_work_async,
    verify_totp_code,
)
from ..seed import seed_new_farm
from ..services.idempotency import RequiredIdempotencyKey, execute_idempotent
from ..utils import utcnow

router = APIRouter(prefix="/api/auth", tags=["auth"], responses=COMMON_ERROR_RESPONSES)

logger = logging.getLogger("goatfarm.auth")

ALREADY_REGISTERED = "That email is already registered."

# Separate map: charging duplicate-email probes must not add keys to the
# shared auth limiter's bounded bookkeeping (its cardinality ceiling is
# load-bearing for the invalid-token and login budgets).
register_email_limiter = SlidingWindowRateLimiter()

# Same isolation rule, same reason: roster probes are unauthenticated, so the per-farm budget below
# is charged by arbitrary callers and must not consume the shared auth limiter's bounded bookkeeping
# either.
worker_roster_farm_limiter = SlidingWindowRateLimiter()


def _register_email_probe_key(email: str) -> str:
    """Bucket key for one probed address — a hash, never the raw address."""
    return hashlib.sha256(email.lower().encode("utf-8")).hexdigest()


TOO_MANY_ATTEMPTS = "Too many attempts — please try again later."
UNTRUSTED_COOKIE_ORIGIN = "Untrusted origin for cookie-authenticated request."

# The composite (IP, email) failure budget is the base ceiling;
# the IP-agnostic per-email ceiling (distributed guessing against ONE account
# from rotating addresses) and the email-agnostic per-IP ceiling (password
# spraying across MANY accounts from one address) are multiples of it, so
# bystanders behind a shared proxy/NAT IP keep their own composite budgets.
EMAIL_LIMIT_MULTIPLIER = 3
IP_LIMIT_MULTIPLIER = 10

# Refresh signature checks are much cheaper than Argon2, but they still need an
# admission ceiling *before* untrusted JWT material reaches PyJWT.  This wider
# all-request budget is deliberately wider than ``refresh-invalid`` so normal
# page-load refreshes have shared-NAT headroom. Both buckets are checked before
# PyJWT: once the smaller rejected-token budget is full, later requests do not
# repeat signature work merely to return 429.
REFRESH_PREVERIFY_SCOPE = "refresh-preverify"
REFRESH_PREVERIFY_LIMIT_MULTIPLIER = 10
# Settings rejects values above this ceiling. It also gives down-configuration
# compaction a finite legacy bound instead of ever loading an arbitrary family.
REFRESH_SESSION_HISTORY_HARD_CEILING = 4096


def _refresh_preverification_limit(max_attempts: int) -> int:
    """Return the wider all-refresh admission ceiling."""
    return max_attempts * REFRESH_PREVERIFY_LIMIT_MULTIPLIER


# Current-password confirmation is what stops a stolen access token from
# becoming a permanent account takeover, so it gets the same two-layer budget:
# a composite (IP, account) counter plus an IP-agnostic per-account ceiling
# that rotating source addresses cannot reset. `_reserve_password_work` only
# serializes concurrent verifies for one account; it is not a budget.
CHANGE_PASSWORD_SCOPE = "change-password"
ACCOUNT_DELETE_SCOPE = "account-delete"
# Both endpoints confirm the SAME secret and answer a miss with the same
# distinguishable 400, so they must share the IP-agnostic ceiling — otherwise
# an attacker who exhausted one endpoint's budget simply switches to the other
# and doubles their guesses per window, exactly the way rotating source
# addresses would if the per-account counter did not exist. This mirrors
# _hash_team_password, where worker-create and password-reset already share
# both the reservation and the ledger.
ACCOUNT_PASSWORD_CONFIRM_ACCOUNT_SCOPE = "account-password-confirm"
# Both account password workflows share one reservation, as the two team
# password endpoints do: with a scope each, one account could hold two of the
# global Argon slots at once and 429 every other user's login.
ACCOUNT_PASSWORD_RESERVATION_SCOPE = "account-password-work"

# Lazily built (Argon2 needs the settings and costs real CPU at first use).
# create_app() calls prime_dummy_password_hash() at startup so the first
# unknown-email login is not measurably slower than any subsequent one
_DUMMY_PASSWORD_HASH: str | None = None


def _dummy_password_hash() -> str:
    """A real Argon2id hash of a throwaway password. Unknown-email logins
    verify against it so both login paths do equal Argon2 work — the response
    time can't reveal whether the email exists (user-enumeration oracle)."""
    global _DUMMY_PASSWORD_HASH
    if _DUMMY_PASSWORD_HASH is None:
        _DUMMY_PASSWORD_HASH = prime_dummy_password_hash()
    return _DUMMY_PASSWORD_HASH


def _client_key(request: Request) -> str:
    # request.client is None on some ASGI servers/transports: those requests
    # share one bucket instead of bypassing the limiter.
    return request.client.host if request.client else "unknown"


def _browser_origin(url: str) -> str:
    """Return a URL's browser-serialized HTTP(S) origin, or ``""``.

    A Starlette ``base_url`` may include an ASGI ``root_path`` even though an
    Origin header never contains a path.  Reconstructing from parsed
    scheme/authority also mirrors browser treatment of case, IPv6 brackets,
    and explicit default ports.
    """
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return ""
    scheme = parsed.scheme.lower()
    host = parsed.hostname
    if scheme not in {"http", "https"} or host is None:
        return ""
    authority = f"[{host}]" if ":" in host else host
    default_port = {"http": 80, "https": 443}[scheme]
    if port is not None and port != default_port:
        authority = f"{authority}:{port}"
    return f"{scheme}://{authority}"


def _guard_cookie_request_origin(request: Request) -> None:
    """Reject browser cross-site requests before consuming a refresh cookie.

    SameSite=Lax is the first barrier, but sibling subdomains are still
    considered same-site. Browsers attach Origin (or, for older clients,
    Referer) to unsafe requests, so require an exact configured SPA/API origin
    whenever either signal is present. Header-less server/mobile clients stay
    supported; a browser that explicitly reports ``Sec-Fetch-Site: cross-site``
    is never allowed to use that compatibility path.
    """
    if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        raise HTTPException(status_code=403, detail=UNTRUSTED_COOKIE_ORIGIN)

    allowed = set(get_settings().cors_origins)
    allowed.add(_browser_origin(str(request.base_url)))
    origin = request.headers.get("Origin")
    if origin is not None:
        if origin not in allowed:
            raise HTTPException(status_code=403, detail=UNTRUSTED_COOKIE_ORIGIN)
        return

    referer = request.headers.get("Referer")
    if referer is None:
        return
    referer_origin = _browser_origin(referer)
    if referer_origin not in allowed:
        raise HTTPException(status_code=403, detail=UNTRUSTED_COOKIE_ORIGIN)


def _login_key(request: Request, email: str) -> str:
    """Login attempts are throttled per (IP, email): behind a shared proxy IP
    (the Next dev proxy, a NAT, a corporate egress) one attacker's failures
    lock out only the account they target — not every user of the proxy —
    and per-attacker limiting still works when IPs differ. The email is
    already normalized (stripped/lowercased) by LoginIn."""
    return f"{_client_key(request)}|{email}"


def _login_block_reason(request: Request, email: str) -> str | None:
    """Return the first tripped login throttle.

    All three scopes are temporary hard admission ceilings. In particular,
    the IP-agnostic email bucket must stop verification itself: continuing to
    verify and admitting a correct guess after the advertised ceiling leaves
    rotating-source credential stuffing uncapped. The bounded window is the
    recovery path; successful authentication before the ceiling still clears
    the account-specific history.
    """
    s = get_settings()
    if not s.auth_rate_limit_enabled:
        return None
    attempts, window = s.auth_rate_limit_max_attempts, s.auth_rate_limit_window_seconds
    if auth_limiter.is_blocked("login", _login_key(request, email), attempts, window):
        reason = "composite"
    elif auth_limiter.is_blocked(
        "login-ip", _client_key(request), attempts * IP_LIMIT_MULTIPLIER, window
    ):
        reason = "ip"
    elif auth_limiter.is_blocked("login-email", email, attempts * EMAIL_LIMIT_MULTIPLIER, window):
        reason = "email"
    else:
        return None
    metrics.record_auth_rate_limit_rejection("login")
    return reason


def _login_hard_blocked(request: Request, email: str) -> bool:
    return _login_block_reason(request, email) is not None


def _login_email_locked(request: Request, email: str) -> bool:
    """Direct per-email bucket query — never derived from
    ``_login_block_reason``: when several scopes trip at once (e.g. low test
    ceilings), the reason function's composite-first ordering would shadow
    the email scope and the threshold-crossing request would answer a plain
    401 instead of announcing the new temporary account lock with 429."""
    s = get_settings()
    if not s.auth_rate_limit_enabled:
        return False
    return auth_limiter.is_blocked(
        "login-email",
        email,
        s.auth_rate_limit_max_attempts * EMAIL_LIMIT_MULTIPLIER,
        s.auth_rate_limit_window_seconds,
    )


def _record_login_failure(request: Request, email: str) -> None:
    s = get_settings()
    if s.auth_rate_limit_enabled:
        attempts, window = s.auth_rate_limit_max_attempts, s.auth_rate_limit_window_seconds
        auth_limiter.record("login", _login_key(request, email), window, max_attempts=attempts)
        auth_limiter.record(
            "login-email", email, window, max_attempts=attempts * EMAIL_LIMIT_MULTIPLIER
        )
        auth_limiter.record(
            "login-ip",
            _client_key(request),
            window,
            max_attempts=attempts * IP_LIMIT_MULTIPLIER,
        )


def _reset_login_failures(request: Request, email: str) -> None:
    """A success clears the failure count — but only for the account that
    authenticated: the per-IP spray bucket ("login-ip") is never reset by
    someone else's (or one's own) success."""
    auth_limiter.reset("login", _login_key(request, email))
    auth_limiter.reset("login-email", email)


def _rate_limited(scope: str, key: str) -> bool:
    """Settings are read per request so tests can monkeypatch them."""
    s = get_settings()
    blocked = s.auth_rate_limit_enabled and auth_limiter.is_blocked(
        scope, key, s.auth_rate_limit_max_attempts, s.auth_rate_limit_window_seconds
    )
    if blocked:
        metrics.record_auth_rate_limit_rejection(scope)
    return blocked


def _record_attempt(scope: str, key: str, *, limit_multiplier: int = 1) -> None:
    s = get_settings()
    if s.auth_rate_limit_enabled:
        auth_limiter.record(
            scope,
            key,
            s.auth_rate_limit_window_seconds,
            max_attempts=s.auth_rate_limit_max_attempts * limit_multiplier,
        )


def _account_password_blocked(scope: str, account_scope: str, rate_key: str, user_id: int) -> bool:
    """Composite (IP, account) ceiling plus the IP-agnostic per-account one.

    Without the second counter a caller who varies their source address gets a
    fresh budget on every request, exactly the distributed-guessing hole the
    login path closes with "login-email".
    """
    s = get_settings()
    if not s.auth_rate_limit_enabled:
        return False
    attempts, window = s.auth_rate_limit_max_attempts, s.auth_rate_limit_window_seconds
    blocked = auth_limiter.is_blocked(scope, rate_key, attempts, window) or auth_limiter.is_blocked(
        account_scope, str(user_id), attempts * EMAIL_LIMIT_MULTIPLIER, window
    )
    if blocked:
        metrics.record_auth_rate_limit_rejection(scope)
    return blocked


def _record_account_password_attempt(
    scope: str, account_scope: str, rate_key: str, user_id: int
) -> None:
    """Charge a password-confirmation workflow that reached verification.

    Successful commits clear the counters. Rejections, cancellation, and
    post-verification failures retain the charge so none can loop Argon work
    outside the bounded confirmation budget.
    """
    _record_attempt(scope, rate_key)
    _record_attempt(account_scope, str(user_id), limit_multiplier=EMAIL_LIMIT_MULTIPLIER)


def _reset_account_password_attempts(
    scope: str, account_scope: str, rate_key: str, user_id: int
) -> None:
    auth_limiter.reset(scope, rate_key)
    auth_limiter.reset(account_scope, str(user_id))


async def _require_json_content_type(request: Request) -> None:
    """AUTH-2 (2026-09-16): the unauthenticated credential endpoints relied
    on FastAPI's strict JSON parsing (which rejects form/text bodies) as an
    incidental login-CSRF defense. Make the requirement explicit at the
    application level so a future framework-config change cannot silently
    re-open classic JSON-smuggling login CSRF."""
    content_type = request.headers.get("content-type", "application/json")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(
            status_code=415,
            detail="Content-Type must be application/json for this endpoint.",
        )


def _too_many_attempts(window_seconds: int | None = None) -> HTTPException:
    """Standards-friendly throttle response with a conservative retry hint.

    ``window_seconds`` overrides the header for scopes whose window is tuned
    independently of ``auth_rate_limit_window_seconds`` (the worker-PIN
    scopes) — the hint must cite the scope's own window, not the global
    default the two merely happen to share (2026-09-29 audit)."""
    window = (
        window_seconds
        if window_seconds is not None
        else get_settings().auth_rate_limit_window_seconds
    )
    return HTTPException(
        status_code=429,
        detail=TOO_MANY_ATTEMPTS,
        headers={"Retry-After": str(window)},
    )


class _PasswordWorkReservation:
    """Keep one identity admission until its native password job is idle.

    ``security._run_password_work`` correctly retains the global Argon slot
    after request cancellation.  A route-level identity reservation must have
    the same lifetime; releasing it as soon as the cancelled route unwinds lets
    one client cancel/retry until it occupies every global worker.  Each route
    has at most one password task in flight, and this wrapper lets that task
    finish under ``shield`` before a completion callback returns admission.
    """

    def __init__(self, scope: str, key: str) -> None:
        self.scope = scope
        self.key = key
        self._current: asyncio.Future[Any] | None = None
        self._released = False

    async def run[ResultT](self, work: Callable[[], Awaitable[ResultT]]) -> ResultT:
        if self._current is not None:
            raise RuntimeError("Password reservation already has work in flight")
        task: asyncio.Future[ResultT] = asyncio.ensure_future(work())
        self._current = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                self._current = None

    def release_when_idle(self) -> None:
        if self._released:
            return
        self._released = True
        current = self._current
        if current is None or current.done():
            auth_limiter.release(self.scope, self.key)
            return

        def release(finished: asyncio.Future[Any]) -> None:
            auth_limiter.release(self.scope, self.key)
            # The cancelled HTTP caller no longer awaits this task. Consume a
            # late exception so asyncio does not report it as unhandled.
            if not finished.cancelled():
                finished.exception()

        current.add_done_callback(release)


def _reserve_password_work(scope: str, key: str) -> _PasswordWorkReservation:
    """Admit at most one password workflow per identity at a time.

    Failure counters are recorded only after verification, so a simultaneous
    same-email burst could otherwise have every request pass the pre-check.
    This non-waiting reservation closes that gap without revealing whether the
    normalized email exists: known and unknown identities use the same keying
    and the same generic retryable 429 response.
    """
    if not auth_limiter.try_reserve(scope, key):
        metrics.record_auth_rate_limit_rejection(scope)
        raise PasswordWorkCapacityError("Password workflow already in flight")
    return _PasswordWorkReservation(scope, key)


def _set_refresh_cookie(response: Response, token: str, *, max_age: int | None = None) -> None:
    s = get_settings()
    response.set_cookie(
        s.refresh_cookie_name,
        token,
        max_age=s.refresh_token_ttl_seconds if max_age is None else max_age,
        httponly=True,
        samesite="lax",
        secure=s.cookie_secure,
        # ``__Host-`` cookies require Path=/ and no Domain.  Keeping the same
        # path in development makes cookie identity/deletion consistent across
        # environments and prevents a deployment-only behavior change.
        path="/",
    )


def _delete_refresh_cookie(response: Response) -> None:
    """Expire the refresh cookie using the same identity/security attributes."""
    settings = get_settings()
    response.delete_cookie(
        settings.refresh_cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _refresh_cookie(request: Request) -> str | None:
    """Return exactly one refresh cookie, rejecting ambiguous duplicates.

    ``Request.cookies`` is a mapping, so duplicate names have already been
    collapsed by the time a route reads it.  Count the raw Cookie fields first;
    this closes parent-domain/host-only ambiguity on legacy deployments while
    the production ``__Host-`` prefix prevents such duplicates being created.
    Refresh JWTs contain no semicolons, so a simple RFC cookie-pair split is
    sufficient and fails closed for malformed input.
    """
    name = get_settings().refresh_cookie_name
    count = 0
    for header in request.headers.getlist("cookie"):
        for pair in header.split(";"):
            cookie_name, separator, _value = pair.strip().partition("=")
            if separator and cookie_name == name:
                count += 1
                if count > 1:
                    return None
    if count != 1:
        return None
    return request.cookies.get(name)


def _refresh_token_key(token: str | None) -> str:
    """Bucket key for one specific presented cookie (never the raw value)."""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _check_refresh_preverification_budget(request: Request, token: str | None) -> None:
    """Bound JWT verification work before decoding the supplied cookie.

    Keyed on the PRESENTED COOKIE, not the client address. Authenticity is
    unknowable before decoding, so a shared-IP history must never reject a
    credential this budget has not itself classified — keying the pre-check on
    the address let ten garbage cookies from one NAT/CGNAT egress turn every
    co-located user's still-valid refresh into a 429, logging out a whole
    office for the window and renewably so. ``deps`` states exactly this rule
    for access tokens and keys its own pre-check on sha256(token); /refresh now
    matches it. The wider per-IP ceiling still bounds verification CPU, but
    only where it cannot harm a valid credential: it is consulted after a token
    has actually failed, in ``_raise_invalid_refresh``.
    """
    settings = get_settings()
    if not settings.auth_rate_limit_enabled:
        return
    invalid_limit = settings.auth_rate_limit_max_attempts
    window = settings.auth_rate_limit_window_seconds
    if auth_limiter.is_blocked(
        REFRESH_PREVERIFY_SCOPE,
        _refresh_token_key(token),
        invalid_limit,
        window,
    ):
        metrics.record_auth_rate_limit_rejection(REFRESH_PREVERIFY_SCOPE)
        raise _too_many_attempts()


async def _make_refresh_session_slot(
    db: AsyncSession,
    *,
    user_id: int,
    family_id: str,
    new_family: bool,
    preserve_session_id: int | None = None,
) -> None:
    """Evict finite old session/family history before issuing a successor.

    The migration compacts legacy rows to these same hard ceilings. From then
    on each successful issue stays within the configured ceiling. A deployment
    that lowers its session limit compacts the bounded prior history in the same
    transaction instead of deleting and adding one row forever at the old size.
    """
    settings = get_settings()
    if new_family:
        if preserve_session_id is not None:
            raise ValueError("A new refresh family cannot preserve a predecessor")
        family_rows = (
            await db.execute(
                select(
                    RefreshSession.family_id,
                    func.max(RefreshSession.id).label("latest_session_id"),
                )
                .where(RefreshSession.user_id == user_id)
                .group_by(RefreshSession.family_id)
                # User-locked issuance makes the session sequence the stable
                # family order. Wall clocks can move backward; using created_at
                # would evict a newer login while retaining an older family.
                .order_by(func.max(RefreshSession.id).desc(), RefreshSession.family_id)
            )
        ).all()
        # Keep the newest max-1 existing families, leaving one slot for this
        # login/change-password issuance. Rows in evicted families are no
        # longer usable; deleting them is equivalent to server-side logout.
        evicted = [
            existing_family
            for existing_family, _latest_id in family_rows[
                settings.refresh_max_families_per_user - 1 :
            ]
            if existing_family != family_id
        ]
        if evicted:
            await db.execute(
                delete(RefreshSession).where(
                    RefreshSession.user_id == user_id,
                    RefreshSession.family_id.in_(evicted),
                )
            )
        return

    if preserve_session_id is None:
        raise ValueError("Refresh rotation must preserve its presented session")
    overflow_ids = list(
        (
            await db.execute(
                select(RefreshSession.id)
                .where(
                    RefreshSession.user_id == user_id,
                    RefreshSession.family_id == family_id,
                    RefreshSession.id != preserve_session_id,
                )
                .order_by(RefreshSession.id.desc())
                # Retain the presented row plus max-2 others, leaving exactly
                # one slot for its successor. Never infer "presented/newest"
                # from a wall-clock timestamp: NTP correction can move time
                # backward, and deleting the row being rotated destroys the
                # concurrent-tab grace link.
                # Loading one more than the legal configuration ceiling detects
                # manually corrupted history without an unbounded request query.
                .offset(settings.refresh_max_sessions_per_family - 2)
                .limit(REFRESH_SESSION_HISTORY_HARD_CEILING + 1)
            )
        ).scalars()
    )
    if len(overflow_ids) > REFRESH_SESSION_HISTORY_HARD_CEILING:
        detail = "Refresh-session history exceeds its repairable bound"
        detail += "; contact an administrator."
        raise standing_quota(
            detail=detail,
        )
    if overflow_ids:
        await db.execute(delete(RefreshSession).where(RefreshSession.id.in_(overflow_ids)))


async def _issue_tokens(
    db: AsyncSession,
    user: User,
    response: Response,
    family_id: str | None = None,
    *,
    replacement_for: RefreshSession | None = None,
    scope: SessionScope = PASSWORD_SESSION_SCOPE,
    transient: bool = False,
) -> TokenOut:
    """Mint the access/refresh pair and persist the refresh jti's session row
    (new family unless rotating within `family_id`). The caller commits."""
    if user.deleted_at is not None:
        raise HTTPException(status_code=401, detail="Account no longer exists")
    s = get_settings()
    jti = uuid.uuid4().hex
    effective_family_id = family_id or uuid.uuid4().hex
    await _make_refresh_session_slot(
        db,
        user_id=user.id,
        family_id=effective_family_id,
        new_family=family_id is None,
        preserve_session_id=replacement_for.id if replacement_for is not None else None,
    )
    issued_at = utcnow()
    expires_at = issued_at + timedelta(
        seconds=min(s.access_token_ttl_seconds, s.refresh_token_ttl_seconds)
        if transient
        else s.refresh_token_ttl_seconds
    )
    session = RefreshSession(
        user_id=user.id,
        jti=jti,
        family_id=effective_family_id,
        session_origin=scope.origin,
        farm_id=scope.farm_id,
        membership_id=scope.membership_id,
        expires_at=expires_at,
        created_at=issued_at,
    )
    db.add(session)
    await db.flush()  # sessions run autoflush=False — land the row explicitly
    if replacement_for is not None:
        replacement_for.replacement_jti = jti
    if not transient:
        _set_refresh_cookie(
            response,
            issue_refresh_token(
                user.id,
                jti=jti,
                family_id=effective_family_id,
                scope=scope,
                issued_at=issued_at,
                expires_at=expires_at,
            ),
        )
    return TokenOut(
        access_token=issue_access_token(
            user.id, user.token_version, scope=scope, family_id=effective_family_id
        ),
        user=UserOut.model_validate(user),
    )


def _refresh_token_expired_but_genuine(token: str) -> bool:
    """True only when an authentic refresh token's sole defect is expiry.

    Mirrors the deliberate ``AccessDecodeResult.expired`` exemption
    (deps.current_user and logout's access branch): with a 14-day cookie TTL a
    user returning from a long absence presents a signature-valid, well-formed
    but expired cookie on the very first page load. That is a returning
    client, not attacker probing, so it must not be charged to the per-IP
    invalid-token ledgers — behind a shared NAT a handful of such returns
    would otherwise 429 colleagues' still-valid refreshes. Forged or malformed
    material (bad signature, wrong kind, bogus claims) still returns False and
    stays on the attacker ledger.
    """
    payload, expired = _decode_payload_result(token, "refresh")
    if payload is None or not expired:
        return False
    # Apply decode_refresh_claims' extra family-claim shape rule: a token that
    # would have been rejected even when fresh is invalid, not merely expired.
    family_id = payload.get("fid")
    return _decode_session_scope(payload) is not None and (
        family_id is None or (isinstance(family_id, str) and 1 <= len(family_id) <= 64)
    )


def _raise_invalid_refresh(request: Request, token: str | None = None) -> NoReturn:
    """Record one classified-invalid refresh and reject it.

    Both ledgers are written only once the presented cookie has actually
    failed, so neither can reject an unclassified credential:

    * the per-cookie bucket makes a repeat of THIS bad cookie cheap to refuse
      before the next RSA verification (``_check_refresh_preverification_budget``);
    * the per-IP bucket remains the CPU backstop, but it can only turn *this
      already-invalid* request into a 429 — a valid cookie arriving from the
      same address is never judged by a neighbour's history.

    This mirrors ``deps.record_invalid_token_verification`` for access tokens.
    """
    note_transient_security_signal("auth.refresh.invalid")
    settings = get_settings()
    if settings.auth_rate_limit_enabled:
        rate_key = _client_key(request)
        limit = settings.auth_rate_limit_max_attempts
        window = settings.auth_rate_limit_window_seconds
        ip_limit = _refresh_preverification_limit(limit)
        # Per-IP gate BEFORE the per-cookie record (RT-M-5): once the address
        # is already throttled, appending yet another unique per-cookie key
        # only pressures the limiter's key ceiling — it cannot change this
        # request's outcome.
        if auth_limiter.is_blocked("refresh-invalid", rate_key, ip_limit, window):
            auth_limiter.record("refresh-invalid", rate_key, window, max_attempts=ip_limit)
            metrics.record_auth_rate_limit_rejection("refresh-invalid")
            raise _too_many_attempts()
        auth_limiter.record(
            REFRESH_PREVERIFY_SCOPE,
            _refresh_token_key(token),
            window,
            max_attempts=limit,
        )
        auth_limiter.record("refresh-invalid", rate_key, window, max_attempts=ip_limit)
        if auth_limiter.is_blocked("refresh-invalid", rate_key, ip_limit, window):
            metrics.record_auth_rate_limit_rejection("refresh-invalid")
            raise _too_many_attempts()
    raise HTTPException(status_code=401, detail="Invalid or expired refresh token")


@router.post(
    "/register",
    status_code=201,
    dependencies=[Depends(_require_json_content_type)],
    responses=JSON_CONTENT_TYPE_ERROR_RESPONSE,
)
async def register(
    payload: RegisterIn, request: Request, response: Response, db: DbSession
) -> TokenOut:
    rate_key = _client_key(request)
    if _rate_limited("register", rate_key):
        raise _too_many_attempts()
    _record_attempt("register", rate_key)
    error = password_policy_error(payload.password)
    if error:
        raise HTTPException(status_code=400, detail=error)
    # Hash BEFORE the existence check so a duplicate email doesn't return measurably earlier than a
    # fresh one (timing half of the register enumeration oracle). The explicit 400 remains — without
    # email verification there is no accept-and-notify path. Repeated probing of one email is ALSO
    # charged to a per-email bucket — but ONLY when the email already exists: enumerating live
    # account names is the attack, and charging fresh-address attempts would let an IP-rotating
    # attacker lock a legitimate registrant out of their own address. The per-email ceiling is SOFT
    # exactly like login's email scope (RT-A-1): it is consulted only where the oracle answer — the
    # duplicate 400 — is about to be sent, never before. A bucket holding history therefore cannot
    # refuse a registration that would otherwise succeed (a freed address re-registering after a
    # deletion inside the window; any mischarge). This supersedes the old RT-A-2 pre-hash gate: its
    # only unique effect was to let a hot bucket 429 before the hash, and unregistered-address
    # bursts could always spend those same Argon2 slots ungated — so the gate bought no CPU bound,
    # only the registrant lockout the soft scope forbids. The probe key is a hash of the address,
    # never the address itself. It is process-local limiter state and is never copied into logs.
    email_probe_key = _register_email_probe_key(payload.email)
    s_limits = get_settings()

    def _charge_email_probe() -> None:
        if s_limits.auth_rate_limit_enabled:
            register_email_limiter.record(
                "register-email",
                email_probe_key,
                s_limits.auth_rate_limit_window_seconds,
                max_attempts=s_limits.auth_rate_limit_max_attempts,
            )

    def _probe_ceiling_tripped() -> bool:
        """The duplicate answer is about to be sent: should it be a 429?"""
        return bool(
            s_limits.auth_rate_limit_enabled
            and register_email_limiter.is_blocked(
                "register-email",
                email_probe_key,
                s_limits.auth_rate_limit_max_attempts,
                s_limits.auth_rate_limit_window_seconds,
            )
        )

    pw_hash = await hash_password_async(payload.password)
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none() is not None:
        if _probe_ceiling_tripped():
            metrics.record_auth_rate_limit_rejection("register-email")
            raise _too_many_attempts()
        _charge_email_probe()
        raise HTTPException(status_code=400, detail=ALREADY_REGISTERED)
    user = User(
        email=payload.email,
        name=(payload.name or "").strip() or None,
        password_hash=pw_hash,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:  # same email registered concurrently
        await db.rollback()
        _charge_email_probe()
        raise HTTPException(status_code=400, detail=ALREADY_REGISTERED) from None
    if s_limits.auth_rate_limit_enabled:
        # The address now belongs to this registrant — its probe history must
        # not throttle the account it became.
        register_email_limiter.reset("register-email", email_probe_key)
    out = await _issue_tokens(db, user, response)
    await db.commit()
    return out


@router.post(
    "/login",
    dependencies=[Depends(_require_json_content_type)],
    responses=JSON_CONTENT_TYPE_ERROR_RESPONSE,
)
async def login(payload: LoginIn, request: Request, response: Response, db: DbSession) -> LoginOut:
    if _login_hard_blocked(request, payload.email):
        raise _too_many_attempts()
    reservation_scope = "login-password-work"
    reservation = _reserve_password_work(reservation_scope, payload.email)
    credential_accepted = False
    password_work_started = False
    try:
        # Re-check after the atomic admission reservation. A preceding request
        # may have recorded the threshold immediately before releasing its
        # slot; no expensive work starts from a stale limiter observation.
        # Every scope pre-rejects, including the IP-agnostic account ceiling;
        # no rotating-source request may keep spending Argon2 guesses after
        # that account budget is exhausted.
        if _login_hard_blocked(request, payload.email):
            raise _too_many_attempts()

        # Snapshot immutable scalar values, then end the read transaction so
        # Argon2 never holds a row lock, transaction, or checked-out DB
        # connection. The write-class reload below exact-compares this snapshot
        # before a token can be minted.
        snapshot = (
            await db.execute(
                select(User.id, User.password_hash, User.token_version).where(
                    User.email == payload.email,
                    User.deleted_at.is_(None),
                )
            )
        ).one_or_none()
        await db.rollback()

        invalid = HTTPException(status_code=401, detail="Invalid email or password.")
        stored_hash = snapshot.password_hash if snapshot is not None else _dummy_password_hash()
        password_work_started = True
        ok, needs_rehash, did_argon_work = await reservation.run(
            lambda: verify_password_with_work_async(payload.password, stored_hash)
        )
        if snapshot is None or not ok:
            # Every rejection has exactly two bounded executor submissions and
            # pays one Argon2 plus one fixed PBKDF2 budget. A legacy hash spent
            # part of the latter above; completion adds only the remainder.
            # The credential decision is already made. A saturated pool must
            # not upgrade this definitive 401 into a 429, and the rejection
            # must still reach the brute-force ledger — losing padding
            # fidelity under overload is the lesser harm.
            with suppress(PasswordWorkCapacityError):
                await reservation.run(
                    lambda: complete_rejected_login_timing_async(
                        payload.password,
                        stored_hash,
                        _dummy_password_hash(),
                        did_argon_work,
                    )
                )
            _record_login_failure(request, payload.email)
            if _login_email_locked(request, payload.email):
                # The request that reaches the per-email ceiling announces the
                # temporary account lock immediately; subsequent requests are
                # rejected before another expensive verification.
                metrics.record_auth_rate_limit_rejection("login")
                raise _too_many_attempts()
            raise invalid

        # From this point cancellation is no longer an invalid-credential CPU
        # bypass: the caller proved the secret. Before this point, a disconnect
        # is a failed login attempt and must enter the same ledgers as a 401;
        # otherwise cancel/retry can spend Argon work forever without ever
        # reaching the post-verification accounting below.
        credential_accepted = True

        replacement_hash = (
            await reservation.run(lambda: hash_password_async(payload.password))
            if needs_rehash
            else None
        )
        user = (
            await db.execute(
                select(User)
                .where(User.id == snapshot.id, User.deleted_at.is_(None))
                # Every locked re-read repopulates: a row already in the
                # identity map would otherwise be returned with its pre-lock
                # column values, silently discarding the row this SELECT
                # locked specifically in order to read.
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if user is None or user.token_version != snapshot.token_version:
            # Reset/change/delete won during verification. The old credential
            # is no longer valid and the unauthenticated response stays generic.
            _record_login_failure(request, payload.email)
            raise invalid
        if user.password_hash != snapshot.password_hash:
            # Rewritten hash bytes under an UNCHANGED token_version can only
            # be another process's concurrent login transparently rehashing
            # this same credential (legacy pbkdf2 → Argon2id, or outdated
            # Argon2 parameters): every genuine change/reset/delete bumps
            # token_version and was caught above. Re-verify against the
            # reloaded hash instead of failing on raw byte inequality — a
            # correct password must not 401, and a benign format-only rehash
            # must not charge the account's brute-force ledger. The extra
            # verify runs while the row lock is held, an accepted cost on this
            # cross-worker race path that the per-email reservation already
            # makes rare in a single process.
            reverified, needs_current_rehash = await reservation.run(
                lambda: verify_password_async(payload.password, user.password_hash)
            )
            if not reverified:
                _record_login_failure(request, payload.email)
                raise invalid
            if not needs_current_rehash:
                # The concurrent winner already stored current-format
                # material; overwriting it with this request's equivalent
                # replacement would be gratuitous churn.
                replacement_hash = None
        if replacement_hash is not None:  # legacy pbkdf2 → Argon2id
            user.password_hash = replacement_hash
            logger.info("upgraded legacy pbkdf2 hash to Argon2id (user_id=%s)", user.id)
        if user.totp_state == "ACTIVE":
            # The password is proven, but an active TOTP enrollment demands the second factor before
            # ANY session material exists — no refresh cookie, no access token. The single-use
            # challenge token is short-lived and version-bound, so a password-only thief still
            # cannot reach the account.
            challenge = issue_token(
                user.id,
                "mfa",
                TOTP_CHALLENGE_TTL_SECONDS,
                extra_claims={"ver": user.token_version, "tablet_setup": payload.tablet_setup},
            )
            security_event(
                "auth.totp.challenge_issued",
                "password accepted; TOTP challenge demanded",
                session=db,
                user_id=user.id,
            )
            await db.commit()  # persist the hash upgrade and audit row atomically
            _reset_login_failures(request, payload.email)
            return LoginOut(mfa_token=challenge)
        out = await _issue_tokens(db, user, response, transient=payload.tablet_setup)
        await db.commit()
        _reset_login_failures(request, payload.email)
        return LoginOut(access_token=out.access_token, token_type=out.token_type, user=out.user)
    except asyncio.CancelledError:
        if password_work_started and not credential_accepted:
            _record_login_failure(request, payload.email)
        raise
    finally:
        reservation.release_when_idle()


@router.get("/worker-roster")
async def worker_roster(
    request: Request,
    db: DbSession,
    farm_id: Annotated[int, Query(ge=1, le=MAX_INT32_ID)],
    after_membership_id: Annotated[int, Query(ge=0, le=MAX_INT32_ID)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> WorkerRosterOut:
    """Names tap-to-sign-in offers on this farm's shared tablet.

    Unauthenticated by design (the tablet's first screen has no session) and
    hard-throttled per IP: this trades limited first/last-name enumeration per
    farm id for a PIN pad a field worker can actually use — the documented
    owner-operator tradeoff (README, worker tablet app).
    """
    # (Kept out of the docstring above so the OpenAPI snapshot — and the orval client
    # regeneration-locked to it — stays byte-identical.) The throttle has separate per-IP and
    # per-farm budgets, so rotating source addresses cannot reset the probe budget for one farm's
    # names.
    s = get_settings()
    if not s.worker_roster_enabled:
        # GOATFARM_WORKER_ROSTER_ENABLED=false: deployments with no shared
        # tablets close the enumeration oracle (README, worker tablet app).
        # Kept out of the docstring so the OpenAPI snapshot — and the orval
        # client regeneration-locked to it — stays byte-identical.
        raise HTTPException(status_code=404, detail="Worker roster is disabled.")
    if s.auth_rate_limit_enabled and auth_limiter.is_blocked(
        WORKER_ROSTER_SCOPE,
        _client_key(request),
        _WORKER_ROSTER_MAX_ATTEMPTS,
        s.auth_rate_limit_window_seconds,
    ):
        metrics.record_auth_rate_limit_rejection(WORKER_ROSTER_SCOPE)
        raise _too_many_attempts()
    if s.auth_rate_limit_enabled and worker_roster_farm_limiter.is_blocked(
        WORKER_ROSTER_FARM_SCOPE,
        str(farm_id),
        _WORKER_ROSTER_FARM_MAX_ATTEMPTS,
        s.auth_rate_limit_window_seconds,
    ):
        # The per-farm ceiling is charged by the farm id alone, never the source address, so this
        # 429 fires however many addresses the probing rotated through.
        metrics.record_auth_rate_limit_rejection(WORKER_ROSTER_FARM_SCOPE)
        raise _too_many_attempts()
    if s.auth_rate_limit_enabled:
        # The only record among the auth call sites that must be gated explicitly: with limiting
        # disabled, an unauthenticated roster probe must leave no limiter bookkeeping at all.
        auth_limiter.record(
            WORKER_ROSTER_SCOPE,
            _client_key(request),
            s.auth_rate_limit_window_seconds,
            max_attempts=_WORKER_ROSTER_MAX_ATTEMPTS,
        )
        worker_roster_farm_limiter.record(
            WORKER_ROSTER_FARM_SCOPE,
            str(farm_id),
            s.auth_rate_limit_window_seconds,
            max_attempts=_WORKER_ROSTER_FARM_MAX_ATTEMPTS,
        )
    rows = (
        await db.execute(
            select(FarmMembership.id, User)
            .join(User, FarmMembership.user_id == User.id)
            .where(
                FarmMembership.farm_id == farm_id,
                FarmMembership.id > after_membership_id,
                FarmMembership.is_active.is_(True),
                # PIN login is role-scoped by construction; the predicate
                # stays explicit so a future nullable role can't silently
                # widen the roster.
                FarmMembership.role_id.is_not(None),
                FarmMembership.pin_hash.is_not(None),
                User.deleted_at.is_(None),
                User.must_change_password.is_(False),
                User.totp_state.is_distinct_from("ACTIVE"),
                FarmMembership.role_id.in_(
                    select(Role.id).where(Role.farm_id == farm_id, Role.deleted_at.is_(None))
                ),
            )
            .order_by(FarmMembership.id)
            .limit(limit + 1)
        )
    ).all()
    return WorkerRosterOut(
        next_after_membership_id=int(rows[limit - 1].id) if len(rows) > limit else None,
        items=[
            WorkerRosterEntryOut(
                membership_id=int(row.id),
                # User.display_name falls back to the email when name is
                # NULL — this endpoint is unauthenticated, so a nameless
                # worker must never surface their email here. The fallback
                # reuses the membership id this payload already publishes,
                # disclosing nothing new and keeping taps distinguishable.
                display_name=row.User.name or f"Worker {row.id}",
            )
            for row in rows[:limit]
        ],
    )


@router.post(
    "/worker-login",
    dependencies=[Depends(_require_json_content_type)],
    responses=JSON_CONTENT_TYPE_ERROR_RESPONSE,
)
async def worker_login(
    payload: WorkerLoginIn,
    request: Request,
    response: Response,
    db: DbSession,
) -> LoginOut:
    """Shared-tablet quick sign-in: farm + tap + PIN, throttled like login.

    The PIN is scoped to one membership. It cannot bypass an active TOTP second
    factor or admit a farm owner. Invalid credentials return the same generic 401
    after equivalent Argon work to prevent timing-based enumeration of farms,
    memberships, or PINs.

    After a correct PIN, an account with active TOTP or a required password change
    receives a distinct 403 directing it to the appropriate authentication flow."""
    s = get_settings()
    identity_key = f"{_client_key(request)}|{payload.farm_id}|{payload.membership_id}"
    # IP-agnostic per-membership budget: rotating source addresses cannot reset
    # it. This is a bounded-window hard ceiling, so verification stops after
    # the budget rather than accepting the eventual correct guess.
    account_key = f"{payload.farm_id}|{payload.membership_id}"
    spray_key = f"{_client_key(request)}|{payload.farm_id}"

    def _hard_blocked() -> bool:
        return bool(
            s.auth_rate_limit_enabled
            and (
                auth_limiter.is_blocked(
                    WORKER_PIN_SCOPE,
                    identity_key,
                    s.worker_pin_rate_limit_max_attempts,
                    s.worker_pin_rate_limit_window_seconds,
                )
                or auth_limiter.is_blocked(
                    WORKER_PIN_SPRAY_SCOPE,
                    spray_key,
                    10 * s.worker_pin_rate_limit_max_attempts,
                    s.worker_pin_rate_limit_window_seconds,
                )
                or auth_limiter.is_blocked(
                    WORKER_PIN_ACCOUNT_SCOPE,
                    account_key,
                    s.worker_pin_rate_limit_max_attempts * WORKER_PIN_ACCOUNT_LIMIT_MULTIPLIER,
                    s.worker_pin_rate_limit_window_seconds,
                )
            )
        )

    if _hard_blocked():
        metrics.record_auth_rate_limit_rejection(WORKER_PIN_SCOPE)
        raise _too_many_attempts(s.worker_pin_rate_limit_window_seconds)

    generic = HTTPException(status_code=401, detail="Invalid PIN.")

    async def _charge_failure_ledgers() -> None:
        """Record one failed attempt in all three scopes + the security
        event. Shared by the 401/429 answer and the cancellation accounting
        below (login's rule: a disconnect mid-verify is a failed attempt)."""
        if s.auth_rate_limit_enabled:
            auth_limiter.record(
                WORKER_PIN_SCOPE,
                identity_key,
                s.worker_pin_rate_limit_window_seconds,
                max_attempts=s.worker_pin_rate_limit_max_attempts,
            )
            auth_limiter.record(
                WORKER_PIN_ACCOUNT_SCOPE,
                account_key,
                s.worker_pin_rate_limit_window_seconds,
                max_attempts=s.worker_pin_rate_limit_max_attempts
                * WORKER_PIN_ACCOUNT_LIMIT_MULTIPLIER,
            )
            auth_limiter.record(
                WORKER_PIN_SPRAY_SCOPE,
                spray_key,
                s.worker_pin_rate_limit_window_seconds,
                max_attempts=10 * s.worker_pin_rate_limit_max_attempts,
            )
        # Failure has no domain mutation to share, so release any credential
        # read/lock transaction. The fixed-cardinality signal preserves a
        # useful count without allowing guesses to force durable writes.
        await db.rollback()
        note_transient_security_signal("auth.worker_pin.login_failed")

    # Snapshot the credential scalars WITHOUT the row lock and end the read transaction, so Argon2
    # never holds a row lock, transaction, or checked-out DB connection — the same invariant login
    # states. The locked reload below exact-compares this snapshot before a token can be minted.
    row = (
        await db.execute(
            select(FarmMembership, User)
            .join(User, FarmMembership.user_id == User.id)
            .where(
                FarmMembership.id == payload.membership_id,
                FarmMembership.farm_id == payload.farm_id,
                FarmMembership.is_active.is_(True),
                FarmMembership.role_id.is_not(None),
                User.deleted_at.is_(None),
            )
        )
    ).first()
    membership_id: int | None = None
    user_id: int | None = None
    pin_hash: str | None = None
    token_version: int | None = None
    if row is not None:
        membership_id = row[0].id
        pin_hash = row[0].pin_hash
        user_id = row[1].id
        token_version = row[1].token_version
    await db.rollback()
    stored_hash = pin_hash if pin_hash is not None else _dummy_password_hash()
    pin_work_started = False
    pin_accepted = False
    reservation = _reserve_password_work(WORKER_PIN_RESERVATION_SCOPE, identity_key)
    try:
        # Re-check after the atomic admission reservation (login's rule): a preceding request may
        # have recorded the threshold immediately before releasing its slot; no expensive work
        # starts from a stale limiter observation.
        if _hard_blocked():
            metrics.record_auth_rate_limit_rejection(WORKER_PIN_SCOPE)
            raise _too_many_attempts(s.worker_pin_rate_limit_window_seconds)
        pin_work_started = True
        ok, _needs_rehash = await reservation.run(
            lambda: verify_password_async(payload.pin, stored_hash)
        )
        # Existing short PIN hashes may predate a tightened deployment floor.
        # Still spend the normal verification budget to preserve timing, but
        # never let a below-policy credential establish a new session.
        ok = ok and len(payload.pin) >= s.worker_pin_min_length
    except asyncio.CancelledError:
        # A disconnect mid-verify is a failed attempt: without this, paced
        # cancel/retry spends Argon2 work forever without ever reaching the
        # post-verification ledgers (login charges the same way).
        if pin_work_started and not pin_accepted:
            await _charge_failure_ledgers()
        raise
    finally:
        reservation.release_when_idle()

    async def _failed() -> NoReturn:
        await _charge_failure_ledgers()
        if s.auth_rate_limit_enabled and auth_limiter.is_blocked(
            WORKER_PIN_ACCOUNT_SCOPE,
            account_key,
            s.worker_pin_rate_limit_max_attempts * WORKER_PIN_ACCOUNT_LIMIT_MULTIPLIER,
            s.worker_pin_rate_limit_window_seconds,
        ):
            # The request that consumes the last allowance announces the
            # temporary lock immediately; later requests are rejected before
            # another expensive verification.
            metrics.record_auth_rate_limit_rejection(WORKER_PIN_ACCOUNT_SCOPE)
            raise _too_many_attempts(s.worker_pin_rate_limit_window_seconds)
        raise generic

    if membership_id is None or user_id is None or pin_hash is None or not ok:
        await _failed()
    # From this point cancellation is no longer free verification: the caller
    # proved the PIN, so a disconnect must not enter the failure ledgers
    # (login's credential_accepted rule).
    pin_accepted = True
    # Re-read under the row lock only after the PIN is proven, and revalidate
    # the snapshot before minting: a deactivation, deletion or credential
    # rotation that won during verification must not ride this proof (login
    # reloads and revalidates exactly the same way).
    locked_membership = (
        await db.execute(
            select(FarmMembership)
            .where(
                FarmMembership.id == membership_id,
                FarmMembership.farm_id == payload.farm_id,
                FarmMembership.user_id == user_id,
                FarmMembership.is_active.is_(True),
                FarmMembership.pin_hash == pin_hash,
            )
            .execution_options(populate_existing=True)
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if locked_membership is None:
        await _failed()
    row = (
        await db.execute(
            select(FarmMembership, User)
            .join(User, FarmMembership.user_id == User.id)
            .where(FarmMembership.id == membership_id, User.deleted_at.is_(None))
            .execution_options(populate_existing=True)
            .with_for_update(of=User)
        )
    ).first()
    if row is None:
        # Deactivated or deleted while the PIN was being verified: the pair
        # is unknown now, and the answer stays the generic failure.
        await _failed()
    user = row[1]
    live_role = (
        await db.execute(
            select(Role.id)
            .where(
                Role.id == locked_membership.role_id,
                Role.farm_id == payload.farm_id,
                Role.deleted_at.is_(None),
            )
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    farm_owner_id = (
        await db.execute(select(Farm.owner_id).where(Farm.id == payload.farm_id))
    ).scalar_one_or_none()
    if live_role is None or farm_owner_id == user.id:
        await _failed()
    if user.token_version != token_version:
        # Every PIN/password rotation and every deletion bumps token_version
        # under this same User lock, so the proven credential is stale — the
        # login reload-mismatch case, answered with the generic failure.
        await _failed()
    if user.totp_state == "ACTIVE":
        # The tablet is not an authenticator; a second-factor account must use
        # the password + TOTP flow.
        await db.rollback()
        raise HTTPException(
            status_code=403,
            detail="This account requires two-factor sign-in — use the password login.",
        )
    if user.must_change_password:
        # The account's password is still the owner-chosen one and every
        # domain route is fenced behind its rotation; a PIN session would be
        # a session that can do nothing.
        await db.rollback()
        raise HTTPException(
            status_code=403,
            detail="This account must change its password before PIN sign-in.",
        )
    out = await _issue_tokens(
        db, user, response, scope=SessionScope("PIN", payload.farm_id, membership_id)
    )
    security_event(
        "auth.worker_pin.login_succeeded",
        "worker signed in on the tablet by PIN",
        session=db,
        farm_id=payload.farm_id,
        membership_id=payload.membership_id,
        user_id=user.id,
    )
    await db.commit()
    if s.auth_rate_limit_enabled:
        # A success clears only the failure counts of the membership that authenticated; the (IP,
        # farm) spray bucket is NEVER reset by a success — same rule as _reset_login_failures, so
        # one valid PIN cannot refresh an attacker's spray budget.
        auth_limiter.reset(WORKER_PIN_SCOPE, identity_key)
        auth_limiter.reset(WORKER_PIN_ACCOUNT_SCOPE, account_key)
    return LoginOut(access_token=out.access_token, token_type=out.token_type, user=out.user)


@router.post("/refresh")
async def refresh(request: Request, response: Response, db: DbSession) -> TokenOut:
    _guard_cookie_request_origin(request)
    token = _refresh_cookie(request)
    _check_refresh_preverification_budget(request, token)
    claims = decode_refresh_claims(token) if token else None
    if claims is None:
        if token is not None and _refresh_token_expired_but_genuine(token):
            # Authentic-but-expired: reject without recording, matching the
            # access-token exemption — the ``refresh-invalid`` ledger is for
            # material that was never valid, not for a returning client whose
            # cookie simply outlived its TTL.
            raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
        _raise_invalid_refresh(request, token)
    if claims.expires_at <= utcnow():
        # PyJWT's bounded leeway authenticates a client whose clock is slightly
        # skewed, but the server-side session lifetime remains exact. Check the
        # signed expiry before any family revocation so an ordinary token that
        # crosses its boundary in flight is never misclassified as replay.
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    pin_role_id: int | None = None
    if claims.scope.origin == "PIN":
        pin_membership = (
            await db.execute(
                select(FarmMembership)
                .where(
                    FarmMembership.id == claims.scope.membership_id,
                    FarmMembership.user_id == claims.user_id,
                    FarmMembership.farm_id == claims.scope.farm_id,
                    FarmMembership.is_active.is_(True),
                    FarmMembership.pin_hash.is_not(None),
                )
                .execution_options(populate_existing=True)
                .with_for_update(read=True)
            )
        ).scalar_one_or_none()
        if pin_membership is None:
            raise HTTPException(status_code=401, detail="Tablet membership has been revoked")
        pin_role_id = pin_membership.role_id
    # Lock order for every auth/session mutation is User -> RefreshSession.
    # Password reset/change holds the same user lock before revoking sessions,
    # preventing a refresh from minting a successor after revocation.
    user = (
        await db.execute(
            select(User)
            .where(User.id == claims.user_id, User.deleted_at.is_(None))
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if user is None:
        _raise_invalid_refresh(request, token)
    if claims.scope.origin == "PIN":
        if pin_role_id is None:
            raise HTTPException(status_code=401, detail="Tablet membership has been revoked")
        live_role_id = (
            await db.execute(
                select(Role.id)
                .where(
                    Role.id == pin_role_id,
                    Role.farm_id == claims.scope.farm_id,
                    Role.deleted_at.is_(None),
                )
                .with_for_update(read=True)
            )
        ).scalar_one_or_none()
        if live_role_id is None:
            raise HTTPException(status_code=401, detail="Tablet membership has been revoked")
    # Lock the row: two concurrent refreshes presenting the same jti must not
    # both pass the consumption check.
    session = (
        await db.execute(
            select(RefreshSession)
            .where(RefreshSession.jti == claims.jti)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    now = utcnow()
    if claims.expires_at <= now:
        # The signed credential was live at decode but expired while this
        # request waited for the User/session locks. Repeat the signed boundary
        # before either an absent row is treated as replay or a drifted-later
        # persisted expiry could admit it.
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    if session is None:
        # New refresh tokens carry their signed family id. Consumed rows may
        # have been compacted, but replay still revokes the bounded current
        # family rather than degrading to a harmless 401 for the attacker.
        if claims.family_id is not None:
            revoked = await revoke_session_family(
                db,
                claims.family_id,
                user_id=claims.user_id,
            )
            if revoked:
                # this is the platform's strongest theft signal — a signed, once-valid credential
                # whose row is already gone. Only the request that changes family state is durable;
                # subsequent replays are bounded transient signals below.
                security_event(
                    "auth.refresh.family_revoked",
                    "compacted refresh replay revoked the family",
                    session=db,
                    user_id=claims.user_id,
                    family_id=claims.family_id,
                )
                await db.commit()
            else:
                await db.rollback()
        _raise_invalid_refresh(
            request,
            token,
        )  # predates session tracking, compacted, or never issued here
    if claims.family_id is not None and claims.family_id != session.family_id:
        _raise_invalid_refresh(request, token)
    if session.user_id != claims.user_id:
        _raise_invalid_refresh(request, token)
    if (session.session_origin, session.farm_id, session.membership_id) != claims.scope:
        _raise_invalid_refresh(request, token)
    if claims.scope.origin == "PIN" and (user.totp_state == "ACTIVE" or user.must_change_password):
        raise HTTPException(status_code=401, detail="Password sign-in is required")
    if session.expires_at <= now:
        # The signature, user and persisted JTI already proved this was a real
        # credential whose lifetime ended while/before the request was being
        # checked. Do not classify that expiry-boundary race as attacker input
        # or spend either invalid-token budget.
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    if session.revoked_at is not None:
        _raise_invalid_refresh(request, token)
    if session.consumed_at is not None:
        # A tiny grace window makes the same rotation idempotent across tabs.
        # The successor's fixed iat/exp/jti reconstruct the exact same signed
        # cookie. Bound both sides so a small backward wall-clock correction
        # between simultaneous requests does not become a false theft signal;
        # anything outside the symmetric window still revokes the family.
        grace = timedelta(seconds=get_settings().refresh_reuse_grace_seconds)
        successor = None
        elapsed = now - session.consumed_at
        if session.replacement_jti and -grace <= elapsed <= grace:
            successor = (
                await db.execute(
                    select(RefreshSession).where(
                        RefreshSession.jti == session.replacement_jti,
                        RefreshSession.family_id == session.family_id,
                        RefreshSession.user_id == session.user_id,
                    )
                )
            ).scalar_one_or_none()
        if (
            successor is not None
            and successor.revoked_at is None
            and successor.consumed_at is None
            and successor.expires_at > now
            and (successor.session_origin, successor.farm_id, successor.membership_id)
            == claims.scope
        ):
            remaining = max(0, int((successor.expires_at - now).total_seconds()))
            _set_refresh_cookie(
                response,
                issue_refresh_token(
                    user.id,
                    jti=successor.jti,
                    family_id=successor.family_id,
                    scope=claims.scope,
                    issued_at=successor.created_at,
                    expires_at=successor.expires_at,
                ),
                max_age=remaining,
            )
            return TokenOut(
                access_token=issue_access_token(
                    user.id, user.token_version, scope=claims.scope, family_id=session.family_id
                ),
                user=UserOut.model_validate(user),
            )
        await revoke_session_family(db, session.family_id, user_id=session.user_id)
        logger.warning(
            "refresh-token reuse detected — revoked family %s (user_id=%s)",
            session.family_id,
            session.user_id,
        )
        # cookie replay outside the rotation grace — a stolen refresh token was presented a second
        # time. Alertable.
        security_event(
            "auth.refresh.family_revoked",
            "refresh-token reuse outside the rotation grace revoked the family",
            session=db,
            user_id=session.user_id,
            family_id=session.family_id,
        )
        await db.commit()
        _raise_invalid_refresh(request, token)
    session.consumed_at = now
    out = await _issue_tokens(
        db, user, response, family_id=session.family_id, replacement_for=session, scope=claims.scope
    )
    await db.commit()
    return out


@router.post("/logout-session", status_code=204)
async def logout_session(request: Request, db: DbSession, user: CurrentUser) -> Response:
    """Cancel exactly the bearer session; a newer browser cookie is untouched."""
    actor_id = user.id
    expected_version = getattr(request.state, "authenticated_token_version", None)
    family_id = getattr(request.state, "authenticated_family_id", None)
    if not isinstance(family_id, str) or not isinstance(expected_version, int):
        raise HTTPException(status_code=401, detail="Session is no longer valid")
    locked = (
        await db.execute(
            select(User)
            .where(User.id == actor_id, User.deleted_at.is_(None))
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if locked is None:
        raise HTTPException(status_code=401, detail="Account no longer exists")
    await _require_authenticated_generation(db, request, locked, expected_version)
    revoked = await revoke_session_family(db, family_id, user_id=actor_id)
    if revoked:
        security_event(
            "auth.logout.session_revoked",
            "exact-session logout revoked the refresh family",
            session=db,
            user_id=actor_id,
            family_id=family_id,
            revoked_sessions=revoked,
        )
        await db.commit()
    else:
        # The bearer can outlive an already-cancelled refresh family. Keep
        # this idempotent endpoint from turning repeated valid requests into
        # no-op WAL/commit traffic.
        await db.rollback()
    return Response(status_code=204)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, db: DbSession) -> Response:
    _guard_cookie_request_origin(request)
    # Revoke the presented session server-side — deleting the
    # cookie alone leaves an exfiltrated token fully usable.
    token = _refresh_cookie(request)
    access_token = single_bearer_token(request)
    if token is not None:
        guard_invalid_token_verification_budget(
            request,
            INVALID_LOGOUT_REFRESH_TOKEN_SCOPE,
            token,
        )
    if access_token is not None:
        guard_invalid_token_verification_budget(
            request,
            INVALID_ACCESS_TOKEN_SCOPE,
            access_token,
        )
    claims = decode_refresh_claims(token) if token else None
    access_result = decode_access_claims_result(access_token) if access_token is not None else None
    access_claims = access_result.claims if access_result is not None else None
    if (
        claims is not None
        and access_claims is not None
        and (
            claims.user_id != access_claims.user_id
            or claims.scope != access_claims.scope
            or (claims.scope.origin == "PIN" and claims.family_id != access_claims.family_id)
        )
    ):
        # Cookie and bearer authentication are two proofs for one logout, not
        # a priority list. Silently preferring the cookie would revoke that
        # account while leaving the explicitly presented bearer account live.
        # Reject before acquiring either User/RefreshSession lock so neither
        # valid identity is mutated by an ambiguous composite request.
        raise HTTPException(
            status_code=401,
            detail="Refresh cookie and bearer token identify different accounts",
        )
    refresh_ip_blocked = False
    if (
        token is not None
        and claims is None
        # Same exemption as the expired-access branch below: an authentic,
        # merely-expired cookie is a returning client clearing local state,
        # not attacker input, and must not spend the invalid-token budget.
        and not _refresh_token_expired_but_genuine(token)
    ):
        note_transient_security_signal("auth.refresh.invalid")
        refresh_ip_blocked = record_invalid_token_verification(
            request,
            INVALID_LOGOUT_REFRESH_TOKEN_SCOPE,
            token,
        )
    access_ip_blocked = False
    if (
        access_token is not None
        and access_claims is None
        and not (access_result is not None and access_result.expired)
    ):
        note_transient_security_signal("auth.token.invalid")
        access_ip_blocked = record_invalid_token_verification(
            request,
            INVALID_ACCESS_TOKEN_SCOPE,
            access_token,
        )
    # Record both independently before returning a shared-IP throttle. A bad
    # companion token must not escape its own ledger merely because the other
    # component crossed the wider post-classification budget first.
    if refresh_ip_blocked:
        raise invalid_token_rate_error(request, INVALID_LOGOUT_REFRESH_TOKEN_SCOPE)
    if access_ip_blocked:
        raise invalid_token_rate_error(request, INVALID_ACCESS_TOKEN_SCOPE)
    candidate_user_id = claims.user_id if claims is not None else None
    if candidate_user_id is None and access_claims is not None:
        candidate_user_id = access_claims.user_id
    if candidate_user_id is None:
        # A request containing only absent/invalid/expired credentials has no
        # server-side state to mutate. Clear the browser cookie without even a
        # read-only ORM commit: hostile logout traffic must not manufacture a
        # transaction or an after-commit audit projection opportunity.
        _delete_refresh_cookie(response)
        response.status_code = 204
        return response
    logged_out_user = (
        (
            await db.execute(
                select(User)
                .where(User.id == candidate_user_id, User.deleted_at.is_(None))
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if candidate_user_id is not None
        else None
    )
    cookie_confirmed = False
    confirmed_family_id: str | None = None
    mutated = False
    if claims is not None and logged_out_user is not None:
        session = (
            await db.execute(
                select(RefreshSession)
                .where(RefreshSession.jti == claims.jti)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
        now = utcnow()
        if (
            session is not None
            and session.user_id == claims.user_id
            and (session.session_origin, session.farm_id, session.membership_id) == claims.scope
            and session.revoked_at is None
            and claims.expires_at > now
            and session.expires_at > now
        ):
            # Logout is a family boundary, not merely a one-JTI revocation.
            # If a concurrent refresh won the User lock first, its committed
            # successor is already in this family and is revoked by the same
            # UPDATE. It can never refresh successfully after this 204.
            revoked = await revoke_session_family(
                db,
                session.family_id,
                user_id=session.user_id,
            )
            candidate_user_id = session.user_id
            cookie_confirmed = revoked > 0
            if cookie_confirmed:
                confirmed_family_id = session.family_id
            mutated = mutated or cookie_confirmed
        elif session is None and claims.family_id is not None and claims.expires_at > now:
            # Rotation compaction deliberately removes old consumed rows. A
            # still-live, correctly signed predecessor nevertheless identifies
            # its family, so compaction must not turn logout into a local cookie
            # deletion that leaves the successor usable. Confirm that this
            # family still has a live row before revoking/bumping: a duplicate
            # logout then remains idempotent instead of advancing token_version
            # again merely because the compacted predecessor stays absent.
            live_family_session = (
                await db.execute(
                    select(RefreshSession.id)
                    .where(
                        RefreshSession.user_id == claims.user_id,
                        RefreshSession.family_id == claims.family_id,
                        RefreshSession.session_origin == claims.scope.origin,
                        RefreshSession.farm_id == claims.scope.farm_id,
                        RefreshSession.membership_id == claims.scope.membership_id,
                        RefreshSession.revoked_at.is_(None),
                        RefreshSession.expires_at > now,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if live_family_session is not None:
                revoked = await revoke_session_family(
                    db,
                    claims.family_id,
                    user_id=claims.user_id,
                )
                cookie_confirmed = revoked > 0
                if cookie_confirmed:
                    confirmed_family_id = claims.family_id
                mutated = mutated or cookie_confirmed
    bearer_family_live = False
    if logged_out_user is not None and access_claims is not None:
        bearer_family_live = (
            await db.execute(
                select(RefreshSession.id)
                .where(
                    RefreshSession.user_id == access_claims.user_id,
                    RefreshSession.family_id == access_claims.family_id,
                    RefreshSession.session_origin == access_claims.scope.origin,
                    RefreshSession.farm_id == access_claims.scope.farm_id,
                    RefreshSession.membership_id == access_claims.scope.membership_id,
                    RefreshSession.revoked_at.is_(None),
                    RefreshSession.expires_at > utcnow(),
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None
    if logged_out_user is not None:
        # A duplicate logout carrying only the now-stale access token must not
        # repeatedly advance the counter. A valid cookie already proved the
        # session even if its bearer header happened to be stale.
        bearer_current = bool(
            access_claims is not None
            and bearer_family_live
            and access_claims.user_id == candidate_user_id
            and access_claims.token_version == logged_out_user.token_version
        )
        scope = (
            claims.scope
            if cookie_confirmed and claims is not None
            else access_claims.scope
            if access_claims is not None
            else SessionScope()
        )
        if scope.origin == "PIN":
            if bearer_current and access_claims is not None and access_claims.family_id:
                revoked = await revoke_session_family(
                    db, access_claims.family_id, user_id=logged_out_user.id
                )
                mutated = mutated or revoked > 0
        elif cookie_confirmed or bearer_current:
            if bearer_current and not cookie_confirmed:
                # With no valid cookie there is no provable family to target.
                # Revoking only the access-token version would be reversible:
                # any surviving refresh family could immediately mint a token
                # carrying the new version. Session caps keep this update
                # finite, so bearer-only logout means logout everywhere.
                await revoke_user_sessions(db, logged_out_user.id)
            logged_out_user.token_version += 1
            mutated = True
    if mutated and logged_out_user is not None:
        # Emit once, atomically with the real revocation/version change.
        # Replayed, invalid and no-op logout requests never grow this ledger.
        security_event(
            "auth.logout.completed",
            "logout revoked authenticated session state",
            session=db,
            user_id=logged_out_user.id,
            session_origin=scope.origin,
            family_id=confirmed_family_id
            or (access_claims.family_id if access_claims is not None else None),
            revocation_scope="refresh_family"
            if scope.origin == "PIN"
            else "refresh_family_and_access_generation"
            if cookie_confirmed
            else "all_refresh_sessions_and_access_generation",
        )
        await db.commit()
    else:
        await db.rollback()
    _delete_refresh_cookie(response)
    response.status_code = 204
    return response


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordIn,
    request: Request,
    response: Response,
    db: DbSession,
    user: CurrentUser,
) -> TokenOut:
    """Self-service password change. Requires the current password,
    revokes EVERY outstanding refresh session, then
    issues a fresh pair so the current device stays signed in."""
    # Snapshot every scalar before rollback expires the dependency-loaded ORM
    # object. The auth route is exempt from generic unsafe-domain SHARE locks;
    # it performs its own exact write-lock revalidation after password work.
    user_id = user.id
    authenticated_token_version = user.token_version
    authenticated_password_hash = user.password_hash
    rate_key = f"{_client_key(request)}|{user_id}"
    # (composite scope, account scope, composite key, account id) for the
    # budget helpers above.
    scopes = (CHANGE_PASSWORD_SCOPE, ACCOUNT_PASSWORD_CONFIRM_ACCOUNT_SCOPE, rate_key, user_id)
    if _account_password_blocked(*scopes):
        raise _too_many_attempts()
    reservation = _reserve_password_work(ACCOUNT_PASSWORD_RESERVATION_SCOPE, str(user_id))
    try:
        if _account_password_blocked(*scopes):
            raise _too_many_attempts()
        # Release CurrentUser's read transaction/connection before Argon2.
        await db.rollback()
        try:
            ok, _needs_rehash = await reservation.run(
                lambda: verify_password_async(
                    payload.current_password,
                    authenticated_password_hash,
                )
            )
        except PasswordWorkCapacityError:
            # The global native pool rejected this workflow before doing work;
            # transient shared capacity pressure is not a password failure.
            raise
        except BaseException:
            # A disconnect can abandon the await after native work was
            # submitted. Charge before unwinding; the reservation callback
            # independently stays until that native job is truly idle.
            _record_account_password_attempt(*scopes)
            raise
        _record_account_password_attempt(*scopes)
        if not ok:
            if authenticated_password_hash.startswith(LEGACY_PBKDF2_PREFIX + "$"):
                await reservation.run(
                    lambda: verify_password_async(payload.current_password, _dummy_password_hash())
                )
            raise HTTPException(status_code=400, detail="Current password is incorrect.")
        # A rejected replacement still cost a full memory-hard verify. Charging
        # it (and clearing the budget only once the change commits) is what
        # stops an authenticated caller from looping this endpoint unthrottled
        # and holding a slot in the deliberately non-queuing Argon pool.
        if payload.new_password == payload.current_password:
            raise HTTPException(
                status_code=400,
                detail="New password must be different from the current password.",
            )
        error = password_policy_error(payload.new_password)
        if error:
            raise HTTPException(status_code=400, detail=error)
        replacement_hash = await reservation.run(lambda: hash_password_async(payload.new_password))

        locked_user = (
            await db.execute(
                select(User)
                .where(User.id == user_id, User.deleted_at.is_(None))
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked_user is None:
            raise HTTPException(status_code=401, detail="Account no longer exists")
        # token_version alone is the "credential still unchanged" signal,
        # exactly as create_farm revalidates: every genuine password
        # change/reset/delete bumps it under the same User lock. The raw
        # password_hash bytes are deliberately NOT compared — a concurrent
        # login's transparent legacy→Argon2id (or outdated-parameter) rehash
        # rewrites the bytes WITHOUT changing the credential, and byte
        # equality here turned that benign race into a spurious 401 for a
        # correct, unchanged current password.
        await _require_authenticated_generation(
            db, request, locked_user, authenticated_token_version
        )
        locked_user.password_hash = replacement_hash
        locked_user.token_version += 1
        # A completed self-service change proves sole possession of the
        # credential; owner-provisioned flags clear here and only here.
        locked_user.must_change_password = False
        await revoke_user_sessions(db, locked_user.id)
        out = await _issue_tokens(db, locked_user, response)  # new family, fresh session
        # A self-service credential change is the single most security-relevant lifecycle event
        # (every session died), yet it emitted nothing an operator could alert on. ids only, no PII,
        # same convention as the TOTP lifecycle events.
        security_event(
            "auth.password.changed",
            "Password changed (all sessions revoked)",
            session=db,
            user_id=locked_user.id,
        )
        await db.commit()
        # Only a completed change proves the caller knew the current password
        # AND consumed no further budget; clear it after the commit.
        _reset_account_password_attempts(*scopes)
        return out
    finally:
        reservation.release_when_idle()


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.get("/account/export")
async def export_account(response: Response, db: DbSession, user: CurrentUser) -> AccountExportOut:
    """Export the caller's identity and tenant relationships as JSON.

    Deliberately excludes farm-domain records: a worker's attribution to a
    transaction, animal, or duty does not entitle them to export another
    tenant's operational data. Farm owners receive farm metadata only here;
    domain-specific exports can be added separately with explicit tenancy
    authorization.
    """
    affiliation_cap = get_settings().max_account_affiliations_per_response
    owned = list(
        (
            await db.execute(
                select(Farm)
                .where(Farm.owner_id == user.id)
                .order_by(Farm.id)
                .limit(affiliation_cap + 1)
            )
        ).scalars()
    )
    if len(owned) > affiliation_cap:
        raise standing_quota(
            detail="Account has too many farm affiliations to return safely.",
        )
    remaining_affiliations = affiliation_cap - len(owned)
    memberships = list(
        (
            await db.execute(
                select(FarmMembership)
                .options(
                    # Separate SELECTs avoid a row-amplifying join and keep the
                    # relationship fields available after the query.
                    selectinload(FarmMembership.farm),
                    selectinload(FarmMembership.role),
                )
                .where(FarmMembership.user_id == user.id)
                .order_by(FarmMembership.farm_id)
                .limit(remaining_affiliations + 1)
            )
        ).scalars()
    )
    if len(memberships) > remaining_affiliations:
        raise standing_quota(
            detail="Account has too many farm affiliations to return safely.",
        )
    response.headers["Content-Disposition"] = (
        f'attachment; filename="goatfarm-account-{user.id}.json"'
    )
    # This response contains personal data, so it leaves the same durable security-event trail the
    # DPR download got (DET-2, planner.dpr.download) — ids only, never the exported PII itself.
    security_event(
        "auth.account.exported",
        "account identity and tenant relationships exported",
        session=db,
        user_id=user.id,
    )
    await db.commit()
    return AccountExportOut(
        exported_at=utcnow(),
        account=AccountIdentityExport(
            id=user.id,
            email=user.email,
            name=user.name,
            created_at=user.created_at,
        ),
        owned_farms=[
            OwnedFarmExport(
                id=farm.id,
                name=farm.name,
                location=farm.location,
                timezone=farm.timezone,
                created_at=farm.created_at,
            )
            for farm in owned
        ],
        memberships=[
            MembershipExport(
                farm_id=membership.farm_id,
                farm_name=membership.farm.name,
                role_id=membership.role_id,
                role_name=membership.role.name,
                is_active=membership.is_active,
                created_at=membership.created_at,
            )
            for membership in memberships
        ],
    )


@router.delete("/account", status_code=204)
async def delete_account(
    payload: AccountDeleteIn,
    request: Request,
    response: Response,
    db: DbSession,
    user: CurrentUser,
) -> Response:
    """Remove account access and profile data after password confirmation.

    Farm operational and audit rows retain a pseudonymous actor reference so
    attributed history is not silently rewritten when a worker leaves.
    """
    user_id = user.id
    authenticated_token_version = user.token_version
    authenticated_password_hash = user.password_hash
    rate_key = f"{_client_key(request)}|{user_id}"
    # (composite scope, account scope, composite key, account id) for the
    # budget helpers above.
    scopes = (ACCOUNT_DELETE_SCOPE, ACCOUNT_PASSWORD_CONFIRM_ACCOUNT_SCOPE, rate_key, user_id)
    if _account_password_blocked(*scopes):
        raise _too_many_attempts()
    reservation = _reserve_password_work(ACCOUNT_PASSWORD_RESERVATION_SCOPE, str(user_id))
    try:
        if _account_password_blocked(*scopes):
            raise _too_many_attempts()
        # CurrentUser performed only an unlocked read for this exempt auth
        # lifecycle route. End that transaction before password verification.
        await db.rollback()
        try:
            ok, _needs_rehash = await reservation.run(
                lambda: verify_password_async(
                    payload.current_password,
                    authenticated_password_hash,
                )
            )
        except PasswordWorkCapacityError:
            raise
        except BaseException:
            _record_account_password_attempt(*scopes)
            raise
        _record_account_password_attempt(*scopes)
        if not ok:
            if authenticated_password_hash.startswith(LEGACY_PBKDF2_PREFIX + "$"):
                await reservation.run(
                    lambda: verify_password_async(payload.current_password, _dummy_password_hash())
                )
            raise HTTPException(status_code=400, detail="Current password is incorrect.")

        # Produce unusable replacement material before acquiring the User
        # write lock. Exact snapshot comparison below discards it safely if a
        # concurrent reset/change/delete won during either Argon operation.
        tombstone_password_hash = await reservation.run(
            lambda: hash_password_async(uuid.uuid4().hex + uuid.uuid4().hex)
        )
        tombstone_email = f"deleted-{user_id}-{uuid.uuid4().hex}@deleted.invalid"
        locked_user = (
            await db.execute(
                select(User)
                .where(User.id == user_id, User.deleted_at.is_(None))
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked_user is None:
            raise HTTPException(status_code=401, detail="Account no longer exists")
        # token_version-only, matching change_password: a concurrent login's
        # benign format-only rehash rewrites password_hash bytes without
        # changing the credential and must not fail this deletion, while
        # every genuine credential mutation bumps token_version.
        await _require_authenticated_generation(
            db, request, locked_user, authenticated_token_version
        )

        owns_farm = (
            await db.execute(select(Farm.id).where(Farm.owner_id == locked_user.id).limit(1))
        ).scalar_one_or_none()
        if owns_farm is not None:
            # Two Argon2 runs already happened. The admission charge remains in
            # place; only a completed deletion clears it below.
            raise lifecycle_conflict(
                detail=(
                    "Account deletion is unavailable while this account owns a farm; "
                    "transfer ownership to a password-enabled active team member first."
                ),
            )

        # User.deleted_at is the synchronous authorization barrier. Retained
        # memberships remain immutable FK/audit anchors and are deactivated by
        # a finite SKIP LOCKED background worker; this request never enumerates
        # or locks an account's unbounded tenant history.
        await db.execute(delete(RefreshSession).where(RefreshSession.user_id == locked_user.id))
        locked_user.deleted_at = utcnow()
        locked_user.email = tombstone_email
        locked_user.name = None
        locked_user.password_hash = tombstone_password_hash
        locked_user.token_version += 1
        locked_user.totp_secret_enc = None
        locked_user.totp_state = None
        locked_user.totp_last_step = None
        locked_user.must_change_password = False
        # Deletion is an irreversible identity event and needs the same operator-visible trail as a
        # password change (ids only; the tombstoned email is deliberately never logged).
        security_event(
            "auth.account.deleted",
            "Account deleted",
            session=db,
            user_id=locked_user.id,
        )
        await db.commit()
        _reset_account_password_attempts(*scopes)

        _delete_refresh_cookie(response)
        response.status_code = 204
        return response
    finally:
        reservation.release_when_idle()


@router.get("/permissions")
async def permissions(
    request: Request,
    user: CurrentUser,
    farm: CurrentFarm,
    membership: CurrentMembership,
    perms: CurrentPerms,
) -> PermissionsOut:
    return PermissionsOut(
        is_owner=farm.owner_id == user.id
        and getattr(request.state, "authenticated_scope", SessionScope()).origin != "PIN",
        permissions=sorted(perms),
    )


@router.get("/farms")
async def list_farms(request: Request, db: DbSession, user: CurrentUser) -> list[FarmOut]:
    scope = getattr(request.state, "authenticated_scope", SessionScope())
    pairs: list[tuple[Farm, str | None]]
    if scope.origin == "PIN":
        if scope.farm_id is None:
            raise HTTPException(status_code=401, detail="Invalid tablet scope")
        membership = await active_membership(db, user.id, scope.farm_id)
        if membership is None or membership.id != scope.membership_id:
            raise HTTPException(status_code=401, detail="Tablet membership has been revoked")
        farm = await db.get(Farm, scope.farm_id)
        if farm is None:
            raise HTTPException(status_code=404, detail="Farm not found")
        pairs = [(farm, membership.role.name)]
    else:
        pairs = await accessible_farms(db, user)
    return [
        FarmOut(
            id=f.id,
            name=f.name,
            location=f.location,
            timezone=f.timezone,
            role=role,
        )
        for f, role in pairs
    ]


@router.post("/farms/{farm_id}/transfer-ownership", status_code=200)
async def transfer_farm_ownership(
    farm_id: int,
    payload: FarmOwnershipTransferIn,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> FarmOut:
    """Transfer the farm to an active member before deleting the former owner's account.

    The successor must already have taken sole possession of a password.
    Operational records retain their farm and actor IDs throughout transfer.
    """
    if not 1 <= farm_id <= MAX_INT32_ID:
        raise HTTPException(status_code=404, detail="Farm not found")
    actor_id, expected_version = user.id, user.token_version
    owned = (
        await db.execute(select(Farm.id).where(Farm.id == farm_id, Farm.owner_id == actor_id))
    ).scalar_one_or_none()
    if owned is None:
        raise HTTPException(status_code=404, detail="Farm not found")
    await _confirm_current_password(
        db, request, payload.current_password, user, scope=ACCOUNT_DELETE_SCOPE
    )
    # Ownership changes are rare. Serialize transfers before taking any row
    # lock, preventing reciprocal cross-farm transfers from inverting users.
    # Then follow manager authorization's actor User -> Farm -> Membership ->
    # target User order, matching reset and roster mutations.
    await db.execute(text("SELECT pg_advisory_xact_lock(130013003)"))
    actor = (
        await db.execute(
            select(User)
            .where(User.id == actor_id, User.deleted_at.is_(None))
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if actor is None:
        raise HTTPException(status_code=401, detail="Account no longer exists")
    await _require_authenticated_generation(db, request, actor, expected_version)
    farm = (
        await db.execute(
            select(Farm)
            .where(Farm.id == farm_id, Farm.owner_id == actor_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if farm is None:
        raise HTTPException(status_code=404, detail="Farm not found")
    member = (
        await db.execute(
            select(FarmMembership)
            .where(
                FarmMembership.id == payload.membership_id,
                FarmMembership.farm_id == farm_id,
                FarmMembership.is_active.is_(True),
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if member is None or member.user_id == actor_id:
        raise lifecycle_conflict(detail="Choose another active team member.")
    try:
        successor = (
            await db.execute(
                select(User)
                .where(User.id == member.user_id)
                .execution_options(populate_existing=True)
                # The successor can own another farm. Its roster operations
                # lock that actor before targeting this transfer's actor;
                # waiting here would create a reciprocal User-lock cycle.
                .with_for_update(nowait=True)
            )
        ).scalar_one_or_none()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != "55P03":
            raise
        await db.rollback()
        raise lifecycle_conflict(
            detail="The selected team member's account is busy; retry the ownership transfer.",
        ) from None
    if successor is None or successor.deleted_at is not None or successor.must_change_password:
        raise lifecycle_conflict(
            detail="The new owner must sign in and rotate their password first."
        )
    # PIN-only provisioning stores an unknown random password. It cannot take
    # owner authority until a password-enabled membership is selected.
    if member.account_provisioned_by_farm and member.pin_hash is not None:
        raise lifecycle_conflict(
            detail="Choose a password-enabled team member without a tablet PIN."
        )
    owned_count = (
        await db.execute(
            select(func.count()).select_from(Farm).where(Farm.owner_id == successor.id)
        )
    ).scalar_one()
    if owned_count >= get_settings().max_farms_per_user:
        raise standing_quota(detail="The new owner already owns the maximum number of farms.")
    farm.owner_id = successor.id
    successor.token_version += 1
    await revoke_user_sessions(db, successor.id)
    _reset_account_password_attempts(
        ACCOUNT_DELETE_SCOPE,
        ACCOUNT_PASSWORD_CONFIRM_ACCOUNT_SCOPE,
        f"{_client_key(request)}|{actor_id}",
        actor_id,
    )
    security_event(
        "auth.farm.ownership_transferred",
        "Farm ownership transferred",
        session=db,
        farm_id=farm_id,
        user_id=actor_id,
        new_owner_id=successor.id,
    )
    await db.commit()
    return FarmOut(
        id=farm.id, name=farm.name, location=farm.location, timezone=farm.timezone, role=None
    )


@router.post("/farms", status_code=201)
async def create_farm(
    payload: FarmCreateIn,
    response: Response,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    idempotency_key: RequiredIdempotencyKey,
) -> FarmOut:
    # Snapshot before the first await: the locked populate-existing query below
    # refreshes the same User object. This closes reset/logout -> stale
    # create-farm races where a revoked request could otherwise acquire new
    # ownership after the resetter's affiliation check had already passed.
    authenticated_token_version = user.token_version
    name = payload.name.strip()
    if not name:
        # Same rule as animal tags: whitespace-only is not a name.
        raise HTTPException(status_code=400, detail="Name is required.")
    limit = get_settings().max_farms_per_user
    # Lock the user row before inserting an actor-scoped idempotency claim.
    # Besides serializing the quota count, this lock order prevents distinct
    # keyed requests from first taking an actor-FK KEY SHARE lock and then
    # deadlocking while both try to upgrade it to FOR UPDATE.
    locked_user = (
        await db.execute(
            select(User)
            .where(User.id == user.id, User.deleted_at.is_(None))
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if locked_user is None:
        raise HTTPException(status_code=401, detail="Account no longer exists")
    await _require_authenticated_generation(db, request, locked_user, authenticated_token_version)

    async def mutate() -> FarmOut:
        owned = (
            await db.execute(
                select(func.count()).select_from(Farm).where(Farm.owner_id == locked_user.id)
            )
        ).scalar_one()
        if owned >= limit:
            raise HTTPException(
                status_code=400,
                detail=f"You already own the maximum of {limit} farms.",
            )
        farm = Farm(
            name=name,
            location=(payload.location or "").strip() or None,
            timezone=payload.timezone,
            owner_id=locked_user.id,
        )
        db.add(farm)
        await db.flush()
        await seed_new_farm(db, farm)  # preset roles + feed inventory
        return FarmOut(
            id=farm.id,
            name=farm.name,
            location=farm.location,
            timezone=farm.timezone,
            role=None,
        )

    return await execute_idempotent(
        db,
        http_response=response,
        key=idempotency_key,
        farm_id=None,
        actor_id=locked_user.id,
        operation=CREATE_FARM_IDEMPOTENCY_OPERATION,
        payload=payload,
        path_identity={},
        success_status=201,
        response_type=FarmOut,
        mutate=mutate,
    )


# --- TOTP second factor  ----------------------
#
# Opt-in two-factor authentication for any account (recommended for owners: an owner is god-mode and
# password-only phishing was the audit's top real-world risk). The secret never touches the database
# in plaintext and the challenge path is single-use, version-bound and strictly throttled.

TOTP_ENROLL_SCOPE = "totp-enroll"
TOTP_CHALLENGE_USER_SCOPE = "totp-challenge"
TOTP_CONFIRM_USER_SCOPE = "totp-confirm"
# Disable and confirm have independent guess budgets so failed confirmation attempts cannot lock a
# user out of disabling their factor, or vice versa.
TOTP_DISABLE_USER_SCOPE = "totp-disable"
TOTP_RECOVERY_REGEN_SCOPE = "totp-recovery-regen"
# Recovery-code redemption runs up to ten sequential Argon2 verifies in one request, so it takes the
# same one-per-account work reservation as the other credential workflows.
TOTP_RECOVERY_RESERVATION_SCOPE = "totp-recovery-work"
# Garbage mfa_tokens each cost an RS256 verify, so the challenge path carries
# the same pre-verification budget as /refresh (REFRESH_PREVERIFY_SCOPE): a
# per-token bucket keyed on a hash of the PRESENTED token refuses a repeat of
# known-bad material before PyJWT, and the wider per-IP ledger bounds rotating
# garbage. Both are written only after the presented token actually fails
# verification, so a shared-NAT neighbour's history can never 429 a
# still-valid challenge.
TOTP_CHALLENGE_PREVERIFY_SCOPE = "totp-challenge-preverify"
TOTP_CHALLENGE_INVALID_SCOPE = "totp-challenge-invalid"
# Worker-tablet PIN login: one scope for the exact (IP, farm, membership) identity, one IP-AGNOSTIC
# per-membership ceiling, and one spray scope for the whole (IP, farm) pair so spraying many
# memberships cannot multiply the budget.
WORKER_PIN_SCOPE = "worker-pin"
WORKER_PIN_ACCOUNT_SCOPE = "worker-pin-account"
WORKER_PIN_ACCOUNT_LIMIT_MULTIPLIER = 3
WORKER_PIN_SPRAY_SCOPE = "worker-pin-spray"
WORKER_PIN_RESERVATION_SCOPE = "worker-pin-work"
WORKER_ROSTER_SCOPE = "worker-roster"
_WORKER_ROSTER_MAX_ATTEMPTS = 30
# A farm-wide budget caps roster probing even when the source IP rotates. Keep it equal to the
# per-IP allowance so one tablet's budget is unchanged.
WORKER_ROSTER_FARM_SCOPE = "worker-roster-farm"
_WORKER_ROSTER_FARM_MAX_ATTEMPTS = 30
# Challenge codes are 6 digits: 5 attempts / 5 minutes per account makes
# exhaustive guessing ~700 years; per-IP composite mirrors login.
TOTP_CHALLENGE_MAX_ATTEMPTS = 5
TOTP_SECRET_UNAVAILABLE_DETAIL = (
    "Two-factor authentication is temporarily unavailable. Contact an administrator."
)


async def _decrypt_totp_secret_or_unavailable(
    db: AsyncSession,
    encrypted: bytes,
) -> DecryptedTotpSecret:
    """Read TOTP material or return a deliberately non-diagnostic 503.

    An AEAD authentication failure can mean an interrupted legacy migration,
    a ciphertext integrity failure, or an operator rotated JWTs before
    running the rekey job. It must never turn into a 500, and we must not
    consume the MFA challenge token because a repaired configuration should
    allow the user to retry it within its normal TTL.
    """
    try:
        return decrypt_totp_secret_with_metadata(encrypted)
    except TotpSecretUnavailableError:
        await db.rollback()
        note_transient_security_signal("auth.totp.secret_unavailable")
        raise HTTPException(status_code=503, detail=TOTP_SECRET_UNAVAILABLE_DETAIL) from None


# Single-use challenge tokens: each successful exchange claims its jti in
# ``consumed_mfa_challenges`` durably, including across process restarts. The claim rides the
# login-success transaction, so committed consumption and a successful exchange are the same event:
# a request that fails later in the handler rolls the claim back and the challenge stays retryable,
# while a committed success is consumed for good — restart or not (RFC 6238: the verifier MUST
# detect replay of a used exchange).
async def _consume_mfa_jti(db: AsyncSession, jti: str, expires_at: datetime) -> bool:
    """Claim a challenge token's single use; False when it was already used.

    INSERT ... ON CONFLICT DO NOTHING arbitrates concurrent replays: exactly
    one session inserts, the others block on the unique jti until that
    transaction commits (then see the conflict) or rolls back (then claim it
    themselves). The opportunistic expired-row sweep shares the statement's
    transaction, keeping the table bounded by one TTL window of successful
    exchanges; it is rolled back with the claim on the replay path, which is
    harmless — the next consume anywhere sweeps again.
    """
    await db.execute(delete(ConsumedMfaChallenge).where(ConsumedMfaChallenge.expires_at < utcnow()))
    claimed = (
        await db.execute(
            pg_insert(ConsumedMfaChallenge)
            .values(jti=jti, expires_at=expires_at)
            .on_conflict_do_nothing(index_elements=[ConsumedMfaChallenge.jti])
            .returning(ConsumedMfaChallenge.jti)
        )
    ).scalar_one_or_none()
    return claimed is not None


async def _require_authenticated_generation(
    db: AsyncSession,
    request: Request,
    user: User,
    authenticated_token_version: int,
) -> None:
    """The final exclusive account lock must still belong to the signed generation."""
    if user.token_version != authenticated_token_version:
        raise HTTPException(status_code=401, detail="Session is no longer valid")
    await require_live_authenticated_session(db, request, user)


async def _confirm_current_password(
    db: AsyncSession,
    request: Request,
    payload_password: str,
    user: User,
    *,
    scope: str,
) -> None:
    """Shared password confirmation for TOTP enrollment/disable.

    Mirrors change-password's budgeting (shared per-account ceiling, so
    endpoint-switching cannot double the guessing rate) and its
    lock-free-verify-then-reload shape."""
    user_id = user.id
    password_hash = user.password_hash
    rate_key = f"{_client_key(request)}|{user_id}"
    scopes = (scope, ACCOUNT_PASSWORD_CONFIRM_ACCOUNT_SCOPE, rate_key, user_id)
    if _account_password_blocked(*scopes):
        raise _too_many_attempts()
    reservation = _reserve_password_work(ACCOUNT_PASSWORD_RESERVATION_SCOPE, str(user_id))
    try:
        if _account_password_blocked(*scopes):
            raise _too_many_attempts()
        await db.rollback()  # never hold a transaction over Argon2
        ok, _needs_rehash = await reservation.run(
            lambda: verify_password_async(payload_password, password_hash)
        )
        _record_account_password_attempt(*scopes)
        if not ok:
            raise HTTPException(status_code=400, detail="Current password is incorrect.")
    finally:
        reservation.release_when_idle()


def _otpauth_uri(secret_b32: str, email: str) -> str:
    label = urllib.parse.quote(f"Herdly:{email}", safe=":")
    return (
        f"otpauth://totp/{label}"
        f"?secret={secret_b32}&issuer=Herdly&algorithm=SHA1&digits=6&period=30"
    )


@router.post("/totp/enroll", status_code=200)
async def totp_enroll(
    payload: TotpEnrollIn,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> TotpEnrollOut:
    """Begin enrollment: confirm the current password, generate a fresh
    secret (stored PENDING — not yet demanded at login) and return it with an
    otpauth:// URI. On phones the URI link opens the authenticator app
    directly; the secret text remains for manual entry."""
    # The password confirmation rolls the session back; snapshot first.
    user_id = user.id
    # Signed credential generation the request authenticated under — the
    # rollback inside _confirm_current_password expires the dependency-loaded
    # ORM object, so the scalar must be captured now, exactly the way
    # change_password snapshots its `authenticated_token_version` before its
    # own Argon2 path.
    authenticated_token_version = user.token_version
    await _confirm_current_password(
        db, request, payload.current_password, user, scope=TOTP_ENROLL_SCOPE
    )
    locked = (
        await db.execute(
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if locked is None:
        # The account vanished between authentication and this locked reload
        # (scalar_one would have raised NoResultFound → 500 here).
        raise HTTPException(status_code=401, detail="Account no longer exists.")
    # token_version revalidation mirrors change_password: the password was
    # confirmed against the pre-rollback snapshot, and every genuine credential
    # mutation bumps token_version under this same User lock — a password
    # proof that has since gone stale must not mint a fresh PENDING enrollment.
    await _require_authenticated_generation(db, request, locked, authenticated_token_version)
    # Password-only proof must never retire an ACTIVE second factor: re-enrolling over ACTIVE used
    # to silently replace the enrolled secret, so a phished password alone could swap away a factor
    # the thief could not satisfy. Disable demands a currently-valid code from the enrolled secret;
    # route re-enrollers through it first. Re-rolling a merely PENDING (never confirmed) enrollment
    # stays allowed.
    if locked.totp_state == "ACTIVE":
        raise lifecycle_conflict(
            detail=(
                "Two-factor is already enabled. Disable it (with a current code) "
                "before starting a new enrollment."
            ),
        )
    secret = generate_totp_secret_b32()
    locked.totp_secret_enc = encrypt_totp_secret(secret)
    locked.totp_state = "PENDING"
    locked.totp_last_step = None
    security_event(
        "auth.totp.enroll_started",
        "TOTP enrollment started (pending confirmation)",
        session=db,
        user_id=locked.id,
    )
    await db.commit()
    # A completed enrollment proves the caller knew the current password — the confirmation budget's
    # own contract ("successful commits clear the counters") must hold here like it does in
    # change_password and delete_account, or a user toggling 2FA inside one rate-limit window crawls
    # toward the composite ceiling on correct passwords.
    _reset_account_password_attempts(
        TOTP_ENROLL_SCOPE,
        ACCOUNT_PASSWORD_CONFIRM_ACCOUNT_SCOPE,
        f"{_client_key(request)}|{user_id}",
        user_id,
    )
    return TotpEnrollOut(secret=secret, otpauth_uri=_otpauth_uri(secret, locked.email))


async def _prepare_totp_recovery_codes() -> tuple[list[str], list[str]]:
    """Generate and hash a recovery-code set without a DB transaction.

    Ten Argon2 hashes are intentionally completed before acquiring the final
    user row lock. The caller must then re-lock and revalidate every security-
    relevant snapshot field before atomically replacing the stored set.
    """
    codes = [generate_totp_recovery_code() for _ in range(TOTP_RECOVERY_CODE_COUNT)]
    hashes = await hash_totp_recovery_codes_async(codes)
    return codes, hashes


async def _replace_totp_recovery_codes(db: AsyncSession, user_id: int, hashes: list[str]) -> None:
    """Atomically replace hashes inside the caller's final locked transaction."""
    await db.execute(delete(TotpRecoveryCode).where(TotpRecoveryCode.user_id == user_id))
    for digest in hashes:
        db.add(TotpRecoveryCode(user_id=user_id, code_hash=digest))


@router.post("/totp/confirm")
async def totp_confirm(
    payload: TotpCodeIn,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> TotpRecoveryCodesOut:
    """Finish enrollment by proving possession of the PENDING secret.

    A valid code activates the second factor and returns the one-time recovery
    codes exactly once. The codes cannot be recovered later, so clients must
    offer a copy or print action immediately."""
    user_id = user.id
    authenticated_token_version = user.token_version
    snapshot = (
        await db.execute(
            select(
                User.token_version, User.totp_state, User.totp_secret_enc, User.totp_last_step
            ).where(User.id == user_id, User.deleted_at.is_(None))
        )
    ).one_or_none()
    if snapshot is None:
        # Concurrently deleted account: scalar_one would raise NoResultFound → 500 here.
        raise HTTPException(status_code=401, detail="Account no longer exists.")
    if snapshot.token_version != authenticated_token_version:
        raise HTTPException(status_code=401, detail="Session is no longer valid")
    if snapshot.totp_state != "PENDING" or snapshot.totp_secret_enc is None:
        raise lifecycle_conflict(detail="Start enrollment first.")
    # Same guess budget as the login challenge: a stolen access token must not get an unthrottled
    # 6-digit oracle here either.
    s = get_settings()
    composite_key = f"{_client_key(request)}|{user_id}"
    if s.auth_rate_limit_enabled and (
        auth_limiter.is_blocked(
            TOTP_CONFIRM_USER_SCOPE,
            str(user_id),
            TOTP_CHALLENGE_MAX_ATTEMPTS,
            s.auth_rate_limit_window_seconds,
        )
        or auth_limiter.is_blocked(
            TOTP_CONFIRM_USER_SCOPE,
            composite_key,
            TOTP_CHALLENGE_MAX_ATTEMPTS,
            s.auth_rate_limit_window_seconds,
        )
    ):
        metrics.record_auth_rate_limit_rejection(TOTP_CONFIRM_USER_SCOPE)
        await db.rollback()
        raise _too_many_attempts()
    encrypted = snapshot.totp_secret_enc
    decrypted = await _decrypt_totp_secret_or_unavailable(db, encrypted)
    matched = verify_totp_code(decrypted.secret, payload.code, at=utcnow(), last_used_step=None)
    if matched is None:
        auth_limiter.record(
            TOTP_CONFIRM_USER_SCOPE,
            str(user_id),
            s.auth_rate_limit_window_seconds,
            max_attempts=TOTP_CHALLENGE_MAX_ATTEMPTS,
        )
        auth_limiter.record(
            TOTP_CONFIRM_USER_SCOPE,
            composite_key,
            s.auth_rate_limit_window_seconds,
            max_attempts=TOTP_CHALLENGE_MAX_ATTEMPTS,
        )
        await db.rollback()
        raise HTTPException(status_code=400, detail="That code is not valid right now.")
    # Release the read transaction and pool checkout before ten Argon2 hashes.
    # The exact snapshot is revalidated under the final row lock below.
    await db.rollback()
    codes, hashes = await _prepare_totp_recovery_codes()
    locked = (
        await db.execute(
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if locked is None:
        raise HTTPException(status_code=401, detail="Account no longer exists.")
    await _require_authenticated_generation(db, request, locked, authenticated_token_version)
    if (
        locked.totp_state != "PENDING"
        or locked.totp_secret_enc != encrypted
        or locked.totp_last_step != snapshot.totp_last_step
    ):
        await db.rollback()
        raise stale_state_conflict(detail="Two-factor enrollment changed; try again.")
    locked.totp_state = "ACTIVE"
    locked.totp_last_step = matched
    if decrypted.needs_rewrap:
        locked.totp_secret_enc = encrypt_totp_secret(decrypted.secret)
    await _replace_totp_recovery_codes(db, locked.id, hashes)
    security_event(
        "auth.totp.enabled",
        "TOTP second factor activated; recovery codes minted",
        session=db,
        user_id=locked.id,
    )
    await db.commit()
    if s.auth_rate_limit_enabled:
        auth_limiter.reset(TOTP_CONFIRM_USER_SCOPE, str(user_id))
        auth_limiter.reset(TOTP_CONFIRM_USER_SCOPE, composite_key)
    return TotpRecoveryCodesOut(codes=codes)


@router.post("/totp/disable", status_code=204)
async def totp_disable(
    payload: TotpDisableIn,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> Response:
    """Remove the second factor: requires BOTH the current password and a
    currently-valid code (or, for an unconfirmed PENDING enrollment, the
    password alone — nothing is gating login yet)."""
    user_id = user.id  # _confirm_current_password rolls back and expires `user`
    authenticated_token_version = user.token_version
    # Same guess budget as confirm: the ACTIVE-state code check below is a 6-digit oracle and its
    # wrong-code path used to record nothing, so a stolen access token could grind it unthrottled.
    # Both keys are derived from the pre-rollback user_id snapshot for that reason.
    s = get_settings()
    composite_key = f"{_client_key(request)}|{user_id}"
    # The password confirmation cannot hold the row lock (it rolls back so Argon2 never runs inside
    # a transaction), so the state decision must be re-taken under a freshly acquired lock
    # afterwards. Loop until the state seen under the final lock matches the checks already paid
    # for; the retry budget bounds pathological concurrent toggling with a 409 instead of an
    # unhandled assertion failure.
    password_confirmed = False
    for _ in range(3):
        locked = (
            await db.execute(
                select(User)
                .where(User.id == user_id, User.deleted_at.is_(None))
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked is None:
            # Concurrently deleted account: scalar_one would raise NoResultFound → 500 here.
            raise HTTPException(status_code=401, detail="Account no longer exists.")
        await _require_authenticated_generation(db, request, locked, authenticated_token_version)
        state = locked.totp_state
        if state is None or locked.totp_secret_enc is None:
            raise lifecycle_conflict(detail="Two-factor is not enrolled.")
        if not password_confirmed:
            await _confirm_current_password(
                db, request, payload.current_password, locked, scope=TOTP_ENROLL_SCOPE
            )
            password_confirmed = True
            continue
        # From here to the commit the row lock is held continuously: the
        # state below is the state that gets cleared.
        locked_id = locked.id
        if state == "ACTIVE":
            encrypted = locked.totp_secret_enc
            if encrypted is None:  # unreachable: checked above + the pairing CHECK
                raise lifecycle_conflict(detail="Two-factor is not enrolled.")
            # Same is_blocked pre-check pattern as totp_confirm: consult the
            # ledger before another code ever reaches the verifier.
            if s.auth_rate_limit_enabled and (
                auth_limiter.is_blocked(
                    TOTP_DISABLE_USER_SCOPE,
                    str(user_id),
                    TOTP_CHALLENGE_MAX_ATTEMPTS,
                    s.auth_rate_limit_window_seconds,
                )
                or auth_limiter.is_blocked(
                    TOTP_DISABLE_USER_SCOPE,
                    composite_key,
                    TOTP_CHALLENGE_MAX_ATTEMPTS,
                    s.auth_rate_limit_window_seconds,
                )
            ):
                metrics.record_auth_rate_limit_rejection(TOTP_DISABLE_USER_SCOPE)
                await db.rollback()
                raise _too_many_attempts()
            decrypted = await _decrypt_totp_secret_or_unavailable(db, encrypted)
            if (
                verify_totp_code(
                    decrypted.secret,
                    payload.code,
                    at=utcnow(),
                    last_used_step=locked.totp_last_step,
                )
                is None
            ):
                # Mirror confirm's ledger: per-account plus composite keys, so
                # rotating source addresses cannot reset the budget.
                auth_limiter.record(
                    TOTP_DISABLE_USER_SCOPE,
                    str(user_id),
                    s.auth_rate_limit_window_seconds,
                    max_attempts=TOTP_CHALLENGE_MAX_ATTEMPTS,
                )
                auth_limiter.record(
                    TOTP_DISABLE_USER_SCOPE,
                    composite_key,
                    s.auth_rate_limit_window_seconds,
                    max_attempts=TOTP_CHALLENGE_MAX_ATTEMPTS,
                )
                await db.rollback()
                note_transient_security_signal("auth.totp.disable_failed")
                raise HTTPException(status_code=400, detail="That code is not valid right now.")
        locked.totp_secret_enc = None
        locked.totp_state = None
        locked.totp_last_step = None
        # The recovery codes exist only to bypass the second factor; disabling
        # it must retire them (a later re-enrollment mints a fresh set).
        await db.execute(delete(TotpRecoveryCode).where(TotpRecoveryCode.user_id == user_id))
        security_event(
            "auth.totp.disabled",
            "TOTP second factor removed",
            session=db,
            user_id=locked_id,
        )
        await db.commit()
        # The password side of the proof succeeded and committed — clear the confirmation budget
        # like every other successful credential workflow; disable shares TOTP_ENROLL_SCOPE with
        # enrollment by design, so one reset covers both charge sites.
        _reset_account_password_attempts(
            TOTP_ENROLL_SCOPE,
            ACCOUNT_PASSWORD_CONFIRM_ACCOUNT_SCOPE,
            f"{_client_key(request)}|{user_id}",
            user_id,
        )
        if s.auth_rate_limit_enabled:
            # A completed disable proves possession of the current code; clear
            # its ledger the way a successful confirm does.
            auth_limiter.reset(TOTP_DISABLE_USER_SCOPE, str(user_id))
            auth_limiter.reset(TOTP_DISABLE_USER_SCOPE, composite_key)
        return Response(status_code=204)
    # The state kept changing under the lock across the whole retry budget;
    # nothing was modified.
    await db.rollback()
    raise stale_state_conflict(detail="Two-factor state changed; try again.")


@router.post("/totp/recovery/regenerate", status_code=200)
async def totp_recovery_regenerate(
    payload: TotpRecoveryRegenerateIn,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> TotpRecoveryCodesOut:
    """Re-mint the recovery-code set, revoking every prior code.

    Requires BOTH the current password and a currently-valid TOTP code: an
    authenticated session alone (a stolen unlocked laptop, an XSS-surviving
    bearer token) must not be enough to rotate the break-glass material. The
    new codes are revealed here exactly once, like enrollment's."""
    user_id = user.id  # _confirm_current_password rolls back and expires `user`
    authenticated_token_version = user.token_version
    # Proof 1 — password, verified outside any transaction (Argon2).
    await _confirm_current_password(
        db,
        request,
        payload.current_password,
        user,
        scope=TOTP_RECOVERY_REGEN_SCOPE,
    )
    # Proof 2 — snapshot and verify a live TOTP code with the same guess
    # budget as disable. Expensive recovery-code hashing happens after the
    # read transaction is released; a final lock revalidates this snapshot.
    snapshot = (
        await db.execute(
            select(
                User.token_version, User.totp_state, User.totp_secret_enc, User.totp_last_step
            ).where(User.id == user_id, User.deleted_at.is_(None))
        )
    ).one_or_none()
    if snapshot is None:
        raise HTTPException(status_code=401, detail="Account no longer exists.")
    if snapshot.token_version != authenticated_token_version:
        raise HTTPException(status_code=401, detail="Session is no longer valid")
    if snapshot.totp_state != "ACTIVE" or snapshot.totp_secret_enc is None:
        await db.rollback()
        raise lifecycle_conflict(detail="Two-factor is not active.")
    s = get_settings()
    composite_key = f"{_client_key(request)}|{user_id}"
    if s.auth_rate_limit_enabled and (
        auth_limiter.is_blocked(
            TOTP_RECOVERY_REGEN_SCOPE,
            str(user_id),
            TOTP_CHALLENGE_MAX_ATTEMPTS,
            s.auth_rate_limit_window_seconds,
        )
        or auth_limiter.is_blocked(
            TOTP_RECOVERY_REGEN_SCOPE,
            composite_key,
            TOTP_CHALLENGE_MAX_ATTEMPTS,
            s.auth_rate_limit_window_seconds,
        )
    ):
        metrics.record_auth_rate_limit_rejection(TOTP_RECOVERY_REGEN_SCOPE)
        await db.rollback()
        raise _too_many_attempts()
    decrypted = await _decrypt_totp_secret_or_unavailable(db, snapshot.totp_secret_enc)
    matched = verify_totp_code(
        decrypted.secret, payload.code, at=utcnow(), last_used_step=snapshot.totp_last_step
    )
    if matched is None:
        auth_limiter.record(
            TOTP_RECOVERY_REGEN_SCOPE,
            str(user_id),
            s.auth_rate_limit_window_seconds,
            max_attempts=TOTP_CHALLENGE_MAX_ATTEMPTS,
        )
        auth_limiter.record(
            TOTP_RECOVERY_REGEN_SCOPE,
            composite_key,
            s.auth_rate_limit_window_seconds,
            max_attempts=TOTP_CHALLENGE_MAX_ATTEMPTS,
        )
        await db.rollback()
        raise HTTPException(status_code=400, detail="That code is not valid right now.")
    await db.rollback()
    codes, hashes = await _prepare_totp_recovery_codes()
    locked = (
        await db.execute(
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if locked is None:
        raise HTTPException(status_code=401, detail="Account no longer exists.")
    await _require_authenticated_generation(db, request, locked, authenticated_token_version)
    if (
        locked.totp_state != "ACTIVE"
        or locked.totp_secret_enc != snapshot.totp_secret_enc
        or locked.totp_last_step != snapshot.totp_last_step
    ):
        await db.rollback()
        raise stale_state_conflict(detail="Two-factor state changed; try again.")
    locked.totp_last_step = matched
    if decrypted.needs_rewrap:
        locked.totp_secret_enc = encrypt_totp_secret(decrypted.secret)
    await _replace_totp_recovery_codes(db, locked.id, hashes)
    security_event(
        "auth.totp.recovery_codes_regenerated",
        "recovery-code set revoked and re-minted",
        session=db,
        user_id=user_id,
    )
    await db.commit()
    # Password proof committed successfully — clear the confirmation budget like
    # change_password/delete_account do.
    _reset_account_password_attempts(
        TOTP_RECOVERY_REGEN_SCOPE,
        ACCOUNT_PASSWORD_CONFIRM_ACCOUNT_SCOPE,
        f"{_client_key(request)}|{user_id}",
        user_id,
    )
    if s.auth_rate_limit_enabled:
        auth_limiter.reset(TOTP_RECOVERY_REGEN_SCOPE, str(user_id))
        auth_limiter.reset(TOTP_RECOVERY_REGEN_SCOPE, composite_key)
    return TotpRecoveryCodesOut(codes=codes)


def _mfa_token_key(token: str) -> str:
    """Bucket key for one specific presented challenge token (never the raw value)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _check_mfa_preverification_budget(token: str) -> None:
    """Bound JWT verification work before decoding the supplied challenge token.

    Keyed on the PRESENTED token, not the client address — authenticity is
    unknowable before decoding, so a shared-IP history must never reject a
    credential this budget has not itself classified
    (``_check_refresh_preverification_budget`` states the same rule for
    refresh cookies, and ``deps`` for access tokens).
    The wider per-IP ceiling still bounds verification CPU, but only where it
    cannot harm a valid challenge: it is consulted after a token has actually
    failed, in ``_raise_invalid_mfa_challenge``.
    """
    settings = get_settings()
    if not settings.auth_rate_limit_enabled:
        return
    invalid_limit = settings.auth_rate_limit_max_attempts
    window = settings.auth_rate_limit_window_seconds
    if auth_limiter.is_blocked(
        TOTP_CHALLENGE_PREVERIFY_SCOPE,
        _mfa_token_key(token),
        invalid_limit,
        window,
    ):
        metrics.record_auth_rate_limit_rejection(TOTP_CHALLENGE_PREVERIFY_SCOPE)
        raise _too_many_attempts()


async def _raise_invalid_mfa_challenge(
    request: Request, token: str, generic: HTTPException
) -> NoReturn:
    """Record one classified-invalid challenge token and reject it.

    Both ledgers are written only once the presented token has actually
    failed, so neither can reject an unclassified credential:

    * the per-token bucket makes a repeat of THIS bad challenge cheap to
      refuse before the next RSA verification
      (``_check_mfa_preverification_budget``);
    * the per-IP bucket remains the CPU backstop, but it can only turn *this
      already-invalid* request into a 429 — a valid challenge arriving from
      the same address is never judged by a neighbour's history.

    This mirrors ``_raise_invalid_refresh``; like the access-token path in
    ``deps.current_user``, the failure is aggregated without a durable write.
    """
    note_transient_security_signal("auth.totp.challenge_failed")
    settings = get_settings()
    if settings.auth_rate_limit_enabled:
        rate_key = _client_key(request)
        limit = settings.auth_rate_limit_max_attempts
        window = settings.auth_rate_limit_window_seconds
        ip_limit = _refresh_preverification_limit(limit)
        # Per-IP gate BEFORE the per-token record (RT-M-5): once the address
        # is already throttled, appending yet another unique per-token key
        # only pressures the limiter's key ceiling — it cannot change this
        # request's outcome.
        if auth_limiter.is_blocked(TOTP_CHALLENGE_INVALID_SCOPE, rate_key, ip_limit, window):
            auth_limiter.record(
                TOTP_CHALLENGE_INVALID_SCOPE, rate_key, window, max_attempts=ip_limit
            )
            metrics.record_auth_rate_limit_rejection(TOTP_CHALLENGE_INVALID_SCOPE)
            raise _too_many_attempts()
        auth_limiter.record(
            TOTP_CHALLENGE_PREVERIFY_SCOPE,
            _mfa_token_key(token),
            window,
            max_attempts=limit,
        )
        auth_limiter.record(TOTP_CHALLENGE_INVALID_SCOPE, rate_key, window, max_attempts=ip_limit)
        if auth_limiter.is_blocked(TOTP_CHALLENGE_INVALID_SCOPE, rate_key, ip_limit, window):
            metrics.record_auth_rate_limit_rejection(TOTP_CHALLENGE_INVALID_SCOPE)
            raise _too_many_attempts()
    raise generic


@router.post(
    "/totp/challenge",
    dependencies=[Depends(_require_json_content_type)],
    responses=JSON_CONTENT_TYPE_ERROR_RESPONSE,
)
async def totp_challenge(
    payload: TotpChallengeIn,
    request: Request,
    response: Response,
    db: DbSession,
) -> TokenOut:
    """Exchange a login-issued challenge token + current code for the full
    session. Single-use, version-bound, strictly throttled per account."""
    _check_mfa_preverification_budget(payload.mfa_token)
    body, expired = _decode_payload_result(payload.mfa_token, "mfa")
    generic = HTTPException(status_code=401, detail="Invalid or expired challenge.")
    if body is None:
        # Only material that failed verification is charged: a signature-valid
        # but expired challenge (body present, expired=True) is a returning
        # client, not probing — the same exemption ``_refresh_token_expired_but_genuine``
        # gives the refresh path.
        await _raise_invalid_mfa_challenge(request, payload.mfa_token, generic)
    try:
        challenge_user_id = int(body["sub"])
        challenge_ver = body["ver"]
        jti = body["jti"]
    except (KeyError, TypeError, ValueError):
        raise generic from None
    tablet_setup = body.get("tablet_setup", False)
    if type(tablet_setup) is not bool:
        raise generic
    if expired or isinstance(challenge_ver, bool) or not isinstance(challenge_ver, int):
        raise generic

    user = (
        await db.execute(
            select(User)
            .where(User.id == challenge_user_id, User.deleted_at.is_(None))
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    user_id = user.id if user is not None else challenge_user_id
    if user is None or user.token_version != challenge_ver:
        await db.rollback()
        note_transient_security_signal("auth.totp.challenge_failed")
        raise generic
    if user.totp_state != "ACTIVE" or user.totp_secret_enc is None:
        # Second factor disabled after the challenge was issued: the password
        # proof is stale. Fail closed; the user simply logs in again.
        await db.rollback()
        raise generic

    s = get_settings()
    composite_key = f"{_client_key(request)}|{user.id}"
    if s.auth_rate_limit_enabled and (
        auth_limiter.is_blocked(
            TOTP_CHALLENGE_USER_SCOPE,
            str(user.id),
            TOTP_CHALLENGE_MAX_ATTEMPTS,
            s.auth_rate_limit_window_seconds,
        )
        or auth_limiter.is_blocked(
            TOTP_CHALLENGE_USER_SCOPE,
            composite_key,
            TOTP_CHALLENGE_MAX_ATTEMPTS,
            s.auth_rate_limit_window_seconds,
        )
    ):
        metrics.record_auth_rate_limit_rejection(TOTP_CHALLENGE_USER_SCOPE)
        await db.rollback()
        raise _too_many_attempts()

    decrypted = await _decrypt_totp_secret_or_unavailable(db, user.totp_secret_enc)
    matched = verify_totp_code(
        decrypted.secret, payload.code, at=utcnow(), last_used_step=user.totp_last_step
    )
    # A recovery code (XXXXX-XXXXX) redeems the challenge exactly like a TOTP code — the break-glass
    # path for a lost authenticator. Single-use is enforced by a conditional UPDATE race, not a held
    # lock, so two concurrent redemptions of the same code can never both succeed.
    recovery_redeemed = False
    if matched is None and looks_like_totp_recovery_code(payload.code):
        normalized = normalize_totp_recovery_code(payload.code)
        # Snapshot the unused-code hashes WITHOUT holding the row lock, then end the read
        # transaction: the batch below is up to ten sequential Argon2 verifies, and Argon2 never
        # holds a row lock, transaction, or checked-out DB connection — the same invariant
        # worker_login states.
        candidates = list(
            (
                await db.execute(
                    select(TotpRecoveryCode.id, TotpRecoveryCode.code_hash)
                    .where(
                        TotpRecoveryCode.user_id == user.id,
                        TotpRecoveryCode.used_at.is_(None),
                    )
                    .order_by(TotpRecoveryCode.id)
                )
            ).all()
        )
        await db.rollback()
        # The batch below is up to ten sequential Argon2 verifies in one request, so it holds a
        # per-account work reservation like every other credential workflow.
        reservation = _reserve_password_work(TOTP_RECOVERY_RESERVATION_SCOPE, str(user_id))
        verified_candidate_id: int | None = None
        try:
            if not candidates:
                # Zero unused codes still pays one dummy verify (S4): the
                # response time must not reveal the remaining-code count.
                await reservation.run(
                    lambda: verify_password_async(normalized, _dummy_password_hash())
                )
            for candidate_id, code_hash in candidates:
                ok, _needs_rehash = await reservation.run(
                    # partial, not a closure: one hash is bound per iteration
                    # (B023) and mypy keeps the exact coroutine type.
                    partial(verify_password_async, normalized, code_hash)
                )
                if ok:
                    verified_candidate_id = candidate_id
                    break
        finally:
            reservation.release_when_idle()
        if verified_candidate_id is not None:
            # Re-read under the row lock only after the code is proven, and
            # revalidate the challenge before consuming it: a credential
            # change, TOTP disable, or deletion that won during verification
            # must not ride the proof (the worker_login reload pattern).
            user = (
                await db.execute(
                    select(User)
                    .where(User.id == challenge_user_id, User.deleted_at.is_(None))
                    .execution_options(populate_existing=True)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if (
                user is None
                or user.token_version != challenge_ver
                or user.totp_state != "ACTIVE"
                or user.totp_secret_enc is None
            ):
                await db.rollback()
                note_transient_security_signal("auth.totp.challenge_failed")
                raise generic
            claimed = cast(
                CursorResult[Any],
                await db.execute(
                    update(TotpRecoveryCode)
                    .where(
                        TotpRecoveryCode.id == verified_candidate_id,
                        TotpRecoveryCode.used_at.is_(None),
                    )
                    .values(used_at=utcnow())
                ),
            )
            if claimed.rowcount == 1:
                recovery_redeemed = True
    if matched is None and not recovery_redeemed:
        auth_limiter.record(
            TOTP_CHALLENGE_USER_SCOPE,
            # user_id, not user.id: the recovery branch's rollback expired the
            # ORM instance, and refreshing it here would need a live session.
            str(user_id),
            s.auth_rate_limit_window_seconds,
            max_attempts=TOTP_CHALLENGE_MAX_ATTEMPTS,
        )
        auth_limiter.record(
            TOTP_CHALLENGE_USER_SCOPE,
            composite_key,
            s.auth_rate_limit_window_seconds,
            max_attempts=TOTP_CHALLENGE_MAX_ATTEMPTS,
        )
        # No 429 metric here: this answer is a 401, and the periodic summary counts only actual
        # throttle decisions.
        await db.rollback()
        note_transient_security_signal("auth.totp.challenge_failed")
        raise generic
    if matched is not None:
        user.totp_last_step = matched
    if recovery_redeemed:
        # The break-glass event an operator alerts on: this login did NOT
        # prove possession of the authenticator device.
        security_event(
            "auth.totp.recovery_code_used",
            "login completed with a single-use recovery code",
            session=db,
            user_id=user_id,
        )
    # Single-use means single SUCCESS: a wrong code leaves the challenge
    # retryable inside the throttle budget above; the successful exchange
    # burns it for any later replay (including a thief with a copy) —
    # durably, in the database, so a restart inside the TTL no longer
    # reopens the window.
    if not await _consume_mfa_jti(db, str(jti), _mfa_token_expiry(payload.mfa_token, generic)):
        await db.rollback()
        note_transient_security_signal("auth.totp.challenge_failed")
        raise generic
    if decrypted.needs_rewrap:
        user.totp_secret_enc = encrypt_totp_secret(decrypted.secret)
    out = await _issue_tokens(db, user, response, transient=tablet_setup)
    await db.commit()
    auth_limiter.reset(TOTP_CHALLENGE_USER_SCOPE, str(user_id))
    auth_limiter.reset(TOTP_CHALLENGE_USER_SCOPE, composite_key)
    return out


def _mfa_token_expiry(token: str, generic: HTTPException) -> datetime:
    """Expiry of a decoded mfa token (already structurally verified)."""
    body, _expired = _decode_payload_result(token, "mfa")
    if body is None:
        raise generic
    exp = body.get("exp")
    if not isinstance(exp, (int, float)):
        raise generic
    return datetime.fromtimestamp(float(exp), tz=UTC).replace(tzinfo=None)
