import { fireEvent, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { Component, type PropsWithChildren } from "react";
import { expect, it, vi } from "vitest";

import type { SimulationAssumptions } from "@/api/generated/models";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import backend from "./backend-public-scalar-contract.fixture.json";
import SimulationPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/simulation", useSearchParams: () => new URLSearchParams(), useParams: () => ({}),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

class SimulationBoundary extends Component<PropsWithChildren, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <p role="alert">Scalar domain editor failed.</p> : this.props.children; }
}

type ScalarCase = {
  path: string; group: string; key: string; label: string; integer: boolean;
  constraints: { minimum?: number; maximum?: number; exclusiveMinimum?: number };
  lower: number; upper: number; invalid: number[]; payload: SimulationAssumptions; unit?: string;
};
const cases = backend.fields as unknown as ScalarCase[];

async function setup(assumptions: SimulationAssumptions) {
  const requests: { assumptions: SimulationAssumptions }[] = [];
  server.use(
    permissionsHandler(["simulation.view", "simulation.manage"]),
    http.get("/api/simulation/defaults/breeds", () => HttpResponse.json({ breeds: ["osmanabadi"], systems: ["stall_fed"] })),
    http.get("/api/simulation/defaults", () => HttpResponse.json(assumptions)),
    http.get("/api/simulation/scenarios", () => HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 })),
    http.post("/api/simulation/run", async ({ request }) => {
      requests.push(await request.json() as typeof requests[number]);
      return HttpResponse.json({ detail: "The accepted scalar request was captured." }, { status: 422 });
    }),
  );
  const view = renderWithProviders(<SimulationBoundary><SimulationPage /></SimulationBoundary>);
  await view.waitForAuthIdle();
  await screen.findByLabelText("Horizon Months");
  return { requests, run: screen.getByRole("button", { name: "Run simulation" }) };
}

function valueAt(assumptions: SimulationAssumptions, group: string, key: string) {
  return (assumptions[group as keyof SimulationAssumptions] as Record<string, unknown>)[key];
}

// The fixture comes from the complete backend model, including companion
// fields required by its range/finance/growth validators. No editor metadata
// helper supplies the bounds, integer flags, units or expected wire values.
it.each(cases)("preserves public numeric scalar $path", async ({ path, group, key, label, integer, constraints, lower, upper, invalid, payload, unit }) => {
  const { requests, run } = await setup(structuredClone(payload));
  const field = screen.getByLabelText(label);
  const details = field.closest("details");
  if (details && !details.open) fireEvent.click(details.querySelector("summary")!);
  if (constraints.minimum === undefined) expect(field).not.toHaveAttribute("min");
  else expect(field).toHaveAttribute("min", String(constraints.minimum));
  if (constraints.maximum === undefined) expect(field).not.toHaveAttribute("max");
  else expect(field).toHaveAttribute("max", String(constraints.maximum));
  expect(field).toHaveAttribute("step", integer ? "1" : "any");
  // Rates and dimensional captions already exposed by this form must retain
  // their raw backend scale. Head-count labels and the mortality section
  // and retention-step limits identify their quantities without requiring an
  // extra caption attribute.
  if (unit && path !== "optimization.retention_step" && group !== "mortality" && !(group === "herd" && integer)) {
    expect(field).toHaveAttribute("data-unit", unit);
  }
  expect(run).toBeEnabled();
  fireEvent.change(field, { target: { value: String(lower) } });
  expect(field).not.toHaveAttribute("aria-invalid", "true");
  expect(run).toBeEnabled();
  fireEvent.click(run);
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(valueAt(requests[0].assumptions, group, key)).toBe(lower);
  await waitFor(() => expect(run).toBeEnabled());
  for (const value of invalid) {
    fireEvent.change(field, { target: { value: String(value) } });
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(run).toBeDisabled();
    expect(requests).toHaveLength(1);
  }
  fireEvent.change(field, { target: { value: String(upper) } });
  expect(field).not.toHaveAttribute("aria-invalid", "true");
  expect(run).toBeEnabled();
  fireEvent.click(run);
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(valueAt(requests[1].assumptions, group, key)).toBe(upper);
  if (path === "growth.birth_weight_kg") {
    expect(requests[1].assumptions.growth?.weight_by_age_months?.[0]).toBe(upper);
  }
  expect(screen.queryByText("Scalar domain editor failed.")).not.toBeInTheDocument();
}, 60_000);
