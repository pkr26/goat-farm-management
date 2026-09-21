"""Buckets & Tasks daily-operations engine: hand-derived golden traces.

Every date below is worked out by hand from the operational single sources
of truth (not from the implementation):

* GOAT_PROFILE: gestation 150d, pregnancy check +32d, pregnancy-late day 100,
  prepartum lead 15d, weaning day 60, postpartum recovery 14d.
* Seed/protocol: quarantine protocol steps land on days 1 (×2)/4/5/10/13/20/
  30 (×2)/40/45 of the stay, release to FOUNDATION on day 45; pre-kidding
  ET+TT vaccine at EKD−40 and booster at EKD−25 (EKD = service + 150).
* Feed: 40/20/40 shift split; per-head rates 1.0 (kids) … 1.5 (recovery);
  creep ramp 0.1/0.2/0.3 kg by age band from day 14 for unweaned kids;
  quarantine dry roughage for the first three days in the bucket.
* Policy: failed scan re-serves on the next heat (21 days); two consecutive
  failures cull; male kids sell entering the 8-month window.

Stochastic tests force the outcome they need (rate 0 or 1) so the arithmetic
above pins every day number exactly. DB-free: part of the mutation-pure set.
"""

from datetime import date

import pytest
from pydantic import ValidationError

from app.models.lifecycle import LEGAL_BUCKET_TRANSITIONS
from app.models.species import GOAT_PROFILE
from app.simulation.daily_ops import (
    DAILY_OPS_MODEL_VERSION,
    AnimalStartSpec,
    DailyOpsInput,
    DailyOpsParams,
    build_daily_ledger,
    run_daily_ops,
)


def _quiet_params(**overrides: object) -> DailyOpsParams:
    """Params with every stochastic outcome forced to its deterministic pole
    (unless a test overrides one on purpose)."""
    base: dict[str, object] = {
        "conception_rate": 1.0,
        "litter_size_mean": 1.0,
        "female_fraction_at_birth": 1.0,
        "stillbirth_rate": 0.0,
        "abortion_rate": 0.0,
        "kid_pre_weaning_mortality": 0.0,
        "adult_annual_mortality": 0.0,
    }
    base.update(overrides)
    return DailyOpsParams(**base)  # type: ignore[arg-type]


def _doe(tag: str = "D1", **spec: object) -> AnimalStartSpec:
    fields: dict[str, object] = {"tag": tag, "sex": "F", "bucket": "BREEDING", "age_months": 18}
    fields.update(spec)
    return AnimalStartSpec(**fields)  # type: ignore[arg-type]


def _buck(tag: str = "B1") -> AnimalStartSpec:
    return AnimalStartSpec(tag=tag, sex="M", bucket="BREEDING", age_months=24)


def _moves_of(result, tag: str) -> list[tuple[int, str, str, str]]:
    return [
        (m.day, m.from_bucket, m.to_bucket, m.context)
        for m in result.days
        for m in m.moves
        if m.tag == tag
    ][:] or [
        (h.day, h.from_bucket or "", h.to_bucket, h.context)
        for j in result.journeys
        if j.tag == tag
        for h in j.hops
    ]


def _journey(result, tag: str):
    return next(j for j in result.journeys if j.tag == tag)


def _tasks_on(result, day: int) -> list[tuple[str, str, str]]:
    record = result.days[day - 1]
    return [(t.time, t.category, t.headline) for t in record.tasks]


# ---------------------------------------------------------------------------
# 1. The full doe cycle, day by day
# ---------------------------------------------------------------------------


def test_full_doe_cycle_golden_trace() -> None:
    """D1 (18 mo, BREEDING) + B1. Conception certain, one female kid, no
    losses. Hand-derived calendar:

    day   1 — D1 served by B1
    day  33 — pregnancy check +32d → PREGNANCY_EARLY (ultrasound)
    day 101 — gestation day 100 → PREGNANCY_LATE
    day 111 — EKD−40 pre-kidding ET+TT vaccine   (EKD = 1 + 150 = day 151)
    day 126 — EKD−25 booster
    day 136 — EKD−15 move to DELIVERY
    day 151 — kidding due; D1-1 born (F) → both in RECOVERY
    day 211 — kidding + 60: D1-1 → FEMALE_KIDS, D1 → RESTING
    day 241 — RESTING flush (30d) → BREEDING, re-served same day
    day 273 — second scan +32d → PREGNANCY_EARLY again
    """
    payload = DailyOpsInput(
        start_date=date(2026, 9, 3),
        horizon_days=280,
        seed=1,
        animals=[_doe(), _buck()],
        params=_quiet_params(),
    )
    result = run_daily_ops(payload)
    assert _moves_of(result, "D1") == [
        (33, "BREEDING", "PREGNANCY_EARLY", "ultrasound"),
        (101, "PREGNANCY_EARLY", "PREGNANCY_LATE", "manual"),
        (136, "PREGNANCY_LATE", "DELIVERY", "delivery"),
        (151, "DELIVERY", "RECOVERY", "kidding"),
        (211, "RECOVERY", "RESTING", "weaning"),
        (241, "RESTING", "BREEDING", "breeding"),
        (273, "BREEDING", "PREGNANCY_EARLY", "ultrasound"),
    ]
    # The kid is born into RECOVERY with the dam and weans to FEMALE_KIDS.
    assert _moves_of(result, "D1-1") == [(211, "RECOVERY", "FEMALE_KIDS", "weaning")]
    kid = _journey(result, "D1-1")
    assert (kid.sex, kid.born_day, kid.dam_tag, kid.final_status) == ("F", 151, "D1", "ACTIVE")

    # Service on day 1, scan tasks on day 33 (check + POSITIVE).
    day1 = [t for t in _tasks_on(result, 1) if t[1] == "OTHER"]
    assert day1 == [("09:00", "OTHER", "Breed D1 — sire B1")]
    day33 = [h for _, c, h in _tasks_on(result, 33)]
    assert "Pregnancy check: D1" in day33
    assert "D1: scan POSITIVE" in day33
    # Pre-kidding vaccines on their hand-derived days.
    assert any("Pre-kidding ET+TT vaccine: D1" in h for _, _, h in _tasks_on(result, 111))
    assert any("booster: D1" in h for _, _, h in _tasks_on(result, 126))
    assert any("Move D1 to DELIVERY" in h for _, _, h in _tasks_on(result, 136))
    assert any("Kidding due: D1" in h for _, _, h in _tasks_on(result, 151))
    assert any("Record kidding: D1 — 1 live of 1" in h for _, _, h in _tasks_on(result, 151))
    assert any("Wean kids of D1" in h for _, _, h in _tasks_on(result, 211))

    assert result.totals.services == 2
    assert result.totals.conceptions == 2
    assert result.totals.kids_born_alive == 1
    assert result.totals.kids_born_dead == 0
    assert (result.totals.deaths, result.totals.culls, result.totals.sales) == (0, 0, 0)


def test_day1_schedule_order_is_the_operational_routine() -> None:
    """Feed at 06:30 (mix first), clean after it, duties at 09:00, feed 13:30
    and 19:30, night clean after the night feed."""
    payload = DailyOpsInput(
        start_date=date(2026, 9, 3),
        horizon_days=7,
        animals=[_doe(), _buck()],
        params=_quiet_params(),
    )
    result = run_daily_ops(payload)
    times = [t.time for t in result.days[0].tasks]
    assert times == sorted(times)  # schedule is chronological
    feed = [t for t in result.days[0].tasks if t.category == "FEED"]
    assert feed[0].building == "FEED_STORE"  # the mix precedes every delivery
    assert feed[0].headline.startswith("Mix 2.400 kg — Maintenance 75:25")
    # One ration line: the mix plus one delivery per shift.
    assert [t.time for t in feed] == ["06:30", "06:30", "13:30", "19:30"]
    clean = [t for t in result.days[0].tasks if t.category == "CLEANING"]
    # BREEDING occupied: morning clean + verify, night clean + verify.
    assert len(clean) == 4
    assert {t.time for t in clean} == {"07:15", "20:15"}
    assert any("morning (after feeding)" in t.headline for t in clean)
    assert any("Verify" in t.headline for t in clean)


