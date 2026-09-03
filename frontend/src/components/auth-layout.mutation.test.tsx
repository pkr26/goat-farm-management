/**
 * Mutation-hardening for src/components/auth-layout.tsx: both brand surfaces
 * (the desktop split panel and the mobile bar) must carry the gradient built
 * from the --primary token, and the marketing copy/slots must render.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AuthLayout } from "@/components/auth-layout";

function gradientPanels(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>("[style]"),
  ).filter((element) => element.style.backgroundImage !== "");
}

describe("AuthLayout", () => {
  it("paints the primary-derived gradient on both brand surfaces", () => {
    const { container } = render(
      <AuthLayout title="Sign in" subtitle="Welcome back" footer="Need an account?">
        <form aria-label="sign-in form">fields</form>
      </AuthLayout>,
    );

    const panels = gradientPanels(container);
    expect(panels).toHaveLength(2);

    const [desktop, mobile] = panels;
    expect(desktop).toHaveClass("hidden", "lg:flex");
    expect(desktop.style.backgroundImage).toContain("linear-gradient(165deg");
    expect(desktop.style.backgroundImage).toContain("var(--primary)");

    expect(mobile).toHaveClass("lg:hidden");
    expect(mobile.style.backgroundImage).toContain("linear-gradient(165deg");
  });

  it("renders the brand story and the form slots", () => {
    const { container } = render(
      <AuthLayout title="Create account" subtitle="Start tracking" footer="Already registered?">
        <p>registration fields</p>
      </AuthLayout>,
    );

    const headline = container.querySelector("h1");
    expect(headline).toHaveTextContent("Herd management,simplified.");
    expect(screen.getByText(/Run a healthier, more profitable farm/)).toBeInTheDocument();
    for (const feature of [
      "Complete herd records",
      "Proactive health care",
      "Insights that pay off",
    ]) {
      expect(screen.getByText(feature)).toBeInTheDocument();
    }
    expect(
      screen.getByText(/Osmanabadi goat herds and Murrah dairies/),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Create account" })).toBeInTheDocument();
    expect(screen.getByText("Start tracking")).toBeInTheDocument();
    expect(screen.getByText("registration fields")).toBeInTheDocument();
    expect(screen.getByText("Already registered?")).toBeInTheDocument();
    // Desktop panel and mobile bar each carry the wordmark.
    expect(screen.getAllByText("Herdly")).toHaveLength(2);
  });
});
