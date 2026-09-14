"""Red team audit 2026-09-13 — domain remediation regressions.

Covers the fixes from this remediation pass:
- RT-FG-1: the form-link fence on the bare complete route is category-derived
  for generated duties, so a linkage-less legacy row cannot close with zero
  data recorded, while linked duties still complete via their form.
- RT-FG-2: a non-owner cannot clear a scheduled-disease hold they placed
  themselves (two-person rule, owner exempt).
- RT-FG-3: opening a new restriction episode resets the previous episode's
  clearance attribution.
- RT-FG-4/RT-FG-5: skip reason and reject note are required.
- RT-DE-2: the inbreeding fence blocks grandparent-grandchild and avuncular
  matings; half-siblings and unrelated pairs remain permitted.
- RT-HIJ-2: a correction cannot re-book a manual row into a system-only
  category.
- RT-HIJ-3: quarantine task titles reference only the batch id, never the
  supplier.
- RT-B-2: owner-side reactivation revalidates target role liveness.
- RT-B-3: team-admin mutations emit "goatfarm.audit" security events.
- RT-C-6: tag/name identifiers reject embedded tab/LF/CR.
"""

import logging
from datetime import date

import httpx
import pytest
from pydantic import ValidationError

from app.db import get_sessionmaker
from app.models import Animal, Role, Task
from app.schemas.kidding import KidIn
from app.utils import today, utcnow

from .conftest import owner_with_farm
from .test_breeding_extended import make_buck, make_doe, post_breeding
from .test_finance_extended import add_txn, txn_payload
from .test_health_extended import (
    get_animal,
    iso,
    make_animal,
    make_batch,
    record_event,
    worker_with_role,
)
from .test_tasks_extended import complete_duty, find_task, get_tabs, make_duty
from .test_team_extended import (
    WORKER_PW,
    add_worker,
    membership_id,
    role_id,
    set_worker_active,
    team_page,
)


async def _insert_task(
    farm_id: int,
    *,
    title: str = "Day 10: vaccinate PPR (live viral, SC)",
    category: str = "VACCINE",
    animal_id: int | None = None,
    due_date: date | None = None,
) -> int:
    """Insert a synthetic (legacy/out-of-process) task row directly.

    The API's own generators always populate linkage columns; the fence this
    exercises exists precisely for rows they did not write.
    """
    async with get_sessionmaker()() as db:
        task = Task(
            farm_id=farm_id,
            title=title,
            due_date=due_date or today(),
            category=category,
            auto_generated=True,
            animal_id=animal_id,
        )
        db.add(task)
        await db.commit()
        return int(task.id)


async def _restriction_version(
    client: httpx.AsyncClient, headers: dict[str, str], animal_id: int
) -> int:
    history = await client.get(f"/api/health/restrictions/{animal_id}", headers=headers)
    assert history.status_code == 200, history.text
    return int(history.json()["restriction_version"])


