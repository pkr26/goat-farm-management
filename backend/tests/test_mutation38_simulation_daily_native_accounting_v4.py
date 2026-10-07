"""Actual native daily runs conserve animals, feed and the operator audit ledger."""

import json
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal

import pytest

from app.models.constants import SHIFT_SPLIT
from app.models.feed_rules import GOAT_BUILDING_NAMES
from app.models.lifecycle import LEGAL_BUCKET_TRANSITIONS
from app.models.species import GOAT_PROFILE
from app.permissions import TASK_CATEGORY_ROLE_MAP
from app.services._common import REBREED_AFTER_RESTING_DAYS
from app.simulation.daily_ops import (
    FEED_STORE,
    FEED_STORE_NAME,
    DailyOpsInput,
    DailyOpsResult,
    DailyOpsResultCapExceeded,
    _DailyOpsRun,
    build_daily_ledger,
    run_daily_ops,
)
from app.simulation.feed import DAYS_PER_MONTH


def _animal(tag: str, **updates: object) -> dict[str, object]:
    document: dict[str, object] = {"tag": tag, "sex": "F", "bucket": "FOUNDATION", "age_months": 12}
    document.update(updates)
    return document


def _input(animals: list[dict[str, object]], days: int, **params: object) -> DailyOpsInput:
    policy: dict[str, object] = {
        "conception_rate": 1.0,
        "litter_size_mean": 3.0,
        "stillbirth_rate": 0.0,
        "abortion_rate": 0.0,
        "kid_pre_weaning_mortality": 0.0,
        "kid_post_weaning_mortality": 0.0,
        "grower_annual_mortality": 0.0,
        "adult_annual_mortality": 0.0,
    }
    policy.update(params)
    return DailyOpsInput.model_validate_json(
        json.dumps(
            {
                "start_date": "2026-01-01",
                "horizon_days": days,
                "seed": 37,
                "animals": animals,
                "params": policy,
            }
        )
    )


def _completed(payload: DailyOpsInput, *, cap: int | None = None) -> DailyOpsResult:
    try:
        return run_daily_ops(payload, max_result_head_days=cap)
    except (
        ArithmeticError,
        AttributeError,
        IndexError,
        KeyError,
        TypeError,
        ValueError,
        RuntimeError,
    ) as exc:
        pytest.fail(
            f"A supported ordinary native daily run must complete with an auditable result: {exc}"
        )


def _mixed(days: int) -> DailyOpsInput:
    return _input(
        [
            _animal("B1", sex="M", bucket="BREEDING", age_months=24),
            _animal("D1", bucket="BREEDING"),
            _animal("P1", bucket="PREGNANCY_EARLY", bred_days_ago=99),
            _animal("P2", bucket="DELIVERY", bred_days_ago=149),
            _animal("Q1", bucket="QUARANTINE", days_in_bucket=0),
            _animal("R1", bucket="RESTING", days_in_bucket=29),
            _animal("K1", bucket="RECOVERY", age_months=1, dependent_kid=True),
            _animal("K2", sex="M", bucket="RECOVERY", age_months=0, dependent_kid=True),
            _animal("M1", sex="M", bucket="MALE_KIDS", age_months=8),
            _animal("F1", bucket="FEMALE_KIDS", age_months=5),
        ],
        days,
    )


