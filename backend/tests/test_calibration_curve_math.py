"""Exact-value pins for the simulation calibration curve math.

The 2026-09-30 mutation campaign showed the whole linear-interpolation core
of ``_curve_from_observations`` mutating freely (neighbour selection
``a < age`` / ``a > age``, ``span = upper - lower``, and the lerp ``+``/``*``/
``/``), plus the module's own ``_age_months``: only the preset anchor points
themselves were asserted anywhere. A mis-interpolated growth curve — the
input to every feed/growth simulation — passed the suite. These tests pin
exact curve values between anchors, outside them, and under isotonic
smoothing.
"""

from datetime import date

import pytest

from app.services.simulation_calibration import (
    _age_months,
    _curve_from_observations,
    _curve_rescale,
    _isotonic_fit,
    _months_between,
)


def test_age_months_boundaries() -> None:
    assert _age_months(date(2026, 1, 15), date(2026, 3, 14)) == 1
    assert _age_months(date(2026, 1, 15), date(2026, 3, 15)) == 2
    assert _age_months(date(2025, 11, 20), date(2026, 2, 10)) == 2
    assert _age_months(None, date(2026, 3, 15)) is None
    assert _age_months(date(2026, 6, 1), date(2026, 1, 1)) == 0


def test_months_between_exact_fractions() -> None:
    # 30.44 days per month, the engine's DAYS_PER_MONTH convention.
    assert _months_between(date(2026, 1, 1), date(2026, 2, 1)) == pytest.approx(31 / 30.44)
    assert _months_between(date(2026, 1, 1), date(2026, 1, 31)) == pytest.approx(30 / 30.44)
    # Empty and reversed spans are zero, never negative.
    assert _months_between(date(2026, 5, 1), date(2026, 5, 1)) == 0.0
    assert _months_between(date(2026, 6, 1), date(2026, 5, 1)) == 0.0


def test_curve_interpolation_between_anchors_is_exact() -> None:
    preset = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    curve = _curve_from_observations({1: 2.0, 3: 6.0}, preset)
    # Below the first anchor: preset shape rescaled by observed/preset = 1.0.
    assert curve[0] == pytest.approx(1.0)
    # Anchors honoured exactly.
    assert curve[1] == pytest.approx(2.0)
    assert curve[3] == pytest.approx(6.0)
    # Midpoint lerp: 2.0 + (6.0 - 2.0) * (2 - 1) / (3 - 1) = 4.0 — a wrong
    # neighbour pick, span, or lerp operator moves this off the exact value.
    assert curve[2] == pytest.approx(4.0)
    assert curve[2] == 4.0
    # Past the last anchor: preset tail rescaled by 6.0/4.0 = 1.5.
    assert curve[4] == pytest.approx(7.5)
    assert curve[5] == pytest.approx(9.0)


def test_curve_uneven_anchor_spacing() -> None:
    # Anchors 1 and 4: interior points 2 and 3 lerp in thirds, not halves.
    preset = [10.0, 10.0, 10.0, 10.0, 10.0]
    curve = _curve_from_observations({1: 3.0, 4: 12.0}, preset)
    assert curve[1] == pytest.approx(3.0)
    assert curve[2] == pytest.approx(6.0)
    assert curve[3] == pytest.approx(9.0)
    assert curve[4] == pytest.approx(12.0)


def test_curve_isotonic_smoothing_averages_violations() -> None:
    # A decreasing observation pair is averaged (PAVA), not ratcheted: both
    # fitted points become 4.0, and the interpolation between them is flat.
    curve = _curve_from_observations({1: 6.0, 3: 2.0}, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    assert curve[1] == pytest.approx(4.0)
    assert curve[2] == pytest.approx(4.0)
    assert curve[3] == pytest.approx(4.0)


def test_isotonic_fit_pools_violators() -> None:
    assert _isotonic_fit([(0, 5.0), (1, 3.0), (2, 4.0)]) == [(0, 4.0), (1, 4.0), (2, 4.0)]
    assert _isotonic_fit([(0, 1.0), (1, 2.0), (2, 3.0)]) == [(0, 1.0), (1, 2.0), (2, 3.0)]
    assert _isotonic_fit([]) == []


def test_curve_rescale_band_clamps() -> None:
    assert _curve_rescale(1.0, 2.0) == pytest.approx(0.5)
    # The ratio is clamped into [0.25, 4.0] so a single wild measurement
    # cannot explode or flatten the extrapolated tail.
    assert _curve_rescale(0.1, 1.0) == pytest.approx(0.25)
    assert _curve_rescale(10.0, 1.0) == pytest.approx(4.0)


def test_curve_empty_observations_returns_preset_unchanged() -> None:
    preset = [3.0, 4.0, 5.0]
    assert _curve_from_observations({}, preset) == preset
