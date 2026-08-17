import { renderToString } from "react-dom/server";
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

describe("ThemeToggle", () => {
  beforeEach(() => {
    theme.resolvedTheme = "light";
    theme.setTheme.mockReset();
  });

  it("renders a stable, labelled server placeholder", () => {
    const markup = renderToString(<ThemeToggle />);

    expect(markup).toContain('aria-label="Toggle theme"');
    expect(markup).not.toContain("<svg");
  });

  it("switches a light theme to dark", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle />);

    const button = screen.getByRole("button", { name: "Switch to dark theme" });
    expect(button.querySelector(".lucide-moon")).toBeInTheDocument();
    await user.click(button);
    expect(theme.setTheme).toHaveBeenCalledWith("dark");
  });

  it("switches a dark theme to light", async () => {
    theme.resolvedTheme = "dark";
    const user = userEvent.setup();
    render(<ThemeToggle />);

    const button = screen.getByRole("button", { name: "Switch to light theme" });
    expect(button.querySelector(".lucide-sun")).toBeInTheDocument();
    await user.click(button);
    expect(theme.setTheme).toHaveBeenCalledWith("light");
  });
});
