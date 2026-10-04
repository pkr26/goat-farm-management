/**
 * Health target pickers falsy-prop semantics: placeholder/dialogTitle are
 * `??`-guarded — an explicit empty string must stay empty rather than falling back
 * to the default copy.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { HealthAnimalPicker, HealthPurchaseBatchPicker } from "@/components/health-target-pickers";
import { Label } from "@/components/ui/label";
import { server } from "@/test/msw-server";

vi.mock("@/hooks/use-farm-type", () => ({ useFarmType: () => "GOAT" }));

function Providers({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

describe("HealthAnimalPicker falsy props", () => {
  it("keeps an explicitly empty placeholder on the trigger", async () => {
    render(
      <Providers>
        <Label htmlFor="blank-health">Animal</Label>
        <HealthAnimalPicker
          id="blank-health"
          value=""
          onValueChange={() => undefined}
          placeholder=""
          dialogTitle="Pick the treated animal"
        />
      </Providers>,
    );
    const trigger = screen.getByRole("button", { name: "Animal" });
    expect(trigger.textContent).toBe("");
    expect(trigger.textContent).not.toContain("Pick an animal");
  });

  it("keeps an explicitly empty dialog title when opened", async () => {
    server.use(
      http.get("/api/animals", () => HttpResponse.json({ animals: [], total: 0 })),
    );
    const user = userEvent.setup();
    render(
      <Providers>
        <Label htmlFor="blank-title-health">Animal</Label>
        <HealthAnimalPicker
          id="blank-title-health"
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
});

describe("HealthPurchaseBatchPicker falsy props", () => {
  it("keeps an explicitly empty placeholder on the trigger", async () => {
    render(
      <Providers>
        <Label htmlFor="blank-batch">Batch</Label>
        <HealthPurchaseBatchPicker
          id="blank-batch"
          value=""
          onValueChange={() => undefined}
          placeholder=""
          dialogTitle="Choose a purchase batch"
        />
      </Providers>,
    );
    const trigger = screen.getByRole("button", { name: "Batch" });
    expect(trigger.textContent).toBe("");
    expect(trigger.textContent).not.toContain("Pick a purchase batch");
  });

  it("keeps an explicitly empty dialog title when opened", async () => {
    server.use(
      http.get("/api/health/purchase-batches", () =>
        HttpResponse.json({ batches: [], total: 0 }),
      ),
    );
    const user = userEvent.setup();
    render(
      <Providers>
        <Label htmlFor="blank-title-batch">Batch</Label>
        <HealthPurchaseBatchPicker
          id="blank-title-batch"
          value=""
          onValueChange={() => undefined}
          placeholder="Select batch"
          dialogTitle=""
        />
      </Providers>,
    );
    await user.click(screen.getByRole("button", { name: "Batch" }));
    const dialog = await screen.findByRole("dialog");
    const heading = within(dialog).getByRole("heading");
    expect(heading.textContent).toBe("");
    expect(heading.textContent).not.toContain("Choose a purchase batch");
  });
});
