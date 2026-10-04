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
    Noto_Sans_Telugu: loader("Noto_Sans_Telugu"),
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

// RootLayout reads the proxy-minted CSP nonce from the request headers
// (src/proxy.ts, M-1 2026-09-20); jsdom has no request context, so hand it
// the exact header the real proxy sets.
vi.mock("next/headers", () => ({
  headers: async () => new Headers({ "x-nonce": "dGVzdC1ub25jZS0wMTIzNDU2Nzg5" }),
  cookies: async () => ({ get: vi.fn(() => undefined) }),
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
    // The font loaders run at module scope and the hoisted mock records that
    // import. Do not reset Vitest's process-wide module registry here: doing
    // so discards the locale catalog installed by vitest.setup and makes every
    // later test sharing this worker observe a different i18n singleton.

    // globals.css maps --font-sans/--font-mono onto these exact variable
    // names, so a renamed or dropped variable silently un-styles the app.
    // Noto Sans Telugu covers the Telugu script Inter/Fraunces lack.
    expect(fontLoaderCalls).toEqual([
      { family: "Inter", options: { subsets: ["latin"], variable: "--font-inter" } },
      { family: "Fraunces", options: { subsets: ["latin"], variable: "--font-fraunces" } },
      {
        family: "JetBrains_Mono",
        options: { subsets: ["latin"], variable: "--font-jetbrains-mono" },
      },
      {
        family: "Noto_Sans_Telugu",
        options: { subsets: ["telugu", "latin"], variable: "--font-noto-sans-telugu" },
      },
    ]);

    expect(metadata.title).toEqual({
      default: "Herdly — Goat farm management",
      template: "%s · Herdly",
    });
    expect(metadata.description).toBe(
      "Goat farm management — herd, health, breeding, kidding and finance in one place.",
    );
  });

  it("exports the document metadata Next renders into <head>", () => {
    expect(metadata.title).toEqual({
      default: "Herdly — Goat farm management",
      template: "%s · Herdly",
    });
    expect(metadata.description).toBe(
      "Goat farm management — herd, health, breeding, kidding and finance in one place.",
    );
  });

  it("renders the html/body shell with both font variable classes", async () => {
    // RootLayout is an async server component (it awaits the request headers
    // for the CSP nonce): resolve it to its element tree before jsdom render.
    render(
      await RootLayout({
        children: <p>page content</p>,
      }),
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
      "__variable_--font-noto-sans-telugu",
    );

    expect(document.body).toHaveClass(
      "min-h-screen",
      "bg-background",
      "text-foreground",
      "antialiased",
    );
    expect(await screen.findByText("page content")).toBeInTheDocument();
  });

  it("mounts children inside the app providers", async () => {
    render(
      await RootLayout({
        children: <ProviderProbe />,
      }),
    );

    // The probe only renders at all because Providers supplied AuthProvider,
    // and it resolves to the session user once the refresh bootstrap lands.
    await waitFor(() => expect(screen.getByText(TEST_USER.name)).toBeInTheDocument());
  });
});