def test_feeding_math_matches_seeded_rates_and_shift_split() -> None:
    """Two animals in BREEDING (1.2 kg/head) plus, from day 151, a lactating
    doe (1.5) and her creep kid on the age-banded ramp: daily totals and
    40/20/40 shares."""
    payload = DailyOpsInput(
        start_date=date(2026, 9, 3),
        horizon_days=175,
        animals=[_doe(), _buck()],
        params=_quiet_params(),
    )
    result = run_daily_ops(payload)
    day1 = {(line.building, line.recipe): line for line in result.days[0].feeding}
    breeding = day1[("BREEDING", "MAINTENANCE_75_25")]
    assert breeding.heads == 2
    assert breeding.daily_kg == pytest.approx(2.4)
    assert (breeding.morning_kg, breeding.afternoon_kg, breeding.night_kg) == (
        pytest.approx(0.96),
        pytest.approx(0.48),
        pytest.approx(0.96),
    )
    # The day AFTER kidding (feed is planned at the morning round, before the
    # 09:00 lifecycle events): the doe's lactating line only — a day-old kid
    # is pre-creep (the creep line starts at day 14 of age).
    day152 = {line.building: line for line in result.days[151].feeding}
    assert day152["BREEDING"].recipe == "MAINTENANCE_75_25"
    assert day152["BREEDING"].daily_kg == pytest.approx(1.2)
    recovery_lines = {
        line.recipe: line for line in result.days[151].feeding if line.building == "RECOVERY"
    }
    assert recovery_lines["LACTATING_60_40"].daily_kg == pytest.approx(1.5)
    assert "CREEP" not in recovery_lines
    # Kid age 14 (kidding day 151 + 14): the first creep band opens at 0.1
    # kg/head, labelled with the band on the display string.
    day165 = [line for line in result.days[164].feeding if line.recipe == "CREEP"]
    assert len(day165) == 1
    assert day165[0].kg_per_head == pytest.approx(0.1)
    assert "14–30 d" in day165[0].recipe_display
    for record in result.days:
        for line in record.feeding:
            # Gram-exact internal consistency on every line of every day.
            assert line.daily_kg == pytest.approx(
                line.morning_kg + line.afternoon_kg + line.night_kg
            )


# ---------------------------------------------------------------------------
# 2. Quarantine protocol
# ---------------------------------------------------------------------------


def test_quarantine_protocol_days_and_release() -> None:
    """Arrival on day 1: protocol steps on days 1 (×2)/4/5/10/13/20/30 (×2)/40
    and release on day 45; dry roughage for the first three days in the bucket."""
    payload = DailyOpsInput(
        start_date=date(2026, 9, 3),
        horizon_days=46,
        animals=[
            AnimalStartSpec(
                tag="Q1", sex="F", bucket="QUARANTINE", age_months=14, days_in_bucket=0
            ),
            _buck(),
        ],
        params=_quiet_params(),
    )
    result = run_daily_ops(payload)
    protocol_days: dict[int, list[str]] = {}
    for record in result.days:
        for task in record.tasks:
            if task.category in (
                "QUARANTINE",
                "DEWORMING",
                "VACCINE",
                "BUCKET_MOVE",
            ) and task.animals == ["Q1"]:
                protocol_days.setdefault(record.day, []).append(task.headline)
    assert sorted(protocol_days) == [1, 4, 5, 10, 13, 20, 30, 40, 45]
    # Day 1 carries two duties (arrival inspection + rest), day 30 two
    # (Goat Pox + fecal recheck) — the step-keyed dedupe fires both.
    assert len(protocol_days[1]) == 2
    assert len(protocol_days[30]) == 2
    assert any("arrival inspection" in h.lower() for h in protocol_days[1])
    assert any("fecal" in h.lower() for h in protocol_days[13])
    assert any("fecal" in h.lower() for h in protocol_days[30])
    assert "deworm" in protocol_days[4][0]
    assert any("PPR" in h for h in protocol_days[10])
    assert any("Release Q1 to FOUNDATION" in h for h in protocol_days[45])
    # Released at 14 months with a buck standing: bred the same day.
    assert _moves_of(result, "Q1") == [
        (45, "QUARANTINE", "FOUNDATION", "quarantine_release"),
        (45, "FOUNDATION", "BREEDING", "breeding"),
    ]
    # Dry roughage days 1–3 (bucket days 0,1,2), maintenance from day 4.
    quarantine_recipes = {
        record.day: [line.recipe for line in record.feeding if line.building == "QUARANTINE"]
        for record in result.days[:5]
    }
    assert quarantine_recipes[1] == ["DRY_ROUGHAGE_ONLY"]
    assert quarantine_recipes[3] == ["DRY_ROUGHAGE_ONLY"]
    assert quarantine_recipes[4] == ["MAINTENANCE_75_25"]
    # Released at 14 months, she is breeding-ready the same day.
    assert (45, "FOUNDATION", "BREEDING", "breeding") in _moves_of(result, "Q1")


def test_quarantine_mid_stay_input_catches_up() -> None:
    """Arrived 10 days before the sim (arrival day index -9, so protocol
    day = run day + 10): next steps are protocol day 20 (run day 10) and the
    day-45 release (run day 35)."""
    payload = DailyOpsInput(
        start_date=date(2026, 9, 3),
        horizon_days=36,
        animals=[
            AnimalStartSpec(
                tag="Q1", sex="F", bucket="QUARANTINE", age_months=14, days_in_bucket=10
            )
        ],
        params=_quiet_params(),
    )
    result = run_daily_ops(payload)
    # Released at 14 months but no buck stands on this farm: she stays in
    # FOUNDATION (the breeding record is what moves a doe to BREEDING).
    assert _moves_of(result, "Q1") == [(35, "QUARANTINE", "FOUNDATION", "quarantine_release")]
    headlines_by_day = {
        record.day: [t.headline for t in record.tasks if "Q1" in t.animals]
        for record in result.days
    }
    assert any("Day 20" in h for h in headlines_by_day[10])


# ---------------------------------------------------------------------------
# 3. Pregnancy milestones and edge paths
# ---------------------------------------------------------------------------


def test_mid_pregnancy_start_skips_passed_milestones() -> None:
    """Starting at gestation day 120 (PREGNANCY_LATE): the primary vaccine
    (day 110) is history — only the booster (gestation 125 → day 6), the
    DELIVERY move (135 → day 16) and kidding (150 → day 31) lie ahead."""
    payload = DailyOpsInput(
        start_date=date(2026, 9, 3),
        horizon_days=50,
        animals=[
            AnimalStartSpec(
                tag="P1", sex="F", bucket="PREGNANCY_LATE", age_months=30, bred_days_ago=120
            ),
            _buck(),
        ],
        params=_quiet_params(),
    )
    result = run_daily_ops(payload)
    assert _moves_of(result, "P1") == [
        (16, "PREGNANCY_LATE", "DELIVERY", "delivery"),
        (31, "DELIVERY", "RECOVERY", "kidding"),
    ]
    vaccine_days = [
        record.day for record in result.days for task in record.tasks if task.category == "VACCINE"
    ]
    assert vaccine_days == [6]
    assert any("booster: P1" in h for _, _, h in _tasks_on(result, 6))


