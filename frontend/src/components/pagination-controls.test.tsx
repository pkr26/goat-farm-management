import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PaginationControls } from "./pagination-controls";

describe("PaginationControls", () => {
  it("reports the exact range and advances by the API limit", async () => {
    const onOffsetChange = vi.fn();
    const user = userEvent.setup();
    render(
      <PaginationControls
        total={55}
        limit={20}
        offset={0}
        label="transactions"
        onOffsetChange={onOffsetChange}
      />,
    );

    expect(screen.getByText("Showing 1–20 of 55 transactions")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(onOffsetChange).toHaveBeenCalledWith(20);
  });

  it("disables next on the final partial page", () => {
    render(
      <PaginationControls
        total={55}
        limit={20}
        offset={40}
        onOffsetChange={vi.fn()}
      />,
    );

    expect(screen.getByText("Showing 41–55 of 55 records")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("never inverts the range when the offset outlives a shrunken list", () => {
    // The parent kept offset=90 while the list shrank to 5 rows (deletion,
    // filter, or a stale offset carried across records). Previously this
    // rendered "Showing 91–5 of 5 records" — an impossible, inverted range.
    render(
      <PaginationControls
        total={5}
        limit={10}
        offset={90}
        onOffsetChange={vi.fn()}
      />,
    );

    expect(screen.getByText("Showing 5–5 of 5 records")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    // Previous stays live so the user can page back to real rows.
    expect(screen.getByRole("button", { name: "Previous" })).toBeEnabled();
  });
});
