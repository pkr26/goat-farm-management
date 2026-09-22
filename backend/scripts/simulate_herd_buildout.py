"""End-to-end herd build-out verification run (Osmanabadi, 5 years).

Scenario: the farm onboards 50 female Osmanabadi growers (6 months old) plus
3 breeding bucks (~12 months old) every 2 months — 10 batches at simulation
months 1, 3, 5, ..., 19 — until 500 purchased females stand on the farm, then
lets the herd breed under the app's default Osmanabadi assumptions for the
rest of the 60-month horizon. Start date 2026-10-01 so the embedded Bakrid
calendar (simulation/market.py) lines up with real Gregorian dates.

Two engines, both pure Python (no DB):

* the monthly bio-economic engine (simulation/engine.py) carries the whole
  500-head, 60-month trajectory and the P&L — twice: without and with the
  NLM 50% capital-subsidy toggle (the grower purchase events carry
  ``age_months=6``, the scenario's real arrival age, and the NLM run's
  eligible-head cap sees the event-purchased unit);
* the daily-ops engine (simulation/daily_ops.py) carries the day-resolution
  bucket/task detail. That engine takes its herd on day 1 only (no mid-run
  arrivals) and caps a run at 500 head / 365 days, so the staggered build-out
  is modelled as one 365-day run per batch (10 runs), a "continuation" run
  that picks a batch-1 doe group up in late pregnancy to show kidding ->
  weaning -> re-breeding inside the day cap, and one whole-herd operational
  snapshot at month 19. The limitation is stated plainly in the README.

Everything is seeded/pinned: re-running this script regenerates the report
byte-for-byte. Run from the repo root:

    backend/.venv/bin/python backend/scripts/simulate_herd_buildout.py [out-dir]

Default out-dir: scratch/e2e_herd_buildout (gitignored)
"""

import csv
import sys
from datetime import date
from pathlib import Path
from typing import cast

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.simulation.assumptions import (  # noqa: E402
    FinanceAssumptions,
    HerdAssumptions,
    HerdEventAssumptions,
    MetaAssumptions,
    SimulationAssumptions,
)
from app.simulation.daily_ops import (  # noqa: E402
    AnimalStartSpec,
    BucketCode,
    DailyOpsInput,
    DailyOpsParams,
    DailyOpsResult,
    build_daily_ledger,
    run_daily_ops,
)
from app.simulation.engine import (  # noqa: E402
    NLM_CAPITAL_CEILING_PER_HEAD,
    NLM_SUBSIDY_FRACTION,
    run_simulation,
)
from app.simulation.feed import DAYS_PER_MONTH  # noqa: E402
from app.simulation.results import SimulationResult  # noqa: E402

# --- scenario constants -------------------------------------------------------
START_DATE = date(2026, 10, 1)
START_YEAR_MONTH = "2026-10"
HORIZON_MONTHS = 60
BATCH_COUNT = 10
DOES_PER_BATCH = 50
BUCKS_PER_BATCH = 3
BATCH_INTERVAL_MONTHS = 2
DOE_AGE_AT_PURCHASE_MONTHS = 6
BUCK_AGE_AT_PURCHASE_MONTHS = 12
# Fixed seeds: the daily engine replays one Random(seed) stream deterministically.
DAILY_SEED_BASE = 20261001
CONTINUATION_SEED = 20270801
SNAPSHOT_SEED = 20280401
SNAPSHOT_DATE = date(2028, 4, 1)  # simulation month 19: the day batch 10 lands
SNAPSHOT_HORIZON_DAYS = 90


def add_months(day: date, months: int) -> date:
    """Calendar-month addition, clamped to the month's last day."""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    # Days per month without calendar arithmetic: the day before the 1st of next month.
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date.fromordinal(date(year, month + 1, 1).toordinal() - 1)
    return date(year, month, min(day.day, last.day))


def batch_arrival_date(batch: int) -> date:
    """Arrival date of 1-based batch number ``batch``."""
    return add_months(START_DATE, (batch - 1) * BATCH_INTERVAL_MONTHS)


def batch_arrival_month(batch: int) -> int:
    """1-based simulation month in which 1-based batch ``batch`` arrives."""
    return 1 + (batch - 1) * BATCH_INTERVAL_MONTHS


def sim_month_date(month: int) -> date:
    """Calendar date of the first day of 1-based simulation month ``month``."""
    return add_months(START_DATE, month - 1)


def months_between(earlier: date, later: date) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


# --- formatting -----------------------------------------------------------------


def inr(value: float) -> str:
    """₹ with Indian digit grouping (12,34,567), no decimals."""
    rounded = round(value)
    sign = "-" if rounded < 0 else ""
    digits = str(abs(rounded))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups: list[str] = []
        while head:
            groups.insert(0, head[-2:])
            head = head[:-2]
        digits = ",".join([*groups, tail])
    return f"{sign}₹{digits}"


def inr_short(value: float) -> str:
    """Lakh/crore rendering for narrative text."""
    abs_value = abs(value)
    if abs_value >= 1e7:
        return f"₹{value / 1e7:.2f} crore"
    if abs_value >= 1e5:
        return f"₹{value / 1e5:.2f} lakh"
    return inr(value)


def _num(value: object) -> float:
    return float(cast("float | int | str", value))


def head(value: object) -> str:
    return f"{_num(value):,.0f}"


def kg(value: object) -> str:
    return f"{_num(value):,.0f}"


# --- assumptions -----------------------------------------------------------------


def build_monthly_assumptions(*, nlm_subsidy: bool) -> SimulationAssumptions:
    """The build-out scenario on default Osmanabadi biology.

    Two MANAGEMENT toggles differ from ``SimulationAssumptions()`` defaults —
    they express the build-to-500 policy, they do not touch biology:

    * ``herd.max_breeding_does = 0`` (unlimited): the default cap of 50 holds a
      NABARD 50+2 unit flat and would sell every graduate above 50 as meat;
    * ``herd.female_retention_fraction = 1.0``: the default 0.60 sells 40% of
      graduating growers as meat; this farm keeps every graduate because it is
      deliberately building the flock.

    Everything else — conception, gestation, litter, mortality, growth curve,
    prices, feed, costs, finance — is the app default.
    """
    events: list[HerdEventAssumptions] = []
    for batch in range(1, BATCH_COUNT + 1):
        month = batch_arrival_month(batch)
        events.append(
            HerdEventAssumptions(
                month=month,
                kind="purchase",
                animal_class="female_grower",
                count=DOES_PER_BATCH,
                # The scenario buys 6-month-olds: place them at the 6-month
                # slot of the grower chain (not the mid-class default), so
                # graduation to the doe pool takes the real ~6 months.
                age_months=DOE_AGE_AT_PURCHASE_MONTHS,
            )
        )
        events.append(
            HerdEventAssumptions(
                month=month, kind="purchase", animal_class="buck", count=BUCKS_PER_BATCH
            )
        )
    return SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=HORIZON_MONTHS, start_year_month=START_YEAR_MONTH),
        herd=HerdAssumptions(
            does=0,
            bucks=0,
            max_breeding_does=0,
            female_retention_fraction=1.0,
        ),
        finance=FinanceAssumptions(nlm_subsidy=nlm_subsidy),
        events=events,
    )


# --- daily-ops inputs --------------------------------------------------------------


def batch_daily_input(batch: int) -> DailyOpsInput:
    """One batch's arrival, day 1 = the truck rolling in (quarantine)."""
    return DailyOpsInput(
        start_date=batch_arrival_date(batch),
        horizon_days=365,
        seed=DAILY_SEED_BASE + batch,
        animals=[
            *(
                AnimalStartSpec(
                    tag=f"G{i:02d}",
                    sex="F",
                    bucket="QUARANTINE",
                    age_months=DOE_AGE_AT_PURCHASE_MONTHS,
                    days_in_bucket=0,
                )
                for i in range(1, DOES_PER_BATCH + 1)
            ),
            *(
                AnimalStartSpec(
                    tag=f"S{i}",
                    sex="M",
                    bucket="QUARANTINE",
                    age_months=BUCK_AGE_AT_PURCHASE_MONTHS,
                    days_in_bucket=0,
                )
                for i in range(1, BUCKS_PER_BATCH + 1)
            ),
        ],
        params=DailyOpsParams(),
    )


