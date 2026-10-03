import { useState } from "react";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { FinanceAssumptions, SimulationAssumptions, ViabilityMetrics } from "@/api/generated/models";
import { renderWithProviders } from "@/test/render";
import { assumptionsIdentity, assessedIrr } from "@/lib/simulation-result-basis";
import { NlmFundingEditor, validNlmFunding } from "./nlm-funding-editor";
import { parseFestivalDates } from "./festival-date-editor";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/simulation",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

describe("decision evidence boundary", () => {
  it("keeps cash schedules absent until approval and explicit months are entered", async () => {
    let latest: FinanceAssumptions = { nlm_subsidy: true };
    function Editor() {
      const [value, setValue] = useState(latest);
      latest = value;
      return <NlmFundingEditor value={value} onValidityChange={() => undefined}
        onChange={(patch) => setValue((previous) => ({ ...previous, ...patch }))} />;
    }
    renderWithProviders(<Editor />);
    expect(latest.nlm_approved_subsidy_amount).toBeUndefined();
    expect(latest.nlm_subsidy_receipts).toBeUndefined();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Declared breeding females in eligible unit"), "100");
    await user.type(screen.getByLabelText("Declared breeding males in eligible unit"), "5");
    await user.type(screen.getByLabelText("Eligible capital budget (₹)"), "3000000");
    expect(latest.nlm_subsidy_receipts).toBeUndefined();
    await user.type(screen.getByLabelText("Documented approved subsidy (₹)"), "1000000");
    expect(latest.nlm_subsidy_receipts).toEqual([]);
    await user.type(screen.getByLabelText("Approved installment 1: receipt month"), "3");
    await user.type(screen.getByLabelText("Approved installment 2: receipt month"), "9");
    expect(latest.nlm_subsidy_receipts).toEqual([{ month: 3, amount: 500000 }, { month: 9, amount: 500000 }]);
    expect(validNlmFunding(latest)).toBe(true);
    // Changing the documented approval invalidates prior timing, visibly.
    await user.clear(screen.getByLabelText("Documented approved subsidy (₹)"));
    expect(latest.nlm_subsidy_receipts).toEqual([]);
    expect(screen.getByLabelText("Approved installment 1: receipt month")).toHaveValue(null);
  });

  it("rejects unsupported approval, month zero, overpayment and reversed milestones", () => {
    const base: FinanceAssumptions = { nlm_subsidy: true, nlm_unit_females: 100,
      nlm_unit_males: 5, nlm_eligible_capital_cost: 3000000, nlm_approved_subsidy_amount: 1000000 };
    expect(validNlmFunding({ ...base, nlm_approved_subsidy_amount: 1000001 })).toBe(false);
    expect(validNlmFunding({ ...base, nlm_unit_females: 50 })).toBe(false);
    expect(validNlmFunding({ ...base, nlm_subsidy_receipts: [{ month: 0, amount: 500000 }] })).toBe(false);
    expect(validNlmFunding({ ...base, nlm_subsidy_receipts: [{ month: 1, amount: 1000000 }] })).toBe(false);
    expect(validNlmFunding({ ...base, nlm_subsidy_receipts: [{ month: 9, amount: 500000 }, { month: 3, amount: 500000 }] })).toBe(false);
  });

  it("withholds ambiguous or unproven IRRs even if a server supplied a number", () => {
    for (const irr_status of ["multiple_roots", "indeterminate", "no_root", "not_assessed"])
      expect(assessedIrr({ irr: 0.25, irr_status } as ViabilityMetrics)).toBeNull();
    expect(assessedIrr({ irr: 0.25, irr_status: "unique" } as ViabilityMetrics)).toBe(0.25);
  });

  it("compares executed calibration payloads regardless of object key order", () => {
    const a = { herd: { bucks: 5, does: 100 }, meta: { horizon_months: 12 }, events: [] } as SimulationAssumptions;
    const b = { meta: { horizon_months: 12 }, herd: { does: 100, bucks: 5 } } as SimulationAssumptions;
    expect(assumptionsIdentity(a)).toBe(assumptionsIdentity(b));
    expect(assumptionsIdentity(a)).not.toBe(assumptionsIdentity({ ...b, herd: { ...b.herd, does: 101 } }));
  });

  it("retains both 2039 occurrences and rejects impossible or duplicated dates", () => {
    expect(parseFestivalDates("2039-12-26, 2039-01-05")).toEqual(["2039-01-05", "2039-12-26"]);
    expect(parseFestivalDates("2044-02-30")).toBe(false);
    expect(parseFestivalDates("2039-01-05,2039-01-05")).toBe(false);
    expect(parseFestivalDates("")).toBeNull();
  });
});
