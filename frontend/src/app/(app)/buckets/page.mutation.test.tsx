/**
 * Buckets board — regression suite: species-aware bucket labels, the full-register
 * link's safe path handling (external backend path vs the plain filter fallback),
 * the ration's 3-dp rendering, and both retry paths.
 */

import { Component, type ReactNode } from "react";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import BucketsPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/buckets",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

function boardRow(overrides: Record<string, unknown> = {}) {
  return {
    bucket: "PREGNANCY_EARLY",
    name: "Pregnancy A pen",
    who: "Confirmed pregnant does",
    exit_rule: null,
    daily_kg_per_head: 1.25,
    animals: [],
    animals_total: 0,
    animals_limit: 10,
    animals_page_path: null,
    ...overrides,
  };
}

function boardHandler(rows: unknown[]) {
  return http.get("/api/buckets", () => HttpResponse.json(rows));
}

class BoardRenderBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed
      ? <p role="alert">Bucket register failed to render</p>
      : this.props.children;
  }
}

async function renderLoaded() {
  renderWithProviders(<BoardRenderBoundary><BucketsPage /></BoardRenderBoundary>);
  await screen.findByText("Pregnancy A pen");
}

describe("BucketsPage — mutation targets", () => {
  it("shows a newly entered bucket's zero elapsed days as zero", async () => {
    server.use(boardHandler([boardRow({
      animals: [{ id: 1, tag_number: "G-TODAY", name: "Nandi", sex: "F", days_in_current_bucket: 0 }],
      animals_total: 1,
    })]));
    await renderLoaded();
    const row = screen.getByText("G-TODAY").closest("tr")!;
    expect(within(row).getAllByRole("cell")[4]).toHaveTextContent(/^0$/);
  });

  it("shows the missing-name placeholder for null and empty names in persisted bucket records", async () => {
    server.use(boardHandler([boardRow({
      animals: [
        { id: 1, tag_number: "G-NULL", name: null, sex: "F" },
        { id: 2, tag_number: "G-EMPTY", name: "", sex: "F" },
        { id: 3, tag_number: "G-NAMED", name: "Nandi", sex: "F" },
      ],
      animals_total: 3,
    })]));
    await renderLoaded();
    for (const [tag, name] of [["G-NULL", "—"], ["G-EMPTY", "—"], ["G-NAMED", "Nandi"]]) {
      const row = screen.getByText(tag).closest("tr")!;
      expect(within(row).getAllByRole("cell")[1]).toHaveTextContent(name);
    }
  });

  it("labels the bucket with its species word and keeps the 3-dp ration", async () => {
    server.use(boardHandler([boardRow({ daily_kg_per_head: 1.234 })]));
    await renderLoaded();

    expect(screen.getByText("Pregnancy A pen")).toBeInTheDocument();
    expect(screen.getByText("0 head")).toBeInTheDocument();
    expect(
      screen.getByText("Pregnancy A · 1.234 kg/head/day · Confirmed pregnant does"),
    ).toBeInTheDocument();
    expect(screen.queryByText("PREGNANCY_EARLY")).not.toBeInTheDocument();
  });

  it("uses the backend's register path only when it is a safe app path", async () => {
    server.use(
      boardHandler([
        boardRow({
          animals: [{ id: 1, tag_number: "G-001" }],
          animals_total: 4,
          animals_page_path: "https://evil.example/farm",
        }),
      ]),
    );
    await renderLoaded();

    expect(screen.getByRole("link", { name: "View the full bucket register" })).toHaveAttribute(
      "href",
      "/animals?bucket=PREGNANCY_EARLY",
    );
  });

  it("links straight to a backend-supplied internal register path", async () => {
    server.use(
      boardHandler([
        boardRow({
          animals: [{ id: 1, tag_number: "G-001" }],
          animals_total: 4,
          animals_page_path: "/animals?bucket=PREGNANCY_EARLY&status=ACTIVE",
        }),
      ]),
    );
    await renderLoaded();

    expect(screen.getByRole("link", { name: "View the full bucket register" })).toHaveAttribute(
      "href",
      "/animals?bucket=PREGNANCY_EARLY&status=ACTIVE",
    );
  });

  it("falls back to the plain filter when the safe path leaves the animals module", async () => {
    // RT-P6-1: safeAppPath alone accepts ANY same-origin absolute path, so a
    // (hypothetical buggy) generator value pointing at another module must
    // not become the register link — even for a caller who can view that
    // other module (the default owner here holds every permission).
    server.use(
      boardHandler([
        boardRow({
          animals: [{ id: 1, tag_number: "G-001" }],
          animals_total: 4,
          animals_page_path: "/finance",
        }),
      ]),
    );
    await renderLoaded();

    expect(screen.getByRole("link", { name: "View the full bucket register" })).toHaveAttribute(
      "href",
      "/animals?bucket=PREGNANCY_EARLY",
    );
  });

  it("retries the board load from the error state", async () => {
    let calls = 0;
    server.use(
      http.get("/api/buckets", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ detail: "board offline" }, { status: 503 })
          : HttpResponse.json([boardRow()]);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<BucketsPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("board offline");
    await user.click(screen.getByRole("button", { name: "Retry buckets" }));

    await waitFor(() => expect(calls).toBe(2));
    expect(await screen.findByText("Pregnancy A pen")).toBeInTheDocument();
  });

  it("recovers the page when the permissions probe is retried", async () => {
    let permsCalls = 0;
    server.use(
      http.get("/api/auth/permissions", () => {
        permsCalls += 1;
        return permsCalls === 1
          ? HttpResponse.json({ detail: "boom" }, { status: 500 })
          : HttpResponse.json({ is_owner: true, permissions: ["buckets.view"] });
      }),
      boardHandler([boardRow()]),
    );
    const user = userEvent.setup();
    renderWithProviders(<BucketsPage />);

    await user.click(await screen.findByRole("button", { name: /retry/i }));
    expect(await screen.findByText("Pregnancy A pen")).toBeInTheDocument();
  });
});
