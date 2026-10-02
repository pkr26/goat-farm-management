import { describe, expect, it } from "vitest";

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

describe("persisted number boundaries", () => {
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

  it("groups digits in the active UI language, like every sibling surface (2026-10-01 audit, 07-L4)", () => {
    // The stock ledger used to hardcode en-IN grouping while the rest of the
    // app already grouped through formatNumber (te-IN under Telugu). Pin the
    // delegation itself, the same way format.locale.test.ts does.
    setActiveLanguage("te");
    try {
      expect(formatPersistedKg(1234.567)).toBe(
        (1234.567).toLocaleString("te-IN", {
          minimumFractionDigits: 1,
          maximumFractionDigits: 3,
        }),
      );
      expect(formatPersistedKg(1234567.891)).toBe(
        (1234567.891).toLocaleString("te-IN", {
          minimumFractionDigits: 1,
          maximumFractionDigits: 3,
        }),
      );
    } finally {
      setActiveLanguage("en");
    }
    // The English default keeps its byte-identical en-IN rendering.
    expect(formatPersistedKg(1234.567)).toBe("1,234.567");
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
