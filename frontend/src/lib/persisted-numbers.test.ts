import { beforeEach, describe, expect, it, vi } from "vitest";

import { setActiveLanguage } from "@/lib/active-language";

import {
  MIN_PERSISTED_KG,
  MIN_PERSISTED_KG_MESSAGE,
  MIN_PERSISTED_MONEY,
  MIN_PERSISTED_MONEY_MESSAGE,
  formatPersistedKg,
  isPersistableNonnegativeMoney,
  isPersistableNonnegativeWeight,
} from "./persisted-numbers";

// Recording wrapper around the shared formatter: node's ICU renders te-IN and
// en-IN grouping identically, so output comparisons cannot distinguish a
// delegating call from the hardcoded en-IN this audit removed. The call
// itself is the contract (2026-10-01 audit, 07-L4).
const formatNumberCalls: Array<{
  value: number;
  opts: Intl.NumberFormatOptions | undefined;
}> = [];
vi.mock("@/lib/format", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./format")>();
  return {
    ...actual,
    formatNumber: (value: number, opts?: Intl.NumberFormatOptions) => {
      formatNumberCalls.push({ value, opts });
      return actual.formatNumber(value, opts);
    },
  };
});

describe("persisted number boundaries", () => {
  beforeEach(() => {
    formatNumberCalls.length = 0;
  });
  it("pins the backend normalization thresholds and their validation copy", () => {
    expect(MIN_PERSISTED_KG).toBe(0.0005);
    expect(MIN_PERSISTED_MONEY).toBe(0.005);
    expect(MIN_PERSISTED_KG_MESSAGE).toBe("Quantity must be at least 0.0005 kg");
    expect(MIN_PERSISTED_MONEY_MESSAGE).toBe("Amount must be ₹0 or at least ₹0.005");
  });

  it("preserves significant stored kilograms without hiding ordinary precision", () => {
    expect(formatPersistedKg(0)).toBe("0.0");
    expect(formatPersistedKg(1)).toBe("1.0");
    expect(formatPersistedKg(0.0005)).toBe("0.001");
    expect(formatPersistedKg(1234.567)).toBe("1,234.567");
  });

  it("groups digits through the shared language-aware formatNumber (2026-10-01 audit, 07-L4)", () => {
    // The stock ledger used to hardcode en-IN grouping while the rest of the
    // app already grouped through formatNumber (te-IN under Telugu). Node's
    // ICU renders te-IN and en-IN identically, so pin the DELEGATION — the
    // call must go through the shared formatter, which a hardcoded
    // toLocaleString("en-IN") copy would not do.
    setActiveLanguage("te");
    try {
      expect(formatPersistedKg(1234.567)).toBe("1,234.567");
      expect(formatPersistedKg(1234567.891)).toBe("12,34,567.891");
    } finally {
      setActiveLanguage("en");
    }
    expect(formatNumberCalls).toEqual([
      { value: 1234.567, opts: { minimumFractionDigits: 1, maximumFractionDigits: 3 } },
      { value: 1234567.891, opts: { minimumFractionDigits: 1, maximumFractionDigits: 3 } },
    ]);
    // The English default keeps its byte-identical en-IN rendering.
    expect(formatPersistedKg(1234.567)).toBe("1,234.567");
    expect(formatNumberCalls).toHaveLength(3);
  });

  it("uses the shared missing-value marker for impossible non-finite quantities", () => {
    expect(formatPersistedKg(Number.NaN)).toBe("—");
    expect(formatPersistedKg(Number.POSITIVE_INFINITY)).toBe("—");
    expect(formatPersistedKg(Number.NEGATIVE_INFINITY)).toBe("—");
  });

  it("accepts exactly zero or values at and above the money threshold", () => {
    expect(isPersistableNonnegativeMoney(0)).toBe(true);
    expect(isPersistableNonnegativeMoney(-0)).toBe(true);
    expect(isPersistableNonnegativeMoney(MIN_PERSISTED_MONEY)).toBe(true);
    expect(isPersistableNonnegativeMoney(25)).toBe(true);
  });

  it("rejects negative, sub-threshold, NaN and infinite amounts", () => {
    expect(isPersistableNonnegativeMoney(-1)).toBe(false);
    expect(isPersistableNonnegativeMoney(MIN_PERSISTED_MONEY - 0.0001)).toBe(false);
    expect(isPersistableNonnegativeMoney(Number.NaN)).toBe(false);
    expect(isPersistableNonnegativeMoney(Number.POSITIVE_INFINITY)).toBe(false);
    expect(isPersistableNonnegativeMoney(Number.NEGATIVE_INFINITY)).toBe(false);
  });

  it("accepts exactly zero or weights at and above the kg threshold", () => {
    expect(isPersistableNonnegativeWeight(0)).toBe(true);
    expect(isPersistableNonnegativeWeight(-0)).toBe(true);
    expect(isPersistableNonnegativeWeight(MIN_PERSISTED_KG)).toBe(true);
    expect(isPersistableNonnegativeWeight(42.5)).toBe(true);
  });

  it("rejects negative, sub-threshold, NaN and infinite weights", () => {
    expect(isPersistableNonnegativeWeight(-1)).toBe(false);
    expect(isPersistableNonnegativeWeight(MIN_PERSISTED_KG - 0.0001)).toBe(false);
    expect(isPersistableNonnegativeWeight(Number.NaN)).toBe(false);
    expect(isPersistableNonnegativeWeight(Number.POSITIVE_INFINITY)).toBe(false);
    expect(isPersistableNonnegativeWeight(Number.NEGATIVE_INFINITY)).toBe(false);
  });
});
