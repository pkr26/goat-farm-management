import { fireEvent, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { useState } from "react";
import { expect, it, vi } from "vitest";

import { getBreedDefaultsApiSimulationDefaultsGetQueryKey, type breedDefaultsApiSimulationDefaultsGetResponse200 } from "@/api/generated/endpoints";
import type { SimulationAssumptions } from "@/api/generated/models";
import { useAuth } from "@/lib/auth-context";
import { server } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import SimulationPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/simulation", useSearchParams: () => new URLSearchParams(), useParams: () => ({}),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

type LegacyReproduction = NonNullable<SimulationAssumptions["reproduction"]> & {
  sexed_semen_services: number;
  sexed_female_fraction: number;
  sexed_conception_multiplier: number;
};

it.each([
  { description: "previously valid dairy inputs", retired: [3, 0.8, 1] },
  { description: "ignored values outside the retired ranges", retired: [99, -99, 99] },
  { description: "current active breeding inputs", retired: null },
])("keeps $description out of the goat-meat editor and its run gate", async ({ retired }) => {
  // Structural compatibility retains old cache fields without weakening the
  // current generated response type. The current backend strips these fields.
  const oldReproduction: LegacyReproduction | undefined = retired ? {
    max_services_before_cull: 12,
    sexed_semen_services: retired[0],
    sexed_female_fraction: retired[1],
    sexed_conception_multiplier: retired[2],
  } : undefined;
  const legacy: SimulationAssumptions = {
    meta: { horizon_months: 60, start_year_month: "2026-01" },
    herd: { does: 50, bucks: 2 },
    reproduction: oldReproduction ?? { max_services_before_cull: 12 },
  };
  const cached: breedDefaultsApiSimulationDefaultsGetResponse200 & { headers: Headers } = {
    data: legacy, status: 200, headers: new Headers(),
  };
  const queryClient = createTestQueryClient();
  const bodies: { assumptions: SimulationAssumptions }[] = [];
  server.use(
    http.get("/api/simulation/defaults/breeds", () => HttpResponse.json({ breeds: ["osmanabadi"], systems: ["stall_fed"] })),
    http.get("/api/simulation/scenarios", () => HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 })),
    http.post("/api/simulation/run", async ({ request }) => {
      bodies.push(await request.json() as typeof bodies[number]);
      return HttpResponse.json({ detail: "server declined this test run" }, { status: 422 });
    }),
  );
  function RestoreCachedDefaults() {
    const { loading, user, farmId } = useAuth();
    const [restored, setRestored] = useState(false);
    return <>
      <button disabled={loading || !user || !farmId} onClick={() => {
        const key = getBreedDefaultsApiSimulationDefaultsGetQueryKey({ breed: "osmanabadi", system: "stall_fed" });
        queryClient.setQueryDefaults(key, { staleTime: Infinity });
        queryClient.setQueryData(key, cached);
        setRestored(true);
      }}>Restore cached simulation</button>
      {restored && <SimulationPage />}
    </>;
  }
  renderWithProviders(<RestoreCachedDefaults />, queryClient);
  const restore = screen.getByRole("button", { name: "Restore cached simulation" });
  await waitFor(() => expect(restore).toBeEnabled());
  fireEvent.click(restore);
  await screen.findByText("Horizon Months");
  fireEvent.click(screen.getByText("Reproduction", { selector: "summary" }));
  const run = screen.getByRole("button", { name: "Run simulation" });
  expect(run).toBeEnabled();
  expect(screen.queryByLabelText("Sexed Semen Services")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Sexed Female Fraction")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Sexed Conception Multiplier")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Max Services Before Cull")).toHaveValue(12);
  const active = screen.getByLabelText("Max Services Before Cull");
  fireEvent.change(active, { target: { value: "13" } });
  expect(active).toHaveAttribute("aria-invalid", "true");
  expect(run).toBeDisabled();
  expect(bodies).toHaveLength(0);
  fireEvent.change(active, { target: { value: "12" } });
  expect(active).not.toHaveAttribute("aria-invalid", "true");
  expect(run).toBeEnabled();
  fireEvent.click(run);
  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(bodies[0].assumptions.reproduction?.max_services_before_cull).toBe(12);
}, 60_000);
