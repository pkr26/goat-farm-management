import { fireEvent, screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { Component, type PropsWithChildren } from "react";
import { expect, it, vi } from "vitest";

import type { DailyOpsResult, SimTask } from "@/api/generated/models";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import OpsSimulationPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/ops-simulation", useSearchParams: () => new URLSearchParams(), useParams: () => ({}),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

class OutputBoundary extends Component<PropsWithChildren, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <p role="alert">Operational output failed.</p> : this.props.children; }
}
const date = "2026-09-03";
function output(): DailyOpsResult {
  return {
    model_version: "1.0.0", species: "GOAT", start_date: date, horizon_days: 1, seed: 11, head_start: 1,
    days: [{ day: 1, date }], journeys: [], transition_counts: [], explanations: [], notes: [],
    totals: { feed_kg_by_recipe: {}, tasks_by_category: {}, tasks_by_role: {}, vet_tasks_by_building: {},
      building_days: {}, services: 0, conceptions: 0, failed_services: 0, kids_born_alive: 0,
      kids_born_dead: 0, deaths: 0, culls: 0, sales: 0, moves: 0 },
  };
}
async function run(result: DailyOpsResult) {
  server.use(http.post("/api/ops-sim/run", () => HttpResponse.json({ result, ledger: null })));
  const view = renderWithProviders(<OutputBoundary><OpsSimulationPage /></OutputBoundary>);
  await view.waitForAuthIdle();
  fireEvent.click(await screen.findByRole("button", { name: /^Run 90 days/ }));
  await screen.findByRole("button", { name: /Go to day 1/ });
  expect(screen.queryByText("Operational output failed.")).not.toBeInTheDocument();
}

it.each(["constructor", "toString", "__proto__"])("keeps the public unknown building code %s visible across operational output", async (building) => {
  const result = output();
  result.days[0].feeding = [{ day: 1, building, recipe: "A", recipe_display: "Public ration",
    heads: 1, kg_per_head: 1, daily_kg: 1, morning_kg: 0.4, afternoon_kg: 0.2, night_kg: 0.4 }];
  result.days[0].occupancy = [{ building, heads: 1 }];
  result.days[0].tasks = [{ day: 1, date, time: "08:00", category: "FEED", building,
    building_name: "", role: "FEEDER", headline: "Public duty", detail: "Measured work" }];
  result.transition_counts = [{ from_bucket: building, to_bucket: "BREEDING", context: "public transition", count: 1 }];
  result.journeys = [{ tag: "Public path", sex: "F", born_day: null, dam_tag: null, start_bucket: building,
    final_status: "ACTIVE", final_bucket: building, exit_day: null, exit_kind: null, exit_reason: "",
    hops: [{ day: 1, from_bucket: building, to_bucket: "BREEDING", context: "public hop" }] }];
  await run(result);
  const feedRow = screen.getByText(/Public ration/).closest("tr")!;
  expect(within(feedRow).getAllByRole("cell")[0]).toHaveTextContent(`${building} — Public ration`);
  const taskRow = screen.getByText("Public duty").closest("tr")!;
  expect(within(taskRow).getAllByRole("cell")[2]).toHaveTextContent(building);
  const journeyRow = screen.getByText("Public path").closest("tr")!;
  expect(within(journeyRow).getAllByRole("cell")[4]).toHaveTextContent(building);
  expect(within(journeyRow).getByText(building, { exact: true })).toHaveAttribute("title", building);
});

it.each([
  { name: "", displayed: "Breeding Bucket" },
  { name: "Manager-defined North Shed", displayed: "Manager-defined North Shed" },
])("keeps a meaningful duty location for building_name '$name'", async ({ name, displayed }) => {
  const result = output();
  const task: SimTask = { day: 1, date, time: "08:00", category: "FEED", building: "BREEDING",
    building_name: name, role: "FEEDER", headline: "Location-aware duty", detail: "Measured work" };
  result.days[0].tasks = [task];
  await run(result);
  expect(within(screen.getByText("Location-aware duty").closest("tr")!).getAllByRole("cell")[2]).toHaveTextContent(displayed);
});

it("identifies an unknown exit kind without losing the recorded exit day and reason", async () => {
  const result = output();
  result.journeys = [{ tag: "Archived animal", sex: "F", born_day: null, dam_tag: null, start_bucket: "BREEDING",
    final_status: "SOLD", final_bucket: null, exit_day: 3, exit_kind: "", exit_reason: "Public archived exit", hops: [] }];
  await run(result);
  expect(within(screen.getByText("Archived animal").closest("tr")!).getAllByRole("cell")[4])
    .toHaveTextContent(/^— on day 3 — Public archived exit$/);
});

it("preserves a recorded zero day for birth and exit instead of substituting unknown metadata", async () => {
  const result = output();
  result.journeys = [{ tag: "Zero-day animal", sex: "F", born_day: 0, dam_tag: null, start_bucket: "BREEDING",
    final_status: "SOLD", final_bucket: null, exit_day: 0, exit_kind: "SOLD", exit_reason: "Boundary record", hops: [] }];
  await run(result);
  const row = screen.getByText("Zero-day animal").closest("tr")!;
  expect(within(row).getAllByRole("cell")[2]).toHaveTextContent(/^0$/);
  expect(row).toHaveTextContent("SOLD on day 0 — Boundary record");
});
