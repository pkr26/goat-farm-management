"""Team management (requires team.manage — the owner by default): workers,
their roles, password resets, and the farm's custom/preset roles."""

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from ..core.config import get_settings
from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm, revoke_user_sessions
from ..models import (
    Farm,
    FarmMembership,
    Role,
    Task,
    User,
)
from ..permissions import (
    ALL_PERMISSIONS,
    PERMISSION_DEPENDENCIES,
    PERMISSION_GROUPS,
    PERMISSIONS,
)
from ..ratelimit import auth_limiter
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
    WorkerStatusIn,
)
from ..security import PasswordWorkCapacityError, hash_password_async, password_policy_error
from ..services import IdempotencyKey, execute_idempotent
from ..services.idempotency import replay_idempotent_if_committed
from ..utils import utcnow

router = APIRouter(prefix="/api/team", tags=["team"])
logger = logging.getLogger("goatfarm.team")

TEAM_PERM = Annotated[set[str], Depends(require_perm("team.manage"))]

# Generic refusal for any pre-existing account in create_worker. Until an
# email-verified invitation/acceptance flow exists, a farm must never silently
# attach somebody else's global identity to its roster.
CANT_ADD_TO_TEAM = "That email can't be added to this farm's team."
RESET_PASSWORD_INACTIVE_REASON = "Reactivate this membership before resetting the password."
RESET_PASSWORD_SELF_SERVICE_REASON = "This account must use self-service password recovery."
RESET_PASSWORD_OWNER_ONLY_REASON = "Only the farm owner can reset worker passwords."
ROLE_SCOPE_REASON = "You can only manage workers and roles within your own permissions."
TEAM_CAPACITY_REASON = "This farm has reached its team-member limit."
ROLE_CAPACITY_REASON = "This farm has reached its role limit."
CREATE_WORKER_OWNER_ONLY_REASON = "Only the farm owner can create worker accounts."
TEAM_RESPONSE_OVERFLOW_REASON = (
    "This farm exceeds the configured team response limit; "
    "archive legacy memberships before retrying."
)
ROLE_RESPONSE_OVERFLOW_REASON = (
    "This farm exceeds the configured role response limit; archive legacy roles before retrying."
)
TEAM_PASSWORD_WORK_LIMIT_REASON = "Too many worker password operations — please try again later."
TEAM_PASSWORD_WORK_SCOPE = "team-password-work"
TEAM_PASSWORD_RESERVATION_SCOPE = "team-password-work-in-flight"
MAX_WORKER_IDEMPOTENCY_GATES = 10_000

# Namespace for the per-farm team/role provisioning mutex. Advisory lock keys
# are global to the database, so every acquisition of it must pass this.
TEAM_PROVISIONING_LOCK_NAMESPACE = 4712


@dataclass
class _WorkerIdempotencyGate:
    lock: asyncio.Lock
    users: int = 0


_worker_idempotency_gates: dict[tuple[int, int, str], _WorkerIdempotencyGate] = {}


@asynccontextmanager
async def _serialize_worker_idempotency(
    *,
    farm_id: int,
    actor_id: int,
    key: str | None,
) -> AsyncIterator[None]:
    """Coalesce same-key preparation without retaining a DB connection.

    The deployment runs one API worker because auth throttles are in-memory.
    This small in-process gate therefore prevents simultaneous retries from
    repeating Argon work. PostgreSQL idempotency remains authoritative across
    restarts/processes, so correctness never depends on this optimization.
    """
    if key is None:
        yield
        return
    identity = (farm_id, actor_id, hashlib.sha256(key.encode("ascii")).hexdigest())
    gate = _worker_idempotency_gates.get(identity)
    if gate is None:
        if len(_worker_idempotency_gates) >= MAX_WORKER_IDEMPOTENCY_GATES:
            # Correctness still comes from PostgreSQL; only the local
            # duplicate-work optimization is skipped at the memory ceiling.
            yield
            return
        gate = _WorkerIdempotencyGate(lock=asyncio.Lock())
        _worker_idempotency_gates[identity] = gate
    # No await occurs between lookup and increment; on the single event-loop
    # thread this is an atomic reference acquisition.
    gate.users += 1
    try:
        async with gate.lock:
            yield
    finally:
        gate.users -= 1
        if gate.users == 0 and _worker_idempotency_gates.get(identity) is gate:
            _worker_idempotency_gates.pop(identity, None)


