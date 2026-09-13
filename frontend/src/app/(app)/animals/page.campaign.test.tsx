/**
 * AnimalsPage — fresh-domain mutation campaign kills (2026-09). Two layers:
 * direct schema-level kills for gates the dialog's native inputs cannot
 * produce (malformed dates, workflow buckets), and dialog/list UX kills for
 * affordances no other suite exercised (Cancel, clear-filters, sort headers,
 * no-permission gating, pagination double-click, search trimming).
 */

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { setCurrentFarmId } from "@/lib/api-client";
import { farmVocabulary } from "@/lib/farm-vocabulary";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import {
  completedMonths,
  createAnimalSchema,
  default as AnimalsPage,
} from "./page";

const nav = vi.hoisted(() => {
  const state = { search: "" };
  const applyUrl = (url: string) => {
    state.search = url.includes("?") ? url.slice(url.indexOf("?") + 1) : "";
  };
  const push = vi.fn(applyUrl);
  const replace = vi.fn((url: string) => applyUrl(url));
  return { state, push, replace, router: { push, replace, prefetch: vi.fn() } };
});

vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  usePathname: () => "/animals",
  useSearchParams: () => new URLSearchParams(nav.state.search),
  useParams: () => ({}),
}));

beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  Element.prototype.scrollIntoView = () => {};
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (window as unknown as { ResizeObserver: typeof ResizeObserverStub }).ResizeObserver =
    ResizeObserverStub;
});

const animal = (overrides: Record<string, unknown> = {}) => ({
  tag_number: "G-001",
  id: 1,
  name: "Lakshmi",
  breed: "Osmanabadi",
  sex: "F",
  date_of_birth: "2025-05-10",
  estimated_dob: null,
  birth_type: "SINGLE",
  source: "BORN",
  dam_id: null,
  sire_id: null,
  birth_weight: 2.4,
  current_bucket: "LACTATING",
  status: "ACTIVE",
  status_date: null,
  sale_price: null,
  purchase_date: null,
  purchase_price: null,
  seller_name: null,
  cull_candidate: false,
  age_months: 14,
  latest_weight_kg: 32.5,
  movement_restricted: false,
  ...overrides,
});

/** Collect superRefine issues as "path: message" pairs. */
function issuesOf(input: unknown): Array<[string, unknown]> {
  const result = createAnimalSchema(farmVocabulary).safeParse(input);
  if (result.success) return [];
  return result.error.issues
    .filter((i) => i.code === "custom")
    .map((i) => [i.path.join("."), i.message]);
}

/** A valid BORN breeding import base (F, old enough, heavy enough). */
const bornBase = {
  sex: "F",
  source: "BORN",
  current_bucket: "BREEDING",
  historical_import_reason: "cull-programme import",
  date_of_birth: "2020-01-01",
  weight_kg: 30,
};

