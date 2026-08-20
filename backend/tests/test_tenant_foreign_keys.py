"""Database-enforced farm ownership for tenant-scoped relationships."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.db import get_sessionmaker
from app.models import (
    Animal,
    BreedingRecord,
    FarmMembership,
    HealthEvent,
    KiddingRecord,
    KidEntry,
    PurchaseBatch,
    Role,
    Task,
    Transaction,
    User,
)
from app.utils import today

from .conftest import create_farm, owner_with_farm


@dataclass(frozen=True)
class Mutation:
    table: str
    row_id: int
    column: str
    same_farm_value: int
    other_farm_value: int
    constraint: str
    accepted_early_denials: tuple[str, ...] = ()


async def _build_two_farm_graphs(client: httpx.AsyncClient) -> list[Mutation]:
    owner = await owner_with_farm(client, email="tenant-fk-owner@farm.in")
    other = await create_farm(
        client,
        {"Authorization": owner["Authorization"]},
        name="Other Tenant Farm",
    )
    farm_one = int(owner["X-Farm-Id"])
    farm_two = int(other["X-Farm-Id"])

    async with get_sessionmaker()() as db:
        role_one = (
            await db.execute(
                select(Role).where(Role.farm_id == farm_one).order_by(Role.id).limit(1)
            )
        ).scalar_one()
        role_two = (
            await db.execute(
                select(Role).where(Role.farm_id == farm_two).order_by(Role.id).limit(1)
            )
        ).scalar_one()

        worker_one = User(
            email="tenant-fk-worker-one@farm.in",
            name="Worker One",
            password_hash="not-used-by-this-database-test",
        )
        worker_two = User(
            email="tenant-fk-worker-two@farm.in",
            name="Worker Two",
            password_hash="not-used-by-this-database-test",
        )
        db.add_all([worker_one, worker_two])
        await db.flush()
        membership_one = FarmMembership(
            user_id=worker_one.id,
            farm_id=farm_one,
            role_id=role_one.id,
            is_active=True,
            account_provisioned_by_farm=True,
        )
        membership_two = FarmMembership(
            user_id=worker_two.id,
            farm_id=farm_two,
            role_id=role_two.id,
            is_active=True,
            account_provisioned_by_farm=True,
        )
        db.add_all([membership_one, membership_two])

        batch_one = PurchaseBatch(
            farm_id=farm_one,
            date=today(),
            supplier="Farm One Supplier",
            count=1,
        )
        batch_two = PurchaseBatch(
            farm_id=farm_two,
            date=today(),
            supplier="Farm Two Supplier",
            count=1,
        )
        db.add_all([batch_one, batch_two])
        await db.flush()

        doe_one = Animal(
            farm_id=farm_one,
            tag_number="TENANT-DOE-1",
            sex="F",
            source="PURCHASED",
            purchase_batch_id=batch_one.id,
            current_bucket="BREEDING",
        )
        buck_one = Animal(
            farm_id=farm_one,
            tag_number="TENANT-BUCK-1",
            sex="M",
            source="PURCHASED",
            current_bucket="BREEDING",
        )
        doe_two = Animal(
            farm_id=farm_two,
            tag_number="TENANT-DOE-2",
            sex="F",
            source="PURCHASED",
            purchase_batch_id=batch_two.id,
            current_bucket="BREEDING",
        )
        buck_two = Animal(
            farm_id=farm_two,
            tag_number="TENANT-BUCK-2",
            sex="M",
            source="PURCHASED",
            current_bucket="BREEDING",
        )
        db.add_all([doe_one, buck_one, doe_two, buck_two])
        await db.flush()
        kid_animal_one = Animal(
            farm_id=farm_one,
            tag_number="TENANT-KID-1",
            sex="F",
            source="BORN",
            date_of_birth=today(),
            dam_id=doe_one.id,
            sire_id=buck_one.id,
            current_bucket="RECOVERY",
        )
        kid_animal_two = Animal(
            farm_id=farm_two,
            tag_number="TENANT-KID-2",
            sex="F",
            source="BORN",
            date_of_birth=today(),
            dam_id=doe_two.id,
            sire_id=buck_two.id,
            current_bucket="RECOVERY",
        )
        db.add_all([kid_animal_one, kid_animal_two])
        await db.flush()

        breeding_one = BreedingRecord(
            farm_id=farm_one,
            doe_id=doe_one.id,
            buck_id=buck_one.id,
            breeding_date=today() - timedelta(days=150),
            ultrasound_done=True,
            pregnant=True,
            expected_kidding_date=today(),
            outcome="CONFIRMED_PREGNANT",
        )
        breeding_two = BreedingRecord(
            farm_id=farm_two,
            doe_id=doe_two.id,
            buck_id=buck_two.id,
            breeding_date=today() - timedelta(days=150),
            ultrasound_done=True,
            pregnant=True,
            expected_kidding_date=today(),
            outcome="CONFIRMED_PREGNANT",
        )
        breeding_two_unlinked = BreedingRecord(
            farm_id=farm_two,
            doe_id=doe_two.id,
            buck_id=buck_two.id,
            breeding_date=today() - timedelta(days=200),
            ultrasound_done=True,
            pregnant=False,
            outcome="FAILED",
        )
        db.add_all([breeding_one, breeding_two, breeding_two_unlinked])
        await db.flush()
        kidding_one = KiddingRecord(
            farm_id=farm_one,
            doe_id=doe_one.id,
            date=today(),
            breeding_record_id=breeding_one.id,
        )
        kidding_two = KiddingRecord(
            farm_id=farm_two,
            doe_id=doe_two.id,
            date=today(),
            breeding_record_id=breeding_two.id,
        )
        db.add_all([kidding_one, kidding_two])
        await db.flush()

        kid_entry_one = KidEntry(
            farm_id=farm_one,
            kidding_record_id=kidding_one.id,
            tag=kid_animal_one.tag_number,
            sex=kid_animal_one.sex,
            status="ALIVE",
            animal_id=kid_animal_one.id,
        )
        kid_entry_two = KidEntry(
            farm_id=farm_two,
            kidding_record_id=kidding_two.id,
            tag=kid_animal_two.tag_number,
            sex=kid_animal_two.sex,
            status="ALIVE",
            animal_id=kid_animal_two.id,
        )
        health_one = HealthEvent(
            farm_id=farm_one,
            animal_id=doe_one.id,
            purchase_batch_id=batch_one.id,
            date=today(),
            type="TREATMENT",
        )
        health_two = HealthEvent(
            farm_id=farm_two,
            animal_id=doe_two.id,
            purchase_batch_id=batch_two.id,
            date=today(),
            type="TREATMENT",
        )
        task_one = Task(
            farm_id=farm_one,
            title="Tenant-safe task one",
            due_date=today(),
            category="OTHER",
            animal_id=doe_one.id,
            purchase_batch_id=batch_one.id,
            breeding_record_id=breeding_one.id,
            assigned_role_id=role_one.id,
            assigned_user_id=worker_one.id,
        )
        task_two = Task(
            farm_id=farm_two,
            title="Tenant-safe task two",
            due_date=today(),
            category="OTHER",
            animal_id=doe_two.id,
            purchase_batch_id=batch_two.id,
            breeding_record_id=breeding_two.id,
            assigned_role_id=role_two.id,
            assigned_user_id=worker_two.id,
        )
        original_one = Transaction(
            farm_id=farm_one,
            date=today(),
            type="EXPENSE",
            category="OTHER",
            amount=Decimal("10.00"),
            related_animal_id=doe_one.id,
        )
        original_two = Transaction(
            farm_id=farm_two,
            date=today(),
            type="EXPENSE",
            category="OTHER",
            amount=Decimal("20.00"),
            related_animal_id=doe_two.id,
        )
        db.add_all(
            [
                kid_entry_one,
                kid_entry_two,
                health_one,
                health_two,
                task_one,
                task_two,
                original_one,
                original_two,
            ]
        )
        await db.flush()
        correction_one = Transaction(
            farm_id=farm_one,
            date=today(),
            type="EXPENSE",
            category="OTHER",
            amount=Decimal("9.00"),
            correction_of_id=original_one.id,
        )
        db.add(correction_one)
        await db.commit()

        return [
            Mutation(
                "farm_memberships",
                membership_one.id,
                "role_id",
                role_one.id,
                role_two.id,
                "fk_farm_memberships_farm_role",
            ),
            Mutation(
                "animals",
                doe_one.id,
                "purchase_batch_id",
                batch_one.id,
                batch_two.id,
                "fk_animals_farm_purchase_batch",
            ),
            Mutation(
                "animals",
                kid_animal_one.id,
                "dam_id",
                doe_one.id,
                doe_two.id,
                "fk_animals_farm_dam",
            ),
            Mutation(
                "animals",
                kid_animal_one.id,
                "sire_id",
                buck_one.id,
                buck_two.id,
                "fk_animals_farm_sire",
            ),
            Mutation(
                "health_events",
                health_one.id,
                "animal_id",
                doe_one.id,
                doe_two.id,
                "fk_health_events_farm_animal",
            ),
            Mutation(
                "health_events",
                health_one.id,
                "purchase_batch_id",
                batch_one.id,
                batch_two.id,
                "fk_health_events_farm_purchase_batch",
            ),
            Mutation(
                "breeding_records",
                breeding_one.id,
                "doe_id",
                doe_one.id,
                doe_two.id,
                "fk_breeding_records_farm_doe",
                (
                    "ck_breeding_relationship_immutable",
                    "breeding farm, doe and buck relationship are immutable",
                ),
            ),
            Mutation(
                "breeding_records",
                breeding_one.id,
                "buck_id",
                buck_one.id,
                buck_two.id,
                "fk_breeding_records_farm_buck",
                (
                    "ck_breeding_relationship_immutable",
                    "breeding farm, doe and buck relationship are immutable",
                ),
            ),
            Mutation(
                "kidding_records",
                kidding_one.id,
                "doe_id",
                doe_one.id,
                doe_two.id,
                "fk_kidding_records_farm_doe",
                (
                    "ck_kidding_relationship_immutable",
                    "kidding farm, doe and breeding relationship are immutable",
                ),
            ),
            Mutation(
                "kidding_records",
                kidding_one.id,
                "breeding_record_id",
                breeding_one.id,
                breeding_two_unlinked.id,
                "fk_kidding_records_farm_breeding_record",
                (
                    "ck_kidding_relationship_immutable",
                    "kidding farm, doe and breeding relationship are immutable",
                ),
            ),
            Mutation(
                "tasks",
                task_one.id,
                "animal_id",
                doe_one.id,
                doe_two.id,
                "fk_tasks_farm_animal",
            ),
            Mutation(
                "tasks",
                task_one.id,
                "purchase_batch_id",
                batch_one.id,
                batch_two.id,
                "fk_tasks_farm_purchase_batch",
            ),
            Mutation(
                "tasks",
                task_one.id,
                "breeding_record_id",
                breeding_one.id,
                breeding_two.id,
                "fk_tasks_farm_breeding_record",
            ),
            Mutation(
                "tasks",
                task_one.id,
                "assigned_role_id",
                role_one.id,
                role_two.id,
                "fk_tasks_farm_assigned_role",
            ),
            Mutation(
                "tasks",
                task_one.id,
                "assigned_user_id",
                worker_one.id,
                worker_two.id,
                "fk_tasks_farm_assigned_membership",
            ),
            Mutation(
                "transactions",
                original_one.id,
                "related_animal_id",
                doe_one.id,
                doe_two.id,
                "fk_transactions_farm_related_animal",
            ),
            Mutation(
                "transactions",
                correction_one.id,
                "correction_of_id",
                original_one.id,
                original_two.id,
                "fk_transactions_farm_correction",
            ),
            Mutation(
                "kid_entries",
                kid_entry_one.id,
                "kidding_record_id",
                kidding_one.id,
                kidding_two.id,
                "fk_kid_entries_farm_kidding_record",
            ),
            Mutation(
                "kid_entries",
                kid_entry_one.id,
                "animal_id",
                kid_animal_one.id,
                kid_animal_two.id,
                "fk_kid_entries_farm_animal",
            ),
        ]


async def test_direct_sql_accepts_same_farm_and_rejects_every_cross_farm_link(
    client: httpx.AsyncClient,
) -> None:
    mutations = await _build_two_farm_graphs(client)

    # All same-farm relationships remain legal through direct SQL.
    async with get_sessionmaker()() as db:
        for mutation in mutations:
            await db.execute(
                text(
                    f'UPDATE "{mutation.table}" SET "{mutation.column}" = :value WHERE id = :row_id'
                ),
                {"value": mutation.same_farm_value, "row_id": mutation.row_id},
            )
        await db.commit()

    # The identical direct-SQL writes aimed at another tenant are rejected by
    # the named PostgreSQL constraints, independently of API/service guards.
    for mutation in mutations:
        async with get_sessionmaker()() as db:
            with pytest.raises(IntegrityError) as caught:
                await db.execute(
                    text(
                        f'UPDATE "{mutation.table}" SET "{mutation.column}" = :value '
                        "WHERE id = :row_id"
                    ),
                    {"value": mutation.other_farm_value, "row_id": mutation.row_id},
                )
            await db.rollback()
        accepted_denials = (mutation.constraint, *mutation.accepted_early_denials)
        assert any(denial in str(caught.value) for denial in accepted_denials)


async def test_task_assignment_requires_a_membership_even_for_existing_user(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="membership-fk-owner@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        role_id = (
            (await db.execute(select(Role.id).where(Role.farm_id == farm_id).order_by(Role.id)))
            .scalars()
            .first()
        )
        assert role_id is not None
        unaffiliated = User(
            email="unaffiliated-task-user@farm.in",
            password_hash="not-used-by-this-database-test",
        )
        db.add(unaffiliated)
        await db.flush()
        with pytest.raises(IntegrityError) as caught:
            await db.execute(
                text(
                    "INSERT INTO tasks "
                    "(farm_id, title, due_date, status, category, auto_generated, "
                    "assigned_role_id, assigned_user_id) "
                    "VALUES (:farm_id, 'Invalid assignment', :due_date, 'PENDING', "
                    "'OTHER', false, :role_id, :user_id)"
                ),
                {
                    "farm_id": farm_id,
                    "due_date": today(),
                    "role_id": role_id,
                    "user_id": unaffiliated.id,
                },
            )
        await db.rollback()
    assert "fk_tasks_farm_assigned_membership" in str(caught.value)


async def test_composite_lineage_guards_preserve_single_fk_set_null(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="lineage-delete-owner@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        dam = Animal(
            farm_id=farm_id,
            tag_number="DELETE-DAM",
            sex="F",
            source="PURCHASED",
            current_bucket="BREEDING",
        )
        sire = Animal(
            farm_id=farm_id,
            tag_number="DELETE-SIRE",
            sex="M",
            source="PURCHASED",
            current_bucket="BREEDING",
        )
        db.add_all([dam, sire])
        await db.flush()
        child = Animal(
            farm_id=farm_id,
            tag_number="SURVIVING-CHILD",
            sex="F",
            source="BORN",
            date_of_birth=today(),
            dam_id=dam.id,
            sire_id=sire.id,
            current_bucket="RECOVERY",
        )
        db.add(child)
        await db.commit()
        child_id, dam_id, sire_id = child.id, dam.id, sire.id

    async with get_sessionmaker()() as db:
        await db.execute(
            text("DELETE FROM animals WHERE id IN (:dam_id, :sire_id)"),
            {"dam_id": dam_id, "sire_id": sire_id},
        )
        await db.commit()
        surviving = await db.get(Animal, child_id)
    assert surviving is not None
    assert surviving.dam_id is None
    assert surviving.sire_id is None
    assert surviving.farm_id == farm_id
