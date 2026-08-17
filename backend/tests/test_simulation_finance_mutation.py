"""Mutation-resistant boundary contracts for the simulation finance kernel."""

from decimal import Decimal, localcontext

import pytest

import app.simulation.finance as finance_module
from app.simulation.finance import (
    _all_decimal_power_roots,
    _crossing_decimal_power_roots,
    _decimal_normalise_power_terms,
    _decimal_normalise_terms,
    _decimal_power_sum,
    _decimal_sign,
    _deduplicate_decimal_roots,
    _normalise_power_terms,
    _positive_power_roots,
    _scanned_power_roots,
    _sign_variations,
    irr_roots,
    mirr,
    monthly_emi,
    npv,
    payback_month,
)


def test_decimal_power_helpers_preserve_coefficients_scale_and_origin() -> None:
    normalised = _decimal_normalise_power_terms([(2.0, 3.0), (0.0, 2.0), (2.0, -1.0), (1.0, -4.0)])
    assert normalised == [
        (Decimal(0), Decimal(2)),
        (Decimal(1), Decimal(-4)),
        (Decimal(2), Decimal(2)),
    ]
    assert _decimal_normalise_terms(
        [
            (Decimal(2), Decimal(1)),
            (Decimal(0), Decimal(-2)),
            (Decimal(1), Decimal(3)),
        ]
    ) == [
        (Decimal(0), Decimal(-2)),
        (Decimal(1), Decimal(3)),
        (Decimal(2), Decimal(1)),
    ]

    value, scale = _decimal_power_sum(
        Decimal(2),
        [
            (Decimal(0), Decimal(-3)),
            (Decimal(1), Decimal(4)),
            (Decimal(2), Decimal(-2)),
        ],
    )
    assert value == Decimal(-3)
    assert scale == Decimal(19)


def test_decimal_sign_uses_an_inclusive_scale_aware_zero_band() -> None:
    threshold = Decimal("3e-64")
    assert _decimal_sign(threshold, Decimal(3)) == 0
    assert _decimal_sign(-threshold, Decimal(3)) == 0
    assert _decimal_sign(threshold * 2, Decimal(3)) == 1
    assert _decimal_sign(threshold * -2, Decimal(3)) == -1

    # Below unit scale, the absolute floor is one rather than two.
    assert _decimal_sign(Decimal("1.5e-64"), Decimal(0)) == 1


def test_decimal_root_deduplication_uses_relative_inclusive_tolerance() -> None:
    with localcontext() as context:
        context.prec = 96
        exact_boundary = [Decimal(10), Decimal(10) + Decimal("1e-54")]
        assert _deduplicate_decimal_roots(exact_boundary) == [Decimal(10)]

        just_outside_relative_band = [
            Decimal("1.5"),
            Decimal("1.5") + Decimal("1.75e-55"),
        ]
        assert _deduplicate_decimal_roots(just_outside_relative_band) == just_outside_relative_band

        near_duplicates = [Decimal(1), Decimal(1) + Decimal("5e-56")]
        assert _deduplicate_decimal_roots(near_duplicates) == [Decimal(1)]


def test_decimal_root_isolation_keeps_both_interval_endpoints_and_crossings() -> None:
    with localcontext() as context:
        context.prec = 96
        lo = Decimal("0.5")
        hi = Decimal("2")
        assert _all_decimal_power_roots(
            [(Decimal(0), Decimal("-0.5")), (Decimal(1), Decimal(1))], lo, hi
        ) == [lo]
        assert _all_decimal_power_roots(
            [(Decimal(0), Decimal(-2)), (Decimal(1), Decimal(1))], lo, hi
        ) == [hi]
        roots = _all_decimal_power_roots(
            [
                (Decimal(0), Decimal(2)),
                (Decimal(1), Decimal(-3)),
                (Decimal(2), Decimal(1)),
            ],
            lo,
            Decimal(3),
        )
    assert [float(root) for root in roots] == pytest.approx([1.0, 2.0], abs=1e-14)


def test_crossing_filter_retains_roots_at_each_public_bracket_boundary() -> None:
    assert _crossing_decimal_power_roots([(0.0, -0.5), (1.0, 1.0)], 0.5, 2.0) == [0.5]
    assert _crossing_decimal_power_roots([(0.0, -2.0), (1.0, 1.0)], 0.5, 2.0) == [2.0]
    # Retaining a boundary root must not stop the filter from considering a
    # later crossing: (x - 0.5)(x - 1.5) has both in the closed bracket.
    assert _crossing_decimal_power_roots(
        [(0.0, 0.75), (1.0, -2.0), (2.0, 1.0)], 0.5, 2.0
    ) == pytest.approx([0.5, 1.5], abs=1e-14)