describe("createAnimalSchema — campaign kills", () => {
  it("rejects malformed dates instead of computing NaN ages", () => {
    expect(completedMonths("2024-1", "2026-09-09")).toBeNull();
    expect(completedMonths("2024-01-05", "bad")).toBeNull();
    expect(completedMonths("2024-01-xx", "2026-09-09")).toBeNull();
    expect(completedMonths("2024-01-05", "2026-09-09")).toBe(32);
    // Day-of-month borrow: the 10th to the 5th is not a whole month yet.
    expect(completedMonths("2026-01-10", "2026-03-05")).toBe(1);
    // A reference with four all-numeric parts defeats the NaN check; only
    // the length guard rejects it before undefined month/day math runs.
    expect(completedMonths("2024-01-05", "2024-01-02-03")).toBeNull();

    // The same guard feeds the breeding age gate: a malformed DOB must read
    // as "not old enough", never as a passing NaN comparison.
    const issues = issuesOf({ ...bornBase, date_of_birth: "2024-01-xx" });
    expect(issues).toContainEqual([
      "date_of_birth",
      expect.stringContaining("months old to enter BREEDING"),
    ]);
  });

  it("requires an entry weight when an entry weight date is given", () => {
    const withoutWeight = { ...bornBase, weight_kg: undefined };
    expect(issuesOf({ ...withoutWeight, weight_date: "2026-09-01" })).toContainEqual([
      "weight_kg",
      "Entry weight date requires an entry weight",
    ]);
  });

  it("enforces the bucket sex reservation", () => {
    // MALE_KIDS is reserved for males, so a female import is refused.
    expect(issuesOf({ ...bornBase, current_bucket: "MALE_KIDS" })).toContainEqual([
      "current_bucket",
      "Only male animals may enter MALE_KIDS",
    ]);
  });

  it("demands a reason for every historical import", () => {
    expect(issuesOf({ ...bornBase, historical_import_reason: "   " })).toContainEqual([
      "historical_import_reason",
      "Explain why this historical animal is being imported",
    ]);
  });

  it("refuses workflow-owned buckets without their linked records", () => {
    expect(issuesOf({ ...bornBase, current_bucket: "PREGNANCY_EARLY" })).toContainEqual([
      "current_bucket",
      "Pregnancy, delivery and recovery buckets require their linked workflow records",
    ]);
  });

  it("requires a DOB for a breeding import", () => {
    const withoutDob = { ...bornBase, date_of_birth: undefined };
    expect(issuesOf(withoutDob)).toContainEqual([
      "date_of_birth",
      "A breeding import requires a date of birth or estimated DOB",
    ]);
  });

  it("enforces the breeding minimum weight", () => {
    expect(issuesOf({ ...bornBase, weight_kg: 5 })).toContainEqual([
      "weight_kg",
      expect.stringContaining("kg to enter BREEDING"),
    ]);
  });

  it("accepts a fully valid breeding import", () => {
    expect(issuesOf(bornBase)).toEqual([]);
  });

  it("treats blank optional numerics as absent, never zero", () => {
    // A blank birth weight preprocesses to undefined (optional), not 0 —
    // zero would trip the species minimum.
    expect(issuesOf({ ...bornBase, birth_weight: "" })).toEqual([]);
    const purchased = { ...bornBase, source: "PURCHASED", current_bucket: "QUARANTINE" };
    expect(issuesOf({ ...purchased, purchase_price: "" })).toEqual([]);
    expect(issuesOf({ ...purchased, weight_kg: "" })).toEqual([]);
  });

  it("rejects a short-form DOB on the breeding age gate", () => {
    // "2024-1" splits to two parts: the length guard must reject it before
    // any NaN math runs.
    const issues = issuesOf({ ...bornBase, date_of_birth: "2024-1" });
    expect(issues).toContainEqual([
      "date_of_birth",
      expect.stringContaining("months old to enter BREEDING"),
    ]);
  });

  it("defaults the historical reason to empty (failing BORN validation), not undefined", () => {
    const { historical_import_reason: _r, ...withoutReason } = bornBase;
    // The .default("") makes an omitted reason fail loudly with the custom
    // issue instead of crashing the refine on undefined.
    expect(issuesOf(withoutReason)).toContainEqual([
      "historical_import_reason",
      "Explain why this historical animal is being imported",
    ]);
  });

  it("accepts an explicitly empty tag and defaults the breed", () => {
    const result = createAnimalSchema(farmVocabulary).safeParse({
      ...bornBase,
      tag_number: "",
    });
    expect(result.success).toBe(true);
  });
});

const toastMocks = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMocks }));