@dataclass(frozen=True)
class PreparedWorkerCreate:
    payload: WorkerCreateIn
    actor_id: int
    actor_token_version: int
    farm_id: int


@dataclass(frozen=True)
class PreparedPasswordReset:
    payload: PasswordResetIn
    password_hash: str
    actor_id: int
    actor_token_version: int
    farm_id: int


def _team_password_work_throttled(actor_id: int) -> bool:
    settings = get_settings()
    return settings.auth_rate_limit_enabled and auth_limiter.is_blocked(
        TEAM_PASSWORD_WORK_SCOPE,
        str(actor_id),
        settings.auth_rate_limit_max_attempts,
        settings.auth_rate_limit_window_seconds,
    )


def _team_password_rate_error(actor_id: int) -> HTTPException:
    logger.info("team password work throttled (actor_id=%s)", actor_id)
    return HTTPException(
        status_code=429,
        detail=TEAM_PASSWORD_WORK_LIMIT_REASON,
        headers={"Retry-After": str(get_settings().auth_rate_limit_window_seconds)},
    )


async def _hash_team_password(password: str, *, actor_id: int) -> str:
    """Admit one bounded Argon workflow per owner and charge it up front.

    The global password pool is deliberately non-queuing. Without this
    actor-level admission, one authenticated owner can occupy every Argon slot
    with worker creates/resets and make unrelated logins fail. Create and reset
    share both the in-flight reservation and sliding-window budget so switching
    endpoints or farms cannot bypass either control.
    """
    key = str(actor_id)
    if _team_password_work_throttled(actor_id):
        raise _team_password_rate_error(actor_id)
    if not auth_limiter.try_reserve(TEAM_PASSWORD_RESERVATION_SCOPE, key):
        raise PasswordWorkCapacityError("Owner already has password work in flight")
    work: asyncio.Task[str] | None = None
    try:
        # Close the check/reserve race before expensive work. The reservation
        # is synchronous and shared by both team password endpoints.
        if _team_password_work_throttled(actor_id):
            raise _team_password_rate_error(actor_id)
        settings = get_settings()
        if settings.auth_rate_limit_enabled:
            auth_limiter.record(
                TEAM_PASSWORD_WORK_SCOPE,
                key,
                settings.auth_rate_limit_window_seconds,
                max_attempts=settings.auth_rate_limit_max_attempts,
            )
        # Keep the owner reservation until native Argon work really finishes.
        # Cancelling/disconnecting an HTTP request must not release this slot
        # while security._run_password_work intentionally continues its shielded
        # thread; otherwise one owner can cancel/retry to occupy every global
        # worker despite the per-owner admission rule.
        work = asyncio.create_task(hash_password_async(password))

        def release_when_finished(finished: asyncio.Task[str]) -> None:
            auth_limiter.release(TEAM_PASSWORD_RESERVATION_SCOPE, key)
            # A disconnected caller no longer awaits the task. Retrieve a late
            # exception to avoid an unhandled-task warning; the HTTP response is
            # already gone, so there is nowhere else to propagate it.
            if not finished.cancelled():
                finished.exception()

        try:
            return await asyncio.shield(work)
        finally:
            if work.done():
                release_when_finished(work)
            else:
                work.add_done_callback(release_when_finished)
    finally:
        # Admission failures occur before ``work`` exists and must release
        # synchronously. Once work starts, its completion callback owns release.
        if work is None:
            auth_limiter.release(TEAM_PASSWORD_RESERVATION_SCOPE, key)


