/**
 * Dashboard advisory + localized duty titles: a singular bakrid_hold advisory
 * object renders the localized hold-for-festival banner (unknown keys are
 * ignored), and task previews render generated titles through the taskGen
 * catalog when the payload carries title_key/title_args.
 */

import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DashboardOut, TaskOut } from "@/api/generated/models";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { farmToday } from "@/lib/format";
import { LanguageProvider, LANGUAGE_STORAGE_KEY } from "@/lib/i18n";

import DashboardPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const TODAY = farmToday();

function makeTask(overrides: Partial<TaskOut>): TaskOut {
  return {
    id: 1,
    title: "Task",
    due_date: TODAY,
    status: "PENDING",
    category: "OTHER",
    auto_generated: false,
    animal_id: null,
    purchase_batch_id: null,
    breeding_record_id: null,
    assigned_role_id: null,
    assigned_user_id: null,
    recur_days: null,
    recurring_series_id: null,
    completed_by_id: null,
    completed_at: null,
    verified_by_id: null,
    verified_at: null,
    verification_note: null,
    skipped_by_id: null,
    skipped_at: null,
    skip_reason: null,
    rejected_by_id: null,
    rejected_at: null,
    action_url: null,
    ...overrides,
  };
}

function payload(overrides: Record<string, unknown> = {}) {
  return {
    buckets: [],
    total_active: 5,
    sex_counts: { F: 3, M: 2 },
    status_totals: {},
    todays_tasks: [],
    overdue_tasks: [],
    ultrasounds_due: [],
    kiddings_due: [],
    cull_candidates: [],
    suggestions: [],
    restricted_animals: [],
    recent_weights: [],
    insurance_expiring: [],
    todays_tasks_total: 0,
    overdue_tasks_total: 0,
    ultrasounds_due_total: 0,
    kiddings_due_total: 0,
    cull_candidates_total: 0,
    suggestions_total: 0,
    restricted_animals_total: 0,
    recent_weights_total: 0,
    insurance_expiring_total: 0,
    preview_limit: 20,
    recent_weights_limit: 10,
    ...overrides,
  } as DashboardOut;
}

function useDashboard(body: DashboardOut | Record<string, unknown>) {
  server.use(http.get("/api/dashboard", () => HttpResponse.json(body)));
}

describe("DashboardPage — advisories", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.lang = "en";
  });

  it("renders the Bakrid hold banner from a bakrid_hold advisory", async () => {
    useDashboard(
      payload({
        advisory: { key: "bakrid_hold", args: { count: 7, festival_date: "2027-05-27" } },
      }),
    );
    renderWithProviders(<DashboardPage />);

    expect(
      await screen.findByText(
        "7 males finish within 2 months of Bakrid (27 May 2027) — hold for the festival price.",
      ),
    ).toBeInTheDocument();
  });

  it("ignores unknown advisory keys and a missing advisory field", async () => {
    useDashboard(payload({ advisory: { key: "future_kind", args: {} } }));
    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText(/Dashboard/)).toBeInTheDocument();
    expect(screen.queryByText(/Bakrid/)).not.toBeInTheDocument();
  });

  it("renders the banner in Telugu when the worker switched languages", async () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    useDashboard(
      payload({
        advisory: { key: "bakrid_hold", args: { count: 3, festival_date: "2027-05-27" } },
      }),
    );
    renderWithProviders(
      <LanguageProvider>
        <DashboardPage />
      </LanguageProvider>,
    );

    await waitFor(() =>
      expect(
        screen.getByText(/3 మగ మేకలు బక్రీద్ .* పండుగ ధర కోసం ఆపి ఉంచండి\./),
      ).toBeInTheDocument(),
    );
  });
});

describe("DashboardPage — generated duty titles", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.lang = "en";
  });

  it("renders a keyed duty through the taskGen catalog (Telugu session)", async () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    useDashboard(
      payload({
        todays_tasks_total: 1,
        todays_tasks: [
          makeTask({
            id: 9,
            title: "Kidding watch: G-101 (due 20-09) — check udder fill",
            title_key: "kidding_watch",
            title_args: { tag: "G-101", kidding_date: "2026-09-20", days_before: 3 },
          } as Partial<TaskOut>),
        ],
      }),
    );
    renderWithProviders(
      <LanguageProvider>
        <DashboardPage />
      </LanguageProvider>,
    );

    await waitFor(() =>
      expect(
        screen.getByText(/పిల్లల గమనింపు: G-101 \(.+ సెప్టెం 2026 నాటికి\)/),
      ).toBeInTheDocument(),
    );
    // The raw English payload title is not what renders.
    expect(screen.queryByText(/Kidding watch: G-101/)).not.toBeInTheDocument();
  });

  it("falls back to the payload title for unkeyed duties", async () => {
    useDashboard(
      payload({
        todays_tasks_total: 1,
        todays_tasks: [makeTask({ id: 10, title: "Clean water troughs" })],
      }),
    );
    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText(/Clean water troughs/)).toBeInTheDocument();
  });
});