@pytest.mark.parametrize("days", [7, 70, 250])
def test_native_daily_records_conserve_actual_animals_tasks_and_transition_provenance(
    days: int,
) -> None:
    payload = _mixed(days)
    result = _completed(payload)
    assert len(result.days) == days
    assert result.head_start == len(payload.animals)
    assert result.start_date == payload.start_date.isoformat()
    assert result.seed == payload.seed
    assert [record.day for record in result.days] == list(range(1, days + 1))
    known = {animal.tag for animal in payload.animals}
    active = set(known)
    all_tasks = []
    transitions: Counter[tuple[str, str, str]] = Counter()
    building_days: Counter[str] = Counter()
    births_alive = 0
    births_dead = 0
    exits: Counter[str] = Counter()
    for record in result.days:
        observed_date = (payload.start_date + timedelta(days=record.day - 1)).isoformat()
        assert record.date == observed_date
        for task in record.tasks:
            assert task.day == record.day and task.date == record.date
            assert task.role == TASK_CATEGORY_ROLE_MAP.get(task.category, "MANAGER")
            assert task.building_name == (
                FEED_STORE_NAME
                if task.building == FEED_STORE
                else GOAT_BUILDING_NAMES[task.building]
            )
        all_tasks.extend(record.tasks)
        for birth in record.births:
            assert birth.day == record.day and birth.date == record.date
            assert birth.dam_tag in known
            assert birth.live_kids == sum(kid.status == "ALIVE" for kid in birth.kids)
            for kid in birth.kids:
                assert kid.tag not in known
                known.add(kid.tag)
                if kid.status == "ALIVE":
                    active.add(kid.tag)
                    births_alive += 1
                else:
                    births_dead += 1
        for move in record.moves:
            assert move.day == record.day and move.date == record.date
            assert move.tag in known
            if move.from_bucket is not None:
                assert move.context in LEGAL_BUCKET_TRANSITIONS[(move.from_bucket, move.to_bucket)]
            transitions[(move.from_bucket or "", move.to_bucket, move.context)] += 1
        for exit_ in record.exits:
            assert exit_.day == record.day and exit_.date == record.date
            assert exit_.tag in active
            active.remove(exit_.tag)
            exits[exit_.kind] += 1
        assert sum(row.heads for row in record.occupancy) == len(active)
        assert all(row.heads > 0 for row in record.occupancy)
        building_days.update(row.building for row in record.occupancy)
    assert result.totals.tasks_by_category == Counter(task.category for task in all_tasks)
    assert result.totals.tasks_by_role == Counter(task.role for task in all_tasks)
    assert result.totals.vet_tasks_by_building == Counter(
        task.building for task in all_tasks if task.role == "VET"
    )
    assert result.totals.building_days == building_days
    assert result.totals.kids_born_alive == births_alive
    assert result.totals.kids_born_dead == births_dead
    assert result.totals.deaths == exits["DEAD"]
    assert result.totals.culls == exits["CULLED"]
    assert result.totals.sales == exits["SOLD"]
    assert result.totals.moves == sum(transitions.values())
    assert {
        (r.from_bucket, r.to_bucket, r.context): r.count for r in result.transition_counts
    } == dict(transitions)
    assert {
        journey.tag for journey in result.journeys if journey.final_status == "ACTIVE"
    } == active
    assert len(result.journeys) == len(payload.animals) + births_alive
    explanations = {item.key: item for item in result.explanations}
    assert explanations["exits"].figures["head_final"] == len(active)
    assert explanations["routine"].figures["buildings_occupied_last_day"] == len(
        result.days[-1].occupancy
    )
    assert explanations["buildings"].figures == result.totals.vet_tasks_by_building
    assert explanations["feed"].figures == result.totals.feed_kg_by_recipe
    if result.totals.vet_tasks_by_building:
        busiest = max(
            result.totals.vet_tasks_by_building,
            key=lambda building: result.totals.vet_tasks_by_building[building],
        )
        assert GOAT_BUILDING_NAMES[busiest] in explanations["buildings"].explanation
    assert json.loads(result.model_dump_json())["seed"] == payload.seed


