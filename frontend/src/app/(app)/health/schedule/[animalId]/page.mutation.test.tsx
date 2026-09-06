/**
 * Mutation-hardening for the vaccination schedule page: the per-status
 * switch (each known status must carry its own icon and label, not the
 * humanized fallback), the permissions dead-end retry, and the empty-state
 * record CTA (RBAC gate, deep-link href, and its small-button styling).
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ScheduleRowOut } from "@/api/generated/models";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import VaccinationSchedulePage from "./page";

const paramsMock: { animalId: string; search: string } = { animalId: "7", search: "" };

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/health/schedule/7",
  useSearchParams: () => new URLSearchParams(paramsMock.search),
  useParams: () => paramsMock,
}));

function makeRow(overrides: Partial<ScheduleRowOut>): ScheduleRowOut {
  return {
    template_id: 1,
    template_name: "PPR",
    timing_note: null,
    first_due: "2026-02-01",
    booster_due: null,
    last_done: "2026-02-03",
    next_due: "2027-02-01",
    status: "DONE",
    ...overrides,
  };
}

const ROWS: ScheduleRowOut[] = [
  makeRow({ template_id: 1, template_name: "PPR", status: "DONE" }),
  makeRow({ template_id: 2, template_name: "ET + TT", status: "OVERDUE" }),
  makeRow({
    template_id: 3,
    template_name: "HS",
    status: "UPCOMING",
    next_due: "2026-09-01",
  }),
];

describe("VaccinationSchedulePage (mutation hardening)", () => {
  beforeEach(() => {
    paramsMock.animalId = "7";
    paramsMock.search = "";
    server.use(
      http.get("/api/health/schedule/:animalId", () =>
        HttpResponse.json({ animal_id: 7, rows: ROWS }),
      ),
    );
  });

  // Each known status renders through its own switch arm, which stamps a
  // leading icon (Syringe for DONE, CalendarClock otherwise) ahead of the
  // label. The default arm humanizes without any icon, so "case 'DONE'"
  // collapsing to the fallback is only observable through the icon.
  it("pairs every known status with its own icon, not the fallback badge", async () => {
    renderWithProviders(<VaccinationSchedulePage />);
    const doneRow = (await screen.findByText("PPR")).closest("tr")!;
    const doneBadge = within(doneRow).getByText("Done");
    expect(doneBadge.querySelector("svg.lucide-syringe")).not.toBeNull();
    expect(doneBadge.querySelector("svg.lucide-calendar-clock")).toBeNull();

    const overdueBadge = within(
      (await screen.findByText("ET + TT")).closest("tr")!,
    ).getByText("Overdue");
    expect(overdueBadge.querySelector("svg.lucide-calendar-clock")).not.toBeNull();
    expect(overdueBadge.querySelector("svg.lucide-syringe")).toBeNull();

    const upcomingBadge = within(screen.getByText("HS").closest("tr")!).getByText("Upcoming");
    expect(upcomingBadge.querySelector("svg.lucide-calendar-clock")).not.toBeNull();
    expect(upcomingBadge.querySelector("svg.lucide-syringe")).toBeNull();
  });

  it("retries the permission fetch from the permissions dead end", async () => {
    let attempts = 0;
    server.use(
      http.get("/api/auth/permissions", () => {
        attempts += 1;
        return attempts === 1
          ? HttpResponse.json({ detail: "permissions unavailable" }, { status: 503 })
          : HttpResponse.json({ is_owner: true, permissions: ["health.view"] });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<VaccinationSchedulePage />);

    expect(
      await screen.findByText("Could not load your permissions — refresh the page to try again."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry permissions" }));

    expect(await screen.findByText("PPR")).toBeInTheDocument();
    expect(attempts).toBe(2);
  });

  // ---------- empty-state record CTA ----------

  function emptySchedule() {
    server.use(
      http.get("/api/health/schedule/:animalId", () =>
        HttpResponse.json({ animal_id: 7, rows: [] }),
      ),
    );
  }

  it("offers a record CTA that deep-links this animal back into /health/new", async () => {
    emptySchedule();
    renderWithProviders(<VaccinationSchedulePage />);
    expect(
      await screen.findByText("No vaccination templates apply to this animal."),
    ).toBeInTheDocument();

    const cta = screen.getByRole("link", { name: "Record a health event" });
    const url = new URL(cta.getAttribute("href") ?? "", "https://goatfarm.test");
    expect(url.pathname).toBe("/health/new");
    expect(url.searchParams.get("animal_id")).toBe("7");
    expect(url.searchParams.get("returnTo")).toBe(
      "/health/schedule/7?returnTo=%2Fhealth%3Fschedule_animal_id%3D7",
    );
    // The CTA is the small secondary action inside the empty state.
    expect(cta).toHaveClass("text-[0.8rem]", "px-3");
    expect(cta).not.toHaveClass("px-3.5");
  });

  it("withholds the record CTA from a read-only health viewer", async () => {
    emptySchedule();
    server.use(permissionsHandler(["health.view"]));
    renderWithProviders(<VaccinationSchedulePage />);

    expect(
      await screen.findByText("No vaccination templates apply to this animal."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Record a health event" }),
    ).not.toBeInTheDocument();
  });
});