def test_stillborn_litter_moves_dam_postpartum_at_day_14() -> None:
    """No surviving kids: the dam leaves RECOVERY via the postpartum move at
    kidding + 14 (POSTPARTUM_RECOVERY_DAYS)."""
    payload = DailyOpsInput(
        start_date=date(2026, 9, 3),
        horizon_days=50,
        animals=[
            AnimalStartSpec(tag="P1", sex="F", bucket="DELIVERY", age_months=30, bred_days_ago=140)
        ],
        params=_quiet_params(stillbirth_rate=1.0),
    )
    result = run_daily_ops(payload)
    assert _moves_of(result, "P1") == [
        (11, "DELIVERY", "RECOVERY", "kidding"),
        (25, "RECOVERY", "RESTING", "postpartum"),
    ]
    assert result.totals.kids_born_alive == 0
    assert result.totals.kids_born_dead == 1


def test_failed_services_reservice_on_heat_then_cull() -> None:
    """Conception impossible: scan 1 fails day 33 (re-serve day 54 = 33+21),
    scan 2 fails day 86 (54+32) → culled the same day."""
    payload = DailyOpsInput(
        start_date=date(2026, 9, 3),
        horizon_days=100,
        animals=[_doe(), _buck()],
        params=_quiet_params(conception_rate=0.0),
    )
    result = run_daily_ops(payload)
    assert _moves_of(result, "D1") == []  # she never leaves BREEDING
    assert any("scan NEGATIVE" in h for _, _, h in _tasks_on(result, 33))
    assert any("Breed D1 — sire B1" in h for _, _, h in _tasks_on(result, 54))
    assert any("cull review" in h for _, _, h in _tasks_on(result, 86))
    journey = _journey(result, "D1")
    assert (journey.final_status, journey.exit_day, journey.exit_kind) == ("CULLED", 86, "CULLED")
    assert result.totals.culls == 1


def test_buck_ratio_caps_open_services() -> None:
    """One buck, ratio 2: only the first two does are served; the third waits
    with an explicit hold duty."""
    payload = DailyOpsInput(
        start_date=date(2026, 9, 3),
        horizon_days=7,
        animals=[_doe("D1"), _doe("D2"), _doe("D3"), _buck()],
        params=_quiet_params(buck_doe_ratio=2),
    )
    result = run_daily_ops(payload)
    day1 = [h for _, _, h in _tasks_on(result, 1)]
    assert sum(1 for h in day1 if h.startswith("Breed ")) == 2
    assert any(h.startswith("Hold D3 — sire capacity reached") for h in day1)
    assert result.totals.services == 2


def test_male_kid_sells_entering_the_meat_window() -> None:
    """A 7-month male kid (213 days old on day 1) crosses 8 months
    (243.52 days) after 31 more days: sold on day 32."""
    payload = DailyOpsInput(
        start_date=date(2026, 9, 3),
        horizon_days=40,
        animals=[AnimalStartSpec(tag="MK1", sex="M", bucket="MALE_KIDS", age_months=7)],
        params=_quiet_params(),
    )
    result = run_daily_ops(payload)
    journey = _journey(result, "MK1")
    assert (journey.final_status, journey.exit_day, journey.exit_kind) == ("SOLD", 32, "SOLD")
    assert any("Sell MK1 — meat window" in h for _, _, h in _tasks_on(result, 32))


def test_female_kid_breeding_ready_graduation() -> None:
    """A 9-month female kid (274 days on day 1) reaches the 12-month
    first-service floor (365.3 days) on day 93 and moves FEMALE_KIDS →
    BREEDING the same day."""
    payload = DailyOpsInput(
        start_date=date(2026, 9, 3),
        horizon_days=95,
        animals=[
            AnimalStartSpec(tag="FK1", sex="F", bucket="FEMALE_KIDS", age_months=9),
            _buck(),
        ],
        params=_quiet_params(),
    )
    result = run_daily_ops(payload)
    assert _moves_of(result, "FK1") == [(93, "FEMALE_KIDS", "BREEDING", "breeding")]


def test_adult_mortality_casualty_path() -> None:
    """Certain mortality: every animal dies on day 1 with a vet casualty duty."""
    payload = DailyOpsInput(
        start_date=date(2026, 9, 3),
        horizon_days=7,
        animals=[_doe(), _buck()],
        params=_quiet_params(adult_annual_mortality=1.0),
    )
    result = run_daily_ops(payload)
    assert result.totals.deaths == 2
    assert any("Attend casualty: D1 died" in h for _, _, h in _tasks_on(result, 1))
    # The farm stands empty from the first evening onward.
    assert all(day.occupancy == [] for day in result.days)


# ---------------------------------------------------------------------------
# 4. Cross-run invariants (stochastic mix, whole-run properties)
# ---------------------------------------------------------------------------


def _toy_herd(seed: int, horizon: int = 120) -> DailyOpsInput:
    animals = [_doe(f"D{i}", age_months=12 + i) for i in range(1, 11)] + [
        _buck(),
        AnimalStartSpec(tag="MK1", sex="M", bucket="MALE_KIDS", age_months=7),
        AnimalStartSpec(tag="FK1", sex="F", bucket="FEMALE_KIDS", age_months=9),
        AnimalStartSpec(tag="Q1", sex="F", bucket="QUARANTINE", age_months=14, days_in_bucket=5),
        AnimalStartSpec(
            tag="P1", sex="F", bucket="PREGNANCY_EARLY", age_months=30, bred_days_ago=50
        ),
    ]
    return DailyOpsInput(
        start_date=date(2026, 9, 3), horizon_days=horizon, seed=seed, animals=animals
    )


def test_engine_emits_every_simulatable_transition_context() -> None:
    """Across a battery covering each workflow, the engine stamps exactly the
    ten contexts the legal graph allows to arise in simulation — a dropped
    phase (or an illegal shortcut) changes this set."""
    payloads = [
        _toy_herd(seed=7, horizon=220),  # P1 kidded day 101 → weaning day 161
        # age-cull orphan → orphan_weaning
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=200,
            seed=1,
            animals=[_doe(age_months=12), _buck()],
            params=_quiet_params(kid_pre_weaning_mortality=0.9, max_doe_age_months=14),
        ),
        # certain abortion → abortion
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=100,
            seed=1,
            animals=[_doe(), _buck()],
            params=_quiet_params(abortion_rate=1.0),
        ),
        # stillborn litter → postpartum
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=50,
            animals=[
                AnimalStartSpec(
                    tag="P1", sex="F", bucket="DELIVERY", age_months=30, bred_days_ago=140
                )
            ],
            params=_quiet_params(stillbirth_rate=1.0),
        ),
        # quarantine release
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=46,
            animals=[AnimalStartSpec(tag="Q1", sex="F", bucket="QUARANTINE", age_months=14)],
            params=_quiet_params(),
        ),
        # started RECOVERY doe → postpartum from the starter clock
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=10,
            animals=[
                AnimalStartSpec(
                    tag="R1", sex="F", bucket="RECOVERY", age_months=30, days_in_bucket=5
                )
            ],
            params=_quiet_params(),
        ),
    ]
    seen: set[str] = set()
    for payload in payloads:
        for record in run_daily_ops(payload).days:
            for move in record.moves:
                allowed = LEGAL_BUCKET_TRANSITIONS[(move.from_bucket, move.to_bucket)]
                assert move.context in allowed, move
                seen.add(move.context)
    assert seen == {
        "manual",
        "breeding",
        "ultrasound",
        "quarantine_release",
        "delivery",
        "kidding",
        "abortion",
        "postpartum",
        "weaning",
        "orphan_weaning",
    }


