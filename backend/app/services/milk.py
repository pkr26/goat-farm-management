"""Milk production recording (dairy farms).

One reading per animal, business date and milking shift. Re-submitting the
same milking replaces the reading (upsert under the animal's row lock) so the
parlour can correct a fat test or a mis-keyed yield without double-counting.
Unlike finance's void-and-replace corrections, the replacement must keep the
same row — the (animal, date, shift) unique constraint is what prevents a
corrected milking from being counted twice — so a correction states its
reason and freezes the first submitted reading in the original_* audit
columns instead of spawning a second row.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Animal, Farm, MilkRecord, species_profile
from ..utils import today, utcnow


def _litres_quantum(value: float) -> float:
    """Round to the column's 0.001-litre precision."""
    return round(float(value), 3)


def _litre_weighted_fat_pct() -> Any:
    """sum(litres x fat%) / sum(litres) over the fat-tested milk only.

    A plain AVG(fat_pct) weighs a 2 L evening sample as much as a 10 L
    morning milking; procurement pays for the fat solids shipped, so the
    herd (and per-animal) fat level must be weighted by the litres that were
    actually measured. Rows without a fat reading contribute to neither sum.
    """
    tested = MilkRecord.fat_pct.isnot(None)
    return func.sum(MilkRecord.litres * MilkRecord.fat_pct).filter(tested) / func.sum(
        MilkRecord.litres
    ).filter(tested)


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
    correction_reason: str | None = None,
) -> MilkRecord:
    """Insert or replace the (animal, date, shift) reading.

    The animal row is already locked by the caller (the API loads it FOR
    UPDATE while verifying it belongs to this farm), which serializes two
    parlour submits racing on the same milking.

    Replacing an existing reading is a correction and must say why. The
    pre-edit values are stashed into the original_* audit columns only when
    they are still NULL, so the FIRST reading stays visible across a chain
    of corrections while corrected_at / correction_reason / created_by_id
    always describe the latest one.
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
    # Species sanity band on the whole day's yield (not just one shift):
    # Murrah peak production is ~18-20 L/day, so a day total beyond the
    # profile's cap is a units error or an inflated ledger, whichever way the
    # three shifts combine — including through a correction re-key.
    day_cap = species_profile(farm.farm_type).max_daily_milk_litres
    if day_cap > 0:
        other_shifts = (
            await db.execute(
                select(func.coalesce(func.sum(MilkRecord.litres), 0.0)).where(
                    MilkRecord.animal_id == animal.id,
                    MilkRecord.date == record_date,
                    MilkRecord.shift != shift,
                )
            )
        ).scalar_one()
        if float(other_shifts) + _litres_quantum(litres) > day_cap:
            raise ValueError(
                f"{animal.tag_number}'s total yield for {record_date.isoformat()} would "
                f"exceed the {day_cap:.0f} L/day sanity band for this species"
            )
    clean_notes = (notes or "").strip() or None
    clean_reason = (correction_reason or "").strip() or None
    if existing is not None:
        if clean_reason is None:
            raise ValueError(
                "This milking is already recorded; correcting it requires a correction_reason"
            )
        if existing.original_litres is None:
            existing.original_litres = existing.litres
            existing.original_fat_pct = existing.fat_pct
            existing.original_notes = existing.notes
            existing.original_recorded_by_id = existing.created_by_id
        existing.litres = _litres_quantum(litres)
        existing.fat_pct = fat_pct
        existing.notes = clean_notes
        existing.created_by_id = created_by_id
        existing.corrected_at = utcnow()
        existing.correction_reason = clean_reason
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
) -> tuple[list[Any], list[Any], float, float | None, int]:
    """Herd daily totals and per-animal averages over the last N days.

    The per-animal list is capped at 200 rows (the summary is a parlour
    board, not an export); the window's true animal count rides along on
    every row so callers can tell a complete herd from a truncated one.
    """
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
                _litre_weighted_fat_pct().label("avg_fat_pct"),
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
                _litre_weighted_fat_pct().label("avg_fat_pct"),
                # Window over the grouped rows (before LIMIT): the count of
                # animals with records in the window, not just the page.
                func.count().over().label("animals_total"),
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
                _litre_weighted_fat_pct(),
            ).where(*filters)
        )
    ).one()
    return (
        list(daily),
        list(animals),
        float(herd[0]),
        (float(herd[1]) if herd[1] is not None else None),
        (int(animals[0].animals_total) if animals else 0),
    )
