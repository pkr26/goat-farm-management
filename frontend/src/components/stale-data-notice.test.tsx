import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { StaleDataNotice } from "./stale-data-notice";

describe("StaleDataNotice", () => {
  it("announces retained stale data behind a working retry", () => {
    const onRetry = vi.fn();
    render(<StaleDataNotice onRetry={onRetry} />);

    const notice = screen.getByRole("status");
    expect(notice).toHaveTextContent(
      "Could not refresh — showing the last loaded data.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("accepts a context-specific message", () => {
    render(<StaleDataNotice message="Could not refresh the herd list." onRetry={() => {}} />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Could not refresh the herd list.",
    );
  });
});
