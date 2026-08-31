"""Milk production recording (dairy farms).

One reading per animal, business date and milking shift. Re-submitting the
same milking replaces the reading (upsert under the animal's row lock) so the
parlour can correct a fat test or a mis-keyed yield without double-counting.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Animal, AnimalStatus, Farm, MilkRecord
from ..utils import today


def _litres_quantum(value: float) -> float:
    """Round to the column's 0.001-litre precision."""
    return round(float(value), 3)


async def record_milk(
    db: AsyncSession,
    farm: Farm,
    animal: Animal,
    record_date: date,
    shift: str,
    litres: float,
    fat_pct: float | None,
    notes: str | None,
    created_by_id: int | None = None,
) -> MilkRecord:
    """Insert or replace the (animal, date, shift) reading.

    The animal row is already locked by the caller (the API loads it FOR
    UPDATE while verifying it belongs to this farm), which serializes two
    parlour submits racing on the same milking.
    """
    existing = (
        await db.execute(
            select(MilkRecord).where(
                MilkRecord.animal_id == animal.id,
                MilkRecord.date == record_date,
                MilkRecord.shift == shift,
            )
        )
    ).scalar_one_or_none()
    clean_notes = (notes or "").strip() or None
    if existing is not None:
        existing.litres = _litres_quantum(litres)
        existing.fat_pct = fat_pct
        existing.notes = clean_notes
        existing.created_by_id = created_by_id
        await db.flush()
        return existing
    record = MilkRecord(
        farm_id=farm.id,
        animal_id=animal.id,
        date=record_date,
        shift=shift,
        litres=_litres_quantum(litres),
        fat_pct=fat_pct,
        notes=clean_notes,
        created_by_id=created_by_id,
    )
    db.add(record)
    await db.flush()
    return record


async def list_milk_records(
    db: AsyncSession,
    farm: Farm,
    *,
    animal_id: int | None,
    shift: str | None,
    date_from: date | None,
    date_to: date | None,
    limit: int,
    offset: int,
) -> tuple[list[tuple[MilkRecord, str | None]], int, float]:
    """One bounded page plus the full-set count and litre total."""
    filters = [
        MilkRecord.farm_id == farm.id,
        Animal.farm_id == farm.id,
        Animal.id == MilkRecord.animal_id,
    ]
    if animal_id is not None:
        filters.append(MilkRecord.animal_id == animal_id)
    if shift is not None:
        filters.append(MilkRecord.shift == shift)
    if date_from is not None:
        filters.append(MilkRecord.date >= date_from)
    if date_to is not None:
        filters.append(MilkRecord.date <= date_to)
    totals = (
        await db.execute(
            select(
                func.count(MilkRecord.id),
                func.coalesce(func.sum(MilkRecord.litres), 0.0),
            ).where(*filters)
        )
    ).one()
    rows = (
        await db.execute(
            select(MilkRecord, Animal.tag_number)
            .where(*filters)
            .order_by(MilkRecord.date.desc(), MilkRecord.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return [(record, tag) for record, tag in rows], int(totals[0]), float(totals[1])


async def milk_summary(
    db: AsyncSession,
    farm: Farm,
    *,
    days: int,
    animal_id: int | None,
) -> tuple[list[Any], list[Any], float, float | None]:
    """Herd daily totals and per-animal averages over the last N days."""
    reference = today(farm.timezone)
    window_start = reference - timedelta(days=days - 1)
    filters = [
        MilkRecord.farm_id == farm.id,
        MilkRecord.date >= window_start,
        MilkRecord.date <= reference,
    ]
    if animal_id is not None:
        filters.append(MilkRecord.animal_id == animal_id)
    daily = (
        await db.execute(
            select(
                MilkRecord.date.label("date"),
                func.sum(MilkRecord.litres).label("litres"),
                func.count(func.distinct(MilkRecord.animal_id)).label("recorded_animals"),
                func.avg(MilkRecord.fat_pct).label("avg_fat_pct"),
            )
            .where(*filters)
            .group_by(MilkRecord.date)
            .order_by(MilkRecord.date.desc())
        )
    ).all()
    animal_filters = [
        MilkRecord.farm_id == farm.id,
        Animal.farm_id == farm.id,
        Animal.id == MilkRecord.animal_id,
        MilkRecord.date >= window_start,
        MilkRecord.date <= reference,
    ]
    if animal_id is not None:
        animal_filters.append(MilkRecord.animal_id == animal_id)
    animals = (
        await db.execute(
            select(
                MilkRecord.animal_id.label("animal_id"),
                Animal.tag_number.label("animal_tag"),
                func.sum(MilkRecord.litres).label("total_litres"),
                func.count(func.distinct(MilkRecord.date)).label("days_recorded"),
                func.avg(MilkRecord.fat_pct).label("avg_fat_pct"),
            )
            .where(*animal_filters)
            .group_by(MilkRecord.animal_id, Animal.tag_number)
            .order_by(func.sum(MilkRecord.litres).desc())
            .limit(200)
        )
    ).all()
    herd = (
        await db.execute(
            select(
                func.coalesce(func.sum(MilkRecord.litres), 0.0),
                func.avg(MilkRecord.fat_pct),
            ).where(*filters)
        )
    ).one()
    return (
        list(daily),
        list(animals),
        float(herd[0]),
        (float(herd[1]) if herd[1] is not None else None),
    )


async def milking_herd_count(db: AsyncSession, farm: Farm) -> int:
    """Active female animals in the milking-side buckets (dairy context)."""
    return int(
        (
            await db.execute(
                select(func.count(Animal.id)).where(
                    Animal.farm_id == farm.id,
                    Animal.status == AnimalStatus.ACTIVE.value,
                    Animal.sex == "F",
                )
            )
        ).scalar_one()
    )
