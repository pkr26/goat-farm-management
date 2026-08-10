"""Health / vaccination schedule."""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Animal,
    Farm,
    HealthEvent,
    HealthEventType,
    MovementRestrictionAction,
    Transaction,
    TransactionCategory,
    TransactionType,
    VaccineTemplate,
)
from ..utils import add_months, allocate_money, money, today, utcnow

# HealthEvent.type → TransactionCategory: what P&L bucket the spend belongs in.
# TREATMENT/FOOTBATH/VITAMIN → VET (vet consultation, hoof care, tonics).
# VACCINE/DEWORMING → MEDICINE (the actual drug/vaccine spend).
_HEALTH_TYPE_TO_TX_CATEGORY: dict[str, str] = {
    HealthEventType.VACCINE.value: TransactionCategory.MEDICINE.value,
    HealthEventType.DEWORMING.value: TransactionCategory.MEDICINE.value,
    HealthEventType.TREATMENT.value: TransactionCategory.VET.value,
    HealthEventType.FOOTBATH.value: TransactionCategory.VET.value,
    HealthEventType.VITAMIN.value: TransactionCategory.MEDICINE.value,
}


# Canonical title of the generated pre-kidding vaccine duty. Owned here (with
# the rest of the programme vocabulary) so services.breeding builds exactly the
# phrase protocol_phrase_of/template_name_for_task recognise; the doe's tag is
# appended as ": {tag_number}" display text and carries no programme meaning.
PRE_KIDDING_VACCINE_TITLE = "Pre-kidding ET+TT vaccine"


