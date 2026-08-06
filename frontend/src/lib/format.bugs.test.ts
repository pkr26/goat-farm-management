// SUSPECTED APP BUG — reported to orchestrator
//
// formatDate's documented contract is "ISO date string (YYYY-MM-DD) →
// 'd Mon yyyy'; empty/invalid → '—'". But it only checks that the three
// numeric parts are non-zero — it never validates month ≤ 12 or the day
// against the calendar. Out-of-range dates therefore render as nonsense
// ("1 undefined 2026" for month 13, "30 Feb 2024" for a non-existent day)
// instead of the em dash. These tests assert the contract-correct output
// and FAIL against the current source; the source must not be edited from
// here.

import { describe, expect, it } from "vitest";

import { formatDate } from "./format";

describe("formatDate — out-of-range calendar values (suspected bug)", () => {
  it("renders an em dash for month 13 instead of 'undefined'", () => {
    // Actual today: "1 undefined 2026".
    expect(formatDate("2026-13-01")).toBe("—");
  });

  it("renders an em dash for 30 Feb on a leap year", () => {
    // Actual today: "30 Feb 2024".
    expect(formatDate("2024-02-30")).toBe("—");
  });

  it("renders an em dash for 29 Feb on a non-leap year", () => {
    // Actual today: "29 Feb 2026".
    expect(formatDate("2026-02-29")).toBe("—");
  });

  it("renders an em dash for day 31 in a 30-day month", () => {
    // Actual today: "31 Apr 2026".
    expect(formatDate("2026-04-31")).toBe("—");
  });

  it("renders an em dash for day 32", () => {
    // Actual today: "32 Aug 2026".
    expect(formatDate("2026-08-32")).toBe("—");
  });
});
