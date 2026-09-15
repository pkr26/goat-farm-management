/**
 * Animal profile — phenotype descriptors (coat_color / horned): the optional
 * contract fields render as localized Details when the payload carries them
 * and leave no row at all when absent (pre-contract payloads).
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import AnimalProfilePage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/animals/1",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ id: "1" }),
}));

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
  current_bucket: "FOUNDATION",
  status: "ACTIVE",
  status_date: null,
  sale_price: null,
  sale_weight_kg: null,
  sale_price_per_kg: null,
  purchase_date: null,
  purchase_price: null,
  seller_name: null,
  cull_candidate: false,
  movement_restricted: false,
  restriction_reason: null,
  suspected_scheduled_disease: false,
  suspected_disease: null,
  authority_notified_at: null,
  restriction_cleared_at: null,
  restriction_cleared_by_id: null,
  restriction_clearance_reference: null,
  restriction_version: 0,
  mortality_cause: null,
  mortality_cause_code: null,
  disposal_method: null,
  necropsy_done: false,
  necropsy_findings: null,
  mortality_reported_at: null,
  notes: null,
  created_at: "2026-01-01T05:30:00Z",
  age_months: 14,
  latest_weight_kg: 32.5,
  days_in_current_bucket: 12,
};

const PROFILE = {
  animal: ANIMAL,
  kids: [],
  kids_total: 0,
  kids_offset: 0,
  weights: [],
  weights_total: 0,
  weights_offset: 0,
  moves: [],
  moves_total: 0,
  moves_offset: 0,
  health_events: [],
  health_events_total: 0,
  health_events_offset: 0,
  breedings: [],
  breedings_total: 0,
  breedings_offset: 0,
  history_limit: 25,
};

function detailsCard(): HTMLElement {
  return screen.getByText("Details").closest("[data-slot='card']") as HTMLElement;
}

describe("AnimalProfilePage — phenotype descriptors", () => {
  beforeEach(() => {
    server.use(
      http.get("/api/health/restrictions/1", () =>
        HttpResponse.json({
          animal_id: 1,
          restriction_version: 0,
          active: false,
          actions: [],
          total: 0,
          limit: 25,
          offset: 0,
        }),
      ),
    );
  });

  function useProfile(animal: Record<string, unknown>) {
    server.use(
      http.get("/api/animals/1", () =>
        HttpResponse.json({ ...PROFILE, animal: { ...ANIMAL, ...animal } }),
      ),
    );
  }

  it("renders the coat colour and horn status when the payload carries them", async () => {
    useProfile({ coat_color: "black_patched", horned: false });
    renderWithProviders(<AnimalProfilePage />);

    const details = await screen.findByText("Details");
    const card = details.closest("[data-slot='card']") as HTMLElement;
    expect(within(card).getByText("Coat colour")).toBeInTheDocument();
    expect(within(card).getByText("Black with patches")).toBeInTheDocument();
    expect(within(card).getByText("Horned")).toBeInTheDocument();
    expect(within(card).getByText("No")).toBeInTheDocument();
  });

  it("renders no phenotype rows for a pre-contract payload", async () => {
    useProfile({});
    renderWithProviders(<AnimalProfilePage />);

    await screen.findByText("Details");
    const card = detailsCard();
    expect(within(card).getByText("Breed")).toBeInTheDocument();
    expect(within(card).queryByText("Coat colour")).not.toBeInTheDocument();
    expect(within(card).queryByText("Horned")).not.toBeInTheDocument();
  });

  it("shows an unknown coat code verbatim instead of dropping it", async () => {
    useProfile({ coat_color: "roan", horned: true });
    renderWithProviders(<AnimalProfilePage />);

    await screen.findByText("Details");
    const card = detailsCard();
    expect(within(card).getByText("roan")).toBeInTheDocument();
    expect(within(card).getByText("Yes")).toBeInTheDocument();
  });
});

describe("AnimalProfilePage — phenotype edit dialog", () => {
  let patchBody: Record<string, unknown> | null;
  let patchCalls: number;

  beforeEach(() => {
    patchBody = null;
    patchCalls = 0;
    server.use(
      http.get("/api/health/restrictions/1", () =>
        HttpResponse.json({
          animal_id: 1,
          restriction_version: 0,
          active: false,
          actions: [],
          total: 0,
          limit: 25,
          offset: 0,
        }),
      ),
      http.patch("/api/animals/1", async ({ request }) => {
        patchCalls += 1;
        patchBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(ANIMAL);
      }),
    );
  });

  function useProfile(animal: Record<string, unknown>) {
    server.use(
      http.get("/api/animals/1", () =>
        HttpResponse.json({ ...PROFILE, animal: { ...ANIMAL, ...animal } }),
      ),
    );
  }

  it("sends the chosen phenotype through PATCH /api/animals/{id}", async () => {
    const user = userEvent.setup();
    useProfile({});
    renderWithProviders(<AnimalProfilePage />);

    await user.click(await screen.findByRole("button", { name: "Edit phenotype" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByLabelText("Coat colour"));
    await user.click(await screen.findByRole("option", { name: "Spotted" }));
    await user.click(within(dialog).getByLabelText("Horned"));
    await user.click(await screen.findByRole("option", { name: "Yes" }));
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(patchCalls).toBe(1));
    expect(patchBody).toEqual({ coat_color: "spotted", horned: true });
  });

  it("clears stored values with explicit null when set back to Not recorded", async () => {
    const user = userEvent.setup();
    useProfile({ coat_color: "black_patched", horned: false });
    renderWithProviders(<AnimalProfilePage />);

    await user.click(await screen.findByRole("button", { name: "Edit phenotype" }));
    const dialog = await screen.findByRole("dialog");
    // The stored values prefill the selects.
    expect(within(dialog).getByLabelText("Coat colour")).toHaveTextContent(
      "Black with patches",
    );
    expect(within(dialog).getByLabelText("Horned")).toHaveTextContent("No");
    await user.click(within(dialog).getByLabelText("Coat colour"));
    await user.click(await screen.findByRole("option", { name: "Not recorded" }));
    await user.click(within(dialog).getByLabelText("Horned"));
    await user.click(await screen.findByRole("option", { name: "Not recorded" }));
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(patchCalls).toBe(1));
    expect(patchBody).toEqual({ coat_color: null, horned: null });
  });

  it("hides the edit action for animals that left the herd (PATCH is ACTIVE-only)", async () => {
    useProfile({ status: "SOLD", status_date: "2026-08-01" });
    renderWithProviders(<AnimalProfilePage />);

    await screen.findByText("Details");
    expect(screen.queryByRole("button", { name: "Edit phenotype" })).not.toBeInTheDocument();
  });
});
