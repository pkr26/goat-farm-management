/**
 * Vaccination schedule page (/health/schedule/[animalId]): header link, row
 * rendering (timing notes, date-or-dash cells), status badge variants
 * (DONE/OVERDUE/UPCOMING/unknown), empty state, invalid-id guard, RBAC, and
 * the server-error detail.
 *
 * Also: the permission-answer wait, multi-digit route ids, the header's
 * title composition with and without animals.view, the Back link's outline
 * styling, and the detail-free ("network died") failure message.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, delay, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ScheduleRowOut } from "@/api/generated/models";
import { ALL_PERMISSIONS, permissionsHandler, server } from "@/test/msw-server";
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
    expect(await screen.findByText("Done")).toHaveClass("bg-success-tint");
    const overdue = screen.getByText("Overdue");
    const upcoming = screen.getByText("Upcoming");
    expect(overdue).toHaveClass("bg-destructive/10");
    expect(upcoming).toHaveClass("bg-warning-tint");
    expect(overdue.closest("tr")).toHaveClass("bg-destructive/[0.04]");
    expect(upcoming.closest("tr")).not.toHaveClass("bg-destructive/[0.04]");
    expect(screen.getByText("Done").closest("tr")).not.toHaveClass("bg-destructive/[0.04]");
    expect(screen.getByText("Something Else")).toBeInTheDocument();
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
    const addEventHref = screen.getByRole("link", { name: "Add event" }).getAttribute("href");
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
    expect(screen.queryByRole("link", { name: "Add event" })).not.toBeInTheDocument();
  });

  // ---------- route id parsing ----------

  it("fetches a multi-digit animal id verbatim", async () => {
    // Herds outgrow single-digit ids: id 42 must reach the API and drive the
    // header link and the Back link, not trip the invalid-id guard.
    paramsMock.animalId = "42";
    server.use(
      http.get("/api/health/schedule/:animalId", ({ params }) => {
        requestedIds.push(String(params.animalId));
        return HttpResponse.json({ animal_id: 42, rows: ROWS });
      }),
    );
    renderWithProviders(<VaccinationSchedulePage />);

    expect(await screen.findByRole("link", { name: "Animal #42" })).toHaveAttribute(
      "href",
      "/animals/42",
    );
    expect(screen.queryByText("Invalid animal id.")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to health log" })).toHaveAttribute(
      "href",
      "/health?schedule_animal_id=42",
    );
    expect(requestedIds).toEqual(["42"]);
  });

  // ---------- permission gate ----------

  it("waits for the permission answer instead of announcing no access", async () => {
    // An unanswered permission query is not evidence of a missing grant: the
    // page must hold its loading placeholder rather than telling an operator
    // with full rights that the schedule is not theirs.
    server.use(
      http.get("/api/auth/permissions", async () => {
        await delay("infinite");
        return HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS });
      }),
    );
    renderWithProviders(<VaccinationSchedulePage />);

    // The page holds its header + skeleton, not a bare "Loading…" line.
    expect(
      await screen.findByRole("heading", { name: "Vaccination schedule" }),
    ).toBeInTheDocument();
    expect(document.querySelector('[data-slot="skeleton"]')).not.toBeNull();
    expect(
      screen.queryByText("You don't have access to this page."),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Invalid animal id.")).not.toBeInTheDocument();
    expect(requestedIds).toEqual([]);
  });

  // ---------- header composition ----------

  it("keeps the title readable around the animal link", async () => {
    renderWithProviders(<VaccinationSchedulePage />);
    await screen.findByRole("link", { name: "Animal #7" });

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      /^Vaccination schedule — Animal #7$/,
    );
  });

  it("names the animal as plain text for a viewer without animals.view", async () => {
    server.use(permissionsHandler(["health.view", "health.manage"]));
    renderWithProviders(<VaccinationSchedulePage />);
    await screen.findByText("PPR");

    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent(/^Vaccination schedule — Animal #7$/);
    expect(within(heading).queryByRole("link")).not.toBeInTheDocument();
  });

  it("styles Back as the secondary action next to the primary add-event button", async () => {
    renderWithProviders(<VaccinationSchedulePage />);
    await screen.findByText("PPR");

    const back = screen.getByRole("link", { name: "Back to health log" });
    expect(back).toHaveClass("border-border", "bg-background");
    expect(back).not.toHaveClass("bg-primary");
    expect(screen.getByRole("link", { name: "Add event" })).toHaveClass("bg-primary");
  });

  // ---------- failure with no server detail ----------

  it("explains a schedule fetch that failed without a server detail", async () => {
    server.use(http.get("/api/health/schedule/:animalId", () => HttpResponse.error()));
    renderWithProviders(<VaccinationSchedulePage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load the vaccination schedule.",
    );
    expect(screen.getByRole("button", { name: "Retry schedule" })).toBeInTheDocument();
  });
});
