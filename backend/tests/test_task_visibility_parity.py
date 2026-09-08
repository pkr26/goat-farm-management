"""Parity between the twin duty-visibility engines.

The duty-visibility contract exists twice by necessity — once as the SQL
predicate ``services.tasks.task_scope`` (set queries for lists) and once as
the row-at-a-time evaluator ``app.api._shared.visible_to`` (object-level
authorization, with optional FOR UPDATE pins). Merging them would be a
behavioral rewrite, so this module is the single source of truth's
enforcement backstop: it enumerates every DB-reachable combination of

- viewer (owner / worker-with-role / worker-with-inactive-membership /
  non-member),
- assignment shape (role-only / personal / role+named worker / unassigned /
  other-role / tombstoned-role),
- assignee availability (active / deactivated membership / deleted account),
- task status (PENDING / DONE / SKIPPED / VERIFIED),

builds the rows in a real database, and asserts BOTH engines agree on every
combination — and agree with the documented expectation.

Two arms of the Python fallback cannot be produced through the current
schema and are therefore documented here instead of exercised:
``assigned_user_id`` with a NULL role on a PENDING row is rejected by
ck_tasks_user_assignment_has_role (the retained-assignee-role arm only
guards pre-constraint legacy rows), and an assignee whose membership row is
gone entirely is pinned by fk_tasks_farm_assigned_membership. Both arms are
mirrored verbatim in the twin implementations.
"""

import itertools
from datetime import date

import httpx
import pytest
from sqlalchemy import select, update

from app.api._shared import visible_to
from app.db import get_sessionmaker
from app.models import Farm, FarmMembership, Role, Task, User
from app.services.tasks import task_scope
from app.utils import today, utcnow

from .conftest import owner_with_farm

_email_counter = itertools.count()
_PW_HASH_PLACEHOLDER = "parity-not-a-login-hash"

STATUSES = ("PENDING", "DONE", "SKIPPED", "VERIFIED")
ASSIGNEE_STATES = ("active", "membership_deactivated", "user_deleted")


def _unique_email(prefix: str) -> str:
    return f"{prefix}-{next(_email_counter)}@parity.farm"


def _task_extras(status: str) -> dict[str, object]:
    """Column values the per-status CHECK constraints require."""
    if status == "DONE":
        return {"completed_at": utcnow()}
    if status == "SKIPPED":
        return {"skipped_at": utcnow()}
    if status == "VERIFIED":
        return {"completed_at": utcnow(), "verified_at": utcnow()}
    return {}


