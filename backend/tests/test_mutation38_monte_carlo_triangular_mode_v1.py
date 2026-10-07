"""The triangular inverse CDF preserves its configured mode at the split.

The asymmetric examples are ordinary schema-valid risks reached by genuine
seeded Gaussian copula draws. This corrects floating point drift at the mode;
it does not assign a historical mutant verdict or replace random draws.
"""

from __future__ import annotations

import json
import math
import random

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.montecarlo import _correlated_draws, _triangular_from_uniform

_RISK_NAMES = (
    "meat_price",
    "feed_price",
    "adult_mortality",
    "kid_mortality",
    "litter_size",
    "conception_rate",
    "fodder_yield",
    "operating_cost",
    "milk_price",
)


@pytest.mark.parametrize(
    ("seed", "low", "high"),
    (
        (0, 0.1, 2.0496799958441656),
        (2, 0.2, 1.6140916887927546),
        (3, 0.01, 3.3672901265895),
    ),
    ids=("seed-zero", "seed-two", "seed-three"),
)
def test_native_copula_at_triangular_split_retains_configured_mode(
    seed: int, low: float, high: float
) -> None:
    risk: dict[str, object] = {
        name: {"enabled": name == "meat_price", "low": 0.8, "high": 1.2} for name in _RISK_NAMES
    }
    risk["meat_price"] = {"enabled": True, "low": low, "high": high}
    risk["correlation_strength"] = 0
    assumptions = SimulationAssumptions.model_validate_json(json.dumps({"risk": risk}))
    reference = random.Random(seed)
    for _ in range(3):
        reference.normalvariate(0.0, 1.0)
    normal = reference.normalvariate(0.0, 1.0)
    uniform = 0.5 * (1.0 + math.erf(normal / math.sqrt(2.0)))
    assert uniform == (1.0 - low) / (high - low)
    for _ in _RISK_NAMES[1:]:
        reference.normalvariate(0.0, 1.0)
    variables = {name: getattr(assumptions.risk, name) for name in _RISK_NAMES}
    actual = random.Random(seed)
    draws = _correlated_draws(actual, variables, assumptions.risk.correlation_strength)
    assert draws["meat_price"] == 1.0
    assert all(draws[name] == 1.0 for name in _RISK_NAMES[1:])
    assert actual.getstate() == reference.getstate()


@pytest.mark.parametrize(
    ("low", "high"),
    ((0.1, 2.0496799958441656), (0.2, 1.6140916887927546), (0.01, 3.3672901265895)),
    ids=("first-asymmetric", "second-asymmetric", "third-asymmetric"),
)
def test_triangular_mode_neighbors_keep_quantile_order(low: float, high: float) -> None:
    split = (1.0 - low) / (high - low)
    before = _triangular_from_uniform(low, high, math.nextafter(split, 0.0))
    center = _triangular_from_uniform(low, high, split)
    after = _triangular_from_uniform(low, high, math.nextafter(split, 1.0))
    tolerance = 8.0 * math.ulp(1.0)
    assert center == 1.0
    assert low <= before <= center + tolerance
    assert center - tolerance <= after <= high
    assert abs(before - center) <= tolerance
    assert abs(after - center) <= tolerance
    assert _triangular_from_uniform(low, high, 0.0) == low
    assert _triangular_from_uniform(low, high, 1.0) == high
