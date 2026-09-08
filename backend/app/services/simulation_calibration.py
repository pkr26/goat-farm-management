"""Derive simulation assumptions from audited operational farm records."""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from math import exp
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
from ..simulation.assumptions import (
    MAX_MONEY,
    MAX_WEIGHT_KG,
    SimulationAssumptions,
    _normalized_seasonality,
)
from ..simulation.defaults import System, get_preset
from ..simulation.engine import _ceil_head_ratio
from ..simulation.market import BAKRID_DATES_BY_YEAR, bakrid_festival_months
from ..utils import add_months, today


def _is_bakrid_month(observed: date) -> bool:
    """Whether a sale date's (year, month) contains Bakrid per the embedded
    lunar calendar — used to deflate festival-premium observations back to the
    plain market level the engine's uplift will re-apply."""
    festival_month = BAKRID_DATES_BY_YEAR.get(observed.year)
    return festival_month is not None and festival_month[0] == observed.month


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


def _months_between(start: date, end: date) -> float:
    """Fractional months from ``start`` to ``end`` (0 when the span is empty)."""
    if end <= start:
        return 0.0
    return (end - start).days / 30.44  # same convention as the engine's DAYS_PER_MONTH


def _annual_fraction_from_exposure(deaths: int, animal_months: float) -> float:
    """Annual mortality fraction from deaths per animal-month at risk.

    Dividing a multi-year death count by a point-in-time headcount is only
    valid when the population is stationary and every member was observed for
    the whole window. Neither holds here: a transient class such as "grower"
    (ages 6-11 months) is emptied by the animals aging out of it, so survivors
    end up counted in a later class while their deaths stay in this one. Rates
    of several hundred percent came out of that mismatch. Exposure time is the
    standard fix: ``deaths / animal-years`` is an incidence rate, and
    ``1 - exp(-rate)`` converts it to the annual fraction the engine wants.
    """
    if deaths <= 0 or animal_months <= 0.0:
        return 0.0
    rate = deaths / (animal_months / 12.0)
    return 1.0 - exp(-rate)


def _isotonic_fit(points: list[tuple[int, float]]) -> list[tuple[int, float]]:
    """Pool-adjacent-violators fit forcing a nondecreasing weight sequence.

    A plain forward ``max`` would ratchet every dip up to the running maximum,
    letting one high outlier dominate every later age. PAVA instead replaces
    each decreasing run with its mean, so a noisy pair is averaged rather than
    one of them being discarded.
    """
    blocks: list[tuple[float, int, list[int]]] = []  # (total, count, ages)
    for age, value in points:
        blocks.append((value, 1, [age]))
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            total_b, count_b, ages_b = blocks.pop()
            total_a, count_a, ages_a = blocks.pop()
            blocks.append((total_a + total_b, count_a + count_b, ages_a + ages_b))
    return [(age, total / count) for total, count, ages in blocks for age in ages]


#: How far a farm's own measurements may stretch or shrink the preset's shape
#: outside the observed age range. The rescale multiplies the *whole* tail, so
#: leaving it unbounded let a single mis-keyed weight ("250" for 25.0 kg) push
#: derived points past the schema ceiling and turn every later calibration
#: request into a 500. A 4x band still lets a genuinely heavier or lighter
#: flock reshape the preset well beyond breed variation.
_CURVE_RESCALE_BAND = (0.25, 4.0)


def _curve_rescale(observed_weight: float, preset_weight: float) -> float:
    """Bounded ratio used to extend the preset's shape past the observations."""
    return _clamp(observed_weight / preset_weight, *_CURVE_RESCALE_BAND)