async def _preflight_worker_create(db: AsyncSession, farm: Farm, payload: WorkerCreateIn) -> None:
    """Reject cheap farm-local failures before consuming an Argon slot.

    These observations are rechecked under the Farm/Role locks in the final
    mutation. Global email existence stays after hashing so this preflight
    does not create a timing-based account-enumeration signal.
    """
    settings = get_settings()
    count = (
        await db.execute(
            select(func.count())
            .select_from(FarmMembership)
            .join(User, User.id == FarmMembership.user_id)
            .where(FarmMembership.farm_id == farm.id, User.deleted_at.is_(None))
        )
    ).scalar_one()
    if count >= settings.max_team_members_per_farm:
        raise HTTPException(status_code=409, detail=TEAM_CAPACITY_REASON)
    try:
        await _get_role(db, farm, payload.role_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=400, detail="Pick a valid role.") from None
        raise


async def _prepare_worker_create(
    payload: WorkerCreateIn,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: TEAM_PERM,
) -> PreparedWorkerCreate:
    """Authorize and snapshot the owner before idempotency arbitration.

    Mutable capacity/role checks and password work intentionally happen inside
    the idempotent mutation.  An already-committed retry must be replayed before
    those preconditions are re-evaluated, and simultaneous duplicates must let
    PostgreSQL choose one claimant before either repeats Argon work.
    """
    if user.id != farm.owner_id:
        raise HTTPException(status_code=403, detail=CREATE_WORKER_OWNER_ONLY_REASON)
    actor_id = user.id
    actor_token_version = user.token_version
    farm_id = farm.id
    # CurrentFarm pins the canonical owner authorization bundle on unsafe
    # requests. End that transaction, then let the route reauthorize this exact
    # principal snapshot immediately before idempotency/farm arbitration.
    await db.rollback()
    return PreparedWorkerCreate(
        payload=payload,
        actor_id=actor_id,
        actor_token_version=actor_token_version,
        farm_id=farm_id,
    )


async def _prepare_password_reset(
    payload: PasswordResetIn,
    membership_id: int,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: TEAM_PERM,
) -> PreparedPasswordReset:
    """Authorize the owner, then hash without holding a DB connection."""
    if user.id != farm.owner_id:
        raise HTTPException(status_code=403, detail=RESET_PASSWORD_OWNER_ONLY_REASON)
    error = password_policy_error(payload.password)
    if error:
        raise HTTPException(status_code=400, detail=error)
    # Resolve bounds, tenant ownership and the advertised reset policy before
    # Argon. Every condition is revalidated under mutation locks after hashing.
    membership = await _get_membership(db, farm, membership_id)
    reset_policy = await _reset_password_policy_for_membership(db, membership)
    if not reset_policy[0]:
        raise HTTPException(status_code=400, detail=reset_policy[1])
    actor_id = user.id
    actor_token_version = user.token_version
    farm_id = farm.id
    await db.rollback()
    return PreparedPasswordReset(
        payload=payload,
        password_hash=await _hash_team_password(payload.password, actor_id=actor_id),
        actor_id=actor_id,
        actor_token_version=actor_token_version,
        farm_id=farm_id,
    )


PreparedWorkerCreateDep = Annotated[PreparedWorkerCreate, Depends(_prepare_worker_create)]
PreparedPasswordResetDep = Annotated[PreparedPasswordReset, Depends(_prepare_password_reset)]