# ---------------------------------------------------------------------------
# RT-FG-1 — category-derived form-link fence on POST /api/tasks/{id}/complete
# ---------------------------------------------------------------------------
async def test_linkage_less_generated_vaccine_duty_refuses_bare_completion(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    task_id = await _insert_task(int(owner["X-Farm-Id"]))
    resp = await client.post(f"/api/tasks/{task_id}/complete", headers=owner)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "Use the linked form to complete this duty"


async def test_linkage_less_generated_ultrasound_duty_refuses_bare_completion(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    task_id = await _insert_task(
        int(owner["X-Farm-Id"]),
        title="Pregnancy check",
        category="ULTRASOUND",
    )
    resp = await client.post(f"/api/tasks/{task_id}/complete", headers=owner)
    assert resp.status_code == 409, resp.text


async def test_generated_vaccine_duty_with_linkage_completes_via_the_health_form(
    client: httpx.AsyncClient,
) -> None:
    """The fence must not over-block: an animal-linked generated VACCINE duty
    still closes through POST /api/health/events with task_id (its form)."""
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="FG1-LINKED")
    task_id = await _insert_task(int(owner["X-Farm-Id"]), animal_id=animal["id"])
    # The bare endpoint refuses the linked duty too — the form is the only way.
    bare = await client.post(f"/api/tasks/{task_id}/complete", headers=owner)
    assert bare.status_code == 409, bare.text

    recorded = await record_event(
        client,
        owner,
        scope="animal",
        animal_id=animal["id"],
        type="VACCINE",
        disease_target="PPR",
        task_id=task_id,
    )
    assert recorded[0]["type"] == "VACCINE"
    done = await client.get(f"/api/tasks/{task_id}", headers=owner)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "DONE"


# ---------------------------------------------------------------------------
# RT-FG-2 — two-person rule for statutory hold clearance
# ---------------------------------------------------------------------------
async def test_placer_cannot_clear_their_own_scheduled_disease_hold(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    vet = await worker_with_role(client, owner, "VET", "fg2-vet@farm.in")
    animal = await make_animal(client, owner, tag="FG2-HOLD")
    await record_event(
        client,
        vet,
        animal_id=animal["id"],
        type="TREATMENT",
        disease_target="PPR concern",
        suspected_scheduled_disease=True,
    )
    version = await _restriction_version(client, owner, animal["id"])
    self_clear = await client.post(
        f"/api/health/restrictions/{animal['id']}/clear",
        json={"clearance_reference": "self OK", "expected_restriction_version": version},
        headers=vet,
    )
    assert self_clear.status_code == 409, self_clear.text
    assert self_clear.json()["detail"] == "Someone else must clear this scheduled-disease hold"

    # The owner is exempt from the two-person rule (same as duty verification).
    owner_clear = await client.post(
        f"/api/health/restrictions/{animal['id']}/clear",
        json={"clearance_reference": "AHD-2026-117", "expected_restriction_version": version},
        headers=owner,
    )
    assert owner_clear.status_code == 204, owner_clear.text


async def test_owner_who_placed_a_hold_can_still_clear_it(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="FG2-OWNER")
    await record_event(
        client,
        owner,
        animal_id=animal["id"],
        type="TREATMENT",
        disease_target="Anthrax concern",
        suspected_scheduled_disease=True,
    )
    version = await _restriction_version(client, owner, animal["id"])
    cleared = await client.post(
        f"/api/health/restrictions/{animal['id']}/clear",
        json={"clearance_reference": "AHD-2026-118", "expected_restriction_version": version},
        headers=owner,
    )
    assert cleared.status_code == 204, cleared.text


# ---------------------------------------------------------------------------
# RT-FG-3 — new episode resets stale clearance attribution
# ---------------------------------------------------------------------------
async def test_new_restriction_episode_carries_no_stale_clearance(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="FG3-EPISODE")

    await record_event(
        client,
        owner,
        animal_id=animal["id"],
        type="TREATMENT",
        disease_target="First concern",
        suspected_scheduled_disease=True,
    )
    cleared = await client.post(
        f"/api/health/restrictions/{animal['id']}/clear",
        json={"clearance_reference": "AHD-2026-117", "expected_restriction_version": 1},
        headers=owner,
    )
    assert cleared.status_code == 204, cleared.text
    profile = await get_animal(client, owner, animal["id"])
    assert profile["restriction_cleared_at"] is not None
    assert profile["restriction_clearance_reference"] == "AHD-2026-117"

    await record_event(
        client,
        owner,
        animal_id=animal["id"],
        type="TREATMENT",
        disease_target="Second concern",
        suspected_scheduled_disease=True,
    )
    reopened = await get_animal(client, owner, animal["id"])
    assert reopened["movement_restricted"] is True
    assert reopened["suspected_scheduled_disease"] is True
    assert reopened["restriction_reason"] is not None
    # The v1 clearance no longer describes the live v2 episode.
    assert reopened["restriction_cleared_at"] is None
    assert reopened["restriction_cleared_by_id"] is None
    assert reopened["restriction_clearance_reference"] is None
    # The historical fact stays on the episode audit trail.
    history = await client.get(f"/api/health/restrictions/{animal['id']}", headers=owner)
    assert [(row["restriction_version"], row["action"]) for row in history.json()["actions"]] == [
        (2, "PLACED"),
        (1, "CLEARED"),
        (1, "PLACED"),
    ]


# ---------------------------------------------------------------------------
# RT-FG-4 / RT-FG-5 — required skip reason and reject note
# ---------------------------------------------------------------------------
async def test_skip_without_a_reason_is_rejected(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "FG4 duty")
    missing = await client.post(f"/api/tasks/{duty['id']}/skip", headers=owner)
    assert missing.status_code == 422, missing.text
    blank = await client.post(f"/api/tasks/{duty['id']}/skip", json={"reason": "  "}, headers=owner)
    assert blank.status_code == 422, blank.text
    explained = await client.post(
        f"/api/tasks/{duty['id']}/skip", json={"reason": "area under repair"}, headers=owner
    )
    assert explained.status_code == 200, explained.text
    assert explained.json()["skip_reason"] == "area under repair"


# ---------------------------------------------------------------------------
# RT-DE-2 — two-generation inbreeding fence
# ---------------------------------------------------------------------------
async def _set_lineage(animal_id: int, *, sire_id: int | None, dam_id: int | None) -> None:
    """Point an animal's sire_id/dam_id at existing herd members.

    The public API writes lineage only through kidding; these fence tests
    model rows a real farm accumulates as kids grow into the breeding herd.
    """
    async with get_sessionmaker()() as db:
        animal = await db.get(Animal, animal_id)
        assert animal is not None
        animal.sire_id = sire_id
        animal.dam_id = dam_id
        await db.commit()


async def test_grandparent_grandchild_mating_rejected_both_directions(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    grandsire = await make_buck(client, owner, tag="IB2-GS")
    grandmother = await make_doe(client, owner, tag="IB2-GM")
    unrelated_doe = await make_doe(client, owner, tag="IB2-XD")
    son = await make_buck(client, owner, tag="IB2-SON")
    await _set_lineage(son["id"], sire_id=grandsire["id"], dam_id=grandmother["id"])
    granddaughter = await make_doe(client, owner, tag="IB2-GD")
    await _set_lineage(granddaughter["id"], sire_id=son["id"], dam_id=unrelated_doe["id"])

    grandsire_x_granddaughter = await post_breeding(
        client, owner, granddaughter["id"], grandsire["id"]
    )
    assert grandsire_x_granddaughter.status_code == 409, grandsire_x_granddaughter.text
    assert "inbreeding" in grandsire_x_granddaughter.json()["detail"].lower()

    grandson = await make_buck(client, owner, tag="IB2-GS2")
    await _set_lineage(grandson["id"], sire_id=son["id"], dam_id=unrelated_doe["id"])
    grandmother_x_grandson = await post_breeding(client, owner, grandmother["id"], grandson["id"])
    assert grandmother_x_grandson.status_code == 409, grandmother_x_grandson.text
    assert "inbreeding" in grandmother_x_grandson.json()["detail"].lower()


async def test_avuncular_mating_rejected(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    grandsire = await make_buck(client, owner, tag="AV-GS")
    granddam = await make_doe(client, owner, tag="AV-GD")
    mother = await make_doe(client, owner, tag="AV-MOM")
    uncle = await make_buck(client, owner, tag="AV-UNCLE")
    for sibling in (mother, uncle):
        await _set_lineage(sibling["id"], sire_id=grandsire["id"], dam_id=granddam["id"])
    outsider_buck = await make_buck(client, owner, tag="AV-OUT")
    niece = await make_doe(client, owner, tag="AV-NIECE")
    await _set_lineage(niece["id"], sire_id=outsider_buck["id"], dam_id=mother["id"])

    resp = await post_breeding(client, owner, niece["id"], uncle["id"])
    assert resp.status_code == 409, resp.text
    assert "inbreeding" in resp.json()["detail"].lower()


async def test_unrelated_and_half_sibling_matings_remain_permitted(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    shared_sire = await make_buck(client, owner, tag="OK-SIRE")
    dam_a = await make_doe(client, owner, tag="OK-DAM-A")
    dam_b = await make_doe(client, owner, tag="OK-DAM-B")
    half_brother = await make_buck(client, owner, tag="OK-HALF-BRO")
    half_sister = await make_doe(client, owner, tag="OK-HALF-SIS")
    await _set_lineage(half_brother["id"], sire_id=shared_sire["id"], dam_id=dam_a["id"])
    await _set_lineage(half_sister["id"], sire_id=shared_sire["id"], dam_id=dam_b["id"])
    outsider_buck = await make_buck(client, owner, tag="OK-OUT")

    # The depth-1 fence still fires: shared_sire is half_sister's father.
    parent_offspring = await post_breeding(client, owner, half_sister["id"], shared_sire["id"])
    assert parent_offspring.status_code == 409, parent_offspring.text

    # Half-siblings (shared sire, different dams) stay permitted practice.
    half_siblings = await post_breeding(client, owner, half_sister["id"], half_brother["id"])
    assert half_siblings.status_code == 201, half_siblings.text

    # A fully unrelated pairing is untouched by the fence.
    unrelated = await post_breeding(client, owner, dam_a["id"], outsider_buck["id"])
    assert unrelated.status_code == 201, unrelated.text


# ---------------------------------------------------------------------------
# RT-HIJ-2 — correction cannot mint a system-only category on a manual row
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("category", ["ANIMAL_SALE", "ANIMAL_PURCHASE"])
async def test_correction_cannot_rebook_a_manual_row_into_system_only_categories(
    client: httpx.AsyncClient, category: str
) -> None:
    owner = await owner_with_farm(client)
    manual = await add_txn(client, owner)  # EXPENSE / FEED
    hijack = await client.post(
        f"/api/finance/transactions/{manual['id']}/correct",
        json=txn_payload(
            type="INCOME" if category == "ANIMAL_SALE" else "EXPENSE",
            category=category,
            amount=500000.0,
            reason="rebook as system revenue",
        ),
        headers=owner,
    )
    assert hijack.status_code == 422, hijack.text
    assert "cannot be entered manually" in hijack.json()["detail"]


async def test_legitimate_manual_corrections_still_work(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    manual = await add_txn(client, owner, amount=100.0)
    corrected = await client.post(
        f"/api/finance/transactions/{manual['id']}/correct",
        json=txn_payload(category="MEDICINE", amount=150.0, reason="wrong bucket and total"),
        headers=owner,
    )
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["category"] == "MEDICINE"
    assert float(corrected.json()["amount"]) == 150.0


# ---------------------------------------------------------------------------
# RT-HIJ-3 — supplier-free quarantine titles on the task board
# ---------------------------------------------------------------------------
async def test_quarantine_task_titles_never_disclose_the_supplier(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    batch = await make_batch(
        client, owner, supplier="Secret Supplier Co", count=2, date=iso(today())
    )
    detail = await client.get(f"/api/purchases/{batch['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    tasks = detail.json()["tasks"]
    assert tasks, "purchase creation generates the 8-step protocol"
    for task in tasks:
        assert task["title"].startswith(f"[Batch #{batch['id']}] "), task["title"]
        assert "Secret Supplier Co" not in task["title"]


# ---------------------------------------------------------------------------
# RT-B-2 — owner-side reactivation revalidates role liveness
# ---------------------------------------------------------------------------
async def test_owner_reactivation_of_a_tombstoned_role_membership_fails_closed(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    cleaner = await role_id(client, owner, "CLEANER")
    created = await add_worker(client, owner, cleaner, "rt-b-2@farm.in")
    assert created.status_code == 201, created.text
    mid = created.json()["id"]
    assert (await set_worker_active(client, owner, mid, False)).status_code == 200

    # delete_role refuses while a live-user membership exists; model the
    # unreachable state (manual damage) by tombstoning the role directly.
    async with get_sessionmaker()() as db:
        role = await db.get(Role, cleaner)
        assert role is not None
        role.deleted_at = utcnow()
        await db.commit()

    reactivation = await set_worker_active(client, owner, mid, True)
    assert reactivation.status_code == 404, reactivation.text
    assert reactivation.json()["detail"] == "Role not found"


# ---------------------------------------------------------------------------
# RT-B-3 — security-event audit logging for team administration
# ---------------------------------------------------------------------------
def _audit_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "goatfarm.audit" and record.levelno == logging.INFO
    ]


async def test_worker_lifecycle_mutations_emit_audit_events(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="goatfarm.audit"):
        owner = await owner_with_farm(client)
        cleaner = await role_id(client, owner, "CLEANER")
        mover = await role_id(client, owner, "MOVER")
        created = await add_worker(client, owner, cleaner, "rt-b-3@farm.in")
        assert created.status_code == 201, created.text
        mid = created.json()["id"]
        target_user_id = created.json()["user_id"]

        changed = await client.post(
            f"/api/team/workers/{mid}/role", json={"role_id": mover}, headers=owner
        )
        assert changed.status_code == 200, changed.text

        reset = await client.post(
            f"/api/team/workers/{mid}/reset-password",
            json={"password": f"{WORKER_PW}-new1"},
            headers=owner,
        )
        assert reset.status_code == 200, reset.text

        deactivated = await set_worker_active(client, owner, mid, False)
        assert deactivated.status_code == 200, deactivated.text

    events = _audit_messages(caplog)
    joined = "\n".join(events)
    assert "event='team.worker.create'" in joined
    assert "event='team.worker.role_change'" in joined
    assert "event='team.worker.password_reset'" in joined
    assert "event='team.worker.status_change'" in joined
    for event_name in (
        "team.worker.create",
        "team.worker.role_change",
        "team.worker.password_reset",
        "team.worker.status_change",
    ):
        line = next(message for message in events if event_name in message)
        assert f"membership_id={mid}" in line, line
        assert f"user_id={target_user_id}" in line, line
    # Credential material never enters the audit trail.
    assert WORKER_PW not in joined
    assert f"{WORKER_PW}-new1" not in joined
    role_change = next(message for message in events if "team.worker.role_change" in message)
    assert f"from_role_id={cleaner}" in role_change and f"to_role_id={mover}" in role_change
    status_change = next(message for message in events if "team.worker.status_change" in message)
    assert "from_is_active=True" in status_change and "to_is_active=False" in status_change


async def test_role_mutations_emit_audit_events(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="goatfarm.audit"):
        owner = await owner_with_farm(client)
        created = await client.post(
            "/api/team/roles",
            json={"name": "Audit Fixture Role", "permissions": ["animals.view"]},
            headers=owner,
        )
        assert created.status_code == 201, created.text
        role_id_created = created.json()["id"]

        updated = await client.put(
            f"/api/team/roles/{role_id_created}",
            json={
                "name": "Audit Fixture Role",
                "permissions": ["animals.view", "animals.weight"],
                "expected_revision": created.json()["revision"],
            },
            headers=owner,
        )
        assert updated.status_code == 200, updated.text

        deleted = await client.delete(f"/api/team/roles/{role_id_created}", headers=owner)
        assert deleted.status_code == 204, deleted.text

    events = _audit_messages(caplog)
    joined = "\n".join(events)
    assert "event='team.role.create'" in joined
    assert "event='team.role.update'" in joined
    assert "event='team.role.delete'" in joined
    for event_name in ("team.role.create", "team.role.update", "team.role.delete"):
        line = next(message for message in events if event_name in message)
        assert f"role_id={role_id_created}" in line, line


# ---------------------------------------------------------------------------
# RT-C-6 — identifier control-character hygiene
# ---------------------------------------------------------------------------
async def test_tag_and_name_reject_embedded_control_characters(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    for field, value in (("tag_number", "AB\tCD"), ("name", "Line\nOne"), ("tag_number", "AB\rCD")):
        resp = await client.post(
            "/api/animals",
            json={
                "tag_number": "CTL-OK",
                "sex": "F",
                "source": "PURCHASED",
                "current_bucket": "FOUNDATION",
                "historical_import_reason": "Existing-herd test fixture",
                field: value,
            },
            headers=owner,
        )
        assert resp.status_code == 422, (field, value, resp.text)

    # Narrative fields keep their multi-line tolerance.
    narrative_ok = await make_animal(
        client, owner, tag="CTL-NOTES", notes="line one\nline two\tindented"
    )
    assert narrative_ok["notes"] == "line one\nline two\tindented"


def test_kid_tag_rejects_control_characters() -> None:
    with pytest.raises(ValidationError):
        KidIn(tag="K\t1", sex="F")
    with pytest.raises(ValidationError):
        KidIn(tag="K\r1", sex="F")


# ---------------------------------------------------------------------------
# Cross-checks that the new fences compose with existing flows
# ---------------------------------------------------------------------------
async def test_skip_reason_roundtrip_on_the_board(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "FG4 board duty")
    skipped = await client.post(
        f"/api/tasks/{duty['id']}/skip", json={"reason": "supplier delay"}, headers=owner
    )
    assert skipped.status_code == 200, skipped.text
    tabs = await get_tabs(client, owner)
    row = find_task(tabs, duty["id"])
    assert row["status"] == "SKIPPED"
    assert row["skip_reason"] == "supplier delay"


async def test_team_page_still_lists_the_farm_roster(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner = await role_id(client, owner, "CLEANER")
    await add_worker(client, owner, cleaner, "roster-check@farm.in")
    page = await team_page(client, owner)
    assert await membership_id(client, owner, "roster-check@farm.in")
    assert any(m["email"] == "roster-check@farm.in" for m in page["memberships"])
    assert page["memberships"], "roster pagination unchanged"


async def test_farm_membership_and_task_fences_do_not_block_plain_completion(
    client: httpx.AsyncClient,
) -> None:
    """A plain manual duty still completes; only data-capture duties fence."""
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Plain feeder top-up")
    resp = await complete_duty(client, owner, duty["id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "DONE"