def test_head_conservation_day_over_day() -> None:
    """occupancy(day) - occupancy(day-1) == in-moves + births - out-moves -
    exits, per building, every day (day-1 baseline = the starting herd)."""
    payload = _toy_herd(seed=7)
    result = run_daily_ops(payload)
    previous: dict[str, int] = {}
    for spec in payload.animals:
        previous[spec.bucket] = previous.get(spec.bucket, 0) + 1
    journeys = {j.tag: j for j in result.journeys}
    for record in result.days:
        current = {row.building: row.heads for row in record.occupancy}
        delta = {b: current.get(b, 0) - previous.get(b, 0) for b in set(current) | set(previous)}
        expected = {b: 0 for b in delta}
        for move in record.moves:
            if move.from_bucket:
                expected[move.from_bucket] = expected.get(move.from_bucket, 0) - 1
            expected[move.to_bucket] = expected.get(move.to_bucket, 0) + 1
        for birth in record.births:
            for kid in birth.kids:
                if kid.status == "ALIVE":
                    expected["RECOVERY"] += 1
        for exit_ in record.exits:
            journey = journeys[exit_.tag]
            hops = [h for h in journey.hops if h.day <= exit_.day]
            bucket = hops[-1].to_bucket if hops else journey.start_bucket
            expected[bucket] = expected.get(bucket, 0) - 1
        # A building only transited on this day (release + same-day re-move)
        # nets zero on both sides; compare the non-zero flows.
        assert {k: v for k, v in delta.items() if v} == {k: v for k, v in expected.items() if v}, (
            record.day
        )
        previous = current


def test_feeding_follows_morning_occupancy() -> None:
    """Feed lines are planned at the 06:30 round, before the 09:00 lifecycle
    events: a building is fed exactly when it stood occupied the previous
    evening (day 1: the starting buckets)."""
    payload = _toy_herd(seed=7)
    result = run_daily_ops(payload)
    previous = {spec.bucket for spec in payload.animals}
    for record in result.days:
        fed = {line.building for line in record.feeding}
        assert fed == previous, record.day
        for line in record.feeding:
            total = line.morning_kg + line.afternoon_kg + line.night_kg
            assert line.daily_kg == pytest.approx(total, abs=1e-9)
            if line.recipe == "CREEP":
                assert line.building == "RECOVERY"
                assert line.kg_per_head in (0.1, 0.2, 0.3)
        previous = {row.building for row in record.occupancy}


def test_cleaning_covers_morning_and_night_occupancy() -> None:
    """Each building is cleaned in the morning iff it stood occupied the
    previous evening, and at night iff occupied that evening; every cleaning
    carries a cleaner-manager verification duty."""
    payload = _toy_herd(seed=7)
    result = run_daily_ops(payload)
    previous = {spec.bucket for spec in payload.animals}
    for record in result.days:
        current = {row.building for row in record.occupancy}
        morning_cleans: dict[str, int] = {}
        night_cleans: dict[str, int] = {}
        verifies = 0
        for task in record.tasks:
            if task.category != "CLEANING":
                continue
            if task.headline.startswith("Verify"):
                verifies += 1
            elif "morning" in task.headline:
                morning_cleans[task.building] = morning_cleans.get(task.building, 0) + 1
            else:
                night_cleans[task.building] = night_cleans.get(task.building, 0) + 1
        assert set(morning_cleans) == previous, record.day
        assert set(night_cleans) == current, record.day
        assert all(count == 1 for count in morning_cleans.values())
        assert all(count == 1 for count in night_cleans.values())
        assert verifies == len(previous) + len(current), record.day
        previous = current


def test_roles_follow_the_task_category_role_map() -> None:
    from app.permissions import TASK_CATEGORY_ROLE_MAP

    result = run_daily_ops(_toy_herd(seed=7))
    for record in result.days:
        for task in record.tasks:
            if task.category in TASK_CATEGORY_ROLE_MAP:
                assert task.role == TASK_CATEGORY_ROLE_MAP[task.category]


def test_seed_replays_identically_and_other_seeds_differ() -> None:
    first = run_daily_ops(_toy_herd(seed=7))
    second = run_daily_ops(_toy_herd(seed=7))
    assert first.model_dump() == second.model_dump()
    other = run_daily_ops(_toy_herd(seed=8))
    assert first.model_dump() != other.model_dump()


def test_journeys_match_day_moves() -> None:
    result = run_daily_ops(_toy_herd(seed=7))
    flat_moves = {(m.day, m.tag) for record in result.days for m in record.moves}
    flat_hops = {(h.day, j.tag) for j in result.journeys for h in j.hops}
    assert flat_moves == flat_hops
    # transition_counts sum to the totals.moves figure.
    assert sum(t.count for t in result.transition_counts) == result.totals.moves


def test_ledger_renders_the_whole_run_deterministically() -> None:
    result = run_daily_ops(_toy_herd(seed=7))
    ledger = build_daily_ledger(result)
    assert ledger.startswith("# Buckets & Tasks — daily operations ledger")
    assert "## Day 1 — 2026-09-03" in ledger
    assert "## Day 120 —" in ledger
    assert "## Transition matrix" in ledger
    assert "## Animal journeys" in ledger
    assert "## Explanations" in ledger
    assert build_daily_ledger(result) == ledger
    assert result.model_version == DAILY_OPS_MODEL_VERSION


def test_explanations_echo_the_run_numbers() -> None:
    result = run_daily_ops(_toy_herd(seed=7))
    by_key = {e.key: e for e in result.explanations}
    assert set(by_key) == {
        "routine",
        "buildings",
        "feed",
        "transitions",
        "reproduction",
        "exits",
        "determinism",
    }
    assert by_key["transitions"].figures["moves"] == result.totals.moves
    assert by_key["reproduction"].figures["services"] == result.totals.services
    assert by_key["reproduction"].figures["kids_born_alive"] == result.totals.kids_born_alive
    # Notes carry the v1 caveats.
    assert any("Goat farm simulation" in note for note in result.notes)


def test_notes_quote_the_live_breeding_age_gate() -> None:
    """The eligibility note must read GOAT_PROFILE.min_breeding_age_months,
    not a hardcoded age that can drift from the enforced gate."""
    result = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 1),
            horizon_days=30,
            seed=7,
            animals=[_doe("D1"), _buck("B1")],
            params=_quiet_params(),
        )
    )
    assert any(
        f"age-gated (≥{GOAT_PROFILE.min_breeding_age_months} months)" in note
        for note in result.notes
    )


# ---------------------------------------------------------------------------
# 5. Input validation (deliberate rejections)
# ---------------------------------------------------------------------------


def test_rejects_duplicate_tags() -> None:
    with pytest.raises(ValidationError, match="unique"):
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            animals=[_doe("D1"), _doe("D1")],
        )


def test_rejects_sex_bucket_mismatch() -> None:
    with pytest.raises(ValidationError, match="MALE_KIDS"):
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            animals=[AnimalStartSpec(tag="X", sex="F", bucket="MALE_KIDS", age_months=6)],
        )
    with pytest.raises(ValidationError, match="RESTING"):
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            animals=[AnimalStartSpec(tag="X", sex="M", bucket="RESTING", age_months=24)],
        )


def test_rejects_pregnant_bucket_without_service_date() -> None:
    with pytest.raises(ValidationError, match="needs bred_days_ago"):
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            animals=[_doe(bucket="PREGNANCY_EARLY")],
        )


def test_rejects_out_of_window_gestation_days() -> None:
    with pytest.raises(ValidationError, match="gestation days"):
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            animals=[_doe(bucket="PREGNANCY_EARLY", bred_days_ago=10)],
        )