async def _reauthorize_prepared_owner(
    db: AsyncSession,
    *,
    actor_id: int,
    actor_token_version: int,
    farm_id: int,
) -> tuple[User, Farm]:
    """Revalidate the exact owner snapshot after off-transaction preparation."""
    user = (
        await db.execute(
            select(User)
            .where(
                User.id == actor_id,
                User.token_version == actor_token_version,
                User.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="Session has been revoked")
    farm = (
        await db.execute(select(Farm).where(Farm.id == farm_id, Farm.owner_id == actor_id))
    ).scalar_one_or_none()
    if farm is None:
        # Match CurrentFarm's non-enumerating response if ownership changed or
        # the farm vanished while the password was being hashed.
        raise HTTPException(status_code=404, detail="Farm not found")
    return user, farm


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
    membership: FarmMembership,
    reset_policy: tuple[bool, str | None],
    user: User,
    farm: Farm,
) -> MembershipOut:
    role = membership.role
    if user.id != farm.owner_id:
        # Passwords belong to the global User, not this farm-local membership.
        # A delegated team manager may administer the roster, but only the
        # owner may take over a provisioned account's credentials.
        reset_policy = (False, RESET_PASSWORD_OWNER_ONLY_REASON)
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
    db: AsyncSession,
    farm: Farm,
    membership_id: int,
    *,
    for_update: bool = False,
    no_key_update: bool = False,
) -> FarmMembership:
    # Ids above the int4 PK ceiling cannot exist — 404, never an asyncpg
    # int32 DataError (500).
    if not 1 <= membership_id <= MAX_INT32_ID:
        raise HTTPException(status_code=404, detail="Membership not found")
    statement = (
        select(FarmMembership)
        .options(selectinload(FarmMembership.user), selectinload(FarmMembership.role))
        .where(
            FarmMembership.id == membership_id,
            FarmMembership.farm_id == farm.id,
            FarmMembership.user.has(User.deleted_at.is_(None)),
        )
    )
    if for_update:
        # SQLAlchemy's key_share=True without read=True renders PostgreSQL
        # FOR NO KEY UPDATE. It serializes is_active changes but remains
        # compatible with the KEY SHARE lock acquired by a Task FK insert.
        statement = statement.with_for_update(key_share=no_key_update)
        statement = statement.execution_options(populate_existing=True)
    result = await db.execute(statement)
    membership = result.scalar_one_or_none()
    if membership is None or membership.farm_id != farm.id:
        raise HTTPException(status_code=404, detail="Membership not found")
    return membership


async def _get_role(
    db: AsyncSession,
    farm: Farm,
    role_id: int,
    *,
    for_update: bool = False,
    no_key_update: bool = False,
    for_share: bool = False,
) -> Role:
    statement = select(Role).where(
        Role.id == role_id,
        Role.farm_id == farm.id,
        Role.deleted_at.is_(None),
    )
    if for_update:
        statement = statement.with_for_update(key_share=no_key_update)
        statement = statement.execution_options(populate_existing=True)
    elif for_share:
        statement = statement.with_for_update(read=True)
        statement = statement.execution_options(populate_existing=True)
    role = (
        (await db.execute(statement)).scalar_one_or_none() if 1 <= role_id <= MAX_INT32_ID else None
    )
    if role is None or role.farm_id != farm.id:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


async def _pin_membership_role(
    db: AsyncSession,
    farm: Farm,
    membership: FarmMembership,
) -> Role:
    """Durably revalidate a target membership's effective role.

    Team lifecycle routes lock Membership first, then Role FOR SHARE. Permission
    edits therefore either win first and are seen by the peer-manager guards,
    or wait until the authorized lifecycle change commits. SHARE is sufficient
    because these routes do not edit Role and avoids role-to-role upgrade
    cycles when two workers exchange roles concurrently.
    """
    role = await _get_role(db, farm, membership.role_id, for_share=True)
    membership.role = role
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


def _guard_role_scope(role: Role | None, perms: set[str], user: User, farm: Farm) -> None:
    """Keep delegated roster administration below the caller's RBAC ceiling.

    A non-owner with ``team.manage`` may create or reassign workers only into
    roles whose complete effective permission set they already hold. Otherwise
    they can provision an alternate account with a richer role and log into it.
    The same guard on a target's current role prevents a delegated manager from
    taking control of an already more-privileged account through reassignment.
    """
    if user.id == farm.owner_id or role is None:
        return
    if not role.permission_set().issubset(perms):
        raise HTTPException(status_code=403, detail=ROLE_SCOPE_REASON)


def _guard_manager_permission(raw: list[str], user: User, farm: Farm) -> None:
    if user.id != farm.owner_id and "team.manage" in raw:
        raise HTTPException(
            status_code=403, detail="Only the farm owner can grant team management."
        )