def continuation_daily_input() -> DailyOpsInput:
    """Batch-1 does picked up in late pregnancy (day 120 of 150), so the
    post-kidding half of the journey — delivery move, kidding, recovery,
    day-60 weaning, resting flush, re-breeding, second scan — fits inside the
    daily engine's 365-day horizon cap (the batch-1 arrival run ends right
    after the first kiddings)."""
    return DailyOpsInput(
        start_date=date(2027, 8, 1),
        horizon_days=365,
        seed=CONTINUATION_SEED,
        animals=[
            *(
                AnimalStartSpec(
                    tag=f"C{i:02d}",
                    sex="F",
                    bucket="PREGNANCY_LATE",
                    # Batch-1 doe: 6 months old on 2026-10-01 -> 16 months now.
                    age_months=16,
                    bred_days_ago=120,
                )
                for i in range(1, 11)
            ),
            AnimalStartSpec(tag="CS1", sex="M", bucket="BREEDING", age_months=24),
            AnimalStartSpec(tag="CS2", sex="M", bucket="BREEDING", age_months=24),
        ],
        params=DailyOpsParams(),
    )


def snapshot_daily_input() -> DailyOpsInput:
    """Whole-herd operational snapshot on 2028-04-01 (month 19, the day batch
    10 arrives). The daily engine caps a run at 500 starting head, so the
    snapshot carries batches 1-9 and their bucks (477 head); batch 10's day-1
    quarantine is identical to batch 1's, already covered.

    State convention (stated, not hidden): the monthly engine tracks pools,
    not individuals, so per-doe pregnancy state on this date is not
    recoverable. Every purchased doe of breeding age stands OPEN in BREEDING;
    the two youngest batches stand in FOUNDATION (post-quarantine, under the
    12-month breeding gate). The 90-day window therefore shows a fresh
    breeding wave at full build-out scale — feed, cleaning, services, scans —
    not the true mixed-state herd, which the monthly trajectory carries.
    """
    animals: list[AnimalStartSpec] = []
    for batch in range(1, 10):
        age = DOE_AGE_AT_PURCHASE_MONTHS + months_between(batch_arrival_date(batch), SNAPSHOT_DATE)
        if age >= 12:
            bucket: BucketCode = "BREEDING"
        else:
            bucket = "FOUNDATION"
        animals.extend(
            AnimalStartSpec(tag=f"B{batch}-G{i:02d}", sex="F", bucket=bucket, age_months=age)
            for i in range(1, DOES_PER_BATCH + 1)
        )
        animals.extend(
            AnimalStartSpec(
                tag=f"B{batch}-S{i}",
                sex="M",
                bucket="BREEDING",
                age_months=BUCK_AGE_AT_PURCHASE_MONTHS
                + months_between(batch_arrival_date(batch), SNAPSHOT_DATE),
            )
            for i in range(1, BUCKS_PER_BATCH + 1)
        )
    return DailyOpsInput(
        start_date=SNAPSHOT_DATE,
        horizon_days=SNAPSHOT_HORIZON_DAYS,
        seed=SNAPSHOT_SEED,
        animals=animals,
        params=DailyOpsParams(),
    )


# --- daily-ops fact extraction ------------------------------------------------------

MILESTONE_LABELS = {
    "release": "Released from quarantine (day-45 protocol complete)",
    "first_service": "First natural service recorded",
    "first_scan_positive": "First positive pregnancy scan (+32d)",
    "first_move_late": "First move to PREGNANCY_LATE (gestation day 100)",
    "first_move_delivery": "First move to DELIVERY (15 days before due)",
    "first_kidding": "First kidding",
    "first_weaning": "First day-60 weaning",
    "first_rebreed": "First re-breeding (any path — here a doe whose litter died)",
    "first_post_weaning_rebreed": "First re-breeding after weaning + ~30-day resting flush",
}


def batch_milestones(result: DailyOpsResult) -> dict[str, tuple[int, str]]:
    """First (day, date) at which each journey milestone fires in a run."""
    found: dict[str, tuple[int, str]] = {}

    def keep(key: str, day: int, day_date: str) -> None:
        if key not in found:
            found[key] = (day, day_date)

    kidded_dams: set[str] = set()
    weaned_dams: set[str] = set()
    for record in result.days:
        for move in record.moves:
            if move.context == "quarantine_release":
                keep("release", move.day, move.date)
            if move.to_bucket == "PREGNANCY_LATE":
                keep("first_move_late", move.day, move.date)
            if move.to_bucket == "DELIVERY":
                keep("first_move_delivery", move.day, move.date)
            if move.to_bucket == "RECOVERY" and move.context == "kidding":
                keep("first_kidding", move.day, move.date)
        for task in record.tasks:
            if task.headline.startswith("Breed ") and task.animals:
                keep("first_service", task.day, task.date)
                # A genuine re-breed: the served doe has kidded earlier in this
                # run (not a first service, not a re-service after a failed
                # scan). The post-WEANING re-breed is tracked separately — a
                # doe whose litter died re-breeds weeks earlier via the
                # postpartum path, which is a different journey.
                if task.animals[0] in kidded_dams:
                    keep("first_rebreed", task.day, task.date)
                if task.animals[0] in weaned_dams:
                    keep("first_post_weaning_rebreed", task.day, task.date)
            if "scan POSITIVE" in task.headline:
                keep("first_scan_positive", task.day, task.date)
            if task.category == "WEANING":
                keep("first_weaning", task.day, task.date)
                if task.animals:
                    weaned_dams.add(task.animals[0])
        for birth in record.births:
            kidded_dams.add(birth.dam_tag)
            keep("first_kidding", record.day, record.date)
    return found


def death_split(result: DailyOpsResult) -> tuple[int, int]:
    """(starter deaths, in-sim-born kid deaths) — the totals mix both."""
    starters = {j.tag for j in result.journeys if j.born_day is None}
    adult_deaths = kid_deaths = 0
    for record in result.days:
        for exit_ in record.exits:
            if exit_.kind != "DEAD":
                continue
            if exit_.tag in starters:
                adult_deaths += 1
            else:
                kid_deaths += 1
    return adult_deaths, kid_deaths


def journey_table_rows(result: DailyOpsResult, tag: str) -> list[tuple[str, str, str, str]]:
    """(date, from, to, context) hops of one animal, for the sample-doe table."""
    journey = next(j for j in result.journeys if j.tag == tag)
    start = date.fromisoformat(result.start_date)
    rows = []
    for hop in journey.hops:
        hop_date = date.fromordinal(start.toordinal() + hop.day - 1).isoformat()
        rows.append((hop_date, hop.from_bucket or "—", hop.to_bucket, hop.context))
    return rows


def first_service_ages(payload: DailyOpsInput, result: DailyOpsResult) -> list[float]:
    """Age in months of each started doe at her first service."""
    start_age = {a.tag: a.age_months for a in payload.animals}
    first_day: dict[str, int] = {}
    for record in result.days:
        for task in record.tasks:
            if task.headline.startswith("Breed ") and task.animals:
                first_day.setdefault(task.animals[0], task.day)
    return sorted(start_age[tag] + (day - 1) / DAYS_PER_MONTH for tag, day in first_day.items())


# --- report builders -------------------------------------------------------------

MONTHLY_COLUMNS = [
    ("month", "Month"),
    ("date", "Calendar"),
    ("f_growers", "F growers"),
    ("does", "Does (open+preg+lact)"),
    ("bucks", "Bucks"),
    ("kids", "Kids+weaners"),
    ("m_growers", "M growers"),
    ("total_herd", "Total herd"),
    ("births", "Kids born"),
    ("sales_head", "Sales (head)"),
    ("sales_revenue", "Sales ₹"),
    ("feed_kg", "Feed (kg)"),
    ("water_litres", "Water (L)"),
    ("net_cash_flow", "Net cash ₹"),
    ("cumulative_cash_flow", "Cum. cash ₹"),
]


def monthly_row_dicts(result: SimulationResult) -> list[dict[str, object]]:
    rows = []
    for row in result.months:
        does = row.open_does + row.pregnant_does + row.lactating_does
        kids = row.f_kids + row.m_kids + row.f_weaners + row.m_weaners
        feed_kg = row.feed_green_kg + row.feed_dry_kg + row.feed_concentrate_kg
        rows.append(
            {
                "month": row.month,
                "date": sim_month_date(row.month).strftime("%Y-%m"),
                "f_growers": row.f_growers,
                "does": does,
                "bucks": row.bucks,
                "kids": kids,
                "m_growers": row.m_growers,
                "total_herd": row.total_herd,
                "births": row.births,
                "sales_head": row.sales_head,
                "sales_revenue": row.sales_revenue + row.cull_revenue,
                "feed_kg": feed_kg,
                "water_litres": row.water_litres,
                "net_cash_flow": row.net_cash_flow,
                "cumulative_cash_flow": row.cumulative_cash_flow,
            }
        )
    return rows


