"""Team management (requires team.manage — the owner by default): workers,
their roles, password resets, and the farm's custom/preset roles."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm, revoke_user_sessions
from ..models import Farm, FarmMembership, Role, Task, User
from ..models.enums import TaskStatus
from ..permissions import (
    ALL_PERMISSIONS,
    PERMISSION_DEPENDENCIES,
    PERMISSION_GROUPS,
    PERMISSIONS,
)
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

# Generic refusal for any pre-existing account in create_worker. Until an
# email-verified invitation/acceptance flow exists, a farm must never silently
# attach somebody else's global identity to its roster.
CANT_ADD_TO_TEAM = "That email can't be added to this farm's team."
RESET_PASSWORD_INACTIVE_REASON = "Reactivate this membership before resetting the password."
RESET_PASSWORD_SELF_SERVICE_REASON = "This account must use self-service password recovery."


def _reset_password_policy(
    membership: FarmMembership, *, owns_farm: bool, has_other_membership: bool
) -> tuple[bool, str | None]:
    """Return the stable, tenant-safe password-reset capability contract."""
    if not membership.is_active:
        return False, RESET_PASSWORD_INACTIVE_REASON
    if not membership.account_provisioned_by_farm or owns_farm or has_other_membership:
        # Do not reveal whether the global identity owns a farm, belongs to a
        # different team, or predates secure provisioning.
        return False, RESET_PASSWORD_SELF_SERVICE_REASON
    return True, None


async def _reset_password_policy_for_membership(
    db: AsyncSession, membership: FarmMembership
) -> tuple[bool, str | None]:
    """Resolve global affiliations in one bounded query for mutation responses."""
    if not membership.is_active or not membership.account_provisioned_by_farm:
        return _reset_password_policy(membership, owns_farm=False, has_other_membership=False)
    affiliations = (
        await db.execute(
            select(
                select(Farm.id)
                .where(Farm.owner_id == membership.user_id)
                .exists()
                .label("owns_farm"),
                select(FarmMembership.id)
                .where(
                    FarmMembership.user_id == membership.user_id,
                    FarmMembership.farm_id != membership.farm_id,
                )
                .exists()
                .label("has_other_membership"),
            )
        )
    ).one()
    return _reset_password_policy(
        membership,
        owns_farm=bool(affiliations.owns_farm),
        has_other_membership=bool(affiliations.has_other_membership),
    )


def _membership_out(
    membership: FarmMembership, reset_policy: tuple[bool, str | None]
) -> MembershipOut:
    role = membership.role
    can_reset_password, reset_password_block_reason = reset_policy
    return MembershipOut(
        id=membership.id,
        user_id=membership.user_id,
        email=membership.user.email,
        name=membership.user.name,
        role_id=membership.role_id,
        role_name=role.name if role else None,
        is_active=membership.is_active,
        can_reset_password=can_reset_password,
        reset_password_block_reason=reset_password_block_reason,
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


async def _get_membership(
    db: AsyncSession, farm: Farm, membership_id: int, *, for_update: bool = False
) -> FarmMembership:
    # Ids above the int4 PK ceiling cannot exist — 404, never an asyncpg
    # int32 DataError (500).
    if membership_id > MAX_INT32_ID:
        raise HTTPException(status_code=404, detail="Membership not found")
    statement = (
        select(FarmMembership)
        .options(selectinload(FarmMembership.user), selectinload(FarmMembership.role))
        .where(FarmMembership.id == membership_id)
    )
    if for_update:
        statement = statement.with_for_update()
    result = await db.execute(statement)
    membership = result.scalar_one_or_none()
    if membership is None or membership.farm_id != farm.id:
        raise HTTPException(status_code=404, detail="Membership not found")
    return membership


async def _get_role(
    db: AsyncSession, farm: Farm, role_id: int, *, for_update: bool = False
) -> Role:
    statement = select(Role).where(Role.id == role_id)
    if for_update:
        statement = statement.with_for_update()
    role = (await db.execute(statement)).scalar_one_or_none() if role_id <= MAX_INT32_ID else None
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


def _guard_manager_role(role: Role, user: User, farm: Farm) -> None:
    """Only the owner may assign, edit, or delete a role that can manage team.

    `team.manage` includes global-password resets and roster administration;
    allowing one holder to create another holder is privilege delegation, not
    ordinary worker management.
    """
    if user.id != farm.owner_id and "team.manage" in role.permission_set():
        raise HTTPException(
            status_code=403, detail="Only the farm owner can manage team-manager roles."
        )


def _guard_manager_permission(raw: list[str], user: User, farm: Farm) -> None:
    if user.id != farm.owner_id and "team.manage" in raw:
        raise HTTPException(
            status_code=403, detail="Only the farm owner can grant team management."
        )


async def _member_count(db: AsyncSession, role_id: int) -> int:
    result = await db.execute(
        select(func.count()).select_from(FarmMembership).where(FarmMembership.role_id == role_id)
    )
    return result.scalar_one()


def _clean_permissions(
    raw: list[str], allowed: set[str], *, preserve: set[str] | None = None
) -> list[str]:
    """Apply only permissions the editor controls, preserving the rest."""
    chosen = ({code for code in raw if code in ALL_PERMISSIONS} & allowed) | (
        (preserve or set()) - allowed
    )
    for action, required_view in PERMISSION_DEPENDENCIES.items():
        if action in chosen and required_view not in chosen:
            raise HTTPException(
                status_code=400,
                detail=f"Permission '{action}' requires '{required_view}'.",
            )
    return [code for _group, codes in PERMISSION_GROUPS for code in codes if code in chosen]


@router.get("")
async def team_page(db: DbSession, farm: CurrentFarm, perms: TEAM_PERM) -> TeamOut:
    other_membership = aliased(FarmMembership)
    membership_rows = await db.execute(
        select(
            FarmMembership,
            select(Farm.id)
            .where(Farm.owner_id == FarmMembership.user_id)
            .exists()
            .label("owns_farm"),
            select(other_membership.id)
            .where(
                other_membership.user_id == FarmMembership.user_id,
                other_membership.farm_id != farm.id,
            )
            .exists()
            .label("has_other_membership"),
        )
        .options(selectinload(FarmMembership.user), selectinload(FarmMembership.role))
        .where(FarmMembership.farm_id == farm.id)
        .order_by(FarmMembership.is_active.desc(), FarmMembership.id)
    )
    rows = membership_rows.all()
    memberships = [row[0] for row in rows]
    roles = list(
        (await db.execute(select(Role).where(Role.farm_id == farm.id).order_by(Role.id))).scalars()
    )
    member_counts = {role.id: 0 for role in roles}
    for m in memberships:
        if m.role_id in member_counts:
            member_counts[m.role_id] += 1
    return TeamOut(
        memberships=[
            _membership_out(
                row[0],
                _reset_password_policy(
                    row[0], owns_farm=bool(row[1]), has_other_membership=bool(row[2])
                ),
            )
            for row in rows
        ],
        roles=[_role_out(role, member_counts[role.id]) for role in roles],
        permission_groups=[PermissionGroupOut(group=g, codes=c) for g, c in PERMISSION_GROUPS],
        permission_labels=dict(PERMISSIONS),
    )


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------
@router.post("/workers", status_code=201)
async def create_worker(
    payload: WorkerCreateIn,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: TEAM_PERM,
) -> MembershipOut:
    """Provision a new worker account owned by this farm.

    A pre-existing global account is always refused. Supporting those users
    safely requires an invitation that they explicitly accept while signed in
    (or through a verified one-time email link); silent enrollment is an
    account-takeover primitive because farm managers can reset provisioned
    worker passwords.
    """
    email = payload.email  # normalized by EmailMixin (strip/lower, shape-checked)
    try:
        role = await _get_role(db, farm, payload.role_id, for_update=True)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=400, detail="Pick a valid role.") from None
        raise
    _guard_manager_role(role, user, farm)
    worker = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if worker is not None:
        # One response for owner, worker, and otherwise unaffiliated accounts:
        # do not turn team management into an account/tenant enumeration API.
        raise HTTPException(status_code=400, detail=CANT_ADD_TO_TEAM)

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
    except IntegrityError:  # account registered/provisioned concurrently
        await db.rollback()
        raise HTTPException(status_code=400, detail=CANT_ADD_TO_TEAM) from None

    membership = FarmMembership(
        farm_id=farm.id,
        user_id=worker.id,
        role_id=role.id,
        is_active=True,
        account_provisioned_by_farm=True,
    )
    db.add(membership)
    try:
        await db.commit()
    except IntegrityError:  # defensive: same person/farm raced through an external writer
        await db.rollback()
        raise HTTPException(status_code=400, detail=CANT_ADD_TO_TEAM) from None
    return MembershipOut(
        id=membership.id,
        user_id=worker.id,
        email=worker.email,
        name=worker.name,
        role_id=role.id,
        role_name=role.name,
        is_active=membership.is_active,
        can_reset_password=True,
        reset_password_block_reason=None,
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
    membership = await _get_membership(db, farm, membership_id, for_update=True)
    # Same self-service guard as the toggle endpoint: holding team.manage
    # must not let a worker promote his own membership to a richer role.
    if membership.user_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot change your own role.")
    _guard_peer_manager(membership, user, farm)
    try:
        role = await _get_role(db, farm, payload.role_id, for_update=True)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=400, detail="Pick a valid role.") from None
        raise
    _guard_manager_role(role, user, farm)
    membership.role = role
    await db.commit()
    return _membership_out(membership, await _reset_password_policy_for_membership(db, membership))


@router.post("/workers/{membership_id}/toggle")
async def toggle_worker(
    membership_id: int, db: DbSession, user: CurrentUser, farm: CurrentFarm, perms: TEAM_PERM
) -> MembershipOut:
    membership = await _get_membership(db, farm, membership_id, for_update=True)
    if membership.user_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own membership.")
    _guard_peer_manager(membership, user, farm)
    membership.is_active = not membership.is_active
    if not membership.is_active:
        # Preserve actionable ownership for pending duties: a worker-specific
        # assignment becomes a role assignment (without overwriting an already
        # explicit role), atomically with the membership deactivation.
        await db.execute(
            update(Task)
            .where(
                Task.farm_id == farm.id,
                Task.assigned_user_id == membership.user_id,
                Task.status == TaskStatus.PENDING.value,
            )
            .values(
                assigned_user_id=None,
                assigned_role_id=case(
                    (Task.assigned_role_id.is_(None), membership.role_id),
                    else_=Task.assigned_role_id,
                ),
            )
        )
        # Deactivation must end live refresh and access tokens immediately.
        locked_user = (
            await db.execute(select(User).where(User.id == membership.user_id).with_for_update())
        ).scalar_one()
        locked_user.token_version += 1
        await revoke_user_sessions(db, membership.user_id)
    await db.commit()
    return _membership_out(membership, await _reset_password_policy_for_membership(db, membership))


@router.post("/workers/{membership_id}/reset-password")
async def reset_password(
    payload: PasswordResetIn,
    membership_id: int,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: TEAM_PERM,
) -> MembershipOut:
    """Reset only a global account this farm demonstrably provisioned."""
    membership = await _get_membership(db, farm, membership_id, for_update=True)
    _guard_peer_manager(membership, user, farm)
    # Serialize with farm creation and new foreign-key affiliations before the
    # eligibility query; otherwise an account could gain a global affiliation
    # between the check and the password rewrite.
    locked_user = (
        await db.execute(select(User).where(User.id == membership.user_id).with_for_update())
    ).scalar_one()
    reset_policy = await _reset_password_policy_for_membership(db, membership)
    if not reset_policy[0]:
        raise HTTPException(status_code=400, detail=reset_policy[1])
    error = password_policy_error(payload.password)
    if error:
        raise HTTPException(status_code=400, detail=error)
    locked_user.password_hash = hash_password(payload.password)
    locked_user.token_version += 1
    await revoke_user_sessions(db, membership.user_id)
    await db.commit()
    return _membership_out(membership, reset_policy)


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
@router.post("/roles", status_code=201)
async def create_role(
    payload: RoleIn,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: TEAM_PERM,
) -> RoleOut:
    _guard_manager_permission(payload.permissions, user, farm)
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
    payload: RoleIn,
    role_id: int,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: TEAM_PERM,
) -> RoleOut:
    role = await _get_role(db, farm, role_id, for_update=True)
    _guard_manager_role(role, user, farm)
    _guard_manager_permission(payload.permissions, user, farm)
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
    role.permissions = json.dumps(
        _clean_permissions(payload.permissions, perms, preserve=role.permission_set())
    )
    try:
        await db.commit()
    except IntegrityError:  # concurrent rename collided with another role
        await db.rollback()
        raise HTTPException(
            status_code=400, detail="Name is required and must be unique on this farm."
        ) from None
    return _role_out(role, await _member_count(db, role.id))


@router.delete("/roles/{role_id}", status_code=204)
async def delete_role(
    role_id: int,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: TEAM_PERM,
) -> Response:
    role = await _get_role(db, farm, role_id, for_update=True)
    _guard_manager_role(role, user, farm)
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