def _words(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", text.lower()))


def _normalize(text: str) -> str:
    return " ".join(text.lower().replace("+", " + ").split())


def _has_alias(words: tuple[str, ...], *aliases: str) -> bool:
    """Match complete words/phrases, never substrings inside another word."""
    for alias in aliases:
        alias_words = _words(alias)
        if not alias_words:
            continue
        width = len(alias_words)
        windows = range(len(words) - width + 1)
        if any(words[index : index + width] == alias_words for index in windows):
            return True
    return False


def protocol_phrase_of(title: str) -> str:
    """The operator-free portion of an auto-generated health-duty title.

    Generated titles wrap user text around a fixed protocol phrase: the
    quarantine schedule prefixes ``[{supplier} #{batch id}] `` and the
    pre-kidding duty appends ``: {tag number}``. A supplier or tag naming
    another disease ("PPR Traders", a doe tagged "PPR-01") must never decide
    which programme item the duty belongs to, so both wrappers are removed
    before any alias scan.
    """
    phrase = title
    if phrase.startswith("["):
        # A supplier may itself contain "]"; the generated prefix always ends
        # at the LAST one, because no protocol phrase contains a bracket.
        closing = phrase.rfind("]")
        if closing != -1:
            phrase = phrase[closing + 1 :]
    phrase = phrase.strip()
    if phrase.startswith(PRE_KIDDING_VACCINE_TITLE):
        return PRE_KIDDING_VACCINE_TITLE
    return phrase


def template_name_for_task(title: str, category: str) -> str | None:
    """Canonical template inferred from an auto-generated health task.

    The task is the authoritative link; free text remains useful as a product
    note but cannot silently complete a different programme item — which is
    also why only the protocol phrase of the title is scanned.
    """
    words = _words(protocol_phrase_of(title))
    if category == HealthEventType.DEWORMING.value:
        return "Deworming"
    if _has_alias(words, "ppr", "peste des petits"):
        return "PPR"
    if _has_alias(words, "goat pox", "goatpox"):
        return "Goat Pox"
    if _has_alias(words, "fmd", "foot and mouth"):
        return "FMD"
    et_matches = _has_alias(words, "et", "enterotoxaemia", "enterotoxemia")
    tt_matches = _has_alias(words, "tt", "tetanus", "tetanus toxoid")
    if _has_alias(words, "pre kidding") and et_matches and tt_matches:
        return "ET + TT pre-kidding"
    if et_matches and tt_matches:
        return "Enterotoxaemia (ET)"
    return None


def target_matches_template(target: str, template_name: str) -> bool:
    """Validate only an explicitly supplied target; a blank target is filled
    by the linked task's canonical template rather than guessed from a brand."""
    target_words = _words(target)
    if not target_words:
        return True

    et_matches = _has_alias(target_words, "et", "enterotoxaemia", "enterotoxemia")
    tt_matches = _has_alias(target_words, "tt", "tetanus", "tetanus toxoid")
    if template_name == "ET + TT pre-kidding":
        # A combined duty is complete only when both vaccine components were
        # explicitly recorded. "ET" alone must never close the TT half.
        return et_matches and tt_matches
    if template_name == "Enterotoxaemia (ET)":
        return et_matches
    if template_name == "FMD":
        return _has_alias(target_words, "fmd", "foot and mouth")
    if template_name == "PPR":
        return _has_alias(target_words, "ppr", "peste des petits")
    if template_name == "Goat Pox":
        return _has_alias(target_words, "goat pox", "goatpox")
    if template_name == "Deworming":
        return any(word.startswith("deworm") for word in target_words)
    # A template that advertises its own abbreviation — "Haemorrhagic
    # Septicaemia (HS)" — must accept it: the read/inference path already does
    # (_legacy_event_matches), so rejecting it here would punish the more
    # precise entry while a blank target is accepted.
    abbrev_match = re.search(r"\(([^)]+)\)", template_name)
    aliases = [template_name.split("(")[0]]
    if abbrev_match:
        aliases.append(abbrev_match.group(1))
    return _has_alias(target_words, *aliases)


def place_movement_restriction(
    db: AsyncSession,
    animal: Animal,
    *,
    disease_target: str,
    restriction_reason: str,
    action_reference: str,
    acted_by_id: int | None,
    health_event_id: int | None = None,
    acted_at: datetime | None = None,
) -> MovementRestrictionAction:
    """Start and audit a new scheduled-disease restriction episode.

    A later placement supersedes an earlier still-open episode by advancing
    the monotonic version; it does not fabricate a clearance for the older
    concern. Only a referenced explicit clearance appends ``CLEARED``.
    """
    target = disease_target.strip()
    if not target:
        raise ValueError("A suspected scheduled disease requires a disease target")
    animal.restriction_version = (animal.restriction_version or 0) + 1
    animal.suspected_scheduled_disease = True
    animal.suspected_disease = target
    animal.movement_restricted = True
    animal.restriction_reason = restriction_reason
    action = MovementRestrictionAction(
        farm_id=animal.farm_id,
        animal_id=animal.id,
        restriction_version=animal.restriction_version,
        action="PLACED",
        acted_at=acted_at or utcnow(),
        acted_by_id=acted_by_id,
        action_reference=action_reference,
        disease_target=target,
        health_event_id=health_event_id,
    )
    db.add(action)
    return action


async def validated_template(
    db: AsyncSession, template_name: str | None, event_type: str
) -> VaccineTemplate | None:
    """Return an exact seeded template or reject a free-form override."""
    if not template_name:
        return None
    if event_type not in {HealthEventType.VACCINE.value, HealthEventType.DEWORMING.value}:
        raise ValueError("A schedule template is valid only for vaccine or deworming events")
    template = (
        await db.execute(select(VaccineTemplate).where(VaccineTemplate.name == template_name))
    ).scalar_one_or_none()
    if template is None:
        raise ValueError("Unknown schedule template")
    if template.name == "Deworming" and event_type != HealthEventType.DEWORMING.value:
        raise ValueError("Deworming template requires a DEWORMING event")
    if template.name != "Deworming" and event_type != HealthEventType.VACCINE.value:
        raise ValueError("Vaccine template requires a VACCINE event")
    return template


async def record_health_event(
    db: AsyncSession,
    farm: Farm,
    animals: list[Animal],
    event_date: date,
    event_type: str,
    product_name: str,
    disease_target: str,
    dose: str,
    route: str,
    vet_name: str,
    total_cost: float | None,
    next_due_date: date | None,
    schedule_template_name: str | None,
    schedule_template_id: int | None,
    next_due_authority: str | None,
    product_lot: str,
    product_manufactured_on: date | None,
    product_expires_on: date | None,
    vaccine_valid_until: date | None,
    certificate_number: str,
    official_tag_number: str,
    administered_by: str,
    withdrawal_until: date | None,
    suspected_scheduled_disease: bool,
    authority_notified_at: date | None,
    isolation_started_at: date | None,
    notes: str,
    purchase_batch_id: int | None = None,
    created_by_id: int | None = None,
) -> list[HealthEvent]:
    """Create one HealthEvent row per animal; total cost divided in paise.

    Shares are exact and non-negative even when the total is smaller than the
    animal count; an explicit ₹0 cost is stored as 0.00, not NULL.
    """
    exact_total_cost = money(total_cost) if total_cost is not None else None
    if suspected_scheduled_disease and not disease_target.strip():
        raise ValueError("A suspected scheduled disease requires a disease target")
    costs: list[Decimal | None] = []
    if exact_total_cost is not None and animals:
        costs.extend(allocate_money(exact_total_cost, len(animals)))
    else:
        costs.extend([None] * len(animals))
    events = []
    placed_restrictions: list[tuple[Animal, HealthEvent]] = []
    for animal, per_animal_cost in zip(animals, costs, strict=True):
        event = HealthEvent(
            farm_id=farm.id,
            animal_id=animal.id,
            purchase_batch_id=purchase_batch_id,
            date=event_date,
            type=event_type,
            product_name=product_name or None,
            disease_target=disease_target or None,
            dose=dose or None,
            route=route or None,
            vet_name=vet_name or None,
            cost=per_animal_cost,
            next_due_date=next_due_date,
            schedule_template_name=schedule_template_name or None,
            schedule_template_id=schedule_template_id,
            next_due_authority=next_due_authority or None,
            product_lot=product_lot or None,
            product_manufactured_on=product_manufactured_on,
            product_expires_on=product_expires_on,
            vaccine_valid_until=vaccine_valid_until,
            certificate_number=certificate_number or None,
            official_tag_number=official_tag_number or None,
            administered_by=administered_by or None,
            withdrawal_until=withdrawal_until,
            suspected_scheduled_disease=suspected_scheduled_disease,
            authority_notified_at=authority_notified_at,
            isolation_started_at=isolation_started_at,
            notes=notes or None,
            created_by_id=created_by_id,
        )
        db.add(event)
        events.append(event)
        if suspected_scheduled_disease:
            placed_restrictions.append((animal, event))
    # Book the health-event spend in the ledger. Without
    # this, monthly P&L reports ₹0 medicine/vet spend even when HealthEvent
    # rows carry a cost — a farmer's monthly loss would be understated for
    # every vaccination round. One aggregated Transaction per record_health_event
    # call (matches how the user entered it in the UI: one form, one total).
    # Allocate event ids before creating the source-linked aggregate ledger
    # row. The first event is the stable identity of this one submitted form.
    await db.flush()
    placed_at = utcnow()
    for animal, event in placed_restrictions:
        place_movement_restriction(
            db,
            animal,
            disease_target=disease_target,
            restriction_reason="Scheduled-disease suspicion recorded in health log",
            action_reference=f"Health event #{event.id}",
            acted_by_id=created_by_id,
            health_event_id=event.id,
            acted_at=placed_at,
        )
        # Deliberately assigned, not merged: this column describes the CURRENT
        # episode only. place_movement_restriction opens a new episode for every
        # suspicion, so carrying a previous date forward would falsely assert
        # the authority had been told about THIS concern. The historical fact is
        # not lost — it stays on the HealthEvent that recorded it, which the
        # restriction action cites via health_event_id.
        animal.authority_notified_at = authority_notified_at
    if exact_total_cost is not None and animals:
        db.add(
            Transaction(
                farm_id=farm.id,
                date=event_date,
                type=TransactionType.EXPENSE.value,
                category=_HEALTH_TYPE_TO_TX_CATEGORY.get(
                    event_type, TransactionCategory.MEDICINE.value
                ),
                amount=exact_total_cost,
                related_animal_id=animals[0].id if len(animals) == 1 else None,
                notes=_health_transaction_note(
                    event_type, product_name, disease_target, len(animals)
                ),
                created_by_id=created_by_id,
                source_type="HEALTH_EVENT",
                source_id=events[0].id,
            )
        )
    await db.flush()
    return events


def _health_transaction_note(
    event_type: str, product_name: str, disease_target: str, animal_count: int
) -> str:
    """Human-readable ledger note for a health-event Transaction."""
    label = (product_name or disease_target or event_type).strip() or event_type
    head = f"for {animal_count} animal" + ("" if animal_count == 1 else "s")
    return f"{label} {head}"


# Common trade names / aliases for the seeded vaccine templates:
# matching is otherwise a conservative substring on the template name, so an
# event recorded by drug brand with a blank disease target would never match.
# Keyed by the NORMALIZED (lowercase) template name sans parenthetical;
# values are matched as substrings of the normalized "product + target"
# haystack. Keep aliases specific — a bare brand prefix ("raksha") spans
# multiple vaccines.
_TEMPLATE_ALIASES: dict[str, tuple[str, ...]] = {
    "fmd": ("raksha-triovac", "triovac", "raksha-ovac"),
    "ppr": ("raksha-ppr",),
    "goat pox": ("goatpox", "goat-pox", "raksha-gp"),
    "enterotoxaemia": ("entero", "raksha-et"),
    "haemorrhagic septicaemia": ("hemorrhagic", "raksha-hs"),
}

LEGACY_SCHEDULE_SCAN_LIMIT = 500


def _legacy_event_matches(
    template_name: str,
    *,
    event_type: str,
    schedule_template_name: str | None,
    product_name: str | None,
    disease_target: str | None,
) -> bool:
    """Compatibility match for a bounded set of pre-linkage event rows."""
    if schedule_template_name:
        return _normalize(schedule_template_name) == _normalize(template_name)
    haystack = _normalize(f"{product_name or ''} {disease_target or ''}")
    words = _words(haystack)
    key = _normalize(template_name.split("(")[0])
    if key.startswith("deworm"):
        return event_type == HealthEventType.DEWORMING.value or any(
            word.startswith("deworm") for word in words
        )
    if key and _has_alias(words, key):
        return True
    if any(_has_alias(words, alias) for alias in _TEMPLATE_ALIASES.get(key, ())):
        return True
    abbrev_match = re.search(r"\(([^)]+)\)", template_name)
    abbrev = abbrev_match.group(1).strip().lower() if abbrev_match else ""
    return bool(abbrev) and re.search(rf"\b{re.escape(abbrev)}\b", haystack) is not None


async def inferred_schedule_template(
    db: AsyncSession,
    event_type: str,
    product_name: str,
    disease_target: str,
) -> VaccineTemplate | None:
    """Canonicalize a new unlabelled event only when its legacy text is unique."""
    if event_type == HealthEventType.DEWORMING.value:
        return (
            await db.execute(select(VaccineTemplate).where(VaccineTemplate.name == "Deworming"))
        ).scalar_one_or_none()
    if event_type != HealthEventType.VACCINE.value:
        return None
    templates = list(
        (
            await db.execute(
                select(VaccineTemplate).where(
                    VaccineTemplate.name != "Deworming",
                    (VaccineTemplate.first_dose_age_months.is_not(None))
                    | (VaccineTemplate.repeat_months.is_not(None)),
                )
            )
        ).scalars()
    )
    matches = [
        template
        for template in templates
        if _legacy_event_matches(
            template.name,
            event_type=event_type,
            schedule_template_name=None,
            product_name=product_name,
            disease_target=disease_target,
        )
    ]
    return matches[0] if len(matches) == 1 else None


async def vaccination_schedule_for_animal(db: AsyncSession, animal: Animal) -> list[dict[str, Any]]:
    """Per-animal vaccination schedule from seeded VaccineTemplates.
    Status: DONE (event recorded) / OVERDUE (due date passed) / UPCOMING.

    The newest two matching facts plus the earliest (primary) fact per template
    are selected. That bounded set distinguishes a lone first dose from a
    completed booster, preserves the actual booster anchor, and derives the
    latest repeat/authority date without lifetime-volume memory growth.
    """
    farm = await db.get(Farm, animal.farm_id)
    reference_date = today(farm.timezone) if farm is not None else today()
    dob = animal.effective_dob
    templates_result = await db.execute(select(VaccineTemplate).order_by(VaccineTemplate.id))
    templates = [
        template
        for template in templates_result.scalars()
        if template.first_dose_age_months is not None or template.repeat_months
    ]

    limited_event_queries: list[Any] = []
    for template in templates:
        latest = (
            select(
                literal(template.id).label("template_id"),
                HealthEvent.id.label("event_id"),
                HealthEvent.date.label("event_date"),
                HealthEvent.next_due_date,
                HealthEvent.next_due_authority,
                literal(False).label("is_primary"),
            )
            .where(
                HealthEvent.animal_id == animal.id,
                HealthEvent.schedule_template_id == template.id,
                HealthEvent.type.in_(
                    [HealthEventType.VACCINE.value, HealthEventType.DEWORMING.value]
                ),
            )
            .order_by(HealthEvent.date.desc(), HealthEvent.id.desc())
            .limit(2)
            .subquery()
        )
        primary_query = (
            select(
                literal(template.id).label("template_id"),
                HealthEvent.id.label("event_id"),
                HealthEvent.date.label("event_date"),
                HealthEvent.next_due_date,
                HealthEvent.next_due_authority,
                literal(True).label("is_primary"),
            )
            .where(
                HealthEvent.animal_id == animal.id,
                HealthEvent.schedule_template_id == template.id,
                HealthEvent.type.in_(
                    [HealthEventType.VACCINE.value, HealthEventType.DEWORMING.value]
                ),
            )
            .order_by(HealthEvent.date, HealthEvent.id)
            .limit(1)
            .subquery()
        )
        # Two bounded index probes (at most three rows) per programme item: the
        # newest two facts determine current status, while the earliest fact
        # permanently anchors the primary-dose booster date even after repeats
        # push it out of the newest-two window.
        limited_event_queries.extend((select(latest), select(primary_query)))

    events_by_template: dict[int, list[Any]] = {template.id: [] for template in templates}
    primary_event_by_template: dict[int, Any] = {}
    if limited_event_queries:
        event_rows = (await db.execute(union_all(*limited_event_queries))).all()
        for event_row in event_rows:
            template_id = int(event_row.template_id)
            if event_row.is_primary:
                primary_event_by_template[template_id] = event_row
            else:
                events_by_template[template_id].append(event_row)

        # Rows written before the immutable template FK are scanned once, in a
        # fixed newest-first window. D7 backfills every unambiguous historical
        # match, so this path is only compatibility for ambiguous/manual legacy
        # data or an out-of-process writer that still omits the canonical id.
        legacy_rows = (
            await db.execute(
                select(
                    HealthEvent.id.label("event_id"),
                    HealthEvent.date.label("event_date"),
                    HealthEvent.next_due_date,
                    HealthEvent.next_due_authority,
                    HealthEvent.type.label("event_type"),
                    HealthEvent.schedule_template_name,
                    HealthEvent.product_name,
                    HealthEvent.disease_target,
                )
                .where(
                    HealthEvent.animal_id == animal.id,
                    HealthEvent.schedule_template_id.is_(None),
                    HealthEvent.type.in_(
                        [HealthEventType.VACCINE.value, HealthEventType.DEWORMING.value]
                    ),
                )
                .order_by(HealthEvent.date.desc(), HealthEvent.id.desc())
                .limit(LEGACY_SCHEDULE_SCAN_LIMIT)
            )
        ).all()
        for legacy_event in legacy_rows:
            for template in templates:
                if _legacy_event_matches(
                    template.name,
                    event_type=legacy_event.event_type,
                    schedule_template_name=legacy_event.schedule_template_name,
                    product_name=legacy_event.product_name,
                    disease_target=legacy_event.disease_target,
                ):
                    events_by_template[template.id].append(legacy_event)
                    legacy_primary = primary_event_by_template.get(template.id)
                    if legacy_primary is None or (
                        legacy_event.event_date,
                        legacy_event.event_id,
                    ) < (
                        legacy_primary.event_date,
                        legacy_primary.event_id,
                    ):
                        primary_event_by_template[template.id] = legacy_event

        for matches in events_by_template.values():
            matches.sort(key=lambda row: (row.event_date, row.event_id), reverse=True)
            del matches[2:]

    rows: list[dict[str, Any]] = []
    for template in templates:
        first_due = (
            add_months(dob, template.first_dose_age_months)
            if dob and template.first_dose_age_months is not None
            else None
        )
        done = events_by_template[template.id]
        last_event = done[0] if done else None
        last_done = last_event.event_date if last_event is not None else None
        # Before any dose, show the planned DOB-derived booster date. Once the
        # first real dose exists, its actual administration date becomes the
        # anchor; otherwise a late primary dose can make its booster appear to
        # have happened in the past and jump straight to the repeat cadence.
        primary_event = primary_event_by_template.get(template.id)
        first_administered = primary_event.event_date if primary_event is not None else None
        if template.booster_weeks and first_administered is not None:
            booster_due = first_administered + timedelta(weeks=template.booster_weeks)
        else:
            booster_due = (
                first_due + timedelta(weeks=template.booster_weeks)
                if first_due and template.booster_weeks
                else None
            )
        next_due = None
        authoritative_next_due = (
            last_event.next_due_date
            if last_event is not None
            and last_event.next_due_date is not None
            and last_event.next_due_authority
            else None
        )
        if authoritative_next_due is not None:
            next_due = authoritative_next_due
        elif len(done) == 1 and booster_due is not None:
            next_due = booster_due
        elif last_done and template.repeat_months:
            next_due = add_months(last_done, int(template.repeat_months))
        booster_missed = (
            authoritative_next_due is None
            and len(done) == 1
            and booster_due is not None
            and booster_due < reference_date
        )
        if booster_missed:
            # First dose recorded but the booster window lapsed with no second
            # matching event — DONE would hide the missed booster.
            status = "OVERDUE"
        elif done and not next_due:
            status = "DONE"
        elif done:
            # `not next_due` was handled above, so next_due is set here.
            status = "DONE" if (next_due is not None and next_due >= reference_date) else "OVERDUE"
        elif first_due is None and dob is None and template.first_dose_age_months is not None:
            # Age-based template but the animal's DOB is unknown → no date can
            # be computed; not the same as "due now" (that's for herd-wide
            # recurring items like Deworming, which have no age-based dose).
            status = "UNKNOWN"
        elif first_due is None:
            # Herd-wide recurring item with no age-based first dose (Deworming)
            # and nothing recorded yet → due now.
            status = "OVERDUE"
        elif first_due < reference_date:
            status = "OVERDUE"
        else:
            status = "UPCOMING"
        rows.append(
            {
                "template": template,
                "first_due": first_due,
                "booster_due": booster_due,
                "last_done": last_done,
                "next_due": next_due,
                "status": status,
            }
        )
    return rows
