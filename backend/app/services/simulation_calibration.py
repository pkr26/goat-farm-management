"""Derive simulation assumptions from audited operational farm records."""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from math import pow
from statistics import median
from typing import Literal

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..models import (
    Animal,
    AnimalStatus,
    BreedingOutcome,
    BreedingRecord,
    Farm,
    FeedInventory,
    KiddingRecord,
    KidEntry,
    Transaction,
    WeightRecord,
)
from ..schemas.simulation import CalibrationEvidence, FarmCalibrationOut
from ..simulation.assumptions import MAX_MONEY, SimulationAssumptions
from ..simulation.defaults import System, get_preset
from ..utils import add_months, today

type CalibrationValue = int | float | list[float]
_MAX_HISTORY_ROWS = 20_000


def _age_months(dob: date | None, reference: date) -> int | None:
    if dob is None:
        return None
    months = (reference.year - dob.year) * 12 + reference.month - dob.month
    if reference.day < dob.day:
        months -= 1
    return max(0, months)


def _confidence(
    sample_size: int, *, medium: int = 10, high: int = 30
) -> Literal["low", "medium", "high"]:
    if sample_size >= high:
        return "high"
    if sample_size >= medium:
        return "medium"
    return "low"


def _as_float(value: Decimal | float | int) -> float:
    return float(value)


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _annualized_fraction(events: int, population: int, lookback_months: int) -> float:
    if events <= 0 or population <= 0:
        return 0.0
    observed = min(0.999999, events / population)
    return 1.0 - pow(1.0 - observed, 12.0 / lookback_months)


