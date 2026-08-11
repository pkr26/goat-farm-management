"""Financial mathematics: amortization and NABARD-style viability metrics.

Pure functions over cash-flow series; the engine supplies the flows. Cash flows
are paired with explicit times (in years) so partial final years discount
correctly.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from itertools import pairwise
from math import pow as _fpow  # mypy 2.x infers float ** float as Any; math.pow stays float


@dataclass(frozen=True)
class AmortizationRow:
    """One month of the loan repayment schedule."""

    month: int
    opening_balance: float
    payment: float
    interest: float
    principal: float
    closing_balance: float


def monthly_emi(principal: float, annual_rate: float, n_months: int) -> float:
    """Equated monthly instalment: ``P.r(1+r)^n / ((1+r)^n - 1)``."""
    if principal <= 0.0 or n_months <= 0:
        return 0.0
    r = annual_rate / 12.0
    if r == 0.0:
        return principal / n_months
    factor = (1.0 + r) ** n_months
    return principal * r * factor / (factor - 1.0)


def amortization_schedule(
    principal: float,
    annual_rate: float,
    term_months: int,
    moratorium_months: int,
) -> list[AmortizationRow]:
    """Monthly schedule with an interest-only moratorium, then EMI.

    During the moratorium only interest is paid (the balance stays at P); the
    remaining term is amortised with a standard EMI. The final payment is
    clamped so the balance lands exactly on zero.
    """
    r = annual_rate / 12.0
    n_emi = max(term_months - moratorium_months, 1)
    emi = monthly_emi(principal, annual_rate, n_emi)
    rows: list[AmortizationRow] = []
    balance = principal
    for month in range(1, term_months + 1):
        interest = balance * r
        if month <= moratorium_months:
            payment = interest
            principal_paid = 0.0
        else:
            payment = min(emi, balance + interest)
            principal_paid = payment - interest
        closing = balance - principal_paid
        rows.append(
            AmortizationRow(
                month=month,
                opening_balance=balance,
                payment=payment,
                interest=interest,
                principal=principal_paid,
                closing_balance=closing,
            )
        )
        balance = closing
    return rows


def npv(rate_annual: float, flows: Sequence[float], times_years: Sequence[float]) -> float:
    """Net present value of flows at explicit times (years)."""
    return sum(cf / _fpow(1.0 + rate_annual, t) for cf, t in zip(flows, times_years, strict=True))


def mirr(
    flows: Sequence[float],
    times_years: Sequence[float],
    finance_rate_annual: float,
    reinvestment_rate_annual: float,
) -> float | None:
    """Modified internal rate of return for irregularly timed cash flows.

    Negative cash flows are discounted to time zero at the finance rate and
    positive cash flows are compounded to the final observation at the
    reinvestment rate. Unlike ordinary IRR this has a single answer for any
    series containing at least one positive and one negative flow.
    """
    if len(flows) != len(times_years) or not flows:
        return None
    horizon = max(times_years)
    if horizon <= 0.0:
        return None
    present_costs = -sum(
        cash_flow / _fpow(1.0 + finance_rate_annual, time)
        for cash_flow, time in zip(flows, times_years, strict=True)
        if cash_flow < 0.0
    )
    future_benefits = sum(
        cash_flow * _fpow(1.0 + reinvestment_rate_annual, horizon - time)
        for cash_flow, time in zip(flows, times_years, strict=True)
        if cash_flow > 0.0
    )
    if present_costs <= 0.0 or future_benefits <= 0.0:
        return None
    return _fpow(future_benefits / present_costs, 1.0 / horizon) - 1.0


IRR_BRACKET = (-0.99, 10.0)

# Cash-flow series with several sign variations are the numerically difficult
# IRR case: close roots and flat odd-multiplicity crossings make binary-float
# evaluations at derivative roots lose their sign.  Decimal is used only for
# that uncommon branch; the ordinary one-sign-change project retains the fast
# float bisection below.  Ninety-six digits leave ample room for the <=21 annual
# terms produced by the bounded 240-month simulation horizon.
_IRR_DECIMAL_PRECISION = 96
_IRR_DECIMAL_BISECTION_STEPS = 360
_IRR_DECIMAL_ZERO_RELATIVE = Decimal("1e-64")


def _power_sum(x: float, terms: Sequence[tuple[float, float]]) -> float:
    """Evaluate ``sum(coefficient * x**exponent)`` for positive ``x``."""
    return sum(coefficient * _fpow(x, exponent) for exponent, coefficient in terms)


def _sign_variations(terms: Sequence[tuple[float, float]]) -> int:
    signs = [1 if coefficient > 0.0 else -1 for _, coefficient in terms if coefficient != 0.0]
    return sum(left != right for left, right in pairwise(signs))


def _normalise_power_terms(
    terms: Sequence[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Combine equal exponents and remove a positive monomial factor.

    Multiplying by ``x**k`` cannot add or remove a root for ``x > 0``. Keeping
    the smallest exponent at zero makes the recursive derivative isolation
    numerically better behaved, especially for a partial final simulation
    year whose exponent is not an integer.
    """
    combined: dict[float, float] = {}
    for exponent, coefficient in terms:
        combined[exponent] = combined.get(exponent, 0.0) + coefficient
    ordered = sorted(
        (exponent, coefficient) for exponent, coefficient in combined.items() if coefficient
    )
    if not ordered:
        return []
    shift = ordered[0][0]
    return [(exponent - shift, coefficient) for exponent, coefficient in ordered]