def test_rejects_breeding_doe_past_her_scan_date() -> None:
    with pytest.raises(ValidationError, match="pregnancy check"):
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            animals=[_doe(bred_days_ago=33)],
        )


def test_rejects_bred_days_ago_on_incoherent_buckets() -> None:
    with pytest.raises(ValidationError, match="bred_days_ago belongs"):
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            animals=[_doe(bucket="RESTING", bred_days_ago=10)],
        )


def test_rejects_overstayed_quarantine() -> None:
    with pytest.raises(ValidationError, match="quarantine"):
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            animals=[
                AnimalStartSpec(
                    tag="Q1", sex="F", bucket="QUARANTINE", age_months=14, days_in_bucket=45
                )
            ],
        )


def test_rejects_out_of_bounds_horizon_dates_and_herd() -> None:
    with pytest.raises(ValidationError):
        DailyOpsInput(start_date=date(2026, 9, 3), horizon_days=3, animals=[_doe()])
    with pytest.raises(ValidationError):
        DailyOpsInput(start_date=date(2026, 9, 3), horizon_days=400, animals=[_doe()])
    with pytest.raises(ValidationError):
        DailyOpsInput(start_date=date(1999, 12, 31), animals=[_doe()])
    with pytest.raises(ValidationError):
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            animals=[_doe(f"D{i}") for i in range(501)],
        )
    with pytest.raises(ValidationError):
        DailyOpsInput(start_date=date(2026, 9, 3), animals=[])


def test_rejects_float_and_extra_fields_strictly() -> None:
    with pytest.raises(ValidationError):
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=90.5,  # type: ignore[arg-type]
            animals=[_doe()],
        )
    with pytest.raises(ValidationError):
        AnimalStartSpec(tag="D1", sex="F", bucket="BREEDING", age_months=18, surprise=1)  # type: ignore[call-arg]


def test_no_buck_note_when_herd_has_no_sire() -> None:
    result = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=7,
            animals=[_doe()],
            params=_quiet_params(),
        )
    )
    assert result.totals.services == 0
    assert any("No buck in this herd" in note for note in result.notes)


# ---------------------------------------------------------------------------
# 6. Audit-fix goldens: orphan hazard, tag collisions, RECOVERY starters,
#    abortion window, and previously uncovered branches
# ---------------------------------------------------------------------------


def test_orphan_weaning_on_dam_cull_clears_the_pre_weaning_hazard() -> None:
    """D1 (12 mo) reaches the 12-month first-service floor on day 2, is served
    then, kidded day 152 and — no longer pregnant — age-culled the same day
    (12 + 152/30.44 ≈ 17 mo ≥ 14). Her kid is orphan-weaned on its birth day;
    graduation must also end the pre-weaning hazard: with kid mortality 90%
    and adult mortality 0, the kid survives to day 365 only if the flag is
    cleared (with the flag kept it dies within weeks)."""
    payload = DailyOpsInput(
        start_date=date(2026, 9, 3),
        horizon_days=365,
        seed=1,
        animals=[_doe(age_months=12), _buck()],
        params=_quiet_params(kid_pre_weaning_mortality=0.9, max_doe_age_months=14),
    )
    result = run_daily_ops(payload)
    dam = _journey(result, "D1")
    assert (dam.exit_kind, dam.exit_day) == ("CULLED", 152)
    kid = _journey(result, "D1-1")
    assert [(h.day, h.from_bucket, h.to_bucket, h.context) for h in kid.hops] == [
        (152, "RECOVERY", "FEMALE_KIDS", "orphan_weaning")
    ]
    assert (kid.final_status, kid.final_bucket) == ("ACTIVE", "FEMALE_KIDS")
    # Feeding follows morning occupancy: day 152's ration was planned at 06:30
    # (before the birth) and by day 153 the kid stands weaned in FEMALE_KIDS —
    # so this kid never eats a creep line.
    creep_days = [
        record.day for record in result.days for line in record.feeding if line.recipe == "CREEP"
    ]
    assert creep_days == []


def test_orphan_weaned_kids_never_carry_the_kid_hazard() -> None:
    """Cross-seed invariant: an orphan-weaned kid may only leave the herd by
    adult mortality — never as a 'Pre-weaning kid loss' while standing in a
    weaned pen (the audited defect)."""
    orphans_seen = 0
    for seed in range(1, 26):
        result = run_daily_ops(
            DailyOpsInput(
                start_date=date(2026, 9, 3),
                horizon_days=300,
                seed=seed,
                animals=[_doe("D1"), _doe("D2"), _doe("D3"), _buck()],
                params=_quiet_params(kid_pre_weaning_mortality=0.5, adult_annual_mortality=0.30),
            )
        )
        for journey in result.journeys:
            if journey.born_day and any(h.context == "orphan_weaning" for h in journey.hops):
                orphans_seen += 1
                assert journey.final_status == "ACTIVE" or (
                    journey.exit_reason.startswith("Adult mortality")
                ), journey
    assert orphans_seen > 0  # the sweep must actually exercise the path


def test_newborn_tags_never_replace_starting_animals() -> None:
    """D1's first female kid would be tagged D1-1, which a starter already
    owns: the newborn must take D1-2, the starter keeps her journey, and head
    conservation holds (start 3 + born 1 == final 4)."""
    payload = DailyOpsInput(
        start_date=date(2026, 9, 3),
        horizon_days=200,
        seed=1,
        animals=[
            _doe(),
            AnimalStartSpec(tag="D1-1", sex="F", bucket="FEMALE_KIDS", age_months=6),
            _buck(),
        ],
        params=_quiet_params(),
    )
    result = run_daily_ops(payload)
    tags = {j.tag for j in result.journeys}
    assert tags == {"B1", "D1", "D1-1", "D1-2"}
    starter = _journey(result, "D1-1")
    assert starter.start_bucket == "FEMALE_KIDS"  # she was never overwritten
    assert starter.born_day is None and starter.dam_tag is None
    newborn = _journey(result, "D1-2")
    assert (newborn.born_day, newborn.dam_tag) == (151, "D1")
    final_head = sum(row.heads for row in result.days[-1].occupancy)
    assert (
        result.head_start
        + result.totals.kids_born_alive
        - result.totals.deaths
        - result.totals.culls
        - result.totals.sales
    ) == final_head


def test_recovery_starter_kid_weans_by_age() -> None:
    """Started unweaned kids have no dam link, so the day-60 clock runs on
    their own age: a 9-month starter kid weans on day 1; a 1-month kid (30
    days old on day 1) weans on day 31 (= 60 − 30 days of age)."""
    result = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=35,
            animals=[
                AnimalStartSpec(
                    tag="RK1", sex="F", bucket="RECOVERY", age_months=9, dependent_kid=True
                ),
                AnimalStartSpec(
                    tag="RK2",
                    sex="F",
                    bucket="RECOVERY",
                    age_months=1,
                    dependent_kid=True,
                    days_in_bucket=30,
                ),
            ],
            params=_quiet_params(),
        )
    )
    assert _moves_of(result, "RK1") == [(1, "RECOVERY", "FEMALE_KIDS", "weaning")]
    assert _moves_of(result, "RK2") == [(31, "RECOVERY", "FEMALE_KIDS", "weaning")]
    assert any("Age wean RK1" in h for _, _, h in _tasks_on(result, 1))
    assert any("Age wean RK2" in h for _, _, h in _tasks_on(result, 31))