def _curve_from_observations(
    observed: dict[int, float],
    preset: list[float],
) -> list[float]:
    """Build a 0..12 month weight curve that actually honours the farm's data.

    The previous implementation wrote the observed medians into a copy of the
    breed preset and then took a forward ``max`` over the whole mixed array.
    Because the unobserved slots still held preset weights, a farm whose stock
    is smaller than the preset had every one of its own measurements ratcheted
    back up to the preset value it sat next to — the endpoint returned the
    preset and labelled it as the farm's evidence.

    Here the isotonic fit runs over the observed ages only. Those fitted points
    are then honoured exactly; ages between them are linearly interpolated, and
    ages outside them keep the preset's *shape* rescaled to meet the nearest
    fitted point, so the curve stays continuous and nondecreasing without any
    preset weight surviving as if it were a measurement.
    """
    fitted = _isotonic_fit(sorted(observed.items()))
    if not fitted:
        return list(preset)
    lookup = dict(fitted)
    ages = [age for age, _ in fitted]
    first_age, last_age = ages[0], ages[-1]
    curve: list[float] = []
    for age in range(len(preset)):
        if age in lookup:
            curve.append(lookup[age])
        elif age < first_age:
            # Preset shape rescaled so it lands exactly on the first fitted
            # point. WeightKg is gt=0, so the denominator is never zero.
            curve.append(preset[age] * _curve_rescale(lookup[first_age], preset[first_age]))
        elif age > last_age:
            curve.append(preset[age] * _curve_rescale(lookup[last_age], preset[last_age]))
        else:
            lower = max(a for a in ages if a < age)
            upper = min(a for a in ages if a > age)
            span = upper - lower
            curve.append(lookup[lower] + (lookup[upper] - lookup[lower]) * (age - lower) / span)
    # Interpolating between nondecreasing anchors and rescaling a nondecreasing
    # preset are both monotone, so this only absorbs float noise.
    for index in range(1, len(curve)):
        curve[index] = max(curve[index], curve[index - 1])
    # The observed anchors themselves are only bounded by the weight_records
    # CHECK (0, 1000]; a legal-but-mistyped value could still land a rescaled
    # tail point above the assumption schema's own ceiling, and the endpoint
    # turns that ValidationError into a 500. Keep the result inside the domain
    # the model accepts.
    return [min(value, float(MAX_WEIGHT_KG)) for value in curve]


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
    if assumptions.sales.festival_sale_months:
        # The preset fills the Bakrid calendar for its own default start; the
        # calibrated run starts today, so re-anchor the lunar months or the
        # uplift keeps firing on the preset's Gregorian months (observed as
        # festival indices landing months away from any actual Bakrid).
        assumptions.sales.festival_sale_months = bakrid_festival_months(
            assumptions.meta.start_year_month, assumptions.meta.horizon_months
        )
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
        curve = _curve_from_observations(
            {age: median(values) for age, values in weights_by_age.items()},
            previous_curve,
        )
        assumptions.growth.weight_by_age_months = curve
        assumptions.growth.birth_weight_kg = curve[0]
        record(
            "growth.weight_by_age_months",
            previous_curve,
            curve,
            observed_curve_points,
            "Median recorded weight per age-month, isotonic-fitted; unobserved ages "
            "interpolated between fitted points and the preset shape rescaled outside them",
            "weight_records",
        )

    latest_adult_weights: dict[str, list[float]] = {"F": [], "M": []}
    for measured_on, weight, sex, dob in latest_weight.values():
        age = _age_months(dob, measured_on)
        if age is None or age >= 24:
            latest_adult_weights[sex].append(weight)
    for sex, field_name in (("F", "adult_weight_doe_kg"), ("M", "adult_weight_buck_kg")):
        values = latest_adult_weights[sex]
        if len(values) >= 3:
            adult_previous = float(getattr(assumptions.growth, field_name))
            adult_calibrated = max(max(assumptions.growth.weight_by_age_months), median(values))
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
                KidEntry.mortality_reported_at,
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
        # Goat plausibility window (planned ~150 days, recorded band 100-200).
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
    # Stillborn kids weigh far less than live-born ones, so including them
    # drags the median down under a method string that promises live-born.
    recorded_birth_weights = [
        float(kid_row.birth_weight)
        for kid_row in kidding_rows
        if kid_row.status != "STILLBORN"
        and kid_row.birth_weight is not None
        and kid_row.birth_weight > 0.0
    ]
    if len(recorded_birth_weights) >= 5:
        previous_birth_weight = assumptions.growth.birth_weight_kg
        calibrated_birth_weight = median(recorded_birth_weights)
        previous_curve = list(assumptions.growth.weight_by_age_months)
        curve = list(previous_curve)
        curve[0] = calibrated_birth_weight
        for index in range(1, len(curve)):
            curve[index] = max(curve[index], curve[index - 1])
        assumptions.growth.birth_weight_kg = calibrated_birth_weight
        assumptions.growth.weight_by_age_months = curve
        curve_evidence = next(
            (item for item in evidence if item.path == "growth.weight_by_age_months"), None
        )
        if curve_evidence is not None:
            curve_evidence.calibrated_value = curve
            curve_evidence.method += "; age zero set from median recorded birth weight"
            curve_evidence.source += "/kid_entries"
        elif curve != previous_curve:
            # Without the growth-curve branch there is no row to amend, yet the
            # forward max above can still lift later ages. Record that change
            # rather than mutating the returned curve with no stated basis.
            record(
                "growth.weight_by_age_months",
                previous_curve,
                curve,
                len(recorded_birth_weights),
                "Age zero set from median recorded live-born birth weight, "
                "later ages raised only where the curve would otherwise decrease",
                "kid_entries",
            )
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
        # ReproductionAssumptions.stillbirth_rate is le=0.5. An unclamped ratio
        # from a disease year sailed past that ceiling and made the final
        # model_validate — and therefore the whole endpoint — fail.
        observed_stillbirth = stillborn / kid_count
        assumptions.reproduction.stillbirth_rate = min(0.5, observed_stillbirth)
        if observed_stillbirth > 0.5:
            warnings.append(
                f"Observed stillbirth rate {observed_stillbirth:.0%} exceeds the model "
                "ceiling of 50%; it was capped at 50%."
            )
        record(
            "reproduction.stillbirth_rate",
            previous_stillbirth,
            assumptions.reproduction.stillbirth_rate,
            kid_count,
            "Stillborn kid entries divided by all kid entries, capped at the model ceiling",
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
        # A kid born last month cannot yet have died of anything in months two
        # or three of its pre-weaning life. Counting it in the denominator
        # dilutes the rate, so only kiddings old enough to have completed the
        # exposure are eligible.
        weaning_cutoff = add_months(reference_date, -3)
        weaned_rows = [row for row in alive_rows if row.date <= weaning_cutoff]
        if weaned_rows:
            died = sum(
                kid_row.status == "DIED"
                and kid_row.mortality_reported_at is not None
                and kid_row.mortality_reported_at <= add_months(kid_row.date, 3)
                for kid_row in weaned_rows
            )
            previous_kid_mortality = assumptions.mortality.kid_pre_weaning
            # The engine consumes kid_pre_weaning as a WHOLE-PHASE rate: the
            # pre-wean class removes exactly this fraction of each crop
            # (engine.py phase_monthly_mortality_rate). Write the observed
            # phase fraction directly — annualizing it (1-(1-x)^(12/3))
            # inflated a 10% observed loss into a 34% modelled loss (3.4x).
            calibrated_kid_mortality = min(0.9, died / len(weaned_rows))
            assumptions.mortality.kid_pre_weaning = calibrated_kid_mortality
            record(
                "mortality.kid_pre_weaning",
                previous_kid_mortality,
                assumptions.mortality.kid_pre_weaning,
                len(weaned_rows),
                "Dependent-kid deaths as the observed whole-phase pre-weaning fraction, "
                "counting only kids born early enough to have completed it and deaths "
                "reported within that three-month window",
                "kid_entries",
            )

    # Class mortality is measured as deaths per animal-month at risk. Counting
    # heads instead put survivors in whatever class they had aged into by the
    # reference date while their class-mates' deaths stayed behind, so the two
    # transient classes (post-weaning kid, grower) could report a death count
    # larger than the population it was divided by.
    mortality_exposure: dict[str, float] = defaultdict(float)
    mortality_animals: dict[str, int] = defaultdict(int)
    mortality_deaths: dict[str, int] = defaultdict(int)
    # (class, first age-month inclusive, last age-month exclusive; None = open)
    _CLASSES: tuple[tuple[str, int, int | None], ...] = (
        ("kid_post_weaning", 3, 6),
        ("grower", 6, 12),
        ("adult", 12, None),
    )
    for mortality_row in animal_rows:
        dob = mortality_row.date_of_birth or mortality_row.estimated_dob
        died_in_window = (
            mortality_row.status == AnimalStatus.DEAD.value
            and mortality_row.status_date is not None
            and period_start <= mortality_row.status_date <= reference_date
        )
        # An animal stops being at risk when it leaves the herd; an ACTIVE one
        # is at risk right up to the reference date.
        left_on = (
            mortality_row.status_date
            if mortality_row.status != AnimalStatus.ACTIVE.value
            and mortality_row.status_date is not None
            else reference_date
        )
        observed_end = min(reference_date, left_on)
        if dob is None:
            # Unknown age counts as adult for the whole observed span, matching
            # the herd-snapshot convention used above.
            exposure_start = max(period_start, mortality_row.purchase_date or period_start)
            months = _months_between(exposure_start, observed_end)
            if months > 0.0:
                mortality_exposure["adult"] += months
                mortality_animals["adult"] += 1
                if died_in_window:
                    mortality_deaths["adult"] += 1
            continue
        for group, from_age, to_age in _CLASSES:
            class_start = add_months(dob, from_age)
            class_end = add_months(dob, to_age) if to_age is not None else observed_end
            overlap = _months_between(
                max(
                    period_start,
                    mortality_row.purchase_date or period_start,
                    class_start,
                ),
                min(observed_end, class_end),
            )
            if overlap <= 0.0:
                continue
            mortality_exposure[group] += overlap
            mortality_animals[group] += 1
            if died_in_window and class_start <= mortality_row.status_date < (
                add_months(dob, to_age) if to_age is not None else date.max
            ):
                mortality_deaths[group] += 1
    for group, field_name in (
        ("adult", "adult"),
        ("grower", "grower"),
        ("kid_post_weaning", "kid_post_weaning"),
    ):
        # Twelve animal-months is one animal-year: below that a single death
        # implies an absurd rate, so it is not evidence of anything.
        if mortality_exposure[group] >= 12.0 and mortality_animals[group] >= 10:
            mortality_previous = float(getattr(assumptions.mortality, field_name))
            mortality_calibrated = min(
                0.9,
                _annual_fraction_from_exposure(mortality_deaths[group], mortality_exposure[group]),
            )
            setattr(assumptions.mortality, field_name, mortality_calibrated)
            record(
                f"mortality.{field_name}",
                mortality_previous,
                mortality_calibrated,
                mortality_animals[group],
                "Deaths per animal-month at risk in the class, converted to an annual rate",
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
        # cull_candidate is a live worklist flag and is cleared at every
        # terminal transition. CULLED is the durable disposition fact.
        if pricing_row.status == AnimalStatus.CULLED.value and pricing_row.sex == "F":
            cull_doe_prices_per_kg.append(unit_price)
        elif pricing_row.status == AnimalStatus.CULLED.value and pricing_row.sex == "M":
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
        # Observed Bakrid-month prices already embed the festival premium the
        # engine will re-apply through festival_sale_months — deflate those
        # observations back to the plain market level before deriving the
        # base price and the seasonal curve, or the premium counts twice.
        # Only when the run will actually apply festival pricing (a preset
        # carrying an explicit empty festival list would never re-add the
        # premium — deflating it there just biased the base price down ~26%
        # on Bakrid-month observations for nothing).
        festival_active = bool(assumptions.sales.festival_sale_months) or (
            assumptions.sales.eid_month > 0
        )
        festival_deflator = 1.0 / (1.0 + assumptions.sales.eid_price_uplift)

        def _deflated(sold_on: date, price: float) -> float:
            if festival_active and _is_bakrid_month(sold_on):
                return price * festival_deflator
            return price

        deflated_prices = [
            (sold_on, _deflated(sold_on, price)) for sold_on, price in sale_prices_per_kg
        ]
        sale_calibrated = min(MAX_MONEY, median([price for _sold_on, price in deflated_prices]))
        assumptions.sales.meat_price_per_kg = sale_calibrated
        record(
            "sales.meat_price_per_kg",
            sale_previous,
            sale_calibrated,
            len(sale_prices_per_kg),
            "Median sale amount divided by latest pre-sale recorded live weight "
            "(Bakrid-month observations deflated to the plain market level when "
            "festival pricing will re-apply the premium)",
            "animals/weight_records",
        )
        by_month: dict[int, list[float]] = defaultdict(list)
        for sold_on, price in deflated_prices:
            by_month[sold_on.month].append(price)
        if len(sale_prices_per_kg) >= 12 and len(by_month) >= 4 and sale_calibrated > 0.0:
            previous_curve = list(assumptions.sales.monthly_meat_price_multipliers)
            raw_curve = [
                median(by_month[month]) / sale_calibrated if month in by_month else 1.0
                for month in range(1, 13)
            ]
            # Renormalise to mean exactly 1.0: the base price is the annual
            # mean, and a curve averaging 0.9 silently redefines it as a
            # peak-month price (the same fix the schema default factory makes).
            curve = _normalized_seasonality([_clamp(value, 0.25, 4.0) for value in raw_curve])
            assumptions.sales.monthly_meat_price_multipliers = curve
            record(
                "sales.monthly_meat_price_multipliers",
                previous_curve,
                curve,
                len(sale_prices_per_kg),
                "Calendar-month median live-weight price divided by overall median "
                "(festival-deflated, renormalised to mean 1.0)",
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

    # A per-month cost must be divided by the months the ledger actually
    # covers, not by the window the caller happened to ask for. A six-month-old
    # farm queried with the default lookback_months=24 had every recurring cost
    # reported at a quarter of its real level, which is the direction that makes
    # an unviable project look financeable.
    expense_dates = [
        transaction_row.date
        for transaction_row in transaction_rows
        if transaction_row.type == "EXPENSE"
    ]
    if expense_dates:
        observed_months = max(1, round(_months_between(min(expense_dates), reference_date)) + 1)
        cost_months = min(lookback_months, observed_months)
    else:
        cost_months = lookback_months
    if cost_months < lookback_months:
        warnings.append(
            f"Recurring costs were averaged over the {cost_months} month(s) of ledger "
            f"history that exist, not the {lookback_months} month(s) requested."
        )
    cost_basis = f"divided by the {cost_months} month(s) of ledger history"

    labour_total = category_expense["LABOUR"]
    if labour_total > 0.0:
        labour_previous = assumptions.costs.labour_per_month
        # ``labour_per_month`` is a PER-LABOURER wage: the engine charges
        # ``max(1, ceil(adult_females / labour_per_head_threshold)) *
        # labour_per_month`` — labour scales with ADULT breeding females
        # (kids/additional young stock add fractionally to workload; TNAU/
        # NABARD norm is one worker per ~50 does *with progeny*). The ledger
        # only knows the farm's whole labour bill, so it must be split across
        # the labourers that adult-female headcount implies — via the
        # engine's own helper, so the two cannot drift. Splitting on total
        # standing head (or assigning the total directly) makes the engine
        # under- or over-multiply the wage.
        adult_females = counts["does"]
        labour_headcount = (
            max(1, _ceil_head_ratio(adult_females, assumptions.costs.labour_per_head_threshold))
            if adult_females > 0
            else 1
        )
        labour_calibrated = min(MAX_MONEY, labour_total / cost_months / labour_headcount)
        assumptions.costs.labour_per_month = labour_calibrated
        sample_size = sum(
            1 for transaction_row in transaction_rows if transaction_row.category == "LABOUR"
        )
        record(
            "costs.labour_per_month",
            labour_previous,
            labour_calibrated,
            sample_size,
            (
                f"Total labour expense {cost_basis}, then split across the "
                f"{labour_headcount} labourer(s) implied by {adult_females} adult "
                f"female(s) (the engine's labour basis, matching one worker per "
                f"~{assumptions.costs.labour_per_head_threshold} does with progeny)"
            ),
            "transactions",
        )
    vet_total = category_expense["VET"] + category_expense["MEDICINE"]
    if vet_total > 0.0 and current_head > 0:
        vet_previous = assumptions.costs.vet_per_animal_per_year
        vet_calibrated = min(MAX_MONEY, vet_total * 12.0 / cost_months / current_head)
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
            f"Vet and medicine spend {cost_basis}, annualized and divided by active head",
            "transactions/animals",
        )
    misc_total = category_expense["OTHER"]
    if misc_total > 0.0:
        misc_previous = assumptions.costs.misc_overhead_per_month
        misc_calibrated = min(MAX_MONEY, misc_total / cost_months)
        assumptions.costs.misc_overhead_per_month = misc_calibrated
        sample_size = sum(
            1 for transaction_row in transaction_rows if transaction_row.category == "OTHER"
        )
        record(
            "costs.misc_overhead_per_month",
            misc_previous,
            misc_calibrated,
            sample_size,
            f"Total other operating expense {cost_basis}",
            "transactions",
        )

    # SimulationAssumptions._adult_weight_above_yearling_curve requires both
    # adult weights to be at least the heaviest point of the 0-12 month curve.
    # Several branches above can raise that curve, and the adult weights are
    # only recalibrated when three animals aged 24 months or more carry a
    # weight record — a young herd trips the validator and the whole endpoint
    # answered 400. Reconcile once, here, after every curve mutation.
    calibrated_yearling_max = max(assumptions.growth.weight_by_age_months)
    for field_name in ("adult_weight_doe_kg", "adult_weight_buck_kg"):
        if float(getattr(assumptions.growth, field_name)) < calibrated_yearling_max:
            raised_from = float(getattr(assumptions.growth, field_name))
            setattr(assumptions.growth, field_name, calibrated_yearling_max)
            record(
                f"growth.{field_name}",
                raised_from,
                calibrated_yearling_max,
                observed_curve_points,
                "Raised to the heaviest calibrated yearling weight; the breed preset's "
                "adult weight was below the growth this farm actually records",
                "weight_records",
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
