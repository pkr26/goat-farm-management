"""Auth: register, login (JWT pair), refresh (rotating httpOnly cookie),
logout, and farm listing/creation."""

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..core.config import get_settings
from ..deps import (
    CurrentFarm,
    CurrentMembership,
    CurrentPerms,
    CurrentUser,
    DbSession,
    accessible_farms,
)
from ..models import Farm, User
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
    decode_token,
    hash_password,
    issue_access_token,
    issue_refresh_token,
    password_policy_error,
    verify_password,
)
from ..seed import seed_new_farm

router = APIRouter(prefix="/api/auth", tags=["auth"])

ALREADY_REGISTERED = "That email is already registered."
TOO_MANY_ATTEMPTS = "Too many attempts — please try again later."

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


def _rate_limited(scope: str, key: str) -> bool:
    """Settings are read per request so tests can monkeypatch them."""
    s = get_settings()
    return s.auth_rate_limit_enabled and auth_limiter.is_blocked(
        scope, key, s.auth_rate_limit_max_attempts, s.auth_rate_limit_window_seconds
    )


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


def _tokens(user: User, response: Response) -> TokenOut:
    _set_refresh_cookie(response, issue_refresh_token(user.id))
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
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail=ALREADY_REGISTERED)
    user = User(
        email=payload.email,
        name=(payload.name or "").strip() or None,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:  # same email registered concurrently
        await db.rollback()
        raise HTTPException(status_code=400, detail=ALREADY_REGISTERED) from None
    return _tokens(user, response)


@router.post("/login")
async def login(payload: LoginIn, request: Request, response: Response, db: DbSession) -> TokenOut:
    rate_key = _login_key(request, payload.email)
    if _rate_limited("login", rate_key):
        raise HTTPException(status_code=429, detail=TOO_MANY_ATTEMPTS)
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    invalid = HTTPException(status_code=401, detail="Invalid email or password.")
    if user is None:
        # Equal work either way: verify against a dummy hash so an unknown
        # email doesn't return measurably earlier than a wrong password.
        verify_password(payload.password, _dummy_password_hash())
        _record_attempt("login", rate_key)
        raise invalid
    ok, needs_rehash = verify_password(payload.password, user.password_hash)
    if not ok:
        _record_attempt("login", rate_key)
        raise invalid
    auth_limiter.reset("login", rate_key)  # a success clears the failure count
    if needs_rehash:  # legacy pbkdf2 → transparent Argon2id upgrade
        user.password_hash = hash_password(payload.password)
        await db.commit()
    return _tokens(user, response)


@router.post("/refresh")
async def refresh(request: Request, response: Response, db: DbSession) -> TokenOut:
    token = request.cookies.get(get_settings().refresh_cookie_name)
    user_id = decode_token(token, "refresh") if token else None
    user = await db.get(User, user_id) if user_id else None
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    return _tokens(user, response)  # rotates the refresh cookie


@router.post("/logout", status_code=204)
async def logout(response: Response) -> Response:
    response.delete_cookie(get_settings().refresh_cookie_name, path="/api/auth")
    response.status_code = 204
    return response


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