async def _make_user(db: object, *, deleted: bool = False) -> User:
    user = User(
        email=(
            f"deleted-{next(_email_counter)}@deleted.invalid"
            if deleted
            else _unique_email("worker")
        ),
        name=None if deleted else "Parity Worker",
        password_hash=_PW_HASH_PLACEHOLDER,
        deleted_at=utcnow() if deleted else None,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_role(db: object, farm_id: int, name: str) -> Role:
    role = Role(farm_id=farm_id, name=name, permissions="[]")
    db.add(role)
    await db.flush()
    return role


async def _make_membership(
    db: object, farm_id: int, user_id: int, role_id: int, *, active: bool = True
) -> FarmMembership:
    membership = FarmMembership(
        farm_id=farm_id,
        user_id=user_id,
        role_id=role_id,
        is_active=active,
    )
    db.add(membership)
    await db.flush()
    return membership


async def _make_task(
    db: object,
    farm_id: int,
    *,
    assigned_role_id: int | None,
    assigned_user_id: int | None,
    status: str,
) -> Task:
    task = Task(
        farm_id=farm_id,
        title="Parity probe",
        due_date=today(),
        status=status,
        category="OTHER",
        auto_generated=False,
        assigned_role_id=assigned_role_id,
        assigned_user_id=assigned_user_id,
        **_task_extras(status),  # type: ignore[arg-type]
    )
    db.add(task)
    await db.flush()
    return task


async def _viewer_membership(db: object, farm_id: int, user_id: int) -> FarmMembership | None:
    """Resolve the viewer's membership exactly as task_scope does (active
    only) so both engines judge the same input."""
    return (
        (
            await db.execute(
                select(FarmMembership).where(
                    FarmMembership.farm_id == farm_id,
                    FarmMembership.user_id == user_id,
                    FarmMembership.is_active.is_(True),
                )
            )
        )
        .scalars()
        .first()
    )


async def _assert_engines_agree(
    db: object,
    *,
    task: Task,
    farm: Farm,
    viewer: User,
    expected: bool,
    case: str,
) -> None:
    membership = await _viewer_membership(db, farm.id, viewer.id)
    python_visible = await visible_to(db, task, viewer, farm, membership)
    scoped_ids = (
        (await db.execute((await task_scope(db, farm, viewer)).where(Task.id == task.id)))
        .scalars()
        .all()
    )
    sql_visible = any(row.id == task.id for row in scoped_ids)
    assert python_visible == expected, f"{case}: visible_to said {python_visible}"
    assert sql_visible == expected, f"{case}: task_scope said {sql_visible}"


@pytest.mark.parametrize("status", STATUSES)
async def test_owner_sees_every_shape(client: httpx.AsyncClient, status: str) -> None:
    owner_headers = await owner_with_farm(client, "parity-owner@farm.in")
    farm_id = int(owner_headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = (await db.execute(select(Farm).where(Farm.id == farm_id))).scalar_one()
        owner = (await db.execute(select(User).where(User.id == farm.owner_id))).scalar_one()
        role = await _make_role(db, farm_id, "Owner Probe Role")
        worker = await _make_user(db)
        await _make_membership(db, farm_id, worker.id, role.id)
        shapes: list[tuple[str, int | None, int | None]] = [
            ("role-only", role.id, None),
            ("personal", role.id, worker.id),
            ("unassigned", None, None),
        ]
        for name, role_id, user_id in shapes:
            task = await _make_task(
                db,
                farm_id,
                assigned_role_id=role_id,
                assigned_user_id=user_id,
                status=status,
            )
            await _assert_engines_agree(
                db,
                task=task,
                farm=farm,
                viewer=owner,
                expected=True,
                case=f"owner/{name}/{status}",
            )
        await db.commit()


@pytest.mark.parametrize("status", STATUSES)
async def test_role_duty_visibility_ignores_role_tombstone(
    client: httpx.AsyncClient, status: str
) -> None:
    """A tombstoned role keeps its historical duties: both engines judge by
    role_id equality alone and never consult Role.deleted_at."""
    owner_headers = await owner_with_farm(client, "parity-tombstone@farm.in")
    farm_id = int(owner_headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = (await db.execute(select(Farm).where(Farm.id == farm_id))).scalar_one()
        role = await _make_role(db, farm_id, "Dutied Role")
        viewer = await _make_user(db)
        await _make_membership(db, farm_id, viewer.id, role.id)
        task = await _make_task(
            db, farm_id, assigned_role_id=role.id, assigned_user_id=None, status=status
        )
        await db.execute(update(Role).where(Role.id == role.id).values(deleted_at=utcnow()))
        await _assert_engines_agree(
            db,
            task=task,
            farm=farm,
            viewer=viewer,
            expected=True,
            case=f"tombstoned-role-duty/{status}",
        )
        await db.commit()


async def test_worker_role_and_assignment_matrix(client: httpx.AsyncClient) -> None:
    """Every DB-reachable worker-visible combination, both engines, once."""
    owner_headers = await owner_with_farm(client, "parity-matrix@farm.in")
    farm_id = int(owner_headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = (await db.execute(select(Farm).where(Farm.id == farm_id))).scalar_one()
        viewer_role = await _make_role(db, farm_id, "Viewer Role")
        other_role = await _make_role(db, farm_id, "Other Role")
        viewer = await _make_user(db)
        await _make_membership(db, farm_id, viewer.id, viewer_role.id)

        async def assignee_for(state: str) -> int:
            assignee = await _make_user(db, deleted=state == "user_deleted")
            await _make_membership(
                db,
                farm_id,
                assignee.id,
                other_role.id,
                active=state != "membership_deactivated",
            )
            return assignee.id

        for status in STATUSES:
            pending = status == "PENDING"
            # Role-only duty for the viewer's role: visible at every status.
            task = await _make_task(
                db,
                farm_id,
                assigned_role_id=viewer_role.id,
                assigned_user_id=None,
                status=status,
            )
            await _assert_engines_agree(
                db,
                task=task,
                farm=farm,
                viewer=viewer,
                expected=True,
                case=f"role-duty/{status}",
            )
            # Role-only duty for a different role: never visible.
            task = await _make_task(
                db,
                farm_id,
                assigned_role_id=other_role.id,
                assigned_user_id=None,
                status=status,
            )
            await _assert_engines_agree(
                db,
                task=task,
                farm=farm,
                viewer=viewer,
                expected=False,
                case=f"other-role-duty/{status}",
            )
            # Unassigned duty: owner-visible only.
            task = await _make_task(
                db, farm_id, assigned_role_id=None, assigned_user_id=None, status=status
            )
            await _assert_engines_agree(
                db,
                task=task,
                farm=farm,
                viewer=viewer,
                expected=False,
                case=f"unassigned/{status}",
            )
            # Personal duty (viewer is the assignee): always visible. A NULL
            # stored role is legal only outside PENDING.
            task = await _make_task(
                db,
                farm_id,
                assigned_role_id=viewer_role.id if pending else None,
                assigned_user_id=viewer.id,
                status=status,
            )
            await _assert_engines_agree(
                db,
                task=task,
                farm=farm,
                viewer=viewer,
                expected=True,
                case=f"personal/{status}",
            )
            for state in ASSIGNEE_STATES:
                assignee_id = await assignee_for(state)
                unavailable = state != "active"
                # Viewer's role stored beside a named worker: the fallback
                # window opens only while PENDING and the assignee cannot
                # serve the duty (inactive membership or deleted account).
                task = await _make_task(
                    db,
                    farm_id,
                    assigned_role_id=viewer_role.id,
                    assigned_user_id=assignee_id,
                    status=status,
                )
                await _assert_engines_agree(
                    db,
                    task=task,
                    farm=farm,
                    viewer=viewer,
                    expected=pending and unavailable,
                    case=f"user+own-role/{state}/{status}",
                )
                # A different role stored beside the named worker: the
                # fallback never matches the viewer's role.
                task = await _make_task(
                    db,
                    farm_id,
                    assigned_role_id=other_role.id,
                    assigned_user_id=assignee_id,
                    status=status,
                )
                await _assert_engines_agree(
                    db,
                    task=task,
                    farm=farm,
                    viewer=viewer,
                    expected=False,
                    case=f"user+other-role/{state}/{status}",
                )
                # Legacy-shaped user assignment without a stored role exists
                # only outside PENDING (ck_tasks_user_assignment_has_role);
                # the fallback requires PENDING, so it stays invisible.
                if not pending:
                    task = await _make_task(
                        db,
                        farm_id,
                        assigned_role_id=None,
                        assigned_user_id=assignee_id,
                        status=status,
                    )
                    await _assert_engines_agree(
                        db,
                        task=task,
                        farm=farm,
                        viewer=viewer,
                        expected=False,
                        case=f"user+no-role/{state}/{status}",
                    )
        await db.commit()


async def test_deactivated_viewer_membership_reduces_to_personal(
    client: httpx.AsyncClient,
) -> None:
    """An inactive membership resolves to no role context in BOTH engines:
    only duties assigned to the worker personally remain visible."""
    owner_headers = await owner_with_farm(client, "parity-inactive@farm.in")
    farm_id = int(owner_headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = (await db.execute(select(Farm).where(Farm.id == farm_id))).scalar_one()
        role = await _make_role(db, farm_id, "Suspended Role")
        worker = await _make_user(db)
        membership = await _make_membership(db, farm_id, worker.id, role.id, active=False)
        assert membership.is_active is False
        role_duty = await _make_task(
            db, farm_id, assigned_role_id=role.id, assigned_user_id=None, status="PENDING"
        )
        personal_duty = await _make_task(
            db, farm_id, assigned_role_id=role.id, assigned_user_id=worker.id, status="PENDING"
        )
        await _assert_engines_agree(
            db,
            task=role_duty,
            farm=farm,
            viewer=worker,
            expected=False,
            case="inactive-membership/role-duty",
        )
        await _assert_engines_agree(
            db,
            task=personal_duty,
            farm=farm,
            viewer=worker,
            expected=True,
            case="inactive-membership/personal-duty",
        )
        await db.commit()


async def test_non_member_sees_nothing(client: httpx.AsyncClient) -> None:
    """A user with no membership on the farm never matches any branch."""
    owner_headers = await owner_with_farm(client, "parity-outsider@farm.in")
    farm_id = int(owner_headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = (await db.execute(select(Farm).where(Farm.id == farm_id))).scalar_one()
        role = await _make_role(db, farm_id, "Outsider Probe Role")
        member = await _make_user(db)
        await _make_membership(db, farm_id, member.id, role.id)
        outsider = await _make_user(db)
        for role_id, user_id in ((role.id, None), (None, None), (role.id, member.id)):
            task = await _make_task(
                db,
                farm_id,
                assigned_role_id=role_id,
                assigned_user_id=user_id,
                status="PENDING",
            )
            await _assert_engines_agree(
                db,
                task=task,
                farm=farm,
                viewer=outsider,
                expected=False,
                case=f"non-member/role={role_id}/user={user_id}",
            )
        await db.commit()


async def test_fallback_window_is_not_due_date_dependent(client: httpx.AsyncClient) -> None:
    """The fallback window is availability-driven, not due-date-driven: an
    overdue PENDING duty behaves exactly like a future one in both engines."""
    owner_headers = await owner_with_farm(client, "parity-dates@farm.in")
    farm_id = int(owner_headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = (await db.execute(select(Farm).where(Farm.id == farm_id))).scalar_one()
        role = await _make_role(db, farm_id, "Dated Role")
        viewer = await _make_user(db)
        await _make_membership(db, farm_id, viewer.id, role.id)
        assignee = await _make_user(db)
        await _make_membership(db, farm_id, assignee.id, role.id, active=False)
        overdue = Task(
            farm_id=farm_id,
            title="Overdue parity probe",
            due_date=date(2020, 1, 1),
            status="PENDING",
            category="OTHER",
            auto_generated=False,
            assigned_role_id=role.id,
            assigned_user_id=assignee.id,
        )
        db.add(overdue)
        await db.flush()
        await _assert_engines_agree(
            db,
            task=overdue,
            farm=farm,
            viewer=viewer,
            expected=True,
            case="overdue-fallback",
        )
        await db.commit()