def test_recovery_starter_doe_finishes_postpartum_recovery() -> None:
    """A started doe in RECOVERY is mid postpartum recovery. R1 (20 days in
    the bucket) has exhausted the 14-day window → RESTING on day 1; R2 (5
    days in) moves on day 10 = 1 + (14 − 5)."""
    result = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=12,
            animals=[
                AnimalStartSpec(
                    tag="R1", sex="F", bucket="RECOVERY", age_months=30, days_in_bucket=20
                ),
                AnimalStartSpec(
                    tag="R2", sex="F", bucket="RECOVERY", age_months=30, days_in_bucket=5
                ),
            ],
            params=_quiet_params(),
        )
    )
    assert _moves_of(result, "R1") == [(1, "RECOVERY", "RESTING", "postpartum")]
    assert _moves_of(result, "R2") == [(10, "RECOVERY", "RESTING", "postpartum")]


def test_certain_abortion_golden_trace() -> None:
    """Abortion rate 1.0: the loss fires the same day as the positive scan
    (the gestation-duties draw follows the 09:00 check), day 33 = 1 + 32.
    RESTING flush is 30 days → re-served day 63, second scan-and-loss day 95."""
    result = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=100,
            seed=1,
            animals=[_doe(), _buck()],
            params=_quiet_params(abortion_rate=1.0),
        )
    )
    assert _moves_of(result, "D1") == [
        (33, "BREEDING", "PREGNANCY_EARLY", "ultrasound"),
        (33, "PREGNANCY_EARLY", "RESTING", "abortion"),
        (63, "RESTING", "BREEDING", "breeding"),
        (95, "BREEDING", "PREGNANCY_EARLY", "ultrasound"),
        (95, "PREGNANCY_EARLY", "RESTING", "abortion"),
    ]
    assert any("Pregnancy loss: D1" in h for _, _, h in _tasks_on(result, 33))
    assert result.totals.conceptions == 2
    assert (result.totals.deaths, result.totals.kids_born_alive) == (0, 0)


def test_abortion_hazard_anchors_to_the_exposed_window() -> None:
    """The draw window is scan → kidding (150 − 32 = 118 days), not the whole
    150-day gestation: the nominal rate must reproduce over the window the
    engine actually draws on."""
    from app.models.species import GOAT_PROFILE
    from app.simulation.daily_ops import _daily_hazard, _DailyOpsRun

    run = _DailyOpsRun(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            animals=[_doe()],
            params=_quiet_params(abortion_rate=0.02),
        )
    )
    exposed = GOAT_PROFILE.gestation_days - GOAT_PROFILE.pregnancy_check_after_service_days
    assert exposed == 118
    assert run.abortion_hazard == _daily_hazard(0.02, exposed)


def test_kid_death_starts_the_dams_postpartum_clock() -> None:
    """Kid mortality 1.0: D1-1 dies on her birth day (151) and D1, left with
    no kids, moves RECOVERY → RESTING at kidding + 14 = day 165."""
    result = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=170,
            seed=1,
            animals=[_doe(), _buck()],
            params=_quiet_params(kid_pre_weaning_mortality=1.0),
        )
    )
    kid = _journey(result, "D1-1")
    assert (kid.final_status, kid.exit_day) == ("DEAD", 151)
    assert kid.exit_reason.startswith("Pre-weaning kid loss")
    assert _moves_of(result, "D1") == [
        (33, "BREEDING", "PREGNANCY_EARLY", "ultrasound"),
        (101, "PREGNANCY_EARLY", "PREGNANCY_LATE", "manual"),
        (136, "PREGNANCY_LATE", "DELIVERY", "delivery"),
        (151, "DELIVERY", "RECOVERY", "kidding"),
        (165, "RECOVERY", "RESTING", "postpartum"),
    ]
    assert result.totals.deaths == 1


def test_age_cull_fires_for_old_does_and_spares_pregnant_ones() -> None:
    """max_doe_age 36: a 40-month open doe is culled on day 1; a 40-month
    pregnant doe (P1, gestation day 50) is exempt until she kids."""
    result = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=7,
            animals=[
                AnimalStartSpec(tag="OLD", sex="F", bucket="BREEDING", age_months=40),
                AnimalStartSpec(
                    tag="P1", sex="F", bucket="PREGNANCY_EARLY", age_months=40, bred_days_ago=50
                ),
            ],
            params=_quiet_params(max_doe_age_months=36),
        )
    )
    old = _journey(result, "OLD")
    assert (old.final_status, old.exit_kind, old.exit_day) == ("CULLED", "CULLED", 1)
    assert any("Cull OLD — age" in h for _, _, h in _tasks_on(result, 1))
    assert _journey(result, "P1").final_status == "ACTIVE"
    assert result.totals.culls == 1


def test_young_buck_cannot_service() -> None:
    """Sires are age-gated at 12 months: a 2-month buck serves nobody (and the
    no-buck note fires); a 13-month buck serves normally."""
    young = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=7,
            animals=[_doe(), AnimalStartSpec(tag="B1", sex="M", bucket="BREEDING", age_months=2)],
            params=_quiet_params(),
        )
    )
    assert young.totals.services == 0
    assert any("No buck stood in BREEDING" in note for note in young.notes)
    mature = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=7,
            animals=[_doe(), AnimalStartSpec(tag="B1", sex="M", bucket="BREEDING", age_months=13)],
            params=_quiet_params(),
        )
    )
    assert mature.totals.services == 1


def test_quads_wean_to_male_kids_and_sell() -> None:
    """Litter mean 4.0 with all-male births: D1-1..D1-4 are born day 151 and
    wean together to MALE_KIDS on day 211; with male_sale_age_months=1 they
    are already past the sale age at weaning, so they sell the same day (the
    sales phase runs after the weaning phase)."""
    result = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=220,
            seed=1,
            animals=[_doe(), _buck()],
            params=_quiet_params(
                litter_size_mean=4.0, female_fraction_at_birth=0.0, male_sale_age_months=1
            ),
        )
    )
    born = sorted((j.tag, j.sex, j.born_day) for j in result.journeys if j.born_day == 151)
    assert born == [
        ("D1-1", "M", 151),
        ("D1-2", "M", 151),
        ("D1-3", "M", 151),
        ("D1-4", "M", 151),
    ]
    for tag in ("D1-1", "D1-2", "D1-3", "D1-4"):
        assert _moves_of(result, tag) == [(211, "RECOVERY", "MALE_KIDS", "weaning")]
        journey = _journey(result, tag)
        assert (journey.exit_kind, journey.exit_day) == ("SOLD", 211)
    assert result.totals.sales == 4
    assert any(
        "sale window 1–2 months" in task.detail for record in result.days for task in record.tasks
    )


def test_preserviced_doe_is_scanned_on_her_own_calendar() -> None:
    """A doe arriving 10 days past service (gestation day 10 on day 1) is
    scanned when her gestation reaches 32: day 23, not day 33."""
    result = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=25,
            animals=[_doe(bred_days_ago=10), _buck()],
            params=_quiet_params(),
        )
    )
    assert _moves_of(result, "D1") == [(23, "BREEDING", "PREGNANCY_EARLY", "ultrasound")]
    assert any("scan POSITIVE" in h for _, _, h in _tasks_on(result, 23))
    assert not any("Pregnancy check: D1" in h for _, _, h in _tasks_on(result, 22))


def test_booster_detail_admits_a_missed_primary() -> None:
    """Arriving at gestation day 120, the primary (EKD−40) is history: the
    booster's detail must say so instead of claiming a 15-day gap."""
    result = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=10,
            animals=[
                AnimalStartSpec(
                    tag="P1", sex="F", bucket="PREGNANCY_LATE", age_months=30, bred_days_ago=120
                )
            ],
            params=_quiet_params(),
        )
    )
    booster = [
        task for record in result.days for task in record.tasks if task.category == "VACCINE"
    ]
    assert len(booster) == 1
    assert booster[0].detail == "Booster only — her primary dose pre-dates this run."


