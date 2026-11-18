"""Observable security, bounded affiliation and admission behavior."""

import asyncio
import hashlib
from datetime import timedelta
from typing import Literal

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select, text, update

from app.api import team
from app.api._run_limits import _RunCostWindow
from app.core.config import get_settings
from app.db import get_sessionmaker
from app.deps import (
    _lock_membership_role,
    _lock_membership_row,
    _pin_authenticated_user,
    accessible_farms,
    deactivate_deleted_user_memberships,
    purge_expired_refresh_sessions,
    revoke_session_family,
)
from app.models import (
    Farm,
    FarmMembership,
    NotificationRecipient,
    RefreshSession,
    Role,
    TotpRecoveryCode,
    User,
)
from app.ratelimit import MemoryLimiterBackend, SlidingWindowRateLimiter
from app.security import decode_access_claims, encrypt_totp_secret, generate_totp_secret_b32
from app.utils import utcnow

from .conftest import login_and_rotate, owner_with_farm
from .test_animals_extended import custom_worker_headers, get_profile, make_animal, mark_status
from .test_config_path_and_identity_boundary_contracts import scoped_request
from .test_health_extended import record_event
from .test_session_privacy import _password_worker
from .test_team_extended import add_worker, create_custom_role, create_duty
from .test_totp import _enroll_and_activate, stable_totp_key  # noqa: F401 - imported fixture
from .test_worker_pin_auth import _make_pin_worker, _worker_login


