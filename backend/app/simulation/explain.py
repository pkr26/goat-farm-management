"""Plain-language explanations of every simulation output.

Deterministic and template-based: every metric explanation and every report
section is generated from the actual run numbers, so a user (or a bank
officer reviewing the project) can see exactly how each figure was produced.
No AI and no external calls — the same run always yields the same text.
"""

from .assumptions import SimulationAssumptions
from .results import MetricExplanation, ReportSection, SensitivityItem, SimulationResult


def _inr(value: float) -> str:
    """Format ₹ in Indian units (lakh / crore) for narrative text."""
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1e7:
        return f"{sign}₹{v / 1e7:.2f} crore"
    if v >= 1e5:
        return f"{sign}₹{v / 1e5:.2f} lakh"
    return f"{sign}₹{v:,.0f}"


def _pct(fraction: float) -> str:
    return f"{fraction * 100:.1f}%"


def _share(part: float, total: float) -> str:
    """'part of total' as a percentage string; '—' when total is zero."""
    return _pct(part / total) if total > 0.0 else "—"


def _year_of(month: int) -> str:
    if month <= 0:
        return "project start (month 0)"
    return f"month {month} (year {(month - 1) // 12 + 1})"


def _outstanding_at_horizon(a: SimulationAssumptions, result: SimulationResult) -> float:
    """Loan balance the engine force-repays in the final simulated month."""
    horizon = a.meta.horizon_months
    if horizon >= len(result.amortization):
        return 0.0
    return result.amortization[horizon - 1].closing_balance


def _ranked(items: list[tuple[str, float]], total: float) -> str:
    """'labour ₹12.00 lakh (64.4%), feed … and vet …', largest first.

    The ranking is computed, never assumed: a flat ₹10,000/month labour floor
    makes labour — not feed — the biggest cost of any smallholder herd, and a
    sentence that hard-codes the order contradicts the figures it prints.
    """
    ordered = sorted(items, key=lambda item: item[1], reverse=True)
    parts = [f"{name} {_inr(value)} ({_share(value, total)})" for name, value in ordered]
    return (", ".join(parts[:-1]) + " and " + parts[-1]) if len(parts) > 1 else parts[0]


