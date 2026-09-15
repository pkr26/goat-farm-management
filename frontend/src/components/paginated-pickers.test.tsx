import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { useState, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import type { AnimalOut, HealthPurchaseBatchOptionOut, AnimalOutCurrentBucket} from "@/api/generated/models";
import { AnimalPicker } from "@/components/animal-picker";
import { BreedingCandidatePicker } from "@/components/breeding-candidate-picker";
import { HealthPurchaseBatchPicker } from "@/components/health-target-pickers";
import { Label } from "@/components/ui/label";
import { createTestQueryClient } from "@/test/render";
import { server } from "@/test/msw-server";

// Species nouns come from the session farm type; these harnesses run
// outside the auth providers, so pin the GOAT vocabulary.
vi.mock("@/hooks/use-farm-type", () => ({ useFarmType: () => "GOAT" }));

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
    current_bucket: "GROWER_FEMALE" as AnimalOutCurrentBucket,
    status: "ACTIVE",
    status_date: null,
    sale_price: null,
    sale_weight_kg: null,
    buyer_name: null,
    mortality_cause_code: null,
    disposal_method: null,
    necropsy_done: false,
    necropsy_findings: null,
    coat_color: null,
    horned: null,
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
    active_quarantine_animal_count: 12,
  };
}

function Providers({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={createTestQueryClient()}>{children}</QueryClientProvider>;
}

describe("paginated domain pickers", () => {
  it("loads only breeding-scoped identities in bounded pages beyond 200", async () => {
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
    expect(await within(dialog).findByRole("option", { name: /G-0001/ })).toBeInTheDocument();

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
    expect(purchaseSearch).toHaveAttribute("maxlength", "20");
    await user.type(purchaseSearch, "#201");
    await waitFor(() => expect(purchaseQueries).toContain("#201"));
    expect(
      await screen.findByRole("option", { name: "Batch #201 — 12 active in quarantine" }),
    ).toBeInTheDocument();
  });

  it("does not reuse cached pages across different default eligibility filters", async () => {
    let calls = 0;
    server.use(
      http.get("/api/animals", () => {
        calls += 1;
        return HttpResponse.json({ animals: [animal(1), animal(2)], total: 2 });
      }),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Number.POSITIVE_INFINITY } },
    });

    function Harness({ eligibleIds }: { eligibleIds: readonly number[] }) {
      return (
        <QueryClientProvider client={client}>
          <Label htmlFor="eligibility-animal">Animal</Label>
          <AnimalPicker
            id="eligibility-animal"
            value=""
            onValueChange={() => undefined}
            eligibleIds={eligibleIds}
          />
        </QueryClientProvider>
      );
    }

    const user = userEvent.setup();
    const view = render(<Harness eligibleIds={[1]} />);
    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    let dialog = screen.getByRole("dialog", { name: "Choose an animal" });
    expect(await within(dialog).findByRole("option", { name: /G-0001/ })).toBeInTheDocument();
    expect(within(dialog).queryByRole("option", { name: /G-0002/ })).not.toBeInTheDocument();
    await user.keyboard("{Escape}");

    view.rerender(<Harness eligibleIds={[2]} />);
    await user.click(screen.getByRole("combobox", { name: "Animal" }));
    dialog = screen.getByRole("dialog", { name: "Choose an animal" });
    expect(await within(dialog).findByRole("option", { name: /G-0002/ })).toBeInTheDocument();
    expect(within(dialog).queryByRole("option", { name: /G-0001/ })).not.toBeInTheDocument();
    expect(calls).toBe(2);
  });
});
