"""Actual daily hazard compounding and litter mass reproduce the requested biology."""

import math

import pytest

from app.simulation.daily_ops import _daily_hazard, _litter_probabilities


@pytest.mark.parametrize("rate,days", [(0.0, 60), (0.15, 60), (0.05, 123), (0.9, 365), (1.0, 60)])
def test_daily_survival_compounds_to_requested_whole_phase(rate: float, days: int) -> None:
    try:
        hazard = _daily_hazard(rate, days)
    except (ArithmeticError, TypeError, ValueError) as exc:
        pytest.fail(f"A valid whole-phase mortality rate must produce a daily hazard: {exc}")
    assert math.isfinite(hazard) and 0.0 <= hazard <= 1.0
    assert 1.0 - (1.0 - hazard) ** days == pytest.approx(rate, abs=1e-12)


@pytest.mark.parametrize("mean", [1.0, 1.25, 2.0, 2.5, 3.0, 3.75, 4.0])
def test_litter_probabilities_conserve_birth_mass_and_expected_kids(mean: float) -> None:
    try:
        probabilities = _litter_probabilities(mean)
    except (ArithmeticError, TypeError, ValueError) as exc:
        pytest.fail(f"A supported litter mean must produce its birth probabilities: {exc}")
    assert len(probabilities) == 4
    assert all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in probabilities)
    assert sum(probabilities) == pytest.approx(1.0)
    assert sum(size * value for size, value in enumerate(probabilities, start=1)) == pytest.approx(
        mean
    )
    # Between two integer litter sizes, only those neighbouring sizes may occur.
    assert all(
        value == 0.0 for size, value in enumerate(probabilities, start=1) if abs(size - mean) >= 1
    )
