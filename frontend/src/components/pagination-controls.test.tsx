import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PaginationControls } from "./pagination-controls";

describe("PaginationControls", () => {
  it.each([0, -1])("renders nothing when total is %s", (total) => {
    const { container } = render(
      <PaginationControls
        total={total}
        limit={20}
        offset={0}
        onOffsetChange={vi.fn()}
      />,
    );

    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

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
    expect(screen.getByRole("navigation", { name: "transactions pagination" })).toBeInTheDocument();
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

  it("disables next on an exact final page boundary", () => {
    render(
      <PaginationControls
        total={60}
        limit={20}
        offset={40}
        onOffsetChange={vi.fn()}
      />,
    );

    expect(screen.getByText("Showing 41–60 of 60 records")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("moves back by exactly one limit without going below zero", async () => {
    const onOffsetChange = vi.fn();
    const user = userEvent.setup();
    render(
      <PaginationControls
        total={55}
        limit={20}
        offset={20}
        onOffsetChange={onOffsetChange}
      />,
    );

    expect(screen.getByText("Showing 21–40 of 55 records")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Previous" }));
    expect(onOffsetChange).toHaveBeenCalledOnce();
    expect(onOffsetChange).toHaveBeenCalledWith(0);
  });

  it("never inverts the range when the offset outlives a shrunken list", async () => {
    // The parent kept offset=90 while the list shrank to 5 rows (deletion,
    // filter, or a stale offset carried across records). Previously this
    // rendered "Showing 91–5 of 5 records" — an impossible, inverted range.
    const onOffsetChange = vi.fn();
    const user = userEvent.setup();
    render(
      <PaginationControls
        total={5}
        limit={10}
        offset={90}
        onOffsetChange={onOffsetChange}
      />,
    );

    expect(screen.getByText("Showing 5–5 of 5 records")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    // Previous stays live so the user can page back to real rows.
    const previous = screen.getByRole("button", { name: "Previous" });
    expect(previous).toBeEnabled();
    await user.click(previous);
    expect(onOffsetChange).toHaveBeenCalledWith(80);
  });

  it("blocks both directions while its rows are placeholder data", async () => {
    const onOffsetChange = vi.fn();
    const user = userEvent.setup();
    render(
      <PaginationControls
        total={55}
        limit={20}
        offset={20}
        disabled
        onOffsetChange={onOffsetChange}
      />,
    );

    expect(screen.getByRole("navigation")).toHaveAttribute("aria-busy", "true");
    const previous = screen.getByRole("button", { name: "Previous" });
    const next = screen.getByRole("button", { name: "Next" });
    expect(previous).toBeDisabled();
    expect(next).toBeDisabled();
    await user.click(previous);
    await user.click(next);
    expect(onOffsetChange).not.toHaveBeenCalled();
  });
});
