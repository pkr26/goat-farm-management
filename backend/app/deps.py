"""Request-scoped dependencies: JWT auth, active farm (X-Farm-Id header),
and the RBAC authorization layer (membership, permissions, require_perm)."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .db import get_db
from .models import Farm, FarmMembership, User
from .permissions import ALL_PERMISSIONS
from .schemas.common import MAX_INT32_ID
from .security import decode_token

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _unauthenticated(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(status_code=401, detail=detail)


async def current_user(
    db: DbSession, authorization: Annotated[str | None, Header()] = None
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise _unauthenticated("Missing bearer token")
    user_id = decode_token(authorization.removeprefix("Bearer "), "access")
    if user_id is None:
        raise _unauthenticated("Invalid or expired token")
    user = await db.get(User, user_id)
    if user is None:
        raise _unauthenticated("Account no longer exists")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def active_membership(db: AsyncSession, user_id: int, farm_id: int) -> FarmMembership | None:
    result = await db.execute(
        select(FarmMembership)
        .options(selectinload(FarmMembership.role))
        .where(
            FarmMembership.farm_id == farm_id,
            FarmMembership.user_id == user_id,
            FarmMembership.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def accessible_farms(db: AsyncSession, user: User) -> list[tuple[Farm, str | None]]:
    """(farm, role_name) pairs the user may work on: owned farms first
    (role None = owner), then active memberships."""
    owned = await db.execute(select(Farm).where(Farm.owner_id == user.id))
    pairs: list[tuple[Farm, str | None]] = [(farm, None) for farm in owned.scalars()]
    result = await db.execute(
        select(FarmMembership)
        .options(selectinload(FarmMembership.farm), selectinload(FarmMembership.role))
        .where(FarmMembership.user_id == user.id, FarmMembership.is_active.is_(True))
    )
    for membership in result.scalars():
        pairs.append((membership.farm, membership.role.name if membership.role else "Worker"))
    return pairs


async def current_farm(
    db: DbSession,
    user: CurrentUser,
    x_farm_id: Annotated[str | None, Header()] = None,
) -> Farm:
    if x_farm_id is None:
        raise HTTPException(status_code=400, detail="X-Farm-Id header is required")
    try:
        farm_id = int(x_farm_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="X-Farm-Id must be an integer") from None
    if not 0 < farm_id < 2**62:
        raise HTTPException(status_code=400, detail="X-Farm-Id out of range")
    # Above the int4 PK ceiling no farm can exist — 404, never an asyncpg
    # int32 DataError (500).
    farm = await db.get(Farm, farm_id) if farm_id <= MAX_INT32_ID else None
    if farm is None:
        raise HTTPException(status_code=404, detail="Farm not found")
    if farm.owner_id != user.id and await active_membership(db, user.id, farm.id) is None:
        raise HTTPException(status_code=403, detail="No access to this farm")
    return farm


CurrentFarm = Annotated[Farm, Depends(current_farm)]


async def current_membership(
    db: DbSession, user: CurrentUser, farm: CurrentFarm
) -> FarmMembership | None:
    """The user's active membership on the current farm; None for the owner."""
    if farm.owner_id == user.id:
        return None
    return await active_membership(db, user.id, farm.id)


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
            raise HTTPException(status_code=403, detail=f"Missing permission: {code}")
        return perms

    return dependency
