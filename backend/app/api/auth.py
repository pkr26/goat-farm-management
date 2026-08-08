"""Auth: register, login (JWT pair), refresh (rotating httpOnly cookie backed
by a server-side session row with reuse detection), logout (revokes the
presented session), change-password, and farm listing/creation."""

import logging
import uuid
from datetime import timedelta
from typing import NoReturn

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
from ..models import (
    Animal,
    BreedingRecord,
    BucketMove,
    Farm,
    FarmMembership,
    FeedingRecord,
    HealthEvent,
    KiddingRecord,
    RefreshSession,
    SimulationScenario,
    Task,
    Transaction,
    User,
    WeightRecord,
)
from ..ratelimit import auth_limiter
from ..schemas.auth import (
    AccountDeleteIn,
    AccountExportOut,
    AccountIdentityExport,
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
    decode_access_claims,
    decode_refresh_claims,
    hash_password,
    issue_access_token,
    issue_refresh_token,
    password_policy_error,
    prime_dummy_password_hash,
    verify_password,
)
from ..seed import seed_new_farm
from ..utils import utcnow

router = APIRouter(prefix="/api/auth", tags=["auth"])

logger = logging.getLogger("goatfarm.auth")

ALREADY_REGISTERED = "That email is already registered."
TOO_MANY_ATTEMPTS = "Too many attempts — please try again later."

# The composite (IP, email) failure budget is the base ceiling;
# the IP-agnostic per-email ceiling (distributed guessing against ONE account
# from rotating addresses) and the email-agnostic per-IP ceiling (password
# spraying across MANY accounts from one address) are multiples of it, so
# bystanders behind a shared proxy/NAT IP keep their own composite budgets.
EMAIL_LIMIT_MULTIPLIER = 3
IP_LIMIT_MULTIPLIER = 10

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


def _set_refresh_cookie(response: Response, token: str, *, max_age: int | None = None) -> None:
    s = get_settings()
    response.set_cookie(
        s.refresh_cookie_name,
        token,
        max_age=s.refresh_token_ttl_seconds if max_age is None else max_age,
        httponly=True,
        samesite="lax",
        secure=s.cookie_secure,
        path="/api/auth",
    )


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
    s = get_settings()
    jti = uuid.uuid4().hex
    issued_at = utcnow()
    expires_at = issued_at + timedelta(seconds=s.refresh_token_ttl_seconds)
    session = RefreshSession(
        user_id=user.id,
        jti=jti,
        family_id=family_id or uuid.uuid4().hex,
        expires_at=expires_at,
        created_at=issued_at,
    )
    db.add(session)
    await db.flush()  # sessions run autoflush=False — land the row explicitly
    if replacement_for is not None:
        replacement_for.replacement_jti = jti
    _set_refresh_cookie(
        response,
        issue_refresh_token(user.id, jti=jti, issued_at=issued_at, expires_at=expires_at),
    )
    return TokenOut(
        access_token=issue_access_token(user.id, user.token_version),
        user=UserOut.model_validate(user),
    )