def test_scanned_root_search_covers_the_final_cell_and_exact_grid_roots() -> None:
    lo = 0.25
    hi = 2.25
    step = (hi - lo) / 256
    final_cell_root = hi - step / 2.0
    assert _scanned_power_roots([(0.0, -final_cell_root), (1.0, 1.0)], lo, hi) == pytest.approx(
        [final_cell_root], abs=1e-14
    )

    exact_grid_root = lo + step * 37
    assert _scanned_power_roots([(0.0, -exact_grid_root), (1.0, 1.0)], lo, hi) == pytest.approx(
        [exact_grid_root], abs=1e-14
    )

    # A positive left endpoint is not itself a root.
    assert _scanned_power_roots([(0.0, 1.0)], lo, hi) == []

    # Root finding is scale invariant. Multiplying adjacent function values
    # underflowed here and silently hid the same crossing found at unit scale.
    assert _scanned_power_roots([(0.0, -1e-300), (1.0, 1e-300)], 0.5, 2.0) == pytest.approx(
        [1.0], abs=1e-14
    )


def test_monthly_emi_preserves_small_principals_and_single_payment_terms() -> None:
    monthly_rate = 0.12 / 12.0
    assert monthly_emi(0.5, 0.12, 12) == pytest.approx(
        0.5 * monthly_rate / (1.0 - (1.0 + monthly_rate) ** -12)
    )
    assert monthly_emi(120.0, 0.12, 1) == pytest.approx(121.2)

    # These guards are part of the public helper's total-function contract,
    # even though validated simulation assumptions never request such a loan.
    assert monthly_emi(-1.0, 0.12, 12) == 0.0
    assert monthly_emi(120.0, 0.12, 0) == 0.0


def test_npv_rejects_unpaired_cash_flows_and_times() -> None:
    with pytest.raises(ValueError, match=r"zip\(\) argument 2 is shorter"):
        npv(0.10, [-100.0, 110.0], [0.0])


def test_mirr_handles_undefined_and_fractional_cash_flow_boundaries() -> None:
    assert mirr([], [], 0.10, 0.10) is None
    assert mirr([-1.0, 2.0], [0.0], 0.10, 0.10) is None
    assert mirr([-1.0, 2.0], [0.0, 0.0], 0.10, 0.10) is None
    assert mirr([-1.0, 2.0], [0.0, 1.0], 0.0, 0.0) == pytest.approx(1.0)

    # Sub-unit currency flows are still real costs and benefits; one must not
    # be reclassified as the other merely because its magnitude is below one.
    assert mirr([-0.5, 0.5], [0.0, 1.0], 0.0, 0.0) == pytest.approx(0.0)
    assert mirr([1.0, 2.0], [0.0, 1.0], 0.0, 0.0) is None
    assert mirr([-1.0, -2.0], [0.0, 1.0], 0.0, 0.0) is None


def test_float_sign_variations_and_normalisation_keep_unit_and_duplicate_terms() -> None:
    assert _sign_variations([(0.0, -2.0), (1.0, 0.0), (2.0, 1.0), (3.0, -1.0)]) == 2
    assert _normalise_power_terms([(2.0, 3.0), (2.0, -1.0), (3.0, -4.0)]) == [
        (0.0, 2.0),
        (1.0, -4.0),
    ]


def test_decimal_normalisation_combines_duplicates_before_shifting_origin() -> None:
    assert _decimal_normalise_power_terms([(2.0, 3.0), (2.0, -1.0), (3.0, -4.0)]) == [
        (Decimal(0), Decimal(2)),
        (Decimal(1), Decimal(-4)),
    ]
    assert _decimal_normalise_terms(
        [
            (Decimal(2), Decimal(3)),
            (Decimal(2), Decimal(-1)),
            (Decimal(3), Decimal(-4)),
        ]
    ) == [(Decimal(0), Decimal(2)), (Decimal(1), Decimal(-4))]


def test_decimal_root_deduplication_merges_the_absolute_tolerance_boundary() -> None:
    with localcontext() as context:
        context.prec = 96
        roots = [Decimal("0.5"), Decimal("0.5") + Decimal("1e-55")]
        assert _deduplicate_decimal_roots(roots) == [Decimal("0.5")]