def build_metric_explanations(
    a: SimulationAssumptions,
    result: SimulationResult,
    *,
    break_even_computed: bool = True,
) -> list[MetricExplanation]:
    """One explanation per viability metric, in display order.

    ``break_even_computed`` distinguishes the two reasons
    ``metrics.break_even_meat_price_per_kg`` can be ``None``: the search ran
    and failed at the schema ceiling, or it was never run for this request.
    Without it, a run that simply skipped the search was reported as a
    project no market price could rescue.
    """
    m = result.metrics
    b = result.project_cost_breakdown
    fin = a.finance
    out: list[MetricExplanation] = []

    out.append(
        MetricExplanation(
            key="project_cost",
            title="Project cost",
            explanation=(
                f"The total capital needed to start the project: shed {_inr(b.shed_cost)} "
                f"+ equipment {_inr(b.equipment_cost)} + starting stock {_inr(b.stock_cost)} "
                f"+ working capital {_inr(b.working_capital)} ("
                f"{fin.working_capital_months} month(s) of average year-1 recurring "
                f"operating cost — feed, vet, labour, insurance, overheads and selling; "
                f"stock purchases are excluded). "
                f"Shed and equipment are funded for {b.capacity_places:.1f} animal places "
                f"using the {b.capacity_basis.replace('_', ' ')} capacity basis; the peak funded "
                f"headcount — the larger of the opening herd and the highest physical "
                f"headcount reached during the projection — is {b.projected_peak_head:.1f} head."
            ),
            figures={
                "project_cost": m.project_cost,
                "shed_cost": b.shed_cost,
                "equipment_cost": b.equipment_cost,
                "stock_cost": b.stock_cost,
                "working_capital": b.working_capital,
                "capacity_places": b.capacity_places,
                "capacity_basis": b.capacity_basis,
                "projected_peak_head": b.projected_peak_head,
            },
        )
    )
    out.append(
        MetricExplanation(
            key="loan_amount",
            title="Bank loan",
            explanation=(
                f"{_pct(fin.loan_fraction_of_project_cost)} of the project cost "
                f"({_inr(m.project_cost)}) is financed by the bank: {_inr(m.loan_amount)}. "
                f"It is repaid over {fin.loan_term_months} months at "
                f"{_pct(fin.interest_rate_annual)} annual interest"
                + (
                    f", with an interest-only moratorium for the first "
                    f"{fin.moratorium_months} month(s)"
                    if fin.moratorium_months > 0
                    else ""
                )
                # When the loan outlives the projection the engine charges the
                # whole outstanding balance in the final month. Saying only
                # "repaid over N months" hid a balloon the reader is asked to
                # fund.
                + (
                    f". The {a.meta.horizon_months}-month projection ends first, so the "
                    f"{_inr(_outstanding_at_horizon(a, result))} still outstanding is charged "
                    f"as a single balloon repayment in the final month."
                    if _outstanding_at_horizon(a, result) > 0.0
                    else "."
                )
            ),
            figures={
                "loan_amount": m.loan_amount,
                "loan_fraction": fin.loan_fraction_of_project_cost,
                "interest_rate_annual": fin.interest_rate_annual,
                "loan_term_months": fin.loan_term_months,
                "moratorium_months": fin.moratorium_months,
            },
        )
    )
    out.append(
        MetricExplanation(
            key="subsidy_amount",
            title="Subsidy",
            explanation=(
                f"Capital subsidy at {_pct(fin.subsidy_fraction)} of the project cost: "
                f"{_inr(m.subsidy_amount)}. This is money you do not have to invest or repay."
                if m.subsidy_amount > 0.0
                else "No capital subsidy is assumed. Set a subsidy fraction if your scheme "
                "(e.g. NABARD/NLM) provides one."
            ),
            figures={"subsidy_amount": m.subsidy_amount, "subsidy_fraction": fin.subsidy_fraction},
        )
    )
    out.append(
        MetricExplanation(
            key="equity",
            title="Your investment (equity)",
            explanation=(
                f"What you pay out of pocket at the start: project cost {_inr(m.project_cost)} "
                f"− loan {_inr(m.loan_amount)} − subsidy {_inr(m.subsidy_amount)} "
                f"= {_inr(m.equity)}. All returns below are measured against this outflow."
            ),
            figures={"equity": m.equity},
        )
    )
    # Three disjoint branches. ">= 0" here against "<= 0" in the verdict below
    # meant an NPV of exactly zero was called positive in one paragraph and
    # negative in the next, and still came out NOT VIABLE.
    npv_method = (
        f"Net present value: every month's net cash flow, including the recoverable closing "
        f"assets of {_inr(m.terminal_value)}, is discounted to today at "
        f"{_pct(fin.discount_rate_annual)} per year, and the {_inr(m.equity)} equity outflow "
        f"is subtracted. "
    )
    if m.npv > 0.0:
        npv_text = (
            f"{npv_method}A positive NPV of {_inr(m.npv)} means the project creates that much "
            f"wealth over and above a {_pct(fin.discount_rate_annual)} annual return."
        )
    elif m.npv == 0.0:
        npv_text = (
            f"{npv_method}The NPV is exactly zero: the project earns the "
            f"{_pct(fin.discount_rate_annual)} discount rate and nothing more, so it breaks "
            f"even against the required return with no margin for error."
        )
    else:
        npv_text = (
            f"{npv_method}The NPV is {_inr(m.npv)} — negative, so at this discount rate the "
            f"project destroys value; improve margins, prices or costs before investing."
        )
    out.append(
        MetricExplanation(
            key="npv",
            title="Net present value (NPV)",
            explanation=npv_text,
            figures={"npv": m.npv, "discount_rate_annual": fin.discount_rate_annual},
        )
    )
    out.append(
        MetricExplanation(
            key="mirr",
            title="Modified IRR (MIRR)",
            explanation=(
                f"MIRR is {_pct(m.mirr)} using the loan rate "
                f"({_pct(fin.interest_rate_annual)}) to finance negative cash flows and "
                f"{_pct(fin.reinvestment_rate_annual)} to reinvest positive cash flows. "
                "It gives one conservative annual return even when ordinary IRR is ambiguous."
                if m.mirr is not None
                else "MIRR is undefined because this projection does not contain both a "
                "negative and a positive cash flow."
            ),
            figures={
                "mirr": m.mirr,
                "finance_rate_annual": fin.interest_rate_annual,
                "reinvestment_rate_annual": fin.reinvestment_rate_annual,
            },
        )
    )
    out.append(
        MetricExplanation(
            key="irr",
            title="Internal rate of return (IRR)",
            explanation=(
                f"The discount rate that zeroes the same monthly cash-flow series as NPV — "
                f"project's implied annual return: {_pct(m.irr)}. It exceeds your "
                f"{_pct(fin.discount_rate_annual)} discount rate, so the project beats the "
                f"required return."
                if m.irr is not None and m.irr >= fin.discount_rate_annual
                else (
                    f"The discount rate that zeroes the same monthly cash-flow series as NPV — "
                    f"project's implied annual return: {_pct(m.irr)}. This is below your "
                    f"{_pct(fin.discount_rate_annual)} discount rate, so the project falls "
                    f"short of the required return."
                    if m.irr is not None
                    else "IRR is undefined for this cash-flow pattern: the flows either "
                    "never cross zero or admit several mathematically valid rates. "
                    "Judge the project on NPV and MIRR instead."
                )
            ),
            figures={"irr": m.irr, "discount_rate_annual": fin.discount_rate_annual},
        )
    )
    out.append(
        MetricExplanation(
            key="bcr",
            title="Benefit-cost ratio (BCR)",
            explanation=(
                f"Present value of gross operating revenue plus terminal recovery divided by "
                f"the present value of equity, operating cost, tax and debt service: {m.bcr:.2f}. "
                f"Above 1.0 the project earns more than it costs (at the "
                f"{_pct(fin.discount_rate_annual)} discount rate); use your lender's own "
                f"required threshold for approval."
                if m.bcr is not None
                else "Benefit-cost ratio is undefined because the project has no "
                "discounted costs at all (e.g. a fully subsidised, immediately "
                "cash-positive setup)."
            ),
            figures={"bcr": m.bcr},
        )
    )
    # A DSCR of zero or less is a real (catastrophic) debt year, so the branch
    # is on whether any year carries debt service — never on the ratio's sign.
    debt_years = sum(1 for row in result.annual_pl if row.debt_service > 0.0)
    out.append(
        MetricExplanation(
            key="avg_dscr",
            title="Average DSCR",
            explanation=(
                f"Debt-service coverage ratio: operating surplus after cash tax divided by the "
                f"year's loan repayment, averaged over the {debt_years} "
                f"year(s) with debt outstanding: {m.avg_dscr:.2f}. Above 1.0 the farm can "
                f"service the loan from operations; banks usually want 1.5 or better."
                if m.avg_dscr is not None
                else "No debt service falls inside the projection horizon (zero loan or the "
                "loan is fully repaid before year 1), so no DSCR is computed."
            ),
            figures={"avg_dscr": m.avg_dscr, "min_dscr": m.min_dscr, "debt_years": debt_years},
        )
    )
    if m.min_dscr is None:
        min_dscr_text = "No debt year in the horizon, so there is no weak year to report."
    elif m.min_dscr <= 0.0:
        min_dscr_text = (
            f"The worst single debt year: {m.min_dscr:.2f}. It is not positive, so that "
            f"year's operating surplus (EBITDA) was zero or negative — the farm cannot pay "
            f"any part of the instalment out of operations and must fund it from the "
            f"promoter's pocket, a longer moratorium or a rescheduling."
        )
    elif m.min_dscr < 1.0:
        # State the fact, not the hypothetical: this reading has already fallen
        # below 1.0, and "if this dips below 1.0" read as reassurance about the
        # very number that failed.
        min_dscr_text = (
            f"The worst single debt year: {m.min_dscr:.2f}. That is below 1.0, so the farm "
            f"cannot cover that year's repayment from operations and needs a cash buffer "
            f"or a rescheduling."
        )
    else:
        min_dscr_text = (
            f"The worst single debt year: {m.min_dscr:.2f}. If this dips below 1.0 the "
            f"farm cannot cover that year's repayment from operations and needs a cash "
            f"buffer or rescheduling."
        )
    out.append(
        MetricExplanation(
            key="min_dscr",
            title="Weakest-year DSCR",
            explanation=min_dscr_text,
            figures={"min_dscr": m.min_dscr},
        )
    )
    out.extend(
        [
            MetricExplanation(
                key="peak_capacity_head",
                title="Funded capacity",
                explanation=(
                    f"The project funds {m.peak_capacity_head:.1f} animal places against a "
                    f"peak funded headcount of {b.projected_peak_head:.1f} head — the "
                    f"larger of the opening herd and the highest physical headcount "
                    "reached during the projection. "
                    f"The reserve is {m.peak_capacity_head - b.projected_peak_head:.1f} places."
                ),
                figures={
                    "capacity_places": m.peak_capacity_head,
                    "projected_peak_head": b.projected_peak_head,
                    "capacity_reserve_head": m.peak_capacity_head - b.projected_peak_head,
                },
            ),
            MetricExplanation(
                key="terminal_value",
                title="Terminal value",
                explanation=(
                    f"The final cash flow recovers {_inr(m.terminal_value)}: livestock "
                    f"{_inr(result.terminal_value_breakdown.livestock)}, shed "
                    f"{_inr(result.terminal_value_breakdown.shed)}, equipment "
                    f"{_inr(result.terminal_value_breakdown.equipment)} and working capital "
                    f"{_inr(result.terminal_value_breakdown.working_capital)}. These are "
                    "closing assets, not operating revenue."
                    if fin.include_terminal_value
                    else "Terminal value is disabled, so the projection assumes no livestock, "
                    "facility or working-capital recovery at the horizon."
                ),
                figures={
                    "terminal_value": m.terminal_value,
                    "livestock": result.terminal_value_breakdown.livestock,
                    "shed": result.terminal_value_breakdown.shed,
                    "equipment": result.terminal_value_breakdown.equipment,
                    "working_capital": result.terminal_value_breakdown.working_capital,
                },
            ),
            MetricExplanation(
                key="tax_total",
                title="Cash tax",
                explanation=(
                    f"The model pays {_inr(m.tax_total)} of cash tax at an assumed "
                    f"{_pct(fin.income_tax_rate)} rate after interest, straight-line "
                    f"depreciation and "
                    + (
                        "carried-forward losses."
                        if fin.tax_loss_carryforward
                        else "current-period losses only."
                    )
                ),
                figures={"tax_total": m.tax_total, "income_tax_rate": fin.income_tax_rate},
            ),
            MetricExplanation(
                key="accounting_profit_total",
                title="Accounting profit",
                explanation=(
                    f"Cumulative profit after depreciation, interest and tax is "
                    f"{_inr(m.accounting_profit_total)}. This accrual profit excludes terminal "
                    "asset recovery and differs from cash flow because depreciation is non-cash "
                    "while principal repayment is not an expense."
                ),
                figures={"accounting_profit_total": m.accounting_profit_total},
            ),
            MetricExplanation(
                key="minimum_cash_balance",
                title="Liquidity low point",
                explanation=(
                    f"The operating cash reserve reaches its low point of "
                    f"{_inr(m.minimum_cash_balance)} in {_year_of(m.minimum_cash_month)}. "
                    + (
                        f"The plan therefore needs at least another "
                        f"{_inr(m.additional_working_capital_required)} beyond the funded "
                        "working-capital reserve to avoid a cash shortfall."
                        if m.additional_working_capital_required > 0.0
                        else "The funded working-capital reserve remains non-negative throughout."
                    )
                ),
                figures={
                    "minimum_cash_balance": m.minimum_cash_balance,
                    "minimum_cash_month": m.minimum_cash_month,
                    "additional_working_capital_required": m.additional_working_capital_required,
                },
            ),
            MetricExplanation(
                key="operating_margin",
                title="Operating margin",
                explanation=(
                    f"EBITDA is {_pct(m.operating_margin)} of operating revenue over the "
                    "projection. This isolates farm operations before depreciation, financing "
                    "and tax."
                    if m.operating_margin is not None
                    else "Operating margin is undefined because the projection has no "
                    "operating revenue."
                ),
                figures={"operating_margin": m.operating_margin},
            ),
        ]
    )
    out.append(
        MetricExplanation(
            key="payback_month",
            title="Payback period",
            explanation=(
                f"Cumulative cash flow first covers your {_inr(m.equity)} equity in "
                f"{_year_of(m.payback_month)}."
                if m.payback_month is not None
                else f"Cumulative cash flow never covers your {_inr(m.equity)} equity within "
                f"the {a.meta.horizon_months}-month horizon."
            ),
            figures={"payback_month": m.payback_month, "equity": m.equity},
        )
    )
    be = m.break_even_meat_price_per_kg
    assumed = a.sales.meat_price_per_kg
    if be is None and not break_even_computed:
        be_text = (
            "The break-even meat price was not computed for this run. Re-run with the "
            "break-even search enabled to see how far the price can fall."
        )
        margin: float | None = None
    elif be is None:
        be_text = (
            "Even at the break-even search ceiling the project cannot reach NPV = 0 — "
            "the economics need structural changes (costs, herd size, financing), not just "
            "a better market price."
        )
        margin = None
    elif be <= 0.0:
        be_text = (
            "The project is profitable even with meat revenue at zero — the break-even "
            "meat price is effectively ₹0/kg."
        )
        margin = 1.0
    elif assumed <= 0.0:
        margin = None
        be_text = (
            f"Meat must rise to ₹{be:,.0f}/kg for the project to break even; the current "
            "assumption is ₹0/kg, so a percentage safety margin is not defined."
        )
    else:
        margin = (assumed - be) / assumed
        be_text = (
            f"Meat can fall to ₹{be:,.0f}/kg before the project's NPV turns negative. "
            f"Your assumed price is ₹{assumed:,.0f}/kg — a safety margin of {_pct(margin)}."
            if margin >= 0.0
            else f"Meat must rise to ₹{be:,.0f}/kg for the project to break even; your "
            f"assumed price of ₹{assumed:,.0f}/kg is {_pct(-margin)} too low."
        )
    out.append(
        MetricExplanation(
            key="break_even_meat_price_per_kg",
            title="Break-even meat price",
            explanation=be_text,
            figures={
                "break_even_meat_price_per_kg": be,
                "assumed_meat_price_per_kg": assumed,
                "safety_margin": margin,
            },
        )
    )
    return out


