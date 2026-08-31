"""Auth: register, login (JWT pair), refresh (rotating httpOnly cookie backed
by a server-side session row with reuse detection), logout (revokes the
presented session), change-password, and farm listing/creation."""

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, NoReturn
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
    guard_invalid_token_verification_budget,
    invalid_token_rate_error,
    record_invalid_token_verification,
    revoke_session_family,
    revoke_user_sessions,
    single_bearer_token,
)
from ..models import (
    Farm,
    FarmMembership,
    RefreshSession,
    User,
)
from ..models.idempotency import CREATE_FARM_IDEMPOTENCY_OPERATION
from ..ratelimit import auth_limiter
from ..schemas.auth import (
    AccountDeleteIn,
    AccountExportOut,
    AccountIdentityExport,
    ChangePasswordIn,
    FarmCreateIn,
    FarmOut,
    LoginIn,
    MembershipExport,
    OwnedFarmExport,
    PermissionsOut,
    RegisterIn,
    TokenOut,
    UserOut,
)
from ..security import (
    LEGACY_PBKDF2_PREFIX,
    PasswordWorkCapacityError,
    _decode_payload_result,
    complete_rejected_login_timing_async,
    decode_access_claims_result,
    decode_refresh_claims,
    hash_password_async,
    issue_access_token,
    issue_refresh_token,
    password_policy_error,
    prime_dummy_password_hash,
    verify_password_async,
    verify_password_with_work_async,
)
from ..seed import seed_new_farm
from ..services.idempotency import IdempotencyKey, execute_idempotent
from ..utils import utcnow

router = APIRouter(prefix="/api/auth", tags=["auth"])

logger = logging.getLogger("goatfarm.auth")

ALREADY_REGISTERED = "That email is already registered."
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


def _login_blocked(request: Request, email: str) -> bool:
    """Composite (IP, email) ceiling plus the 0-3 counters: per-email
    (IP-agnostic, against distributed brute force) and per-IP
    (email-agnostic, against spraying)."""
    s = get_settings()
    if not s.auth_rate_limit_enabled:
        return False
    attempts, window = s.auth_rate_limit_max_attempts, s.auth_rate_limit_window_seconds
    blocked = (
        auth_limiter.is_blocked("login", _login_key(request, email), attempts, window)
        or auth_limiter.is_blocked("login-email", email, attempts * EMAIL_LIMIT_MULTIPLIER, window)
        or auth_limiter.is_blocked(
            "login-ip", _client_key(request), attempts * IP_LIMIT_MULTIPLIER, window
        )
    )
    if blocked:
        # Security audit trail: throttling must be observable.
        logger.info("login throttled (ip=%s)", _client_key(request))
    return blocked


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
        logger.info("%s throttled (key=%s)", scope, key)
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
        logger.info("%s throttled (user_id=%s)", scope, user_id)
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


