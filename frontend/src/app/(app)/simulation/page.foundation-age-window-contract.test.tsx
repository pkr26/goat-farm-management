import { fireEvent, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { Component, type PropsWithChildren } from "react";
import { expect, it, vi } from "vitest";

import type { SimulationAssumptions } from "@/api/generated/models";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import backend from "./backend-numeric-parity.fixture.json";
import SimulationPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/simulation", useSearchParams: () => new URLSearchParams(), useParams: () => ({}),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

class SimulationBoundary extends Component<PropsWithChildren, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <p role="alert">Foundation age form failed.</p> : this.props.children; }
}

async function setup(low = 18, high = 42, omit?: "minimum" | "maximum") {
  const assumptions: SimulationAssumptions = {
    meta: backend.completeDefaults.meta,
    herd: {
      does: backend.completeDefaults.herd.does, bucks: backend.completeDefaults.herd.bucks,
      ...(omit === "minimum" ? {} : { foundation_doe_age_min_months: low }),
      ...(omit === "maximum" ? {} : { foundation_doe_age_max_months: high }),
    },
  };
  const requests: { assumptions: SimulationAssumptions }[] = [];
  server.use(
    permissionsHandler(["simulation.view", "simulation.manage"]),
    http.get("/api/simulation/defaults/breeds", () => HttpResponse.json({ breeds: ["osmanabadi"], systems: ["stall_fed"] })),
    http.get("/api/simulation/defaults", () => HttpResponse.json(assumptions)),
    http.get("/api/simulation/scenarios", () => HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 })),
    http.post("/api/simulation/run", async ({ request }) => {
      requests.push(await request.json() as typeof requests[number]);
      return HttpResponse.json({ detail: "The accepted request was captured." }, { status: 422 });
    }),
  );
  const view = renderWithProviders(<SimulationBoundary><SimulationPage /></SimulationBoundary>);
  await view.waitForAuthIdle();
  const available = await screen.findByLabelText(omit === "minimum" ? "Foundation Doe Age Max Months" : "Foundation Doe Age Min Months");
  const minimum = screen.queryByLabelText("Foundation Doe Age Min Months");
  const maximum = screen.queryByLabelText("Foundation Doe Age Max Months");
  const details = available.closest("details");
  if (details && !details.open) fireEvent.click(details.querySelector("summary")!);
  return { minimum, maximum, requests, run: screen.getByRole("button", { name: "Run simulation" }) };
}

it.each([
  { edit: "minimum", invalid: 50, repair: "maximum", value: 50 },
  { edit: "maximum", invalid: 10, repair: "minimum", value: 10 },
] as const)("blocks an inverted foundation age window after editing its $edit and recovers after editing its $repair", async ({ edit, invalid, repair, value }) => {
  const view = await setup();
  expect(view.run).toBeEnabled();
  fireEvent.change(view[edit]!, { target: { value: String(invalid) } });
  // Both fields are individually legal 0..180 integers; ordering is a
  // separate complete-model requirement, so explain it before an API run.
  expect(view[edit]).not.toHaveAttribute("aria-invalid", "true");
  expect(view.run).toBeDisabled();
  expect(screen.getByText("Minimum foundation age must not exceed maximum foundation age.")).toBeInTheDocument();
  expect(view.requests).toHaveLength(0);
  fireEvent.change(view[repair]!, { target: { value: String(value) } });
  expect(view.run).toBeEnabled();
  expect(screen.queryByText("Minimum foundation age must not exceed maximum foundation age.")).not.toBeInTheDocument();
  fireEvent.click(view.run);
  await waitFor(() => expect(view.requests).toHaveLength(1));
  expect(view.requests[0].assumptions.herd?.foundation_doe_age_min_months).toBe(value);
  expect(view.requests[0].assumptions.herd?.foundation_doe_age_max_months).toBe(value);
}, 60_000);

it.each([0, 180])("accepts equal foundation age endpoints at %s months", async (age) => {
  const { requests, run } = await setup(age, age);
  expect(run).toBeEnabled();
  expect(screen.queryByText("Minimum foundation age must not exceed maximum foundation age.")).not.toBeInTheDocument();
  fireEvent.click(run);
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0].assumptions.herd?.foundation_doe_age_min_months).toBe(age);
  expect(requests[0].assumptions.herd?.foundation_doe_age_max_months).toBe(age);
  expect(screen.queryByText("Foundation age form failed.")).not.toBeInTheDocument();
}, 60_000);

// An omitted age is valid in the public DTO: the complete backend model
// supplies 18 months for minimum and 42 months for maximum.
it.each([
  { edit: "minimum", omit: "maximum", invalid: 43, recovery: 42 },
  { edit: "maximum", omit: "minimum", invalid: 17, recovery: 18 },
] as const)("respects the backend default for an omitted $omit age while editing $edit", async ({ edit, omit, invalid, recovery }) => {
  const view = await setup(18, 42, omit);
  expect(view[omit]).toBeNull();
  expect(view.run).toBeEnabled();
  fireEvent.change(view[edit]!, { target: { value: String(invalid) } });
  expect(view[edit]).not.toHaveAttribute("aria-invalid", "true");
  expect(view.run).toBeDisabled();
  expect(screen.getByText("Minimum foundation age must not exceed maximum foundation age.")).toBeInTheDocument();
  expect(view.requests).toHaveLength(0);
  fireEvent.change(view[edit]!, { target: { value: String(recovery) } });
  expect(view.run).toBeEnabled();
  expect(screen.queryByText("Minimum foundation age must not exceed maximum foundation age.")).not.toBeInTheDocument();
  fireEvent.click(view.run);
  await waitFor(() => expect(view.requests).toHaveLength(1));
  const herd = view.requests[0].assumptions.herd;
  const editedKey = edit === "minimum" ? "foundation_doe_age_min_months" : "foundation_doe_age_max_months";
  const omittedKey = omit === "minimum" ? "foundation_doe_age_min_months" : "foundation_doe_age_max_months";
  expect(herd?.[editedKey]).toBe(recovery);
  expect(herd).not.toHaveProperty(omittedKey);
  expect(screen.queryByText("Foundation age form failed.")).not.toBeInTheDocument();
}, 60_000);
