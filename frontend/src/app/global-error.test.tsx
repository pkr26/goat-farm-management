/**
 * Root global-error boundary: owns its <html>/<body> (the root layout is not mounted
 * when this boundary fires), logs once, and offers the retry that re-runs the failed
 * render. Copy and <html lang> come from the stored language, resolved without the
 * provider.
 */

import { render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LANGUAGE_STORAGE_KEY } from "@/lib/i18n";

import GlobalError from "./global-error";

beforeEach(() => {
  window.localStorage.clear();
});

describe("GlobalError", () => {
  it("logs the error once and offers the retry that re-runs the render", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const reset = vi.fn();
    const error = new Error("root layout exploded");

    render(<GlobalError error={error} reset={reset} />);

    expect(consoleError).toHaveBeenCalledWith(error);
    expect(screen.getByText("Something went wrong loading this page.")).toBeInTheDocument();

    screen.getByRole("button", { name: "Try again" }).click();
    expect(reset).toHaveBeenCalledTimes(1);
    consoleError.mockRestore();
  });

  it("renders in Telugu when the stored language is te", () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    render(<GlobalError error={new Error("boom")} reset={() => {}} />);

    expect(screen.getByText("పేజీ లోడ్ చేయడంలో ఏదో తప్పు జరిగింది.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "మళ్ళీ ప్రయత్నించు" })).toBeInTheDocument();
    consoleError.mockRestore();
  });

  it("carries its own <html>/<body> shell (jsdom dissolves them on inline renders)", () => {
    // RTL renders into a container div, where the HTML parser relocates
    // body-level nodes and drops the document tags — so assert the document
    // shell on the static markup Next would stream instead.
    const markup = renderToStaticMarkup(
      <GlobalError error={new Error("boom")} reset={() => {}} />,
    );
    expect(markup.startsWith('<html lang="en">')).toBe(true);
    expect(markup).toContain("<body>");
    expect(markup).toContain("Something went wrong loading this page.");
  });

  it("stamps the stored language on <html lang> in the streamed shell", () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    const markup = renderToStaticMarkup(
      <GlobalError error={new Error("boom")} reset={() => {}} />,
    );
    expect(markup.startsWith('<html lang="te">')).toBe(true);
    expect(markup).toContain("పేజీ లోడ్ చేయడంలో ఏదో తప్పు జరిగింది.");
  });
});
