/**
 * Mutation-hardening for src/components/data-table-card.tsx: a numeric-zero
 * description is content, not a falsy absence, and the busy marker is
 * forwarded only when asked for.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DataTableCard } from "@/components/data-table-card";

describe("DataTableCard", () => {
  it("renders a numeric-zero description as real content", () => {
    render(
      <DataTableCard description={0}>
        <p>Row data</p>
      </DataTableCard>,
    );

    const description = screen.getByText("0");
    expect(description).toHaveAttribute("data-slot", "card-description");
    expect(description).toHaveClass("text-muted-foreground");
    expect(screen.getByText("Row data").closest('[data-slot="card-content"]')).not.toBeNull();
  });

  it("forwards the busy marker and anchor attributes to the card", () => {
    const { rerender } = render(
      <DataTableCard id="animals-table" tabIndex={-1} ariaBusy>
        <p>Rows</p>
      </DataTableCard>,
    );

    const card = screen.getByText("Rows").closest('[data-slot="card"]');
    expect(card).toHaveAttribute("id", "animals-table");
    expect(card).toHaveAttribute("tabindex", "-1");
    expect(card).toHaveAttribute("aria-busy", "true");

    rerender(
      <DataTableCard id="animals-table" tabIndex={-1}>
        <p>Rows</p>
      </DataTableCard>,
    );
    expect(screen.getByText("Rows").closest('[data-slot="card"]')).not.toHaveAttribute(
      "aria-busy",
    );
  });
});
