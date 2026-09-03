/**
 * Mutation-hardening for src/components/feeding-nav.tsx: the tab list's
 * labels, hrefs, and — the whole point of the component — the aria-current
 * and visual treatment that mark exactly the active tab.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FeedingNav } from "@/components/feeding-nav";

describe("FeedingNav", () => {
  it("marks exactly the active tab as the current page", () => {
    render(<FeedingNav active="recipes" />);

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
    const { unmount } = render(<FeedingNav active={active} />);
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
