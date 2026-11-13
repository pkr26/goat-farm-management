import { fireEvent, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { useState } from "react";
import { expect, it, vi } from "vitest";

import { getBreedDefaultsApiSimulationDefaultsGetQueryKey, type breedDefaultsApiSimulationDefaultsGetResponse200 } from "@/api/generated/endpoints";
import type { BackwardPlanIn, SimulationAssumptions } from "@/api/generated/models";
import { useAuth } from "@/lib/auth-context";
import { permissionsHandler, server } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";
import backend from "../simulation/backend-numeric-parity.fixture.json";
import PlannerPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/planner", useSearchParams: () => new URLSearchParams(), useParams: () => ({}),
}));

function retainedFunding(females: number, males: number, eligibleCost: number): SimulationAssumptions {
  const assumptions = structuredClone(backend.completeDefaults) as SimulationAssumptions;
  Object.assign(assumptions.finance!, {
    nlm_subsidy: false, nlm_unit_females: females, nlm_unit_males: males,
    nlm_eligible_capital_cost: eligibleCost, nlm_approved_subsidy_amount: null, nlm_subsidy_receipts: [],
  });
  return assumptions;
}

function expectedWire(assumptions: SimulationAssumptions): BackwardPlanIn {
  return {
    assumptions: { ...assumptions, meta: { ...assumptions.meta, start_year_month: "2026-10" } },
    targets: [{ year_month: "2027-10", animal_class: "male_grower", count: 20 }],
    close_gaps: true, risk_runs: 100,
  };
}

async function restore(assumptions: SimulationAssumptions) {
  const queryClient = createTestQueryClient();
  const key = getBreedDefaultsApiSimulationDefaultsGetQueryKey({ breed: "osmanabadi", system: "stall_fed" });
  const cached: breedDefaultsApiSimulationDefaultsGetResponse200 & { headers: Headers } = {
    data: assumptions, status: 200, headers: new Headers(),
  };
  const requests: BackwardPlanIn[] = [];
  server.use(
    permissionsHandler(["simulation.view", "simulation.manage"]),
    http.get("/api/simulation/defaults/breeds", () => HttpResponse.json({ breeds: ["osmanabadi"], systems: ["stall_fed"] })),
    http.get("/api/planner/plans", () => HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 })),
    http.post("/api/planner/plan", async ({ request }) => {
      requests.push(await request.json() as BackwardPlanIn);
      return HttpResponse.json({ detail: "The complete backward-plan request was captured." }, { status: 422 });
    }),
  );
  function RestoreSavedFunding() {
    const { loading, user, farmId } = useAuth();
    const [loaded, setLoaded] = useState(false);
    return <>
      <button disabled={loading || !user || !farmId} onClick={() => {
        queryClient.setQueryDefaults(key, { staleTime: Infinity });
        queryClient.setQueryData(key, cached);
        setLoaded(true);
      }}>Restore saved Planner funding evidence</button>
      {loaded && <PlannerPage />}
    </>;
  }
  renderWithProviders(<RestoreSavedFunding />, queryClient);
  const restoreButton = screen.getByText("Restore saved Planner funding evidence", { selector: "button" });
  await waitFor(() => expect(restoreButton).toBeEnabled());
  fireEvent.click(restoreButton);
  const start = await screen.findByLabelText("Plan start");
  fireEvent.change(start, { target: { value: "2026-10" } });
  expect(start).toHaveValue("2026-10");
  fireEvent.click(screen.getByText("Add target", { selector: "button" }));
  const plan = screen.getByText("Plan", { selector: "button" });
  expect(plan).toBeVisible();
  expect(plan).toHaveAccessibleName("Plan");
  const toggle = screen.getByRole("checkbox", { name: "Apply NLM subsidy" });
  expect(toggle).not.toBeChecked();
  return { plan, toggle, requests };
}

for (const bound of [{ name: "zero", females: 0, males: 0, cost: 0 }, { name: "inclusive unit and capital caps", females: 100_000, males: 100_000, cost: 1_000_000_000 }]) {
  it(`plans with valid retained funding evidence at ${bound.name} while the scheme is off`, async () => {
    const assumptions = retainedFunding(bound.females, bound.males, bound.cost);
    const { plan, requests } = await restore(assumptions);
    await waitFor(() => expect(plan).toBeEnabled());
    fireEvent.click(plan);
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toEqual(expectedWire(assumptions));
  });
}

it("keeps restored fractional unit evidence blocked when NLM is turned off and permits stock correction", async () => {
  // The generated number cache admits 100.5; the copied BackwardPlanIn model
  // rejects this exact retained unit on and off. It is never a legal HTTP default.
  const { plan, toggle, requests } = await restore(retainedFunding(100.5, 5, 2_000_000));
  fireEvent.click(toggle);
  expect(toggle).toBeChecked();
  const females = screen.getByLabelText("Declared breeding females in eligible unit");
  await waitFor(() => expect(females).toHaveAttribute("aria-invalid", "true"));
  expect(screen.getByText("Enter a whole number.")).toBeVisible();
  await waitFor(() => expect(plan).toBeDisabled());
  fireEvent.click(toggle);
  expect(toggle).not.toBeChecked();
  expect(screen.queryByLabelText("Declared breeding females in eligible unit")).not.toBeInTheDocument();
  await waitFor(() => expect(plan).toBeDisabled());
  fireEvent.click(plan);
  expect(requests).toHaveLength(0);
  expect(screen.getByText("NLM funding evidence: Declared breeding females in eligible unit — Enter a whole number.")).toBeVisible();

  fireEvent.click(toggle);
  const correction = screen.getByLabelText("Declared breeding females in eligible unit");
  fireEvent.change(correction, { target: { value: "100" } });
  fireEvent.blur(correction);
  await waitFor(() => expect(correction).not.toHaveAttribute("aria-invalid"));
  fireEvent.click(toggle);
  expect(toggle).not.toBeChecked();
  expect(screen.queryByText(/NLM funding evidence:/)).not.toBeInTheDocument();
  await waitFor(() => expect(plan).toBeEnabled());
  fireEvent.click(plan);
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0]).toEqual(expectedWire(retainedFunding(100, 5, 2_000_000)));
});