def _raise_invalid_refresh(request: Request) -> NoReturn:
    """Charge only rejected refreshes to the abuse budget.

    A normal page reload performs a successful refresh because the access
    token is intentionally memory-only. Charging successes lets ordinary
    navigation exhaust a shared NAT/proxy IP bucket and log users out.
    """
    rate_key = _client_key(request)
    if _rate_limited("refresh-invalid", rate_key):
        raise HTTPException(status_code=429, detail=TOO_MANY_ATTEMPTS)
    _record_attempt("refresh-invalid", rate_key)
    raise HTTPException(status_code=401, detail="Invalid or expired refresh token")


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
    # Hash BEFORE the existence check so a duplicate email doesn't
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
    # Serialize login with password resets/changes. If login wins, a following
    # reset revokes the just-issued session; if reset wins, this request must
    # verify only the new password hash.
    result = await db.execute(select(User).where(User.email == payload.email).with_for_update())
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
            # A failed legacy pbkdf2 verify is far cheaper than
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
    token = request.cookies.get(get_settings().refresh_cookie_name)
    claims = decode_refresh_claims(token) if token else None
    if claims is None:
        _raise_invalid_refresh(request)
    # Lock order for every auth/session mutation is User -> RefreshSession.
    # Password reset/change holds the same user lock before revoking sessions,
    # preventing a refresh from minting a successor after revocation.
    user = (
        await db.execute(select(User).where(User.id == claims.user_id).with_for_update())
    ).scalar_one_or_none()
    if user is None:
        _raise_invalid_refresh(request)
    # Lock the row: two concurrent refreshes presenting the same jti must not
    # both pass the consumption check.
    session = (
        await db.execute(
            select(RefreshSession).where(RefreshSession.jti == claims.jti).with_for_update()
        )
    ).scalar_one_or_none()
    if session is None:
        _raise_invalid_refresh(request)  # predates session tracking, or never issued here
    now = utcnow()
    if session.revoked_at is not None:
        _raise_invalid_refresh(request)
    if session.consumed_at is not None:
        # A tiny grace window makes the same rotation idempotent across tabs.
        # The successor's fixed iat/exp/jti reconstruct the exact same signed
        # cookie; anything outside the window remains a theft signal.
        grace = timedelta(seconds=get_settings().refresh_reuse_grace_seconds)
        successor = None
        elapsed = now - session.consumed_at
        if session.replacement_jti and timedelta(0) <= elapsed <= grace:
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
                    issued_at=successor.created_at,
                    expires_at=successor.expires_at,
                ),
                max_age=remaining,
            )
            return TokenOut(
                access_token=issue_access_token(user.id, user.token_version),
                user=UserOut.model_validate(user),
            )
        await revoke_session_family(db, session.family_id)
        await db.commit()
        logger.warning(
            "refresh-token reuse detected — revoked family %s (user_id=%s)",
            session.family_id,
            session.user_id,
        )
        _raise_invalid_refresh(request)
    if session.user_id != claims.user_id or session.expires_at <= now:
        _raise_invalid_refresh(request)
    session.consumed_at = now
    out = await _issue_tokens(
        db, user, response, family_id=session.family_id, replacement_for=session
    )
    await db.commit()
    return out


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, db: DbSession) -> Response:
    # Revoke the presented session server-side — deleting the
    # cookie alone leaves an exfiltrated token fully usable.
    token = request.cookies.get(get_settings().refresh_cookie_name)
    claims = decode_refresh_claims(token) if token else None
    authorization = request.headers.get("Authorization")
    access_claims = (
        decode_access_claims(authorization.removeprefix("Bearer "))
        if authorization and authorization.startswith("Bearer ")
        else None
    )
    candidate_user_id = claims.user_id if claims is not None else None
    if candidate_user_id is None and access_claims is not None:
        candidate_user_id = access_claims.user_id
    logged_out_user = (
        (
            await db.execute(select(User).where(User.id == candidate_user_id).with_for_update())
        ).scalar_one_or_none()
        if candidate_user_id is not None
        else None
    )
    cookie_confirmed = False
    if claims is not None and logged_out_user is not None:
        session = (
            await db.execute(
                select(RefreshSession).where(RefreshSession.jti == claims.jti).with_for_update()
            )
        ).scalar_one_or_none()
        if (
            session is not None
            and session.user_id == claims.user_id
            and session.revoked_at is None
            and session.expires_at > utcnow()
        ):
            session.revoked_at = utcnow()
            candidate_user_id = session.user_id
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
            logged_out_user.token_version += 1
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
    """Self-service password change. Requires the current password,
    revokes EVERY outstanding refresh session, then
    issues a fresh pair so the current device stays signed in."""
    locked_user = (
        await db.execute(select(User).where(User.id == user.id).with_for_update())
    ).scalar_one()
    ok, _needs_rehash = verify_password(payload.current_password, locked_user.password_hash)
    if not ok:
        if locked_user.password_hash.startswith(LEGACY_PBKDF2_PREFIX + "$"):
            # Same legacy-timing equalization as login.
            verify_password(payload.current_password, _dummy_password_hash())
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    error = password_policy_error(payload.new_password)
    if error:
        raise HTTPException(status_code=400, detail=error)
    locked_user.password_hash = hash_password(payload.new_password)
    locked_user.token_version += 1
    await revoke_user_sessions(db, locked_user.id)
    out = await _issue_tokens(db, locked_user, response)  # new family, fresh session
    await db.commit()
    return out


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
    owned = list(
        (await db.execute(select(Farm).where(Farm.owner_id == user.id).order_by(Farm.id))).scalars()
    )
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
            )
        ).scalars()
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


