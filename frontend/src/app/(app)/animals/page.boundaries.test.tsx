/**
 * Branch coverage for the animals list + Add-animal dialog that the other
 * animals suites leave open: the page clamp against the backend's offset
 * ceiling, the entry-weight-date boundary and its inline error, the BREEDING
 * entry weight requirement, the eligibility hint beside the bucket picker
 * (shown only for a historical import into BREEDING, worded per sex), the
 * audit-reason aria-invalid contract, the birth-type trigger label, the
 * form lock raised as soon as a submit starts, and the provenance fallback
 * when owner rights disappear mid-edit.
 */

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { addDays, farmToday } from "@/lib/format";
import { ALL_PERMISSIONS, server } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import AnimalsPage from "./page";

const nav = vi.hoisted(() => {
  const state = { search: "" };
  const applyUrl = (url: string) => {
    state.search = url.includes("?") ? url.slice(url.indexOf("?") + 1) : "";
  };
  const push = vi.fn(applyUrl);
  const replace = vi.fn(applyUrl);
  return { state, push, replace, router: { push, replace, prefetch: vi.fn() } };
});

vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  usePathname: () => "/animals",
  useSearchParams: () => new URLSearchParams(nav.state.search),
  useParams: () => ({}),
}));

beforeAll(() => {
  // jsdom lacks the pointer-capture / observer APIs the Select relies on.
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

const ANIMAL = {
  id: 1,
  tag_number: "G-001",
  name: "Lakshmi",
  breed: "Osmanabadi",
  sex: "F",
  date_of_birth: "2025-05-10",
  estimated_dob: null,
  birth_type: "TWIN",
  source: "BORN",
  dam_id: null,
  sire_id: null,
  birth_weight: 2.4,
  current_bucket: "Resting",
  status: "ACTIVE",
  status_date: null,
  sale_price: null,
  purchase_date: null,
  purchase_price: null,
  seller_name: null,
  cull_candidate: false,
  notes: null,
  created_at: "2026-01-01T05:30:00Z",
  age_months: 14,
  latest_weight_kg: 32.5,
};

type User = ReturnType<typeof userEvent.setup>;

async function pickOption(user: User, trigger: HTMLElement, name: string) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

function setDate(input: HTMLElement, value: string) {
  fireEvent.change(input, { target: { value } });
}

describe("AnimalsPage branches", () => {
  let seenParams: URLSearchParams[];
  let postCalls: number;
  let postBody: Record<string, unknown> | null;

  beforeEach(() => {
    nav.state.search = "";
    nav.push.mockClear();
    nav.replace.mockClear();
    seenParams = [];
    postCalls = 0;
    postBody = null;
    server.use(
      http.get("/api/animals", ({ request }) => {
        seenParams.push(new URL(request.url).searchParams);
        return HttpResponse.json({ animals: [ANIMAL], total: 1 });
      }),
      http.post("/api/animals", async ({ request }) => {
        postCalls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...ANIMAL, id: 2 }, { status: 201 });
      }),
    );
  });

  async function renderLoaded() {
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");
  }

  async function openCreateDialog(user: User) {
    await user.click(screen.getByRole("button", { name: "Add animal" }));
    return await screen.findByRole("dialog");
  }

  /** Dialog switched to the owner-only historical import, audit reason filled. */
  async function openImportDialog(user: User, reason = "Paper herd register import") {
    const dialog = await openCreateDialog(user);
    await pickOption(
      user,
      within(dialog).getAllByRole("combobox")[1],
      "Historical born-on-farm import",
    );
    if (reason) {
      await user.type(within(dialog).getByLabelText("Historical import reason *"), reason);
    }
    return dialog;
  }

  it("clamps a page deep link to the last offset the list endpoint accepts", async () => {
    // Mirrors backend/app/schemas/common.py MAX_PAGE_OFFSET: offset=1_000_000 (now clamped to the 10_000 ceiling — P2-16)
    // is the largest the endpoint answers — anything beyond is a 422 the page
    // could never self-heal from.
    nav.state.search = "page=99999999";
    renderWithProviders(<AnimalsPage />);

    await waitFor(() => expect(seenParams.length).toBeGreaterThanOrEqual(1));
    expect(seenParams[0].get("limit")).toBe("50");
    expect(seenParams[0].get("offset")).toBe("10000");

    // One page of results: the clamped page is out of range and self-heals.
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/animals"));
    expect(await screen.findByText("1 animal(s)")).toBeInTheDocument();
  });

  it("rejects a future entry-weight date beside that field instead of POSTing", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openImportDialog(user);
    setDate(within(dialog).getByLabelText("Entry weight (kg)"), "30");
    const weightDate = within(dialog).getByLabelText("Entry weight date");
    expect(weightDate).toHaveAttribute("max", farmToday());
    setDate(weightDate, addDays(farmToday(), 1));
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    const field = weightDate.closest("div") as HTMLElement;
    expect(await within(field).findByText("Date can't be in the future")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("accepts an entry-weight date on the farm's own calendar day", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openImportDialog(user);
    setDate(within(dialog).getByLabelText("Entry weight (kg)"), "30");
    setDate(within(dialog).getByLabelText("Entry weight date"), farmToday());
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toMatchObject({ weight_kg: 30, weight_date: farmToday() });
    expect(
      within(dialog).queryByText("Date can't be in the future"),
    ).not.toBeInTheDocument();
  });

  it("requires an entry weight for a BREEDING import when the field is left blank", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openImportDialog(user);
    await pickOption(user, within(dialog).getByLabelText("Bucket *"), "Breeding");
    setDate(within(dialog).getByLabelText("Date of birth"), "2020-01-01");
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    expect(
      await within(dialog).findByText("Entry weight must be at least 22 kg to enter BREEDING"),
    ).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("spells out the doe BREEDING minimums next to the bucket picker", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openImportDialog(user, "");
    await pickOption(user, within(dialog).getByLabelText("Bucket *"), "Breeding");

    expect(
      within(dialog).getByText(
        "BREEDING imports require a doe of at least 12 months and 22 kg.",
      ),
    ).toBeInTheDocument();
  });

  it("spells out the stricter buck BREEDING minimums when Male is selected", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openCreateDialog(user);
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], "Male");
    await pickOption(
      user,
      within(dialog).getAllByRole("combobox")[1],
      "Historical born-on-farm import",
    );
    await pickOption(user, within(dialog).getByLabelText("Bucket *"), "Breeding");

    expect(
      within(dialog).getByText(
        "BREEDING imports require a buck of at least 12 months and 25 kg.",
      ),
    ).toBeInTheDocument();
  });

  it("keeps the BREEDING hint out of purchases and non-breeding imports", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openCreateDialog(user);
    // Purchased animals are quarantine-only, so the hint never applies.
    expect(within(dialog).queryByText(/BREEDING imports require/)).not.toBeInTheDocument();

    await pickOption(
      user,
      within(dialog).getAllByRole("combobox")[1],
      "Historical born-on-farm import",
    );
    expect(within(dialog).getByLabelText("Bucket *")).toHaveTextContent("Quarantine");
    expect(within(dialog).queryByText(/BREEDING imports require/)).not.toBeInTheDocument();
  });

  it("marks the historical import reason invalid only once it has failed validation", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openImportDialog(user, "");
    const reason = within(dialog).getByLabelText("Historical import reason *");
    expect(reason).not.toHaveAttribute("aria-invalid");

    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    await waitFor(() => expect(reason).toHaveAttribute("aria-invalid", "true"));
    expect(postCalls).toBe(0);
  });

  it("shows the chosen birth type on the closed picker and sends it", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openImportDialog(user);
    const birthType = within(dialog).getByLabelText("Birth type");
    expect(birthType).toHaveTextContent("—");

    await pickOption(user, birthType, "Twin");
    expect(birthType).toHaveTextContent("Twin");

    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));
    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toMatchObject({ birth_type: "TWIN" });
  });

  it("disables the dialog fields the moment a submit starts", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const dialog = await openCreateDialog(user);
    const tag = within(dialog).getByLabelText("Tag number");
    await user.type(tag, "G-777");

    fireEvent.submit(dialog.querySelector("form") as HTMLFormElement);
    // Validation is async: the form must already be locked while it runs, not
    // only once the request itself is in flight.
    expect(tag).toBeDisabled();

    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toMatchObject({ tag_number: "G-777" });
  });

  it("reverts a historical import to Purchased when owner rights are revoked mid-edit", async () => {
    const user = userEvent.setup();
    const queryClient = createTestQueryClient();
    renderWithProviders(<AnimalsPage />, queryClient);
    await screen.findByText("1 animal(s)");
    const dialog = await openImportDialog(user, "Import started while still the owner");

    // Ownership is revoked and the permissions query refreshes underneath the
    // open dialog: only owners may file a historical born-on-farm import.
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ is_owner: false, permissions: ALL_PERMISSIONS }),
      ),
    );
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["/api/auth/permissions"] });
    });

    await waitFor(() =>
      expect(
        within(dialog).queryByLabelText("Historical import reason *"),
      ).not.toBeInTheDocument(),
    );
    expect(within(dialog).getAllByRole("combobox")[1]).toHaveTextContent("Purchased");
    expect(within(dialog).getByLabelText("Purchase price (₹)")).toBeInTheDocument();
  });
});
