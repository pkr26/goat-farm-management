import { fireEvent, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { useState } from "react";
import { expect, it, vi } from "vitest";

import { getBreedDefaultsApiSimulationDefaultsGetQueryKey, type breedDefaultsApiSimulationDefaultsGetResponse200 } from "@/api/generated/endpoints";
import type { SimulationAssumptions } from "@/api/generated/models";
import { useAuth } from "@/lib/auth-context";
import { permissionsHandler, server } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";
import backend from "./backend-numeric-parity.fixture.json";
import SimulationPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/simulation", useSearchParams: () => new URLSearchParams(), useParams: () => ({}),
}));

it("identifies a retained invalid funding field while Finance is collapsed and allows stock correction", async () => {
  const assumptions = structuredClone(backend.completeDefaults) as SimulationAssumptions;
  assumptions.finance!.nlm_unit_females = 100.5;
  assumptions.finance!.nlm_unit_males = 5;
  const cached: breedDefaultsApiSimulationDefaultsGetResponse200 & { headers: Headers } = {
    data: assumptions, status: 200, headers: new Headers(),
  };
  const queryClient = createTestQueryClient();
  const key = getBreedDefaultsApiSimulationDefaultsGetQueryKey({ breed: "osmanabadi", system: "stall_fed" });
  const requests: { assumptions: SimulationAssumptions; monte_carlo: boolean; sensitivity: boolean; optimization: boolean }[] = [];
  server.use(
    permissionsHandler(["simulation.view", "simulation.manage"]),
    http.get("/api/simulation/defaults/breeds", () => HttpResponse.json({ breeds: ["osmanabadi"], systems: ["stall_fed"] })),
    http.get("/api/simulation/scenarios", () => HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 })),
    http.post("/api/simulation/run", async ({ request }) => {
      requests.push(await request.json() as typeof requests[number]);
      return HttpResponse.json({ detail: "The valid corrected request was captured." }, { status: 422 });
    }),
  );
  function RestoreCachedDefaults() {
    const { loading, user, farmId } = useAuth();
    const [loaded, setLoaded] = useState(false);
    return <>
      <button disabled={loading || !user || !farmId} onClick={() => {
        queryClient.setQueryDefaults(key, { staleTime: Infinity });
        queryClient.setQueryData(key, cached);
        setLoaded(true);
      }}>Restore saved assumptions cache</button>
      {loaded && <SimulationPage />}
    </>;
  }
  renderWithProviders(<RestoreCachedDefaults />, queryClient);
  const restore = screen.getByText("Restore saved assumptions cache", { selector: "button" });
  await waitFor(() => expect(restore).toBeEnabled());
  fireEvent.click(restore);
  await screen.findByLabelText("Horizon Months");
  const run = screen.getByText("Run simulation", { selector: "button" });
  expect(run).toBeVisible();
  expect(run).toHaveAccessibleName("Run simulation");
  const summary = screen.getByText("Finance", { selector: "summary" });
  expect(summary.closest("details")).not.toHaveAttribute("open");
  const explanation = screen.getByText("NLM funding evidence: Declared breeding females in eligible unit — Enter a whole number.");
  expect(explanation).toBeVisible();
  expect(explanation.closest('[role="alert"]')).toBeInTheDocument();
  expect(screen.getByText("Fix 1 highlighted numeric field before running or saving.")).toBeVisible();
  expect(run).toBeDisabled();
  fireEvent.click(run);
  expect(requests).toHaveLength(0);

  fireEvent.click(summary);
  const toggle = screen.getByRole("checkbox", { name: /nlm subsidy/i });
  expect(toggle).not.toBeChecked();
  fireEvent.click(toggle);
  const females = screen.getByLabelText("Declared breeding females in eligible unit");
  expect(females).toBeVisible();
  expect(females).toHaveValue(100.5);
  expect(females).toHaveAttribute("aria-invalid", "true");
  fireEvent.change(females, { target: { value: "100" } });
  fireEvent.blur(females);
  fireEvent.click(toggle);
  fireEvent.click(summary);
  expect(summary.closest("details")).not.toHaveAttribute("open");
  expect(screen.queryByText(/NLM funding evidence: Declared breeding females/)).not.toBeInTheDocument();
  await waitFor(() => expect(run).toBeEnabled());
  expect(screen.queryByText(/highlighted numeric field/)).not.toBeInTheDocument();
  fireEvent.click(run);
  await waitFor(() => expect(requests).toHaveLength(1));
  const expected = structuredClone(backend.completeDefaults) as SimulationAssumptions;
  expected.finance!.nlm_unit_females = 100;
  expected.finance!.nlm_unit_males = 5;
  expect(requests[0]).toEqual({ assumptions: expected, monte_carlo: false, sensitivity: false, optimization: false });
}, 20_000);
