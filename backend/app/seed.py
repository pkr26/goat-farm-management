"""Seed reference data (per SPEC "Seed data"): bucket definitions, TMR feed
recipes with lines, vaccination schedule templates. Feed inventory rows and
RBAC role presets are seeded per farm on farm creation. All idempotent."""

import json

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Bucket,
    BucketDefinition,
    Farm,
    FeedInventory,
    FeedRecipe,
    FeedRecipeLine,
    IngredientCategory,
    Role,
    Task,
    VaccineTemplate,
)
from .permissions import ROLE_PRESETS, TASK_CATEGORY_ROLE_MAP

WET = IngredientCategory.ROUGHAGE_WET.value
DRY = IngredientCategory.ROUGHAGE_DRY.value
CONC = IngredientCategory.CONCENTRATE.value

GREEN = "Super Napier green fodder"
DRY_STOVER = "Dry jowar stover"

BUCKET_DEFINITIONS: list[tuple[Bucket, str, str, str, float]] = [
    (
        Bucket.QUARANTINE,
        "Quarantine Ward",
        "Newly purchased animals",
        "45-day protocol complete → FOUNDATION",
        0.8,
    ),
    (
        Bucket.FOUNDATION,
        "Foundation / Grow-out",
        "Purchased doelings 6–7 mo; own female kids 2–10 mo",
        "Breeding-ready (10–12 mo, ≥22 kg) → BREEDING",
        1.2,
    ),
    (
        Bucket.BREEDING,
        "Breeding Bucket",
        "Does ready to conceive; 1 buck per 20 does (rotate 7d on/off)",
        "Ultrasound-confirmed pregnant (day 30–35) → PREGNANCY_EARLY",
        1.2,
    ),
    (
        Bucket.PREGNANCY_EARLY,
        "Pregnancy A (day 35–100)",
        "Confirmed pregnant does; maintenance feed, do NOT overfeed",
        "Day 100 → PREGNANCY_LATE",
        1.2,
    ),
    (
        Bucket.PREGNANCY_LATE,
        "Pregnancy B (day 100–135)",
        "Late gestation; 60/40 feed",
        "Day ~135 (2 weeks before due) → DELIVERY",
        1.4,
    ),
    (
        Bucket.DELIVERY,
        "Delivery Ward",
        "Last ~2 weeks of pregnancy through ~5 days post-kidding",
        "Day ~5–10 post-kidding → RECOVERY",
        1.5,
    ),
    (
        Bucket.RECOVERY,
        "Recovery Ward",
        "Doe + kids together, 2 months (until weaning day 60)",
        "Kids weaned day 60 → doe RESTING; kids to MALE/FEMALE_KIDS",
        1.5,
    ),
    (
        Bucket.RESTING,
        "Resting / Dry-off + Flush",
        "Post-weaning does, ~30 days (dry-off then flush)",
        "After ~30 days → BREEDING",
        1.2,
    ),
    (
        Bucket.MALE_KIDS,
        "Male Kids Growing",
        "Male kids 2–8/9 months (frame-builder then fattening)",
        "Sold at 8–9 months, 24–28 kg",
        1.0,
    ),
    (
        Bucket.FEMALE_KIDS,
        "Female Kids Growing",
        "Female kids 2–10 months; 60/40 mix steady",
        "Breeding-ready (10–12 mo, ≥22 kg) → BREEDING",
        1.0,
    ),
]

# (code, name, description, lines=[(ingredient, kg_per_100kg, category)])
FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [
    (
        "FATTENING_50_50",
        "Fattening 50:50",
        "Male kids day 91 → sale (50% roughage / 50% concentrate)",
        [
            (GREEN, 30, WET),
            (DRY_STOVER, 20, DRY),
            ("Crushed maize", 17.5, CONC),
            ("Maize DDGS", 10, CONC),
            ("Soya DOC", 7.5, CONC),
            ("Mustard DOC", 7.5, CONC),
            ("DORB", 6, CONC),
            ("Mineral mix", 1.5, CONC),
        ],
    ),
    (
        "LACTATING_60_40",
        "Lactating 60:40",
        "Lactating does, growing doelings, frame-builder kids day 61–90",
        [
            (GREEN, 36, WET),
            (DRY_STOVER, 24, DRY),
            ("Crushed maize", 14, CONC),
            ("Maize DDGS", 4.8, CONC),
            ("Soya DOC", 6, CONC),
            ("Mustard DOC", 10, CONC),
            ("DORB", 4, CONC),
            ("Mineral mix", 1.2, CONC),
        ],
    ),
    (
        "MAINTENANCE_75_25",
        "Maintenance 75:25",
        "Resting dry-off, breeding, early pregnancy, dry bucks",
        [
            (GREEN, 45, WET),
            (DRY_STOVER, 30, DRY),
            ("Crushed maize", 7.5, CONC),
            ("Maize DDGS", 2.5, CONC),
            ("Soya DOC", 3, CONC),
            ("Mustard DOC", 3.75, CONC),
            ("DORB", 7.5, CONC),
            ("Mineral mix", 0.75, CONC),
        ],
    ),
    (
        "FLUSH_70_30",
        "Flush 70:30",
        "3–4 weeks pre-breeding flush; resting does days ~10–30",
        [
            (GREEN, 42, WET),
            (DRY_STOVER, 28, DRY),
            ("Crushed maize", 10.5, CONC),
            ("Maize DDGS", 4.5, CONC),
            ("Soya DOC", 4.5, CONC),
            ("Mustard DOC", 4.5, CONC),
            ("DORB", 5.1, CONC),
            ("Mineral mix", 0.9, CONC),
        ],
    ),
    (
        "CREEP",
        "Creep feed",
        "Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)",
        [
            ("Crushed maize", 60, CONC),
            ("Soya DOC", 30, CONC),
            ("Maize DDGS", 9, CONC),
            ("Mineral mix", 1, CONC),
        ],
    ),
]