def test_totals_aggregates_are_pinned_exactly() -> None:
    """Two animals in BREEDING for 7 quiet days: every totals dict is pinned.
    Per day: 4 FEED tasks (1 mix + 3 deliveries) and 4 CLEANING tasks (clean
    + verify, morning and night); day 1 adds the single breed duty."""
    result = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=7,
            seed=1,
            animals=[_doe(), _buck()],
            params=_quiet_params(),
        )
    )
    totals = result.totals
    assert totals.feed_kg_by_recipe == {"MAINTENANCE_75_25": 16.8}  # 7 × 2 × 1.2
    assert totals.tasks_by_category == {"CLEANING": 28, "FEED": 28, "OTHER": 1}
    assert totals.tasks_by_role == {"CLEANER": 28, "FEEDER": 28, "MANAGER": 1}
    assert totals.vet_tasks_by_building == {}
    assert totals.building_days == {"BREEDING": 7}
    assert totals.moves == 0


def test_seed_bounds_match_the_api_layer() -> None:
    assert DailyOpsInput(start_date=date(2026, 9, 3), animals=[_doe()], seed=2**62).seed == 2**62
    assert DailyOpsInput(start_date=date(2026, 9, 3), animals=[_doe()], seed=-(2**62)).seed == -(
        2**62
    )
    with pytest.raises(ValidationError):
        DailyOpsInput(start_date=date(2026, 9, 3), animals=[_doe()], seed=2**62 + 1)


# ---------------------------------------------------------------------------
# 7. Stranded-male fixes: RECOVERY males rejected, Foundation sires graduate
# ---------------------------------------------------------------------------


def test_rejects_male_in_recovery_without_dependent_kid() -> None:
    """The only males in RECOVERY are unweaned kids with their dam; a male
    standing there without dependent_kid has no exit in the engine (sales fire
    from MALE_KIDS, culls apply to does), so the input is rejected up front
    instead of stranding him for the whole run."""
    with pytest.raises(ValidationError, match="growers belong in MALE_KIDS"):
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            animals=[
                AnimalStartSpec(
                    tag="M9", sex="M", bucket="RECOVERY", age_months=6, days_in_bucket=10
                )
            ],
        )
    # The coherent form — an unweaned male kid with its dam — is still valid
    # and weans by age into MALE_KIDS (covered by
    # test_recovery_starter_male_kid_weans_by_age).
    AnimalStartSpec(tag="K1", sex="M", bucket="RECOVERY", age_months=1, dependent_kid=True)


def test_sire_graduates_from_foundation_and_serves_same_day() -> None:
    """A Foundation grower past the 12-month sire gate graduates into the
    breeding pen (context 'breeding') and covers a waiting doe the same day:
    the graduation runs before the bucks are collected."""
    result = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=7,
            seed=1,
            animals=[
                _doe(),
                AnimalStartSpec(tag="G1", sex="M", bucket="FOUNDATION", age_months=13),
            ],
            params=_quiet_params(),
        )
    )
    assert _moves_of(result, "G1") == [(1, "FOUNDATION", "BREEDING", "breeding")]
    assert any("Graduate G1 to BREEDING" in h for _, _, h in _tasks_on(result, 1))
    assert result.totals.services == 1
    assert any(t.headline == "Breed D1 — sire G1" for t in result.days[0].tasks)


def test_foundation_male_waits_for_the_sire_age_gate() -> None:
    """An 11-month grower (dob day -334: 11 × 30.44 = 334.84 → 335) reaches
    12 months (365.28 days) on day 32 — no service before, graduation and
    service on the day itself."""
    result = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=40,
            seed=1,
            animals=[
                _doe(),
                AnimalStartSpec(tag="G1", sex="M", bucket="FOUNDATION", age_months=11),
            ],
            params=_quiet_params(),
        )
    )
    assert _moves_of(result, "G1") == [(32, "FOUNDATION", "BREEDING", "breeding")]
    assert result.totals.services == 1
    assert any(t.headline == "Breed D1 — sire G1" for t in result.days[31].tasks)
    assert all("Breed D1" not in t.headline for record in result.days[:31] for t in record.tasks)


def test_quarantined_male_releases_then_graduates_same_day() -> None:
    """A mature male finishing quarantine (protocol day 45 on day 1) is
    released to FOUNDATION in the 09:00 round and graduates to BREEDING in
    the same day's breeding phase — the two hops land on one day, in order."""
    result = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=7,
            seed=1,
            animals=[
                AnimalStartSpec(
                    tag="Q1",
                    sex="M",
                    bucket="QUARANTINE",
                    age_months=24,
                    days_in_bucket=44,
                )
            ],
            params=_quiet_params(),
        )
    )
    assert _moves_of(result, "Q1") == [
        (1, "QUARANTINE", "FOUNDATION", "quarantine_release"),
        (1, "FOUNDATION", "BREEDING", "breeding"),
    ]


def test_no_buck_note_distinguishes_future_sires_from_no_male() -> None:
    """With no standing buck the note must say when services can still begin
    (a young sire exists somewhere) and when they cannot (no male at all)."""
    future = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=7,
            animals=[_doe(), AnimalStartSpec(tag="G1", sex="M", bucket="FOUNDATION", age_months=6)],
            params=_quiet_params(),
        )
    )
    assert future.totals.services == 0
    assert any("No buck stood in BREEDING" in note for note in future.notes)
    assert any("12-month age gate" in note for note in future.notes)

    young_in_pen = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=7,
            animals=[_doe(), AnimalStartSpec(tag="B1", sex="M", bucket="BREEDING", age_months=2)],
            params=_quiet_params(),
        )
    )
    assert any("No buck stood in BREEDING" in note for note in young_in_pen.notes)

    none = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=7,
            animals=[_doe()],
            params=_quiet_params(),
        )
    )
    assert any("No buck in this herd" in note for note in none.notes)


def test_male_kids_are_flagged_as_never_becoming_the_replacement_sire() -> None:
    """The exits model force-sells every male kid entering the meat window
    (male_sale_age_months = 8, below the 12-month sire gate), so the
    MALE_KIDS -> BREEDING edge of the legal bucket graph is unreachable in
    the simulator: a herd whose only males are kids stays sterile for the
    whole run. The notes must say so instead of letting the kids read as
    future sires."""
    kids_only = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=7,
            animals=[
                _doe(),
                AnimalStartSpec(tag="K1", sex="M", bucket="MALE_KIDS", age_months=7),
            ],
            params=_quiet_params(),
        )
    )
    assert any("No buck in this herd" in note for note in kids_only.notes)
    assert any("does not retain a kid as a replacement sire" in note for note in kids_only.notes)
    # The limitation note is about the KID pen: a herd with a Foundation
    # grower (a reachable future sire) keeps the ordinary future-sire note
    # and carries no kid-retention caveat.
    with_grower = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=7,
            animals=[_doe(), AnimalStartSpec(tag="G1", sex="M", bucket="FOUNDATION", age_months=6)],
            params=_quiet_params(),
        )
    )
    assert any("No buck stood in BREEDING" in note for note in with_grower.notes)
    assert not any(
        "does not retain a kid as a replacement sire" in note for note in with_grower.notes
    )


# ---------------------------------------------------------------------------
# 8. 2026-09-20 audit regressions: second-pregnancy milestones (P1-4) and
#    the male starter dependent kid (P2-6)
# ---------------------------------------------------------------------------


