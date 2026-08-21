/** The (app) route-state fallbacks: loading placeholder, error boundary
 *  (message + reset), and the permission-aware 404 recovery link. */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import AppError from "./error";
import Loading from "./loading";
import NoAccessPage from "./no-access/page";
import NotFound from "./not-found";

const { permissionState } = vi.hoisted(() => ({
  permissionState: {
    loading: false,
    isError: false,
    allowedPath: "health",
  },
}));

vi.mock("@/lib/use-permissions", () => ({
  usePermissions: () => ({
    loading: permissionState.loading,
    isError: permissionState.isError,
    can: (permission: string) => permission === `${permissionState.allowedPath}.view`,
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
    const firstError = new Error("boom");

    const view = render(<AppError error={firstError} reset={reset} />);

    expect(
      screen.getByText("Something went wrong loading this page."),
    ).toBeInTheDocument();
    expect(consoleError).toHaveBeenCalledTimes(1);
    expect(consoleError).toHaveBeenLastCalledWith(firstError);
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledTimes(1);

    const nextError = new Error("different failure");
    view.rerender(<AppError error={nextError} reset={reset} />);
    expect(consoleError).toHaveBeenCalledTimes(2);
    expect(consoleError).toHaveBeenLastCalledWith(nextError);
    consoleError.mockRestore();
  });

  it("not-found links to the first module allowed by the current role", () => {
    permissionState.loading = false;
    permissionState.isError = false;
    permissionState.allowedPath = "health";
    render(<NotFound />);
    expect(screen.getByText("Page not found")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Back to an available page" }),
    ).toHaveAttribute("href", "/health");
  });

  it.each([
    ["permissions are loading", true, false],
    ["permission loading failed", false, true],
  ])("not-found returns to farm selection when %s", (_label, loading, isError) => {
    permissionState.loading = loading;
    permissionState.isError = isError;

    render(<NotFound />);

    expect(
      screen.getByRole("link", { name: "Back to an available page" }),
    ).toHaveAttribute("href", "/farm-select");
  });

  it("no-access explains the recovery path and links to farm selection", () => {
    render(<NoAccessPage />);

    expect(screen.getByRole("heading", { name: "No farm modules assigned" })).toBeInTheDocument();
    expect(screen.getByText(/ask the farm owner to update your role/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Choose another farm" })).toHaveAttribute(
      "href",
      "/farm-select",
    );
  });
});