def _active_festival_months(assumptions: SimulationAssumptions) -> list[int]:
    """Months where the Bakrid uplift applies (explicit lunar months, or the
    legacy recurring Gregorian month)."""
    sales = assumptions.sales
    if sales.festival_sale_months:
        return list(sales.festival_sale_months)
    if sales.eid_month > 0:
        start = int(assumptions.meta.start_year_month.split("-")[1])
        horizon = assumptions.meta.horizon_months
        return [
            month
            for month in range(1, horizon + 1)
            if (start - 1 + (month - 1)) % 12 + 1 == sales.eid_month
        ]
    return []


def _festival_paragraph(assumptions: SimulationAssumptions, result: SimulationResult) -> list[str]:
    months = _active_festival_months(assumptions)
    if not months:
        return []
    uplift_pct = assumptions.sales.eid_price_uplift * 100.0
    # Only meat sales carry the festival uplift; cull disposals are priced on
    # the growth trend alone, so they are not counted as Bakrid-priced head.
    priced = [row for row in result.months if row.month in months and row.sales_head > 0.0]
    head = sum(row.sales_head for row in priced)
    if head <= 0.0:
        return [
            f"Bakrid pricing (+{uplift_pct:.0f}% on the base rate) applies in months "
            f"{', '.join(str(m) for m in months)}, but the plan sells no animals in "
            "those months — timing sales into the festival is the single largest "
            "pricing lever available."
        ]
    return [
        f"Bakrid pricing (+{uplift_pct:.0f}% on the base rate) applies in months "
        f"{', '.join(str(m) for m in months)}; {head:.0f} head sell inside them."
    ]