async def _member_count(db: AsyncSession, role_id: int) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(FarmMembership)
        .join(User, User.id == FarmMembership.user_id)
        .where(FarmMembership.role_id == role_id, User.deleted_at.is_(None))
    )
    return result.scalar_one()


async def _lock_farm_provisioning(db: AsyncSession, farm: Farm) -> None:
    """Take the per-farm serialization lock for team/role provisioning.

    Deliberately an ADVISORY lock, not `SELECT farms.id ... FOR UPDATE`.
    Every farm-scoped child insert — a weight record, a feed dispense, a
    finance transaction, and the idempotency claim these routes make
    themselves — takes FOR KEY SHARE on that same Farm row, so a FOR UPDATE
    held for the rest of the request transaction serializes *all* tenant
    writes, not just the counted resource. A transaction-scoped advisory lock
    self-conflicts exactly like the row lock did (so the ceilings stay real
    bounds), never conflicts with an FK key-share lock, and needs no
    KEY SHARE -> stronger upgrade, so distinct idempotency keys still cannot
    deadlock against each other.
    """
    await db.execute(
        select(
            func.pg_advisory_xact_lock(literal(TEAM_PROVISIONING_LOCK_NAMESPACE), literal(farm.id))
        )
    )


async def _guard_farm_capacity(
    db: AsyncSession, farm: Farm, *, model: type[FarmMembership] | type[Role], limit: int
) -> None:
    """Serialize per-farm count-and-create operations.

    A plain COUNT followed by INSERT is raceable: simultaneous requests could
    all observe one free slot. The provisioning lock makes the configured
    ceiling an actual concurrency-safe bound without taking a table-wide lock.
    """
    await _lock_farm_provisioning(db, farm)
    if model is FarmMembership:
        count_statement = (
            select(func.count())
            .select_from(FarmMembership)
            .join(User, User.id == FarmMembership.user_id)
            .where(FarmMembership.farm_id == farm.id, User.deleted_at.is_(None))
        )
    else:
        count_statement = (
            select(func.count())
            .select_from(Role)
            .where(Role.farm_id == farm.id, Role.deleted_at.is_(None))
        )
    count = (await db.execute(count_statement)).scalar_one()
    if count >= limit:
        reason = TEAM_CAPACITY_REASON if model is FarmMembership else ROLE_CAPACITY_REASON
        raise HTTPException(status_code=409, detail=reason)


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
async def team_page(
    db: DbSession, user: CurrentUser, farm: CurrentFarm, perms: TEAM_PERM
) -> TeamOut:
    settings = get_settings()
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
        .join(User, User.id == FarmMembership.user_id)
        .where(FarmMembership.farm_id == farm.id, User.deleted_at.is_(None))
        .order_by(FarmMembership.is_active.desc(), FarmMembership.id)
        .limit(settings.max_team_members_per_farm + 1)
    )
    rows = membership_rows.all()
    if len(rows) > settings.max_team_members_per_farm:
        raise HTTPException(status_code=409, detail=TEAM_RESPONSE_OVERFLOW_REASON)
    memberships = [row[0] for row in rows]
    roles = list(
        (
            await db.execute(
                select(Role)
                .where(Role.farm_id == farm.id, Role.deleted_at.is_(None))
                .order_by(Role.id)
                .limit(settings.max_roles_per_farm + 1)
            )
        ).scalars()
    )
    if len(roles) > settings.max_roles_per_farm:
        raise HTTPException(status_code=409, detail=ROLE_RESPONSE_OVERFLOW_REASON)
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
                user,
                farm,
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
    prepared: PreparedWorkerCreateDep,
    response: Response,
    db: DbSession,
    idempotency_key: IdempotencyKey = None,
) -> MembershipOut:
    """Provision a new worker account owned by this farm.

    A pre-existing global account is always refused. Supporting those users
    safely requires an invitation that they explicitly accept while signed in
    (or through a verified one-time email link); silent enrollment is an
    account-takeover primitive because farm managers can reset provisioned
    worker passwords.
    """
    async with _serialize_worker_idempotency(
        farm_id=prepared.farm_id,
        actor_id=prepared.actor_id,
        key=idempotency_key,
    ):
        return await _create_worker_after_idempotency_gate(
            prepared=prepared,
            response=response,
            db=db,
            idempotency_key=idempotency_key,
        )