# (name, first_dose_age_months, booster_weeks, repeat_months, timing_note)
VACCINE_TEMPLATES: list[tuple[str, float | None, float | None, float | None, str]] = [
    ("FMD", 3, 3.5, 6, "Every 6 months — September & March"),
    ("PPR", 3, None, 36, "Core vaccine; repeat every 3 years"),
    (
        "Enterotoxaemia (ET)",
        4,
        3.5,
        12,
        "Annual, May–June (pre-monsoon); first week if dam unvaccinated",
    ),
    ("Haemorrhagic Septicaemia (HS)", 3, 3.5, 12, "First dose 3–5 months; annual, May/June"),
    ("Goat Pox", 3, 3.5, 12, "First dose 3–5 months; annual, Nov/Dec"),
    ("Black Quarter", 6, None, 12, "Annual, pre-monsoon"),
    ("Johne's Disease", 6, None, 12, "Annual; herd-history dependent"),
    ("Anthrax", 6, None, 12, "Annual; region-specific"),
    ("ORF", 4, None, 6, "Every 6 months"),
    ("CCPP", 3, None, 12, "Annual, January"),
    (
        "ET + TT pre-kidding",
        None,
        None,
        None,
        "4–6 weeks before kidding; two doses 15 days apart; each pregnancy",
    ),
    (
        "Deworming",
        None,
        None,
        6,
        "All animals every 6 months — June & January (kids: every 3 months)",
    ),
    (
        "Anti-coccidial drench",
        1,
        None,
        None,
        "1–3 months (Amprolium 5 days); coccidiosis peaks 1–6 months",
    ),
]

# (ingredient, category)
FARM_INGREDIENTS: list[tuple[str, str]] = [
    (GREEN, WET),
    (DRY_STOVER, DRY),
    ("Groundnut haulms", DRY),
    ("Crushed maize", CONC),
    ("Maize DDGS", CONC),
    ("Soya DOC", CONC),
    ("Mustard DOC", CONC),
    ("DORB", CONC),
    ("Mineral mix", CONC),
]


async def _count(db: AsyncSession, model: type) -> int:
    result = await db.execute(select(func.count()).select_from(model))
    return result.scalar_one()


async def seed_reference_data(db: AsyncSession) -> None:
    """Idempotently seed global reference tables.

    INSERT ... ON CONFLICT DO NOTHING (not bare count-then-insert): two
    first-booting uvicorn workers both see empty reference tables, and
    without the conflict guard the loser crashes on the code/name UNIQUE
    constraints (AUDIT 2-10). The count checks stay as the steady-state
    fast path so a normal boot writes nothing."""
    if await _count(db, BucketDefinition) == 0:
        await db.execute(
            pg_insert(BucketDefinition)
            .values(
                [
                    {
                        "code": code.value,
                        "name": name,
                        "who": who,
                        "exit_rule": exit_rule,
                        "daily_kg_per_head": kg,
                        "sort_order": order,
                    }
                    for order, (code, name, who, exit_rule, kg) in enumerate(BUCKET_DEFINITIONS)
                ]
            )
            .on_conflict_do_nothing()
        )

    if await _count(db, FeedRecipe) == 0:
        for recipe_code, name, description, lines in FEED_RECIPES:
            recipe_id = (
                await db.execute(
                    pg_insert(FeedRecipe)
                    .values(code=recipe_code, name=name, description=description)
                    .on_conflict_do_nothing()
                    # RETURNING tells us whether THIS process inserted the row:
                    # only the winner inserts the lines, so a losing racer
                    # can't duplicate them (feed_recipe_lines has no UNIQUE).
                    .returning(FeedRecipe.id)
                )
            ).scalar_one_or_none()
            if recipe_id is None:
                continue  # a concurrent boot won this row (and its lines)
            db.add_all(
                [
                    FeedRecipeLine(
                        recipe_id=recipe_id, ingredient=ing, kg_per_100kg=kg, category=cat
                    )
                    for ing, kg, cat in lines
                ]
            )

    if await _count(db, VaccineTemplate) == 0:
        await db.execute(
            pg_insert(VaccineTemplate)
            .values(
                [
                    {
                        "name": name,
                        "first_dose_age_months": first_age,
                        "booster_weeks": booster,
                        "repeat_months": repeat,
                        "timing_note": note,
                    }
                    for name, first_age, booster, repeat, note in VACCINE_TEMPLATES
                ]
            )
            .on_conflict_do_nothing()
        )

    await db.commit()


