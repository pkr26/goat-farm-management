/** The (app) route-state fallbacks: loading placeholder, error boundary
 *  (message + reset), and the permission-aware 404 recovery link. */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import AppError from "./error";
import Loading from "./loading";
import NotFound from "./not-found";

vi.mock("@/lib/use-permissions", () => ({
  usePermissions: () => ({
    loading: false,
    isError: false,
    can: (permission: string) => permission === "health.view",
  }),
}));

describe("(app) route states", () => {
  it("loading renders the shared Loading… placeholder", () => {
    render(<Loading />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("error renders a message and retries via reset", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const reset = vi.fn();
    const user = userEvent.setup();

    render(<AppError error={new Error("boom")} reset={reset} />);

    expect(
      screen.getByText("Something went wrong loading this page."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledTimes(1);
    consoleError.mockRestore();
  });

  it("not-found links to the first module allowed by the current role", () => {
    render(<NotFound />);
    expect(screen.getByText("Page not found")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Back to an available page" }),
    ).toHaveAttribute("href", "/health");
  });
});
