/**
 * src/components/section-nav.tsx: the shared section nav (2026-09-28 audit,
 * I5 — formerly the line-identical FeedingNav/FinanceNav twins). Covers the
 * tab lists' catalog labels, hrefs, the aria-current / visual treatment that
 * mark exactly the active tab, and the Telugu render of labels + landmark.
 */

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import {
  FEEDING_TABS,
  FINANCE_TABS,
  SectionNav,
} from "@/components/section-nav";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "@/lib/i18n";

beforeEach(() => {
  window.localStorage.clear();
});

describe("SectionNav — feeding", () => {
  it("marks exactly the active tab as the current page", () => {
    render(<SectionNav tabs={FEEDING_TABS} active="recipes" ariaLabelKey="feeding.nav.aria" />);

    const nav = screen.getByRole("navigation", { name: "Feeding sections" });
    const links = within(nav).getAllByRole("link");
    expect(links).toHaveLength(3);

    const plan = within(nav).getByRole("link", { name: "Today's plan" });
    expect(plan).toHaveAttribute("href", "/feeding");
    expect(plan).not.toHaveAttribute("aria-current");
    expect(plan).toHaveClass(
      "border-b-2",
      "border-transparent",
      "text-muted-foreground",
    );

    const recipes = within(nav).getByRole("link", { name: "Recipes" });
    expect(recipes).toHaveAttribute("href", "/feeding/recipes");
    expect(recipes).toHaveAttribute("aria-current", "page");
    expect(recipes).toHaveClass("border-primary", "font-medium", "text-foreground");

    const inventory = within(nav).getByRole("link", { name: "Inventory" });
    expect(inventory).toHaveAttribute("href", "/feeding/inventory");
    expect(inventory).not.toHaveAttribute("aria-current");
    expect(inventory).not.toHaveClass("border-primary");
  });

  it.each([
    { active: "plan" as const, label: "Today's plan" },
    { active: "inventory" as const, label: "Inventory" },
  ])("flags only the $active tab", ({ active, label }) => {
    const { unmount } = render(
      <SectionNav tabs={FEEDING_TABS} active={active} ariaLabelKey="feeding.nav.aria" />,
    );
    const nav = screen.getByRole("navigation", { name: "Feeding sections" });

    for (const link of within(nav).getAllByRole("link")) {
      const isCurrent = link.textContent === label;
      if (isCurrent) {
        expect(link).toHaveAttribute("aria-current", "page");
        expect(link).toHaveClass("border-primary");
      } else {
        expect(link).not.toHaveAttribute("aria-current");
        expect(link).toHaveClass("border-transparent");
      }
    }
    unmount();
  });
});

describe("SectionNav — finance", () => {
  it("marks exactly the active tab as the current page", () => {
    render(<SectionNav tabs={FINANCE_TABS} active="insurance" ariaLabelKey="finance.nav.aria" />);

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
    render(<SectionNav tabs={FINANCE_TABS} active="ledger" ariaLabelKey="finance.nav.aria" />);

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

describe("SectionNav — localization (2026-09-28 audit, I5)", () => {
  it("renders the feeding tabs in Telugu when the stored language is te", async () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    render(
      <LanguageProvider>
        <SectionNav tabs={FEEDING_TABS} active="plan" ariaLabelKey="feeding.nav.aria" />
      </LanguageProvider>,
    );

    const nav = await screen.findByRole("navigation", { name: "మేత విభాగాలు" });
    for (const label of ["నేటి ప్రణాళిక", "రెసిపీలు", "నిల్వ"]) {
      expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    }
  });

  it("renders the finance tabs in Telugu when the stored language is te", async () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    render(
      <LanguageProvider>
        <SectionNav tabs={FINANCE_TABS} active="ledger" ariaLabelKey="finance.nav.aria" />
      </LanguageProvider>,
    );

    const nav = await screen.findByRole("navigation", { name: "ఆర్థిక విభాగాలు" });
    for (const label of ["లెడ్జర్", "భీమా"]) {
      expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    }
  });
});