async def seed_farm_inventory(db: AsyncSession, farm_id: int) -> None:
    """Seed zero-stock ingredient rows for a newly created farm."""
    result = await db.execute(
        select(func.count()).select_from(FeedInventory).where(FeedInventory.farm_id == farm_id)
    )
    if result.scalar_one() > 0:
        return
    for ingredient, category in FARM_INGREDIENTS:
        db.add(
            FeedInventory(
                farm_id=farm_id,
                ingredient=ingredient,
                category=category,
                unit="kg",
                qty_on_hand=0.0,
                reorder_level=100.0,
            )
        )
    await db.flush()


def _add_missing_preset_roles(
    db: AsyncSession, farm_id: int, existing_codes: set[str | None]
) -> None:
    """Queue inserts for the preset roles a farm doesn't have yet (flush by caller)."""
    for preset in ROLE_PRESETS:
        if preset["code"] in existing_codes:
            continue
        db.add(
            Role(
                farm_id=farm_id,
                code=preset["code"],
                name=preset["name"],
                description=preset["description"],
                permissions=json.dumps(preset["permissions"]),
            )
        )


async def seed_default_roles(db: AsyncSession, farm_id: int) -> None:
    """Idempotently seed the RBAC role presets (MOVER, VET, CLEANER, ...) for
    a farm. Existing roles — including edited presets — are left untouched."""
    result = await db.execute(select(Role.code).where(Role.farm_id == farm_id))
    _add_missing_preset_roles(db, farm_id, set(result.scalars()))
    await db.flush()


async def seed_new_farm(db: AsyncSession, farm: Farm) -> None:
    """Everything a freshly created farm needs (flushed, caller commits)."""
    await seed_default_roles(db, farm.id)
    await seed_farm_inventory(db, farm.id)


async def backfill_task_assignments(db: AsyncSession, farm_id: int) -> None:
    """Auto-generated tasks created before role assignment existed get their
    category's default preset role. Manually created unassigned duties
    (assigned_role_id NULL) are left alone."""
    roles_result = await db.execute(select(Role).where(Role.farm_id == farm_id))
    roles = {role.code: role.id for role in roles_result.scalars()}
    orphans = await db.execute(
        select(Task).where(
            Task.farm_id == farm_id,
            Task.auto_generated.is_(True),
            Task.assigned_role_id.is_(None),
        )
    )
    for task in orphans.scalars():
        role_id = roles.get(TASK_CATEGORY_ROLE_MAP.get(task.category))
        if role_id:
            task.assigned_role_id = role_id
    await db.flush()


async def seed_startup(db: AsyncSession) -> None:
    """App-startup seeding: reference data + per-farm presets/backfills.

    O(1) queries, not O(farms) (AUDIT 5-L5): role codes for every farm come
    in one bulk select, and the task backfill runs only for farms that
    actually have orphan auto-generated tasks (a steady-state farm never
    does, so routine boots issue no per-farm queries at all)."""
    await seed_reference_data(db)
    farm_ids = list((await db.execute(select(Farm.id))).scalars())
    codes_by_farm: dict[int, set[str | None]] = {}
    if farm_ids:
        role_rows = await db.execute(select(Role.farm_id, Role.code))
        for farm_id, code in role_rows.all():
            codes_by_farm.setdefault(farm_id, set()).add(code)
        for farm_id in farm_ids:
            _add_missing_preset_roles(db, farm_id, codes_by_farm.get(farm_id, set()))
        # Flush the queued roles BEFORE the backfill: sessions run
        # autoflush=False, so backfill_task_assignments' SELECT would not see
        # the roles just added above and orphan tasks would stay unassigned.
        await db.flush()
        orphan_farms = (
            await db.execute(
                select(Task.farm_id)
                .where(Task.auto_generated.is_(True), Task.assigned_role_id.is_(None))
                .distinct()
            )
        ).scalars()
        for farm_id in orphan_farms:
            await backfill_task_assignments(db, farm_id)
    await db.commit()
