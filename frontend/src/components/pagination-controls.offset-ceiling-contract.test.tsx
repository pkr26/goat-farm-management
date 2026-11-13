import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { PaginationControls } from "./pagination-controls";

it("shows the real total at an inclusive endpoint ceiling while keeping Previous usable", () => {
  const change = vi.fn();
  render(<PaginationControls total={10_051} limit={50} offset={10_000} maxOffset={10_000} onOffsetChange={change} />);
  expect(screen.getByText("Showing 10001–10050 of 10051 records")).toBeInTheDocument();
  const next = screen.getByRole("button", { name: "Next" });
  expect(next).toBeDisabled();
  fireEvent.click(next);
  expect(change).not.toHaveBeenCalled();
  const previous = screen.getByRole("button", { name: "Previous" });
  expect(previous).toBeEnabled();
  fireEvent.click(previous);
  expect(change).toHaveBeenCalledExactlyOnceWith(9_950);
});

it("lands an irregular shared-link page on the inclusive ceiling instead of overshooting it", () => {
  const change = vi.fn();
  render(<PaginationControls total={10_051} limit={50} offset={9_990} maxOffset={10_000} onOffsetChange={change} />);
  const next = screen.getByRole("button", { name: "Next" });
  expect(next).toBeEnabled();
  fireEvent.click(next);
  expect(change).toHaveBeenCalledExactlyOnceWith(10_000);
});

it("leaves an endpoint without a ceiling free to page beyond ten thousand", () => {
  const change = vi.fn();
  render(<PaginationControls total={10_051} limit={50} offset={10_000} onOffsetChange={change} />);
  const next = screen.getByRole("button", { name: "Next" });
  expect(next).toBeEnabled();
  fireEvent.click(next);
  expect(change).toHaveBeenCalledExactlyOnceWith(10_050);
});