@pytest.mark.parametrize("days", [7, 70, 250])
def test_native_feed_deliveries_follow_shift_occupants_and_prepared_stock_conserves_mass(
    days: int,
) -> None:
    result = _completed(_mixed(days))
    total_by_recipe: defaultdict[str, Decimal] = defaultdict(Decimal)
    shares = list(SHIFT_SPLIT.values())
    for record in result.days:
        delivered: defaultdict[str, Decimal] = defaultdict(Decimal)
        for line in record.feeding:
            assert line.day == record.day
            heads = [line.morning_heads, line.afternoon_heads, line.night_heads]
            quantities = [line.morning_kg, line.afternoon_kg, line.night_kg]
            assert all(head is not None and head >= 0 for head in heads)
            assert line.heads == max(head for head in heads if head is not None)
            assert quantities == [
                round((head or 0) * line.kg_per_head * share, 3)
                for head, share in zip(heads, shares, strict=True)
            ]
            assert Decimal(str(line.daily_kg)) == sum(Decimal(str(q)) for q in quantities)
            assert line.daily_kg >= 0.0
            delivered[line.recipe] += Decimal(str(line.daily_kg))
            total_by_recipe[line.recipe] += Decimal(str(line.daily_kg))
        recipes = (
            set(record.feed_prepared_kg_by_recipe)
            | set(delivered)
            | set(record.feed_unused_kg_by_recipe)
        )
        for recipe in recipes:
            prepared = Decimal(str(record.feed_prepared_kg_by_recipe.get(recipe, 0.0)))
            unused = Decimal(str(record.feed_unused_kg_by_recipe.get(recipe, 0.0)))
            assert prepared == delivered[recipe] + unused
            assert prepared >= 0 and unused >= 0
    assert result.totals.feed_kg_by_recipe == {
        recipe: float(total) for recipe, total in total_by_recipe.items()
    }


def test_native_pregnancy_milestones_and_later_service_match_species_calendar() -> None:
    profile = GOAT_PROFILE
    started = profile.pregnancy_check_after_service_days
    result = _completed(
        _input(
            [
                _animal("D1", bucket="PREGNANCY_EARLY", bred_days_ago=started),
                _animal("B1", sex="M", bucket="BREEDING", age_months=24),
            ],
            250,
        )
    )
    moves = [move for record in result.days for move in record.moves if move.tag == "D1"]
    expected = [
        (1 + profile.pregnancy_late_day - started, "PREGNANCY_LATE"),
        (1 + profile.gestation_days - profile.prepartum_move_lead_days - started, "DELIVERY"),
        (1 + profile.gestation_days - started, "RECOVERY"),
        (1 + profile.gestation_days + profile.weaning_days - started, "RESTING"),
        (
            1
            + profile.gestation_days
            + profile.weaning_days
            + REBREED_AFTER_RESTING_DAYS
            - started,
            "BREEDING",
        ),
        (
            1
            + profile.gestation_days
            + profile.weaning_days
            + REBREED_AFTER_RESTING_DAYS
            + profile.pregnancy_check_after_service_days
            - started,
            "PREGNANCY_EARLY",
        ),
    ]
    assert [(move.day, move.to_bucket) for move in moves] == expected
    vaccines = [
        task
        for record in result.days
        for task in record.tasks
        if task.category == "VACCINE" and task.animals == ["D1"]
    ]
    assert [task.day for task in vaccines] == [
        1 + profile.gestation_days - 40 - started,
        1 + profile.gestation_days - 25 - started,
    ]
    births = [birth for record in result.days for birth in record.births]
    assert [(birth.day, birth.dam_tag, len(birth.kids), birth.live_kids) for birth in births] == [
        (1 + profile.gestation_days - started, "D1", 3, 3)
    ]
    assert [kid.tag for kid in births[0].kids] == ["D1-1", "D1-2", "D1-3"]
    assert result.totals.conceptions == 1
    assert result.totals.failed_services == 0


