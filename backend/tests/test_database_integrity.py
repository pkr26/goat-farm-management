"""Database defaults, immutability, tenant guards, and screening state integrity."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db import get_sessionmaker
from app.models import (
    BucketDefinition,
    Farm,
    FarmMembership,
    FarmOwnerHistory,
    ScreeningBatch,
    ScreeningImage,
    ScreeningRun,
    User,
)

from .conftest import OWNER_PW, login_and_rotate, owner_with_farm


async def _must_reject(statement: Any) -> None:
    async with get_sessionmaker()() as db:
        with pytest.raises(IntegrityError):
            async with db.begin():
                await db.execute(statement)


async def test_screening_state_checks_reject_contradictory_raw_sql(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="db-state-hardening@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    base = {
        "farm_id": farm_id,
        "bucket": "BREEDING",
        "s3_bucket": "test-bucket",
        "status": "HEALTHY",
    }
    await _must_reject(
        insert(ScreeningImage).values(
            **base,
            s3_key="raw/contradictory-error.jpg",
            error="healthy rows cannot carry errors",
        )
    )
    await _must_reject(
        insert(ScreeningImage).values(
            **base,
            s3_key="raw/contradictory-dimensions.jpg",
            width=-1,
            height=None,
        )
    )
    await _must_reject(
        insert(ScreeningImage).values(
            **base,
            s3_key="raw/half-known-dimensions-width.jpg",
            width=640,
            height=None,
        )
    )
    await _must_reject(
        insert(ScreeningImage).values(
            **base,
            s3_key="raw/half-known-dimensions-height.jpg",
            width=None,
            height=480,
        )
    )

    async with get_sessionmaker()() as db:
        await db.execute(
            insert(ScreeningImage).values(
                {
                    **base,
                    "s3_key": "raw/valid-skipped-reason.jpg",
                    "status": "SKIPPED",
                    "error": "duplicate normalized content",
                }
            )
        )
        image = ScreeningImage(
            farm_id=farm_id,
            bucket="BREEDING",
            s3_bucket="test-bucket",
            status="HEALTHY",
            s3_key="raw/valid-for-run.jpg",
            width=640,
            height=480,
        )
        db.add(image)
        await db.flush()
        image_id = image.id
        await db.commit()
    await _must_reject(
        insert(ScreeningRun).values(
            farm_id=farm_id,
            image_id=image_id,
            stage="GATE",
            run_status="ERROR",
            verdict="invented",
            error=None,
            provider="fake",
            model="fake",
            prompt_version="v1",
            latency_ms=10,
        )
    )
    await _must_reject(
        insert(ScreeningRun).values(
            farm_id=farm_id,
            image_id=image_id,
            stage="GATE",
            run_status="OK",
            verdict=None,
            error=None,
            provider="fake",
            model="fake",
            prompt_version="v1",
            latency_ms=10,
        )
    )
    await _must_reject(
        insert(ScreeningRun).values(
            farm_id=farm_id,
            image_id=image_id,
            stage="SPECIALIST_SKIN",
            run_status="OK",
            verdict="healthy",
            error=None,
            provider="fake",
            model="fake",
            prompt_version="v1",
            latency_ms=10,
        )
    )

    async with get_sessionmaker()() as db:
        await db.execute(
            insert(ScreeningRun).values(
                farm_id=farm_id,
                image_id=image_id,
                stage="DETECT",
                run_status="OK",
                verdict=None,
                error=None,
                provider="fake",
                model="fake",
                prompt_version="v1",
                latency_ms=10,
            )
        )
        await db.execute(
            insert(ScreeningRun).values(
                farm_id=farm_id,
                image_id=image_id,
                stage="SPECIALIST_SKIN",
                run_status="OK",
                verdict=None,
                error=None,
                provider="fake",
                model="fake",
                prompt_version="v1",
                latency_ms=10,
            )
        )
        await db.commit()


async def test_actor_attribution_rejects_cross_farm_but_keeps_history(
    client: httpx.AsyncClient,
) -> None:
    owner_a = await owner_with_farm(client, email="db-actor-a@farm.in", farm_name="Actor A")
    owner_b = await owner_with_farm(client, email="db-actor-b@farm.in", farm_name="Actor B")
    farm_a = int(owner_a["X-Farm-Id"])
    farm_b = int(owner_b["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        owner_a_id = await db.scalar(select(Farm.owner_id).where(Farm.id == farm_a))
        owner_b_id = await db.scalar(select(Farm.owner_id).where(Farm.id == farm_b))
        owner_batch_id = await db.scalar(
            insert(ScreeningBatch)
            .values(farm_id=farm_a, created_by_id=owner_a_id)
            .returning(ScreeningBatch.id)
        )
        await db.commit()
    assert owner_a_id is not None
    assert owner_b_id is not None
    assert owner_batch_id is not None
    await _must_reject(insert(ScreeningBatch).values(farm_id=farm_a, created_by_id=owner_b_id))

    role = await client.post(
        "/api/team/roles",
        json={"name": "Historical actor", "permissions": ["dashboard.view"]},
        headers=owner_a,
    )
    assert role.status_code == 201, role.text
    worker = await client.post(
        "/api/team/workers",
        json={
            "name": "Historical Worker",
            "email": "db-historical-actor@farm.in",
            "password": "worker-pass-123",
            "role_id": role.json()["id"],
        },
        headers=owner_a,
    )
    assert worker.status_code == 201, worker.text
    async with get_sessionmaker()() as db:
        membership = await db.get(FarmMembership, worker.json()["id"])
        assert membership is not None
        actor_id = membership.user_id
        await db.execute(
            update(FarmMembership).where(FarmMembership.id == membership.id).values(is_active=False)
        )
        await db.execute(insert(ScreeningBatch).values(farm_id=farm_a, created_by_id=actor_id))
        await db.commit()

    successor = await client.post(
        "/api/team/workers",
        json={
            "name": "Successor Owner",
            "email": "db-successor-owner@farm.in",
            "password": "successor-pass-123",
            "role_id": role.json()["id"],
        },
        headers=owner_a,
    )
    assert successor.status_code == 201, successor.text
    await login_and_rotate(client, "db-successor-owner@farm.in", "successor-pass-123")
    transferred = await client.post(
        f"/api/auth/farms/{farm_a}/transfer-ownership",
        json={"membership_id": successor.json()["id"], "current_password": OWNER_PW},
        headers=owner_a,
    )
    assert transferred.status_code == 200, transferred.text

    # The old owner is no longer Farm.owner_id and was never a membership,
    # but pre-transfer evidence remains editable in its watched columns. The
    # immutable owner-history pair is the retained affiliation proof.
    async with get_sessionmaker()() as db:
        await db.execute(
            update(ScreeningBatch)
            .where(ScreeningBatch.id == owner_batch_id)
            .values(created_by_id=owner_a_id)
        )
        history = await db.get(FarmOwnerHistory, (farm_a, owner_a_id))
        assert history is not None
        await db.commit()

    # The immutable history pair proves the old row's attribution; it is not
    # standing authority to fabricate brand-new evidence after the transfer.
    await _must_reject(insert(ScreeningBatch).values(farm_id=farm_a, created_by_id=owner_a_id))

    # Retaining legitimate former owners must not create a generic bypass:
    # unrelated users cannot fabricate an anchor or attribution after transfer.
    await _must_reject(insert(FarmOwnerHistory).values(farm_id=farm_a, user_id=owner_b_id))
    await _must_reject(insert(ScreeningBatch).values(farm_id=farm_a, created_by_id=owner_b_id))
    async with get_sessionmaker()() as db:
        # The append-only trigger uses PostgreSQL 55000 (object not in
        # prerequisite state), which SQLAlchemy correctly exposes as a
        # DBAPIError rather than a class-23 integrity exception.
        with pytest.raises(DBAPIError):
            await db.execute(
                update(FarmOwnerHistory)
                .where(
                    FarmOwnerHistory.farm_id == farm_a,
                    FarmOwnerHistory.user_id == owner_a_id,
                )
                .values(acquired_at=None)
            )

    # Transfer is the documented prerequisite for account deletion. The
    # former owner can tombstone their identity without erasing the retained
    # farm-affiliation anchor or historical actor rows.
    deleted = await client.request(
        "DELETE",
        "/api/auth/account",
        json={"current_password": OWNER_PW},
        headers=owner_a,
    )
    assert deleted.status_code == 204, deleted.text
    async with get_sessionmaker()() as db:
        history = await db.get(FarmOwnerHistory, (farm_a, owner_a_id))
        former_owner = await db.get(User, owner_a_id)
        assert history is not None
        assert former_owner is not None and former_owner.deleted_at is not None
        with pytest.raises(DBAPIError):
            await db.execute(
                delete(FarmOwnerHistory).where(
                    FarmOwnerHistory.farm_id == farm_a,
                    FarmOwnerHistory.user_id == owner_a_id,
                )
            )


async def test_seeded_breeding_guidance_was_guardedly_corrected() -> None:
    async with get_sessionmaker()() as db:
        rows = {
            row.code: row
            for row in (
                await db.execute(
                    select(BucketDefinition).where(
                        BucketDefinition.code.in_(("FOUNDATION", "FEMALE_KIDS"))
                    )
                )
            ).scalars()
        }
    assert rows["FOUNDATION"].who == "Purchased doelings 6–7 mo; own female kids 2–11 mo"
    assert rows["FEMALE_KIDS"].who == "Female kids 2–11 months; 60/40 mix steady"
    assert rows["FOUNDATION"].exit_rule == "Breeding-ready (≥12 mo, ≥22 kg) → BREEDING"
    assert rows["FEMALE_KIDS"].exit_rule == "Breeding-ready (≥12 mo, ≥22 kg) → BREEDING"
