/**
 * AnimalPicker falsy-prop semantics: placeholder/dialogTitle are `??`-guarded so an
 * explicit empty string stays empty (a caller deliberately blanking the trigger),
 * and a caller-supplied eligibilityKey of "" is still a DISTINCT cache key — it must
 * not fall back onto the unfiltered picker's "all-active" query cache.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import type { AnimalOut, AnimalOutCurrentBucket } from "@/api/generated/models";
import { AnimalPicker } from "@/components/animal-picker";
import { Label } from "@/components/ui/label";
import { server } from "@/test/msw-server";

vi.mock("@/hooks/use-farm-type", () => ({ useFarmType: () => "GOAT" }));

function animal(id: number): AnimalOut {
  return {
    id,
    tag_number: `G-${String(id).padStart(4, "0")}`,
    name: null,
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
    cull_candidate: false,
    status: "ACTIVE",
    status_notes: null,
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
    mortality_cause: null,
    mortality_reported_at: null,
    notes: null,
    created_at: "2026-01-01T00:00:00Z",
    age_months: 24,
    latest_weight_kg: 31,
    movement_restricted: false,
    restriction_reason: null,
    suspected_scheduled_disease: false,
    suspected_disease: null,
    authority_notified_at: null,
    restriction_cleared_at: null,
    restriction_cleared_by_id: null,
    restriction_clearance_reference: null,
    restriction_version: 0,
  };
}

function Providers({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

describe("AnimalPicker falsy-prop semantics", () => {
  it("keeps an explicitly empty placeholder on the trigger (no default fallback)", async () => {
    render(
      <Providers>
        <Label htmlFor="blanked-animal">Animal</Label>
        <AnimalPicker
          id="blanked-animal"
          value=""
          onValueChange={() => undefined}
          placeholder=""
          dialogTitle=""
        />
      </Providers>,
    );

    const trigger = screen.getByRole("button", { name: "Animal" });
    expect(trigger.textContent).toBe("");
    expect(trigger.textContent).not.toContain("Pick an animal");
  });

  it("keeps an explicitly empty dialog title when opened", async () => {
    server.use(
      http.get("/api/animals", () =>
        HttpResponse.json({ animals: [animal(1)], total: 1 }),
      ),
    );
    const user = userEvent.setup();
    render(
      <Providers>
        <Label htmlFor="blanked-title-animal">Animal</Label>
        <AnimalPicker
          id="blanked-title-animal"
          value=""
          onValueChange={() => undefined}
          placeholder="Select animal"
          dialogTitle=""
        />
      </Providers>,
    );

    await user.click(screen.getByRole("button", { name: "Animal" }));
    const dialog = await screen.findByRole("dialog");
    const heading = within(dialog).getByRole("heading");
    expect(heading.textContent).toBe("");
    expect(heading.textContent).not.toContain("Choose an animal");
  });

  it("treats an empty-string eligibilityKey as its own cache key, not 'all-active'", async () => {
    let calls = 0;
    server.use(
      http.get("/api/animals", () => {
        calls += 1;
        return HttpResponse.json({ animals: [animal(1)], total: 1 });
      }),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Number.POSITIVE_INFINITY } },
    });

    function Harness({ eligibilityKey }: { eligibilityKey?: string }) {
      return (
        <QueryClientProvider client={client}>
          <Label htmlFor="keyed-animal">Animal</Label>
          <AnimalPicker
            id="keyed-animal"
            value=""
            onValueChange={() => undefined}
            eligibilityKey={eligibilityKey}
          />
        </QueryClientProvider>
      );
    }

    const user = userEvent.setup();
    const view = render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Animal" }));
    const dialog = screen.getByRole("dialog", { name: "Choose an animal" });
    expect(await within(dialog).findByRole("option", { name: /G-0001/ })).toBeInTheDocument();
    expect(calls).toBe(1);
    await user.keyboard("{Escape}");

    view.rerender(<Harness eligibilityKey="" />);
    await user.click(screen.getByRole("button", { name: "Animal" }));
    const reopened = screen.getByRole("dialog", { name: "Choose an animal" });
    expect(await within(reopened).findByRole("option", { name: /G-0001/ })).toBeInTheDocument();
    // The "" key is a distinct cache entry: the pages are refetched rather
    // than silently reused from the unfiltered picker's "all-active" cache.
    await waitFor(() => expect(calls).toBe(2));
  });
});
