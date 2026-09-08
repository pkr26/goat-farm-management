"""Species nouns in simulation output.

The goat narrative report, monthly event log and planner notes read the
farm's own words (doe / buck / kid / kidding), and the default run keeps its
exact historical text.
"""

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import run_simulation
from app.simulation.vocabulary import GOAT_NOUNS


def test_goat_nouns_are_the_product_vocabulary() -> None:
    nouns = GOAT_NOUNS
    assert nouns.female == "doe"
    assert nouns.male == "buck"
    assert nouns.young == "kid"
    assert nouns.parturition == "kidding"
    assert nouns.species == "goat"


def test_default_run_keeps_goat_nouns() -> None:
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    overview = res.narrative_report[0].paragraphs[0]
    assert "50 does and 2 bucks" in overview
    herd = res.narrative_report[1].paragraphs[0]
    assert "breeding does" in herd


def test_goat_run_without_nouns_argument_is_unchanged() -> None:
    """Callers that predate the nouns parameter keep the goat vocabulary."""
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    assert "50 does and 2 bucks" in res.narrative_report[0].paragraphs[0]
