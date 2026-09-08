/**
 * Root global-error boundary: owns its <html>/<body> (the root layout is
 * not mounted when this boundary fires), logs once, and offers the retry
 * that re-runs the failed render.
 */

import { render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import GlobalError from "./global-error";

describe("GlobalError", () => {
  it("logs the error once and offers the retry that re-runs the render", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const reset = vi.fn();
    const error = new Error("root layout exploded");

    render(<GlobalError error={error} reset={reset} />);

    expect(consoleError).toHaveBeenCalledWith(error);
    expect(screen.getByText("Something went wrong. Please try again.")).toBeInTheDocument();

    screen.getByRole("button", { name: "Try again" }).click();
    expect(reset).toHaveBeenCalledTimes(1);
    consoleError.mockRestore();
  });

  it("carries its own <html>/<body> shell (jsdom dissolves them on inline renders)", () => {
    // RTL renders into a container div, where the HTML parser relocates
    // body-level nodes and drops the document tags — so assert the document
    // shell on the static markup Next would stream instead.
    const markup = renderToStaticMarkup(
      <GlobalError error={new Error("boom")} reset={() => {}} />,
    );
    expect(markup.startsWith("<html")).toBe(true);
    expect(markup).toContain("<body>");
    expect(markup).toContain("Something went wrong. Please try again.");
  });
});