def _too_many_attempts() -> HTTPException:
    """Standards-friendly throttle response with a conservative retry hint."""
    return HTTPException(
        status_code=429,
        detail=TOO_MANY_ATTEMPTS,
        headers={"Retry-After": str(get_settings().auth_rate_limit_window_seconds)},
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
        logger.info("refresh pre-verification throttled (repeatedly invalid cookie)")
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
        raise HTTPException(
            status_code=409,
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
    expires_at = issued_at + timedelta(seconds=s.refresh_token_ttl_seconds)
    session = RefreshSession(
        user_id=user.id,
        jti=jti,
        family_id=effective_family_id,
        expires_at=expires_at,
        created_at=issued_at,
    )
    db.add(session)
    await db.flush()  # sessions run autoflush=False — land the row explicitly
    if replacement_for is not None:
        replacement_for.replacement_jti = jti
    _set_refresh_cookie(
        response,
        issue_refresh_token(
            user.id,
            jti=jti,
            family_id=effective_family_id,
            issued_at=issued_at,
            expires_at=expires_at,
        ),
    )
    return TokenOut(
        access_token=issue_access_token(user.id, user.token_version),
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
    return family_id is None or (isinstance(family_id, str) and 1 <= len(family_id) <= 64)


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
    settings = get_settings()
    if settings.auth_rate_limit_enabled:
        rate_key = _client_key(request)
        limit = settings.auth_rate_limit_max_attempts
        window = settings.auth_rate_limit_window_seconds
        ip_limit = _refresh_preverification_limit(limit)
        auth_limiter.record(
            REFRESH_PREVERIFY_SCOPE,
            _refresh_token_key(token),
            window,
            max_attempts=limit,
        )
        auth_limiter.record("refresh-invalid", rate_key, window, max_attempts=ip_limit)
        if auth_limiter.is_blocked("refresh-invalid", rate_key, ip_limit, window):
            logger.info("refresh-invalid throttled (key=%s)", rate_key)
            raise _too_many_attempts()
    raise HTTPException(status_code=401, detail="Invalid or expired refresh token")


@router.post("/register", status_code=201)
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
    # Hash BEFORE the existence check so a duplicate email doesn't
    # return measurably earlier than a fresh one (timing half of the register
    # enumeration oracle). The explicit 400 remains — without email
    # verification there is no accept-and-notify path, and the per-IP
    # register throttle blunts probing.
    pw_hash = await hash_password_async(payload.password)
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none() is not None:
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
        raise HTTPException(status_code=400, detail=ALREADY_REGISTERED) from None
    out = await _issue_tokens(db, user, response)
    await db.commit()
    return out


@router.post("/login")
async def login(payload: LoginIn, request: Request, response: Response, db: DbSession) -> TokenOut:
    if _login_blocked(request, payload.email):
        raise _too_many_attempts()
    reservation_scope = "login-password-work"
    reservation = _reserve_password_work(reservation_scope, payload.email)
    credential_accepted = False
    password_work_started = False
    try:
        # Re-check after the atomic admission reservation. A preceding request
        # may have recorded the threshold immediately before releasing its
        # slot; no expensive work starts from a stale limiter observation.
        if _login_blocked(request, payload.email):
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
            try:
                await reservation.run(
                    lambda: complete_rejected_login_timing_async(
                        payload.password,
                        stored_hash,
                        _dummy_password_hash(),
                        did_argon_work,
                    )
                )
            except PasswordWorkCapacityError:
                # The credential decision is already made. A saturated pool
                # must not upgrade this definitive 401 into a 429, and the
                # rejection must still reach the brute-force ledger — losing
                # padding fidelity under overload is the lesser harm.
                pass
            _record_login_failure(request, payload.email)
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
        out = await _issue_tokens(db, user, response)
        await db.commit()
        _reset_login_failures(request, payload.email)
        return out
    except asyncio.CancelledError:
        if password_work_started and not credential_accepted:
            _record_login_failure(request, payload.email)
        raise
    finally:
        reservation.release_when_idle()


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
            await revoke_session_family(
                db,
                claims.family_id,
                user_id=claims.user_id,
            )
            await db.commit()
        _raise_invalid_refresh(
            request,
            token,
        )  # predates session tracking, compacted, or never issued here
    if claims.family_id is not None and claims.family_id != session.family_id:
        _raise_invalid_refresh(request, token)
    if session.user_id != claims.user_id:
        _raise_invalid_refresh(request, token)
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
        ):
            remaining = max(0, int((successor.expires_at - now).total_seconds()))
            _set_refresh_cookie(
                response,
                issue_refresh_token(
                    user.id,
                    jti=successor.jti,
                    family_id=successor.family_id,
                    issued_at=successor.created_at,
                    expires_at=successor.expires_at,
                ),
                max_age=remaining,
            )
            return TokenOut(
                access_token=issue_access_token(user.id, user.token_version),
                user=UserOut.model_validate(user),
            )
        await revoke_session_family(db, session.family_id, user_id=session.user_id)
        await db.commit()
        logger.warning(
            "refresh-token reuse detected — revoked family %s (user_id=%s)",
            session.family_id,
            session.user_id,
        )
        _raise_invalid_refresh(request, token)
    session.consumed_at = now
    out = await _issue_tokens(
        db, user, response, family_id=session.family_id, replacement_for=session
    )
    await db.commit()
    return out


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
    if claims is not None and access_claims is not None and claims.user_id != access_claims.user_id:
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
            and session.revoked_at is None
            and claims.expires_at > now
            and session.expires_at > now
        ):
            # Logout is a family boundary, not merely a one-JTI revocation.
            # If a concurrent refresh won the User lock first, its committed
            # successor is already in this family and is revoked by the same
            # UPDATE. It can never refresh successfully after this 204.
            await revoke_session_family(
                db,
                session.family_id,
                user_id=session.user_id,
            )
            candidate_user_id = session.user_id
            cookie_confirmed = True
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
                        RefreshSession.revoked_at.is_(None),
                        RefreshSession.expires_at > now,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if live_family_session is not None:
                await revoke_session_family(
                    db,
                    claims.family_id,
                    user_id=claims.user_id,
                )
                cookie_confirmed = True
    if logged_out_user is not None:
        # A duplicate logout carrying only the now-stale access token must not
        # repeatedly advance the counter. A valid cookie already proved the
        # session even if its bearer header happened to be stale.
        bearer_current = bool(
            access_claims is not None
            and access_claims.user_id == candidate_user_id
            and access_claims.token_version == logged_out_user.token_version
        )
        if cookie_confirmed or bearer_current:
            if bearer_current and not cookie_confirmed:
                # With no valid cookie there is no provable family to target.
                # Revoking only the access-token version would be reversible:
                # any surviving refresh family could immediately mint a token
                # carrying the new version. Session caps keep this update
                # finite, so bearer-only logout means logout everywhere.
                await revoke_user_sessions(db, logged_out_user.id)
            logged_out_user.token_version += 1
    await db.commit()
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
        if locked_user.token_version != authenticated_token_version:
            raise HTTPException(status_code=401, detail="Session is no longer valid")
        locked_user.password_hash = replacement_hash
        locked_user.token_version += 1
        await revoke_user_sessions(db, locked_user.id)
        out = await _issue_tokens(db, locked_user, response)  # new family, fresh session
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
        raise HTTPException(
            status_code=409,
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
        raise HTTPException(
            status_code=409,
            detail="Account has too many farm affiliations to return safely.",
        )
    response.headers["Content-Disposition"] = (
        f'attachment; filename="goatfarm-account-{user.id}.json"'
    )
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
                farm_type=farm.farm_type,
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
        if locked_user.token_version != authenticated_token_version:
            raise HTTPException(status_code=401, detail="Session is no longer valid")

        owns_farm = (
            await db.execute(select(Farm.id).where(Farm.owner_id == locked_user.id).limit(1))
        ).scalar_one_or_none()
        if owns_farm is not None:
            # Two Argon2 runs already happened. The admission charge remains in
            # place; only a completed deletion clears it below.
            raise HTTPException(
                status_code=409,
                detail=(
                    "Account deletion is unavailable while this account owns a farm; "
                    "farm ownership cannot currently be transferred or deleted."
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
        await db.commit()
        _reset_account_password_attempts(*scopes)

        _delete_refresh_cookie(response)
        response.status_code = 204
        return response
    finally:
        reservation.release_when_idle()


@router.get("/permissions")
async def permissions(
    user: CurrentUser, farm: CurrentFarm, membership: CurrentMembership, perms: CurrentPerms
) -> PermissionsOut:
    return PermissionsOut(is_owner=farm.owner_id == user.id, permissions=sorted(perms))


@router.get("/farms")
async def list_farms(db: DbSession, user: CurrentUser) -> list[FarmOut]:
    pairs = await accessible_farms(db, user)
    return [
        FarmOut(
            id=f.id,
            name=f.name,
            location=f.location,
            timezone=f.timezone,
            role=role,
            farm_type=f.farm_type,
        )
        for f, role in pairs
    ]


@router.post("/farms", status_code=201)
async def create_farm(
    payload: FarmCreateIn,
    response: Response,
    db: DbSession,
    user: CurrentUser,
    idempotency_key: IdempotencyKey = None,
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
    if locked_user.token_version != authenticated_token_version:
        raise HTTPException(status_code=401, detail="Session is no longer valid")

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
            farm_type=payload.farm_type,
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
            farm_type=farm.farm_type,
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