def _festival_figures(assumptions: SimulationAssumptions) -> dict[str, float | str | None]:
    months = _active_festival_months(assumptions)
    if not months:
        return {}
    return {
        "festival_months": ", ".join(str(m) for m in months),
        "festival_uplift": assumptions.sales.eid_price_uplift,
    }


def build_narrative_report(
    a: SimulationAssumptions, result: SimulationResult
) -> list[ReportSection]:
    """The full 'what this means for your farm' report, in display order."""
    m = result.metrics
    months = result.months
    horizon = a.meta.horizon_months
    years = horizon / 12.0
    sections: list[ReportSection] = []

    # --- 1. overview -------------------------------------------------------
    start = months[0]
    purchases = [e for e in a.events if e.kind == "purchase"]
    sales_events = [e for e in a.events if e.kind == "sale"]
    event_bits: list[str] = []
    if purchases:
        event_bits.append(f"buy {sum(e.count for e in purchases):,.0f} head")
    if sales_events:
        event_bits.append(f"sell {sum(e.count for e in sales_events):,.0f} head")
    events_text = (
        f" You have scheduled {len(a.events)} herd event(s) along the way — "
        + " and ".join(event_bits)
        + "."
        if a.events
        else ""
    )
    sections.append(
        ReportSection(
            key="overview",
            title="Overview",
            paragraphs=[
                f"This projection runs {horizon} months ({years:g} years) from "
                f"{a.meta.start_year_month}. You start with {a.herd.does} does and "
                f"{a.herd.bucks} bucks"
                + (
                    f" plus "
                    f"{a.herd.female_kids + a.herd.male_kids:g} kids, "
                    f"{a.herd.female_weaners + a.herd.male_weaners:g} weaners and "
                    f"{a.herd.female_growers + a.herd.male_growers:g} growers"
                    if (
                        a.herd.female_kids
                        + a.herd.male_kids
                        + a.herd.female_weaners
                        + a.herd.male_weaners
                        + a.herd.female_growers
                        + a.herd.male_growers
                    )
                    > 0
                    else ""
                )
                + f" — {start.total_herd:.0f} head on the ground in month 1.{events_text}",
                f"The project needs {_inr(m.project_cost)} in total: {_inr(m.loan_amount)} "
                f"from the bank, {_inr(m.subsidy_amount)} subsidy and {_inr(m.equity)} "
                f"from your own pocket.",
            ],
            figures={
                "horizon_months": horizon,
                "starting_does": a.herd.does,
                "starting_bucks": a.herd.bucks,
                "scheduled_events": len(a.events),
                "project_cost": m.project_cost,
                "loan_amount": m.loan_amount,
                "subsidy_amount": m.subsidy_amount,
                "equity": m.equity,
            },
        )
    )

    # --- 2. herd trajectory -------------------------------------------------
    peak = max(months, key=lambda row: row.total_herd)
    end = months[-1]
    end_does = end.open_does + end.pregnant_does + end.lactating_does
    total_births = sum(row.births for row in months)
    total_deaths = sum(row.deaths for row in months)
    total_sold = sum(row.sales_head for row in months)
    total_culled = sum(row.culls_head for row in months)
    sections.append(
        ReportSection(
            key="herd_trajectory",
            title="Herd trajectory",
            paragraphs=[
                f"The herd grows from {start.total_herd:.0f} head in month 1 to a peak of "
                f"{peak.total_herd:.0f} in {_year_of(peak.month)}, ending at "
                f"{end.total_herd:.0f} head ({end_does:.0f} breeding does and "
                f"{end.bucks:.0f} bucks).",
                f"Over the {years:g} years the farm produces {total_births:.0f} kids, loses "
                f"{total_deaths:.0f} animals to mortality, sells {total_sold:.0f} for meat "
                f"and disposes of {total_culled:.0f} as culls.",
            ],
            figures={
                "start_herd": start.total_herd,
                "peak_herd": peak.total_herd,
                "peak_month": peak.month,
                "end_herd": end.total_herd,
                "total_births": total_births,
                "total_deaths": total_deaths,
                "total_sold": total_sold,
                "total_culled": total_culled,
            },
        )
    )

    # --- 3. revenue mix ------------------------------------------------------
    meat = sum(row.meat_revenue for row in result.annual_pl)
    cull_rev = sum(row.cull_revenue for row in result.annual_pl)
    milk = sum(row.milk_revenue for row in result.annual_pl)
    manure = sum(row.manure_revenue for row in result.annual_pl)
    total_revenue = meat + cull_rev + milk + manure
    sections.append(
        ReportSection(
            key="revenue_mix",
            title="Where the money comes from",
            paragraphs=[
                f"Total revenue over {years:g} years is {_inr(total_revenue)}, "
                f"largest source first: "
                + _ranked(
                    [
                        ("meat sales", meat),
                        ("cull sales", cull_rev),
                        ("milk", milk),
                        ("manure", manure),
                    ],
                    total_revenue,
                )
                + ".",
                *_festival_paragraph(a, result),
            ],
            figures={
                "total_revenue": total_revenue,
                "meat_revenue": meat,
                "cull_revenue": cull_rev,
                "milk_revenue": milk,
                "manure_revenue": manure,
                **_festival_figures(a),
            },
        )
    )

    # --- 4. cost mix ---------------------------------------------------------
    feed = sum(row.feed_cost for row in result.annual_pl)
    vet = sum(row.vet_cost for row in result.annual_pl)
    labour = sum(row.labour_cost for row in result.annual_pl)
    insurance = sum(row.insurance_cost for row in result.annual_pl)
    misc = sum(row.misc_cost for row in result.annual_pl)
    selling = sum(row.selling_cost for row in result.annual_pl)
    stock_purchases = sum(row.stock_purchases for row in result.annual_pl)
    total_opex = feed + vet + labour + insurance + misc + selling + stock_purchases
    sections.append(
        ReportSection(
            key="cost_mix",
            title="Where the money goes",
            paragraphs=[
                f"Operating costs total {_inr(total_opex)} over {years:g} years, "
                f"largest first: "
                + _ranked(
                    [
                        ("feed", feed),
                        ("labour", labour),
                        ("stock purchases", stock_purchases),
                        ("selling", selling),
                        ("vet", vet),
                        ("insurance", insurance),
                        ("overheads", misc),
                    ],
                    total_opex,
                )
                + ".",
                f"Growing green fodder needs about "
                f"{result.feed_summary.land_requirement_acres:.2f} acre(s) on average"
                + (
                    f"; your cultivated area falls short in "
                    f"{result.feed_summary.fodder_deficit_months} month(s). This is a "
                    f"physical and financial shortfall: "
                    f"{sum(result.feed_summary.annual_purchased_green_kg):,.0f} "
                    f"kg as-fed is bought at the configured market price, while on-farm supply "
                    f"is costed separately."
                    if result.feed_summary.fodder_deficit_months > 0
                    else "."
                ),
            ],
            figures={
                "total_opex": total_opex,
                "feed_cost": feed,
                "labour_cost": labour,
                "stock_purchases": stock_purchases,
                "selling_cost": selling,
                "vet_cost": vet,
                "insurance_cost": insurance,
                "misc_cost": misc,
                "land_requirement_acres": result.feed_summary.land_requirement_acres,
            },
        )
    )

    # --- 5. viability verdict -------------------------------------------------
    ebitda_total = total_revenue - total_opex
    problems: list[str] = []
    if m.npv < 0.0:
        problems.append("the NPV is negative")
    elif m.npv == 0.0:
        problems.append(
            "the NPV is exactly zero — the project only just clears the "
            "discount rate, with no margin"
        )
    if m.bcr is not None and m.bcr < 1.0:
        problems.append("the benefit-cost ratio is below 1.0")
    if m.irr is None:
        problems.append("the IRR is undefined for this cash-flow pattern")
    elif m.irr < a.finance.discount_rate_annual:
        problems.append("the IRR is below your discount rate")
    # None means "no debt year at all"; every number below 1.0 — including the
    # non-positive ones, which are strictly the worst outcome — is a problem.
    if m.min_dscr is not None and m.min_dscr <= 0.0:
        problems.append(
            "the weakest debt year has a non-positive DSCR (no operating surplus to pay "
            "the instalment from)"
        )
    elif m.min_dscr is not None and m.min_dscr < 1.0:
        problems.append("the weakest debt year has a DSCR below 1.0")
    if m.payback_month is None:
        problems.append("the equity is never paid back inside the horizon")
    if m.additional_working_capital_required > 0.0:
        problems.append(
            "the funded working-capital reserve becomes negative and additional liquidity is needed"
        )
    capacity_shortfall = (
        result.project_cost_breakdown.projected_peak_head
        - result.project_cost_breakdown.capacity_places
    )
    if capacity_shortfall > 1e-9:
        problems.append(
            f"the projected peak herd exceeds funded housing and equipment capacity by "
            f"{capacity_shortfall:.1f} head"
        )
    # An NPV of exactly zero meets the required return; it is a caution, not
    # a rejection, so only a strictly negative NPV condemns the project.
    if m.npv < 0.0 or (m.bcr is not None and m.bcr < 1.0) or capacity_shortfall > 1e-9:
        verdict = "NOT VIABLE"
    elif problems:
        verdict = "VIABLE WITH CAUTION"
    else:
        verdict = "VIABLE"
    paragraphs = [
        f"At these assumptions the project is {verdict}. Over {years:g} years it earns "
        f"{_inr(total_revenue)} against {_inr(total_opex)} of operating costs — an "
        f"operating surplus (EBITDA) of {_inr(ebitda_total)}.",
    ]
    if problems:
        paragraphs.append("Watch out: " + "; ".join(problems) + ".")
    else:
        # bcr is None only when there are no outflows, which makes irr None
        # too — so the "all checks pass" branch always has a real bcr.
        paragraphs.append(
            f"All standard checks pass: NPV {_inr(m.npv)} is positive, BCR is "
            + (f"{m.bcr:.2f}" if m.bcr is not None else "undefined (no outflows)")
            + (f", IRR is {_pct(m.irr)}" if m.irr is not None else "")
            + f", and the equity is recovered in {_year_of(m.payback_month or 0)}."
        )
    sections.append(
        ReportSection(
            key="viability_verdict",
            title="Bottom line",
            paragraphs=paragraphs,
            figures={
                "verdict": verdict,
                "npv": m.npv,
                "irr": m.irr,
                "bcr": m.bcr,
                "avg_dscr": m.avg_dscr,
                "min_dscr": m.min_dscr,
                "payback_month": m.payback_month,
                "ebitda_total": ebitda_total,
            },
        )
    )

    # --- 6. risks -------------------------------------------------------------
    risk_paragraphs: list[str] = []
    risk_figures: dict[str, float | str | None] = {}
    if result.monte_carlo is not None:
        mc = result.monte_carlo
        risk_paragraphs.append(
            f"Across {mc.runs} correlated Monte Carlo runs (varying prices, feed, fodder yield, "
            f"operating cost, mortality and reproduction, plus monthly adverse events), the NPV "
            f"averages {_inr(mc.npv_mean)} "
            f"with a 90% range of {_inr(mc.npv_p5)} to {_inr(mc.npv_p95)}. The project "
            f"loses money in {_pct(mc.prob_npv_negative)} of runs and runs short of operating "
            f"cash in {_pct(mc.prob_liquidity_shortfall)}."
        )
        risk_figures.update(
            {
                "mc_runs": mc.runs,
                "npv_mean": mc.npv_mean,
                "npv_p5": mc.npv_p5,
                "npv_p95": mc.npv_p95,
                "prob_npv_negative": mc.prob_npv_negative,
                "prob_liquidity_shortfall": mc.prob_liquidity_shortfall,
                "prob_dscr_below_one": mc.prob_dscr_below_one,
            }
        )
    if result.sensitivity:
        top = sorted(
            result.sensitivity,
            key=lambda item: max(abs(item.delta_npv_low), abs(item.delta_npv_high)),
            reverse=True,
        )[:3]

        def _biggest(item: SensitivityItem) -> tuple[str, float]:
            """(applied perturbation, NPV delta) for the item's larger side.

            The label comes from the run itself: the tornado does not move
            every parameter by 20% (sale age moves in whole months, and four
            cases clamp at a schema ceiling), so quoting a flat 20% here
            described scenarios the engine never ran.
            """
            low_first = abs(item.delta_npv_low) >= abs(item.delta_npv_high)
            if low_first:
                return item.label_low, item.delta_npv_low
            return item.label_high, item.delta_npv_high

        movers = ", ".join(
            f"{item.parameter.replace('_', ' ')} "
            f"({_biggest(item)[0]} moves NPV by {_inr(_biggest(item)[1])})"
            for item in top
        )
        risk_paragraphs.append(f"The assumptions that move NPV the most: {movers}.")
        risk_figures["top_sensitivities"] = ", ".join(item.parameter for item in top)
    if not risk_paragraphs:
        risk_paragraphs.append(
            "Run with Monte Carlo and sensitivity enabled to see how robust these results "
            "are to price swings, disease and poor breeding years."
        )
    sections.append(
        ReportSection(
            key="risks",
            title="Risks",
            paragraphs=risk_paragraphs,
            figures=risk_figures,
        )
    )

    if result.optimization is not None:
        optimization = result.optimization
        if optimization.recommended is None:
            recommendation_paragraphs = [
                f"None of the {optimization.evaluated_candidates} tested plans met every "
                f"configured financing, liquidity, capacity and DSCR constraint. No plan is "
                f"labelled as recommended; revise the constraints or economics before acting."
            ]
            recommendation_figures: dict[str, float | str | None] = {
                "objective": optimization.objective,
                "evaluated_candidates": float(optimization.evaluated_candidates),
                "feasible_candidates": 0.0,
            }
        else:
            recommended = optimization.recommended
            recommendation_paragraphs = [
                f"Under the {optimization.objective} objective, the highest-ranked feasible "
                f"plan starts with {recommended.starting_does} does and "
                f"{recommended.starting_bucks} bucks, targets "
                f"{recommended.max_breeding_does} breeding does, sells at "
                f"{recommended.sale_age_months} months, retains "
                f"{_pct(recommended.female_retention_fraction)} of eligible females and uses "
                f"{_pct(recommended.loan_fraction)} debt. Its NPV is {_inr(recommended.npv)} "
                f"with a minimum DSCR of "
                + (f"{recommended.min_dscr:.2f}." if recommended.min_dscr is not None else "N/A.")
            ]
            recommendation_figures = {
                "objective": optimization.objective,
                "evaluated_candidates": float(optimization.evaluated_candidates),
                "feasible_candidates": float(optimization.feasible_candidates),
                "recommended_npv": recommended.npv,
                "recommended_starting_does": float(recommended.starting_does),
                "recommended_min_dscr": recommended.min_dscr,
            }
        sections.append(
            ReportSection(
                key="optimization",
                title="Decision optimization",
                paragraphs=recommendation_paragraphs,
                figures=recommendation_figures,
            )
        )

    return sections