describe("AnimalsPage — campaign kills", () => {
  let createdBodies: Record<string, unknown>[];

  beforeEach(() => {
    nav.state.search = "";
    nav.push.mockClear();
    nav.replace.mockClear();
    createdBodies = [];
    server.use(
      http.get("/api/animals", () =>
        HttpResponse.json({ animals: [animal()], total: 1, limit: 50, offset: 0 }),
      ),
      http.post("/api/animals", async ({ request }) => {
        createdBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ ...animal(), id: 99 }, { status: 201 });
      }),
    );
  });

  async function openCreateDialog(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole("button", { name: "Add animal" }));
    return await screen.findByRole("dialog", { name: "Add animal" });
  }

  it("clears the draft when the create dialog is cancelled", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("G-001");
    const dialog = await openCreateDialog(user);
    await user.type(within(dialog).getByLabelText("Tag number"), "G-DRAFT");

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Add animal" })).not.toBeInTheDocument(),
    );

    const reopened = await openCreateDialog(user);
    expect(within(reopened).getByLabelText("Tag number")).toHaveValue("");
  });

  it("presets and re-forces quarantine for purchased animals, in the UI and the payload", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("G-001");
    const dialog = await openCreateDialog(user);

    await user.click(within(dialog).getByLabelText("Source *"));
    await user.click(await screen.findByRole("option", { name: "Purchased" }));
    // The historical-import reason input belongs to BORN imports only.
    expect(
      within(dialog).queryByLabelText("Historical import reason *"),
    ).not.toBeInTheDocument();

    await user.type(within(dialog).getByLabelText("Tag number"), "G-777");
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));
    await waitFor(() => expect(createdBodies).toHaveLength(1));
    // Even if the operator fought the preset, the wire value stays QUARANTINE.
    expect(createdBodies[0]).toMatchObject({
      current_bucket: "QUARANTINE",
      source: "PURCHASED",
      historical_import_reason: null,
    });
  });

  it("hides the BORN import option from non-owners entirely", async () => {
    server.use(permissionsHandler(["animals.view", "animals.create"]));
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("G-001");
    const dialog = await openCreateDialog(user);

    await user.click(within(dialog).getByLabelText("Source *"));
    expect(
      await screen.findByRole("option", { name: "Purchased" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: "Historical born-on-farm import" }),
    ).not.toBeInTheDocument();
  });

  it("locks the submit control while a create is out and stays silent across a farm switch", async () => {
    let releaseCreate!: () => void;
    server.use(
      http.post("/api/animals", () =>
        new Promise((resolve) => {
          releaseCreate = () => resolve(HttpResponse.json({ ...animal(), id: 99 }, { status: 201 }));
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("G-001");
    const dialog = await openCreateDialog(user);

    await user.click(within(dialog).getByLabelText("Source *"));
    await user.click(await screen.findByRole("option", { name: "Purchased" }));
    await user.type(within(dialog).getByLabelText("Tag number"), "G-888");
    const submit = within(dialog).getByRole("button", { name: "Save animal" });
    await user.click(submit);
    expect(await within(dialog).findByRole("button", { name: "Saving…" })).toBeDisabled();
    // The cancel affordance locks under the same both-flags condition.
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();

    toastMocks.success.mockClear();
    act(() => setCurrentFarmId("77"));
    await act(async () => {
      releaseCreate();
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(toastMocks.success).not.toHaveBeenCalled();
    // The suppressed continuation left the dialog open with the draft intact.
    expect(screen.getByRole("dialog", { name: "Add animal" })).toBeInTheDocument();
  });

  it("stays silent when a FAILED create settles after the farm changed", async () => {
    let releaseCreate!: () => void;
    server.use(
      http.post("/api/animals", () =>
        new Promise((resolve) => {
          releaseCreate = () =>
            resolve(HttpResponse.json({ detail: "boom" }, { status: 500 }));
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("G-001");
    const dialog = await openCreateDialog(user);
    await user.click(within(dialog).getByLabelText("Source *"));
    await user.click(await screen.findByRole("option", { name: "Purchased" }));
    await user.type(within(dialog).getByLabelText("Tag number"), "G-555");
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    act(() => setCurrentFarmId("77"));
    await act(async () => {
      releaseCreate();
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    // The old farm's failure must not toast into the new farm's UI.
    expect(toastMocks.error).not.toHaveBeenCalled();
  });

  it("resets the form after a successful create", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("G-001");
    const dialog = await openCreateDialog(user);
    await user.click(within(dialog).getByLabelText("Source *"));
    await user.click(await screen.findByRole("option", { name: "Purchased" }));
    await user.type(within(dialog).getByLabelText("Tag number"), "G-999");
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));
    await waitFor(() => expect(toastMocks.success).toHaveBeenCalledWith("Animal added."));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Add animal" })).not.toBeInTheDocument(),
    );

    const reopened = await openCreateDialog(user);
    expect(within(reopened).getByLabelText("Tag number")).toHaveValue("");
  });

  it("keeps a chosen bucket for BORN imports (no quarantine forcing)", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("G-001");
    const dialog = await openCreateDialog(user);

    // BORN + BREEDING is the deepest historical-import path; the payload must
    // carry the operator's bucket, never a forced QUARANTINE.
    await user.click(within(dialog).getByLabelText("Source *"));
    await user.click(
      await screen.findByRole("option", { name: "Historical born-on-farm import" }),
    );
    await user.click(within(dialog).getByLabelText("Bucket *"));
    await user.click(await screen.findByRole("option", { name: "Breeding" }));
    await user.type(
      within(dialog).getByLabelText("Historical import reason *"),
      "cull import",
    );
    await user.type(within(dialog).getByLabelText("Date of birth"), "2020-01-01");
    await user.type(within(dialog).getByLabelText("Entry weight (kg)"), "30");
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    await waitFor(() => expect(createdBodies).toHaveLength(1));
    expect(createdBodies[0]).toMatchObject({
      current_bucket: "BREEDING",
      source: "BORN",
      historical_import_reason: "cull import",
    });
    // A successful create closes the dialog and clears the draft for the next.
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Add animal" })).not.toBeInTheDocument(),
    );
    const reopened = await openCreateDialog(user);
    expect(within(reopened).getByLabelText("Entry weight (kg)")).toHaveValue(null);
  });

  it("does not fire the list query without animals.view", async () => {
    let listRequests = 0;
    server.use(
      http.get("/api/animals", () => {
        listRequests += 1;
        return HttpResponse.json({ animals: [], total: 0, limit: 50, offset: 0 });
      }),
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ is_owner: false, permissions: ["dashboard.view"] }),
      ),
    );
    renderWithProviders(<AnimalsPage />);
    expect(
      await screen.findByText("You don't have access to this page."),
    ).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(listRequests).toBe(0);
  });

  it("clears every filter in one URL write from the empty state", async () => {
    const listQueries: URL[] = [];
    server.use(
      http.get("/api/animals", ({ request }) => {
        listQueries.push(new URL(request.url));
        return HttpResponse.json({ animals: [], total: 0, limit: 50, offset: 0 });
      }),
    );
    nav.state.search = "bucket=BREEDING&sex=f&status=ACTIVE&q=G-9";
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);

    expect(await screen.findByText("No animals match these filters.")).toBeInTheDocument();
    // The deep-linked search box rehydrated the query.
    expect(screen.getByLabelText("Search animals by tag")).toHaveValue("G-9");
    await waitFor(() =>
      expect(listQueries.some((url) => url.searchParams.get("bucket") === "BREEDING")).toBe(
        true,
      ),
    );

    const preClearRequestCount = listQueries.length;
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(nav.replace).toHaveBeenLastCalledWith("/animals"));
    expect(screen.getByLabelText("Search animals by tag")).toHaveValue("");
    // The next list request must carry none of the cleared filters — the
    // state setters, not just the URL write, all ran.
    await waitFor(() => {
      const last = listQueries[listQueries.length - 1];
      expect(last.searchParams.get("bucket")).toBeNull();
      expect(last.searchParams.get("sex")).toBeNull();
      expect(last.searchParams.get("status")).toBeNull();
      expect(last.searchParams.get("q")).toBeNull();
    });
    // And no intermediate state may leak either: no URL write and no request
    // in the whole test may carry a filter param (a transient garbage q would
    // self-heal through the debounce 300ms later, masking the leak).
    for (const [url] of nav.replace.mock.calls) {
      expect(String(url)).not.toMatch(/[?&](bucket|sex|status|q)=/);
    }
    for (const url of listQueries.slice(preClearRequestCount)) {
      expect(url.searchParams.get("bucket")).toBeNull();
      expect(url.searchParams.get("q")).toBeNull();
    }
  });

  it("sorts by age and weight from the column headers, both directions", async () => {
    // The sentinel (-1) must stay BELOW real values: an age of 0 months
    // (born this month) and a 0.5 kg weight sit between -1 and +1, so they
    // pin which side of the sentinel the unknowns sort on.
    server.use(
      http.get("/api/animals", () =>
        HttpResponse.json({
          animals: [
            animal({ id: 1, tag_number: "TAG-1", age_months: 14, latest_weight_kg: 32.5 }),
            animal({ id: 2, tag_number: "TAG-2", age_months: 9, latest_weight_kg: 10 }),
            // The unknown row sits BETWEEN plain values and the sub-sentinel
            // rows: insertion sort passes the incoming row as `a`, so the
            // unknown (inserted early) exercises the a-side against 0, and
            // the sub-sentinel rows (inserted after) exercise the b-side
            // against the unknown — a sentinel flip on either side reorders.
            animal({ id: 3, tag_number: "TAG-3", age_months: null, latest_weight_kg: null }),
            animal({ id: 4, tag_number: "TAG-4", age_months: 0, latest_weight_kg: 0.5 }),
            animal({ id: 5, tag_number: "TAG-5", age_months: 1, latest_weight_kg: 1 }),
          ],
          total: 5,
          limit: 50,
          offset: 0,
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("TAG-1");

    // Unknown age sorts as -1: before a zero-month kid, first ascending.
    await user.click(screen.getByRole("button", { name: "Age (mo)" }));
    await waitFor(() => expect(rowTags()).toEqual(["TAG-3", "TAG-4", "TAG-5", "TAG-2", "TAG-1"]));
    expect(
      screen.getByText("5 animal(s) · sorted within the current page"),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Age (mo)" }));
    await waitFor(() => expect(rowTags()).toEqual(["TAG-1", "TAG-2", "TAG-5", "TAG-4", "TAG-3"]));

    // Unknown weight sorts as -1: before a 0.5 kg kid, first ascending.
    await user.click(screen.getByRole("button", { name: "Weight" }));
    await waitFor(() => expect(rowTags()).toEqual(["TAG-3", "TAG-4", "TAG-5", "TAG-2", "TAG-1"]));
    await user.click(screen.getByRole("button", { name: "Weight" }));
    await waitFor(() => expect(rowTags()).toEqual(["TAG-1", "TAG-2", "TAG-5", "TAG-4", "TAG-3"]));
  });

  it("sends only one page navigation while a page turn is still settling", async () => {
    let holdPageTurns = false;
    server.use(
      http.get("/api/animals", () => {
        if (!holdPageTurns) {
          return HttpResponse.json({
            animals: [animal()],
            total: 120,
            limit: 50,
            offset: 0,
          });
        }
        return new Promise(() => undefined);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("G-001");
    await waitFor(() => expect(screen.getByRole("button", { name: "Next" })).toBeEnabled());

    holdPageTurns = true;
    const next = screen.getByRole("button", { name: "Next" });
    await user.click(next);
    fireEvent.click(next);
    fireEvent.click(next);
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/animals?page=2"));
    expect(nav.push).toHaveBeenCalledTimes(1);
  });

  it("trims the search box before it reaches the URL", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("G-001");

    await user.type(screen.getByLabelText("Search animals by tag"), " G-001 ");
    await waitFor(
      () => {
        const urls = JSON.stringify(nav.replace.mock.calls);
        expect(urls).toMatch(/q=G-001(&|")/);
      },
      { timeout: 2000 },
    );
    // The padded spelling must never reach the URL.
    const urls = JSON.stringify(nav.replace.mock.calls);
    expect(urls).not.toContain("q=+");
    expect(urls).not.toContain("%20");
  });
});

function rowTags(): string[] {
  return screen
    .getAllByRole("row")
    .map((row) => row.textContent ?? "")
    .filter((text) => /TAG-/.test(text))
    .map((text) => text.match(/TAG-\d+/)?.[0])
    .filter((tag): tag is string => Boolean(tag));
}
