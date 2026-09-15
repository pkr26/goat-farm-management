/**
 * Mutation-hardening for src/components/auth-layout.tsx: both brand surfaces
 * (the desktop split panel and the mobile bar) must carry the gradient built
 * from the --primary token, and the marketing copy/slots must render.
 */

import { render, screen, waitFor } from "@testing-library/react";
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

    // The single h1 is the form title — it stays mounted below the lg
    // breakpoint where the brand panel (and its slogan) hides (H2b).
    const headings = container.querySelectorAll("h1");
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent("Create account");
    expect(screen.getByText(/Herd management,/)).toBeInTheDocument();
    expect(screen.getByText(/simplified\./)).toBeInTheDocument();
    expect(screen.getByText(/Run a healthier, more profitable farm/)).toBeInTheDocument();
    for (const feature of [
      "Complete herd records",
      "Proactive health care",
      "Insights that pay off",
    ]) {
      expect(screen.getByText(feature)).toBeInTheDocument();
    }
    expect(
      screen.getByText(/Osmanabadi goat herds across Telangana/),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Create account" })).toBeInTheDocument();
    expect(screen.getByText("Start tracking")).toBeInTheDocument();
    expect(screen.getByText("registration fields")).toBeInTheDocument();
    expect(screen.getByText("Already registered?")).toBeInTheDocument();
    // Desktop panel and mobile bar each carry the wordmark.
    expect(screen.getAllByText("Herdly")).toHaveLength(2);
  });

  it("localizes the brand panel in Telugu", async () => {
    const { LanguageProvider } = await import("@/lib/i18n");
    window.localStorage.setItem("herdly.language", "te");
    const { container } = render(
      <LanguageProvider>
        <AuthLayout title="Sign in" subtitle="Welcome back" footer="Need an account?">
          <p>fields</p>
        </AuthLayout>
      </LanguageProvider>,
    );
    // The stored choice is adopted in a mount effect, then the brand slogan
    // and feature rows render in Telugu.
    await waitFor(() => {
      const slogan = container.querySelector(".font-heading.text-4xl");
      expect(slogan?.textContent).toContain("మంద నిర్వహణ,");
    });
    expect(screen.getByText("ముందస్తు ఆరోగ్య సంరక్షణ")).toBeInTheDocument();
    window.localStorage.clear();
  });
});