def test_decimal_isolation_retains_tangent_and_crossing_roots_without_duplicates() -> None:
    # (x - 1)^2 * (x - 2): x=1 is a tangent root needed for recursive
    # partitioning, while x=2 is the only actual sign crossing.
    decimal_terms = [
        (Decimal(0), Decimal(-2)),
        (Decimal(1), Decimal(5)),
        (Decimal(2), Decimal(-4)),
        (Decimal(3), Decimal(1)),
    ]
    with localcontext() as context:
        context.prec = 96
        roots = _all_decimal_power_roots(decimal_terms, Decimal("0.5"), Decimal(3))

    assert [float(root) for root in roots] == pytest.approx([1.0, 2.0], abs=1e-14)
    assert _crossing_decimal_power_roots(
        [(0.0, -2.0), (1.0, 5.0), (2.0, -4.0), (3.0, 1.0)], 0.5, 3.0
    ) == pytest.approx([2.0], abs=1e-14)


def test_scanned_root_search_respects_both_ends_of_its_closed_domain() -> None:
    lo = 0.25
    hi = 2.25
    step = (hi - lo) / 256
    first_root = lo + step / 2.0
    second_root = lo + 3.0 * step / 2.0
    close_pair = [
        (0.0, first_root * second_root),
        (1.0, -(first_root + second_root)),
        (2.0, 1.0),
    ]

    assert _scanned_power_roots(close_pair, lo, hi) == pytest.approx(
        [first_root, second_root], abs=1e-14
    )
    assert _scanned_power_roots([(0.0, -lo), (1.0, 1.0)], lo, hi) == [lo]
    assert _scanned_power_roots([(0.0, -(hi + step / 2.0)), (1.0, 1.0)], lo, hi) == []


def test_positive_root_isolation_handles_two_roots_endpoints_and_no_root() -> None:
    assert _positive_power_roots([(0.0, 2.0), (1.0, -3.0), (2.0, 1.0)], 0.5, 3.0) == pytest.approx(
        [1.0, 2.0], abs=1e-14
    )
    assert _positive_power_roots([(0.0, -0.5), (1.0, 1.0)], 0.5, 2.0) == [0.5]
    assert _positive_power_roots([(0.0, -2.0), (1.0, 1.0)], 0.5, 2.0) == [2.0]
    # Endpoint handling is independent of crossing direction. In particular,
    # a zero endpoint is not itself an opposite-sign bracket to bisect again.
    assert _positive_power_roots([(0.0, 0.5), (1.0, -1.0)], 0.5, 2.0) == [0.5]
    assert _positive_power_roots([(0.0, 2.0), (1.0, -1.0)], 0.5, 2.0) == [2.0]
    # Function values of exactly one are ordinary positive values, whether at
    # the initial left bound or at the first midpoint; they are not roots.
    assert _positive_power_roots([(0.0, 2.0), (1.0, -2.0)], 0.5, 2.0) == pytest.approx(
        [1.0], abs=1e-14
    )
    assert _positive_power_roots([(0.0, 3.5), (1.0, -2.0)], 0.5, 2.0) == pytest.approx(
        [1.75], abs=1e-14
    )
    assert _positive_power_roots([(0.0, -0.1), (1.0, 0.01)], 0.5, 2.0) == []


def test_positive_root_isolation_uses_decimal_path_at_its_term_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terms = [(float(exponent), 1.0 if exponent % 2 == 0 else -1.0) for exponent in range(24)]
    monkeypatch.setattr(finance_module, "_crossing_decimal_power_roots", lambda *_args: [1.25])
    monkeypatch.setattr(finance_module, "_scanned_power_roots", lambda *_args: [1.5])

    assert _positive_power_roots(terms, 0.5, 2.0) == [1.25]


def test_irr_root_search_honours_both_public_rate_bracket_boundaries() -> None:
    assert irr_roots([-1.0, 7.0], [0.0, 1.0]) == pytest.approx([6.0])
    assert irr_roots([-1.0, 11.5], [0.0, 1.0]) == []
    assert irr_roots([-1.0, 0.008], [0.0, 1.0]) == []
    assert irr_roots([-1e-300, 1e-300], [0.0, 1.0]) == pytest.approx([0.0], abs=1e-14)
    with pytest.raises(ValueError, match=r"zip\(\) argument 2 is shorter"):
        irr_roots([-1.0, 2.0], [0.0])


def test_payback_accepts_exact_zero_and_subunit_recovery() -> None:
    assert payback_month([-1.0, 0.0, 2.0]) == 1
    assert payback_month([-1.0, 0.5]) == 1
