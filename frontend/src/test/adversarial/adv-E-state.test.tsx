/**
 * ADVERSARIAL AUDIT E1/E2/E3 — state & persistence attacks (executed).
 *
 * E1  localStorage tampering: an adversary (or a stale browser profile) seeds
 *     goatfarm.farmId with a foreign/absurd value before the app boots. The
 *     bootstrap must never send another tenant's id as X-Farm-Id.
 * E2  URL/prop tampering on PaginationControls: NaN, negative and fractional
 *     offsets must never invert or corrupt the range label.
 * E3  Tenancy boundary: switching farms must drop every URL-keyed query from
 *     the cache — an old-farm response may never resolve into the new farm.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DashboardPage from "@/app/(app)/dashboard/page";
import { PaginationControls } from "@/components/pagination-controls";
import { useAuth } from "@/lib/auth-context";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const FARM_HEADER_ATTACKS: Array<[string, string | null]> = [
  ["non-numeric garbage", "not-a-number"],
  ["fractional id", "1.5"],
  ["negative id", "-3"],
  ["zero", "0"],
  ["float-precision garbage", "9007199254740993"],
];

function armDashboardHandler() {
  server.use(
    http.get("/api/dashboard", ({ request }) => {
      e1FarmHeader = request.headers.get("x-farm-id") ?? "";
      return HttpResponse.json({
        total_active: 0,
        sex_counts: { F: 0, M: 0 },
        status_totals: {},
        buckets: [],
        todays_tasks: [],
        todays_tasks_total: 0,
        overdue_tasks: [],
        overdue_tasks_total: 0,
        ultrasounds_due: [],
        ultrasounds_due_total: 0,
        kiddings_due: [],
        kiddings_due_total: 0,
        cull_candidates: [],
        cull_candidates_total: null,
        suggestions: [],
        suggestions_total: 0,
        recent_weights: [],
        recent_weights_total: null,
        recent_weights_limit: 10,
        preview_limit: 5,
      });
    }),
  );
}

/** Set by armDashboardHandler; read by the E1 assertions. */
let e1FarmHeader = "";

describe("ADV E1: a poisoned goatfarm.farmId never selects a foreign tenant", () => {
  beforeEach(() => {
    e1FarmHeader = "";
    armDashboardHandler();
  });

  it.each(FARM_HEADER_ATTACKS)("stored %j → the only real farm (1) is used", async (_label, stored) => {
    window.localStorage.setItem("goatfarm.farmId", stored ?? "");
    renderWithProviders(<DashboardPage />);
    await screen.findByText("Herd by bucket");
    await waitFor(() => expect(e1FarmHeader).toBe("1"));
  });

  it("stored foreign tenant id → cleared for an explicit pick, never silently entered", async () => {
    // A well-formed but foreign id (42) is not one of this user's memberships.
    // RT-O-3: the bootstrap treats it as revoked — the poisoned value is
    // dropped, no farm is auto-entered (the shell routes to /farm-select),
    // and no dashboard request ever carries the foreign id.
    window.localStorage.setItem("goatfarm.farmId", "42");
    renderWithProviders(<DashboardPage />);
    await waitFor(() =>
      expect(window.localStorage.getItem("goatfarm.farmId")).toBeNull(),
    );
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(e1FarmHeader).toBe("");
  });

  it("quota-blocked storage still boots the session", async () => {
    const original = window.localStorage.setItem;
    window.localStorage.setItem = () => {
      throw new DOMException("quota exceeded", "QuotaExceededError");
    };
    try {
      renderWithProviders(<DashboardPage />);
      await screen.findByText("Herd by bucket");
      await waitFor(() => expect(e1FarmHeader).toBe("1"));
    } finally {
      window.localStorage.setItem = original;
    }
  });
});

describe("ADV E2: PaginationControls under hostile offsets", () => {
  it("DEFENDED (L8): NaN and hostile offsets render a sane label", () => {
    render(
      <PaginationControls total={10} limit={5} offset={Number.NaN} onOffsetChange={() => {}} />,
    );
    expect(screen.getByText(/Showing 1–5 of 10/)).toBeInTheDocument();
  });

  it("negative offsets clamp to the first page instead of '0'", () => {
    render(
      <PaginationControls total={10} limit={5} offset={-1} onOffsetChange={() => {}} />,
    );
    expect(screen.getByText(/Showing 1–5 of 10/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
  });

  it("fractional offsets render without crashing", () => {
    const { container } = render(
      <PaginationControls total={10} limit={5} offset={2.5} onOffsetChange={() => {}} />,
    );
    expect(container.textContent).toMatch(/Showing .*–.* of 10/);
  });
});

describe("ADV E3: farm switch drops every URL-keyed query (tenancy fence)", () => {
  beforeEach(() => armDashboardHandler());

  it("an old-farm cached query cannot survive into the new farm", async () => {
    let switcher: ((id: number) => void) | null = null;
    function Probe() {
      const { selectFarm } = useAuth();
      switcher = selectFarm;
      return null;
    }

    const { queryClient } = renderWithProviders(
      <>
        <Probe />
        <DashboardPage />
      </>,
    );
    await screen.findByText("Herd by bucket");

    // Seed the cache the way a mounted query would.
    queryClient.setQueryData(["/api/animals", { limit: 50 }], {
      data: { animals: [{ id: 1, tag_number: "OLD-FARM" }] },
      status: 200,
      headers: new Headers(),
    });
    expect(queryClient.getQueryState(["/api/animals", { limit: 50 }])).not.toBeNull();

    switcher!(2);

    // queryClient.clear() drops the entry entirely (state becomes undefined).
    await waitFor(() => {
      expect(queryClient.getQueryState(["/api/animals", { limit: 50 }])).toBeUndefined();
    });
  });
});
