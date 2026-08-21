/**
 * Vaccination schedule page (/health/schedule/[animalId]): header link, row
 * rendering (timing notes, date-or-dash cells), status badge variants
 * (DONE/OVERDUE/UPCOMING/unknown), empty state, invalid-id guard, RBAC, and
 * the server-error detail.
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
  makeRow({
    template_id: 2,
    template_name: "ET + TT",
    timing_note: "4–6 weeks before kidding",
    status: "OVERDUE",
    last_done: null,
    next_due: "2026-07-01",
  }),
  makeRow({
    template_id: 3,
    template_name: "HS",
    status: "UPCOMING",
    first_due: "2026-09-01",
    booster_due: "2026-09-22",
    last_done: null,
    next_due: null,
  }),
  makeRow({ template_id: 4, template_name: "Custom", status: "SOMETHING_ELSE" }),
];

describe("VaccinationSchedulePage", () => {
  let requestedIds: string[];

  beforeEach(() => {
    paramsMock.animalId = "7";
    paramsMock.search = "";
    requestedIds = [];
    server.use(
      http.get("/api/health/schedule/:animalId", ({ params }) => {
        requestedIds.push(String(params.animalId));
        return HttpResponse.json({ animal_id: 7, rows: ROWS });
      }),
    );
  });

  it("fetches the schedule for the route id and renders the header link", async () => {
    renderWithProviders(<VaccinationSchedulePage />);
    const link = await screen.findByRole("link", { name: "Animal #7" });
    expect(link).toHaveAttribute("href", "/animals/7");
    expect(requestedIds).toEqual(["7"]);
  });

  it("renders rows with formatted dates and timing notes", async () => {
    renderWithProviders(<VaccinationSchedulePage />);
    const row = (await screen.findByText("PPR")).closest("tr")!;
    expect(within(row).getByText("1 Feb 2026")).toBeInTheDocument();
    expect(within(row).getByText("3 Feb 2026")).toBeInTheDocument();
    expect(within(row).getByText("1 Feb 2027")).toBeInTheDocument();
    expect(
      within((await screen.findByText("ET + TT")).closest("tr")!).getByText(
        "4–6 weeks before kidding",
      ),
    ).toBeInTheDocument();
  });

  it("renders a dash for null date cells", async () => {
    renderWithProviders(<VaccinationSchedulePage />);
    const hsRow = (await screen.findByText("HS")).closest("tr")!;
    // last_done and next_due are null on the HS row.
    expect(within(hsRow).getAllByText("—")).toHaveLength(2);
  });

  it("renders a status badge per row", async () => {
    renderWithProviders(<VaccinationSchedulePage />);
    expect(await screen.findByText("DONE")).toHaveClass("bg-emerald-100");
    const overdue = screen.getByText("OVERDUE");
    const upcoming = screen.getByText("UPCOMING");
    expect(overdue).toHaveClass("bg-red-100");
    expect(upcoming).toHaveClass("bg-amber-100");
    expect(overdue.closest("tr")).toHaveClass("bg-red-50");
    expect(upcoming.closest("tr")).toHaveClass("bg-amber-50");
    expect(screen.getByText("DONE").closest("tr")).not.toHaveClass("bg-red-50", "bg-amber-50");
    expect(screen.getByText("SOMETHING_ELSE")).toBeInTheDocument();
  });

  it("shows the empty state when no templates apply", async () => {
    server.use(
      http.get("/api/health/schedule/:animalId", () =>
        HttpResponse.json({ animal_id: 7, rows: [] }),
      ),
    );
    renderWithProviders(<VaccinationSchedulePage />);
    expect(
      await screen.findByText("No vaccination templates apply to this animal."),
    ).toBeInTheDocument();
  });

  it("shows the server error detail when the schedule fails to load", async () => {
    let attempts = 0;
    server.use(
      http.get("/api/health/schedule/:animalId", () => {
        attempts += 1;
        return attempts === 1
          ? HttpResponse.json({ detail: "no such animal" }, { status: 404 })
          : HttpResponse.json({ animal_id: 7, rows: ROWS });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<VaccinationSchedulePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("no such animal");
    await user.click(screen.getByRole("button", { name: "Retry schedule" }));
    expect(await screen.findByText("PPR")).toBeInTheDocument();
    expect(attempts).toBe(2);
  });

  it.each(["abc", "0", "-3", "2.5", "1e2", "9007199254740992"])(
    "rejects the invalid animal id %s without fetching",
    async (bad) => {
      paramsMock.animalId = bad;
      renderWithProviders(<VaccinationSchedulePage />);
      expect(await screen.findByText("Invalid animal id.")).toBeInTheDocument();
      expect(requestedIds).toEqual([]);
    },
  );

  it("blocks the page without health.view", async () => {
    server.use(permissionsHandler(["health.manage"]));
    renderWithProviders(<VaccinationSchedulePage />);
    expect(
      await screen.findByText("You don't have access to this page."),
    ).toBeInTheDocument();
    expect(requestedIds).toEqual([]);
  });

  it("fails closed when permissions cannot be loaded", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ detail: "permissions unavailable" }, { status: 503 }),
      ),
    );
    renderWithProviders(<VaccinationSchedulePage />);

    expect(
      await screen.findByText("Could not load your permissions — refresh the page to try again."),
    ).toBeInTheDocument();
    expect(requestedIds).toEqual([]);
  });

  it("links back to the health log and the add-event entry", async () => {
    renderWithProviders(<VaccinationSchedulePage />);
    await screen.findByText("PPR");
    expect(screen.getByRole("link", { name: "Back to health log" })).toHaveAttribute(
      "href",
      "/health?schedule_animal_id=7",
    );
    const addEventHref = screen.getByRole("link", { name: "+ Add event" }).getAttribute("href");
    const addEventUrl = new URL(addEventHref ?? "", "https://goatfarm.test");
    expect(addEventUrl.pathname).toBe("/health/new");
    expect(addEventUrl.searchParams.get("animal_id")).toBe("7");
    expect(addEventUrl.searchParams.get("returnTo")).toBe(
      "/health/schedule/7?returnTo=%2Fhealth%3Fschedule_animal_id%3D7",
    );
  });

  it("honours a safe originating page in the Back link", async () => {
    paramsMock.search = "?returnTo=%2Ftasks%3Ftab%3Doverdue";
    renderWithProviders(<VaccinationSchedulePage />);
    await screen.findByText("PPR");

    expect(screen.getByRole("link", { name: "Back to health log" })).toHaveAttribute(
      "href",
      "/tasks?tab=overdue",
    );
  });

  it("hides add-event for a read-only health viewer", async () => {
    server.use(permissionsHandler(["health.view"]));
    renderWithProviders(<VaccinationSchedulePage />);
    await screen.findByText("PPR");
    expect(screen.queryByRole("link", { name: "+ Add event" })).not.toBeInTheDocument();
  });
});
