"""Target-stable bulk health writes and auditable restriction episodes."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db import get_engine, get_sessionmaker
from app.main import create_app
from app.models import HealthEvent, MovementRestrictionAction, Task, Transaction, VaccineTemplate
from app.services.health import (
    _legacy_event_matches,
    protocol_phrase_of,
    target_matches_template,
    template_name_for_task,
)
from app.utils import today, utcnow

from .conftest import create_farm, owner_with_farm
from .test_health_extended import iso, make_animal, make_batch, record_event


async def preview(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    **target: object,
) -> httpx.Response:
    return await client.post("/api/health/events/preview", json=target, headers=headers)


async def test_bulk_preview_is_sorted_and_new_entrants_are_not_silently_included(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    first = await make_animal(client, owner, tag="SNAPSHOT-1", name="First goat")
    second = await make_animal(client, owner, tag="SNAPSHOT-2", name="Second goat")

    reviewed = await preview(client, owner, scope="bucket", bucket="FOUNDATION")
    assert reviewed.status_code == 200, reviewed.text
    body = reviewed.json()
    expected_ids = body["target_animal_ids"]
    assert expected_ids == sorted([first["id"], second["id"]])
    assert body["target_animals"] == [
        {"id": first["id"], "tag_number": "SNAPSHOT-1", "name": "First goat"},
        {"id": second["id"], "tag_number": "SNAPSHOT-2", "name": "Second goat"},
    ]
    assert [row["id"] for row in body["target_animals"]] == expected_ids
    assert body["target_count"] == 2
    assert body["max_targets"] == 250

    entrant = await make_animal(client, owner, tag="SNAPSHOT-LATE")
    recorded = await client.post(
        "/api/health/events",
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "expected_animal_ids": expected_ids,
            "type": "FOOTBATH",
        },
        headers=owner,
    )
    assert recorded.status_code == 201, recorded.text
    assert [event["animal_id"] for event in recorded.json()] == expected_ids
    assert entrant["id"] not in {event["animal_id"] for event in recorded.json()}

    refreshed = await preview(client, owner, scope="bucket", bucket="FOUNDATION")
    assert refreshed.status_code == 200
    assert refreshed.json()["target_count"] == 3


async def test_bulk_write_rejects_missing_duplicate_and_stale_reviewed_sets(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    first = await make_animal(client, owner, tag="STALE-1")
    second = await make_animal(client, owner, tag="STALE-2")
    reviewed = await preview(client, owner, scope="bucket", bucket="FOUNDATION")
    expected = reviewed.json()["target_animal_ids"]

    missing_snapshot = await client.post(
        "/api/health/events",
        json={"scope": "bucket", "bucket": "FOUNDATION", "type": "VACCINE"},
        headers=owner,
    )
    assert missing_snapshot.status_code == 409

    duplicate = await client.post(
        "/api/health/events",
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "expected_animal_ids": [first["id"], first["id"]],
            "type": "VACCINE",
        },
        headers=owner,
    )
    assert duplicate.status_code == 409
    assert "duplicate" in duplicate.json()["detail"].lower()

    nonexistent = await client.post(
        "/api/health/events",
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "expected_animal_ids": [*expected, 2_147_483_647],
            "type": "VACCINE",
        },
        headers=owner,
    )
    assert nonexistent.status_code == 409

    died = await client.post(
        f"/api/animals/{second['id']}/status",
        json={"new_status": "DEAD"},
        headers=owner,
    )
    assert died.status_code == 200, died.text
    stale = await client.post(
        "/api/health/events",
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "expected_animal_ids": expected,
            "type": "VACCINE",
        },
        headers=owner,
    )
    assert stale.status_code == 409
    assert "stale" in stale.json()["detail"].lower()
    ledger = await client.get("/api/health/events", headers=owner)
    assert ledger.status_code == 200
    assert ledger.json()["total"] == 0


async def test_bucket_bulk_stays_at_250_while_valid_purchase_batch_remains_treatable(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="BOUND-1")

    oversized_write = await client.post(
        "/api/health/events",
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "expected_animal_ids": [animal["id"]] * 251,
            "type": "VACCINE",
        },
        headers=owner,
    )
    assert oversized_write.status_code == 409
    assert "250" in oversized_write.json()["detail"]

    # Purchase creation legitimately supports larger batches. The linked
    # quarantine protocol must therefore preview and treat all 251 targets,
    # rather than creating an unfinishable duty at the old 250 boundary.
    batch = await make_batch(
        client,
        owner,
        count=251,
        date=iso(today() - timedelta(days=3)),
    )
    batch_preview = await preview(
        client,
        owner,
        scope="batch",
        purchase_batch_id=batch["id"],
    )
    assert batch_preview.status_code == 200, batch_preview.text
    preview_body = batch_preview.json()
    assert preview_body["target_count"] == 251
    assert preview_body["max_targets"] == 1000

    detail = await client.get(f"/api/purchases/{batch['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    deworming = next(task for task in detail.json()["tasks"] if task["category"] == "DEWORMING")
    treated = await client.post(
        "/api/health/events",
        json={
            "scope": "batch",
            "purchase_batch_id": batch["id"],
            "expected_animal_ids": preview_body["target_animal_ids"],
            "type": "DEWORMING",
            "disease_target": "Deworming",
            "schedule_template_name": "Deworming",
            "task_id": deworming["id"],
        },
        headers=owner,
    )
    assert treated.status_code == 201, treated.text
    assert len(treated.json()) == 251


async def test_restriction_versions_history_and_explicit_supersession(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="EPISODE-1")
    first = await record_event(
        client,
        owner,
        animal_id=animal["id"],
        type="TREATMENT",
        disease_target="PPR concern",
        suspected_scheduled_disease=True,
    )
    second = await record_event(
        client,
        owner,
        animal_id=animal["id"],
        type="TREATMENT",
        disease_target="Anthrax concern",
        suspected_scheduled_disease=True,
    )

    history = await client.get(f"/api/health/restrictions/{animal['id']}", headers=owner)
    assert history.status_code == 200, history.text
    body = history.json()
    assert body["restriction_version"] == 2
    assert body["active"] is True
    assert (body["total"], body["limit"], body["offset"]) == (2, 25, 0)
    assert [(row["restriction_version"], row["action"]) for row in body["actions"]] == [
        (2, "PLACED"),
        (1, "PLACED"),
    ]
    assert [row["disease_target"] for row in body["actions"]] == [
        "Anthrax concern",
        "PPR concern",
    ]
    assert [row["health_event_id"] for row in body["actions"]] == [
        second[0]["id"],
        first[0]["id"],
    ]
    assert all(row["acted_by_id"] is not None for row in body["actions"])
    assert all(row["acted_at"] for row in body["actions"])

    stale_clear = await client.post(
        f"/api/health/restrictions/{animal['id']}/clear",
        json={"clearance_reference": "Old episode clearance", "expected_restriction_version": 1},
        headers=owner,
    )
    assert stale_clear.status_code == 409
    current_clear = await client.post(
        f"/api/health/restrictions/{animal['id']}/clear",
        json={"clearance_reference": "AHD current clearance", "expected_restriction_version": 2},
        headers=owner,
    )
    assert current_clear.status_code == 204, current_clear.text

    closed = await client.get(f"/api/health/restrictions/{animal['id']}", headers=owner)
    assert closed.json()["active"] is False
    # Episode one is deliberately superseded, not falsely reported cleared.
    assert [(row["restriction_version"], row["action"]) for row in closed.json()["actions"]] == [
        (2, "CLEARED"),
        (2, "PLACED"),
        (1, "PLACED"),
    ]


async def test_restriction_history_is_bounded_exact_and_stably_newest_first(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="RESTRICTION-PAGE")
    acted_at = utcnow()
    async with get_sessionmaker()() as db:
        actions = [
            MovementRestrictionAction(
                farm_id=int(owner["X-Farm-Id"]),
                animal_id=animal["id"],
                restriction_version=version,
                action="PLACED",
                acted_at=acted_at,
                acted_by_id=None,
                action_reference=f"Historical placement {version}",
                disease_target=f"Concern {version}",
                health_event_id=None,
            )
            for version in range(1, 131)
        ]
        db.add_all(actions)
        await db.flush()
        inserted_ids = [action.id for action in actions]
        await db.commit()

    newest_ids = list(reversed(inserted_ids))
    default_page = await client.get(
        f"/api/health/restrictions/{animal['id']}",
        headers=owner,
    )
    assert default_page.status_code == 200, default_page.text
    body = default_page.json()
    assert set(body) == {
        "animal_id",
        "restriction_version",
        "active",
        "actions",
        "total",
        "limit",
        "offset",
    }
    assert (body["total"], body["limit"], body["offset"]) == (130, 25, 0)
    assert [row["id"] for row in body["actions"]] == newest_ids[:25]

    middle_page = await client.get(
        f"/api/health/restrictions/{animal['id']}?limit=100&offset=25",
        headers=owner,
    )
    assert middle_page.status_code == 200, middle_page.text
    middle = middle_page.json()
    assert (middle["total"], middle["limit"], middle["offset"]) == (130, 100, 25)
    assert [row["id"] for row in middle["actions"]] == newest_ids[25:125]

    tail_page = await client.get(
        f"/api/health/restrictions/{animal['id']}?limit=100&offset=125",
        headers=owner,
    )
    assert tail_page.status_code == 200, tail_page.text
    assert [row["id"] for row in tail_page.json()["actions"]] == newest_ids[125:]
    too_large = await client.get(
        f"/api/health/restrictions/{animal['id']}?limit=101",
        headers=owner,
    )
    assert too_large.status_code == 422


async def test_schedule_query_work_is_bounded_by_templates_not_event_history(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="SCHEDULE-VOLUME")

    async def captured_schedule() -> tuple[httpx.Response, list[str]]:
        statements: list[str] = []

        def capture_statement(
            _conn: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: object,
        ) -> None:
            statements.append(statement)

        engine = get_engine().sync_engine
        sa_event.listen(engine, "before_cursor_execute", capture_statement)
        try:
            response = await client.get(
                f"/api/health/schedule/{animal['id']}",
                headers=owner,
            )
        finally:
            sa_event.remove(engine, "before_cursor_execute", capture_statement)
        return response, statements

    baseline, baseline_statements = await captured_schedule()
    assert baseline.status_code == 200, baseline.text

    event_date = today()
    async with get_sessionmaker()() as db:
        ppr_id = (
            await db.execute(select(VaccineTemplate.id).where(VaccineTemplate.name == "PPR"))
        ).scalar_one()
        db.add_all(
            [
                HealthEvent(
                    farm_id=int(owner["X-Farm-Id"]),
                    animal_id=animal["id"],
                    purchase_batch_id=None,
                    date=event_date - timedelta(days=1_199 - index),
                    type="VACCINE",
                    product_name="PPR vaccine",
                    schedule_template_name="PPR",
                    schedule_template_id=ppr_id,
                )
                for index in range(1_200)
            ]
            + [
                HealthEvent(
                    farm_id=int(owner["X-Farm-Id"]),
                    animal_id=animal["id"],
                    purchase_batch_id=None,
                    date=event_date - timedelta(days=1_199 - index),
                    type="VACCINE",
                    product_name=f"Unclassified legacy product {index}",
                    schedule_template_name=None,
                    schedule_template_id=None,
                )
                for index in range(1_200)
            ]
        )
        await db.commit()

    expanded, expanded_statements = await captured_schedule()
    assert expanded.status_code == 200, expanded.text
    assert len(expanded_statements) == len(baseline_statements)
    ppr = next(row for row in expanded.json()["rows"] if row["template_name"] == "PPR")
    assert ppr["last_done"] == event_date.isoformat()

    health_event_queries = [
        statement for statement in expanded_statements if "health_events" in statement
    ]
    assert len(health_event_queries) == 2
    canonical_query = next(
        statement for statement in health_event_queries if "UNION ALL" in statement
    )
    legacy_query = next(
        statement
        for statement in health_event_queries
        if "health_events.schedule_template_id IS NULL" in statement
    )
    # Each template uses two bounded probes: newest-two for current status and
    # earliest-one for the permanent primary-dose booster anchor. Work scales
    # with template count, never with the animal's lifetime event volume.
    expected_template_probes = 2 * len(expanded.json()["rows"])
    assert canonical_query.count("LIMIT") == expected_template_probes
    assert canonical_query.count("health_events.schedule_template_id =") == expected_template_probes
    assert legacy_query.count("LIMIT") == 1


async def test_new_schedule_links_are_canonical_immutable_and_indexed(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="SCHEDULE-LINK")
    recorded = await record_event(
        client,
        owner,
        animal_id=animal["id"],
        type="VACCINE",
        product_name="PPR vaccine",
    )
    # Inference is an internal canonical link; it does not rewrite what the
    # farmer actually supplied into the public audit-name field.
    assert recorded[0]["schedule_template_name"] is None

    async with get_sessionmaker()() as db:
        template_ids = dict(
            (
                await db.execute(
                    select(VaccineTemplate.name, VaccineTemplate.id).where(
                        VaccineTemplate.name.in_(["PPR", "FMD"])
                    )
                )
            ).all()
        )
        event_row = await db.get(HealthEvent, recorded[0]["id"])
        assert event_row is not None
        assert event_row.schedule_template_id == template_ids["PPR"]

        with pytest.raises(DBAPIError, match="schedule template link is immutable"):
            await db.execute(
                update(HealthEvent)
                .where(HealthEvent.id == event_row.id)
                .values(schedule_template_id=template_ids["FMD"])
            )
            await db.commit()
        await db.rollback()

        index_definition = (
            await db.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE indexname = 'ix_health_events_animal_template_latest'"
                )
            )
        ).scalar_one()
    assert "(animal_id, schedule_template_id, date DESC, id DESC)" in index_definition


async def test_bounded_legacy_schedule_window_still_matches_unlinked_history(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="SCHEDULE-LEGACY")
    legacy_date = today() - timedelta(days=10)
    async with get_sessionmaker()() as db:
        db.add(
            HealthEvent(
                farm_id=int(owner["X-Farm-Id"]),
                animal_id=animal["id"],
                purchase_batch_id=None,
                date=legacy_date,
                type="VACCINE",
                product_name="PPR vaccine",
                schedule_template_name=None,
                schedule_template_id=None,
            )
        )
        await db.commit()

    schedule = await client.get(f"/api/health/schedule/{animal['id']}", headers=owner)
    assert schedule.status_code == 200, schedule.text
    ppr = next(row for row in schedule.json()["rows"] if row["template_name"] == "PPR")
    assert ppr["last_done"] == legacy_date.isoformat()


async def test_mortality_suspicion_uses_the_same_audited_episode_path(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="MORTALITY-HOLD")
    response = await client.post(
        f"/api/animals/{animal['id']}/status",
        json={
            "new_status": "DEAD",
            "date": iso(today()),
            "mortality_cause": "Sudden death",
            "suspected_scheduled_disease": True,
            "suspected_disease": "Anthrax",
            "authority_notified_at": iso(today()),
        },
        headers=owner,
    )
    assert response.status_code == 200, response.text
    assert response.json()["restriction_version"] == 1
    history = await client.get(f"/api/health/restrictions/{animal['id']}", headers=owner)
    assert history.status_code == 200
    assert history.json()["restriction_version"] == 1
    [action] = history.json()["actions"]
    assert action["action"] == "PLACED"
    assert action["disease_target"] == "Anthrax"
    assert action["health_event_id"] is None
    assert "Mortality status change effective" in action["action_reference"]
    assert action["acted_by_id"] is not None


async def test_clear_racing_a_new_hold_cannot_clear_the_new_episode(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="CLEAR-RACE")
    await record_event(
        client,
        owner,
        animal_id=animal["id"],
        type="TREATMENT",
        disease_target="Initial concern",
        suspected_scheduled_disease=True,
    )

    app = create_app()
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as one,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as two,
    ):
        clear, placement = await asyncio.gather(
            one.post(
                f"/api/health/restrictions/{animal['id']}/clear",
                json={"clearance_reference": "Race clearance", "expected_restriction_version": 1},
                headers=owner,
            ),
            two.post(
                "/api/health/events",
                json={
                    "animal_id": animal["id"],
                    "type": "TREATMENT",
                    "disease_target": "Newer concern",
                    "suspected_scheduled_disease": True,
                },
                headers=owner,
            ),
        )
    assert placement.status_code == 201, placement.text
    assert clear.status_code in {204, 409}, clear.text
    history = await client.get(f"/api/health/restrictions/{animal['id']}", headers=owner)
    assert history.status_code == 200
    assert history.json()["restriction_version"] == 2
    assert history.json()["active"] is True
    assert not any(
        row["restriction_version"] == 2 and row["action"] == "CLEARED"
        for row in history.json()["actions"]
    )


async def test_audit_rows_are_immutable_and_tenant_foreign_keys_fail_closed(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="audit-one@farm.in", farm_name="Audit One")
    other = await create_farm(
        client,
        {"Authorization": owner["Authorization"]},
        name="Audit Two",
    )
    animal_one = await make_animal(client, owner, tag="AUDIT-ONE")
    animal_two = await make_animal(client, other, tag="AUDIT-TWO")
    await record_event(
        client,
        owner,
        animal_id=animal_one["id"],
        type="TREATMENT",
        disease_target="Audit concern",
        suspected_scheduled_disease=True,
    )

    async with get_sessionmaker()() as db:
        action = (await db.execute(select(MovementRestrictionAction))).scalar_one()
        with pytest.raises(DBAPIError, match="immutable"):
            await db.execute(
                update(MovementRestrictionAction)
                .where(MovementRestrictionAction.id == action.id)
                .values(action_reference="rewritten")
            )
            await db.commit()
        await db.rollback()

        db.add(
            MovementRestrictionAction(
                farm_id=int(owner["X-Farm-Id"]),
                animal_id=animal_two["id"],
                restriction_version=1,
                action="PLACED",
                action_reference="Cross-tenant attempt",
                disease_target="Concern",
            )
        )
        with pytest.raises(IntegrityError, match="fk_movement_restriction_actions_farm_animal"):
            await db.commit()


async def test_next_due_provenance_is_enforced_for_direct_database_writes(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="NEXT-DUE-DB")
    async with get_sessionmaker()() as db:
        db.add(
            HealthEvent(
                farm_id=int(owner["X-Farm-Id"]),
                animal_id=animal["id"],
                date=today(),
                type="VACCINE",
                next_due_date=today() + timedelta(days=30),
            )
        )
        with pytest.raises(IntegrityError, match="ck_health_events_next_due_provenance"):
            await db.commit()


@pytest.mark.parametrize("field", ["authority_notified_at", "isolation_started_at"])
async def test_compliance_dates_require_a_suspected_scheduled_disease(
    client: httpx.AsyncClient,
    field: str,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag=f"ORPHAN-{field[:3]}")

    response = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": animal["id"],
            "type": "TREATMENT",
            field: today().isoformat(),
        },
        headers=owner,
    )

    assert response.status_code == 422, response.text


async def test_compliance_dates_are_enforced_for_direct_database_writes(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="ORPHAN-DB")
    async with get_sessionmaker()() as db:
        db.add(
            HealthEvent(
                farm_id=int(owner["X-Farm-Id"]),
                animal_id=animal["id"],
                date=today(),
                type="TREATMENT",
                suspected_scheduled_disease=False,
                authority_notified_at=today(),
            )
        )
        with pytest.raises(
            IntegrityError,
            match="ck_health_events_compliance_requires_suspicion",
        ):
            await db.commit()


def test_health_template_matching_uses_words_and_requires_both_combined_components() -> None:
    assert not target_matches_template("diet review", "Enterotoxaemia (ET)")
    assert not target_matches_template("ET vaccine", "ET + TT pre-kidding")
    assert not target_matches_template("Tetanus toxoid", "ET + TT pre-kidding")
    assert target_matches_template("ET + TT vaccine", "ET + TT pre-kidding")
    assert target_matches_template("Enterotoxaemia and tetanus", "ET + TT pre-kidding")

    assert template_name_for_task("Diet review plus tetanus", "VACCINE") is None
    assert template_name_for_task("Day 20: vaccinate ET + Tetanus", "VACCINE") == (
        "Enterotoxaemia (ET)"
    )
    assert template_name_for_task("Pre-kidding ET+TT vaccine", "VACCINE") == ("ET + TT pre-kidding")


def test_legacy_schedule_matching_rejects_incompatible_event_types() -> None:
    def matches(
        template_name: str,
        event_type: str,
        *,
        schedule_template_name: str | None = None,
        product_name: str | None = None,
    ) -> bool:
        return _legacy_event_matches(
            template_name,
            event_type=event_type,
            schedule_template_name=schedule_template_name,
            product_name=product_name,
            disease_target=None,
        )

    # Exact legacy labels are not enough when the recorded clinical action is
    # from the other programme family.
    assert not matches("PPR", "DEWORMING", schedule_template_name="PPR")
    assert not matches("Deworming", "VACCINE", schedule_template_name="Deworming")
    # Nor may free text or a known trade-name alias override the event type.
    assert not matches("PPR", "DEWORMING", product_name="Raksha-PPR")
    assert not matches("Deworming", "VACCINE", product_name="routine deworming treatment")

    assert matches("PPR", "VACCINE", schedule_template_name="PPR")
    assert matches("PPR", "VACCINE", product_name="Raksha-PPR")
    assert matches("Deworming", "DEWORMING", product_name="routine treatment")


def test_template_inference_ignores_operator_supplied_labels() -> None:
    """Only the protocol phrase decides the programme item: the quarantine
    supplier prefix and the pre-kidding tag suffix are display text."""
    assert protocol_phrase_of("[PPR Traders #1] Day 20: vaccinate ET + Tetanus (toxoid, SC)") == (
        "Day 20: vaccinate ET + Tetanus (toxoid, SC)"
    )
    # A supplier may itself contain "]"; the generated prefix ends at the last.
    assert protocol_phrase_of("[A] Day 10: PPR #1] Day 40: vaccinate FMD (killed, SC)") == (
        "Day 40: vaccinate FMD (killed, SC)"
    )
    assert protocol_phrase_of("Pre-kidding ET+TT vaccine: PPR-01") == "Pre-kidding ET+TT vaccine"

    for supplier in ("PPR Traders", "Goat Pox Agro", "FMD Exports"):
        title = f"[{supplier} #7] Day 20: vaccinate ET + Tetanus (toxoid, SC)"
        assert template_name_for_task(title, "VACCINE") == "Enterotoxaemia (ET)"
        pox = f"[{supplier} #7] Day 30: vaccinate Goat Pox (live viral, SC)"
        assert template_name_for_task(pox, "VACCINE") == "Goat Pox"
    assert template_name_for_task("Pre-kidding ET+TT vaccine: PPR-01", "VACCINE") == (
        "ET + TT pre-kidding"
    )


def test_template_abbreviation_matches_its_own_target() -> None:
    """The read/inference path accepts the advertised abbreviation, so the
    stricter write path must not reject the more precise entry."""
    assert target_matches_template("HS", "Haemorrhagic Septicaemia (HS)")
    assert target_matches_template("Haemorrhagic Septicaemia", "Haemorrhagic Septicaemia (HS)")
    assert not target_matches_template("Anthrax", "Haemorrhagic Septicaemia (HS)")


async def test_linked_duty_without_a_target_cannot_be_closed(client: httpx.AsyncClient) -> None:
    """A generated VACCINE duty carrying neither an animal nor a batch has no
    scope to validate against, so it must never close from a health record."""
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="NO-TARGET")
    async with get_sessionmaker()() as db:
        orphan = Task(
            farm_id=int(owner["X-Farm-Id"]),
            title="Day 10: vaccinate PPR (live viral, SC)",
            due_date=today(),
            status="PENDING",
            category="VACCINE",
            auto_generated=True,
        )
        db.add(orphan)
        await db.commit()
        task_id = orphan.id

    response = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": animal["id"],
            "type": "VACCINE",
            "disease_target": "PPR",
            "task_id": task_id,
        },
        headers=owner,
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Linked health task has no supported target"
    async with get_sessionmaker()() as db:
        stored = await db.get(Task, task_id)
        assert stored is not None and stored.status == "PENDING"


async def test_a_new_suspicion_episode_restates_its_own_authority_notification(
    client: httpx.AsyncClient,
) -> None:
    """place_movement_restriction opens a NEW episode, so the animal-level
    restriction columns describe the current concern only. Carrying a previous
    notification date forward would falsely assert the authority was told about
    the new suspicion; the fact itself stays on the event that recorded it."""
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="NOTIFY-1")
    notified_on = today() - timedelta(days=3)
    await record_event(
        client,
        owner,
        scope="animal",
        animal_id=animal["id"],
        type="TREATMENT",
        disease_target="Anthrax",
        suspected_scheduled_disease=True,
        authority_notified_at=notified_on.isoformat(),
    )
    profile = await client.get(f"/api/animals/{animal['id']}", headers=owner)
    assert profile.json()["animal"]["authority_notified_at"] == notified_on.isoformat()

    await record_event(
        client,
        owner,
        scope="animal",
        animal_id=animal["id"],
        type="TREATMENT",
        disease_target="Anthrax",
        suspected_scheduled_disease=True,
    )
    profile = await client.get(f"/api/animals/{animal['id']}", headers=owner)
    assert profile.json()["animal"]["restriction_version"] == 2
    assert profile.json()["animal"]["authority_notified_at"] is None
    ledger = await client.get("/api/health/events", headers=owner)
    assert ledger.status_code == 200, ledger.text
    assert notified_on.isoformat() in {
        event["authority_notified_at"] for event in ledger.json()["events"]
    }


async def test_manual_diet_task_does_not_gain_an_et_template_by_substring(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    batch = await make_batch(
        client,
        owner,
        count=1,
        date=iso(today() - timedelta(days=21)),
        create_animals=True,
    )
    detail = await client.get(f"/api/purchases/{batch['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    task = next(
        row
        for row in detail.json()["tasks"]
        if row["category"] == "VACCINE" and row["due_date"] <= iso(today())
    )
    # Manual workflow categories are intentionally forbidden.  Model a
    # legacy/system-generated duty whose free-text title contains "diet" so
    # the route-level provenance/template matching remains covered.
    async with get_sessionmaker()() as db:
        task_row = await db.get(Task, task["id"])
        assert task_row is not None
        task_row.title = "Diet review plus tetanus note"
        await db.commit()
    event = await record_event(
        client,
        owner,
        scope="batch",
        purchase_batch_id=batch["id"],
        type="VACCINE",
        task_id=task["id"],
    )
    assert event[0]["schedule_template_name"] is None


async def test_health_cost_and_audit_counts_are_one_per_actual_mutation(
    client: httpx.AsyncClient,
) -> None:
    """A local sanity check used by the keyed concurrency test as well."""
    owner = await owner_with_farm(client)
    animals = [
        await make_animal(client, owner, tag="COUNT-1"),
        await make_animal(client, owner, tag="COUNT-2"),
    ]
    reviewed = await preview(client, owner, scope="bucket", bucket="FOUNDATION")
    response = await client.post(
        "/api/health/events",
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "expected_animal_ids": reviewed.json()["target_animal_ids"],
            "type": "TREATMENT",
            "cost": 25,
            "disease_target": "Count concern",
            "suspected_scheduled_disease": True,
        },
        headers=owner,
    )
    assert response.status_code == 201, response.text
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(func.count(HealthEvent.id)))).scalar_one() == len(animals)
        assert (
            await db.execute(select(func.count(MovementRestrictionAction.id)))
        ).scalar_one() == len(animals)
        assert (await db.execute(select(func.count(Transaction.id)))).scalar_one() == 1


async def test_clearing_a_mortality_hold_keeps_the_authority_notification_date(
    client: httpx.AsyncClient,
) -> None:
    """The mortality path writes no HealthEvent, so the animal column is the
    only copy of a statutorily mandated notification date.

    ``clear_movement_restriction`` justified nulling it by saying the fact
    "stays on the HealthEvent that recorded the suspicion" — true for
    ``record_health_event``, false for ``POST /api/animals/{id}/status`` with
    ``suspected_scheduled_disease``, which places the hold with
    ``health_event_id=None``. There the clearance erased the date for good.
    """
    headers = await owner_with_farm(client)
    created = await client.post(
        "/api/animals",
        json={
            "tag_number": "NOTIFY-1",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "BREEDING",
            "date_of_birth": (today() - timedelta(days=700)).isoformat(),
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    animal_id = created.json()["id"]

    notified_on = today()
    dead = await client.post(
        f"/api/animals/{animal_id}/status",
        json={
            "new_status": "DEAD",
            "mortality_cause": "Sudden death",
            "suspected_scheduled_disease": True,
            "suspected_disease": "Anthrax",
            "authority_notified_at": notified_on.isoformat(),
        },
        headers=headers,
    )
    assert dead.status_code == 200, dead.text
    assert dead.json()["authority_notified_at"] == notified_on.isoformat()
    version = dead.json()["restriction_version"]

    cleared = await client.post(
        f"/api/health/restrictions/{animal_id}/clear",
        json={"clearance_reference": "AHD-2026-9", "expected_restriction_version": version},
        headers=headers,
    )
    assert cleared.status_code in (200, 204), cleared.text

    after = await client.get(f"/api/animals/{animal_id}", headers=headers)
    assert after.status_code == 200, after.text
    # GET /api/animals/{id} returns the profile, with the animal nested.
    body = after.json()["animal"]
    # The hold itself is closed...
    assert body["movement_restricted"] is False
    assert body["suspected_scheduled_disease"] is False
    assert body["suspected_disease"] is None
    # ...but the notification date, whose only copy this is, survives.
    assert body["authority_notified_at"] == notified_on.isoformat()