def _bisect_power_sum(
    lo: float,
    hi: float,
    f_lo: float,
    terms: Sequence[tuple[float, float]],
) -> float:
    """Refine one sign-changing positive-x bracket."""
    for _ in range(100):
        mid = (lo + hi) / 2.0
        f_mid = _power_sum(mid, terms)
        if f_lo * f_mid <= 0.0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0


def _decimal_normalise_power_terms(
    terms: Sequence[tuple[float, float]],
) -> list[tuple[Decimal, Decimal]]:
    """Exact binary-float inputs represented in a high-precision workspace."""
    combined: dict[Decimal, Decimal] = {}
    for exponent, coefficient in terms:
        decimal_exponent = Decimal.from_float(float(exponent))
        decimal_coefficient = Decimal.from_float(float(coefficient))
        combined[decimal_exponent] = (
            combined.get(decimal_exponent, Decimal(0)) + decimal_coefficient
        )
    ordered = sorted(
        (exponent, coefficient) for exponent, coefficient in combined.items() if coefficient != 0
    )
    if not ordered:
        return []
    shift = ordered[0][0]
    return [(exponent - shift, coefficient) for exponent, coefficient in ordered]


def _decimal_power_sum(
    x: Decimal,
    terms: Sequence[tuple[Decimal, Decimal]],
) -> tuple[Decimal, Decimal]:
    """Return the value and its absolute-term scale at positive ``x``."""
    value = Decimal(0)
    scale = Decimal(0)
    for exponent, coefficient in terms:
        term = coefficient * (x**exponent)
        value += term
        scale += abs(term)
    return value, scale


def _decimal_sign(value: Decimal, scale: Decimal) -> int:
    """A scale-aware sign which tolerates only Decimal refinement residue."""
    if abs(value) <= max(Decimal(1), scale) * _IRR_DECIMAL_ZERO_RELATIVE:
        return 0
    return 1 if value > 0 else -1


def _bisect_decimal_power_sum(
    lo: Decimal,
    hi: Decimal,
    sign_lo: int,
    terms: Sequence[tuple[Decimal, Decimal]],
) -> Decimal:
    """Refine one sign-changing Decimal bracket without fixed-grid sampling."""
    for _ in range(_IRR_DECIMAL_BISECTION_STEPS):
        mid = (lo + hi) / 2
        value_mid, scale_mid = _decimal_power_sum(mid, terms)
        sign_mid = _decimal_sign(value_mid, scale_mid)
        if sign_mid == 0:
            return mid
        if sign_mid == sign_lo:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _deduplicate_decimal_roots(roots: list[Decimal]) -> list[Decimal]:
    roots.sort()
    deduplicated: list[Decimal] = []
    tolerance = Decimal("1e-55")
    for root in roots:
        if not deduplicated or abs(root - deduplicated[-1]) > tolerance * max(
            Decimal(1), abs(root)
        ):
            deduplicated.append(root)
    return deduplicated


