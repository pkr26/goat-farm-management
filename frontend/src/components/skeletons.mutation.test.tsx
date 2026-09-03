/**
 * Mutation-hardening for src/components/skeletons.tsx.
 *
 * The skeletons carry no logic, so the contract worth pinning is the rendered
 * shape: how many placeholder bars each region paints, their proportions per
 * row/column (the taper is deliberate — a uniform slab reads as one grey box),
 * the busy semantics for assistive tech, and the responsive grid class the
 * stylesheet keys on for the stat row. Every assertion here fails when a
 * default prop, class fragment, or Array.from length is mutated away.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  CardSkeleton,
  InlineLoading,
  PageSkeleton,
  StatSkeleton,
  TableSkeleton,
} from "@/components/skeletons";

function firstBusyRegion(container: HTMLElement): HTMLElement {
  const region = container.querySelector<HTMLElement>('[aria-busy="true"]');
  if (!region) throw new Error("no aria-busy region rendered");
  return region;
}

describe("StatSkeleton", () => {
  it("paints the card surface, the square mark, and two proportional text bars", () => {
    const { container } = render(<StatSkeleton />);

    const region = firstBusyRegion(container);
    expect(region).toHaveClass(
      "flex",
      "items-start",
      "gap-3.5",
      "rounded-xl",
      "bg-card",
      "p-5",
      "shadow-xs",
      "ring-1",
    );

    const bars = region.querySelectorAll('[data-slot="skeleton"]');
    expect(bars).toHaveLength(3);
    expect(bars[0]).toHaveClass("size-10", "rounded-xl");
    expect(bars[1]).toHaveClass("h-3.5", "w-2/3");
    expect(bars[2]).toHaveClass("h-7", "w-1/3");
  });

  it("keeps the card surface when the caller adds layout classes", () => {
    const { container } = render(<StatSkeleton className="col-span-2" />);

    expect(firstBusyRegion(container)).toHaveClass("col-span-2", "bg-card", "p-5");
  });
});

describe("TableSkeleton", () => {
  it("renders a header bar plus rows × columns of tapering cells", () => {
    const { container } = render(<TableSkeleton rows={3} columns={4} />);

    const region = firstBusyRegion(container);
    expect(region).toHaveClass(
      "space-y-3",
      "rounded-xl",
      "bg-card",
      "p-5",
      "shadow-xs",
      "ring-1",
    );

    const headerBar = region.querySelector('[data-slot="skeleton"]');
    expect(headerBar).toHaveClass("mb-4", "h-4", "w-1/4");

    const rows = region.querySelectorAll("div.flex.items-center.gap-4");
    expect(rows).toHaveLength(3);

    // Column 0 is the wide identity column; later columns taper by two points
    // each, floored at 8% — a mutation to the taper breaks these exact widths.
    const widths = Array.from(rows[0].querySelectorAll<HTMLElement>('[data-slot="skeleton"]')).map(
      (bar) => bar.style.width,
    );
    expect(widths).toEqual(["22%", "16%", "14%", "12%"]);
  });

  it("keeps tapering to the 8% floor on dense tables", () => {
    const { container } = render(<TableSkeleton rows={2} columns={6} />);

    const rows = container.querySelectorAll("div.flex.items-center.gap-4");
    expect(rows).toHaveLength(2);
    const widths = Array.from(rows[0].querySelectorAll<HTMLElement>('[data-slot="skeleton"]')).map(
      (bar) => bar.style.width,
    );
    expect(widths).toEqual(["22%", "16%", "14%", "12%", "10%", "8%"]);
  });

  it("uses the six-row, five-column default shape", () => {
    const { container } = render(<TableSkeleton />);

    const rows = container.querySelectorAll("div.flex.items-center.gap-4");
    expect(rows).toHaveLength(6);
    expect(rows[0].querySelectorAll('[data-slot="skeleton"]')).toHaveLength(5);
  });
});

describe("PageSkeleton", () => {
  it("stacks two stats into the two-column responsive grid", () => {
    const { container } = render(<PageSkeleton stats={2} cards={0} />);

    const grid = container.querySelector<HTMLElement>("div.grid");
    expect(grid).not.toBeNull();
    expect(grid).toHaveClass("grid", "gap-3", "grid-cols-1", "sm:grid-cols-2");
    expect(grid).not.toHaveClass("lg:grid-cols-4");
    expect(grid).not.toHaveClass("lg:grid-cols-3");
    // One StatSkeleton region per requested stat.
    expect(grid?.querySelectorAll('[aria-busy="true"]')).toHaveLength(2);
  });

  it("spreads four stats across the four-column grid", () => {
    const { container } = render(<PageSkeleton stats={4} cards={0} />);

    const grid = container.querySelector<HTMLElement>("div.grid");
    expect(grid).not.toBeNull();
    expect(grid).toHaveClass("grid-cols-2", "lg:grid-cols-4");
    expect(grid).not.toHaveClass("grid-cols-1");
    expect(grid).not.toHaveClass("lg:grid-cols-3");
    expect(grid?.querySelectorAll('[aria-busy="true"]')).toHaveLength(4);
  });

  it("gives three stats the dedicated three-column desktop row", () => {
    const { container } = render(<PageSkeleton stats={3} cards={0} />);

    const grid = container.querySelector<HTMLElement>("div.grid");
    expect(grid).not.toBeNull();
    expect(grid).toHaveClass(
      "grid",
      "gap-3",
      "grid-cols-1",
      "sm:grid-cols-2",
      "lg:grid-cols-3",
    );
    expect(grid).not.toHaveClass("lg:grid-cols-4");
    expect(grid?.querySelectorAll('[aria-busy="true"]')).toHaveLength(3);
  });

  it("renders two content cards with distinct densities plus caller children", () => {
    const { container } = render(
      <PageSkeleton stats={0}>
        <p>Below the fold</p>
      </PageSkeleton>,
    );

    // No stats requested → no grid row at all.
    expect(container.querySelector("div.grid")).toBeNull();

    const regions = container.querySelectorAll('[aria-busy="true"]');
    expect(regions).toHaveLength(2);
    // First card is dense (title + 2 lines), second mirrors a full list (5 lines).
    expect(regions[0].querySelectorAll('[data-slot="skeleton"]')).toHaveLength(3);
    expect(regions[1].querySelectorAll('[data-slot="skeleton"]')).toHaveLength(6);
    expect(screen.getByText("Below the fold")).toBeInTheDocument();
  });

  it("honours explicit card counts and line counts", () => {
    const { container } = render(<CardSkeleton lines={2} />);

    const region = firstBusyRegion(container);
    expect(region).toHaveClass("space-y-3", "bg-card");
    const bars = region.querySelectorAll<HTMLElement>('[data-slot="skeleton"]');
    expect(bars).toHaveLength(3);
    expect(bars[0]).toHaveClass("h-4", "w-1/3");
    expect(bars[1].style.width).toBe("88%");
    expect(bars[2].style.width).toBe("74%");
  });
});

describe("InlineLoading", () => {
  it("announces politely with the default copy and a decorative spinner", () => {
    const { container } = render(<InlineLoading />);

    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveTextContent("Loading…");
    expect(status).toHaveClass("flex", "items-center", "gap-2", "text-sm");
    const spinner = container.querySelector('[aria-hidden="true"]');
    expect(spinner).toHaveClass("size-3.5", "animate-spin", "rounded-full");
  });

  it("carries caller copy and classes", () => {
    render(<InlineLoading className="py-6">Fetching goats…</InlineLoading>);

    expect(screen.getByRole("status")).toHaveTextContent("Fetching goats…");
    expect(screen.getByRole("status")).toHaveClass("py-6");
  });
});