async def test_duplicate_worker_preparations_keep_one_gate_until_the_last_queued_user_exits() -> (
    None
):
    farm_id, actor_id, key = 791, 821, "survivor-overlapping-preparation"
    identity = (farm_id, actor_id, hashlib.sha256(key.encode()).hexdigest())
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    release_first = asyncio.Event()
    release_second = asyncio.Event()
    active = 0
    peak = 0

    async def prepare(entered: asyncio.Event, release: asyncio.Event) -> None:
        nonlocal active, peak
        async with team._serialize_worker_idempotency(farm_id=farm_id, actor_id=actor_id, key=key):
            active += 1
            peak = max(peak, active)
            entered.set()
            try:
                await release.wait()
            finally:
                active -= 1

    first = asyncio.create_task(prepare(first_entered, release_first))
    second = third = None
    try:
        await asyncio.wait_for(first_entered.wait(), timeout=5)
        second = asyncio.create_task(prepare(second_entered, release_second))
        await asyncio.sleep(0)
        assert not second_entered.is_set()
        release_first.set()
        await first
        await asyncio.wait_for(second_entered.wait(), timeout=5)
        third_entered = asyncio.Event()
        third = asyncio.create_task(prepare(third_entered, release_second))
        await asyncio.sleep(0)
        assert not third_entered.is_set(), "A queued user's gate must not be retired underneath it"
        release_second.set()
        await asyncio.gather(second, third)
        assert peak == 1 and active == 0
        assert identity not in team._worker_idempotency_gates, "Completed work must release memory"
    finally:
        release_first.set()
        release_second.set()
        tasks = [task for task in (first, second, third) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Only this test's identity can remain after a counterfactual leak.
        team._worker_idempotency_gates.pop(identity, None)


async def test_worker_preparation_gate_memory_stays_bounded_during_distinct_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(team, "MAX_WORKER_IDEMPOTENCY_GATES", 2)
    first, second, third = (f"bounded-survivor-{index}" for index in range(3))
    identities = {
        (792, 822, hashlib.sha256(key.encode()).hexdigest()) for key in (first, second, third)
    }
    try:
        async with (
            team._serialize_worker_idempotency(farm_id=792, actor_id=822, key=first),
            team._serialize_worker_idempotency(farm_id=792, actor_id=822, key=second),
            team._serialize_worker_idempotency(farm_id=792, actor_id=822, key=third),
        ):
            assert len(identities & team._worker_idempotency_gates.keys()) == 2
        assert not identities & team._worker_idempotency_gates.keys()
    finally:
        for identity in identities:
            team._worker_idempotency_gates.pop(identity, None)


@pytest.mark.parametrize("maintenance", ["probe", "sweep"])
def test_auth_failure_history_expires_at_the_exact_window_boundary(maintenance: str) -> None:
    now = [1000.0]
    limiter = SlidingWindowRateLimiter(
        lambda: now[0], sweep_interval_seconds=60 if maintenance == "sweep" else 1000
    )
    limiter.record("login", "victim", 60, max_attempts=1)
    now[0] = 1059.999
    assert limiter.is_blocked("login", "victim", 1, 60)
    now[0] = 1060.0
    if maintenance == "sweep":
        assert not limiter.is_blocked("login", "unrelated", 1, 60)
        assert not limiter.has_attempts("login", "victim")
    assert not limiter.is_blocked("login", "victim", 1, 60)
    limiter.record("login", "victim", 60, max_attempts=1)
    assert limiter.is_blocked("login", "victim", 1, 60)


@pytest.mark.parametrize("setting", ["max_keys", "sweep_interval_seconds", "max_attempts"])
def test_nonpositive_limiter_admission_settings_are_rejected_before_recording(setting: str) -> None:
    backend = MemoryLimiterBackend(max_keys=2)
    error = None
    try:
        if setting == "max_attempts":
            limiter = SlidingWindowRateLimiter(backend=backend)
            limiter.record("login", "victim", 60, max_attempts=0)
        elif setting == "max_keys":
            SlidingWindowRateLimiter(backend=backend, max_keys=0)
        else:
            SlidingWindowRateLimiter(backend=backend, sweep_interval_seconds=0)
    except ValueError as exc:
        error = exc
    assert isinstance(error, ValueError), "An invalid admission setting must fail before use"
    assert not backend.is_known("login", "victim")
    valid = SlidingWindowRateLimiter(backend=backend, max_keys=2, sweep_interval_seconds=1)
    valid.record("login", "victim", 60, max_attempts=1)
    assert valid.is_blocked("login", "victim", 1, 60)


def test_new_cold_simulation_principals_evict_the_older_cold_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _RunCostWindow(window_seconds=60, budget=100, max_keys=2, clock=lambda: 1000.0)
    actual_eviction = window._evict_one

    def checked_eviction(*, exclude: set[tuple[str, int]]) -> bool:
        before = {key: tuple(history) for key, history in window._spend.items()}
        retained = before.keys() & exclude
        freed = actual_eviction(exclude=exclude)
        if freed:
            after = window._spend
            assert len(after) == len(before) - 1, (
                "Successful eviction must free a slot before admission retries"
            )
            assert retained <= after.keys(), "An excluded principal must keep its live history"
            assert all(tuple(after[key]) == before[key] for key in retained), (
                "Evicting another principal cannot rewrite the retained request's spend"
            )
        return freed

    monkeypatch.setattr(window, "_evict_one", checked_eviction)
    booking_error: RuntimeError | IndexError | KeyError | None = None
    cold_state = None
    retained_state = None
    try:
        window.charge("user", 1, 1)
        window.charge("user", 2, 1)
        window.charge("user", 3, 1)
        cold_state = tuple(window.would_exceed_budget("user", key, 100) for key in (1, 2, 3))
        # The second booking must evict user3 while retaining user2's existing
        # charge, even though user2 is the oldest cold candidate at this point.
        window.charge_many((("user", 2, 1), ("user", 4, 1)))
        retained_state = (
            window.would_exceed_budget("user", 2, 98),
            window.would_exceed_budget("user", 2, 99),
            window.would_exceed_budget("user", 4, 100),
            window.would_exceed_budget("user", 3, 100),
        )
    except (RuntimeError, IndexError, KeyError) as exc:
        booking_error = exc
    assert booking_error is None, (
        f"Valid cold-history bookings must admit the new principal: {booking_error!r}"
    )
    assert cold_state == (False, True, True), "Only the older cold principal should be evicted"
    assert retained_state == (False, True, True, False), (
        "A later booking must retain and add to the excluded principal's history"
    )


def test_less_than_half_budget_does_not_displace_an_older_repeated_principal() -> None:
    window = _RunCostWindow(window_seconds=60, budget=100, max_keys=2)
    window.charge("user", 1, 1)
    window.charge("user", 1, 1)
    window.charge("user", 2, 34)
    window.charge("user", 3, 1)
    assert window.would_exceed_budget("user", 1, 99)
    assert not window.would_exceed_budget("user", 2, 100)
    assert window.would_exceed_budget("user", 3, 100)


@pytest.mark.parametrize("access", ["probe", "blocked-record"])
def test_a_tightened_threshold_makes_the_just_accessed_bucket_the_newest_protected(
    access: str,
) -> None:
    limiter = SlidingWindowRateLimiter(max_keys=2)
    limiter.record("login", "older-block", 60, max_attempts=1)
    limiter.record("login", "newer-history", 60, max_attempts=3)
    if access == "probe":
        assert limiter.is_blocked("login", "newer-history", 1, 60)
    else:
        limiter.record("login", "newer-history", 60, max_attempts=1)
    limiter.record("login", "third-principal", 60, max_attempts=1)
    assert limiter.is_blocked("login", "newer-history", 1, 60)
    assert not limiter.has_attempts("login", "older-block")


def test_backend_history_probe_refreshes_live_eviction_recency_without_adding_a_hit() -> None:
    backend = MemoryLimiterBackend(lambda: 1000.0, max_keys=2)
    backend.append_hit("login", "first", 60, 1000.0, 5)
    backend.append_hit("login", "second", 60, 1000.0, 5)
    assert backend.pruned_hit_count("login", "first", 60) == 1
    backend.append_hit("login", "third", 60, 1000.0, 5)
    assert backend.is_known("login", "first")
    assert not backend.is_known("login", "second")
    assert backend.pruned_hit_count("login", "first", 60) == 1


async def test_saved_notification_preferences_update_the_same_recipient(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="preference-reuse-owner@farm.in")
    role = await create_custom_role(client, owner, "Recipient role", ["tasks.view"])
    worker = await add_worker(client, owner, role["id"], "preference-reuse-worker@farm.in")
    assert worker.status_code == 201, worker.text
    endpoint = f"/api/team/workers/{worker.json()['id']}/notifications"
    first = await client.put(
        endpoint, headers=owner, json={"phone": "+919123456789", "daily_digest": True}
    )
    assert first.status_code == 200, first.text
    error = None
    second = None
    try:
        second = await client.put(
            endpoint,
            headers=owner,
            json={"phone": "+919987654321", "daily_digest": False, "verified": True},
        )
    except Exception as exc:
        error = exc
    assert error is None, "Saving preferences again must update the existing destination"
    assert second is not None and second.status_code == 200
    saved = await client.get(endpoint, headers=owner)
    assert saved.status_code == 200 and saved.json() == second.json()
    assert saved.json()["phone"] == "+919987654321"
    assert saved.json()["daily_digest"] is False and saved.json()["verified"] is True


async def test_a_role_with_multiple_actionable_duties_remains_available_until_all_are_closed(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="multiple-duty-owner@farm.in")
    role = await create_custom_role(client, owner, "Seasonal duties", ["tasks.view"])
    duties = [
        await create_duty(client, owner, f"Close seasonal pen {index}", role["id"])
        for index in range(2)
    ]
    endpoint = f"/api/team/roles/{role['id']}"
    error = None
    denied = None
    try:
        denied = await client.delete(endpoint, headers=owner)
    except Exception as exc:
        error = exc
    assert error is None, "Multiple pending duties must produce an ordinary lifecycle conflict"
    assert denied is not None and denied.status_code == 409
    for duty in duties:
        skipped = await client.post(
            f"/api/tasks/{duty['id']}/skip", headers=owner, json={"reason": "Season ended"}
        )
        assert skipped.status_code == 200, skipped.text
    assert (await client.delete(endpoint, headers=owner)).status_code == 204


@pytest.mark.parametrize("refresh", ["membership", "role"])
async def test_authorization_locks_reload_a_previously_read_identity_map(
    client: httpx.AsyncClient, refresh: str
) -> None:
    owner = await owner_with_farm(client, email="reload-owner@farm.in")
    old_role = await create_custom_role(client, owner, "Previous operator", ["tasks.view"])
    new_role = await create_custom_role(client, owner, "Reassigned operator", ["animals.view"])
    response = await add_worker(client, owner, old_role["id"], "reload-worker@farm.in")
    assert response.status_code == 201, response.text
    async with get_sessionmaker()() as reader:
        membership = await reader.get(FarmMembership, response.json()["id"])
        assert membership is not None
        role = await reader.get(Role, old_role["id"])
        assert role is not None
        # A separate committed administrator transaction changes valid state
        # after this request's earlier read, before its authorization locks.
        async with get_sessionmaker()() as writer:
            if refresh == "membership":
                await writer.execute(
                    update(FarmMembership)
                    .where(FarmMembership.id == membership.id)
                    .values(role_id=new_role["id"])
                )
            else:
                await writer.execute(
                    update(Role).where(Role.id == role.id).values(permissions='["animals.view"]')
                )
            await writer.commit()
        if refresh == "membership":
            locked = await _lock_membership_row(reader, membership.user_id, membership.farm_id)
            assert locked is not None and locked.role_id == new_role["id"]
        else:
            locked_role = await _lock_membership_role(reader, membership, membership.farm_id)
            assert locked_role is not None and locked_role.permission_set() == {"animals.view"}


async def test_restriction_alert_retains_the_recorded_disease(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import notifications

    alerts: list[str] = []

    async def deliver(farm_id: int, kind: str, message: str, dedupe_key: str) -> None:
        assert kind == "MOVEMENT_RESTRICTION"
        alerts.append(message)

    monkeypatch.setattr(notifications, "emit_alert", deliver)
    owner = await owner_with_farm(client, email="specific-disease-owner@farm.in")
    animal = await make_animal(client, owner, tag="DISEASE-ALERT")
    await record_event(
        client,
        owner,
        animal_id=animal["id"],
        type="TREATMENT",
        disease_target="PPR",
        suspected_scheduled_disease=True,
    )
    assert len(alerts) == 1 and "suspected PPR recorded" in alerts[0]


async def test_animal_reader_without_health_permission_cannot_learn_necropsy_state(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="necropsy-survivor-owner@farm.in")
    animal = await make_animal(client, owner, tag="NECROPSY-PRIVATE")
    death = await mark_status(
        client,
        owner,
        animal["id"],
        "DEAD",
        mortality_cause="Clinical history",
        necropsy_done=True,
        necropsy_findings="Confidential pathology",
    )
    assert death.status_code == 200, death.text
    reader = await custom_worker_headers(
        client,
        owner,
        name="Animal identity reader",
        email="necropsy-survivor-reader@farm.in",
        permissions=["animals.view"],
    )
    owner_profile = await get_profile(client, owner, animal["id"])
    profile = await get_profile(client, reader, animal["id"])
    assert owner_profile["animal"]["necropsy_done"] is True
    assert profile["animal"]["necropsy_done"] is False
    assert profile["animal"]["necropsy_findings"] is None
    assert profile["animal"]["mortality_cause"] is None


@pytest.mark.parametrize(
    ("owned_count", "membership_count", "expected"),
    [(2, 0, "complete"), (0, 2, "complete"), (1, 1, "complete"), (1, 2, "quota")],
    ids=["owned-at-cap", "memberships-at-cap", "mixed-at-cap", "mixed-overflow"],
)
async def test_supported_retained_affiliations_are_complete_at_cap_and_rejected_above_it(
    monkeypatch: pytest.MonkeyPatch,
    owned_count: int,
    membership_count: int,
    expected: Literal["complete", "quota"],
) -> None:
    # The service expressly supports retained/imported affiliation graphs;
    # every row here obeys the current ownership and membership foreign keys.
    monkeypatch.setattr(get_settings(), "max_account_affiliations_per_response", 2)
    async with get_sessionmaker()() as db:
        user = User(email="retained-affiliation@farm.in", password_hash="not-a-login-test")
        other = User(email="retained-other-owner@farm.in", password_hash="not-a-login-test")
        db.add_all([user, other])
        await db.flush()
        farms = [Farm(name=f"Owned {index}", owner_id=user.id) for index in range(owned_count)]
        db.add_all(farms)
        await db.flush()
        expected_ids = {farm.id for farm in farms}
        for index in range(membership_count):
            farm = Farm(name=f"Retained member {index}", owner_id=other.id)
            db.add(farm)
            await db.flush()
            role = Role(farm_id=farm.id, name="Retained role", permissions="[]")
            db.add(role)
            await db.flush()
            db.add(
                FarmMembership(farm_id=farm.id, user_id=user.id, role_id=role.id, is_active=True)
            )
            expected_ids.add(farm.id)
        await db.commit()
        error = None
        pairs = None
        try:
            pairs = await accessible_farms(db, user)
        except HTTPException as exc:
            error = exc
        if expected == "quota":
            assert isinstance(error, HTTPException) and error.status_code == 409
            assert pairs is None
        else:
            assert error is None, "An account exactly at its supported response cap remains usable"
            assert pairs is not None and {farm.id for farm, _role in pairs} == expected_ids


@pytest.mark.usefixtures("stable_totp_key")
@pytest.mark.parametrize("cohort", ["refresh", "user", "recovery", "membership", "recipient"])
async def test_privacy_maintenance_skips_owned_row_locks_and_cleans_other_available_work(
    cohort: str,
) -> None:
    # Retained account tombstones are an explicitly supported cleanup input.
    # All foreign keys and CHECKs stay enabled; each class has one locked and
    # one available row so a stuck oldest row cannot starve the whole queue.
    async with get_sessionmaker()() as seed:
        users = [
            User(
                email=f"deleted-locked-cleanup-{cohort}-{index}@deleted.invalid",
                password_hash="retained-unusable-hash",
                deleted_at=None if cohort == "refresh" else utcnow(),
                totp_state="PENDING" if cohort == "user" else None,
                totp_secret_enc=(
                    encrypt_totp_secret(generate_totp_secret_b32()) if cohort == "user" else None
                ),
            )
            for index in range(2)
        ]
        seed.add_all(users)
        await seed.flush()
        ids: list[int] = []
        if cohort == "user":
            ids = [user.id for user in users]
        for index, user in enumerate(users):
            if cohort == "refresh":
                session = RefreshSession(
                    user_id=user.id,
                    jti=f"held-cleanup-refresh-{index}",
                    family_id=f"held-cleanup-family-{index}",
                    expires_at=utcnow() - timedelta(days=60),
                )
                seed.add(session)
                await seed.flush()
                ids.append(session.id)
            elif cohort == "recovery":
                code = TotpRecoveryCode(user_id=user.id, code_hash="retained-unusable-code-hash")
                seed.add(code)
                await seed.flush()
                ids.append(code.id)
            elif cohort in {"membership", "recipient"}:
                # An independent live owner retains the deleted worker's
                # historical membership; cleanup never removes that anchor.
                owner = User(email=f"live-cleanup-{index}@farm.in", password_hash="not-a-login")
                seed.add(owner)
                await seed.flush()
                farm = Farm(name="Retained work", owner_id=owner.id)
                seed.add(farm)
                await seed.flush()
                role = Role(farm_id=farm.id, name="Retained operator", permissions="[]")
                seed.add(role)
                await seed.flush()
                member = FarmMembership(
                    farm_id=farm.id,
                    user_id=user.id,
                    role_id=role.id,
                    is_active=cohort == "membership",
                )
                seed.add(member)
                await seed.flush()
                if cohort == "membership":
                    ids.append(member.id)
                else:
                    recipient = NotificationRecipient(
                        farm_id=farm.id,
                        membership_id=member.id,
                        phone=f"+91912345678{index}",
                        daily_digest=True,
                    )
                    seed.add(recipient)
                    await seed.flush()
                    ids.append(recipient.id)
        await seed.commit()
    table, unfinished = {
        "refresh": ("refresh_sessions", "TRUE"),
        "user": ("users", "totp_state IS NOT NULL"),
        "recovery": ("totp_recovery_codes", "TRUE"),
        "membership": ("farm_memberships", "is_active"),
        "recipient": ("notification_recipients", "phone <> 'deleted'"),
    }[cohort]

    async def cleanup() -> None:
        async with get_sessionmaker()() as worker:
            # This bounds a wrong lock acquisition, not application throughput.
            # A genuine DB lock error is captured below as an ordinary assertion.
            await worker.execute(text("SET LOCAL lock_timeout = '1s'"))
            if cohort == "refresh":
                await purge_expired_refresh_sessions(worker, batch_size=1)
            else:
                await deactivate_deleted_user_memberships(worker, batch_size=1)
            await worker.commit()

    async with get_sessionmaker()() as holder:
        await holder.execute(
            text(f"SELECT id FROM {table} WHERE id = :id FOR UPDATE"), {"id": ids[0]}
        )
        error = None
        try:
            await cleanup()
        except Exception as exc:
            error = exc
        assert error is None, (
            "A held oldest row must not prevent cleaning a different available row"
        )
        async with get_sessionmaker()() as observer:
            remaining = list(
                (
                    await observer.execute(
                        text(
                            f"SELECT id FROM {table} WHERE id IN (:held, :free) "
                            f"AND ({unfinished}) ORDER BY id"
                        ),
                        {"held": ids[0], "free": ids[1]},
                    )
                ).scalars()
            )
            assert remaining == [ids[0]], "Only the owned locked row should remain unfinished"
        await holder.rollback()
    await cleanup()
    async with get_sessionmaker()() as observer:
        assert (
            await observer.scalar(
                text(f"SELECT count(*) FROM {table} WHERE id IN (:held, :free) AND ({unfinished})"),
                {"held": ids[0], "free": ids[1]},
            )
            == 0
        )


@pytest.mark.parametrize("batch_size", [0, 10_000, 10_001], ids=["empty", "maximum", "over-cap"])
async def test_privacy_cleanup_rejects_nonprogress_and_unbounded_page_sizes(
    batch_size: int,
) -> None:
    async with get_sessionmaker()() as db:
        error = None
        removed = None
        try:
            removed = await deactivate_deleted_user_memberships(db, batch_size=batch_size)
        except ValueError as exc:
            error = exc
        if batch_size == 10_000:
            assert error is None and removed == 0, "The documented maximum must remain supported"
        else:
            assert isinstance(error, ValueError) and removed is None


async def test_role_member_counts_conserve_memberships_instead_of_other_accounts(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="accurate-roster-owner@farm.in")
    role = await create_custom_role(client, owner, "Two operators", ["tasks.view"])
    for index in range(2):
        added = await add_worker(client, owner, role["id"], f"accurate-roster-{index}@farm.in")
        assert added.status_code == 201, added.text
    roster = await client.get("/api/team", headers=owner)
    assert roster.status_code == 200, roster.text
    matching = [entry for entry in roster.json()["roles"] if entry["id"] == role["id"]]
    assert len(matching) == 1 and matching[0]["member_count"] == 2
    saved = await client.put(
        f"/api/team/roles/{role['id']}",
        headers=owner,
        json={
            "name": "Two current operators",
            "permissions": ["tasks.view"],
            "expected_revision": role["revision"],
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["member_count"] == 2


@pytest.mark.parametrize("refresh", ["membership", "role", "user-profile"])
async def test_roster_lock_helpers_refresh_state_written_since_their_earlier_read(
    client: httpx.AsyncClient,
    refresh: str,
) -> None:
    owner = await owner_with_farm(client, email="roster-reload-owner@farm.in")
    old_role = await create_custom_role(client, owner, "Before reassignment", ["tasks.view"])
    new_role = await create_custom_role(client, owner, "After reassignment", ["animals.view"])
    response = await add_worker(client, owner, old_role["id"], "roster-reload-worker@farm.in")
    assert response.status_code == 201, response.text
    async with get_sessionmaker()() as reader:
        farm = await reader.get(Farm, int(owner["X-Farm-Id"]))
        member = await reader.get(FarmMembership, response.json()["id"])
        role = await reader.get(Role, old_role["id"])
        user = await reader.get(User, response.json()["user_id"])
        assert farm is not None and member is not None and role is not None and user is not None
        async with get_sessionmaker()() as writer:
            if refresh == "membership":
                await writer.execute(
                    update(FarmMembership)
                    .where(FarmMembership.id == member.id)
                    .values(role_id=new_role["id"])
                )
            elif refresh == "role":
                await writer.execute(
                    update(Role).where(Role.id == role.id).values(permissions='["animals.view"]')
                )
            else:
                await writer.execute(
                    update(User).where(User.id == user.id).values(name="Updated operator name")
                )
            await writer.commit()
        if refresh == "membership":
            locked_member = await team._get_membership(reader, farm, member.id, for_update=True)
            assert locked_member.role_id == new_role["id"]
        elif refresh == "role":
            locked_role = await team._get_role(reader, farm, role.id, for_update=True)
            assert locked_role.permission_set() == {"animals.view"}
        else:
            pinned = await team._pin_membership_user(reader, member)
            assert pinned.name == "Updated operator name"


async def test_concurrent_role_edits_with_the_same_revision_have_only_one_winner(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="serialized-role-owner@farm.in")
    role = await create_custom_role(client, owner, "Original role", ["tasks.view"])
    endpoint = f"/api/team/roles/{role['id']}"
    holder = get_sessionmaker()()
    requests: list[asyncio.Task[httpx.Response]] = []
    try:
        await holder.execute(select(Role).where(Role.id == role["id"]).with_for_update())
        holder_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        for index in range(2):
            requests.append(
                asyncio.create_task(
                    client.put(
                        endpoint,
                        headers=owner,
                        json={
                            "name": f"Concurrent role {index}",
                            "permissions": ["tasks.view"],
                            "expected_revision": role["revision"],
                        },
                    )
                )
            )
        for _ in range(1000):
            async with get_sessionmaker()() as observer:
                waiters = await observer.scalar(
                    text(
                        "WITH RECURSIVE blocked(pid) AS ("
                        "SELECT pid FROM pg_stat_activity WHERE datname = current_database() "
                        "AND :holder = ANY(pg_blocking_pids(pid)) UNION "
                        "SELECT a.pid FROM pg_stat_activity a JOIN blocked b "
                        "ON b.pid = ANY(pg_blocking_pids(a.pid)) "
                        "WHERE a.datname = current_database()) SELECT count(*) FROM blocked"
                    ),
                    {"holder": holder_pid},
                )
            if isinstance(waiters, int) and waiters >= 2:
                break
            assert not any(request.done() for request in requests), (
                "Both edits must reach the held role"
            )
            await asyncio.sleep(0.01)
        else:
            pytest.fail("Both public edits must reach their owned role lock before it is released")
        await holder.rollback()
        results = await asyncio.wait_for(asyncio.gather(*requests), 10)
        assert sorted(result.status_code for result in results) == [200, 409], (
            "A stale role revision must not overwrite the concurrent winner"
        )
    finally:
        await holder.rollback()
        await holder.close()
        for request in requests:
            if not request.done():
                request.cancel()
        await asyncio.gather(*requests, return_exceptions=True)


@pytest.mark.usefixtures("stable_totp_key")
async def test_pinned_tablet_authorization_rechecks_a_factor_enabled_after_its_earlier_read(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="pinned-factor-owner@farm.in")
    worker = await _password_worker(client, owner, "pinned-factor-worker@farm.in")
    await login_and_rotate(client, "pinned-factor-worker@farm.in", "workerpass123")
    reset = await client.post(
        f"/api/team/workers/{worker['id']}/reset-pin", headers=owner, json={"pin": "4321"}
    )
    assert reset.status_code == 200, reset.text
    password_login = await client.post(
        "/api/auth/login",
        json={"email": "pinned-factor-worker@farm.in", "password": "workerpass123!r1"},
    )
    assert password_login.status_code == 200, password_login.text
    password = {"Authorization": f"Bearer {password_login.json()['access_token']}"}
    tablet = await _worker_login(client, int(owner["X-Farm-Id"]), worker["id"], "4321")
    assert tablet.status_code == 200, tablet.text
    token = tablet.json()["access_token"]
    claims = decode_access_claims(token)
    assert claims is not None
    request = scoped_request(token, "/api/tasks", method="POST")
    request.state.authenticated_token_version = claims.token_version
    request.state.authenticated_scope = claims.scope
    request.state.authenticated_family_id = claims.family_id
    async with get_sessionmaker()() as db:
        earlier_user = await db.get(User, claims.user_id)
        assert earlier_user is not None and earlier_user.totp_state is None
        await _enroll_and_activate(client, password, password="workerpass123!r1")
        error = None
        try:
            await _pin_authenticated_user(db, request, earlier_user)
        except HTTPException as exc:
            error = exc
        assert isinstance(error, HTTPException) and error.status_code == 401
        assert error.detail == "Password sign-in is required"


async def test_unscoped_family_revocation_revokes_real_issued_sessions_and_preserves_other_families(
    client: httpx.AsyncClient,
) -> None:
    first = await owner_with_farm(client, email="family-revoke-first@farm.in")
    second = await owner_with_farm(client, email="family-revoke-second@farm.in")
    claims = decode_access_claims(first["Authorization"].removeprefix("Bearer "))
    assert claims is not None and claims.family_id is not None
    async with get_sessionmaker()() as db:
        changed = await revoke_session_family(db, claims.family_id)
        await db.commit()
    assert changed > 0, "The documented whole-family operation must revoke its issued refresh rows"
    assert (await client.get("/api/auth/me", headers=first)).status_code == 401
    assert (await client.get("/api/auth/me", headers=second)).status_code == 200
    async with get_sessionmaker()() as db:
        assert await revoke_session_family(db, claims.family_id) == 0


async def test_a_role_name_on_another_farm_does_not_block_this_farms_rename(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="rename-local-owner@farm.in")
    other = await owner_with_farm(client, email="rename-foreign-owner@farm.in")
    local_role = await create_custom_role(client, owner, "Local original", ["tasks.view"])
    foreign = await create_custom_role(client, other, "Same desired name", ["animals.view"])
    saved = await client.put(
        f"/api/team/roles/{local_role['id']}",
        headers=owner,
        json={
            "name": foreign["name"],
            "permissions": ["tasks.view"],
            "expected_revision": local_role["revision"],
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["name"] == foreign["name"]
    assert saved.json()["revision"] > local_role["revision"]
    foreign_page = await client.get("/api/team", headers=other)
    assert foreign_page.status_code == 200
    preserved = [role for role in foreign_page.json()["roles"] if role["id"] == foreign["id"]]
    assert preserved == [foreign]


async def test_retained_membership_at_the_positive_postgres_identity_ceiling_is_addressable(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="last-membership-owner@farm.in")
    role = await create_custom_role(client, owner, "Retained reader", ["tasks.view"])
    async with get_sessionmaker()() as db:
        user = User(email="last-membership-worker@farm.in", password_hash="retained-unusable-hash")
        db.add(user)
        await db.flush()
        member = FarmMembership(
            id=2**31 - 1,
            farm_id=int(owner["X-Farm-Id"]),
            user_id=user.id,
            role_id=role["id"],
            is_active=True,
        )
        db.add(member)
        await db.commit()
        membership_id = member.id
    response = await client.get(f"/api/team/workers/{membership_id}/notifications", headers=owner)
    assert response.status_code == 200, response.text
    assert response.json() is None

    # A valid retained Role has the same inclusive int4 identity ceiling.
    # The public API does not let a caller choose generated Role ids; this
    # explicitly retained-state fixture avoids billions of create requests.
    retained_role_id = 2**31 - 1
    async with get_sessionmaker()() as db:
        retained_role = Role(
            id=retained_role_id,
            farm_id=int(owner["X-Farm-Id"]),
            code=None,
            name="Retained ceiling role",
            description="Valid retained role at the PostgreSQL identity ceiling",
            permissions='["tasks.view"]',
            revision=1,
        )
        db.add(retained_role)
        await db.commit()

    updated = await client.put(
        f"/api/team/roles/{retained_role_id}",
        headers=owner,
        json={
            "name": "Updated ceiling role",
            "description": "Updated through the public owner contract",
            "permissions": ["tasks.view"],
            "expected_revision": 1,
        },
    )
    assert updated.status_code == 200, "An owner may edit a valid retained role at the int4 ceiling"
    result = updated.json()
    assert result["id"] == retained_role_id
    assert result["name"] == "Updated ceiling role"
    assert result["permissions"] == ["tasks.view"]
    assert result["revision"] > 1
    async with get_sessionmaker()() as db:
        persisted_role = await db.get(Role, retained_role_id)
    assert persisted_role is not None
    assert persisted_role.name == result["name"]
    assert persisted_role.description == "Updated through the public owner contract"
    assert persisted_role.permission_set() == {"tasks.view"}
    assert persisted_role.revision == result["revision"]


@pytest.mark.parametrize(
    "operation", ["worker-role", "worker-status", "role-update", "role-delete"]
)
async def test_forbidden_team_administration_rejects_before_waiting_for_the_protected_target(
    client: httpx.AsyncClient,
    operation: str,
) -> None:
    from .test_team_extended import team_manager_headers

    owner = await owner_with_farm(client, email="preflight-owner@farm.in")
    manager, _ = await team_manager_headers(client, owner, extra_perms=["tasks.view"])
    protected_role = await create_custom_role(
        client, owner, "Owner-controlled delegate", ["team.manage", "tasks.view"]
    )
    worker = await add_worker(client, owner, protected_role["id"], "preflight-peer@farm.in")
    assert worker.status_code == 201, worker.text
    destination = await create_custom_role(client, owner, "Ordinary operator", ["tasks.view"])
    holder = get_sessionmaker()()
    attempt = None
    blocked = False
    try:
        if operation.startswith("worker-"):
            await holder.execute(
                select(FarmMembership)
                .where(FarmMembership.id == worker.json()["id"])
                .with_for_update()
            )
        else:
            await holder.execute(
                select(Role).where(Role.id == protected_role["id"]).with_for_update()
            )
        holder_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        if operation == "worker-role":
            work = client.post(
                f"/api/team/workers/{worker.json()['id']}/role",
                headers=manager,
                json={"role_id": destination["id"]},
            )
        elif operation == "worker-status":
            work = client.put(
                f"/api/team/workers/{worker.json()['id']}/status",
                headers=manager,
                json={"is_active": False},
            )
        elif operation == "role-update":
            work = client.put(
                f"/api/team/roles/{protected_role['id']}",
                headers=manager,
                json={
                    "name": "Unauthorized rename",
                    "permissions": ["tasks.view"],
                    "expected_revision": protected_role["revision"],
                },
            )
        else:
            work = client.delete(f"/api/team/roles/{protected_role['id']}", headers=manager)
        attempt = asyncio.create_task(work)
        for _ in range(1000):
            if attempt.done():
                break
            async with get_sessionmaker()() as observer:
                blocked = bool(
                    await observer.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                            "WHERE datname = current_database() "
                            "AND :holder = ANY(pg_blocking_pids(pid)))"
                        ),
                        {"holder": holder_pid},
                    )
                )
            if blocked:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("The request must finish or expose its owned lock dependency")
        await holder.rollback()
        error = None
        result = None
        try:
            result = await asyncio.wait_for(attempt, 10)
        except Exception as exc:
            error = exc
        assert error is None and result is not None and result.status_code == 403
        assert not blocked, (
            "A caller already known to be forbidden must not wait on the target's write lock"
        )
    finally:
        await holder.rollback()
        await holder.close()
        if attempt is not None:
            if not attempt.done():
                attempt.cancel()
            await asyncio.gather(attempt, return_exceptions=True)


async def test_a_tablet_can_cancel_its_exact_bearer_session_without_canceling_the_browser(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="pin-exact-cancel-owner@farm.in")
    cookie_name = get_settings().refresh_cookie_name
    browser_cookie = client.cookies.get(cookie_name)
    assert browser_cookie is not None
    membership, farm = await _make_pin_worker(
        client, owner, email="pin-exact-cancel-worker@farm.in"
    )
    login = await _worker_login(client, farm, membership, "4321")
    assert login.status_code == 200, login.text
    tablet = {"Authorization": f"Bearer {login.json()['access_token']}"}
    canceled = await client.post(
        "/api/auth/logout-session",
        headers=tablet | {"Cookie": f"{cookie_name}={browser_cookie}"},
    )
    assert canceled.status_code == 204, canceled.text
    assert "set-cookie" not in canceled.headers
    assert (await client.get("/api/auth/me", headers=tablet)).status_code == 401
    assert (await client.get("/api/auth/me", headers=owner)).status_code == 200
    refreshed = await client.post(
        "/api/auth/refresh", headers={"Cookie": f"{cookie_name}={browser_cookie}"}
    )
    assert refreshed.status_code == 200, refreshed.text


async def test_independent_roster_pins_can_share_the_same_retained_global_identity(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="shared-roster-owner@farm.in")
    role = await create_custom_role(client, owner, "First retained assignment", ["tasks.view"])
    created = await add_worker(client, owner, role["id"], "shared-roster-worker@farm.in")
    assert created.status_code == 201, created.text
    async with get_sessionmaker()() as seed:
        # Supported retained affiliation: the same global account can have a
        # membership on another farm. Each roster mutation owns a different
        # membership lock but only pins the common identity for reading.
        existing_farm = await seed.get(Farm, int(owner["X-Farm-Id"]))
        assert existing_farm is not None
        farm = Farm(name="Other retained assignment", owner_id=existing_farm.owner_id)
        seed.add(farm)
        await seed.flush()
        other_role = Role(farm_id=farm.id, name="Retained assignment", permissions='["tasks.view"]')
        seed.add(other_role)
        await seed.flush()
        other_member = FarmMembership(
            farm_id=farm.id,
            user_id=int(created.json()["user_id"]),
            role_id=other_role.id,
            is_active=True,
        )
        seed.add(other_member)
        await seed.commit()
        other_member_id = other_member.id
    async with get_sessionmaker()() as first, get_sessionmaker()() as second:
        membership = (
            await first.execute(
                select(FarmMembership)
                .where(FarmMembership.id == created.json()["id"])
                .with_for_update()
            )
        ).scalar_one()
        other_membership = (
            await second.execute(
                select(FarmMembership).where(FarmMembership.id == other_member_id).with_for_update()
            )
        ).scalar_one()
        pinned = await team._pin_membership_user(first, membership)
        await second.execute(text("SET LOCAL lock_timeout = '1s'"))
        error = None
        other_pinned = None
        try:
            other_pinned = await team._pin_membership_user(second, other_membership)
        except Exception as exc:
            error = exc
        assert error is None, "Independent roster reads must keep compatible global-identity locks"
        assert other_pinned is not None and other_pinned.id == pinned.id
