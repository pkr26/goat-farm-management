/**
 * AppLayoutClient — fresh-domain mutation campaign kills (2026-09): the
 * logout label on both breakpoints and the signed-out bootstrap gate.
 */

import { screen, waitFor } from "@testing-library/react";
import { http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import { AppLayoutClient as AppLayout } from "./app-layout-client";

const navState = vi.hoisted(() => ({ pathname: "/dashboard" }));

vi.mock("next/navigation", () => ({
  usePathname: () => navState.pathname,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

beforeAll(() => {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

describe("AppLayoutClient — campaign kills", () => {
  beforeEach(() => {
    navState.pathname = "/dashboard";
  });

  it("labels the logout control on both breakpoints", async () => {
    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);
    // The desktop span and the screen-reader-only mobile span both carry
    // the catalog's wording.
    expect(await screen.findAllByText("Logout")).toHaveLength(2);
  });

  it("keeps the shell gated while the session bootstrap is still loading", async () => {
    server.use(
      http.get("/api/auth/farms", () => new Promise(() => undefined)),
    );
    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);
    // No navigation and no logout control until the session resolves.
    await waitFor(() => expect(screen.queryByText("Logout")).not.toBeInTheDocument());
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });
});
