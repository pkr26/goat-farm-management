"""Financial mathematics: amortization and NABARD-style viability metrics.

Pure functions over cash-flow series; the engine supplies the flows. Cash flows
are paired with explicit times (in years) so partial final years discount
correctly.
"""

from collections.abc import Sequence
from dataclasses import dataclass
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


IRR_BRACKET = (-0.99, 10.0)
_IRR_SCAN_STEPS = 220  # ~5 percentage points of discount rate per step


def irr_roots(flows: Sequence[float], times_years: Sequence[float]) -> list[float]:
    """Every rate in ``IRR_BRACKET`` where the NPV curve crosses zero.

    A series with more than one sign reversal can cross zero several times,
    and each crossing is a mathematically valid IRR. Bisecting the whole
    bracket would silently keep the leftmost one, so the crossings are located
    first, on a uniform scan, and each is then refined by bisection.
    """
    lo_bound, hi_bound = IRR_BRACKET
    step = (hi_bound - lo_bound) / _IRR_SCAN_STEPS
    roots: list[float] = []
    lo = lo_bound
    f_lo = npv(lo, flows, times_years)
    for index in range(1, _IRR_SCAN_STEPS + 1):
        hi = lo_bound + index * step
        f_hi = npv(hi, flows, times_years)
        if f_lo == 0.0:
            roots.append(lo)
        elif f_lo * f_hi < 0.0:
            roots.append(_bisect(lo, hi, f_lo, flows, times_years))
        lo, f_lo = hi, f_hi
    if f_lo == 0.0:
        roots.append(lo)
    return roots


def _bisect(
    lo: float,
    hi: float,
    f_lo: float,
    flows: Sequence[float],
    times_years: Sequence[float],
) -> float:
    """Refine a bracketed root of NPV(rate) to full double precision."""
    for _ in range(200):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid, flows, times_years)
        if f_lo * f_mid <= 0.0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0


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
