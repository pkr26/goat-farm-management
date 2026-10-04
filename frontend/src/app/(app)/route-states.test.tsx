/**
 * The (app) route-state fallbacks: loading placeholder, error boundary (message +
 * reset), and the permission-aware 404 recovery link. Copy resolves from the stored
 * language without the provider: Telugu when herdly.language=te, English otherwise.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LANGUAGE_STORAGE_KEY } from "@/lib/i18n";

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

beforeEach(() => {
  window.localStorage.clear();
});

describe("(app) route states", () => {
  it("loading renders the shared Loading… placeholder", () => {
    render(<Loading />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Loading page" })).toBeInTheDocument();
  });

  it("loading renders in Telugu when the stored language is te", () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    render(<Loading />);
    expect(screen.getByText("లోడ్ అవుతోంది…")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "పేజీ లోడ్ అవుతోంది" })).toBeInTheDocument();
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

  it("error renders in Telugu when the stored language is te", () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    render(<AppError error={new Error("boom")} reset={() => {}} />);

    expect(screen.getByText("పేజీ లోడ్ చేయడంలో ఏదో తప్పు జరిగింది.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "మళ్ళీ ప్రయత్నించు" })).toBeInTheDocument();
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

  it("not-found renders in Telugu when the stored language is te", () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    permissionState.loading = false;
    permissionState.isError = false;
    permissionState.allowedPath = "health";
    render(<NotFound />);
    expect(screen.getByText("పేజీ కనబడలేదు")).toBeInTheDocument();
    expect(screen.getByText("మీరు వెతుకుతున్న పేజీ లేదు.")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "అందుబాటులో ఉన్న పేజీకి తిరిగి వెళ్ళండి" }),
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

  it.each([
    ["not-found", "Back to an available page"],
    ["no-access", "Choose another farm"],
  ] as const)("%s renders its recovery link as a secondary button", (page, name) => {
    permissionState.loading = false;
    permissionState.isError = false;
    permissionState.allowedPath = "health";

    render(page === "not-found" ? <NotFound /> : <NoAccessPage />);

    // These are dead ends, so the way out is a secondary action: the link
    // must carry the outline variant, not the filled primary call-to-action
    // styling that real page actions use.
    const link = screen.getByRole("link", { name });
    expect(link).toHaveClass("border-border", "bg-background");
    expect(link).not.toHaveClass("bg-primary");
  });

  it("no-access explains the recovery path and links to farm selection", () => {
    render(<NoAccessPage />);

    expect(screen.getByRole("heading", { name: "No farm modules assigned" })).toBeInTheDocument();
    // ITEM 5: the copy is catalog-sourced now; assert the en wording plus the
    // catalog parity (the i18n gate) rather than raw source text.
    expect(
      screen.getByText(/Ask the farm owner to grant access/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Choose another farm" })).toHaveAttribute(
      "href",
      "/farm-select",
    );
  });
});
