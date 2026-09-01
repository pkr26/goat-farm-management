/**
 * Root layout: the document shell every route is rendered inside. Covers the
 * next/font wiring (both families must publish the CSS variables globals.css
 * maps onto --font-sans/--font-mono), the metadata Next puts in <head>, and
 * the html/body attributes plus the Providers mount that hands every page its
 * QueryClient, theme and auth context.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { useAuth } from "@/lib/auth-context";
import { TEST_USER } from "@/test/msw-server";

import RootLayout, { metadata } from "./layout";

const { fontLoaderCalls } = vi.hoisted(() => ({
  fontLoaderCalls: [] as { family: string; options: Record<string, unknown> }[],
}));

vi.mock("next/font/google", () => {
  // Stand-in for the build-time next/font loaders: each records the config it
  // was called with and returns a generated class carrying the requested CSS
  // variable, which is exactly what the real loader's `variable` field is.
  const loader = (family: string) => (options: Record<string, unknown>) => {
    fontLoaderCalls.push({ family, options });
    const { subsets } = options;
    if (!Array.isArray(subsets) || subsets.length === 0) {
      // next/font fails the production build with this error, so a layout that
      // dropped its subsets must fail here too rather than silently pass.
      throw new Error(`Missing selected subsets for font \`${family}\``);
    }
    return {
      className: `__className_${family}`,
      variable: `__variable_${String(options.variable)}`,
      style: { fontFamily: family },
    };
  };
  return {
    Inter: loader("Inter"),
    Fraunces: loader("Fraunces"),
    JetBrains_Mono: loader("JetBrains_Mono"),
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

beforeAll(() => {
  // jsdom has no matchMedia; next-themes' system-theme detection needs it.
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
  }));
});

/** Consumes the contexts Providers supplies; useAuth throws if it is absent. */
function ProviderProbe() {
  const { loading, user } = useAuth();
  return <p>{loading ? "auth-loading" : (user?.name ?? "anonymous")}</p>;
}

describe("RootLayout", () => {
  it("loads both font families with the latin subset and the theme's CSS variables", async () => {
    // The font loaders run at module scope, so importing this file once at the
    // top would record the calls before the test body ever executes — the
    // assertion would then only ever inspect a cached side effect. Re-import
    // under a reset module registry so the configuration is genuinely
    // exercised *by this test*.
    fontLoaderCalls.length = 0;
    vi.resetModules();
    await import("./layout");

    // globals.css maps --font-sans/--font-mono onto these exact variable
    // names, so a renamed or dropped variable silently un-styles the app.
    expect(fontLoaderCalls).toEqual([
      { family: "Inter", options: { subsets: ["latin"], variable: "--font-inter" } },
      { family: "Fraunces", options: { subsets: ["latin"], variable: "--font-fraunces" } },
      {
        family: "JetBrains_Mono",
        options: { subsets: ["latin"], variable: "--font-jetbrains-mono" },
      },
    ]);
  });

  it("exports the document metadata Next renders into <head>", () => {
    expect(metadata.title).toEqual({
      default: "Herdly — Livestock farm management",
      template: "%s · Herdly",
    });
    expect(metadata.description).toBe(
      "Commercial goat and buffalo dairy farm management — herd, health, breeding, milk and finance in one place.",
    );
  });

  it("renders the html/body shell with both font variable classes", () => {
    render(
      <RootLayout>
        <p>page content</p>
      </RootLayout>,
    );

    // React 19 renders <html>/<body> onto the real document singletons, so the
    // layout's attributes land on the document element itself.
    const html = document.documentElement;
    expect(html).toHaveAttribute("lang", "en");
    // Tailwind resolves font-sans/font-mono through these classes; both must be
    // on the root element or every page falls back to the browser default font.
    expect(html).toHaveClass(
      "__variable_--font-inter",
      "__variable_--font-fraunces",
      "__variable_--font-jetbrains-mono",
    );

    expect(document.body).toHaveClass(
      "min-h-screen",
      "bg-background",
      "text-foreground",
      "antialiased",
    );
    expect(screen.getByText("page content")).toBeInTheDocument();
  });

  it("mounts children inside the app providers", async () => {
    render(
      <RootLayout>
        <ProviderProbe />
      </RootLayout>,
    );

    // The probe only renders at all because Providers supplied AuthProvider,
    // and it resolves to the session user once the refresh bootstrap lands.
    await waitFor(() => expect(screen.getByText(TEST_USER.name)).toBeInTheDocument());
  });
});