async def _create_worker_after_idempotency_gate(
    *,
    prepared: PreparedWorkerCreate,
    response: Response,
    db: AsyncSession,
    idempotency_key: str | None,
) -> MembershipOut:
    payload = prepared.payload
    replay: MembershipOut | None = None
    try:
        user, farm = await _reauthorize_prepared_owner(
            db,
            actor_id=prepared.actor_id,
            actor_token_version=prepared.actor_token_version,
            farm_id=prepared.farm_id,
        )
        # Resolve an already-committed response before mutable password,
        # capacity, or role policy. The lookup is read-only and the enclosing
        # authorization transaction is rolled back before Argon work.
        if idempotency_key is not None:
            replay = await replay_idempotent_if_committed(
                db,
                http_response=response,
                key=idempotency_key,
                farm_id=farm.id,
                actor_id=user.id,
                operation="team.workers.create",
                payload=payload,
                path_identity={},
                response_type=MembershipOut,
            )
        if replay is None:
            password = payload.password or ""
            error = password_policy_error(password)
            if error:
                raise HTTPException(status_code=400, detail=error)
            await _preflight_worker_create(db, farm, payload)
    finally:
        # Releases the User FOR SHARE pin and every connection before the
        # memory-hard password operation starts, including on replay/conflict.
        await db.rollback()

    if replay is not None:
        return replay

    password_hash = await _hash_team_password(
        payload.password or "",
        actor_id=prepared.actor_id,
    )
    # Revocation, ownership transfer, role deletion, and capacity are all
    # rechecked after off-transaction preparation before mutation.
    user, farm = await _reauthorize_prepared_owner(
        db,
        actor_id=prepared.actor_id,
        actor_token_version=prepared.actor_token_version,
        farm_id=prepared.farm_id,
    )

    async def mutate() -> MembershipOut:
        await _guard_farm_capacity(
            db,
            farm,
            model=FarmMembership,
            limit=get_settings().max_team_members_per_farm,
        )
        email = payload.email  # normalized by EmailMixin (strip/lower, shape-checked)
        try:
            role = await _get_role(db, farm, payload.role_id, for_share=True)
        except HTTPException as exc:
            if exc.status_code == 404:
                raise HTTPException(status_code=400, detail="Pick a valid role.") from None
            raise
        # No _guard_role_scope call here: worker creation is owner-only
        # (_prepare_worker_create rejects every non-owner), and the guard is a
        # no-op for the owner anyway. Passing ALL_PERMISSIONS as the ceiling
        # made it doubly vacuous — dead code that reads like a live RBAC
        # control is worse than none, so the real guard stays on the
        # role-reassignment paths where a delegated manager can actually reach it.
        _guard_manager_role(role, user, farm)
        worker = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if worker is not None:
            # One response for owner, worker, and otherwise unaffiliated
            # accounts: do not turn team management into an account/tenant
            # enumeration API.
            raise HTTPException(status_code=400, detail=CANT_ADD_TO_TEAM)

        worker = User(
            email=email,
            name=(payload.name or "").strip() or None,
            password_hash=password_hash,
        )
        db.add(worker)
        try:
            await db.flush()
        except IntegrityError:  # account registered/provisioned concurrently
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
            await db.flush()
        except IntegrityError:  # defensive external-writer race
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

    # Only a keyed HMAC-SHA-256 request fingerprint is persisted; neither the
    # raw password nor its Argon hash enters the idempotency record/response.
    # PostgreSQL still arbitrates a cross-process race after preparation.
    await _lock_farm_provisioning(db, farm)
    return await execute_idempotent(
        db,
        http_response=response,
        key=idempotency_key,
        farm_id=farm.id,
        actor_id=user.id,
        operation="team.workers.create",
        payload=payload,
        path_identity={},
        success_status=201,
        response_type=MembershipOut,
        mutate=mutate,
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
    if user.id != farm.owner_id:
        preflight_membership = await _get_membership(db, farm, membership_id)
        if preflight_membership.user_id == user.id:
            raise HTTPException(status_code=400, detail="You cannot change your own role.")
        _guard_peer_manager(preflight_membership, user, farm)
        try:
            preflight_role = await _get_role(db, farm, payload.role_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                raise HTTPException(status_code=400, detail="Pick a valid role.") from None
            raise
        _guard_manager_role(preflight_role, user, farm)
        _guard_role_scope(preflight_membership.role, perms, user, farm)
        _guard_role_scope(preflight_role, perms, user, farm)
    # FOR NO KEY UPDATE, like the status endpoint: a role change only mutates
    # role_id, never the (farm_id, user_id) key that Task rows FK-reference,
    # so the weaker lock suffices and does not block the KEY SHARE lock a
    # concurrent task insert (auto-generation, duty spawning) acquires on
    # this worker's membership row.
    membership = await _get_membership(
        db,
        farm,
        membership_id,
        for_update=True,
        no_key_update=True,
    )
    # Same self-service guard as the toggle endpoint: holding team.manage
    # must not let a worker promote his own membership to a richer role.
    if membership.user_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot change your own role.")
    if user.id != farm.owner_id:
        await _pin_membership_role(db, farm, membership)
    _guard_peer_manager(membership, user, farm)
    try:
        role = await _get_role(db, farm, payload.role_id, for_share=True)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=400, detail="Pick a valid role.") from None
        raise
    _guard_manager_role(role, user, farm)
    _guard_role_scope(membership.role, perms, user, farm)
    _guard_role_scope(role, perms, user, farm)
    membership.role = role
    await db.commit()
    return _membership_out(
        membership,
        await _reset_password_policy_for_membership(db, membership),
        user,
        farm,
    )


@router.post("/workers/{membership_id}/toggle", include_in_schema=False)
async def retired_toggle_worker(
    membership_id: int,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: TEAM_PERM,
) -> None:
    """Refuse the retry-unsafe legacy command instead of inverting twice."""
    raise HTTPException(
        status_code=405,
        detail="Worker toggle was retired; send the desired state to the status endpoint.",
        headers={"Allow": "PUT"},
    )


@router.put("/workers/{membership_id}/status")
async def set_worker_status(
    payload: WorkerStatusIn,
    membership_id: int,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: TEAM_PERM,
) -> MembershipOut:
    if user.id != farm.owner_id:
        preflight_membership = await _get_membership(db, farm, membership_id)
        if preflight_membership.user_id == user.id:
            raise HTTPException(
                status_code=400,
                detail="You cannot deactivate your own membership.",
            )
        _guard_peer_manager(preflight_membership, user, farm)
        _guard_role_scope(preflight_membership.role, perms, user, farm)
    membership = await _get_membership(
        db,
        farm,
        membership_id,
        for_update=True,
        no_key_update=True,
    )
    if membership.user_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own membership.")
    if user.id != farm.owner_id:
        await _pin_membership_role(db, farm, membership)
    _guard_peer_manager(membership, user, farm)
    _guard_role_scope(membership.role, perms, user, farm)
    membership.is_active = payload.is_active
    # This is a farm-local authorization change, not an account security
    # event. Every personal Task already retains its role fallback, so no
    # history rewrite is needed; role peers gain operational fallback only
    # while this membership is inactive. Assigning the requested value makes
    # transport/application retries a no-op instead of a second inversion.
    await db.commit()
    return _membership_out(
        membership,
        await _reset_password_policy_for_membership(db, membership),
        user,
        farm,
    )


@router.post("/workers/{membership_id}/reset-password")
async def reset_password(
    prepared: PreparedPasswordResetDep,
    membership_id: int,
    db: DbSession,
) -> MembershipOut:
    """Owner-only reset of a global account this farm demonstrably provisioned."""
    user, farm = await _reauthorize_prepared_owner(
        db,
        actor_id=prepared.actor_id,
        actor_token_version=prepared.actor_token_version,
        farm_id=prepared.farm_id,
    )
    # FOR NO KEY UPDATE, like the status endpoint: a password reset rewrites
    # only the separately-locked User row, never the (farm_id, user_id) key
    # that Task rows FK-reference, so the weaker lock suffices and does not
    # block the KEY SHARE lock a concurrent task insert (auto-generation,
    # duty spawning) acquires on this worker's membership row.
    membership = await _get_membership(
        db,
        farm,
        membership_id,
        for_update=True,
        no_key_update=True,
    )
    _guard_peer_manager(membership, user, farm)
    # Serialize with farm creation and new foreign-key affiliations before the
    # eligibility query; otherwise an account could gain a global affiliation
    # between the check and the password rewrite.
    locked_user = (
        await db.execute(
            select(User)
            .where(User.id == membership.user_id, User.deleted_at.is_(None))
            # _get_membership selectinloads this exact User, so the row is
            # already in the identity map with its pre-lock column values.
            # Without populate_existing the ORM hands back that stale instance
            # and `token_version += 1` below increments a value the locked
            # SELECT was taken to re-read — a lost update that can re-write the
            # version a concurrent change-password just committed and leave the
            # revoked worker holding a still-valid access token.
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if locked_user is None:
        raise HTTPException(status_code=400, detail="Worker account no longer exists")
    reset_policy = await _reset_password_policy_for_membership(db, membership)
    if not reset_policy[0]:
        raise HTTPException(status_code=400, detail=reset_policy[1])
    locked_user.password_hash = prepared.password_hash
    locked_user.token_version += 1
    await revoke_user_sessions(db, membership.user_id)
    await db.commit()
    return _membership_out(membership, reset_policy, user, farm)


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
    await _guard_farm_capacity(
        db,
        farm,
        model=Role,
        limit=get_settings().max_roles_per_farm,
    )
    _guard_manager_permission(payload.permissions, user, farm)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required.")
    clash = (
        (
            await db.execute(
                select(Role).where(
                    Role.farm_id == farm.id,
                    Role.name == name,
                    Role.deleted_at.is_(None),
                )
            )
        )
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
    if user.id != farm.owner_id:
        preflight_role = await _get_role(db, farm, role_id)
        _guard_manager_role(preflight_role, user, farm)
        _guard_role_scope(preflight_role, perms, user, farm)
    role = await _get_role(db, farm, role_id, for_update=True)
    _guard_manager_role(role, user, farm)
    _guard_role_scope(role, perms, user, farm)
    _guard_manager_permission(payload.permissions, user, farm)
    name = payload.name.strip()
    clash = (
        (
            await db.execute(
                select(Role)
                .where(Role.farm_id == farm.id, Role.name == name, Role.id != role.id)
                .where(Role.deleted_at.is_(None))
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
    if user.id != farm.owner_id:
        preflight_role = await _get_role(db, farm, role_id)
        _guard_manager_role(preflight_role, user, farm)
        _guard_role_scope(preflight_role, perms, user, farm)
    # A full row lock also conflicts with FK KEY SHARE taken by a concurrently
    # spawned recurrence. The pending-duty guard below therefore sees either
    # the old occurrence or its committed successor; it cannot tombstone a
    # role in the gap and strand actionable work.
    role = await _get_role(db, farm, role_id, for_update=True)
    _guard_manager_role(role, user, farm)
    _guard_role_scope(role, perms, user, farm)
    if role.code is not None:
        # Startup seeding re-creates presets, so deleting one would not stick.
        raise HTTPException(status_code=400, detail="Preset roles can't be deleted.")
    if await _member_count(db, role.id) > 0:
        raise HTTPException(
            status_code=400, detail="Role still has workers assigned — reassign them first."
        )
    actionable_task = (
        await db.execute(
            select(Task.id)
            .where(
                Task.farm_id == farm.id,
                Task.assigned_role_id == role.id,
                Task.requires_action_clause(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if actionable_task is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Role still has actionable duties — complete, verify, reject, or skip them first."
            ),
        )
    role.deleted_at = utcnow()
    await db.commit()
    return Response(status_code=204)
