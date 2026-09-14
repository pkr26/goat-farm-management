"""Request-scoped dependencies: JWT auth, active farm (X-Farm-Id header),
and the RBAC authorization layer (membership, permissions, require_perm)."""

import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from . import metrics
from .core.config import get_settings
from .db import get_db
from .models import Farm, FarmMembership, RefreshSession, Role, User
from .permissions import ALL_PERMISSIONS
from .ratelimit import auth_limiter
from .schemas.common import MAX_INT32_ID
from .security import decode_access_claims_result
from .utils import utcnow

DbSession = Annotated[AsyncSession, Depends(get_db)]

logger = logging.getLogger("goatfarm.deps")

INVALID_ACCESS_TOKEN_SCOPE = "access-token-invalid"
INVALID_LOGOUT_REFRESH_TOKEN_SCOPE = "logout-refresh-token-invalid"
INVALID_TOKEN_IP_LIMIT_MULTIPLIER = 10

# A real FastAPI security dependency keeps the runtime and OpenAPI contract in
# agreement. ``auto_error=False`` preserves this module's stable JSON 401s.
_bearer_scheme = HTTPBearer(auto_error=False)


def _unauthenticated(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(status_code=401, detail=detail)


def single_bearer_token(request: Request) -> str | None:
    """Return one canonical Bearer credential, never an ambiguous selection.

    Reverse proxies and ASGI servers are not required to select the same value
    from duplicate Authorization fields. Security-sensitive callers must
    therefore reject the whole credential set unless exactly one canonical
    compact-JWT value is present.
    """
    authorization_values = request.headers.getlist("authorization")
    if len(authorization_values) != 1:
        return None
    authorization = authorization_values[0]
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return None
    token = authorization[len(prefix) :]
    if not token or any(character.isspace() for character in token):
        return None
    return token


def guard_invalid_token_verification_budget(
    request: Request,
    scope: str,
    token_material: str,
) -> None:
    """Stop a previously classified bad JWT before another RSA verification.

    Authenticity is unknowable before decoding, so a shared-IP history must
    never reject a new token here: doing so lets attackers behind the same NAT
    turn valid/expired client credentials into 429s. The wider IP budget is
    consulted only after the presented token actually fails validation.
    """
    settings = get_settings()
    if not settings.auth_rate_limit_enabled:
        return
    token_key = hashlib.sha256(token_material.encode("utf-8")).hexdigest()
    attempts = settings.auth_rate_limit_max_attempts
    window = settings.auth_rate_limit_window_seconds
    if not auth_limiter.is_blocked(
        scope + "-token",
        token_key,
        attempts,
        window,
    ):
        return
    raise invalid_token_rate_error(request, scope)


def invalid_token_rate_error(request: Request, scope: str) -> HTTPException:
    settings = get_settings()
    ip_key = request.client.host if request.client else "unknown"
    logger.info("%s throttled (ip=%s)", scope, ip_key)
    metrics.record_auth_rate_limit_rejection(scope)
    return HTTPException(
        status_code=429,
        detail="Too many invalid authentication attempts — please try again later.",
        headers={"Retry-After": str(settings.auth_rate_limit_window_seconds)},
    )


def record_invalid_token_verification(
    request: Request,
    scope: str,
    token_material: str,
) -> bool:
    """Record one classified-invalid JWT and report a saturated IP budget.

    Callers may turn ``True`` into a 429 for *this invalid request*. They must
    never carry the result forward to reject an unclassified future token.
    """
    settings = get_settings()
    if not settings.auth_rate_limit_enabled:
        return False
    ip_key = request.client.host if request.client else "unknown"
    token_key = hashlib.sha256(token_material.encode("utf-8")).hexdigest()
    attempts = settings.auth_rate_limit_max_attempts
    ip_attempts = attempts * INVALID_TOKEN_IP_LIMIT_MULTIPLIER
    window = settings.auth_rate_limit_window_seconds
    auth_limiter.record(
        scope + "-token",
        token_key,
        window,
        max_attempts=attempts,
    )
    auth_limiter.record(
        scope + "-ip",
        ip_key,
        window,
        max_attempts=ip_attempts,
    )
    return auth_limiter.is_blocked(scope + "-ip", ip_key, ip_attempts, window)


async def current_user(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> User:
    # Preserve the established runtime contract: this API accepts the
    # canonical ``Bearer`` spelling only. HTTPBearer supplies the OpenAPI
    # security scheme, while this explicit check avoids silently changing
    # authorization behavior as part of that documentation fix.
    bearer_token = single_bearer_token(request)
    if (
        credentials is None
        or credentials.scheme != "Bearer"
        or bearer_token != credentials.credentials
    ):
        raise _unauthenticated("Missing bearer token")
    guard_invalid_token_verification_budget(request, INVALID_ACCESS_TOKEN_SCOPE, bearer_token)
    decoded = decode_access_claims_result(bearer_token)
    claims = decoded.claims
    if claims is None:
        if not decoded.expired:
            if record_invalid_token_verification(
                request,
                INVALID_ACCESS_TOKEN_SCOPE,
                bearer_token,
            ):
                raise invalid_token_rate_error(request, INVALID_ACCESS_TOKEN_SCOPE)
        raise _unauthenticated("Invalid or expired token")
    statement = select(User).where(User.id == claims.user_id)
    user = (await db.execute(statement)).scalar_one_or_none()
    if user is None or user.deleted_at is not None:
        raise _unauthenticated("Account no longer exists")
    if user.token_version != claims.token_version:
        raise _unauthenticated("Session has been revoked")
    # The first lookup is deliberately lock-free. Unsafe tenant routes pin
    # their complete authorization bundle later, once CurrentFarm identifies
    # whether this principal is an owner or a worker. A worker must lock in
    # Membership -> User -> Role order, matching password reset and task
    # assignment; locking User here would invert that graph and deadlock a
    # reset against an in-flight worker mutation. Keep the signed value outside
    # the ORM identity map so the later populate-existing reload can compare
    # exactly what this request authenticated.
    request.state.authenticated_token_version = claims.token_version
    # Forced credential rotation: an owner-provisioned (or owner-reset)
    # password must be changed by its holder before any domain access. The
    # exemption is an explicit allowlist, NOT the whole /api/auth/ prefix:
    # the bootstrap routes the app shell needs to present the banner
    # (me/permissions/farm selector) and exactly the routes that rotate the
    # credential. Farm creation, account deletion and account export are
    # identity-level acts the provisioning owner must not perform with a
    # credential the worker has not yet taken sole possession of.
    if user.must_change_password and not _rotation_exempt(request.method, request.url.path):
        raise HTTPException(
            status_code=403,
            detail=(
                "This password was set by the farm owner — change it before "
                "using the farm (Account → Change password)."
            ),
        )
    return user


# Method-scoped exemption for the must-change-password fence. GET-only for
# the read routes: a POST to /api/auth/farms or DELETE /api/auth/account
# from a flagged credential stays fenced.
_ROTATION_EXEMPT_ALWAYS = frozenset(
    {
        "/api/auth/register",
        "/api/auth/login",
        "/api/auth/refresh",
        "/api/auth/logout",
        "/api/auth/me",
        "/api/auth/change-password",
    }
)
_ROTATION_EXEMPT_GET = frozenset(
    {
        "/api/auth/permissions",
        "/api/auth/farms",
    }
)


def _rotation_exempt(method: str, path: str) -> bool:
    if path in _ROTATION_EXEMPT_ALWAYS:
        return True
    return method.upper() == "GET" and path in _ROTATION_EXEMPT_GET


CurrentUser = Annotated[User, Depends(current_user)]


async def revoke_user_sessions(db: AsyncSession, user_id: int) -> None:
    """Revoke every live refresh session the user holds (logout-everywhere:
    password change and owner-initiated worker reset). The caller commits."""
    await db.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


async def revoke_session_family(
    db: AsyncSession, family_id: str, *, user_id: int | None = None
) -> None:
    """Revoke a whole rotation family: a consumed/revoked jti was presented
    again, i.e. a rotated-away refresh token was replayed (theft signal per
    RFC 6819 §5.2.2.3) — every descendant of the stolen token must die. The
    caller commits."""
    filters = [
        RefreshSession.family_id == family_id,
        RefreshSession.revoked_at.is_(None),
    ]
    if user_id is not None:
        filters.append(RefreshSession.user_id == user_id)
    await db.execute(update(RefreshSession).where(*filters).values(revoked_at=utcnow()))


async def purge_expired_refresh_sessions(
    db: AsyncSession,
    older_than_days: int = 30,
    *,
    batch_size: int,
) -> int:
    """Delete one finite, lock-skipping batch of retained expired sessions."""
    from datetime import timedelta

    if not 1 <= batch_size <= 10_000:
        raise ValueError("batch_size must be between 1 and 10000")
    cutoff = utcnow() - timedelta(days=older_than_days)
    candidate_ids = list(
        (
            await db.execute(
                select(RefreshSession.id)
                .where(RefreshSession.expires_at < cutoff)
                .order_by(RefreshSession.expires_at, RefreshSession.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    if not candidate_ids:
        return 0
    # Materialize the finite locked set before DELETE. PostgreSQL may
    # re-evaluate a locking LIMIT subquery embedded in a data-modifying
    # statement and advance past the requested batch after rows change.
    removed = await db.execute(
        delete(RefreshSession)
        .where(RefreshSession.id.in_(candidate_ids))
        .returning(RefreshSession.id)
    )
    return len(removed.scalars().all())


async def deactivate_deleted_user_memberships(
    db: AsyncSession,
    *,
    batch_size: int,
) -> int:
    """Deactivate one finite batch of memberships belonging to tombstones.

    ``User.deleted_at`` is the immediate, authoritative authorization barrier.
    Membership rows remain durable FK/audit anchors and converge to inactive
    asynchronously, so deleting an account never scans or locks an identity's
    unbounded tenant history on the request path.
    """
    if not 1 <= batch_size <= 10_000:
        raise ValueError("batch_size must be between 1 and 10000")
    membership_probe = aliased(FarmMembership)
    has_active_membership = (
        select(membership_probe.id)
        .where(
            membership_probe.user_id == User.id,
            membership_probe.is_active.is_(True),
        )
        .exists()
    )
    deleted_users = (
        select(User.id)
        .where(User.deleted_at.is_not(None), has_active_membership)
        .order_by(User.id)
        .limit(batch_size)
        .subquery("deleted_users_with_active_memberships")
    )
    candidate_ids = list(
        (
            await db.execute(
                select(FarmMembership.id)
                .join(deleted_users, deleted_users.c.id == FarmMembership.user_id)
                .where(FarmMembership.is_active.is_(True))
                .order_by(FarmMembership.user_id, FarmMembership.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True, of=FarmMembership)
            )
        ).scalars()
    )
    if not candidate_ids:
        return 0
    deactivated = await db.execute(
        update(FarmMembership)
        .where(
            FarmMembership.id.in_(candidate_ids),
            FarmMembership.is_active.is_(True),
        )
        .values(is_active=False)
        .returning(FarmMembership.id)
    )
    return len(deactivated.scalars().all())


async def active_membership(
    db: AsyncSession,
    user_id: int,
    farm_id: int,
    *,
    lock_authorization: bool = False,
) -> FarmMembership | None:
    """Load an active farm membership, optionally pinning authorization.

    A caller may hold SHARE locks on both the membership and effective role
    until commit. Generic mutating-request authorization uses the stricter
    Membership -> User -> Role helpers below so password reset, tombstoning,
    deactivation, and permission edits serialize without a lock inversion.
    """
    active_role = (
        select(Role.id)
        .where(
            Role.id == FarmMembership.role_id,
            Role.farm_id == farm_id,
            Role.deleted_at.is_(None),
        )
        .exists()
    )
    statement = select(FarmMembership).where(
        FarmMembership.farm_id == farm_id,
        FarmMembership.user_id == user_id,
        FarmMembership.is_active.is_(True),
        active_role,
    )
    if not lock_authorization:
        result = await db.execute(statement.options(selectinload(FarmMembership.role)))
        return result.scalar_one_or_none()

    membership = (
        await db.execute(
            statement.execution_options(populate_existing=True).with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if membership is None:
        return None
    role = (
        await db.execute(
            select(Role)
            .where(
                Role.id == membership.role_id,
                Role.farm_id == farm_id,
                Role.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if role is None:
        # The composite FK should make this impossible outside manual damage;
        # fail closed rather than authorizing from a stale relationship.
        return None
    membership.role = role
    return membership


async def _lock_membership_row(
    db: AsyncSession,
    user_id: int,
    farm_id: int,
) -> FarmMembership | None:
    """Lock and reload one active membership, first in the auth lock graph."""
    return (
        await db.execute(
            select(FarmMembership)
            .where(
                FarmMembership.farm_id == farm_id,
                FarmMembership.user_id == user_id,
                FarmMembership.is_active.is_(True),
            )
            .execution_options(populate_existing=True)
            .with_for_update(read=True, of=FarmMembership)
        )
    ).scalar_one_or_none()


async def _pin_authenticated_user(
    db: AsyncSession,
    request: Request,
    user: User,
) -> User:
    """Pin and exactly revalidate the signed principal after prior locks."""
    expected_version = getattr(request.state, "authenticated_token_version", None)
    if not isinstance(expected_version, int):
        raise _unauthenticated("Session is no longer valid")
    locked_user = (
        await db.execute(
            select(User)
            .where(User.id == user.id)
            .execution_options(populate_existing=True)
            .with_for_update(read=True, of=User)
        )
    ).scalar_one_or_none()
    if locked_user is None or locked_user.deleted_at is not None:
        raise _unauthenticated("Account no longer exists")
    if locked_user.token_version != expected_version:
        raise _unauthenticated("Session has been revoked")
    return locked_user


async def _lock_membership_role(
    db: AsyncSession,
    membership: FarmMembership,
    farm_id: int,
) -> Role | None:
    """Lock and reload the effective role last in the auth lock graph."""
    return (
        await db.execute(
            select(Role)
            .where(
                Role.id == membership.role_id,
                Role.farm_id == farm_id,
                Role.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
            .with_for_update(read=True, of=Role)
        )
    ).scalar_one_or_none()


async def accessible_farms(db: AsyncSession, user: User) -> list[tuple[Farm, str | None]]:
    """(farm, role_name) pairs the user may work on: owned farms first
    (role None = owner), then active memberships.

    Current provisioning makes affiliation growth tiny, but imported/legacy
    identities may violate that assumption. Load at most ``cap + 1`` and fail
    explicitly on overflow rather than hydrating an unbounded tenant graph or
    silently omitting farms from the selector.
    """
    cap = get_settings().max_account_affiliations_per_response
    owned = list(
        (
            await db.execute(
                select(Farm).where(Farm.owner_id == user.id).order_by(Farm.id).limit(cap + 1)
            )
        ).scalars()
    )
    if len(owned) > cap:
        raise HTTPException(
            status_code=409,
            detail="Account has too many farm affiliations to return safely.",
        )
    pairs: list[tuple[Farm, str | None]] = [(farm, None) for farm in owned]
    remaining = cap - len(pairs)
    memberships = list(
        (
            await db.execute(
                select(FarmMembership)
                .join(Role, Role.id == FarmMembership.role_id)
                .options(selectinload(FarmMembership.farm), selectinload(FarmMembership.role))
                .where(
                    FarmMembership.user_id == user.id,
                    FarmMembership.is_active.is_(True),
                    Role.deleted_at.is_(None),
                )
                .order_by(FarmMembership.farm_id, FarmMembership.id)
                .limit(remaining + 1)
            )
        ).scalars()
    )
    if len(memberships) > remaining:
        raise HTTPException(
            status_code=409,
            detail="Account has too many farm affiliations to return safely.",
        )
    for membership in memberships:
        pairs.append((membership.farm, membership.role.name if membership.role else "Worker"))
    return pairs


async def current_farm(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    # Required in the OpenAPI contract: the runtime always rejected
    # a missing header, but `Header() = None` declared it optional, lying to
    # every generated client. Missing now fails request validation (422)
    # before this dependency runs.
    x_farm_id: Annotated[str, Header()],
) -> Farm:
    # Tenant selection is an authorization input. Reverse proxies and ASGI
    # servers are not required to choose the same member from duplicate
    # headers, so never authorize a request under an ambiguous farm identity.
    if len(request.headers.getlist("x-farm-id")) != 1:
        raise HTTPException(status_code=400, detail="X-Farm-Id must be supplied exactly once")
    # Strict ASCII spelling (RT-B-4): Python's int() also accepts Unicode
    # digits ("٥"), "+5", " 5 " and "5_0" — spellings a WAF or edge validator
    # may treat as non-numeric, creating proxy/application disagreement on a
    # security-relevant header. Canonical [0-9]+ only.
    if not re.fullmatch(r"[0-9]+", x_farm_id):
        raise HTTPException(status_code=400, detail="X-Farm-Id must be an integer")
    farm_id = int(x_farm_id)
    if not 0 < farm_id < 2**62:
        raise HTTPException(status_code=400, detail="X-Farm-Id out of range")
    # Above the int4 PK ceiling no farm can exist — 404, never an asyncpg
    # int32 DataError (500).
    farm = await db.get(Farm, farm_id) if farm_id <= MAX_INT32_ID else None
    # Unknown and forbidden farms share ONE 404 — answering 403 for
    # an existing-but-forbidden id lets any authenticated user enumerate
    # sequential farm ids.
    if farm is None:
        raise HTTPException(status_code=404, detail="Farm not found")

    unsafe_request = request.method not in {"GET", "HEAD", "OPTIONS"}
    if farm.owner_id == user.id:
        if unsafe_request:
            await _pin_authenticated_user(db, request, user)
        return farm

    if not unsafe_request:
        membership = await active_membership(db, user.id, farm.id)
        if membership is None:
            raise HTTPException(status_code=404, detail="Farm not found")
        # current_membership reuses this instead of re-issuing the identical
        # unlocked SELECT (plus its Role sub-query) for the same request.
        request.state.unlocked_membership = membership
        request.state.unlocked_membership_farm_id = farm.id
        return farm

    # Canonical authorization order for a mutating worker request:
    # Membership SHARE -> authenticated User SHARE -> effective Role SHARE.
    # Owner-initiated reset takes Membership UPDATE -> target User UPDATE, so
    # this order prevents the classic cycle where each transaction holds one
    # row while waiting for the other. The locks remain held until the route's
    # commit/rollback and stale authorization is reloaded after every wait.
    membership = await _lock_membership_row(db, user.id, farm.id)
    if membership is None:
        raise HTTPException(status_code=404, detail="Farm not found")
    await _pin_authenticated_user(db, request, user)
    role = await _lock_membership_role(db, membership, farm.id)
    if role is None:
        raise HTTPException(status_code=404, detail="Farm not found")
    membership.role = role
    request.state.locked_membership = membership
    request.state.locked_membership_farm_id = farm.id
    return farm


CurrentFarm = Annotated[Farm, Depends(current_farm)]


async def current_membership(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
) -> FarmMembership | None:
    """The user's active membership on the current farm; None for the owner.

    Read requests stay lock-free. Unsafe HTTP methods pin the exact active
    membership and permission bundle for the transaction's lifetime.
    """
    if farm.owner_id == user.id:
        return None
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        membership = getattr(request.state, "locked_membership", None)
        membership_farm_id = getattr(request.state, "locked_membership_farm_id", None)
        if isinstance(membership, FarmMembership) and membership_farm_id == farm.id:
            return membership
        # Every unsafe CurrentFarm path above either installs the canonical
        # locked bundle or fails. Never fall back to an unlocked authorization
        # read if a future dependency refactor violates that invariant.
        raise _unauthenticated("Authorization could not be pinned")
    cached = getattr(request.state, "unlocked_membership", None)
    cached_farm_id = getattr(request.state, "unlocked_membership_farm_id", None)
    if isinstance(cached, FarmMembership) and cached_farm_id == farm.id:
        return cached
    return await active_membership(
        db,
        user.id,
        farm.id,
        lock_authorization=False,
    )


CurrentMembership = Annotated[FarmMembership | None, Depends(current_membership)]


def perms_for(user: User, farm: Farm, membership: FarmMembership | None) -> set[str]:
    """Effective permission set: owner → everything; worker → role's bundle."""
    if farm.owner_id == user.id:
        return set(ALL_PERMISSIONS)
    if membership is None or membership.role is None:
        return set()
    return membership.role.permission_set()


async def current_perms(
    user: CurrentUser, farm: CurrentFarm, membership: CurrentMembership
) -> set[str]:
    return perms_for(user, farm, membership)


CurrentPerms = Annotated[set[str], Depends(current_perms)]


def require_perm(code: str) -> Callable[[set[str]], Awaitable[set[str]]]:
    """Dependency factory: 403 unless the user holds `code` on this farm."""

    async def dependency(perms: CurrentPerms) -> set[str]:
        if code not in perms:
            # Security audit trail: denials must be observable.
            logger.info("RBAC denial: missing permission %s", code)
            raise HTTPException(status_code=403, detail=f"Missing permission: {code}")
        return perms

    return dependency
