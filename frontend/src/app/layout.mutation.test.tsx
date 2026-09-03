/**
 * Root layout metadata — mutation-hardening: the exported Metadata object
 * (default title, per-page template, description) is what Next injects into
 * <head>; assert the exact strings so template/default swaps or blanked
 * descriptions cannot survive.
 */

import { describe, expect, it, vi } from "vitest";

import { APP_NAME } from "@/lib/brand";

vi.mock("next/font/google", () => {
  const loader = () => (options: Record<string, unknown>) => {
    if (!Array.isArray(options.subsets) || options.subsets.length === 0) {
      throw new Error("Missing selected subsets");
    }
    return { className: "__className", variable: String(options.variable), style: {} };
  };
  return { Inter: loader(), Fraunces: loader(), JetBrains_Mono: loader() };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

import RootLayout, { metadata } from "./layout";

describe("RootLayout metadata", () => {
  it("publishes the default document title and the per-page template", () => {
    expect(metadata.title).toEqual({
      default: `${APP_NAME} — Livestock farm management`,
      template: `%s · ${APP_NAME}`,
    });
  });

  it("describes the product in the meta description", () => {
    expect(metadata.description).toBe(
      "Commercial goat and buffalo dairy farm management — herd, health, breeding, milk and finance in one place.",
    );
  });

  it("keeps a default export for the shell", () => {
    expect(typeof RootLayout).toBe("function");
  });
});
