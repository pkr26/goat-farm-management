"""Computed domain logic (shared by routes, tests, and later phases)."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from datetime import date, timedelta
from typing import TYPE_CHECKING, TypedDict

from ..characters import FORBIDDEN_TEXT_CHARS
from .enums import BreedingOutcome, TaskCategory
from .species import GOAT_PROFILE

if TYPE_CHECKING:
    from .breeding import BreedingRecord
    from .purchases import PurchaseBatch


def expected_kidding_date(breeding_date: date) -> date:
    """Expected kidding date."""
    return breeding_date + timedelta(days=GOAT_PROFILE.gestation_days)


def planned_ultrasound_date(breeding_date: date) -> date:
    """Planned pregnancy-check date after a service (ultrasound / PD)."""
    return breeding_date + timedelta(days=GOAT_PROFILE.pregnancy_check_after_service_days)


# A service counts as a conception once an ultrasound confirmed it, even if
# the pregnancy was later lost: ``mark_aborted`` returns early unless the
# record is already CONFIRMED_PREGNANT, so every ABORTED row *did* conceive.
# Pregnancy loss stays a separate fact (loss_date/loss_cause). Counting an
# abortion as a failure to conceive let an ordinary sale of a pregnant doe —
# which auto-aborts her pregnancy — retroactively rewrite this KPI.
CONCEIVED_OUTCOMES = frozenset(
    {BreedingOutcome.CONFIRMED_PREGNANT.value, BreedingOutcome.ABORTED.value}
)
ASSESSED_OUTCOMES = frozenset(
    {
        BreedingOutcome.CONFIRMED_PREGNANT.value,
        BreedingOutcome.ABORTED.value,
        BreedingOutcome.FAILED.value,
    }
)


def conception_rate(records: Iterable[BreedingRecord]) -> float | None:
    """Conceived / assessed breedings. Percent or ``None`` when none were assessed.

    ``GET /api/dashboard/reports`` computes the same metric in SQL from
    ``CONCEIVED_OUTCOMES``; the two implementations must never drift.
    """
    completed = [r for r in records if r.outcome in ASSESSED_OUTCOMES]
    if not completed:
        return None
    conceived = sum(1 for r in completed if r.outcome in CONCEIVED_OUTCOMES)
    return round(100.0 * conceived / len(completed), 1)


# 45-day quarantine protocols (day offsets relative to batch arrival date).
# Two duties may share one day offset (arrival day carries both the clinical
# inspection and the start of the rest period; day 30 pairs the Goat Pox
# vaccine with the pre-release fecal recheck) — everything downstream that
# derives work from this table must key on the entry, not the offset alone.
#
# ``_QUARANTINE_PROTOCOL`` carries each duty's localization key as a fourth
# element; the public QUARANTINE_PROTOCOL keeps the historical 3-tuple shape
# (offset, category, title) for consumers that predate title keys.
_QUARANTINE_PROTOCOL = [
    (
        1,
        TaskCategory.QUARANTINE,
        "quarantine_arrival_inspection",
        "Day 0–1: arrival inspection — dehydration (skin tent/gums), injuries, "
        "lameness, temperature; isolate sick immediately; handle quarantine "
        "animals LAST (dedicated boots/tools)",
    ),
    (
        1,
        TaskCategory.QUARANTINE,
        "quarantine_rest",
        "Days 1–3: rest, electrolyte/jaggery water, dry roughage only, zero grain",
    ),
    (
        4,
        TaskCategory.DEWORMING,
        "quarantine_deworm",
        "Day 4: deworm — Albendazole/Closantel oral + Ivermectin SC",
    ),
    (
        5,
        TaskCategory.QUARANTINE,
        "quarantine_liver_tonic",
        "Days 5–9: liver tonic in water + Vitamin AD3E injection",
    ),
    (10, TaskCategory.VACCINE, "quarantine_ppr_vaccine", "Day 10: vaccinate PPR (live viral, SC)"),
    (
        13,
        TaskCategory.QUARANTINE,
        "quarantine_fecal_exam",
        "Day 13: fecal/dung sample exam — confirm day-4 deworm efficacy "
        "(record result as a FECAL_EXAM health event)",
    ),
    (
        20,
        TaskCategory.VACCINE,
        "quarantine_et_tetanus_vaccine",
        "Day 20: vaccinate ET + Tetanus (toxoid, SC)",
    ),
    (
        30,
        TaskCategory.VACCINE,
        "quarantine_goat_pox_vaccine",
        "Day 30: vaccinate Goat Pox (live viral, SC)",
    ),
    (
        30,
        TaskCategory.QUARANTINE,
        "quarantine_prerelease_review",
        "Day 30: fecal recheck + clinical review before release",
    ),
    (40, TaskCategory.VACCINE, "quarantine_fmd_vaccine", "Day 40: vaccinate FMD (killed, SC)"),
    (
        45,
        TaskCategory.BUCKET_MOVE,
        "quarantine_release",
        "Day 45: 10% zinc sulfate footbath → release to FOUNDATION",
    ),
]

QUARANTINE_PROTOCOL = [
    (offset, category, title) for offset, category, _title_key, title in _QUARANTINE_PROTOCOL
]


class QuarantineTaskSpec(TypedDict):
    """One auto-generated quarantine task."""

    due_date: date
    category: str
    title: str
    # Localization contract (see Task.title_key / title_args).
    title_key: str
    title_args: dict[str, object]


def no_control_characters(value: str) -> str:
    """Reject control characters in identifier fields (tags, names).

    ``PostgresText`` whitelists ``\\t\\n\\r`` because narrative fields
    legitimately carry multi-line text; identifiers render on the task
    board, in pickers and in line-oriented exports, where embedded line
    breaks only produce visually confusable values. Other C0 controls and
    unpaired surrogates are already rejected by ``PostgresText`` itself.
    DEL (0x7F) and the C1 range (U+0080–U+009F) are not ``< " "`` so
    ``PostgresText`` lets them through; in identifiers they serve only
    terminal-escape/confusable-value attacks, so reject the whole Cc class
    here on top of the tab/LF/CR case. Bidirectional overrides/isolates and
    the Unicode line separators pass the Cc test yet visually reorder or
    split identifiers (2026-09-16 audit INJ-4) — ``FORBIDDEN_TEXT_CHARS``
    rejects those too.
    """
    if any(
        char in "\t\n\r" or char in FORBIDDEN_TEXT_CHARS or unicodedata.category(char) == "Cc"
        for char in value
    ):
        raise ValueError(
            "cannot contain tabs, line breaks, control, or directional formatting characters"
        )
    return value


def quarantine_schedule(batch: PurchaseBatch) -> list[QuarantineTaskSpec]:
    """Due-dated quarantine task definitions for a purchase batch.

    Titles reference the batch by its opaque id only. They surface on the
    task board to every role covering the protocol categories, while the
    supplier name is procurement data gated behind ``purchases.view`` — so
    it must not travel inside the title. (Legacy rows may still carry the
    old ``[<supplier> #id]`` prefix; title parsers strip any bracketed
    prefix.)
    """
    schedule: list[QuarantineTaskSpec] = []
    prefix = f"[Batch #{batch.id}] "
    for day_offset, category, title_key, protocol_title in _QUARANTINE_PROTOCOL:
        due_date = batch.date + timedelta(days=day_offset - 1)
        schedule.append(
            {
                "due_date": due_date,
                "category": category.value,
                "title": f"{prefix}{protocol_title}",
                "title_key": title_key,
                "title_args": {
                    "batch_id": batch.id,
                    "day": day_offset,
                    "due_date": due_date.isoformat(),
                },
            }
        )
    return schedule