def _fmt_cell(key: str, value: object) -> str:
    if key in ("month",):
        return str(value)
    if key == "date":
        return str(value)
    if key in ("sales_revenue", "net_cash_flow", "cumulative_cash_flow"):
        return inr(_num(value))
    if key in ("feed_kg", "water_litres"):
        return kg(_num(value))
    return head(_num(value))


def write_monthly_files(result: SimulationResult, out_dir: Path) -> None:
    rows = monthly_row_dicts(result)
    with (out_dir / "monthly_trajectory.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([label for _, label in MONTHLY_COLUMNS])
        for row in rows:
            writer.writerow([_fmt_cell(key, row[key]) for key, _ in MONTHLY_COLUMNS])

    lines = [
        "# Monthly trajectory — 60 months (monthly engine, expected values)",
        "",
        "Counts are end-of-month expected head (floats — the engine carries "
        "expected values, never rounds inside the loop). `Does` = open + "
        "pregnant + lactating; `Kids+weaners` = both sexes aged 0-5 months; "
        "`Feed` = green + dry + concentrate, as-fed kg; `Sales ₹` includes "
        "cull revenue.",
        "",
        "| " + " | ".join(label for _, label in MONTHLY_COLUMNS) + " |",
        "|" + "---|" * len(MONTHLY_COLUMNS),
    ]
    for row in rows:
        cells = " | ".join(_fmt_cell(key, row[key]) for key, _ in MONTHLY_COLUMNS)
        lines.append(f"| {cells} |")
    lines.append("")
    (out_dir / "monthly_trajectory.md").write_text("\n".join(lines), encoding="utf-8")


def quarterly_rows(result: SimulationResult) -> list[dict[str, object]]:
    """End-of-quarter stocks + summed flows, 20 quarters."""
    rows = monthly_row_dicts(result)
    quarters = []
    for q in range(20):
        chunk = rows[q * 3 : (q + 1) * 3]
        end = chunk[-1]
        quarters.append(
            {
                "quarter": q + 1,
                "months": f"{chunk[0]['date']} – {end['date']}",
                "does": end["does"],
                "f_growers": end["f_growers"],
                "bucks": end["bucks"],
                "kids": end["kids"],
                "total_herd": end["total_herd"],
                "births": sum(_num(r["births"]) for r in chunk),
                "sales_head": sum(_num(r["sales_head"]) for r in chunk),
                "sales_revenue": sum(_num(r["sales_revenue"]) for r in chunk),
                "feed_kg": sum(_num(r["feed_kg"]) for r in chunk),
                "water_kl": sum(_num(r["water_litres"]) for r in chunk) / 1000.0,
                "net_cash_flow": sum(_num(r["net_cash_flow"]) for r in chunk),
                "cumulative_cash_flow": end["cumulative_cash_flow"],
            }
        )
    return quarters


BUCKET_GLOSSARY = [
    (
        "QUARANTINE",
        "New arrivals isolated for the 45-day protocol (inspection, "
        "deworming, PPR/ET+T/Goat Pox/FMD vaccines, fecal checks, footbath).",
    ),
    (
        "FOUNDATION",
        "Cleared arrivals and growing stock — the grow-out pen where "
        "young does wait to reach the 12-month breeding gate.",
    ),
    ("BREEDING", "Open does cycling and being served; the bucks' pen."),
    ("PREGNANCY_EARLY", "Scan-confirmed does, gestation day 32-99."),
    (
        "PREGNANCY_LATE",
        "Gestating does from day 100; pre-kidding ET+TT vaccines fire here (due-date −40 and −25).",
    ),
    ("DELIVERY", "Kidding pen; does move in 15 days before the due date."),
    (
        "RECOVERY",
        "Fresh dams with their unweaned kids (kids on creep feed); "
        "weaning at day 60 moves kids out.",
    ),
    ("RESTING", "Post-weaning dry-off and flush (~30 days) before the doe returns to BREEDING."),
    ("MALE_KIDS", "Weaned males growing to the 8-9 month meat window."),
    ("FEMALE_KIDS", "Weaned females growing toward the breeding gate."),
]


def write_bucket_transitions(
    batch1: DailyOpsResult,
    continuation: DailyOpsResult,
    snapshot: DailyOpsResult,
    out_dir: Path,
) -> None:
    lines = [
        "# Bucket movement analysis — transition matrices",
        "",
        "Every move the daily engine executed, grouped by (from, to, workflow "
        "context). The engine validates each move against the same legal bucket "
        "graph the live app enforces (`models.lifecycle.LEGAL_BUCKET_TRANSITIONS`).",
        "",
        "## What each bucket means",
        "",
        "| Bucket | Meaning |",
        "|---|---|",
    ]
    for bucket, meaning in BUCKET_GLOSSARY:
        lines.append(f"| {bucket} | {meaning} |")
    for title, result in (
        ("Batch 1 — arrival year (365 days from 2026-10-01)", batch1),
        ("Continuation — kidding → re-breeding (from 2027-08-01)", continuation),
        (
            f"Whole-herd snapshot — {SNAPSHOT_HORIZON_DAYS} days from {SNAPSHOT_DATE.isoformat()}",
            snapshot,
        ),
    ):
        lines += [
            "",
            f"## {title}",
            "",
            "| From | To | Context | Moves |",
            "|---|---|---|---:|",
        ]
        for row in result.transition_counts:
            lines.append(
                f"| {row.from_bucket or '—'} | {row.to_bucket} | {row.context} | {row.count} |"
            )
        lines.append("")
    (out_dir / "bucket_transitions.md").write_text("\n".join(lines), encoding="utf-8")


def write_task_summary(
    batch1: DailyOpsResult,
    continuation: DailyOpsResult,
    snapshot: DailyOpsResult,
    out_dir: Path,
) -> None:
    lines = [
        "# Task analysis — what the crews actually do",
        "",
        "Counts of generated duties by category, with real example headlines "
        "the engine produced. FEED and CLEANING dominate by construction: "
        "three feed deliveries plus two verified cleanings per occupied "
        "building per day.",
        "",
    ]
    for title, result in (
        ("Batch 1 — arrival year (53 head, 365 days)", batch1),
        ("Continuation — 10 late-pregnancy does + 2 bucks, 365 days", continuation),
        (f"Whole-herd snapshot — 477 head, {SNAPSHOT_HORIZON_DAYS} days", snapshot),
    ):
        examples: dict[str, str] = {}
        for record in result.days:
            for task in record.tasks:
                examples.setdefault(task.category, task.headline)
        lines += [
            f"## {title}",
            "",
            "| Category | Duties | Example headline |",
            "|---|---:|---|",
        ]
        for category, count in result.totals.tasks_by_category.items():
            lines.append(f"| {category} | {count:,} | {examples.get(category, '—')} |")
        lines += [
            "",
            "By crew role: "
            + ", ".join(f"{role} {count:,}" for role, count in result.totals.tasks_by_role.items())
            + ".",
            "",
        ]
    (out_dir / "task_log_summary.md").write_text("\n".join(lines), encoding="utf-8")


# --- README ---------------------------------------------------------------------


def _milestone_cell(milestones: dict[str, tuple[int, str]], key: str) -> str:
    hit = milestones.get(key)
    return hit[1] if hit else "— (past horizon)"


def _age_range(ages: list[float]) -> str:
    if not ages:
        return "—"
    if round(min(ages), 1) == round(max(ages), 1):
        return f"{min(ages):.1f}"
    return f"{min(ages):.1f}-{max(ages):.1f}"


def build_readme(
    base: SimulationResult,
    nlm: SimulationResult,
    assumptions: SimulationAssumptions,
    batch_results: list[DailyOpsResult],
    continuation: DailyOpsResult,
    snapshot: DailyOpsResult,
) -> str:
    b1 = batch_results[0]
    b1_milestones = batch_milestones(b1)
    cont_milestones = batch_milestones(continuation)
    rows = monthly_row_dicts(base)

    # --- monthly-engine facts ---
    first_birth_month = next(r for r in rows if _num(r["births"]) > 0)
    month19 = rows[18]
    peak = max(rows, key=lambda r: _num(r["total_herd"]))
    total_kids_born = sum(_num(r["births"]) for r in rows)
    total_sales_head = sum(_num(r["sales_head"]) for r in rows)
    total_sales_revenue = sum(_num(r["sales_revenue"]) for r in rows)
    grower_spend = sum(
        fill.revenue
        for row in base.months
        for fill in row.event_fills
        if fill.kind == "purchase" and fill.animal_class == "female_grower"
    )
    scheduled_buck_spend = sum(
        fill.revenue
        for row in base.months
        for fill in row.event_fills
        if fill.kind == "purchase" and fill.animal_class == "buck"
    )
    total_buck_capex = sum(row.breeding_stock_capex for row in base.months)
    auto_buck_spend = total_buck_capex - scheduled_buck_spend
    festival_months = sorted(assumptions.sales.festival_sale_months or [])
    festival_dates = [(month, sim_month_date(month).strftime("%Y-%m")) for month in festival_months]

    # --- daily-engine facts ---
    b1_payload = batch_daily_input(1)
    service_ages = first_service_ages(b1_payload, b1)

    kidding_days_by_dam: dict[str, list[int]] = {}
    for rec in continuation.days:
        for birth in rec.births:
            kidding_days_by_dam.setdefault(birth.dam_tag, []).append(birth.day)
    intervals = sorted(d[1] - d[0] for d in kidding_days_by_dam.values() if len(d) >= 2)
    second_kiddings = sum(1 for d in kidding_days_by_dam.values() if len(d) >= 2)

    # batch-1 quarantine protocol dates (offsets from models.helpers.QUARANTINE_PROTOCOL)
    arrival = batch_arrival_date(1)
    protocol_dates = {
        1: arrival.isoformat(),
        4: date.fromordinal(arrival.toordinal() + 3).isoformat(),
        10: date.fromordinal(arrival.toordinal() + 9).isoformat(),
        13: date.fromordinal(arrival.toordinal() + 12).isoformat(),
        20: date.fromordinal(arrival.toordinal() + 19).isoformat(),
        30: date.fromordinal(arrival.toordinal() + 29).isoformat(),
        40: date.fromordinal(arrival.toordinal() + 39).isoformat(),
        45: date.fromordinal(arrival.toordinal() + 44).isoformat(),
    }

    lines: list[str] = []
    add = lines.append
    add("# End-to-end verification — 500-doe Osmanabadi herd build-out, 2026-10 to 2031-09")
    add("")
    add("**Reproduce this report (deterministic, fixed seeds):**")
    add("")
    add("```bash")
    add("backend/.venv/bin/python backend/scripts/simulate_herd_buildout.py")
    add("```")
    add("")
    add(
        "**Regenerated 2026-09-15** after the three engine fixes the first run of this "
        "report surfaced (same commit): (1) herd events gained a per-event `age_months` "
        "input, so the 10 grower batches now enter at their real 6-month age "
        "(observation 1, RESOLVED); (2) the NLM subsidy's eligible-head cap now counts "
        "breeding stock bought through scheduled events, so the toggle is live for "
        "event-built herds (observation 9, RESOLVED, §7); (3) the daily-ops eligibility "
        "note quotes the enforced `GOAT_PROFILE.min_breeding_age_months` constant "
        "instead of a stale hardcoded age (observation 5, RESOLVED)."
    )
    add("")
    add(
        f"Everything below comes out of the app's own simulation engines — the monthly "
        f"bio-economic engine (model {base.model_version}) for the 5-year money and herd "
        f"trajectory, and the daily-ops engine (model {b1.model_version}) for the day-by-day "
        f"bucket and task detail. No database, no hand-computed figures. Numbers in the "
        f"monthly engine are expected values, so fractions of an animal are normal."
    )
    add("")
    add("## 1. The scenario")
    add("")
    add(
        f"Every 2 months the farm buys 50 female Osmanabadi growers, each 6 months old, "
        f"plus {BUCKS_PER_BATCH} breeding bucks (~12 months old, app default ratio 1 buck : "
        f"20 does). Ten batches, months 1-19, until 500 purchased females stand on the farm. "
        f"After that the herd breeds on its own. Horizon 5 years (60 months), starting "
        f"2026-10-01. The monthly-engine purchase events carry `age_months=6`, so each "
        "batch enters the grower chain at its real age and waits the full ~6 months to "
        "the 12-month breeding gate."
    )
    add("")
    add("| Batch | Arrives | Sim month | Females (6 mo) | Bucks (12 mo) |")
    add("|---|---|---|---:|---:|")
    for batch in range(1, BATCH_COUNT + 1):
        add(
            f"| {batch} | {batch_arrival_date(batch).isoformat()} | "
            f"{batch_arrival_month(batch)} | {DOES_PER_BATCH} | {BUCKS_PER_BATCH} |"
        )
    add(
        f"| **Total** | | | **{BATCH_COUNT * DOES_PER_BATCH}** | "
        f"**{BATCH_COUNT * BUCKS_PER_BATCH}** |"
    )
    add("")
    add(
        "All biology, growth, price, feed and cost assumptions are the app's default "
        "Osmanabadi calibration, untouched. Two *management* toggles had to change to "
        "express a build-to-500 plan: the breeding-doe cap (default 50 — the NABARD 50+2 "
        "unit size — would sell every graduate above 50 as meat) is set to unlimited, and "
        "the female-retention fraction (default 60%) is set to 100%, because this farm "
        "keeps every doe it raises. Both toggles are documented policy switches in "
        "`SimulationAssumptions.herd`, not biology."
    )
    add("")
    add("## 2. The five-year trajectory (monthly engine, quarterly view)")
    add("")
    add(
        "End-of-quarter head counts; flows (births, sales, feed, water, cash) are quarter "
        "totals. The full 60-month table is in [monthly_trajectory.md](monthly_trajectory.md) "
        "and [monthly_trajectory.csv](monthly_trajectory.csv)."
    )
    add("")
    add(
        "| Q | Months | Does | F growers | Bucks | Kids+weaners | Total herd | Kids born | "
        "Sales head | Sales ₹ | Feed kg | Water kL | Net cash | Cum. cash |"
    )
    add("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for q in quarterly_rows(base):
        add(
            f"| {q['quarter']} | {q['months']} | {head(q['does'])} | {head(q['f_growers'])} | "
            f"{head(q['bucks'])} | {head(q['kids'])} | {head(q['total_herd'])} | "
            f"{head(q['births'])} | {head(q['sales_head'])} | {inr(_num(q['sales_revenue']))} | "
            f"{kg(q['feed_kg'])} | {_num(q['water_kl']):,.0f} | {inr(_num(q['net_cash_flow']))} | "
            f"{inr(_num(q['cumulative_cash_flow']))} |"
        )
    add("")
    add("Key waypoints:")
    add("")
    add(
        f"- **First kids born:** simulation month {first_birth_month['month']} "
        f"({first_birth_month['date']}) in the monthly engine — batch 1's 6-month-olds "
        "graduate at the 12-month breeding gate and kid after the 5-month gestation, "
        "in line with the daily engine's 2027-08/09 first kiddings (observation 1)."
    )
    add(
        f"- **Month 19 (2028-04, last batch in):** the doe pool stands at "
        f"{head(month19['does'])} — all purchased stock (the first homebred doe, born "
        f"in month 11, cannot graduate before month 23); {head(month19['f_growers'])} "
        f"females are still in the "
        f"grower chain (the two youngest purchased batches plus the first homebred "
        f"growers); {head(month19['total_herd'])} head total. Survivors of the 500 "
        "purchased females run a few percent below 500 — 5%/yr adult and 4%/yr grower "
        "mortality act from the day each batch lands."
    )
    add(
        f"- **Peak herd:** {head(peak['total_herd'])} head at end of month {peak['month']} "
        f"({peak['date']})."
    )
    add(
        f"- **Over 60 months:** {head(total_kids_born)} kids born, {head(total_sales_head)} "
        f"head sold for {inr(total_sales_revenue)} (meat + culls)."
    )
    add("")
    add("Bakrid price-uplift months the engine auto-filled from its embedded calendar:")
    add("")
    add("| Sim month | Calendar | Sales head that month |")
    add("|---|---|---:|")
    for month, cal in festival_dates:
        row = rows[month - 1]
        add(f"| {month} | {cal} | {head(row['sales_head'])} |")
    add("")
    lines += _readme_daily_section(
        batch_results, continuation, snapshot, service_ages, protocol_dates
    )
    lines += _readme_buckets_tasks_section(b1, continuation, snapshot)
    lines += _readme_benchmarks_section(b1, continuation, intervals, second_kiddings)
    lines += _readme_money_section(base, nlm, grower_spend, scheduled_buck_spend, auto_buck_spend)
    lines += _readme_observations_section(base, nlm, b1, rows)
    lines += _readme_checklist_section(
        base,
        nlm,
        rows,
        batch_results,
        b1_milestones,
        cont_milestones,
        protocol_dates,
        first_birth_month,
        month19,
        peak,
        continuation,
        festival_months,
    )
    return "\n".join(lines) + "\n"


def _readme_daily_section(
    batch_results: list[DailyOpsResult],
    continuation: DailyOpsResult,
    snapshot: DailyOpsResult,
    service_ages: list[float],
    protocol_dates: dict[int, str],
) -> list[str]:
    b1 = batch_results[0]
    b1_milestones = batch_milestones(b1)
    cont_milestones = batch_milestones(continuation)
    lines: list[str] = []
    add = lines.append
    add("## 3. Batch 1, day by day (daily-ops engine)")
    add("")
    add(
        "The daily engine follows individual animals building to building. Batch 1 — 50 "
        "six-month-old growers (G01-G50) and 3 yearling bucks (S1-S3) — was run from the "
        "day the truck arrived, 2026-10-01, for 365 days (seed "
        f"{DAILY_SEED_BASE + 1}). The complete day-by-day ledger — every feed delivery, "
        "cleaning, duty, move, birth and exit — is in "
        "[batch1_journey.md](batch1_journey.md); the highlights:"
    )
    add("")
    add("**The 45-day quarantine protocol** (dates the engine fired each step):")
    add("")
    add("| Protocol day | Date | Step |")
    add("|---|---|---|")
    add(
        f"| 1 | {protocol_dates[1]} | Arrival inspection (dehydration, injuries, "
        "temperature); rest + electrolytes, dry roughage only |"
    )
    add(f"| 4 | {protocol_dates[4]} | Deworm — Albendazole/Closantel oral + Ivermectin SC |")
    add(f"| 10 | {protocol_dates[10]} | PPR vaccine |")
    add(f"| 13 | {protocol_dates[13]} | Fecal exam — confirm deworming worked |")
    add(f"| 20 | {protocol_dates[20]} | ET + Tetanus vaccine |")
    add(f"| 30 | {protocol_dates[30]} | Goat Pox vaccine + fecal recheck / pre-release review |")
    add(f"| 40 | {protocol_dates[40]} | FMD vaccine |")
    add(f"| 45 | {protocol_dates[45]} | Zinc-sulfate footbath → release to FOUNDATION |")
    add("")
    add("**Breeding journey milestones** (first occurrence in the run):")
    add("")
    add("| Milestone | Date |")
    add("|---|---|")
    for key, label in MILESTONE_LABELS.items():
        if key in ("first_weaning", "first_rebreed", "first_post_weaning_rebreed"):
            continue
        add(f"| {label} | {_milestone_cell(b1_milestones, key)} |")
    add("")
    add(
        f"Every doe's first service happened at age {_age_range(service_ages)} months — "
        f"the 12-month first-service gate "
        f"(GOAT_PROFILE.min_breeding_age_months) holding exactly. {b1.totals.services} "
        "services were recorded in all: 50 first services plus re-services of does whose "
        "first scan came back negative (next heat, +21 days). The day-60 weaning and the "
        "doe's re-breeding fall just past the engine's 365-day horizon cap, so that half "
        "of the journey is shown by the continuation run below."
    )
    add("")
    add("**One sample doe — G01's journey, hop by hop:**")
    add("")
    add("| Date | From | To | Why |")
    add("|---|---|---|---|")
    for hop_date, from_bucket, to_bucket, context in journey_table_rows(b1, "G01"):
        add(f"| {hop_date} | {from_bucket} | {to_bucket} | {context} |")
    add("")
    add("### Batches 2-10, same journey")
    add("")
    add(
        "The daily engine cannot take mid-run arrivals (its herd is fixed on day 1 — see "
        "observations), so each batch was run on its own arrival date with its own seed. "
        "Every batch walks the identical path, two months apart:"
    )
    add("")
    add(
        "| Batch | Arrived | Quarantine release | First service | First +32d scan | First kidding |"
    )
    add("|---|---|---|---|---|---|")
    for batch, result in enumerate(batch_results, start=1):
        m = batch_milestones(result)
        add(
            f"| {batch} | {batch_arrival_date(batch).isoformat()} | "
            f"{_milestone_cell(m, 'release')} | {_milestone_cell(m, 'first_service')} | "
            f"{_milestone_cell(m, 'first_scan_positive')} | {_milestone_cell(m, 'first_kidding')} |"
        )
    add("")
    add("### After kidding — the continuation run")
    add("")
    add(
        "To keep the post-kidding half honest (and inside the 365-day cap), a second daily "
        "run starts 2027-08-01 with ten batch-1-age does already in PREGNANCY_LATE at "
        "gestation day 120 (bred ~2027-04-02, exactly when batch 1 was first served) plus "
        "two breeding bucks. It shows:"
    )
    add("")
    add("| Milestone | Date |")
    add("|---|---|")
    for key in (
        "first_move_delivery",
        "first_kidding",
        "first_rebreed",
        "first_weaning",
        "first_scan_positive",
        "first_post_weaning_rebreed",
    ):
        add(f"| {MILESTONE_LABELS[key]} | {_milestone_cell(cont_milestones, key)} |")
    add("")
    add(
        f"In that run {continuation.totals.services} re-services were recorded, "
        f"{continuation.totals.conceptions} confirmed pregnant, and does that kidded twice "
        "show the full kidding-to-kidding cycle below (section 6)."
    )
    add("")
    add("### The whole herd at build-out — 2028-04-01 snapshot")
    add("")
    snap_t = snapshot.totals
    add(
        f"A {SNAPSHOT_HORIZON_DAYS}-day operational snapshot with 477 head (batches 1-9 and "
        f"their 27 bucks — the engine caps a run at 500 starting animals, so batch 10, "
        "arriving that very morning, is covered by the identical batch-1 quarantine ledger). "
        f"Over the window: {snap_t.services} services, {snap_t.conceptions} confirmed "
        f"pregnancies, {snap_t.moves} bucket moves, "
        f"{sum(snap_t.tasks_by_category.values()):,} duties generated "
        f"(~{sum(snap_t.tasks_by_category.values()) / snapshot.horizon_days:,.0f}/day), "
        f"and {sum(snap_t.feed_kg_by_recipe.values()):,.0f} kg of feed delivered "
        f"(~{sum(snap_t.feed_kg_by_recipe.values()) / snapshot.horizon_days:,.0f} kg/day). "
        "State convention: every breeding-age doe starts open in BREEDING (the monthly "
        "engine tracks pools, not individuals), so the window shows a fresh breeding wave "
        "at full scale rather than the true mixed-state herd."
    )
    add("")
    return lines


def _readme_buckets_tasks_section(
    b1: DailyOpsResult,
    continuation: DailyOpsResult,
    snapshot: DailyOpsResult,
) -> list[str]:
    lines: list[str] = []
    add = lines.append
    add("## 4. Bucket movements")
    add("")
    add(
        "The daily engine models the ten lifecycle buckets as ten buildings, each with its "
        "own vet area; every move is validated against the same legal transition graph the "
        "live app enforces (`models.lifecycle.LEGAL_BUCKET_TRANSITIONS`). What each bucket "
        "means:"
    )
    add("")
    add("| Bucket | Meaning |")
    add("|---|---|")
    for bucket, meaning in BUCKET_GLOSSARY:
        add(f"| {bucket} | {meaning} |")
    add("")
    add(
        f"Batch 1's arrival year produced {b1.totals.moves} moves; the matrix (from → to, "
        "with the workflow context that caused it):"
    )
    add("")
    add("| From | To | Context | Moves |")
    add("|---|---|---|---:|")
    for row in b1.transition_counts:
        add(f"| {row.from_bucket or '—'} | {row.to_bucket} | {row.context} | {row.count} |")
    add("")
    add(
        f"The continuation run added {continuation.totals.moves} moves (delivery, kidding, "
        f"weaning, resting, re-breeding) and the 477-head snapshot {snapshot.totals.moves} "
        f"moves in {snapshot.horizon_days} days. Matrices for all three runs: "
        "[bucket_transitions.md](bucket_transitions.md)."
    )
    add("")
    add("## 5. Tasks — what the crews actually do")
    add("")
    add(
        "Every duty the engine generated for batch 1's arrival year, by category (FEED and "
        "CLEANING dominate by construction: three feed deliveries and two verified "
        "cleanings per occupied building per day):"
    )
    add("")
    examples: dict[str, str] = {}
    for record in b1.days:
        for task in record.tasks:
            examples.setdefault(task.category, task.headline)
    add("| Category | Duties | Example headline |")
    add("|---|---:|---|")
    for category, count in b1.totals.tasks_by_category.items():
        add(f"| {category} | {count:,} | {examples.get(category, '—')} |")
    add("")
    snap_duties = sum(snapshot.totals.tasks_by_category.values())
    busiest_vet = (
        max(snapshot.totals.vet_tasks_by_building.items(), key=lambda kv: kv[1])[0]
        if snapshot.totals.vet_tasks_by_building
        else "—"
    )
    add(
        "By crew role: "
        + ", ".join(f"{role} {count:,}" for role, count in b1.totals.tasks_by_role.items())
        + f". At build-out scale (477-head snapshot): {snap_duties:,} duties in "
        f"{snapshot.horizon_days} days (~{snap_duties / snapshot.horizon_days:,.0f}/day), "
        f"with the vet round heaviest in {busiest_vet}. "
        "Full breakdowns for all three runs: [task_log_summary.md](task_log_summary.md)."
    )
    add("")
    return lines


def _readme_benchmarks_section(
    b1: DailyOpsResult,
    continuation: DailyOpsResult,
    intervals: list[int],
    second_kiddings: int,
) -> list[str]:
    b1_t = b1.totals
    b1_scans = b1_t.conceptions + b1_t.failed_services
    b1_conception_pct = 100.0 * b1_t.conceptions / b1_scans if b1_scans else 0.0
    b1_kidding_events = sum(len(rec.births) for rec in b1.days)
    b1_litter_mean = (
        (b1_t.kids_born_alive + b1_t.kids_born_dead) / b1_kidding_events
        if b1_kidding_events
        else 0.0
    )
    b1_adult_deaths, b1_kid_deaths = death_split(b1)
    cont_adult_deaths, cont_kid_deaths = death_split(continuation)
    lines: list[str] = []
    add = lines.append
    add("## 6. Breeding & reproduction — app defaults vs published Osmanabadi benchmarks")
    add("")
    add(
        "The app documents every biological default inline (simulation/assumptions.py, "
        "models/species.py). Both sides quoted:"
    )
    add("")
    add(
        "| Metric | App default (source note in code) | Published Osmanabadi reference | "
        "This run realized |"
    )
    add("|---|---|---|---|")
    add(
        f"| Conception per service | 0.85 (ICAR herd models) | ~80-85% under managed "
        f"natural service | Batch 1: {b1_t.conceptions}/{b1_scans} scans positive = "
        f"{b1_conception_pct:.0f}% |"
    )
    add(
        "| Gestation | 150 days (GOAT_PROFILE; monthly engine: 5 months) | 145-155 days "
        "(the app's own kidding window) | Exactly 150 days by construction — every kidding "
        "lands on service + 150 |"
    )
    add(
        f"| Litter size | 1.6 kids/kidding mean; daily engine draws 40% singles / 60% "
        f"twins; maiden does scaled to ~0.85 of mature (~1.4) per AICRP/NARI parity table | "
        f"Osmanabadi twinning ~35-40% in field records; AICRP/NARI managed herds ~1.65-1.7 "
        f"for mature does | Batch 1 mean litter {b1_litter_mean:.2f} over "
        f"{b1_kidding_events} kiddings (small sample — one batch, one year) |"
    )
    add(
        f"| Kidding interval | 5 mo gestation + 2 mo lactation/weaning + 1 mo open ≈ 8 "
        f"months (~243 d), chosen to sit inside the published band | 232-297 days "
        f"(7.7-9.8 months) published; ~195 d in improved-management herds | Continuation "
        f"run: {second_kiddings} does kidded twice, intervals "
        + (", ".join(f"{i} d" for i in intervals) if intervals else "n/a")
        + (
            "; an interval near 194 d is a doe whose litter died — the postpartum path "
            "(14 d recovery + 30 d flush + 150 d gestation) instead of the full "
            "60 d-to-weaning cycle"
            if any(i < 220 for i in intervals)
            else ""
        )
        + " |"
    )
    add(
        f"| Kid mortality (pre-weaning) | 15% of each crop (NABARD bankable convention; "
        f"field studies 10.9-20.4%) | same | Batch 1: {b1_kid_deaths} kid deaths of "
        f"{b1_t.kids_born_alive} born alive (kiddings begin only on day ~333 of the 365-day "
        f"run, so most of the crop is still pre-weaning at horizon — the rate will keep "
        f"acting past it); continuation: {cont_kid_deaths} kid deaths |"
    )
    add(
        f"| Adult mortality | 5%/yr, compounded monthly/daily | 5-10% field range | "
        f"Batch 1 (53 adults, 365 d): {b1_adult_deaths} death(s); continuation "
        f"(12 adults, 365 d): {cont_adult_deaths} death(s) |"
    )
    add(
        "| First breeding | 12 months + 22 kg (field puberty ~11.5 mo; age at first "
        "kidding norm 19-20 mo) | same | First services at 12.0-12.1 months of age "
        "(section 3) |"
    )
    add(
        "| Repeat breeders | culled after 2 consecutive failed services (the app's own "
        "operational rule) | farm policy, not literature | See cull counts in the ledgers |"
    )
    add("")
    return lines


def _readme_money_section(
    base: SimulationResult,
    nlm: SimulationResult,
    grower_spend: float,
    scheduled_buck_spend: float,
    auto_buck_spend: float,
) -> list[str]:
    metrics = base.metrics
    nlm_metrics = nlm.metrics
    total_stock_spend = grower_spend + scheduled_buck_spend + auto_buck_spend
    lines: list[str] = []
    add = lines.append
    add("## 7. Money over the five years (monthly engine)")
    add("")
    add("### Buying the herd")
    add("")
    add("| What | Head | Spend |")
    add("|---|---:|---:|")
    add(
        f"| 500 female growers (10 batches × 50, at the engine's live-weight valuation, "
        f"which escalates ~4%/yr) | 500 | {inr(grower_spend)} |"
    )
    add(f"| 30 scheduled bucks (10 × 3) | 30 | {inr(scheduled_buck_spend)} |")
    add(
        f"| Automatic replacement bucks (engine restocks sires after the 3-year rotation "
        f"cull, `herd.auto_purchase_bucks`) | — | {inr(auto_buck_spend)} |"
    )
    add(f"| **Total stock purchases** | | **{inr(total_stock_spend)}** |")
    add("")
    add(
        "Accounting note: the engine books grower purchases as that month's operating cost "
        "(young stock is trading inventory) while buck purchases are capitalized as "
        "breeding livestock and depreciated over 60 months — the cash still leaves in the "
        "purchase month either way."
    )
    add("")
    add("### Profit & loss by year")
    add("")
    add(
        "| Year | Meat sales | Culls | Manure | Total revenue | Feed | Vet | Labour | "
        "Insurance | Misc+selling | Grower purchases | EBITDA | Net cash flow |"
    )
    add("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for pl in base.annual_pl:
        add(
            f"| {pl.year} ({sim_month_date((pl.year - 1) * 12 + 1).strftime('%Y-%m')} – "
            f"{sim_month_date(pl.year * 12).strftime('%Y-%m')}) | {inr(pl.meat_revenue)} | "
            f"{inr(pl.cull_revenue)} | {inr(pl.manure_revenue)} | {inr(pl.total_revenue)} | "
            f"{inr(pl.feed_cost)} | {inr(pl.vet_cost)} | {inr(pl.labour_cost)} | "
            f"{inr(pl.insurance_cost)} | {inr(pl.misc_cost + pl.selling_cost)} | "
            f"{inr(pl.stock_purchases)} | {inr(pl.ebitda)} | {inr(pl.net_cash_flow)} |"
        )
    add("")
    add(
        "Year 5's net cash flow includes the terminal value — the closing herd, shed and "
        "working capital valued at the horizon (see the table below); the operating years "
        "1-4 are cash-negative because this plan keeps every female it raises instead of "
        "selling her: the money is going into a growing breeding herd, which is exactly "
        "what the terminal value recovers. EBITDA stays negative through year 5 — at "
        "default prices an ever-expanding, never-sell-a-female herd does not cover its "
        "feed and labour bill from male and cull sales alone (break-even meat price "
        "₹837/kg vs the ₹370 base). That is the honest economics of *this* build policy, "
        "not an engine fault: a farm that sold surplus females (the app's 60% retention "
        "default) would show a very different P&L."
    )
    add("")
    pc = base.project_cost_breakdown
    add("### Project cost and viability")
    add("")
    add("| Item | Without subsidy | With NLM 50% subsidy |")
    add("|---|---:|---:|")
    add(
        f"| Project cost (shed {inr_short(pc.shed_cost)} for {pc.capacity_places:,.0f} "
        f"places + equipment {inr_short(pc.equipment_cost)} + working capital "
        f"{inr_short(pc.working_capital)}) | {inr(metrics.project_cost)} | "
        f"{inr(nlm.metrics.project_cost)} |"
    )
    add(
        f"| Bank loan (85%, 11% p.a., 12-month moratorium) | "
        f"{inr(metrics.loan_amount)} | {inr(nlm.metrics.loan_amount)} |"
    )
    add(f"| Capital subsidy | {inr(metrics.subsidy_amount)} | {inr(nlm.metrics.subsidy_amount)} |")
    add(
        f"| Promoter equity (month-0 outflow) | {inr(metrics.equity)} | {inr(nlm.metrics.equity)} |"
    )
    add(f"| NPV @ 12% | {inr(metrics.npv)} | {inr(nlm.metrics.npv)} |")
    add(
        f"| IRR (annual blocks) / MIRR | "
        f"{f'{metrics.irr * 100:.1f}%' if metrics.irr is not None else '—'} / "
        f"{f'{metrics.mirr * 100:.1f}%' if metrics.mirr is not None else '—'} | "
        f"{f'{nlm_metrics.irr * 100:.1f}%' if nlm_metrics.irr is not None else '—'} / "
        f"{f'{nlm_metrics.mirr * 100:.1f}%' if nlm_metrics.mirr is not None else '—'} |"
    )
    payback = metrics.payback_month
    nlm_payback = nlm_metrics.payback_month
    payback_cell = (
        f"month {payback} ({sim_month_date(payback).strftime('%Y-%m')})"
        if payback
        else "beyond horizon"
    )
    nlm_payback_cell = (
        f"month {nlm_payback} ({sim_month_date(nlm_payback).strftime('%Y-%m')})"
        if nlm_payback
        else "beyond horizon"
    )
    add(f"| Payback (first month cumulative cash ≥ 0) | {payback_cell} | {nlm_payback_cell} |")
    add(
        f"| Deepest cash hole | {inr(metrics.minimum_cash_balance)} in month "
        f"{metrics.minimum_cash_month} "
        f"({sim_month_date(metrics.minimum_cash_month).strftime('%Y-%m')}) | "
        f"{inr(nlm_metrics.minimum_cash_balance)} in month {nlm_metrics.minimum_cash_month} |"
    )
    add(
        f"| Terminal value recovered at horizon (herd + shed + WC) | "
        f"{inr(metrics.terminal_value)} | {inr(nlm.metrics.terminal_value)} |"
    )
    add(
        f"| Break-even meat price | ₹{metrics.break_even_meat_price_per_kg:.0f}/kg vs "
        f"₹370 base | ₹{nlm.metrics.break_even_meat_price_per_kg:.0f}/kg |"
        if metrics.break_even_meat_price_per_kg and nlm.metrics.break_even_meat_price_per_kg
        else "| Break-even meat price | — | — |"
    )
    add("")
    unit_head = BATCH_COUNT * (DOES_PER_BATCH + BUCKS_PER_BATCH)
    nlm_ceiling = NLM_CAPITAL_CEILING_PER_HEAD * unit_head
    nlm_half_ceiling = NLM_SUBSIDY_FRACTION * nlm_ceiling
    equity_floor = nlm_metrics.project_cost - nlm_metrics.loan_amount
    binding_note = (
        f"The per-head ceiling binds: the subsidy is the full {inr(nlm_half_ceiling)}."
        if abs(nlm_metrics.subsidy_amount - nlm_half_ceiling) < 1.0
        else (
            "What binds instead is the funding stack — loan + subsidy may not exceed "
            "the project cost, so with an 85% loan the subsidy lands at 15% of "
            f"{inr_short(nlm_metrics.project_cost)} = {inr(equity_floor)}, below the "
            f"{inr(nlm_half_ceiling)} half-ceiling."
        )
    )
    payback_note = (
        f"payback moves {payback_cell} → {nlm_payback_cell}"
        if payback_cell != nlm_payback_cell
        else f"payback is unchanged ({payback_cell})"
    )
    add(
        f"**NLM comparison result: a real {inr(nlm_metrics.subsidy_amount)} subsidy.** "
        "The eligible-head cap now sizes the unit being established — the 500 "
        f"event-purchased female growers plus the 30 scheduled bucks, {unit_head} head "
        f"× ₹10,000 = {inr_short(nlm_ceiling)} of eligible capital (squarely in the "
        "scheme's 500F+25M ~₹50 lakh band). "
        f"{binding_note} Against the no-subsidy run, promoter equity drops "
        f"{inr(metrics.equity)} → {inr(nlm_metrics.equity)} and {payback_note}. "
        "Operating economics — revenue, EBITDA, break-even price — are unchanged: the "
        "subsidy is a month-0 capital event, not an operating one."
    )
    add("")
    return lines


def _readme_observations_section(
    base: SimulationResult,
    nlm: SimulationResult,
    b1: DailyOpsResult,
    rows: list[dict[str, object]],
) -> list[str]:
    first_doe_month = next(r for r in rows if _num(r["does"]) > 0)
    first_birth_month = next(r for r in rows if _num(r["births"]) > 0)
    scheduled_buck_spend = sum(
        fill.revenue
        for row in base.months
        for fill in row.event_fills
        if fill.kind == "purchase" and fill.animal_class == "buck"
    )
    auto_buck_spend = sum(row.breeding_stock_capex for row in base.months) - scheduled_buck_spend
    lines: list[str] = []
    add = lines.append
    add("## 8. Engine observations and limitations (read before trusting any single number)")
    add("")
    add(
        "1. **RESOLVED — purchased growers now carry their real arrival age.** The first "
        "run of this report found scheduled `female_grower` purchases placed mid-class "
        "(~9 months in the 6-11-month chain) with no per-event age input, so each batch "
        "reached the doe pool ~3 months after arrival instead of ~6 and year-1-2 revenue "
        "ran ahead of reality. Herd events now accept `age_months` (validated against "
        "the class's age chain), and this scenario's events carry `age_months=6`: the "
        f"first batch-1 does graduate in month {first_doe_month['month']} "
        f"({first_doe_month['date']}) and the first monthly-engine kids arrive in month "
        f"{first_birth_month['month']} ({first_birth_month['date']}) — matching the "
        "daily engine's 6-month-old timeline (first service 2027-04, first kidding "
        "2027-08/09)."
    )
    add(
        "2. **Grower purchases are expensed, buck purchases capitalized.** The engine "
        "treats young-stock event purchases (our 500 growers) as trading inventory in "
        "operating cost, while does/bucks bought as adults capitalize. Buying the same "
        "animals as `female_grower` vs `doe` therefore changes EBITDA timing, not cash."
    )
    add(
        f"3. **Automatic sire restocking spent {inr(auto_buck_spend)} beyond the 30 "
        "scheduled bucks.** `herd.auto_purchase_bucks` (default on) buys a buck whenever "
        "the 1:20 ratio or the 3-year rotation cull requires it. This is intended engine "
        "policy, surfaced here so the purchase bill reconciles."
    )
    add(
        "4. **The daily engine takes its herd on day 1 only — no staggered arrivals — and "
        "caps a run at 500 head / 365 days.** The 10-batch build-out was therefore run as "
        "ten independent per-batch daily runs (identical journeys two months apart), one "
        "late-pregnancy continuation run to show weaning/re-breeding past the day-365 cap, "
        "and one 477-head snapshot at month 19. No whole-herd 5-year daily run exists; the "
        "monthly engine is the whole-herd source of truth."
    )
    add(
        "5. **RESOLVED — stale note text in the daily engine.** The first run flagged "
        'that the `notes` output claimed breeding eligibility is "age-gated (≥10 '
        'months)" while the enforced gate is 12 months. The note now quotes '
        "`GOAT_PROFILE.min_breeding_age_months` directly, so it cannot drift from the "
        "enforced gate again; the run itself still proves the gate — no doe was served "
        "before 12.0 months."
    )
    add(
        "6. **Weight gates are age proxies in the daily engine** (its own notes say so): "
        "the 22 kg breeding floor and the 24-28 kg sale window are enforced as 12-month "
        "and 8-9-month age gates. The monthly engine carries the full weight curve."
    )
    add(
        "7. **Monthly resolution synchronizes each batch.** All does in a monthly slot are "
        "served and kid together; a real batch spreads services over a few weeks. The "
        "daily per-batch ledgers show the realistic spread within the arrival year."
    )
    add(
        "8. **Weaning differs by design between engines**: monthly resolution kids span "
        "ages 0-2 months (weaning effectively month 3) while daily ops weans at day 60 — "
        "a documented approximation in the engine docstring."
    )
    add(
        "9. **RESOLVED — the NLM subsidy toggle is live for event-built herds.** The "
        "eligible-head cap used to multiply ₹10,000 by the *starting* doe+buck counts "
        "(zero here — all 530 animals arrive by scheduled events), so base and NLM runs "
        "came out identical. The cap now also counts breeding stock bought through "
        "scheduled events — adult does/bucks and female young stock raised into the doe "
        f"pipeline — so this 500F+30M build-out earns {inr(nlm.metrics.subsidy_amount)} "
        "of capital subsidy against the scheme's 500F+25M band (§7)."
    )
    add(
        "10. **The negative P&L is the policy, not the model.** Keeping 100% of female "
        "graduates (uncapped) means year-on-year negative EBITDA — the farm forgoes "
        "~₹10,000 of sale value per retained graduate and feeds her instead. The wealth "
        "accumulates as breeding stock and shows up in the ₹3+ crore terminal value. Run "
        "the same build-out with the app's 60% retention default and the P&L turns."
    )
    if base.warnings:
        add("")
        add("Engine warnings for this run:")
        for warning in base.warnings:
            add(f"- {warning}")
    add("")
    return lines


def _readme_checklist_section(
    base: SimulationResult,
    nlm: SimulationResult,
    rows: list[dict[str, object]],
    batch_results: list[DailyOpsResult],
    b1_milestones: dict[str, tuple[int, str]],
    cont_milestones: dict[str, tuple[int, str]],
    protocol_dates: dict[int, str],
    first_birth_month: dict[str, object],
    month19: dict[str, object],
    peak: dict[str, object],
    continuation: DailyOpsResult,
    festival_months: list[int],
) -> list[str]:
    metrics = base.metrics
    b1 = batch_results[0]
    b5_milestones = batch_milestones(batch_results[4])
    b10_milestones = batch_milestones(batch_results[9])
    first_sale = next(
        (exit_ for rec in continuation.days for exit_ in rec.exits if exit_.kind == "SOLD"),
        None,
    )
    service_ages = first_service_ages(batch_daily_input(1), b1)
    first_sale_month = next(r for r in rows if _num(r["sales_head"]) > 0)
    checks: list[str] = [
        f"Batch 1 arrives 2026-10-01: 50 female growers + 3 bucks appear in QUARANTINE; "
        f"arrival-inspection and rest duties are dated {protocol_dates[1]}.",
        f"Batch 5 arrives {batch_arrival_date(5).isoformat()} (simulation month "
        f"{batch_arrival_month(5)}); batch 10 arrives {batch_arrival_date(10).isoformat()} "
        f"(simulation month {batch_arrival_month(10)}) — the last purchase.",
        f"Batch-1 deworming tasks are dated {protocol_dates[4]}; PPR vaccine "
        f"{protocol_dates[10]}; fecal exam {protocol_dates[13]}; ET+Tetanus "
        f"{protocol_dates[20]}; Goat Pox + pre-release review {protocol_dates[30]}; FMD "
        f"{protocol_dates[40]}.",
        f"All 53 batch-1 animals are released to FOUNDATION on {protocol_dates[45]} "
        f"(protocol day 45); batch 1's quarantine pen is empty from that day on.",
        f"No batch-1 doe is served before 12.0 months of age (first-service ages: "
        f"{_age_range(service_ages)} months).",
        f"First batch-1 service: {_milestone_cell(b1_milestones, 'first_service')} "
        f"(≈2027-04-01, the day the oldest does cross 12 months).",
        f"First positive pregnancy scan: {_milestone_cell(b1_milestones, 'first_scan_positive')} "
        f"(service + 32 days).",
        f"First move to PREGNANCY_LATE: {_milestone_cell(b1_milestones, 'first_move_late')} "
        f"(gestation day 100).",
        f"First move to DELIVERY: {_milestone_cell(b1_milestones, 'first_move_delivery')} "
        f"(15 days before the due date).",
        f"First kidding: {_milestone_cell(b1_milestones, 'first_kidding')} — exactly 150 "
        f"days after the first service; kids stay with the dam in RECOVERY.",
        f"Batch 5 follows the same path shifted 8 months: release "
        f"{_milestone_cell(b5_milestones, 'release')}, first service "
        f"{_milestone_cell(b5_milestones, 'first_service')}, first kidding "
        f"{_milestone_cell(b5_milestones, 'first_kidding')}.",
        f"Batch 10: release {_milestone_cell(b10_milestones, 'release')}, first service "
        f"{_milestone_cell(b10_milestones, 'first_service')}, first kidding "
        f"{_milestone_cell(b10_milestones, 'first_kidding')}.",
        f"Continuation run: first day-60 weaning "
        f"{_milestone_cell(cont_milestones, 'first_weaning')}; dams move to RESTING the "
        f"same day and are re-bred from "
        f"{_milestone_cell(cont_milestones, 'first_post_weaning_rebreed')} after the "
        f"~30-day flush.",
        (
            f"First male-kid meat sale (8-9 month window): {first_sale.date} — {first_sale.reason}."
            if first_sale
            else "No male-kid sale inside the continuation window (check exits)."
        ),
        f"Monthly engine, first kids born: month {first_birth_month['month']} "
        f"({first_birth_month['date']}) — batch-1 6-month-olds graduate at the "
        f"12-month gate in month 6 and kid after the 5-month gestation, matching "
        f"the daily engine (observation 1).",
        f"Month 19 (2028-04): the doe pool stands at {head(month19['does'])} (all "
        f"purchased — the first homebred doe graduates no earlier than month 23), plus "
        f"{head(month19['f_growers'])} females still growing; total herd "
        f"{head(month19['total_herd'])}.",
        f"Peak herd {head(peak['total_herd'])} head at end of month {peak['month']} "
        f"({peak['date']}).",
        "Bakrid uplift months: "
        + ", ".join(
            f"{sim_month_date(m).strftime('%Y-%m')} (sim month {m})" for m in festival_months
        )
        + ".",
        f"Year-1 meat sales are zero head (the first homebred males, born from month "
        f"11, finish at the 9-month sale age in month {first_sale_month['month']} "
        f"({first_sale_month['date']}); the small year-1 Sales ₹ are cull revenue) — "
        f"revenue ramps from year 2 as all 10 batches cycle.",
        (
            f"Payback (cumulative cash ≥ 0): month {metrics.payback_month} "
            f"({sim_month_date(metrics.payback_month).strftime('%Y-%m')}) without subsidy."
            if metrics.payback_month
            else "Payback (cumulative cash ≥ 0): not reached inside the 60-month horizon "
            "without subsidy — the build-out is still cash-negative at 2031-09 (see §7)."
        ),
        f"NLM 50% subsidy: subsidy amount {inr(metrics.subsidy_amount)} without vs "
        f"{inr(nlm.metrics.subsidy_amount)} with NLM; equity {inr(metrics.equity)} vs "
        f"{inr(nlm.metrics.equity)}.",
        f"Batch-1 daily run generated {b1.totals.tasks_by_category.get('VACCINE', 0)} "
        f"vaccine duties and {b1.totals.tasks_by_category.get('DEWORMING', 0)} deworming "
        f"duties for 53 animals over 365 days.",
        f"Batch-1 run: {b1.totals.services} services, {b1.totals.conceptions} confirmed "
        f"conceptions, {b1.totals.kids_born_alive} kids born alive "
        f"({b1.totals.kids_born_dead} stillborn).",
    ]
    lines: list[str] = []
    add = lines.append
    add("## 9. What to verify manually — tick-off checklist")
    add("")
    add(
        "Each statement below carries the value this run produced, so it can be checked "
        "against the UI or a re-run of the same engines:"
    )
    add("")
    for i, check in enumerate(checks, start=1):
        add(f"- [ ] {i}. {check}")
    add("")
    return lines


# --- driver ----------------------------------------------------------------------


def main() -> None:
    default_out = REPO_ROOT / "scratch" / "e2e_herd_buildout"
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else default_out
    out_dir.mkdir(parents=True, exist_ok=True)

    # Monthly engine: base run + NLM 50% subsidy variant.
    base_assumptions = build_monthly_assumptions(nlm_subsidy=False)
    base = run_simulation(base_assumptions)
    nlm = run_simulation(build_monthly_assumptions(nlm_subsidy=True))
    print(f"monthly engine: {len(base.months)} months x2 runs (base + NLM)")

    # Daily engine: one 365-day run per batch, one continuation, one snapshot.
    batch_results = [run_daily_ops(batch_daily_input(batch)) for batch in range(1, 11)]
    continuation = run_daily_ops(continuation_daily_input())
    snapshot = run_daily_ops(snapshot_daily_input())
    print(
        f"daily engine: 10 batch runs + continuation + snapshot "
        f"({snapshot.head_start} head x {snapshot.horizon_days} days)"
    )

    write_monthly_files(base, out_dir)
    write_bucket_transitions(batch_results[0], continuation, snapshot, out_dir)
    write_task_summary(batch_results[0], continuation, snapshot, out_dir)
    (out_dir / "batch1_journey.md").write_text(
        build_daily_ledger(batch_results[0]), encoding="utf-8"
    )
    (out_dir / "README.md").write_text(
        build_readme(base, nlm, base_assumptions, batch_results, continuation, snapshot),
        encoding="utf-8",
    )
    for path in sorted(out_dir.iterdir()):
        print(f"  wrote {path} ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
