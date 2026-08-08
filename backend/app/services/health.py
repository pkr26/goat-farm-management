"""Health / vaccination schedule."""

import re
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Animal, Farm, HealthEvent, HealthEventType, VaccineTemplate
from ..utils import add_months, today


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
    notes: str,
    purchase_batch_id: int | None = None,
    created_by_id: int | None = None,
) -> list[HealthEvent]:
    """Create one HealthEvent row per animal; total cost divided evenly.
    The first animal absorbs the rounding remainder so the split sums back
    to the recorded total; an explicit ₹0 cost is stored as 0.00, not NULL."""
    costs: list[float | None]
    if total_cost is not None and animals:
        per = round(total_cost / len(animals), 2)
        costs = [per] * len(animals)
        costs[0] = round(total_cost - per * (len(animals) - 1), 2)
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
            notes=notes or None,
            created_by_id=created_by_id,
        )
        db.add(event)
        events.append(event)
    await db.flush()
    return events


# Common trade names / aliases for the seeded vaccine templates (AUDIT 3-1):
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

    def _normalize(text: str) -> str:
        return " ".join(text.lower().split())

    def _matches(template_name: str, event: HealthEvent) -> bool:
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
        next_due = None
        if last_done and template.repeat_months:
            next_due = add_months(last_done, int(template.repeat_months))
        booster_missed = (
            len(done) == 1
            and bool(template.booster_weeks)
            and last_done is not None
            and last_done + timedelta(weeks=template.booster_weeks or 0) < today()
        )
        if booster_missed:
            # First dose recorded but the booster window lapsed with no second
            # matching event — DONE would hide the missed booster.
            status = "OVERDUE"
        elif done and not next_due:
            status = "DONE"
        elif done:
            # `not next_due` was handled above, so next_due is set here.
            status = "DONE" if (next_due is not None and next_due >= today()) else "OVERDUE"
        elif first_due is None and dob is None and template.first_dose_age_months is not None:
            # Age-based template but the animal's DOB is unknown → no date can
            # be computed; not the same as "due now" (that's for herd-wide
            # recurring items like Deworming, which have no age-based dose).
            status = "UNKNOWN"
        elif first_due is None:
            # Herd-wide recurring item with no age-based first dose (Deworming)
            # and nothing recorded yet → due now.
            status = "OVERDUE"
        elif first_due < today():
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
