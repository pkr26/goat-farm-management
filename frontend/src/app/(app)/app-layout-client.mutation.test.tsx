/**
 * App shell (client) — mutation-hardening suite for the surviving branches:
 * the route → document.title mapping (specific routes and the fallback),
 * the farm switcher's accessible name, the sidebar permissions-error retry,
 * nav labels, and the signed-out
 * loading gate that must not render the shell with a missing user.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { APP_NAME } from "@/lib/brand";

import { AppLayoutClient as AppLayout } from "./app-layout-client";

const { pushMock, replaceMock, navState } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  replaceMock: vi.fn(),
  navState: { pathname: "/dashboard", search: "" },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => navState.pathname,
  useSearchParams: () => new URLSearchParams(navState.search),
  useParams: () => ({}),
}));

beforeAll(() => {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
});

function navLinks() {
  return screen
    .queryAllByRole("link")
    .filter((el) => el.closest('[data-slot="sidebar-content"]'));
}

function navGroupItems(label: string) {
  const group = screen.getByText(label).closest('[data-slot="sidebar-group"]');
  if (!group) throw new Error(`no sidebar group titled "${label}"`);
  return Array.from(group.querySelectorAll('a')).map((link) => link.textContent);
}

async function renderShell() {
  const rendered = renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);
  await waitFor(() => expect(navLinks()).toHaveLength(16));
  return rendered;
}

describe("AppLayout — document titles", () => {
  beforeEach(() => {
    document.title = "";
    navState.search = "";
  });

  it.each([
    ["/dashboard", "Dashboard"],
    ["/animals/new", "Add animal"],
    ["/animals/123", "Animal"],
    ["/feeding/inventory", "Feed inventory"],
    ["/breeding/5/ultrasound", "Ultrasound"],
    ["/no-access", "No access"],
    ["/screening", "Photo screening"],
  ])("titles %s as %s", async (pathname, title) => {
    navState.pathname = pathname;
    await renderShell();
    expect(document.title).toBe(`${title} · ${APP_NAME}`);
  });

  it("falls back to the generic product title off the known routes", async () => {
    navState.pathname = "/not-a-route";
    await renderShell();
    expect(document.title).toBe(`${APP_NAME} — Livestock farm management`);
  });

  it("localizes the tab title when the worker switched to Telugu", async () => {
    const { LanguageProvider, LANGUAGE_STORAGE_KEY } = await import("@/lib/i18n");
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    try {
      navState.pathname = "/dashboard";
      renderWithProviders(
        <LanguageProvider>
          <AppLayout defaultOpen={true}>{null}</AppLayout>
        </LanguageProvider>,
      );
      await waitFor(() => expect(document.title).toBe(`డాష్‌బోర్డు · ${APP_NAME}`));
    } finally {
      window.localStorage.clear();
      document.documentElement.lang = "en";
    }
  });
});

describe("AppLayout — farm switcher", () => {
  beforeEach(() => {
    navState.pathname = "/dashboard";
    navState.search = "";
  });

  it("exposes the switcher under an accessible name carrying the farm", async () => {
    await renderShell();
    const switcher = screen.getByRole("link", {
      name: "Switch farm — current: Test Goat Farm",
    });
    expect(switcher).toHaveTextContent("Test Goat Farm");
    // The pill styling: bounded width, hover affordance and a focus ring.
    expect(switcher.className).toContain("group/farm");
    expect(switcher.className).toContain("max-w-96");
    expect(switcher.className).toContain("hover:border-primary/40");
    expect(switcher.className).toContain("focus-visible:ring-2");
  });

  it("retitles the document on every client-side navigation", async () => {
    navState.pathname = "/dashboard";
    const rendered = await renderShell();
    expect(document.title).toBe(`Dashboard · ${APP_NAME}`);

    navState.pathname = "/reports";
    rendered.rerender(<AppLayout defaultOpen={true}>{null}</AppLayout>);
    await waitFor(() => expect(document.title).toBe(`Reports · ${APP_NAME}`));
  });

  it("keeps the current route and query in the switch-farm return state", async () => {
    navState.pathname = "/animals/12";
    navState.search = "?tab=weights&rows=50";
    await renderShell();
    expect(screen.getByRole("link", { name: /Switch farm/ })).toHaveAttribute(
      "href",
      "/farm-select?returnTo=%2Fanimals%2F12%3Ftab%3Dweights%26rows%3D50",
    );
  });
});

describe("AppLayout — sidebar permissions failure", () => {
  beforeEach(() => {
    navState.pathname = "/dashboard";
    navState.search = "";
  });

  it("shows the failure with a retry that restores navigation", async () => {
    let permsCalls = 0;
    server.use(
      http.get("/api/auth/permissions", () => {
        permsCalls += 1;
        return permsCalls === 1
          ? HttpResponse.json({ detail: "boom" }, { status: 500 })
          : HttpResponse.json({ is_owner: true, permissions: ["dashboard.view", "animals.view"] });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    const retry = await screen.findByRole("button", { name: /retry/i });
    expect(screen.queryByRole("link", { name: "Dashboard" })).not.toBeInTheDocument();

    await user.click(retry);
    await waitFor(() => expect(navLinks()).toHaveLength(2));
  });
});

describe("AppLayout — species-aware nav labels", () => {
  beforeEach(() => {
    navState.pathname = "/dashboard";
    navState.search = "";
  });

  it("speaks goat vocabulary throughout the nav", async () => {
    await renderShell();
    expect(navGroupItems("Herd")).toEqual(["Animals", "Buckets", "Breeding", "Kidding"]);
    expect(navGroupItems("Health & Feed")).toEqual(["Health", "Photo screening", "Feeding"]);
    expect(screen.getByRole("link", { name: "Switch farm — current: Test Goat Farm" })).toHaveTextContent(
      "Goat farm",
    );
  });
});

describe("AppLayout — session gate", () => {
  beforeEach(() => {
    navState.pathname = "/dashboard";
    navState.search = "";
  });

  it("holds a signed-out session on the loading screen instead of the shell", async () => {
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
    );
    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    await waitFor(() => expect(navLinks()).toHaveLength(0));
    expect(screen.queryByRole("link", { name: /Switch farm/ })).not.toBeInTheDocument();
  });
});
