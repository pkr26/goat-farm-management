"""Species nouns in simulation output.

A buffalo dairy's narrative report, monthly event log and planner notes must
read the farm's own words (milking buffalo / bull / calf / calving) — never
goat nouns — while the default goat run keeps its exact historical text.
"""

from app.simulation.assumptions import HerdEventAssumptions, SimulationAssumptions
from app.simulation.defaults import get_preset
from app.simulation.engine import run_simulation
from app.simulation.planner import SaleTarget, build_plan_report
from app.simulation.vocabulary import BUFFALO_NOUNS, GOAT_NOUNS, nouns_for_farm_type

DAIRY = nouns_for_farm_type("BUFFALO_DAIRY")


def test_nouns_for_farm_type_defaults_to_goat() -> None:
    assert nouns_for_farm_type("BUFFALO_DAIRY") is BUFFALO_NOUNS
    assert nouns_for_farm_type("GOAT") is GOAT_NOUNS
    assert nouns_for_farm_type(None) is GOAT_NOUNS
    assert nouns_for_farm_type("SOMETHING_ELSE") is GOAT_NOUNS


def test_default_run_keeps_goat_nouns() -> None:
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    overview = res.narrative_report[0].paragraphs[0]
    assert "50 does and 2 bucks" in overview
    herd = res.narrative_report[1].paragraphs[0]
    assert "breeding does" in herd


def test_buffalo_narrative_reads_dairy_nouns() -> None:
    a = get_preset("murrah_dairy")
    a.events = [
        # A scheduled purchase exercises the monthly event log's class labels.
        HerdEventAssumptions(month=1, kind="purchase", animal_class="doe", count=2),
    ]
    res = run_simulation(a, with_break_even=False, nouns=DAIRY)

    overview = res.narrative_report[0].paragraphs[0]
    assert "60 milking buffalo" in overview
    assert "0 bulls" in overview

    herd_title, births = res.narrative_report[1].paragraphs
    assert "breeding milking buffalo" in herd_title
    assert "produces" in births and "calves" in births

    report_text = "\n".join(
        paragraph
        for section in res.narrative_report
        for paragraph in section.paragraphs
    )
    for goat_word in ("doe(s)", " doe ", "does,", "buck(s)", "kid(s)", " kids"):
        assert goat_word not in report_text, f"goat noun {goat_word!r} leaked into a dairy report"

    # The monthly event log names the purchased class with the dairy noun.
    assert any("milking buffalo" in line for line in res.months[0].events), (
        f"event log lost the species label: {res.months[0].events}"
    )


def test_goat_run_without_nouns_argument_is_unchanged() -> None:
    """Callers that predate the nouns parameter keep the goat vocabulary."""
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    assert "50 does and 2 bucks" in res.narrative_report[0].paragraphs[0]


def test_planner_notes_use_species_nouns() -> None:
    a = get_preset("murrah_dairy")
    report = build_plan_report(
        a,
        # 200 head against a 60-head herd: the shortfall fires the cull-sale
        # policy note, which must explain itself with the farm's own animal.
        [SaleTarget(month=8, animal_class="doe", count=200)],
        close_gaps_enabled=True,
        risk_runs=0,
        nouns=DAIRY,
    )
    joined = "\n".join(report.notes)
    assert "a milking buffalo bought for her lifetime of calves" in joined
    assert "doe bought" not in joined
    assert "kids" not in joined