async def calibrate_farm_assumptions(
    db: AsyncSession,
    farm: Farm,
    *,
    breed: str,
    system: System,
    lookback_months: int,
) -> FarmCalibrationOut:
    """Return a complete preset overlaid with evidence-backed farm estimates."""
    reference_date = today(farm.timezone)
    period_start = add_months(reference_date, -lookback_months)
    assumptions = get_preset(breed, system).model_copy(deep=True)
    assumptions.meta.start_year_month = reference_date.strftime("%Y-%m")
    evidence: list[CalibrationEvidence] = []
    warnings: list[str] = []

    def record(
        path: str,
        previous: CalibrationValue,
        calibrated: CalibrationValue,
        sample_size: int,
        method: str,
        source: str,
        *,
        medium: int = 10,
        high: int = 30,
    ) -> None:
        evidence.append(
            CalibrationEvidence(
                path=path,
                previous_value=previous,
                calibrated_value=calibrated,
                sample_size=sample_size,
                confidence=_confidence(sample_size, medium=medium, high=high),
                method=method,
                source=source,
                period_start=period_start,
                period_end=reference_date,
            )
        )

    # Exact live-herd state stays aggregate in SQL, so a large tenant does not
    # need every animal materialized merely to establish opening balances.
    effective_dob_expr = func.coalesce(Animal.date_of_birth, Animal.estimated_dob)
    age_months = (
        (reference_date.year - func.extract("year", effective_dob_expr)) * 12
        + reference_date.month
        - func.extract("month", effective_dob_expr)
        - case(
            (func.extract("day", effective_dob_expr) > reference_date.day, 1),
            else_=0,
        )
    )
    female = Animal.sex == "F"
    male = Animal.sex == "M"
    known_age = effective_dob_expr.is_not(None)
    doe_adult_age = assumptions.reproduction.age_at_first_breeding_months

    def cohort_count(predicate: ColumnElement[bool], label: str) -> ColumnElement[int]:
        return func.count(Animal.id).filter(predicate).label(label)

    counts_row = (
        await db.execute(
            select(
                cohort_count(female & (~known_age | (age_months >= doe_adult_age)), "does"),
                cohort_count(male & (~known_age | (age_months >= 12)), "bucks"),
                cohort_count(female & known_age & (age_months < 3), "f_kids"),
                cohort_count(
                    female & known_age & (age_months >= 3) & (age_months < 6),
                    "f_weaners",
                ),
                cohort_count(
                    female & known_age & (age_months >= 6) & (age_months < doe_adult_age),
                    "f_growers",
                ),
                cohort_count(male & known_age & (age_months < 3), "m_kids"),
                cohort_count(
                    male & known_age & (age_months >= 3) & (age_months < 6),
                    "m_weaners",
                ),
                cohort_count(
                    male & known_age & (age_months >= 6) & (age_months < 12),
                    "m_growers",
                ),
            ).where(
                Animal.farm_id == farm.id,
                Animal.status == AnimalStatus.ACTIVE.value,
            )
        )
    ).one()
    counts = {key: int(value) for key, value in counts_row._mapping.items()}
    current_head = sum(counts.values())

    # Historical rows are bounded and ordered by their latest economic/life
    # event. Active animals remain in the sample as mortality exposure.
    animal_rows = (
        await db.execute(
            select(
                Animal.id,
                Animal.sex,
                Animal.date_of_birth,
                Animal.estimated_dob,
                Animal.status,
                Animal.status_date,
                Animal.purchase_date,
                Animal.purchase_price,
                Animal.sale_price,
                Animal.cull_candidate,
            )
            .where(
                Animal.farm_id == farm.id,
                or_(
                    Animal.status == AnimalStatus.ACTIVE.value,
                    Animal.purchase_date.between(period_start, reference_date),
                    Animal.status_date.between(period_start, reference_date),
                ),
            )
            .order_by(
                func.coalesce(
                    Animal.status_date,
                    Animal.purchase_date,
                    Animal.date_of_birth,
                    Animal.estimated_dob,
                ).desc(),
                Animal.id.desc(),
            )
            .limit(_MAX_HISTORY_ROWS + 1)
        )
    ).all()
    if len(animal_rows) > _MAX_HISTORY_ROWS:
        animal_rows = animal_rows[:_MAX_HISTORY_ROWS]
        warnings.append(
            f"Animal-history calibration used the {_MAX_HISTORY_ROWS:,} most recent rows; "
            "current herd counts remain exact."
        )
    count_mapping = {
        "does": "does",
        "bucks": "bucks",
        "f_kids": "female_kids",
        "f_weaners": "female_weaners",
        "f_growers": "female_growers",
        "m_kids": "male_kids",
        "m_weaners": "male_weaners",
        "m_growers": "male_growers",
    }
    for source_key, target_key in count_mapping.items():
        count_previous = int(getattr(assumptions.herd, target_key))
        count_calibrated = counts[source_key]
        setattr(assumptions.herd, target_key, count_calibrated)
        record(
            f"herd.{target_key}",
            count_previous,
            count_calibrated,
            current_head,
            "Exact ACTIVE-animal cohort count on the reference date",
            "animals",
            medium=1,
            high=1,
        )
    # The observed flock is an opening balance, not evidence of the farmer's
    # permanent expansion ceiling. Preserve the preset plan unless the live doe
    # count already exceeds it; otherwise calibrating a 2-doe farm would force
    # every future female replacement to be sold forever.
    assumptions.herd.max_breeding_does = max(
        assumptions.herd.max_breeding_does,
        counts["does"],
    )
    if not current_head:
        warnings.append("No active animals were found; herd counts were calibrated to zero.")

    weight_rows = (
        await db.execute(
            select(
                WeightRecord.animal_id,
                WeightRecord.date,
                WeightRecord.weight_kg,
                Animal.sex,
                Animal.date_of_birth,
                Animal.estimated_dob,
            )
            .join(Animal, Animal.id == WeightRecord.animal_id)
            .where(
                Animal.farm_id == farm.id,
                WeightRecord.date >= period_start,
                WeightRecord.date <= reference_date,
            )
            .order_by(WeightRecord.date.desc(), WeightRecord.id.desc())
            .limit(_MAX_HISTORY_ROWS + 1)
        )
    ).all()
    if len(weight_rows) > _MAX_HISTORY_ROWS:
        weight_rows = weight_rows[:_MAX_HISTORY_ROWS]
        warnings.append(f"Weight calibration used the {_MAX_HISTORY_ROWS:,} most recent records.")
    weights_by_age: dict[int, list[float]] = defaultdict(list)
    latest_weight: dict[int, tuple[date, float, str, date | None]] = {}
    weight_history: dict[int, list[tuple[date, float]]] = defaultdict(list)
    for weight_row in weight_rows:
        dob = weight_row.date_of_birth or weight_row.estimated_dob
        age = _age_months(dob, weight_row.date)
        weight = float(weight_row.weight_kg)
        if age is not None and 0 <= age <= 12:
            weights_by_age[age].append(weight)
        weight_history[weight_row.animal_id].append((weight_row.date, weight))
        if weight_row.animal_id not in latest_weight:
            latest_weight[weight_row.animal_id] = (
                weight_row.date,
                weight,
                weight_row.sex,
                dob,
            )

    observed_curve_points = sum(len(values) for values in weights_by_age.values())
    if observed_curve_points >= 5 and len(weights_by_age) >= 3:
        previous_curve = list(assumptions.growth.weight_by_age_months)
        curve = previous_curve.copy()
        for age, values in weights_by_age.items():
            curve[age] = median(values)
        # Isotonic upper envelope prevents noisy field measurements from
        # creating an impossible shrinking weight curve.
        for index in range(1, len(curve)):
            curve[index] = max(curve[index], curve[index - 1])
        assumptions.growth.weight_by_age_months = curve
        assumptions.growth.birth_weight_kg = curve[0]
        record(
            "growth.weight_by_age_months",
            previous_curve,
            curve,
            observed_curve_points,
            "Median recorded weight by whole age-month with monotonic smoothing",
            "weight_records",
        )

    latest_adult_weights: dict[str, list[float]] = {"F": [], "M": []}
    for measured_on, weight, sex, dob in latest_weight.values():
        age = _age_months(dob, measured_on)
        if age is None or age >= 24:
            latest_adult_weights[sex].append(weight)
    yearling_max = max(assumptions.growth.weight_by_age_months)
    for sex, field_name in (("F", "adult_weight_doe_kg"), ("M", "adult_weight_buck_kg")):
        values = latest_adult_weights[sex]
        if len(values) >= 3:
            adult_previous = float(getattr(assumptions.growth, field_name))
            adult_calibrated = max(yearling_max, median(values))
            setattr(assumptions.growth, field_name, adult_calibrated)
            record(
                f"growth.{field_name}",
                adult_previous,
                adult_calibrated,
                len(values),
                "Median latest adult weight per animal",
                "weight_records",
            )

    breeding_rows = (
        await db.execute(
            select(BreedingRecord.outcome, BreedingRecord.breeding_date)
            .where(
                BreedingRecord.farm_id == farm.id,
                BreedingRecord.breeding_date >= period_start,
                BreedingRecord.breeding_date <= reference_date,
            )
            .order_by(BreedingRecord.breeding_date.desc())
            .limit(_MAX_HISTORY_ROWS + 1)
        )
    ).all()
    if len(breeding_rows) > _MAX_HISTORY_ROWS:
        breeding_rows = breeding_rows[:_MAX_HISTORY_ROWS]
        warnings.append(
            f"Reproduction calibration used the {_MAX_HISTORY_ROWS:,} most recent services."
        )
    assessed = [
        breeding_row
        for breeding_row in breeding_rows
        if breeding_row.outcome
        in {
            BreedingOutcome.CONFIRMED_PREGNANT.value,
            BreedingOutcome.FAILED.value,
            BreedingOutcome.ABORTED.value,
        }
    ]
    if len(assessed) >= 5:
        conceived = sum(
            breeding_row.outcome
            in {
                BreedingOutcome.CONFIRMED_PREGNANT.value,
                BreedingOutcome.ABORTED.value,
            }
            for breeding_row in assessed
        )
        conception_previous = assumptions.reproduction.conception_rate
        conception_calibrated = conceived / len(assessed)
        assumptions.reproduction.conception_rate = conception_calibrated
        record(
            "reproduction.conception_rate",
            conception_previous,
            conception_calibrated,
            len(assessed),
            "Pregnant-or-aborted services divided by assessed services",
            "breeding_records",
        )

    kidding_rows = (
        await db.execute(
            select(
                KiddingRecord.id,
                KiddingRecord.doe_id,
                KiddingRecord.date,
                BreedingRecord.breeding_date,
                KidEntry.sex,
                KidEntry.status,
                KidEntry.birth_weight,
            )
            .join(
                BreedingRecord,
                BreedingRecord.id == KiddingRecord.breeding_record_id,
            )
            .join(KidEntry, KidEntry.kidding_record_id == KiddingRecord.id)
            .where(
                KiddingRecord.farm_id == farm.id,
                KiddingRecord.date >= period_start,
                KiddingRecord.date <= reference_date,
            )
            .order_by(KiddingRecord.date.desc(), KiddingRecord.id.desc())
            .limit(_MAX_HISTORY_ROWS + 1)
        )
    ).all()
    if len(kidding_rows) > _MAX_HISTORY_ROWS:
        kidding_rows = kidding_rows[:_MAX_HISTORY_ROWS]
        warnings.append(
            f"Kidding calibration used the {_MAX_HISTORY_ROWS:,} most recent kid records."
        )
    kids_by_kidding: dict[int, list[object]] = defaultdict(list)
    kidding_meta: dict[int, tuple[date, date]] = {}
    for kid_row in kidding_rows:
        kids_by_kidding[kid_row.id].append(kid_row)
        kidding_meta[kid_row.id] = (kid_row.date, kid_row.breeding_date)
    if len(kids_by_kidding) >= 5:
        litter_sizes = [len(kids) for kids in kids_by_kidding.values()]
        litter_previous = assumptions.reproduction.litter_size
        litter_calibrated = _clamp(sum(litter_sizes) / len(litter_sizes), 0.5, 4.0)
        assumptions.reproduction.litter_size = litter_calibrated
        record(
            "reproduction.litter_size",
            litter_previous,
            litter_calibrated,
            len(litter_sizes),
            "Mean total kids recorded per kidding",
            "kidding_records/kid_entries",
        )
        gestation_days = [(kidding - breeding).days for kidding, breeding in kidding_meta.values()]
        valid_gestations = [days for days in gestation_days if 90 <= days <= 220]
        if valid_gestations:
            previous_gestation = assumptions.reproduction.gestation_months
            calibrated_gestation = min(7, max(1, round(median(valid_gestations) / 30.44)))
            assumptions.reproduction.gestation_months = calibrated_gestation
            record(
                "reproduction.gestation_months",
                previous_gestation,
                calibrated_gestation,
                len(valid_gestations),
                "Median breeding-to-kidding interval rounded to model months",
                "breeding_records/kidding_records",
            )
    kid_count = len(kidding_rows)
    recorded_birth_weights = [
        float(kid_row.birth_weight)
        for kid_row in kidding_rows
        if kid_row.birth_weight is not None and kid_row.birth_weight > 0.0
    ]
    if len(recorded_birth_weights) >= 5:
        previous_birth_weight = assumptions.growth.birth_weight_kg
        calibrated_birth_weight = median(recorded_birth_weights)
        curve = list(assumptions.growth.weight_by_age_months)
        curve[0] = calibrated_birth_weight
        for index in range(1, len(curve)):
            curve[index] = max(curve[index], curve[index - 1])
        assumptions.growth.birth_weight_kg = calibrated_birth_weight
        assumptions.growth.weight_by_age_months = curve
        assumptions.growth.adult_weight_doe_kg = max(
            assumptions.growth.adult_weight_doe_kg, curve[-1]
        )
        assumptions.growth.adult_weight_buck_kg = max(
            assumptions.growth.adult_weight_buck_kg, curve[-1]
        )
        for item in evidence:
            if item.path == "growth.weight_by_age_months":
                item.calibrated_value = curve
                item.method += "; age zero set from median recorded birth weight"
                item.source += "/kid_entries"
                break
        record(
            "growth.birth_weight_kg",
            previous_birth_weight,
            calibrated_birth_weight,
            len(recorded_birth_weights),
            "Median recorded live-born kid birth weight",
            "kid_entries",
        )
    if kid_count >= 10:
        stillborn = sum(kid_row.status == "STILLBORN" for kid_row in kidding_rows)
        alive_rows = [kid_row for kid_row in kidding_rows if kid_row.status != "STILLBORN"]
        female_alive = sum(kid_row.sex == "F" for kid_row in alive_rows)
        previous_stillbirth = assumptions.reproduction.stillbirth_rate
        assumptions.reproduction.stillbirth_rate = stillborn / kid_count
        record(
            "reproduction.stillbirth_rate",
            previous_stillbirth,
            assumptions.reproduction.stillbirth_rate,
            kid_count,
            "Stillborn kid entries divided by all kid entries",
            "kid_entries",
        )
        if alive_rows:
            previous_sex_ratio = assumptions.reproduction.sex_ratio_female
            assumptions.reproduction.sex_ratio_female = female_alive / len(alive_rows)
            record(
                "reproduction.sex_ratio_female",
                previous_sex_ratio,
                assumptions.reproduction.sex_ratio_female,
                len(alive_rows),
                "Female live-born entries divided by all live-born entries",
                "kid_entries",
            )
        died = sum(kid_row.status == "DIED" for kid_row in alive_rows)
        previous_kid_mortality = assumptions.mortality.kid_pre_weaning
        # The engine expects an annualized class rate but each kid is exposed
        # to the pre-weaning class for three model months, not for the whole
        # historical lookback window.
        calibrated_kid_mortality = _annualized_fraction(died, len(alive_rows), 3)
        assumptions.mortality.kid_pre_weaning = min(0.9, calibrated_kid_mortality)
        record(
            "mortality.kid_pre_weaning",
            previous_kid_mortality,
            assumptions.mortality.kid_pre_weaning,
            len(alive_rows),
            "Dependent-kid deaths annualized from three months of pre-weaning exposure",
            "kid_entries",
        )

    # Status-history mortality is exposure-approximate because the operational
    # schema does not store daily animal-at-risk snapshots. The method and low
    # sample confidence make that limitation visible to the user.
    mortality_population: dict[str, int] = defaultdict(int)
    mortality_deaths: dict[str, int] = defaultdict(int)
    for mortality_row in animal_rows:
        event_date = mortality_row.status_date or reference_date
        dob = mortality_row.date_of_birth or mortality_row.estimated_dob
        age = _age_months(dob, event_date)
        if age is None or age >= 12:
            group = "adult"
        elif age >= 6:
            group = "grower"
        elif age >= 3:
            group = "kid_post_weaning"
        else:
            continue  # kid_entries are the less ambiguous pre-weaning source
        mortality_population[group] += 1
        if (
            mortality_row.status == AnimalStatus.DEAD.value
            and mortality_row.status_date is not None
            and period_start <= mortality_row.status_date <= reference_date
        ):
            mortality_deaths[group] += 1
    for group, field_name in (
        ("adult", "adult"),
        ("grower", "grower"),
        ("kid_post_weaning", "kid_post_weaning"),
    ):
        population = mortality_population[group]
        if population >= 10:
            mortality_previous = float(getattr(assumptions.mortality, field_name))
            mortality_calibrated = min(
                0.9,
                _annualized_fraction(mortality_deaths[group], population, lookback_months),
            )
            setattr(assumptions.mortality, field_name, mortality_calibrated)
            record(
                f"mortality.{field_name}",
                mortality_previous,
                mortality_calibrated,
                population,
                "Terminal deaths divided by observed class population, annualized",
                "animals",
            )

    female_purchase_prices: list[float] = []
    male_purchase_prices: list[float] = []
    sale_prices_per_kg: list[tuple[date, float]] = []
    cull_doe_prices_per_kg: list[float] = []
    cull_buck_prices_per_kg: list[float] = []
    for pricing_row in animal_rows:
        if (
            pricing_row.purchase_price is not None
            and pricing_row.purchase_date is not None
            and period_start <= pricing_row.purchase_date <= reference_date
        ):
            purchase_age = _age_months(
                pricing_row.date_of_birth or pricing_row.estimated_dob,
                pricing_row.purchase_date,
            )
            adult_age = (
                assumptions.reproduction.age_at_first_breeding_months
                if pricing_row.sex == "F"
                else 12
            )
            # Adult foundation-stock prices must not be diluted by kid or
            # weaner purchases. Unknown-age purchases follow the operational
            # snapshot convention and count as adults.
            if purchase_age is None or purchase_age >= adult_age:
                target = female_purchase_prices if pricing_row.sex == "F" else male_purchase_prices
                target.append(_as_float(pricing_row.purchase_price))
        if (
            pricing_row.sale_price is None
            or pricing_row.status_date is None
            or not period_start <= pricing_row.status_date <= reference_date
        ):
            continue
        measured = next(
            (
                weight
                for measured_on, weight in weight_history.get(pricing_row.id, [])
                if measured_on <= pricing_row.status_date
            ),
            None,
        )
        if measured is None or measured <= 0.0:
            continue
        unit_price = _as_float(pricing_row.sale_price) / measured
        if pricing_row.cull_candidate and pricing_row.sex == "F":
            cull_doe_prices_per_kg.append(unit_price)
        elif pricing_row.cull_candidate and pricing_row.sex == "M":
            cull_buck_prices_per_kg.append(unit_price)
        else:
            sale_prices_per_kg.append((pricing_row.status_date, unit_price))

    for values, field_name in (
        (female_purchase_prices, "doe_purchase_price"),
        (male_purchase_prices, "buck_purchase_price"),
    ):
        if len(values) >= 3:
            purchase_previous = float(getattr(assumptions.herd, field_name))
            purchase_calibrated = min(MAX_MONEY, median(values))
            setattr(assumptions.herd, field_name, purchase_calibrated)
            record(
                f"herd.{field_name}",
                purchase_previous,
                purchase_calibrated,
                len(values),
                "Median recorded per-head purchase price",
                "animals",
            )
    if len(sale_prices_per_kg) >= 5:
        sale_previous = assumptions.sales.meat_price_per_kg
        sale_calibrated = min(MAX_MONEY, median([price for _sold_on, price in sale_prices_per_kg]))
        assumptions.sales.meat_price_per_kg = sale_calibrated
        record(
            "sales.meat_price_per_kg",
            sale_previous,
            sale_calibrated,
            len(sale_prices_per_kg),
            "Median sale amount divided by latest pre-sale recorded live weight",
            "animals/weight_records",
        )
        by_month: dict[int, list[float]] = defaultdict(list)
        for sold_on, price in sale_prices_per_kg:
            by_month[sold_on.month].append(price)
        if len(sale_prices_per_kg) >= 12 and len(by_month) >= 4 and sale_calibrated > 0.0:
            previous_curve = list(assumptions.sales.monthly_meat_price_multipliers)
            curve = [
                _clamp(median(by_month[month]) / sale_calibrated, 0.25, 4.0)
                if month in by_month
                else 1.0
                for month in range(1, 13)
            ]
            assumptions.sales.monthly_meat_price_multipliers = curve
            record(
                "sales.monthly_meat_price_multipliers",
                previous_curve,
                curve,
                len(sale_prices_per_kg),
                "Calendar-month median live-weight price divided by overall median",
                "animals/weight_records",
            )
    for values, field_name in (
        (cull_doe_prices_per_kg, "cull_doe_price_per_kg"),
        (cull_buck_prices_per_kg, "cull_buck_price_per_kg"),
    ):
        if len(values) >= 3:
            cull_previous = float(getattr(assumptions.sales, field_name))
            cull_calibrated = min(MAX_MONEY, median(values))
            setattr(assumptions.sales, field_name, cull_calibrated)
            record(
                f"sales.{field_name}",
                cull_previous,
                cull_calibrated,
                len(values),
                "Median cull sale amount divided by latest pre-sale live weight",
                "animals/weight_records",
            )

    transaction_rows = (
        await db.execute(
            select(
                Transaction.date,
                Transaction.type,
                Transaction.category,
                Transaction.amount,
                Transaction.feed_quantity_kg,
                Transaction.feed_unit_price_per_kg,
                FeedInventory.category.label("feed_category"),
            )
            .outerjoin(FeedInventory, FeedInventory.id == Transaction.feed_inventory_id)
            .where(
                Transaction.farm_id == farm.id,
                Transaction.date >= period_start,
                Transaction.date <= reference_date,
                Transaction.voided_at.is_(None),
            )
            .order_by(Transaction.date.desc(), Transaction.id.desc())
            .limit(_MAX_HISTORY_ROWS + 1)
        )
    ).all()
    if len(transaction_rows) > _MAX_HISTORY_ROWS:
        transaction_rows = transaction_rows[:_MAX_HISTORY_ROWS]
        warnings.append(f"Cost calibration used the {_MAX_HISTORY_ROWS:,} most recent ledger rows.")
    feed_spend: dict[str, float] = defaultdict(float)
    feed_quantity: dict[str, float] = defaultdict(float)
    category_expense: dict[str, float] = defaultdict(float)
    for transaction_row in transaction_rows:
        if transaction_row.type != "EXPENSE":
            continue
        category_expense[transaction_row.category] += _as_float(transaction_row.amount)
        if (
            transaction_row.feed_category is not None
            and transaction_row.feed_quantity_kg is not None
            and transaction_row.feed_unit_price_per_kg is not None
        ):
            quantity = _as_float(transaction_row.feed_quantity_kg)
            feed_quantity[transaction_row.feed_category] += quantity
            feed_spend[transaction_row.feed_category] += quantity * _as_float(
                transaction_row.feed_unit_price_per_kg
            )
    for feed_category, field_name in (
        ("ROUGHAGE_WET", "purchased_green_price_per_kg"),
        ("ROUGHAGE_DRY", "dry_price_per_kg"),
        ("CONCENTRATE", "concentrate_price_per_kg"),
    ):
        quantity = feed_quantity[feed_category]
        if quantity > 0.0:
            feed_previous = float(getattr(assumptions.feed, field_name))
            feed_calibrated = min(MAX_MONEY, feed_spend[feed_category] / quantity)
            setattr(assumptions.feed, field_name, feed_calibrated)
            sample_size = sum(
                1
                for transaction_row in transaction_rows
                if transaction_row.feed_category == feed_category
                and transaction_row.feed_quantity_kg is not None
                and transaction_row.feed_unit_price_per_kg is not None
            )
            record(
                f"feed.{field_name}",
                feed_previous,
                feed_calibrated,
                sample_size,
                "Quantity-weighted structured feed purchase unit price",
                "transactions/feed_inventory",
            )

    labour_total = category_expense["LABOUR"]
    if labour_total > 0.0:
        labour_previous = assumptions.costs.labour_per_month
        labour_calibrated = min(MAX_MONEY, labour_total / lookback_months)
        assumptions.costs.labour_per_month = labour_calibrated
        sample_size = sum(
            1 for transaction_row in transaction_rows if transaction_row.category == "LABOUR"
        )
        record(
            "costs.labour_per_month",
            labour_previous,
            labour_calibrated,
            sample_size,
            "Total labour expense divided by calibration months",
            "transactions",
        )
    vet_total = category_expense["VET"] + category_expense["MEDICINE"]
    if vet_total > 0.0 and current_head > 0:
        vet_previous = assumptions.costs.vet_per_animal_per_year
        vet_calibrated = min(MAX_MONEY, vet_total * 12.0 / lookback_months / current_head)
        assumptions.costs.vet_per_animal_per_year = vet_calibrated
        sample_size = sum(
            1
            for transaction_row in transaction_rows
            if transaction_row.category in {"VET", "MEDICINE"}
        )
        record(
            "costs.vet_per_animal_per_year",
            vet_previous,
            vet_calibrated,
            sample_size,
            "Annualized vet and medicine spend divided by current active head",
            "transactions/animals",
        )
    misc_total = category_expense["OTHER"]
    if misc_total > 0.0:
        misc_previous = assumptions.costs.misc_overhead_per_month
        misc_calibrated = min(MAX_MONEY, misc_total / lookback_months)
        assumptions.costs.misc_overhead_per_month = misc_calibrated
        sample_size = sum(
            1 for transaction_row in transaction_rows if transaction_row.category == "OTHER"
        )
        record(
            "costs.misc_overhead_per_month",
            misc_previous,
            misc_calibrated,
            sample_size,
            "Total other operating expense divided by calibration months",
            "transactions",
        )

    covered_groups = {item.path.split(".", maxsplit=1)[0] for item in evidence}
    target_groups = {"herd", "growth", "reproduction", "mortality", "sales", "feed", "costs"}
    missing_groups = sorted(target_groups - covered_groups)
    if missing_groups:
        warnings.append(
            "No sufficient farm evidence for: "
            + ", ".join(missing_groups)
            + "; preset values remain."
        )
    low_confidence = sum(item.confidence == "low" for item in evidence)
    if low_confidence:
        warnings.append(
            f"{low_confidence} calibrated input(s) have low confidence; "
            "review them before lending or investment decisions."
        )

    validated = SimulationAssumptions.model_validate(assumptions.model_dump())
    return FarmCalibrationOut(
        assumptions=validated,
        evidence=evidence,
        warnings=warnings,
        coverage_score=len(covered_groups & target_groups) / len(target_groups),
        reference_date=reference_date,
        lookback_months=lookback_months,
    )
