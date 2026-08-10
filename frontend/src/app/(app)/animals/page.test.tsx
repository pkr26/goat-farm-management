/**
 * Animals page + Add-animal dialog: the list loads with owner permissions,
 * submitting the dialog with a blank tag number POSTs without one (the
 * server auto-generates it), and a valid submit POSTs the mapped payload
 * (defaults applied, blank optionals → null) and invalidates the list query.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import AnimalsPage from "./page";

const { navState, replaceMock } = vi.hoisted(() => ({
  navState: { search: "" },
  replaceMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/animals",
  useSearchParams: () => new URLSearchParams(navState.search),
  useParams: () => ({}),
}));

const ANIMAL = {
  id: 1,
  tag_number: "G-001",
  name: "Lakshmi",
  breed: "Osmanabadi",
  sex: "F",
  date_of_birth: "2025-05-10",
  estimated_dob: null,
  birth_type: "TWINS",
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
  notes: null,
  created_at: "2026-01-01T05:30:00Z",
  age_months: 14,
  latest_weight_kg: 32.5,
};

describe("AnimalsPage", () => {
  let listCalls: number;
  let lastListFarmHeader: string | null;
  let postCalls: number;
  let postBody: Record<string, unknown> | null;
  let postFarmHeader: string | null;

  beforeEach(() => {
    listCalls = 0;
    lastListFarmHeader = null;
    postCalls = 0;
    postBody = null;
    postFarmHeader = null;
    navState.search = "";
    server.use(
      http.get("/api/animals", ({ request }) => {
        listCalls += 1;
        lastListFarmHeader = request.headers.get("X-Farm-Id");
        return HttpResponse.json({ animals: [ANIMAL], total: 1 });
      }),
      http.post("/api/animals", async ({ request }) => {
        postCalls += 1;
        postFarmHeader = request.headers.get("X-Farm-Id");
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          { ...ANIMAL, id: 2, tag_number: String(postBody.tag_number) },
          { status: 201 },
        );
      }),
    );
  });

  async function renderAndWaitForList() {
    renderWithProviders(<AnimalsPage />);
    // List row proves: permissions resolved as owner, farm selected, GET ran.
    expect(await screen.findByText("G-001")).toBeInTheDocument();
    expect(screen.getByText("1 animal(s)")).toBeInTheDocument();
  }

  it("loads the herd list with the selected farm header", async () => {
    await renderAndWaitForList();
    expect(listCalls).toBe(1);
    expect(lastListFarmHeader).toBe("1");
  });

  it("POSTs without a tag number when it is left blank (server auto-generates it)", async () => {
    const user = userEvent.setup();
    await renderAndWaitForList();

    await user.click(screen.getByRole("button", { name: "Add animal" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText(/tag number/i)).toHaveAttribute(
      "placeholder",
      "Auto-generated if left blank (e.g. G-7KP2D)",
    );
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    // No zod error: a blank tag is valid and is omitted from the payload.
    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).not.toHaveProperty("tag_number");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("POSTs the mapped payload on a valid submit and invalidates the list", async () => {
    const user = userEvent.setup();
    await renderAndWaitForList();

    await user.click(screen.getByRole("button", { name: "Add animal" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/tag number/i), "G-101");
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    await waitFor(() => expect(postCalls).toBe(1));
    expect(postFarmHeader).toBe("1");
    expect(postBody).toMatchObject({
      tag_number: "G-101",
      // Defaults from the dialog's pre-selected selects.
      sex: "F",
      source: "PURCHASED",
      current_bucket: "QUARANTINE",
      breed: "Osmanabadi",
      // Blank optionals are mapped to null.
      name: null,
      date_of_birth: null,
      birth_type: null,
      birth_weight: null,
      weight_kg: null,
      weight_date: null,
      historical_import_reason: null,
      notes: null,
    });

    // Dialog closed and the list query was invalidated → refetched.
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
  });

  it("auto-opens the create dialog for /animals?new=1 (the /animals/new redirect)", async () => {
    navState.search = "new=1";
    await renderAndWaitForList();

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Add animal")).toBeInTheDocument();
  });

  it("strips ?new=1 after opening the dialog so a reload doesn't reopen it", async () => {
    replaceMock.mockClear();
    navState.search = "new=1";
    await renderAndWaitForList();

    await screen.findByRole("dialog");
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/animals"));
  });

  it("keeps other params when stripping ?new=1", async () => {
    replaceMock.mockClear();
    navState.search = "bucket=QUARANTINE&new=1";
    await renderAndWaitForList();

    await screen.findByRole("dialog");
    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/animals?bucket=QUARANTINE"),
    );
  });

  it("rejects a future date_of_birth inline instead of hitting the API", async () => {
    const user = userEvent.setup();
    await renderAndWaitForList();

    await user.click(screen.getByRole("button", { name: "Add animal" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText(/date of birth/i), {
      target: { value: "2099-01-01" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    expect(await within(dialog).findByText("Date can't be in the future")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("rejects a future estimated_dob inline instead of hitting the API", async () => {
    const user = userEvent.setup();
    await renderAndWaitForList();

    await user.click(screen.getByRole("button", { name: "Add animal" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText(/estimated dob/i), {
      target: { value: "2099-01-01" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    expect(await within(dialog).findByText("Date can't be in the future")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("rejects a future purchase_date inline instead of hitting the API", async () => {
    const user = userEvent.setup();
    await renderAndWaitForList();

    await user.click(screen.getByRole("button", { name: "Add animal" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText(/purchase date/i), {
      target: { value: "2099-01-01" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    expect(await within(dialog).findByText("Date can't be in the future")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("rejects a purchase_price above the ₹1,000,000,000 cap inline instead of hitting the API", async () => {
    const user = userEvent.setup();
    await renderAndWaitForList();

    await user.click(screen.getByRole("button", { name: "Add animal" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/purchase price/i), "1000000001");
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    expect(
      await within(dialog).findByText("Purchase price cannot exceed ₹1,000,000,000"),
    ).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });
});
