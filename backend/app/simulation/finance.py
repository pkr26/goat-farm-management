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


def irr(flows: Sequence[float], times_years: Sequence[float]) -> float | None:
    """Internal rate of return by bisection on (-0.99, 10), 200 iterations.

    Returns ``None`` when the NPV has the same sign at both bracket ends (no
    root, e.g. all-positive or all-negative series).
    """
    lo, hi = -0.99, 10.0
    f_lo = npv(lo, flows, times_years)
    f_hi = npv(hi, flows, times_years)
    if f_lo * f_hi > 0.0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid, flows, times_years)
        if f_lo * f_mid <= 0.0:
            hi = mid
            f_hi = f_mid
        else:
            lo = mid
            f_lo = f_mid
    return (lo + hi) / 2.0


def bcr(rate_annual: float, flows: Sequence[float], times_years: Sequence[float]) -> float:
    """Benefit-cost ratio: PV of inflows / PV of outflows (``inf`` if no outflows)."""
    pv_in = sum(
        cf / _fpow(1.0 + rate_annual, t)
        for cf, t in zip(flows, times_years, strict=True)
        if cf > 0.0
    )
    pv_out = sum(
        -cf / _fpow(1.0 + rate_annual, t)
        for cf, t in zip(flows, times_years, strict=True)
        if cf < 0.0
    )
    if pv_out == 0.0:
        return float("inf")
    return pv_in / pv_out


def payback_month(cumulative_cash: Sequence[float]) -> int | None:
    """First index (month; 0 = month 0) where cumulative cash turns non-negative."""
    for idx, value in enumerate(cumulative_cash):
        if value >= 0.0:
            return idx
    return None
