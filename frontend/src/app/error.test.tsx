/**
 * Public-shell route error boundary (login/register/farm-select/root hub):
 * logs once, explains, and offers the retry that re-runs the failed render.
 * The file sat at 0% coverage (2026-09-28 audit, T2); the (app) group's own
 * boundary is tested the same way in global-error.test.tsx's mould.
 * Copy resolves from the stored language without the provider (2026-09-28
 * audit, I3) — Telugu when herdly.language=te, English otherwise.
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LANGUAGE_STORAGE_KEY } from "@/lib/i18n";

import RootError from "./error";

beforeEach(() => {
  window.localStorage.clear();
});

describe("RootError", () => {
  it("logs the error once and offers the retry that re-runs the render", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const reset = vi.fn();
    const error = new Error("chunk load failed");

    render(<RootError error={error} reset={reset} />);

    expect(consoleError).toHaveBeenCalledWith(error);
    expect(screen.getByText("Something went wrong loading this page.")).toBeInTheDocument();

    screen.getByRole("button", { name: "Try again" }).click();
    expect(reset).toHaveBeenCalledTimes(1);
    consoleError.mockRestore();
  });

  it("renders in Telugu when the stored language is te", () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    render(<RootError error={new Error("boom")} reset={() => {}} />);

    expect(screen.getByText("పేజీ లోడ్ చేయడంలో ఏదో తప్పు జరిగింది.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "మళ్ళీ ప్రయత్నించు" })).toBeInTheDocument();
    consoleError.mockRestore();
  });
});
