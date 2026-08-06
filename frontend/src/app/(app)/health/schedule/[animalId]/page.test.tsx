/**
 * Vaccination schedule page (/health/schedule/[animalId]): header link, row
 * rendering (timing notes, date-or-dash cells), status badge variants
 * (DONE/OVERDUE/UPCOMING/unknown), empty state, invalid-id guard, RBAC, and
 * the server-error detail.
 */

import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ScheduleRowOut } from "@/api/generated/models";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import VaccinationSchedulePage from "./page";

const paramsMock: { animalId: string } = { animalId: "7" };

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/health/schedule/7",
  useSearchParams: () => new URLSearchParams(),
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
    expect(await screen.findByText("DONE")).toBeInTheDocument();
    expect(screen.getByText("OVERDUE")).toBeInTheDocument();
    expect(screen.getByText("UPCOMING")).toBeInTheDocument();
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
    server.use(
      http.get("/api/health/schedule/:animalId", () =>
        HttpResponse.json({ detail: "no such animal" }, { status: 404 }),
      ),
    );
    renderWithProviders(<VaccinationSchedulePage />);
    expect(await screen.findByText("no such animal")).toBeInTheDocument();
  });

  it.each(["abc", "0", "-3", "2.5"])(
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

  it("links back to the health log and the add-event entry", async () => {
    renderWithProviders(<VaccinationSchedulePage />);
    await screen.findByText("PPR");
    expect(screen.getByRole("link", { name: "Back to health log" })).toHaveAttribute(
      "href",
      "/health",
    );
    expect(screen.getByRole("link", { name: "+ Add event" })).toHaveAttribute(
      "href",
      "/health",
    );
  });
});
