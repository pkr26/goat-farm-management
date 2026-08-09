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
    return f"month {month} (year {(month - 1) // 12 + 1})"


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
    a: SimulationAssumptions, result: SimulationResult
) -> list[MetricExplanation]:
    """One explanation per viability metric, in display order."""
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
                f"{fin.working_capital_months} month(s) of average year-1 operating cost)."
            ),
            figures={
                "project_cost": m.project_cost,
                "shed_cost": b.shed_cost,
                "equipment_cost": b.equipment_cost,
                "stock_cost": b.stock_cost,
                "working_capital": b.working_capital,
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
                    f"{fin.moratorium_months} month(s)."
                    if fin.moratorium_months > 0
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
    npv_text = (
        f"Net present value: every year's net cash flow is discounted to today at "
        f"{_pct(fin.discount_rate_annual)} per year, and the {_inr(m.equity)} equity outflow "
        f"is subtracted. A positive NPV of {_inr(m.npv)} means the project creates that much "
        f"wealth over and above a {_pct(fin.discount_rate_annual)} annual return."
        if m.npv >= 0.0
        else f"Net present value: every year's net cash flow is discounted to today at "
        f"{_pct(fin.discount_rate_annual)} per year, and the {_inr(m.equity)} equity outflow "
        f"is subtracted. The NPV is {_inr(m.npv)} — negative, so at this discount rate the "
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
            key="irr",
            title="Internal rate of return (IRR)",
            explanation=(
                f"The discount rate at which the NPV would be exactly zero — the project's "
                f"implied annual return: {_pct(m.irr)}. It exceeds your "
                f"{_pct(fin.discount_rate_annual)} discount rate, so the project beats the "
                f"required return."
                if m.irr is not None and m.irr >= fin.discount_rate_annual
                else (
                    f"The discount rate at which the NPV would be exactly zero — the project's "
                    f"implied annual return: {_pct(m.irr)}. This is below your "
                    f"{_pct(fin.discount_rate_annual)} discount rate, so the project falls "
                    f"short of the required return."
                    if m.irr is not None
                    else "The IRR is undefined for this cash-flow pattern: the flows either "
                    "never change sign (the investment is never recovered) or change sign "
                    "more than once, which admits several mathematically valid rates. Judge "
                    "the project on its NPV instead."
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
                f"Present value of the project's gross revenue divided by the present value "
                f"of its gross costs (capital, operating cost and debt service): {m.bcr:.2f}. "
                f"Above 1.0 the project earns more than it costs (at the "
                f"{_pct(fin.discount_rate_annual)} discount rate); banks typically look for "
                f"1.5 or better."
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
                f"Debt-service coverage ratio: operating surplus (EBITDA) divided by the "
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
    if be is None:
        be_text = (
            "Even at the break-even search ceiling the project cannot reach NPV = 0 — "
            "the economics need structural changes (costs, herd size, financing), not just "
            "a better market price."
        )
        margin: float | None = None
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
        event_bits.append(f"buy {sum(e.count for e in purchases):g} head")
    if sales_events:
        event_bits.append(f"sell {sum(e.count for e in sales_events):g} head")
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
                + "."
            ],
            figures={
                "total_revenue": total_revenue,
                "meat_revenue": meat,
                "cull_revenue": cull_rev,
                "milk_revenue": milk,
                "manure_revenue": manure,
            },
        )
    )

    # --- 4. cost mix ---------------------------------------------------------
    feed = sum(row.feed_cost for row in result.annual_pl)
    vet = sum(row.vet_cost for row in result.annual_pl)
    labour = sum(row.labour_cost for row in result.annual_pl)
    insurance = sum(row.insurance_cost for row in result.annual_pl)
    misc = sum(row.misc_cost for row in result.annual_pl)
    stock_purchases = sum(row.stock_purchases for row in result.annual_pl)
    total_opex = feed + vet + labour + insurance + misc + stock_purchases
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
                    f"{result.feed_summary.fodder_deficit_months} month(s), which is costed "
                    f"as purchased feed."
                    if result.feed_summary.fodder_deficit_months > 0
                    else "."
                ),
            ],
            figures={
                "total_opex": total_opex,
                "feed_cost": feed,
                "labour_cost": labour,
                "stock_purchases": stock_purchases,
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
    if m.npv <= 0.0:
        problems.append("the NPV is negative")
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
    if m.npv <= 0.0 or (m.bcr is not None and m.bcr < 1.0):
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
            f"Across {mc.runs} Monte Carlo runs (varying prices, mortality, litter size "
            f"and conception within your risk spreads), the NPV averages {_inr(mc.npv_mean)} "
            f"with a 90% range of {_inr(mc.npv_p5)} to {_inr(mc.npv_p95)}. The project "
            f"loses money in {_pct(mc.prob_npv_negative)} of the runs."
        )
        risk_figures.update(
            {
                "mc_runs": mc.runs,
                "npv_mean": mc.npv_mean,
                "npv_p5": mc.npv_p5,
                "npv_p95": mc.npv_p95,
                "prob_npv_negative": mc.prob_npv_negative,
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

    return sections