# Nullable audit/assignment references that must be anonymized before deleting
# the global User row. The farm's operational record remains intact without a
# dangling identity or cross-tenant data being returned to the departing user.
_USER_REFERENCE_COLUMNS = (
    WeightRecord.created_by_id,
    BucketMove.created_by_id,
    Animal.restriction_cleared_by_id,
    BreedingRecord.created_by_id,
    KiddingRecord.created_by_id,
    FeedingRecord.created_by_id,
    HealthEvent.created_by_id,
    Transaction.created_by_id,
    Transaction.voided_by_id,
    SimulationScenario.created_by_id,
    Task.assigned_user_id,
    Task.completed_by_id,
    Task.verified_by_id,
    Task.skipped_by_id,
)


@router.delete("/account", status_code=204)
async def delete_account(
    payload: AccountDeleteIn,
    request: Request,
    response: Response,
    db: DbSession,
    user: CurrentUser,
) -> Response:
    """Permanently remove a non-owner account after password confirmation."""
    rate_key = f"{_client_key(request)}|{user.id}"
    if _rate_limited("account-delete", rate_key):
        raise HTTPException(status_code=429, detail=TOO_MANY_ATTEMPTS)

    # Keep the same Membership -> User lock order used by team lifecycle
    # operations so account deletion cannot deadlock with deactivation/reset.
    memberships = list(
        (
            await db.execute(
                select(FarmMembership)
                .where(FarmMembership.user_id == user.id)
                .order_by(FarmMembership.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    locked_user = (
        await db.execute(select(User).where(User.id == user.id).with_for_update())
    ).scalar_one()
    ok, _needs_rehash = verify_password(payload.current_password, locked_user.password_hash)
    if not ok:
        _record_attempt("account-delete", rate_key)
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    auth_limiter.reset("account-delete", rate_key)

    owns_farm = (
        await db.execute(select(Farm.id).where(Farm.owner_id == locked_user.id).limit(1))
    ).scalar_one_or_none()
    if owns_farm is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Account deletion is unavailable while this account owns a farm; "
                "farm ownership cannot currently be transferred or deleted."
            ),
        )

    for membership in memberships:
        await db.execute(
            update(Task)
            .where(
                Task.farm_id == membership.farm_id,
                Task.assigned_user_id == locked_user.id,
                Task.assigned_role_id.is_(None),
                Task.status == "PENDING",
            )
            .values(assigned_role_id=membership.role_id)
        )
    for column in _USER_REFERENCE_COLUMNS:
        await db.execute(
            update(column.class_).where(column == locked_user.id).values({column: None})
        )
    await db.execute(delete(FarmMembership).where(FarmMembership.user_id == locked_user.id))
    await db.execute(delete(RefreshSession).where(RefreshSession.user_id == locked_user.id))
    await db.delete(locked_user)
    await db.commit()

    response.delete_cookie(get_settings().refresh_cookie_name, path="/api/auth")
    response.status_code = 204
    return response


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
        )
        for f, role in pairs
    ]


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
        timezone=payload.timezone,
        owner_id=user.id,
    )
    db.add(farm)
    await db.flush()
    await seed_new_farm(db, farm)  # preset roles + feed inventory
    await db.commit()
    return FarmOut(
        id=farm.id,
        name=farm.name,
        location=farm.location,
        timezone=farm.timezone,
        role=None,
    )