def test_two_failed_services_follow_the_real_next_heat_and_cull_policy() -> None:
    result = _completed(
        _input(
            [
                _animal("D1", bucket="BREEDING", age_months=24),
                _animal("B1", sex="M", bucket="BREEDING", age_months=24),
            ],
            91,
            conception_rate=0.0,
        )
    )
    services = [
        task
        for record in result.days
        for task in record.tasks
        if task.headline.startswith("Breed D1")
    ]
    # The live species heat-cycle default is 21 days between the negative scan and next service.
    assert [task.day for task in services] == [
        1,
        1 + GOAT_PROFILE.pregnancy_check_after_service_days + 21,
    ]
    assert result.totals.services == 2
    assert result.totals.failed_services == GOAT_PROFILE.failed_services_before_cull
    journey = next(j for j in result.journeys if j.tag == "D1")
    assert journey.exit_kind == "CULLED"
    assert journey.exit_day == 1 + 2 * GOAT_PROFILE.pregnancy_check_after_service_days + 21


def test_native_standing_head_day_cap_accepts_exact_capacity_and_rejects_one_less() -> None:
    payload = _input([_animal("D1"), _animal("B1", sex="M")], 7)
    capacity = len(payload.animals) * payload.horizon_days
    assert len(_completed(payload, cap=capacity).days) == payload.horizon_days
    with pytest.raises(DailyOpsResultCapExceeded) as rejected:
        run_daily_ops(payload, max_result_head_days=capacity - 1)
    assert rejected.value.cap == capacity - 1
    assert rejected.value.head_days == capacity


def test_native_annual_hazards_compound_to_the_configured_adult_and_grower_loss() -> None:
    payload = _input(
        [_animal("D1")],
        7,
        adult_annual_mortality=0.5,
        grower_annual_mortality=0.4,
        kid_post_weaning_mortality=0.2,
    )
    run = _DailyOpsRun(payload)
    assert 1.0 - (1.0 - run.adult_hazard) ** 365 == pytest.approx(
        payload.params.adult_annual_mortality, abs=1e-12
    )
    assert 1.0 - (1.0 - run.grower_hazard) ** 365 == pytest.approx(
        payload.params.grower_annual_mortality, abs=1e-12
    )
    exposed_days = round(6 * DAYS_PER_MONTH) - GOAT_PROFILE.weaning_days
    assert 1.0 - (1.0 - run.weaner_hazard) ** exposed_days == pytest.approx(
        payload.params.kid_post_weaning_mortality, abs=1e-12
    )


def test_native_full_ledger_preserves_real_duties_buildings_and_animal_journey_history() -> None:
    result = _completed(_mixed(70))
    try:
        ledger = build_daily_ledger(result)
    except (ArithmeticError, IndexError, KeyError, TypeError, ValueError) as exc:
        pytest.fail(f"A completed native result must produce its full audit ledger: {exc}")
    assert ledger.endswith("\n")
    for record in result.days:
        assert f"## Day {record.day} — {record.date}" in ledger
        for task in record.tasks:
            names = ", ".join(task.animals) if task.animals else "—"
            assert (
                f"| {task.time} | {task.headline} | {task.building_name} | "
                f"{task.role} | {names} | {task.detail} |"
            ) in ledger
        for line in record.feeding:
            assert f"| {GOAT_BUILDING_NAMES[line.building]} | {line.recipe_display} |" in ledger
        for move in record.moves:
            assert f"- **Move** {move.tag}: {move.from_bucket or '—'} → {move.to_bucket}" in ledger
    for journey in result.journeys:
        born = journey.born_day if journey.born_day is not None else "start"
        assert f"| {journey.tag} | {journey.sex} | {born} |" in ledger
        if journey.hops:
            for hop in journey.hops:
                text = (
                    f"d{hop.day} {hop.to_bucket} ({hop.context})"
                    if hop.from_bucket is None
                    else f"d{hop.day} {hop.from_bucket}→{hop.to_bucket} ({hop.context})"
                )
                assert text in ledger
        if journey.final_status == "ACTIVE":
            assert f"{journey.final_bucket} (active)" in ledger
        else:
            assert (
                f"{journey.exit_kind} on day {journey.exit_day} — {journey.exit_reason}" in ledger
            )
