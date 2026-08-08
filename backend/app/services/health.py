"""Health / vaccination schedule."""

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Animal,
    Farm,
    HealthEvent,
    HealthEventType,
    Transaction,
    TransactionCategory,
    TransactionType,
    VaccineTemplate,
)
from ..utils import add_months, money, today

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


def _normalize(text: str) -> str:
    return " ".join(text.lower().replace("+", " + ").split())


def template_name_for_task(title: str, category: str) -> str | None:
    """Canonical template inferred from an auto-generated health task.

    The task is the authoritative link; free text remains useful as a product
    note but cannot silently complete a different programme item.
    """
    key = _normalize(title)
    if category == HealthEventType.DEWORMING.value:
        return "Deworming"
    if "ppr" in key:
        return "PPR"
    if "goat pox" in key or "goatpox" in key:
        return "Goat Pox"
    if "fmd" in key:
        return "FMD"
    if "pre-kidding" in key and "et" in key:
        return "ET + TT pre-kidding"
    if "et" in key and "tetanus" in key:
        return "Enterotoxaemia (ET)"
    return None


def target_matches_template(target: str, template_name: str) -> bool:
    """Validate only an explicitly supplied target; a blank target is filled
    by the linked task's canonical template rather than guessed from a brand."""
    target_key = _normalize(target)
    if not target_key:
        return True
    required = {
        "FMD": ("fmd", "foot and mouth"),
        "PPR": ("ppr", "peste des petits"),
        "Goat Pox": ("goat pox", "goatpox"),
        "Enterotoxaemia (ET)": ("et", "enterotoxaemia", "enterotoxemia"),
        "ET + TT pre-kidding": ("et", "tetanus"),
        "Deworming": ("deworm",),
    }.get(template_name, ())
    if required:
        return any(token in target_key for token in required)
    canonical = _normalize(template_name.split("(")[0])
    return bool(canonical) and canonical in target_key


async def validated_template_name(
    db: AsyncSession, template_name: str | None, event_type: str
) -> str | None:
    """Return an exact seeded template name or reject a free-form schedule override."""
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
    return str(template.name)


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
    """Create one HealthEvent row per animal; total cost divided evenly.
    The first animal absorbs the rounding remainder so the split sums back
    to the recorded total; an explicit ₹0 cost is stored as 0.00, not NULL."""
    exact_total_cost = money(total_cost) if total_cost is not None else None
    costs: list[Decimal | None]
    if exact_total_cost is not None and animals:
        per = money(exact_total_cost / len(animals))
        costs = [per] * len(animals)
        costs[0] = money(exact_total_cost - per * (len(animals) - 1))
    else:
        costs = [None] * len(animals)
    events = []
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
            animal.suspected_scheduled_disease = True
            animal.suspected_disease = disease_target or None
            animal.authority_notified_at = authority_notified_at
            animal.movement_restricted = True
            animal.restriction_reason = "Scheduled-disease suspicion recorded in health log"
    # Book the health-event spend in the ledger. Without
    # this, monthly P&L reports ₹0 medicine/vet spend even when HealthEvent
    # rows carry a cost — a farmer's monthly loss would be understated for
    # every vaccination round. One aggregated Transaction per record_health_event
    # call (matches how the user entered it in the UI: one form, one total).
    # Allocate event ids before creating the source-linked aggregate ledger
    # row. The first event is the stable identity of this one submitted form.
    await db.flush()
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


async def vaccination_schedule_for_animal(db: AsyncSession, animal: Animal) -> list[dict[str, Any]]:
    """Per-animal vaccination schedule from seeded VaccineTemplates.
    Status: DONE (event recorded) / OVERDUE (due date passed) / UPCOMING."""
    farm = await db.get(Farm, animal.farm_id)
    reference_date = today(farm.timezone) if farm is not None else today()
    dob = animal.effective_dob
    events_result = await db.execute(
        select(HealthEvent)
        .where(
            HealthEvent.animal_id == animal.id,
            HealthEvent.type.in_([HealthEventType.VACCINE.value, HealthEventType.DEWORMING.value]),
        )
        .order_by(HealthEvent.date)
    )
    events = list(events_result.scalars().all())

    def _matches(template_name: str, event: HealthEvent) -> bool:
        if event.schedule_template_name:
            return _normalize(event.schedule_template_name) == _normalize(template_name)
        haystack = _normalize(f"{event.product_name or ''} {event.disease_target or ''}")
        # Conservative substring on the FULL normalized template name (sans
        # parenthetical): "PPR" matches an event recorded as "PPR vaccine",
        # but "Goat Pox" no longer matches a product merely containing "goat".
        key = _normalize(template_name.split("(")[0])
        if key.startswith("deworm"):
            # Dewormers are recorded by drug name (Albendazole, ...) with the
            # target left blank, so the haystack rarely contains "deworming" —
            # the event TYPE is the reliable signal for this template.
            return event.type == HealthEventType.DEWORMING.value or "deworm" in haystack
        if key and key in haystack:
            return True
        if any(alias in haystack for alias in _TEMPLATE_ALIASES.get(key, ())):
            return True
        # "Enterotoxaemia (ET)" should also match an event recorded as "ET + TT".
        abbrev_match = re.search(r"\(([^)]+)\)", template_name)
        abbrev = abbrev_match.group(1).strip().lower() if abbrev_match else ""
        return bool(abbrev) and re.search(rf"\b{re.escape(abbrev)}\b", haystack) is not None

    rows: list[dict[str, Any]] = []
    templates_result = await db.execute(select(VaccineTemplate).order_by(VaccineTemplate.id))
    for template in templates_result.scalars():
        if template.first_dose_age_months is None and not template.repeat_months:
            continue  # pregnancy-linked (ET+TT pre-kidding) handled via tasks
        first_due = (
            add_months(dob, template.first_dose_age_months)
            if dob and template.first_dose_age_months is not None
            else None
        )
        booster_due = (
            first_due + timedelta(weeks=template.booster_weeks)
            if first_due and template.booster_weeks
            else None
        )
        done = [e for e in events if _matches(template.name, e)]
        last_done = done[-1].date if done else None
        last_event = done[-1] if done else None
        next_due = None
        if last_event and last_event.next_due_date and last_event.next_due_authority:
            next_due = last_event.next_due_date
        elif last_done and template.repeat_months:
            next_due = add_months(last_done, int(template.repeat_months))
        booster_missed = (
            len(done) == 1
            and bool(template.booster_weeks)
            and last_done is not None
            and last_done + timedelta(weeks=template.booster_weeks or 0) < reference_date
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
