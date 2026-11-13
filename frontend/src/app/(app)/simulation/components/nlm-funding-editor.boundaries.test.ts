import { describe, expect, it } from "vitest";

import type { FinanceAssumptions } from "@/api/generated/models";

import { validNlmFunding } from "./nlm-funding-editor";

const approvedUnit: FinanceAssumptions = {
  nlm_subsidy: true,
  nlm_unit_females: 100,
  nlm_unit_males: 5,
  nlm_eligible_capital_cost: 3_000_000,
  nlm_approved_subsidy_amount: 1_000_000,
};

function fundingValid(...args: Parameters<typeof validNlmFunding>) {
  let verdict: boolean | undefined;
  // Unsupported evidence must produce a validation result, including the
  // first receipt where there is no previous installment to inspect.
  expect(() => { verdict = validNlmFunding(...args); }).not.toThrow();
  return verdict;
}

describe("documented NLM funding evidence", () => {
  it.each([1, 2, 3, 4, 5])("accepts an eligible %s-hundred-female unit at its approval ceiling", (band) => {
    expect(fundingValid({ ...approvedUnit,
      nlm_unit_females: band * 100, nlm_unit_males: band * 5,
      nlm_eligible_capital_cost: 12_000_000,
      nlm_approved_subsidy_amount: band * 1_000_000,
    })).toBe(true);
  });

  it.each([0, 99, 150, 600])("rejects an unsupported breeding-female count (%s)", (females) => {
    expect(fundingValid({ ...approvedUnit, nlm_unit_females: females })).toBe(false);
  });

  it.each([0, 4, 6])("rejects a male count that does not match the declared unit (%s)", (males) => {
    expect(fundingValid({ ...approvedUnit, nlm_unit_males: males })).toBe(false);
  });

  it("requires paired declarations and uses the supplied herd only when both are absent", () => {
    expect(fundingValid({ ...approvedUnit, nlm_unit_males: null })).toBe(false);
    expect(fundingValid({ ...approvedUnit, nlm_unit_females: null })).toBe(false);
    const inferred = { ...approvedUnit, nlm_unit_females: null, nlm_unit_males: null };
    expect(fundingValid(inferred, 100, 5)).toBe(true);
    expect(fundingValid(inferred, 200, 10)).toBe(true);
    expect(fundingValid(inferred)).toBe(false);
  });

  it("keeps explicit zero declarations separate from the inferred herd", () => {
    expect(fundingValid({ ...approvedUnit, nlm_unit_females: 0 }, 100, 5)).toBe(false);
    expect(fundingValid({ ...approvedUnit, nlm_unit_males: 0 }, 100, 5)).toBe(false);
  });

  it.each([[120, 6], [600, 30], [0, 0]])("rejects an unsupported %s-female unit even when its male count matches the ratio", (females, males) => {
    expect(fundingValid({ ...approvedUnit, nlm_unit_females: females,
      nlm_unit_males: males, nlm_approved_subsidy_amount: 0.001,
    })).toBe(false);
  });

  it("requires the subsidy switch and a documented eligible budget for an approval", () => {
    expect(fundingValid({ ...approvedUnit, nlm_subsidy: false })).toBe(false);
    expect(fundingValid({ ...approvedUnit, nlm_eligible_capital_cost: null })).toBe(false);
    expect(fundingValid({ nlm_subsidy: false })).toBe(true);
    expect(fundingValid({ nlm_subsidy: true, nlm_eligible_capital_cost: 0,
      nlm_approved_subsidy_amount: 0 })).toBe(true);
  });

  it("accepts the exact budget cap and rejects any approval above it", () => {
    const budgetLimited = { ...approvedUnit, nlm_unit_females: 200, nlm_unit_males: 10 };
    expect(fundingValid({ ...budgetLimited, nlm_approved_subsidy_amount: 1_500_000 })).toBe(true);
    expect(fundingValid({ ...budgetLimited, nlm_approved_subsidy_amount: 1_500_000.005 })).toBe(false);
    expect(fundingValid({ ...budgetLimited, nlm_approved_subsidy_amount: 1_500_000.006 })).toBe(false);
  });

  it("accepts the exact qualifying-unit cap and rejects any approval above it", () => {
    expect(fundingValid({ ...approvedUnit, nlm_approved_subsidy_amount: 1_000_000 })).toBe(true);
    expect(fundingValid({ ...approvedUnit, nlm_approved_subsidy_amount: 1_000_000.005 })).toBe(false);
    expect(fundingValid({ ...approvedUnit, nlm_approved_subsidy_amount: 1_000_000.006 })).toBe(false);
  });

  it.each([1, 240])("accepts a receipt at month %s and an exactly half-sized installment", (month) => {
    expect(fundingValid({ ...approvedUnit,
      nlm_subsidy_receipts: [{ month, amount: 500_000 }],
    })).toBe(true);
  });

  it.each([0, 241, 1.5, Number.NaN])("rejects an invalid receipt month (%s)", (month) => {
    expect(fundingValid({ ...approvedUnit,
      nlm_subsidy_receipts: [{ month, amount: 500_000 }],
    })).toBe(false);
  });

  it("requires documented positive approval for every receipt", () => {
    const receipts = [{ month: 1, amount: 500_000 }];
    expect(fundingValid({ nlm_subsidy_receipts: receipts })).toBe(false);
    expect(fundingValid({ ...approvedUnit, nlm_approved_subsidy_amount: 0,
      nlm_subsidy_receipts: receipts })).toBe(false);
    expect(fundingValid({ ...approvedUnit, nlm_approved_subsidy_amount: 0,
      nlm_subsidy_receipts: [{ month: 1, amount: 0 }] })).toBe(false);
  });

  it("allows two same-month installments and rejects a third or descending receipt schedule", () => {
    const receipt = { month: 2, amount: 500_000 };
    expect(fundingValid({ ...approvedUnit, nlm_subsidy_receipts: [receipt, receipt] })).toBe(true);
    expect(fundingValid({ ...approvedUnit, nlm_subsidy_receipts: [receipt, { ...receipt, month: 3 }] })).toBe(true);
    expect(fundingValid({ ...approvedUnit, nlm_subsidy_receipts: [receipt, receipt, receipt] })).toBe(false);
    expect(fundingValid({ ...approvedUnit, nlm_subsidy_receipts: [receipt, { ...receipt, month: 1 }] })).toBe(false);
  });

  it("permits the receipt rounding tolerance inclusively and rejects a larger discrepancy", () => {
    const smallApproval = { ...approvedUnit, nlm_approved_subsidy_amount: 0.01 };
    expect(fundingValid({ ...smallApproval, nlm_subsidy_receipts: [{ month: 1, amount: 0.01 }] })).toBe(true);
    expect(fundingValid({ ...smallApproval, nlm_subsidy_receipts: [{ month: 1, amount: 0.0101 }] })).toBe(false);
  });

  it("keeps the aggregate receipt amount within the award plus its rounding tolerance", () => {
    const smallApproval = { ...approvedUnit, nlm_approved_subsidy_amount: 0.01 };
    expect(fundingValid({ ...smallApproval, nlm_subsidy_receipts: [
      { month: 1, amount: 0.0075 }, { month: 2, amount: 0.0075 },
    ] })).toBe(true);
    expect(fundingValid({ ...smallApproval, nlm_subsidy_receipts: [
      { month: 1, amount: 0.01 }, { month: 2, amount: 0.01 },
    ] })).toBe(false);
  });

  it.each([0, -0.001])("rejects a nonpositive installment (%s) even within the half-amount tolerance", (amount) => {
    expect(fundingValid({ ...approvedUnit, nlm_approved_subsidy_amount: 0.01,
      nlm_subsidy_receipts: [{ month: 1, amount }],
    })).toBe(false);
  });
});