def _all_decimal_power_roots(
    terms: Sequence[tuple[Decimal, Decimal]],
    lo: Decimal,
    hi: Decimal,
) -> list[Decimal]:
    """Every positive root, including tangent roots needed for partitioning.

    The derivative roots divide a generalized polynomial into monotone
    intervals.  Crucially, recursive calls retain even-multiplicity derivative
    roots: an odd-multiplicity (flat) crossing of the parent is a tangent root
    of its derivative.  Dropping that point can merge two parent crossings into
    one same-sign interval and make a genuinely ambiguous IRR look unique.
    """
    variations = _decimal_sign_variations(terms)
    if len(terms) < 2 or variations == 0:
        return []

    value_lo, scale_lo = _decimal_power_sum(lo, terms)
    value_hi, scale_hi = _decimal_power_sum(hi, terms)
    sign_lo = _decimal_sign(value_lo, scale_lo)
    sign_hi = _decimal_sign(value_hi, scale_hi)

    if variations == 1:
        roots: list[Decimal] = []
        if sign_lo == 0:
            roots.append(lo)
        if sign_lo * sign_hi < 0:
            roots.append(_bisect_decimal_power_sum(lo, hi, sign_lo, terms))
        if sign_hi == 0:
            roots.append(hi)
        return _deduplicate_decimal_roots(roots)

    derivative = [
        (exponent - 1, exponent * coefficient) for exponent, coefficient in terms if exponent != 0
    ]
    derivative = _decimal_normalise_terms(derivative)
    critical_points = _all_decimal_power_roots(derivative, lo, hi)
    points = [lo, *critical_points, hi]
    evaluations = [_decimal_power_sum(point, terms) for point in points]
    signs = [_decimal_sign(value, scale) for value, scale in evaluations]

    roots = [point for point, sign in zip(points, signs, strict=True) if sign == 0]
    for left, right, left_sign, right_sign in zip(
        points[:-1], points[1:], signs[:-1], signs[1:], strict=True
    ):
        if left_sign * right_sign < 0:
            roots.append(_bisect_decimal_power_sum(left, right, left_sign, terms))
    return _deduplicate_decimal_roots(roots)


def _decimal_normalise_terms(
    terms: Sequence[tuple[Decimal, Decimal]],
) -> list[tuple[Decimal, Decimal]]:
    """Decimal counterpart of ``_normalise_power_terms`` for derivatives."""
    combined: dict[Decimal, Decimal] = {}
    for exponent, coefficient in terms:
        combined[exponent] = combined.get(exponent, Decimal(0)) + coefficient
    ordered = sorted(
        (exponent, coefficient) for exponent, coefficient in combined.items() if coefficient != 0
    )
    if not ordered:
        return []
    shift = ordered[0][0]
    return [(exponent - shift, coefficient) for exponent, coefficient in ordered]


def _decimal_sign_variations(terms: Sequence[tuple[Decimal, Decimal]]) -> int:
    signs = [1 if coefficient > 0 else -1 for _, coefficient in terms if coefficient != 0]
    return sum(left != right for left, right in pairwise(signs))


def _crossing_decimal_power_roots(
    terms: Sequence[tuple[float, float]],
    lo: float,
    hi: float,
) -> list[float]:
    """High-precision roots filtered to actual left/right sign crossings."""
    with localcontext() as context:
        context.prec = _IRR_DECIMAL_PRECISION
        decimal_terms = _decimal_normalise_power_terms(terms)
        decimal_lo = Decimal.from_float(lo)
        decimal_hi = Decimal.from_float(hi)
        roots = _all_decimal_power_roots(decimal_terms, decimal_lo, decimal_hi)
        crossing_roots: list[Decimal] = []
        for index, root in enumerate(roots):
            if root == decimal_lo or root == decimal_hi:
                crossing_roots.append(root)
                continue
            left_bound = roots[index - 1] if index > 0 else decimal_lo
            right_bound = roots[index + 1] if index + 1 < len(roots) else decimal_hi
            left_probe = (left_bound + root) / 2
            right_probe = (root + right_bound) / 2
            left_value, left_scale = _decimal_power_sum(left_probe, decimal_terms)
            right_value, right_scale = _decimal_power_sum(right_probe, decimal_terms)
            left_sign = _decimal_sign(left_value, left_scale)
            right_sign = _decimal_sign(right_value, right_scale)
            if left_sign * right_sign < 0:
                crossing_roots.append(root)

        converted = [float(root) for root in crossing_roots]
    converted.sort()
    return [
        root for index, root in enumerate(converted) if index == 0 or root != converted[index - 1]
    ]


