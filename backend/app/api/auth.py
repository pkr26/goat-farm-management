"""Auth: register, login (JWT pair), refresh (rotating httpOnly cookie backed
by a server-side session row with reuse detection), logout (revokes the
presented session), change-password, and farm listing/creation."""

import logging
import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..deps import (
    CurrentFarm,
    CurrentMembership,
    CurrentPerms,
    CurrentUser,
    DbSession,
    accessible_farms,
    revoke_session_family,
    revoke_user_sessions,
)
from ..models import Farm, RefreshSession, User
from ..ratelimit import auth_limiter
from ..schemas.auth import (
    FarmCreateIn,
    FarmOut,
    LoginIn,
    PermissionsOut,
    RegisterIn,
    TokenOut,
    UserOut,
)
from ..security import (
    LEGACY_PBKDF2_PREFIX,
    decode_refresh_claims,
    hash_password,
    issue_access_token,
    issue_refresh_token,
    password_policy_error,
    verify_password,
)
from ..seed import seed_new_farm
from ..utils import utcnow

router = APIRouter(prefix="/api/auth", tags=["auth"])

logger = logging.getLogger("goatfarm.auth")

ALREADY_REGISTERED = "That email is already registered."
TOO_MANY_ATTEMPTS = "Too many attempts — please try again later."

# MEDIUM 0-3: the composite (IP, email) failure budget is the base ceiling;
# the IP-agnostic per-email ceiling (distributed guessing against ONE account
# from rotating addresses) and the email-agnostic per-IP ceiling (password
# spraying across MANY accounts from one address) are multiples of it, so
# bystanders behind a shared proxy/NAT IP keep their own composite budgets.
EMAIL_LIMIT_MULTIPLIER = 3
IP_LIMIT_MULTIPLIER = 10

# Lazily built (Argon2 needs the settings and costs real CPU at first use).
_DUMMY_PASSWORD_HASH: str | None = None


def _dummy_password_hash() -> str:
    """A real Argon2id hash of a throwaway password. Unknown-email logins
    verify against it so both login paths do equal Argon2 work — the response
    time can't reveal whether the email exists (user-enumeration oracle)."""
    global _DUMMY_PASSWORD_HASH
    if _DUMMY_PASSWORD_HASH is None:
        _DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-timing-equalization")
    return _DUMMY_PASSWORD_HASH


def _client_key(request: Request) -> str:
    # request.client is None on some ASGI servers/transports: those requests
    # share one bucket instead of bypassing the limiter.
    return request.client.host if request.client else "unknown"


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
        # Security audit trail (AUDIT 4-H2): throttling must be observable.
        logger.info("login throttled (ip=%s)", _client_key(request))
    return blocked


def _record_login_failure(request: Request, email: str) -> None:
    s = get_settings()
    if s.auth_rate_limit_enabled:
        window = s.auth_rate_limit_window_seconds
        auth_limiter.record("login", _login_key(request, email), window)
        auth_limiter.record("login-email", email, window)
        auth_limiter.record("login-ip", _client_key(request), window)


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


def _record_attempt(scope: str, key: str) -> None:
    s = get_settings()
    if s.auth_rate_limit_enabled:
        auth_limiter.record(scope, key, s.auth_rate_limit_window_seconds)


def _set_refresh_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        s.refresh_cookie_name,
        token,
        max_age=s.refresh_token_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=s.cookie_secure,
        path="/api/auth",
    )


async def _issue_tokens(
    db: AsyncSession, user: User, response: Response, family_id: str | None = None
) -> TokenOut:
    """Mint the access/refresh pair and persist the refresh jti's session row
    (new family unless rotating within `family_id`). The caller commits."""
    s = get_settings()
    jti = uuid.uuid4().hex
    db.add(
        RefreshSession(
            user_id=user.id,
            jti=jti,
            family_id=family_id or uuid.uuid4().hex,
            expires_at=utcnow() + timedelta(seconds=s.refresh_token_ttl_seconds),
        )
    )
    await db.flush()  # sessions run autoflush=False — land the row explicitly
    _set_refresh_cookie(response, issue_refresh_token(user.id, jti=jti))
    return TokenOut(access_token=issue_access_token(user.id), user=UserOut.model_validate(user))


@router.post("/register", status_code=201)
async def register(
    payload: RegisterIn, request: Request, response: Response, db: DbSession
) -> TokenOut:
    rate_key = _client_key(request)
    if _rate_limited("register", rate_key):
        raise HTTPException(status_code=429, detail=TOO_MANY_ATTEMPTS)
    _record_attempt("register", rate_key)
    error = password_policy_error(payload.password)
    if error:
        raise HTTPException(status_code=400, detail=error)
    # LOW 0-4: hash BEFORE the existence check so a duplicate email doesn't
    # return measurably earlier than a fresh one (timing half of the register
    # enumeration oracle). The explicit 400 remains — without email
    # verification there is no accept-and-notify path, and the per-IP
    # register throttle blunts probing.
    pw_hash = hash_password(payload.password)
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
        raise HTTPException(status_code=429, detail=TOO_MANY_ATTEMPTS)
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    invalid = HTTPException(status_code=401, detail="Invalid email or password.")
    if user is None:
        # Equal work either way: verify against a dummy hash so an unknown
        # email doesn't return measurably earlier than a wrong password.
        verify_password(payload.password, _dummy_password_hash())
        _record_login_failure(request, payload.email)
        raise invalid
    ok, needs_rehash = verify_password(payload.password, user.password_hash)
    if not ok:
        if user.password_hash.startswith(LEGACY_PBKDF2_PREFIX + "$"):
            # LOW 0-5: a failed legacy pbkdf2 verify is far cheaper than
            # Argon2, which would distinguish "unknown email" from "known
            # pre-migration account". Top up with dummy Argon2 work.
            verify_password(payload.password, _dummy_password_hash())
        _record_login_failure(request, payload.email)
        raise invalid
    _reset_login_failures(request, payload.email)
    if needs_rehash:  # legacy pbkdf2 → transparent Argon2id upgrade
        user.password_hash = hash_password(payload.password)
        logger.info("upgraded legacy pbkdf2 hash to Argon2id (user_id=%s)", user.id)
    out = await _issue_tokens(db, user, response)
    await db.commit()
    return out


