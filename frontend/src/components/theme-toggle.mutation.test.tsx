/**
 * Mutation-hardening for src/components/theme-toggle.tsx.
 *
 * The pending-theme ref exists because next-themes publishes resolvedTheme a
 * render later. Two contracts keep rapid toggles correct:
 *  1. the effect may only clear the pending value once resolvedTheme has
 *     caught up to it — while the provider lags, the pending value wins;
 *  2. once cleared, a later external theme change must be composed from the
 *     freshly published theme, not a stale pending value.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeToggle } from "@/components/theme-toggle";

const theme = vi.hoisted(() => ({
  resolvedTheme: "light" as string | undefined,
  setTheme: vi.fn(),
}));

vi.mock("next-themes", () => ({
  useTheme: () => theme,
}));

describe("ThemeToggle pending-theme bookkeeping", () => {
  beforeEach(() => {
    theme.resolvedTheme = "light";
    theme.setTheme.mockReset();
  });

  it("keeps composing from the pending request while the provider lags behind", async () => {
    const user = userEvent.setup();
    const view = render(<ThemeToggle />);
    const button = screen.getByRole("button", { name: "Switch to dark theme" });

    // Request dark; the provider does not publish it yet.
    await user.click(button);
    expect(theme.setTheme).toHaveBeenLastCalledWith("dark");

    // resolvedTheme moves somewhere unrelated ("system"): the pending "dark"
    // must survive so the next click still targets light.
    theme.resolvedTheme = "system";
    view.rerender(<ThemeToggle />);

    await user.click(button);
    expect(theme.setTheme).toHaveBeenLastCalledWith("light");
    expect(theme.setTheme.mock.calls).toEqual([["dark"], ["light"]]);
  });

  it("clears the pending request once the provider catches up, so an external flip is honoured", async () => {
    const user = userEvent.setup();
    const view = render(<ThemeToggle />);
    const button = screen.getByRole("button", { name: "Switch to dark theme" });

    await user.click(button);
    expect(theme.setTheme).toHaveBeenLastCalledWith("dark");

    // The provider publishes the requested dark theme: pending may be cleared.
    theme.resolvedTheme = "dark";
    view.rerender(<ThemeToggle />);
    expect(screen.getByRole("button", { name: "Switch to light theme" })).toBeInTheDocument();

    // Something else (another tab, a system change) flips the theme back to
    // light without our clicks. The toggle must now offer dark again and
    // request dark — not replay the stale pending value.
    theme.resolvedTheme = "light";
    view.rerender(<ThemeToggle />);
    expect(screen.getByRole("button", { name: "Switch to dark theme" })).toBeInTheDocument();

    await user.click(button);
    expect(theme.setTheme).toHaveBeenLastCalledWith("dark");
    expect(theme.setTheme).not.toHaveBeenLastCalledWith("light");
    expect(theme.setTheme.mock.calls).toEqual([["dark"], ["dark"]]);
  });

  it("toggles from the published theme when nothing is pending", async () => {
    const user = userEvent.setup();
    theme.resolvedTheme = "dark";
    render(<ThemeToggle />);

    const button = screen.getByRole("button", { name: "Switch to light theme" });
    expect(button.querySelector(".lucide-sun")).toBeInTheDocument();
    await user.click(button);
    expect(theme.setTheme).toHaveBeenCalledWith("light");
  });
});