def _positive_power_roots(
    terms: Sequence[tuple[float, float]],
    lo: float,
    hi: float,
) -> list[float]:
    """Isolate every root of a generalized polynomial on ``[lo, hi]``.

    For ``x = 1 / (1 + rate)``, NPV is a generalized polynomial
    ``sum(cash_flow * x**time)``. Its derivative has one fewer term after a
    harmless positive monomial factor is removed. Recursively finding those
    stationary points partitions the function into monotone intervals, so
    close root pairs cannot hide between fixed scan samples.

    Generalized Descartes' rule lets the overwhelmingly common one-sign-change
    project cash flow take the fast path: it has at most one positive root.
    """
    normalised = _normalise_power_terms(terms)
    if len(normalised) < 2 or _sign_variations(normalised) == 0:
        return []

    variations = _sign_variations(normalised)
    f_lo = _power_sum(lo, normalised)
    f_hi = _power_sum(hi, normalised)
    if variations == 1:
        roots: list[float] = []
        if f_lo == 0.0:
            roots.append(lo)
        if f_lo * f_hi < 0.0:
            roots.append(_bisect_power_sum(lo, hi, f_lo, normalised))
        if f_hi == 0.0:
            roots.append(hi)
        return roots

    # With several sign variations, binary-float cancellation at a stationary
    # point can erase its sign.  Isolate every derivative root (including
    # tangencies) in Decimal, then keep only top-level roots whose two sides
    # really have opposite signs.
    return _crossing_decimal_power_roots(normalised, lo, hi)


def irr_roots(flows: Sequence[float], times_years: Sequence[float]) -> list[float]:
    """Every rate in ``IRR_BRACKET`` where the NPV curve crosses zero.

    A series with more than one sign reversal can cross zero several times,
    and each crossing is a mathematically valid IRR. Transforming to
    ``x = 1 / (1 + rate)`` and recursively isolating derivative roots finds
    every monotone interval, including two crossings closer together than a
    practical fixed sampling grid.
    """
    lo_bound, hi_bound = IRR_BRACKET
    terms = [(time, flow) for flow, time in zip(flows, times_years, strict=True)]
    # ``rate`` increases as x decreases, so sort after transforming back.
    roots = [
        1.0 / x_root - 1.0
        for x_root in _positive_power_roots(
            terms,
            1.0 / (1.0 + hi_bound),
            1.0 / (1.0 + lo_bound),
        )
    ]
    roots.sort()
    return roots


def irr(flows: Sequence[float], times_years: Sequence[float]) -> float | None:
    """Internal rate of return: the rate where NPV is zero.

    ``None`` when the series has no such rate inside ``IRR_BRACKET`` (an
    all-positive, all-negative or all-zero series never crosses), and equally
    ``None`` when it has *several* — a series with more than one sign reversal
    can cross zero repeatedly and no single number is then the return. For
    ``[-954244, -390293, -528292, 1930642, 1572511, -238266, -51854, -865625,
    94536]`` the roots are -89.2%, -26.4% and +16.4% while NPV at 10% is
    +₹201,590; bisecting the whole bracket used to report -89.16% for that
    viable project. Multiple sign reversals alone are not disqualifying —
    only genuinely multiple roots are.
    """
    roots = irr_roots(flows, times_years)
    return roots[0] if len(roots) == 1 else None


def bcr(
    rate_annual: float,
    benefit_flows: Sequence[float],
    cost_flows: Sequence[float],
    times_years: Sequence[float],
) -> float | None:
    """Benefit-cost ratio: PV of gross benefits / PV of gross costs.

    The two series are supplied *gross* — revenue on one side, capital plus
    operating cost plus debt service on the other. Splitting a single series
    of net flows by sign instead yields ``1 + NPV / PV(costs)``: it agrees
    with NPV's sign but carries no information beyond it, and the magnitude a
    lender compares against the customary 1.5 threshold comes out 1.5-3x off.

    ``None`` when there are no discounted costs — the ratio is undefined, not
    infinite (an inf would crash JSON serialization of the result).
    """
    pv_costs = npv(rate_annual, cost_flows, times_years)
    if pv_costs == 0.0:
        return None
    return npv(rate_annual, benefit_flows, times_years) / pv_costs


def payback_month(cumulative_cash: Sequence[float]) -> int | None:
    """First month where cumulative cash turns non-negative; None if never.

    Index 0 (the month-0 equity outflow) is skipped: a fully-financed project
    (equity = 0) has no instant payback — it pays back when operating cash has
    actually accumulated.
    """
    for idx, value in enumerate(cumulative_cash):
        if idx > 0 and value >= 0.0:
            return idx
    return None
