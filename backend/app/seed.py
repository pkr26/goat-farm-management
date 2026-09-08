"""Seed reference data (per SPEC "Seed data"): bucket definitions, TMR feed
recipes with lines, vaccination schedule templates. Feed inventory rows and
RBAC role presets are seeded per farm on farm creation. All idempotent."""

import json
import logging
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import String, and_, column, func, literal, or_, select, text, true, tuple_, values
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Bucket,
    BucketDefinition,
    Farm,
    FarmMembership,
    FeedInventory,
    FeedRecipe,
    FeedRecipeLine,
    IngredientCategory,
    Role,
    Task,
    TaskStatus,
    VaccineTemplate,
)
from .permissions import (
    ROLE_PRESETS,
    TASK_CATEGORY_ROLE_MAP,
    TASK_ROLE_CODES,
    preset_codes,
    task_role_codes,
)

logger = logging.getLogger("goatfarm.seed")

WET = IngredientCategory.ROUGHAGE_WET.value
DRY = IngredientCategory.ROUGHAGE_DRY.value
CONC = IngredientCategory.CONCENTRATE.value

GREEN = "Super Napier green fodder"
DRY_STOVER = "Dry jowar stover"

# ---------------------------------------------------------------------------
# Bucket definitions — one row per lifecycle stage code.
# ---------------------------------------------------------------------------
BUCKET_DEFINITIONS: list[tuple[Bucket, str, str, str, float]] = [
    (
        Bucket.QUARANTINE,
        "Quarantine Ward",
        "Newly purchased animals",
        "45-day protocol complete → FOUNDATION",
        # ~3% BW DM maintenance for a 30 kg adult of the 75:25 mix — below
        # this, newly transported animals under-eat through the immunity-
        # critical 45 days.
        1.1,
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
        "Does ready to conceive; 1 buck per 20 does (enforced: a sire is refused his 21st open service)",
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
        "Final ~2 weeks of pregnancy (move generated at day ~135)",
        "Kidding recorded → RECOVERY (same day)",
        1.5,
    ),
    (
        Bucket.RECOVERY,
        "Recovery Ward",
        "Doe + kids together, 2 months (until weaning day 60); the per-head rate is per doe — unweaned kids are planned on the CREEP line",
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
    # FMD at 3 months follows TNAU's Telangana schedule (Vikaspedia/NADCP
    # kid materials say 4; the 6-monthly repeat matching NADCP rounds is the
    # load-bearing half).
    ("FMD", 3, 3.5, 6, "Every 6 months — September & March"),
    # PPR immunity lasts ≥3 years in trials, but Telangana department camps
    # revaccinate yearly and will flag the herd as overdue against an annual
    # convention — default to the field schedule.
    ("PPR", 3, None, 12, "Core vaccine; annual revaccination (department camp schedule)"),
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
    # ORF (contagious ecthyma) is deliberately NOT in the default calendar:
    # Indian practice (TNAU) does not vaccinate for sore mouth; control is
    # outbreak-driven under veterinary direction.
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


async def seed_reference_data(db: AsyncSession) -> None:
    """Idempotently seed global reference tables for every farm type.

    INSERT ... ON CONFLICT DO NOTHING (not a table-wide count gate): two
    first-booting workers can race safely, and a later release can add one
    new bucket, recipe, or vaccine to an already-populated installation.
    Existing reference rows are deliberately preserved; changing an existing
    definition remains an explicit data migration rather than a surprise
    startup rewrite."""
    # Serialize only this small global constant-data repair across booting
    # processes. It never locks tenant Farm rows or scans tenant histories.
    await db.execute(text("SELECT pg_advisory_xact_lock(718204613)"))
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

    for recipe_code, name, description, lines in FEED_RECIPES:
        recipe_id = (
            await db.execute(
                pg_insert(FeedRecipe)
                .values(
                    code=recipe_code,
                    name=name,
                    description=description,
                )
                .on_conflict_do_nothing()
                # RETURNING tells us whether THIS process inserted the row:
                # only the winner inserts the lines, so a losing racer
                # can't duplicate them (feed_recipe_lines has no UNIQUE).
                .returning(FeedRecipe.id)
            )
        ).scalar_one_or_none()
        if recipe_id is None:
            continue  # already present, or a concurrent boot won this row and its lines
        db.add_all(
            [
                FeedRecipeLine(
                    recipe_id=recipe_id, ingredient=ing, kg_per_100kg=kg, category=cat
                )
                for ing, kg, cat in lines
            ]
        )

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


def _ingredient_values() -> Any:
    """VALUES relation of (ingredient, category)."""
    rows = [(ingredient, category) for ingredient, category in FARM_INGREDIENTS]
    return (
        values(
            column("ingredient", String(120)),
            column("category", String(20)),
            name="seed_ingredients",
        )
        .data(rows)
        .alias("seed_ingredients")
    )


async def _seed_farm_inventories(
    db: AsyncSession,
    farm_id: int | None = None,
    *,
    farm_ids: list[int] | None = None,
) -> None:
    """Insert every missing canonical ingredient for one farm or all farms.

    The INSERT .. SELECT keeps startup's bind count independent of tenant
    count. ON CONFLICT makes concurrent app boots/new-farm retries safe and
    deliberately preserves quantities, prices, and operator-edited reorder
    levels on rows that already exist.
    """
    ingredients = _ingredient_values()
    source = select(
        Farm.id,
        ingredients.c.ingredient,
        ingredients.c.category,
        literal("kg"),
        literal(0.0),
        literal(100.0),
    ).select_from(Farm.__table__.join(ingredients, true()))
    if farm_id is not None and farm_ids is not None:
        raise ValueError("provide farm_id or farm_ids, not both")
    if farm_id is not None:
        source = source.where(Farm.id == farm_id)
    elif farm_ids is not None:
        if not farm_ids:
            return
        source = source.where(Farm.id.in_(farm_ids))
    await db.execute(
        pg_insert(FeedInventory)
        .from_select(
            [
                "farm_id",
                "ingredient",
                "category",
                "unit",
                "qty_on_hand",
                "reorder_level",
            ],
            source,
        )
        .on_conflict_do_nothing(constraint="uq_feed_inventory_farm_ingredient")
    )
    await db.flush()


async def seed_farm_inventory(db: AsyncSession, farm_id: int) -> None:
    """Repair/seed all canonical zero-stock ingredient rows for one farm."""
    await _seed_farm_inventories(db, farm_id)


def _free_preset_role_name(preset_name: str, code: str, taken: set[str]) -> str:
    """Return a display name no active role on this farm already holds.

    `uq_roles_farm_active_name` is a real partial unique index on
    (farm_id, name), and nothing reserves preset names: an owner may already
    have created a custom role literally called "Feeder".  A preset's stable
    identity is its `code`, so a decorated display name is fully functional —
    whereas inserting the colliding name raises IntegrityError and takes every
    other tenant's repair down with it.
    """
    if preset_name not in taken:
        return preset_name
    suffixed = f"{preset_name} ({code})"
    candidate = suffixed
    ordinal = 2
    while candidate in taken:
        candidate = f"{suffixed} {ordinal}"
        ordinal += 1
    return candidate


def _add_missing_preset_roles(
    db: AsyncSession,
    farm_id: int,
    existing_codes: set[str | None],
    active_names: set[str],
) -> None:
    """Queue inserts for the preset roles a farm doesn't have yet (flush by
    caller).  `active_names` is updated with each chosen name so two presets
    cannot collide with each other either."""
    for preset in ROLE_PRESETS:
        if preset["code"] in existing_codes:
            continue
        name = _free_preset_role_name(preset["name"], preset["code"], active_names)
        active_names.add(name)
        db.add(
            Role(
                farm_id=farm_id,
                code=preset["code"],
                name=name,
                description=preset["description"],
                permissions=json.dumps(preset["permissions"]),
            )
        )


def _role_identity_rows(
    rows: Iterable[tuple[str | None, str, datetime | None]],
) -> tuple[set[str | None], set[str]]:
    """Split (code, name, deleted_at) rows into seeded codes and taken names.

    Codes deliberately include tombstoned roles (a preset code is never
    re-created), while only live rows reserve a name — the unique index is
    partial on `deleted_at IS NULL`.
    """
    codes: set[str | None] = set()
    names: set[str] = set()
    for code, name, deleted_at in rows:
        codes.add(code)
        if deleted_at is None:
            names.add(name)
    return codes, names


async def seed_default_roles(db: AsyncSession, farm_id: int) -> None:
    """Idempotently seed the RBAC role presets (MOVER, VET, CLEANER, ...) for
    a farm. Existing roles — including edited presets — are left untouched.

    The farm row is the per-tenant seed mutex.  A uniqueness constraint on
    role name alone cannot make an INSERT-only approach safe because preset
    names are editable while their codes are the stable identity.  Locking
    before reading codes serializes concurrent app boots and farm-creation
    retries without overwriting an operator's edits.

    A Farm ROW lock is correct here, unlike the manual-duty queue in
    `services.tasks.lock_manual_task_queue`, which uses an advisory lock.
    The difference is what the holder already owns: this function's own
    `roles` INSERT takes FOR KEY SHARE on the very same Farm row, and its
    callers (farm creation, the legacy-repair worker) reach it already holding
    that row.  Taking the row first is therefore the consistent order; an
    advisory lock would invert against those callers and deadlock — which is
    exactly what `test_concurrent_role_seed_serializes_on_farm_row` pins.
    """
    await db.execute(select(Farm.id).where(Farm.id == farm_id).with_for_update())
    result = await db.execute(
        select(Role.code, Role.name, Role.deleted_at).where(Role.farm_id == farm_id)
    )
    codes, names = _role_identity_rows(
        [(code, name, deleted_at) for code, name, deleted_at in result.all()]
    )
    _add_missing_preset_roles(db, farm_id, codes, names)
    await db.flush()


async def seed_new_farm(db: AsyncSession, farm: Farm) -> None:
    """Everything a freshly created farm needs (flushed, caller commits)."""
    await seed_default_roles(db, farm.id)
    await seed_farm_inventory(db, farm.id)


async def repair_legacy_farms_batch(db: AsyncSession, *, batch_size: int) -> int:
    """Repair at most one finite, lock-skipping batch of legacy farms."""
    if not 1 <= batch_size <= 500:
        raise ValueError("batch_size must be between 1 and 500")
    ingredients = _ingredient_values()
    # A farm needs the repair when ANY preset it should hold is missing.
    missing_role = or_(
        *[
            ~select(Role.id)
            .where(Role.farm_id == Farm.id, Role.code == code)
            .correlate(Farm)
            .exists()
            for code in sorted(preset_codes())
        ]
    )
    # A farm's canonical ingredient list is the seeded farm-ingredient list.
    expected_count = select(func.count()).select_from(ingredients).scalar_subquery()
    inventory_count = (
        select(func.count(FeedInventory.id))
        .join(ingredients, FeedInventory.ingredient == ingredients.c.ingredient)
        .where(FeedInventory.farm_id == Farm.id)
        .correlate(Farm)
        .scalar_subquery()
    )
    farm_rows = (
        await db.execute(
            select(Farm.id)
            .where(or_(missing_role, inventory_count < expected_count))
            .order_by(Farm.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    ).all()
    if not farm_rows:
        return 0
    farm_ids = [farm_id for farm_id, in farm_rows]

    role_rows = await db.execute(
        select(Role.farm_id, Role.code, Role.name, Role.deleted_at).where(
            Role.farm_id.in_(farm_ids)
        )
    )
    rows_by_farm: dict[int, list[tuple[str | None, str, datetime | None]]] = {}
    for selected_farm_id, code, name, deleted_at in role_rows.all():
        rows_by_farm.setdefault(selected_farm_id, []).append((code, name, deleted_at))
    for selected_farm_id in farm_ids:
        codes, names = _role_identity_rows(rows_by_farm.get(selected_farm_id, []))
        # One tenant must not be able to roll the whole batch back. A savepoint
        # per farm keeps an external-writer race (or any other per-farm insert
        # failure) from also cancelling the other claimed farms' repairs, the
        # feed-inventory repair below, and the task-role backfill that runs
        # after this call.
        try:
            async with db.begin_nested():
                _add_missing_preset_roles(db, selected_farm_id, codes, names)
                await db.flush()
        except IntegrityError:
            logger.warning(
                "legacy preset-role repair skipped farm_id=%s (conflicting role row)",
                selected_farm_id,
            )
    await _seed_farm_inventories(db, farm_ids=farm_ids)
    return len(farm_ids)


async def backfill_task_assignments_batch(db: AsyncSession, *, batch_size: int) -> int:
    """Assign at most one finite, lock-skipping batch of legacy duties.

    Besides older auto-generated duties, D9 requires every live personal duty
    to retain the assignee's role as a fallback.  Retained inactive membership
    rows make that lookup stable without rewriting lifetime task history in an
    access-revocation request.
    """
    if not 1 <= batch_size <= 10_000:
        raise ValueError("batch_size must be between 1 and 10000")
    pending_personal_without_role = and_(
        Task.status == TaskStatus.PENDING.value,
        Task.assigned_user_id.is_not(None),
        Task.assigned_role_id.is_(None),
    )
    legacy_generated_without_role = and_(
        Task.auto_generated.is_(True),
        Task.assigned_role_id.is_(None),
        Task.category.in_(list(TASK_CATEGORY_ROLE_MAP)),
    )
    candidate = or_(pending_personal_without_role, legacy_generated_without_role)

    # Do not let one currently unresolvable low-ID row monopolize every
    # ordered batch.  SKIP LOCKED only skips rows held by *other* transactions;
    # after this transaction commits, an unchanged row is immediately selected
    # again.  That used to starve every later repair forever (and made the
    # hourly worker burn its complete max-batch budget on the same rows).
    #
    # A personal assignment can be recovered from its retained same-farm
    # membership.  Otherwise, mapped generated duties can use the farm's live
    # preset role.  Correlated EXISTS predicates keep the claim set limited to
    # rows that have one of those sources *now*; a later membership/role repair
    # makes a previously skipped row eligible automatically.
    # A membership fallback is only usable while its role is live.  Account
    # deletion retains membership rows (FK/audit anchors) and `_member_count`
    # only counts live users, so a custom role whose last holders were all
    # tombstoned users can itself be soft-deleted while a retained membership
    # still points at it.  The request path refuses such a role
    # (`Role.deleted_at IS NULL` in api.tasks); without the same filter here
    # the repair would pin a defunct role onto a duty — and, because the claim
    # query would keep matching the unresolved row, re-claim it every pass.
    membership_role_exists = (
        select(FarmMembership.id)
        .join(Role, Role.id == FarmMembership.role_id)
        .where(
            FarmMembership.farm_id == Task.farm_id,
            FarmMembership.user_id == Task.assigned_user_id,
            Role.deleted_at.is_(None),
        )
        .correlate(Task)
        .exists()
    )
    # A generated duty routes to its category's candidate preset codes.
    preset_role_exists = or_(
        *[
            and_(
                Task.category == category,
                select(Role.id)
                .where(
                    Role.farm_id == Task.farm_id,
                    Role.code.in_(task_role_codes(category)),
                    Role.deleted_at.is_(None),
                )
                .correlate(Task)
                .exists(),
            )
            for category in TASK_CATEGORY_ROLE_MAP
        ]
    )
    resolvable = or_(
        and_(Task.assigned_user_id.is_not(None), membership_role_exists),
        preset_role_exists,
    )
    tasks = list(
        (
            await db.execute(
                select(Task)
                .join(Farm, Farm.id == Task.farm_id)
                .where(candidate, resolvable)
                .order_by(Task.id)
                .limit(batch_size)
                # of=Task: a bare FOR UPDATE would also lock the joined Farm
                # rows for this whole worker transaction, resurrecting the
                # Task->Farm / Farm->Task deadlock cycle the phased repair
                # below exists to prevent and stalling every farm-FK insert
                # on the claimed farms.
                .with_for_update(skip_locked=True, of=Task)
            )
        ).scalars()
    )
    if not tasks:
        return 0
    farm_ids = sorted({task.farm_id for task in tasks})
    personal_pairs = sorted(
        {
            (task.farm_id, task.assigned_user_id)
            for task in tasks
            if task.assigned_user_id is not None
        }
    )
    membership_roles: dict[tuple[int, int], int] = {}
    if personal_pairs:
        membership_rows = await db.execute(
            select(
                FarmMembership.farm_id,
                FarmMembership.user_id,
                FarmMembership.role_id,
            )
            # Mirror the eligibility probe above: a membership pointing at a
            # soft-deleted role must not resolve — the duty falls through to
            # its live category preset (or stays unclaimed) instead of being
            # stamped with a defunct role.
            .join(Role, Role.id == FarmMembership.role_id)
            .where(
                tuple_(FarmMembership.farm_id, FarmMembership.user_id).in_(personal_pairs),
                Role.deleted_at.is_(None),
            )
            .order_by(FarmMembership.farm_id, FarmMembership.user_id)
            # Match the request/lazy-repair path: the role fallback must not
            # change after we read it but before the task FK is written.
            .with_for_update(read=True)
        )
        membership_roles = {
            (farm_id, user_id): role_id for farm_id, user_id, role_id in membership_rows.all()
        }
    role_codes = set(TASK_ROLE_CODES)
    role_rows = await db.execute(
        select(Role.farm_id, Role.code, Role.id).where(
            Role.farm_id.in_(farm_ids),
            Role.code.in_(role_codes),
            Role.deleted_at.is_(None),
        )
    )
    roles = {(farm_id, code): role_id for farm_id, code, role_id in role_rows.all()}
    assigned = 0
    for task in tasks:
        role_id = (
            membership_roles.get((task.farm_id, task.assigned_user_id))
            if task.assigned_user_id is not None
            else None
        )
        if role_id is None:
            for code in task_role_codes(task.category):
                role_id = roles.get((task.farm_id, code))
                if role_id is not None:
                    break
        if role_id is not None:
            task.assigned_role_id = role_id
            assigned += 1
    await db.flush()
    if assigned != len(tasks):
        # A membership/role may still change between the correlated eligibility
        # probe and the locked source-row reads.  That race is harmless: the
        # unresolved row remains eligible for a later pass once a source is
        # stable, while this already-bounded claim still lets the caller
        # continue its current batch budget.
        logger.info("task role backfill resolved %d of %d claimed duties", assigned, len(tasks))
    # Report rows claimed, not rows written: the caller compares this with the
    # batch size to decide whether another finite pass may contain work.
    return len(tasks)


async def repair_legacy_data_batch(
    db: AsyncSession,
    *,
    farm_batch_size: int,
    task_batch_size: int,
) -> tuple[int, int]:
    """One bounded repair unit for the non-blocking maintenance worker.

    Farm repair takes ``Farm FOR UPDATE`` while task backfill takes ``Task FOR
    UPDATE``.  Do not retain the farm locks across the task phase: a live
    recurring-task transition can already hold that Task and then need a Farm
    FK ``KEY SHARE`` lock for its successor insert.  Combining both phases in
    one transaction creates Farm -> Task opposite Task -> Farm and lets
    PostgreSQL deadlock the maintenance worker with a valid request.

    The farm phase is therefore an independently committed unit.  The caller
    commits (or rolls back) the task phase as usual.
    """
    farms = await repair_legacy_farms_batch(db, batch_size=farm_batch_size)
    await db.commit()
    tasks = await backfill_task_assignments_batch(db, batch_size=task_batch_size)
    return farms, tasks


async def seed_startup(db: AsyncSession) -> None:
    """Boot-critical work is fixed-size global reference data only.

    Existing-tenant repairs run after readiness in finite SKIP LOCKED batches;
    a rolling deploy must never lock/materialize every Farm or orphan Task.
    New farms remain synchronously initialized by ``seed_new_farm``.
    """
    await seed_reference_data(db)
