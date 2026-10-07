"""Reproductive narratives describe the configured parity facts and native analysis mode."""

import json
from typing import Any

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import run_simulation
from app.simulation.explain import _litter_expectation_paragraphs, _male_counted
from app.simulation.results import SimulationResult
from app.simulation.vocabulary import GOAT_NOUNS


@pytest.mark.parametrize(
    "litter,table,maiden,mature,expected,excluded",
    [
        (1.7, [1.0], "1.70", "1.70", "35-40%", "occasional twin"),
        (2.0, [0.5, 0.9], "1.00", "1.80", "35-40%", "occasional twin"),
        (2.0, [0.5, 0.9, 0.6], "1.00", "1.80", "35-40%", "mostly singles at"),
        (2.0, [0.5, 0.7], "1.00", "1.40", "occasional twin", "35-40%"),
        (2.0, [0.5, 0.75], "1.00", "1.50", "occasional twin", "35-40%"),
        (2.0, [0.5, 0.5], "1.00", "1.00", "", ""),
    ],
    ids=[
        "flat-one-entry",
        "second-parity",
        "later-parity-differs",
        "transition-lower-end",
        "transition-upper-end",
        "single-only",
    ],
)
def test_litter_narrative_uses_maiden_and_second_parity_and_honest_multiple_bands(
    litter: float,
    table: list[float],
    maiden: str,
    mature: str,
    expected: str,
    excluded: str,
) -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "reproduction": {
                    "litter_size": litter,
                    "parity_multipliers": {"litter_size": table},
                },
            }
        )
    )
    try:
        paragraphs = _litter_expectation_paragraphs(assumptions, GOAT_NOUNS)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A coherent valid parity table must yield its reproductive explanation: {exc}")
    if mature == "1.00":
        assert paragraphs == []
        return
    assert len(paragraphs) == 1
    text = paragraphs[0]
    assert f"~{mature}" in text
    assert f"about {maiden} on average" in text
    assert expected in text and excluded not in text


@pytest.mark.parametrize("heads", [0.0, 1.0, 2.0, 0.5], ids=["none", "one", "two", "fractional"])
def test_counted_sire_vocabulary_uses_singular_only_for_one_head(heads: float) -> None:
    assert _male_counted(heads, GOAT_NOUNS) == ("buck" if heads == 1.0 else "bucks")


def _empty_forecast(**options: Any) -> SimulationResult:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"horizon_months": 12},
                "herd": {"does": 0, "bucks": 0, "auto_purchase_bucks": False},
            }
        )
    )
    try:
        result = run_simulation(assumptions, **options)
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A normal native simulation must return its report and analysis state: {exc}")
    assert len(result.months) == 12
    assert all(month.total_herd == 0.0 for month in result.months)
    return result


def test_native_omitted_vocabulary_preserves_the_explicit_goat_report() -> None:
    omitted = _empty_forecast(with_break_even=False)
    explicit = _empty_forecast(with_break_even=False, nouns=GOAT_NOUNS)
    assert omitted.model_dump(mode="json") == explicit.model_dump(mode="json")


def test_native_omitted_break_even_mode_retains_the_explicit_full_analysis_report() -> None:
    omitted = _empty_forecast()
    explicit = _empty_forecast(with_break_even=True)
    skipped = _empty_forecast(with_break_even=False)
    assert omitted.model_dump(mode="json") == explicit.model_dump(mode="json")
    omitted_explanation = next(
        item for item in omitted.metric_explanations if item.key == "break_even_meat_price_per_kg"
    )
    skipped_explanation = next(
        item for item in skipped.metric_explanations if item.key == "break_even_meat_price_per_kg"
    )
    assert omitted_explanation != skipped_explanation
