import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { useState, type ReactNode } from "react";
import { describe, expect, it } from "vitest";

import type { AnimalOut, HealthPurchaseBatchOptionOut } from "@/api/generated/models";
import { AnimalPicker } from "@/components/animal-picker";
import { BreedingCandidatePicker } from "@/components/breeding-candidate-picker";
import { HealthPurchaseBatchPicker } from "@/components/health-target-pickers";
import { Label } from "@/components/ui/label";
import { createTestQueryClient } from "@/test/render";
import { server } from "@/test/msw-server";

function animal(id: number): AnimalOut {
  return {
    id,
    tag_number: `G-${String(id).padStart(4, "0")}`,
    name: id === 201 ? "Late doe" : null,
    breed: "Osmanabadi",
    sex: "F",
    date_of_birth: "2024-01-01",
    estimated_dob: null,
    birth_type: null,
    source: "BORN",
    dam_id: null,
    sire_id: null,
    birth_weight: null,
    current_bucket: "GROWER_FEMALE",
    status: "ACTIVE",
    status_date: null,
    sale_price: null,
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
    mortality_cause: null,
    mortality_reported_at: null,
    notes: null,
    created_at: "2026-01-01T00:00:00Z",
    age_months: 24,
    latest_weight_kg: 31,
  };
}

function batch(id: number): HealthPurchaseBatchOptionOut {
  return {
    id,
    date: "2026-08-01",
    supplier: "Solapur Market",
    count: 12,
    active_animal_count: 12,
  };
}

function Providers({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={createTestQueryClient()}>{children}</QueryClientProvider>;
}

describe("paginated domain pickers", () => {
  it("intersects breeding eligibility while continuing source pagination beyond 200", async () => {
    const requests: Array<{ offset: number; limit: number; query: string | null }> = [];
    server.use(
      http.get("/api/breeding/candidates", ({ request }) => {
        const url = new URL(request.url);
        const offset = Number(url.searchParams.get("offset") ?? 0);
        const limit = Number(url.searchParams.get("limit") ?? 50);
        const query = url.searchParams.get("q");
        requests.push({ offset, limit, query });
        if (query) {
          return HttpResponse.json({
            candidates: [animal(201)],
            total: 1,
            limit,
            offset,
          });
        }
        const count = Math.max(0, Math.min(limit, 201 - offset));
        return HttpResponse.json({
          candidates: Array.from({ length: count }, (_, index) => {
            const candidate = animal(offset + index + 1);
            return {
              id: candidate.id,
              tag_number: candidate.tag_number,
              name: candidate.name,
              age_months: candidate.age_months ?? null,
              latest_weight_kg: candidate.latest_weight_kg ?? null,
            };
          }),
          total: 201,
          limit,
          offset,
        });
      }),
    );

    function Harness() {
      const [value, setValue] = useState("");
      return (
        <Providers>
          <Label htmlFor="eligible-doe">Doe</Label>
          <BreedingCandidatePicker
            id="eligible-doe"
            kind="doe"
            value={value}
            onValueChange={setValue}
            eligibleIds={[201]}
            placeholder="Select doe"
            dialogTitle="Choose a breeding-ready doe"
          />
        </Providers>
      );
    }

    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("combobox", { name: "Doe" }));
    const dialog = screen.getByRole("dialog", { name: "Choose a breeding-ready doe" });
    expect(
      await within(dialog).findByText(/No listed eligible animals in the records checked yet/),
    ).toBeInTheDocument();

    for (let page = 0; page < 4; page += 1) {
      await user.click(within(dialog).getByRole("button", { name: "Load more" }));
      await waitFor(() => expect(requests).toHaveLength(page + 2));
    }

    expect(await within(dialog).findByRole("option", { name: /G-0201 · Late doe/ })).toBeInTheDocument();
    expect(requests.map(({ offset }) => offset)).toEqual([0, 50, 100, 150, 200]);
    expect(requests.every(({ limit }) => limit === 50 && limit <= 100)).toBe(true);
  });

  it("forwards bounded animal and purchase searches to their exact APIs", async () => {
    const animalQueries: Array<string | null> = [];
    const purchaseQueries: Array<string | null> = [];
    server.use(
      http.get("/api/animals", ({ request }) => {
        const url = new URL(request.url);
        animalQueries.push(url.searchParams.get("q"));
        return HttpResponse.json({ animals: [animal(201)], total: 1 });
      }),
      http.get("/api/health/purchase-batches", ({ request }) => {
        const url = new URL(request.url);
        purchaseQueries.push(url.searchParams.get("q"));
        const limit = Number(url.searchParams.get("limit") ?? 50);
        const offset = Number(url.searchParams.get("offset") ?? 0);
        return HttpResponse.json({ batches: [batch(201)], total: 1, limit, offset });
      }),
    );

    function Harness() {
      const [animalId, setAnimalId] = useState("");
      const [batchId, setBatchId] = useState("");
      return (
        <Providers>
          <Label htmlFor="searched-animal">Animal</Label>
          <AnimalPicker id="searched-animal" value={animalId} onValueChange={setAnimalId} />
          <Label htmlFor="searched-batch">Purchase batch</Label>
          <HealthPurchaseBatchPicker
            id="searched-batch"
            value={batchId}
            onValueChange={setBatchId}
          />
        </Providers>
      );
    }

    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    const animalSearch = screen.getByLabelText("Search animals");
    expect(animalSearch).toHaveAttribute("maxlength", "60");
    await user.type(animalSearch, "Late doe");
    await waitFor(() => expect(animalQueries).toContain("Late doe"));
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("combobox", { name: "Purchase batch" }));
    const purchaseSearch = screen.getByLabelText("Search health purchase batches");
    expect(purchaseSearch).toHaveAttribute("maxlength", "120");
    await user.type(purchaseSearch, "#201");
    await waitFor(() => expect(purchaseQueries).toContain("#201"));
    expect(await screen.findByRole("option", { name: /#201.*Solapur Market/ })).toBeInTheDocument();
  });
});