def test_second_pregnancy_fires_its_full_milestone_set() -> None:
    """P1-4 (2026-09-20 audit): a starter arriving mid-pregnancy carries a
    start-gestation ``milestone_floor`` so passed milestones do not
    retro-fire — but the floor (and the done-flags) were never reset on her
    next service, so her SECOND pregnancy fired zero milestones: no
    PREGNANCY_LATE move, no pre-kidding vaccines, no DELIVERY entry, no
    kidding watch.

    P1 starts at gestation day 120 (only the booster, the DELIVERY move and
    the kidding remain of pregnancy 1), kidded day 31, weans day 91, is
    re-served day 121 (RESTING flush complete + same-day breeding). Her
    second pregnancy must run the FULL calendar: scan day 153 (121 + 32),
    PREGNANCY_LATE day 221 (121 + 100), primary vaccine day 231 (EKD 271 −
    40), booster day 246 (EKD − 25), DELIVERY day 256 (EKD − 15) and kidding
    day 271 (EKD)."""
    payload = DailyOpsInput(
        start_date=date(2026, 9, 3),
        horizon_days=280,
        seed=1,
        animals=[
            AnimalStartSpec(
                tag="P1", sex="F", bucket="PREGNANCY_LATE", age_months=30, bred_days_ago=120
            ),
            _buck(),
        ],
        params=_quiet_params(),
    )
    result = run_daily_ops(payload)
    assert _moves_of(result, "P1") == [
        # pregnancy 1 (started at gestation day 120: primary vaccine is history)
        (16, "PREGNANCY_LATE", "DELIVERY", "delivery"),
        (31, "DELIVERY", "RECOVERY", "kidding"),
        (91, "RECOVERY", "RESTING", "weaning"),
        (121, "RESTING", "BREEDING", "breeding"),
        # pregnancy 2 — the milestone set the stale floor used to suppress
        (153, "BREEDING", "PREGNANCY_EARLY", "ultrasound"),
        (221, "PREGNANCY_EARLY", "PREGNANCY_LATE", "manual"),
        (256, "PREGNANCY_LATE", "DELIVERY", "delivery"),
        (271, "DELIVERY", "RECOVERY", "kidding"),
    ]
    # Vaccine duties: pregnancy 1's lone booster (day 6), then BOTH doses of
    # pregnancy 2 on their hand-derived days — the pre-kidding pair the bug
    # suppressed entirely.
    vaccine_days = [
        record.day for record in result.days for task in record.tasks if task.category == "VACCINE"
    ]
    assert vaccine_days == [6, 231, 246]
    assert any("Pre-kidding ET+TT vaccine: P1" in h for _, _, h in _tasks_on(result, 231))
    assert any("booster: P1" in h for _, _, h in _tasks_on(result, 246))
    # The second pregnancy's DELIVERY entry precedes its kidding, with the
    # kidding watch on the due day itself.
    assert any("Move P1 to DELIVERY" in h for _, _, h in _tasks_on(result, 256))
    assert any("Kidding due: P1" in h for _, _, h in _tasks_on(result, 271))
    assert any("Record kidding: P1 — 1 live of 1" in h for _, _, h in _tasks_on(result, 271))
    second_kid = _journey(result, "P1-2")
    assert (second_kid.sex, second_kid.born_day, second_kid.dam_tag) == ("F", 271, "P1")
    # Only the day-121 re-service happens in-sim (pregnancy 1 predates the run).
    assert result.totals.services == 1
    assert result.totals.conceptions == 1
    assert result.totals.kids_born_alive == 2


def test_recovery_starter_male_kid_weans_by_age() -> None:
    """P2-6 (2026-09-20 audit): the weaning loop used to filter on sex == "F",
    so the age-wean fallback never fired for a MALE dependent starter kid —
    stranded in RECOVERY for life, off the creep ration past day 60 and under
    the pre-weaning mortality hazard forever. The male mirror of
    test_recovery_starter_kid_weans_by_age: a 9-month starter kid weans on
    day 1 (and, past the 8-month meat window, sells the same day); a 1-month
    kid (30 days old on day 1) weans on day 31 = 60 − 30 days of age and then
    eats the MALE_KIDS ration."""
    result = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=35,
            seed=1,
            animals=[
                AnimalStartSpec(
                    tag="RK1", sex="M", bucket="RECOVERY", age_months=9, dependent_kid=True
                ),
                AnimalStartSpec(
                    tag="RK2",
                    sex="M",
                    bucket="RECOVERY",
                    age_months=1,
                    dependent_kid=True,
                    days_in_bucket=30,
                ),
            ],
            params=_quiet_params(),
        )
    )
    # Both leave RECOVERY for MALE_KIDS via the age-wean "weaning" edge.
    assert _moves_of(result, "RK1") == [(1, "RECOVERY", "MALE_KIDS", "weaning")]
    assert _moves_of(result, "RK2") == [(31, "RECOVERY", "MALE_KIDS", "weaning")]
    assert any("Age wean RK1" in h for _, _, h in _tasks_on(result, 1))
    assert any("Age wean RK2" in h for _, _, h in _tasks_on(result, 31))
    # RK1 is 9 months old at weaning — already inside the meat window, so the
    # sales phase (after weaning) sells him the same day; RK2 lands at ~2
    # months and grows on in MALE_KIDS.
    old = _journey(result, "RK1")
    assert (old.final_status, old.exit_day, old.exit_kind) == ("SOLD", 1, "SOLD")
    young = _journey(result, "RK2")
    assert (young.final_status, young.final_bucket) == ("ACTIVE", "MALE_KIDS")
    # Post-wean feed lines: RK2 eats the creep ration (0.1 kg, 14–30 d band)
    # on day 1 while dependent, and the MALE_KIDS line from the morning after
    # his weaning (age 61 d → the frame-builder LACTATING_60_40 at 1.0 kg).
    day1 = {
        (line.building, line.recipe): line
        for line in result.days[0].feeding
        if line.building in ("RECOVERY", "MALE_KIDS")
    }
    assert set(day1) == {("RECOVERY", "CREEP")}
    assert day1[("RECOVERY", "CREEP")].heads == 1
    day32 = [line for line in result.days[31].feeding if line.building == "MALE_KIDS"]
    assert [(line.recipe, line.heads, line.kg_per_head) for line in day32] == [
        ("LACTATING_60_40", 1, pytest.approx(1.0))
    ]

    # Weaning ends the pre-weaning hazard the same day it fires (phase 4
    # weaning runs before phase 5 mortality): a 3-month starter kid (91 days
    # old) weans on day 1 and must SURVIVE certain kid mortality — with the
    # old sex filter he stood dependent in RECOVERY and died on day 1.
    hazard = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 9, 3),
            horizon_days=60,
            seed=1,
            animals=[
                AnimalStartSpec(
                    tag="MK1", sex="M", bucket="RECOVERY", age_months=3, dependent_kid=True
                )
            ],
            params=_quiet_params(kid_pre_weaning_mortality=1.0),
        )
    )
    kid = _journey(hazard, "MK1")
    assert _moves_of(hazard, "MK1") == [(1, "RECOVERY", "MALE_KIDS", "weaning")]
    assert (kid.final_status, kid.final_bucket) == ("ACTIVE", "MALE_KIDS")
    assert hazard.totals.deaths == 0
    # At 91 days he is past the creep ramp, so day 1 plans no ration for him
    # (the stranding symptom); from day 2 he is 92 days old and eats the
    # day-91+ FATTENING line of the MALE_KIDS pen.
    assert [line for line in hazard.days[0].feeding] == []
    day2 = [line for line in hazard.days[1].feeding if line.building == "MALE_KIDS"]
    assert [(line.recipe, line.heads, line.kg_per_head) for line in day2] == [
        ("FATTENING_50_50", 1, pytest.approx(1.0))
    ]
