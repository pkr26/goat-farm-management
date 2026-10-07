"""Current-release seeded uncertainty reports retain recorded public values.

The references come from actual native ordinary JSON runs in the frozen B6
release, with seed 7/42, 24 months and 30/10 runs. Production promises fixed draw
order and seeded replay; this regression protects that release's public report
compatibility. Intentional algorithm or assumption-default changes require a
reviewed new native reference. No bootstrap count or salt is asserted.

Immutable reference provenance: domain32-bootstrap-cdf-native-json-probe/
871dffeddda741c6ab598d4266e86783/original.json, plus completion-source-review/
735acf43e3ed47b19a3c7b9790887174/review.json. These scratch documents are audit
bindings; this test neither reads them nor substitutes their result at runtime.
"""

from __future__ import annotations

import json

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import run_simulation

_RELEASE_REPORTS: tuple[tuple[int, int, dict[str, list[float]], dict[str, float]], ...] = (
    (
        30,
        7,
        {
            "npv_p5_ci": [-665517.0709764654, -557240.7275316655],
            "npv_p50_ci": [-529283.0069152784, -456441.54275136004],
            "npv_p95_ci": [-381269.9418605481, -236236.4518235077],
            "minimum_cash_p5_ci": [-475158.82571680367, -399783.3558768976],
        },
        {
            "npv_p5_ci_low": -665517.0709764654,
            "npv_p5_ci_high": -557240.7275316655,
            "npv_p50_ci_low": -529283.0069152784,
            "npv_p50_ci_high": -456441.54275136004,
            "npv_p95_ci_low": -381269.9418605481,
            "npv_p95_ci_high": -236236.4518235077,
        },
    ),
    (
        10,
        42,
        {
            "npv_p5_ci": [-568733.3468416085, -488854.66956300905],
            "npv_p50_ci": [-515937.43130760186, -404526.0683969854],
            "npv_p95_ci": [-429052.1920612749, -346327.7629719753],
            "minimum_cash_p5_ci": [-457969.0448030703, -365991.2628095343],
        },
        {
            "npv_p5_ci_low": -568733.3468416085,
            "npv_p5_ci_high": -488854.66956300905,
            "npv_p50_ci_low": -515937.43130760186,
            "npv_p50_ci_high": -404526.0683969854,
            "npv_p95_ci_low": -429052.1920612749,
            "npv_p95_ci_high": -346327.7629719753,
        },
    ),
)


@pytest.mark.parametrize(
    ("runs", "seed", "expected_ci", "expected_figures"),
    _RELEASE_REPORTS,
    ids=("thirty-runs-seed-seven", "ten-runs-seed-forty-two"),
)
def test_current_release_seeded_uncertainty_report_matches_native_reference(
    runs: int,
    seed: int,
    expected_ci: dict[str, list[float]],
    expected_figures: dict[str, float],
) -> None:
    payload = {
        "meta": {"horizon_months": 24, "start_year_month": "2026-01"},
        "risk": {"monte_carlo_runs": runs, "seed": seed},
    }
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    result = run_simulation(assumptions, with_break_even=False, with_monte_carlo=True).model_dump(
        mode="json"
    )
    monte_carlo = result["monte_carlo"]
    assert monte_carlo["runs"] == runs
    assert monte_carlo["seed"] == seed
    actual_ci = {field: monte_carlo[field] for field in expected_ci}
    assert actual_ci == expected_ci
    risks = next(section for section in result["narrative_report"] if section["key"] == "risks")
    actual_figures = {field: risks["figures"][field] for field in expected_figures}
    assert actual_figures == expected_figures
