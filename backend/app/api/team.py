"""Team management (requires team.manage — the owner by default): workers,
their roles, password resets, and the farm's custom/preset roles."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm, revoke_user_sessions
from ..models import Farm, FarmMembership, Role, Task, User
from ..permissions import ALL_PERMISSIONS, PERMISSION_GROUPS, PERMISSIONS
from ..schemas.common import MAX_INT32_ID
from ..schemas.team import (
    MembershipOut,
    PasswordResetIn,
    PermissionGroupOut,
    RoleChangeIn,
    RoleIn,
    RoleOut,
    TeamOut,
    WorkerCreateIn,
)
from ..security import hash_password, password_policy_error

router = APIRouter(prefix="/api/team", tags=["team"])

TEAM_PERM = Annotated[set[str], Depends(require_perm("team.manage"))]

# Generic refusal for cross-farm-affiliated accounts in create_worker
# (see the comment at the raise sites).
CANT_ADD_TO_TEAM = "That email can't be added to this farm's team."


def _membership_out(membership: FarmMembership) -> MembershipOut:
    role = membership.role
    return MembershipOut(
        id=membership.id,
        user_id=membership.user_id,
        email=membership.user.email,
        name=membership.user.name,
        role_id=membership.role_id,
        role_name=role.name if role else None,
        is_active=membership.is_active,
    )


def _role_out(role: Role, member_count: int) -> RoleOut:
    selected = role.permission_set()
    return RoleOut(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        permissions=[
            code for _group, codes in PERMISSION_GROUPS for code in codes if code in selected
        ],
        member_count=member_count,
    )


async def _get_membership(db: AsyncSession, farm: Farm, membership_id: int) -> FarmMembership:
    # Ids above the int4 PK ceiling cannot exist — 404, never an asyncpg
    # int32 DataError (500).
    if membership_id > MAX_INT32_ID:
        raise HTTPException(status_code=404, detail="Membership not found")
    result = await db.execute(
        select(FarmMembership)
        .options(selectinload(FarmMembership.user), selectinload(FarmMembership.role))
        .where(FarmMembership.id == membership_id)
    )
    membership = result.scalar_one_or_none()
    if membership is None or membership.farm_id != farm.id:
        raise HTTPException(status_code=404, detail="Membership not found")
    return membership


async def _get_role(db: AsyncSession, farm: Farm, role_id: int) -> Role:
    role = await db.get(Role, role_id) if role_id <= MAX_INT32_ID else None
    if role is None or role.farm_id != farm.id:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


def _guard_peer_manager(membership: FarmMembership, user: User, farm: Farm) -> None:
    """A non-owner team.manage holder may not act on a peer whose
    role ALSO grants team.manage — otherwise one manager could demote,
    deactivate, or password-reset another (insider lockout/account hijack
    only the owner can undo). The owner is exempt; targets without
    team.manage stay delegable."""
    if user.id == farm.owner_id:
        return
    role = membership.role
    if role is not None and "team.manage" in role.permission_set():
        raise HTTPException(
            status_code=403, detail="Only the farm owner can manage other team managers."
        )


async def _member_count(db: AsyncSession, role_id: int) -> int:
    result = await db.execute(
        select(func.count()).select_from(FarmMembership).where(FarmMembership.role_id == role_id)
    )
    return result.scalar_one()


def _clean_permissions(raw: list[str], allowed: set[str]) -> list[str]:
    """Keep only known codes the editor holds, in catalog order (stable storage)."""
    chosen = {code for code in raw if code in ALL_PERMISSIONS} & allowed
    return [code for _group, codes in PERMISSION_GROUPS for code in codes if code in chosen]


@router.get("")
async def team_page(db: DbSession, farm: CurrentFarm, perms: TEAM_PERM) -> TeamOut:
    memberships = list(
        (
            await db.execute(
                select(FarmMembership)
                .options(selectinload(FarmMembership.user), selectinload(FarmMembership.role))
                .where(FarmMembership.farm_id == farm.id)
                .order_by(FarmMembership.is_active.desc(), FarmMembership.id)
            )
        ).scalars()
    )
    roles = list(
        (await db.execute(select(Role).where(Role.farm_id == farm.id).order_by(Role.id))).scalars()
    )
    member_counts = {role.id: 0 for role in roles}
    for m in memberships:
        if m.role_id in member_counts:
            member_counts[m.role_id] += 1
    return TeamOut(
        memberships=[_membership_out(m) for m in memberships],
        roles=[_role_out(role, member_counts[role.id]) for role in roles],
        permission_groups=[PermissionGroupOut(group=g, codes=c) for g, c in PERMISSION_GROUPS],
        permission_labels=dict(PERMISSIONS),
    )


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------
@router.post("/workers", status_code=201)
async def create_worker(
    payload: WorkerCreateIn, db: DbSession, farm: CurrentFarm, perms: TEAM_PERM
) -> MembershipOut:
    """Add a worker: create a brand-new account, or enroll an existing one
    that owns no farm and belongs to no other farm's team. Accounts and
    passwords are global, so absorbing an account affiliated with another
    farm would hand this farm a cross-tenant takeover path (reset-password
    rewrites the global password). Cross-farm refusals share ONE generic
    message so probing arbitrary emails can't reveal other farms'
    roster state. Residual limitation: there is no invitation/consent flow
    yet — an unaffiliated account is enrolled without the account holder's
    say-so."""
    email = payload.email  # normalized by EmailMixin (strip/lower, shape-checked)
    role = await db.get(Role, payload.role_id) if payload.role_id <= MAX_INT32_ID else None
    if role is None or role.farm_id != farm.id:
        raise HTTPException(status_code=400, detail="Pick a valid role.")
    worker = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()

    if worker is not None and worker.id == farm.owner_id:
        raise HTTPException(
            status_code=400, detail="That is the farm owner — owners already have full access."
        )
    owns_a_farm = (
        (await db.execute(select(Farm).where(Farm.owner_id == worker.id))).scalars().first()
        if worker is not None
        else None
    )
    # One generic refusal for every cross-farm affiliation — the
    # distinct messages ("owns a farm" / "belongs to another farm's team")
    # let any farm owner probe arbitrary emails and learn other farms'
    # roster state. Passwords/accounts are global, so absorbing an account
    # affiliated with another farm would hand this farm a cross-tenant
    # takeover path either way.
    if owns_a_farm is not None:
        # Never absorb someone else's owner account.
        raise HTTPException(status_code=400, detail=CANT_ADD_TO_TEAM)
    if (
        worker is not None
        and (
            await db.execute(
                select(FarmMembership).where(
                    FarmMembership.farm_id == farm.id, FarmMembership.user_id == worker.id
                )
            )
        )
        .scalars()
        .first()
        is not None
    ):
        raise HTTPException(status_code=400, detail="That person is already on this farm's team.")
    if (
        worker is not None
        and (
            await db.execute(
                select(FarmMembership).where(
                    FarmMembership.user_id == worker.id, FarmMembership.farm_id != farm.id
                )
            )
        )
        .scalars()
        .first()
        is not None
    ):
        # Any membership — active or inactive — on another farm ties this
        # account to that farm's access; it may not be absorbed here.
        raise HTTPException(status_code=400, detail=CANT_ADD_TO_TEAM)

    if worker is None:
        password = payload.password or ""
        error = password_policy_error(password)
        if error:
            raise HTTPException(status_code=400, detail=error)
        worker = User(
            email=email,
            name=(payload.name or "").strip() or None,
            password_hash=hash_password(password),
        )
        db.add(worker)
        try:
            await db.flush()
        except IntegrityError:  # same email registered concurrently
            await db.rollback()
            raise HTTPException(
                status_code=400, detail="That email is already registered."
            ) from None

    membership = FarmMembership(farm_id=farm.id, user_id=worker.id, role_id=role.id, is_active=True)
    db.add(membership)
    try:
        await db.commit()
    except IntegrityError:  # same person added to this farm concurrently
        await db.rollback()
        raise HTTPException(
            status_code=400, detail="That person is already on this farm's team."
        ) from None
    return MembershipOut(
        id=membership.id,
        user_id=worker.id,
        email=worker.email,
        name=worker.name,
        role_id=role.id,
        role_name=role.name,
        is_active=membership.is_active,
    )


@router.post("/workers/{membership_id}/role")
async def change_role(
    payload: RoleChangeIn,
    membership_id: int,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: TEAM_PERM,
) -> MembershipOut:
    membership = await _get_membership(db, farm, membership_id)
    # Same self-service guard as the toggle endpoint: holding team.manage
    # must not let a worker promote his own membership to a richer role.
    if membership.user_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot change your own role.")
    _guard_peer_manager(membership, user, farm)
    role = await db.get(Role, payload.role_id) if payload.role_id <= MAX_INT32_ID else None
    if role is None or role.farm_id != farm.id:
        raise HTTPException(status_code=400, detail="Pick a valid role.")
    membership.role = role
    await db.commit()
    return _membership_out(membership)


@router.post("/workers/{membership_id}/toggle")
async def toggle_worker(
    membership_id: int, db: DbSession, user: CurrentUser, farm: CurrentFarm, perms: TEAM_PERM
) -> MembershipOut:
    membership = await _get_membership(db, farm, membership_id)
    if membership.user_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own membership.")
    _guard_peer_manager(membership, user, farm)
    membership.is_active = not membership.is_active
    if not membership.is_active:
        # Deactivation must end the worker's live sessions too, not
        # just block farm data on the next request.
        await revoke_user_sessions(db, membership.user_id)
    await db.commit()
    return _membership_out(membership)


@router.post("/workers/{membership_id}/reset-password")
async def reset_password(
    payload: PasswordResetIn,
    membership_id: int,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: TEAM_PERM,
) -> MembershipOut:
    """Rewrite a worker's GLOBAL password. Restricted to accounts whose sole
    farm affiliation is this one (the accounts this farm created): resetting
    the password of an account tied to another farm would be a cross-tenant
    takeover. All of the worker's live refresh sessions are revoked (HIGH
    0-1) — a reset done because the account is suspect must end its sessions."""
    membership = await _get_membership(db, farm, membership_id)
    _guard_peer_manager(membership, user, farm)
    if not membership.is_active:
        raise HTTPException(
            status_code=400, detail="Reactivate this membership before resetting the password."
        )
    owns_a_farm = (
        (await db.execute(select(Farm).where(Farm.owner_id == membership.user_id)))
        .scalars()
        .first()
    )
    if owns_a_farm is not None:
        # Passwords are global: never rewrite a farm owner's from another farm.
        raise HTTPException(
            status_code=400,
            detail="That user owns a farm — owner passwords cannot be reset here.",
        )
    other_farm = (
        (
            await db.execute(
                select(FarmMembership).where(
                    FarmMembership.user_id == membership.user_id,
                    FarmMembership.farm_id != farm.id,
                )
            )
        )
        .scalars()
        .first()
    )
    if other_farm is not None:
        # Any membership — active or inactive — on another farm means this
        # account's access extends beyond this farm; its global password is
        # not ours to rewrite.
        raise HTTPException(
            status_code=400,
            detail="That account belongs to another farm's team — passwords can't be reset here.",
        )
    error = password_policy_error(payload.password)
    if error:
        raise HTTPException(status_code=400, detail=error)
    membership.user.password_hash = hash_password(payload.password)
    await revoke_user_sessions(db, membership.user_id)  # kill all live sessions
    await db.commit()
    return _membership_out(membership)


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
@router.post("/roles", status_code=201)
async def create_role(
    payload: RoleIn, db: DbSession, farm: CurrentFarm, perms: TEAM_PERM
) -> RoleOut:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required.")
    clash = (
        (await db.execute(select(Role).where(Role.farm_id == farm.id, Role.name == name)))
        .scalars()
        .first()
    )
    if clash is not None:
        raise HTTPException(status_code=400, detail="A role with that name already exists.")
    role = Role(
        farm_id=farm.id,
        code=None,
        name=name,
        description=(payload.description or "").strip() or None,
        permissions=json.dumps(_clean_permissions(payload.permissions, perms)),
    )
    db.add(role)
    try:
        await db.commit()
    except IntegrityError:  # concurrent create with the same name
        await db.rollback()
        raise HTTPException(
            status_code=400, detail="A role with that name already exists."
        ) from None
    return _role_out(role, 0)


@router.put("/roles/{role_id}")
async def update_role(
    payload: RoleIn, role_id: int, db: DbSession, farm: CurrentFarm, perms: TEAM_PERM
) -> RoleOut:
    role = await _get_role(db, farm, role_id)
    name = payload.name.strip()
    clash = (
        (
            await db.execute(
                select(Role).where(Role.farm_id == farm.id, Role.name == name, Role.id != role.id)
            )
        )
        .scalars()
        .first()
    )
    if not name or clash is not None:
        raise HTTPException(
            status_code=400, detail="Name is required and must be unique on this farm."
        )
    role.name = name
    role.description = (payload.description or "").strip() or None
    role.permissions = json.dumps(_clean_permissions(payload.permissions, perms))
    try:
        await db.commit()
    except IntegrityError:  # concurrent rename collided with another role
        await db.rollback()
        raise HTTPException(
            status_code=400, detail="Name is required and must be unique on this farm."
        ) from None
    return _role_out(role, await _member_count(db, role.id))


@router.delete("/roles/{role_id}", status_code=204)
async def delete_role(role_id: int, db: DbSession, farm: CurrentFarm, perms: TEAM_PERM) -> Response:
    role = await _get_role(db, farm, role_id)
    if role.code is not None:
        # Startup seeding re-creates presets, so deleting one would not stick.
        raise HTTPException(status_code=400, detail="Preset roles can't be deleted.")
    if await _member_count(db, role.id) > 0:
        raise HTTPException(
            status_code=400, detail="Role still has workers assigned — reassign them first."
        )
    # Leave historical task attribution readable: unassign, don't dangle.
    await db.execute(
        update(Task).where(Task.assigned_role_id == role.id).values(assigned_role_id=None)
    )
    await db.delete(role)
    try:
        await db.commit()
    except IntegrityError:
        # A worker was assigned this role between the member-count
        # check above and the DELETE (FK violation) — answer like the
        # pre-check instead of 500ing.
        await db.rollback()
        raise HTTPException(
            status_code=400, detail="Role still has workers assigned — reassign them first."
        ) from None
    return Response(status_code=204)
