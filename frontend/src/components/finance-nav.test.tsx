/**
 * src/components/finance-nav.tsx: the tab list's labels, hrefs, and the
 * aria-current / visual treatment that mark exactly the active tab (the
 * feeding nav's contract, mirrored for the finance pages).
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FinanceNav } from "@/components/finance-nav";

describe("FinanceNav", () => {
  it("marks exactly the active tab as the current page", () => {
    render(<FinanceNav active="insurance" />);

    const nav = screen.getByRole("navigation", { name: "Finance sections" });
    const links = within(nav).getAllByRole("link");
    expect(links).toHaveLength(2);

    const ledger = within(nav).getByRole("link", { name: "Ledger" });
    expect(ledger).toHaveAttribute("href", "/finance");
    expect(ledger).not.toHaveAttribute("aria-current");
    expect(ledger).toHaveClass("border-b-2", "border-transparent", "text-muted-foreground");

    const insurance = within(nav).getByRole("link", { name: "Insurance" });
    expect(insurance).toHaveAttribute("href", "/finance/insurance");
    expect(insurance).toHaveAttribute("aria-current", "page");
    expect(insurance).toHaveClass("border-primary", "font-medium", "text-foreground");
  });

  it("flags the ledger tab when it is the active one", () => {
    render(<FinanceNav active="ledger" />);

    const nav = screen.getByRole("navigation", { name: "Finance sections" });
    expect(within(nav).getByRole("link", { name: "Ledger" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).getByRole("link", { name: "Insurance" })).not.toHaveAttribute(
      "aria-current",
    );
  });
});