@router.post("/refresh")
async def refresh(request: Request, response: Response, db: DbSession) -> TokenOut:
    # MEDIUM 1-1: refresh was unthrottled; cap attempts per client IP.
    rate_key = _client_key(request)
    if _rate_limited("refresh", rate_key):
        raise HTTPException(status_code=429, detail=TOO_MANY_ATTEMPTS)
    _record_attempt("refresh", rate_key)
    invalid = HTTPException(status_code=401, detail="Invalid or expired refresh token")
    token = request.cookies.get(get_settings().refresh_cookie_name)
    claims = decode_refresh_claims(token) if token else None
    if claims is None:
        raise invalid
    # Lock the row: two concurrent refreshes presenting the same jti must not
    # both pass the consumption check.
    session = (
        await db.execute(
            select(RefreshSession).where(RefreshSession.jti == claims.jti).with_for_update()
        )
    ).scalar_one_or_none()
    if session is None:
        raise invalid  # predates session tracking, or never issued here
    if session.revoked_at is not None or session.consumed_at is not None:
        # MEDIUM 0-2: reuse of a rotated-away/revoked token — theft signal.
        await revoke_session_family(db, session.family_id)
        await db.commit()
        logger.warning(
            "refresh-token reuse detected — revoked family %s (user_id=%s)",
            session.family_id,
            session.user_id,
        )
        raise invalid
    if session.user_id != claims.user_id or session.expires_at <= utcnow():
        raise invalid
    user = await db.get(User, session.user_id)
    if user is None:
        raise invalid
    session.consumed_at = utcnow()
    out = await _issue_tokens(db, user, response, family_id=session.family_id)
    await db.commit()
    return out


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, db: DbSession) -> Response:
    # HIGH 0-1: revoke the presented session server-side — deleting the
    # cookie alone leaves an exfiltrated token fully usable.
    token = request.cookies.get(get_settings().refresh_cookie_name)
    claims = decode_refresh_claims(token) if token else None
    if claims is not None:
        session = (
            await db.execute(select(RefreshSession).where(RefreshSession.jti == claims.jti))
        ).scalar_one_or_none()
        if session is not None and session.revoked_at is None:
            session.revoked_at = utcnow()
            await db.commit()
    response.delete_cookie(get_settings().refresh_cookie_name, path="/api/auth")
    response.status_code = 204
    return response


class ChangePasswordIn(BaseModel):
    # max_length: no unbounded input into the (deliberately expensive) Argon2
    # hasher — same cap as the other password schemas.
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordIn, response: Response, db: DbSession, user: CurrentUser
) -> TokenOut:
    """LOW 0-6: self-service password change. Requires the current password,
    revokes EVERY outstanding refresh session (0-1 logout-everywhere), then
    issues a fresh pair so the current device stays signed in."""
    ok, _needs_rehash = verify_password(payload.current_password, user.password_hash)
    if not ok:
        if user.password_hash.startswith(LEGACY_PBKDF2_PREFIX + "$"):
            # Same legacy-timing equalization as login (LOW 0-5).
            verify_password(payload.current_password, _dummy_password_hash())
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    error = password_policy_error(payload.new_password)
    if error:
        raise HTTPException(status_code=400, detail=error)
    user.password_hash = hash_password(payload.new_password)
    await revoke_user_sessions(db, user.id)
    out = await _issue_tokens(db, user, response)  # new family, fresh session
    await db.commit()
    return out


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.get("/permissions")
async def permissions(
    user: CurrentUser, farm: CurrentFarm, membership: CurrentMembership, perms: CurrentPerms
) -> PermissionsOut:
    return PermissionsOut(is_owner=farm.owner_id == user.id, permissions=sorted(perms))


@router.get("/farms")
async def list_farms(db: DbSession, user: CurrentUser) -> list[FarmOut]:
    pairs = await accessible_farms(db, user)
    return [FarmOut(id=f.id, name=f.name, location=f.location, role=role) for f, role in pairs]


@router.post("/farms", status_code=201)
async def create_farm(payload: FarmCreateIn, db: DbSession, user: CurrentUser) -> FarmOut:
    name = payload.name.strip()
    if not name:
        # Same rule as animal tags: whitespace-only is not a name.
        raise HTTPException(status_code=400, detail="Name is required.")
    limit = get_settings().max_farms_per_user
    # Lock the user row so two concurrent create_farm calls for the same
    # account serialize: both counting below the cap and both inserting would
    # overshoot it. An explicit SELECT ... FOR UPDATE (not db.get) so the
    # lock is taken even with the user already in the identity map.
    await db.execute(select(User).where(User.id == user.id).with_for_update())
    owned = (
        await db.execute(select(func.count()).select_from(Farm).where(Farm.owner_id == user.id))
    ).scalar_one()
    if owned >= limit:
        raise HTTPException(
            status_code=400, detail=f"You already own the maximum of {limit} farms."
        )
    farm = Farm(
        name=name,
        location=(payload.location or "").strip() or None,
        owner_id=user.id,
    )
    db.add(farm)
    await db.flush()
    await seed_new_farm(db, farm)  # preset roles + feed inventory
    await db.commit()
    return FarmOut(id=farm.id, name=farm.name, location=farm.location, role=None)
