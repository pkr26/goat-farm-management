import { fireEvent, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { HealthRoundProgress } from "./health-round-progress";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/health", useSearchParams: () => new URLSearchParams() }));

const round = {
  task_id: 42, task_status: "PENDING", initialized: true, snapshot_at: "2026-10-03T12:00:00Z",
  required_components: ["Enterotoxaemia (ET)", "Haemorrhagic Septicaemia (HS)"],
  total_targets: 2, excluded_targets: 0, covered_targets: 0, remaining_units: 3,
  available_additions: 0, limit: 50, offset: 0,
  targets: [{ animal_id: 8, animal_tag: "GOAT-8", animal_status: "ACTIVE", current_bucket: "FOUNDATION",
    covered_components: ["Haemorrhagic Septicaemia (HS)"], exclusion_reason: null, excluded_by_id: null,
    excluded_at: null, inclusion_reason: null, added_at: "2026-10-03T12:00:00Z" }],
};

describe("HealthRoundProgress", () => {
  it("requires explicit start and displays the declared component coverage", async () => {
    let started = false;
    server.use(
      http.get("/api/health/rounds/42", () => HttpResponse.json({ ...round, initialized: started })),
      http.post("/api/health/rounds/42/start", () => { started = true; return HttpResponse.json(round); }),
    );
    const change = vi.fn();
    renderWithProviders(<HealthRoundProgress taskId={42} component="" onComponentChange={change} onCohortChange={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Start herd round" }));
    const component = await screen.findByRole("combobox", { name: "Component being recorded" });
    fireEvent.change(component, { target: { value: "Enterotoxaemia (ET)" } });
    expect(change).toHaveBeenCalledWith("Enterotoxaemia (ET)");
    expect(screen.getByText(/3 component doses remaining/)).toBeInTheDocument();
    expect(screen.getByText(/Recorded components: Haemorrhagic/)).toBeInTheDocument();
  });

  it("requires a reason and submits only the selected targets for exclusion", async () => {
    let body: unknown;
    server.use(
      http.get("/api/health/rounds/42", () => HttpResponse.json(round)),
      http.post("/api/health/rounds/42/exclusions", async ({ request }) => {
        body = await request.json(); return HttpResponse.json(round);
      }),
    );
    const changed = vi.fn();
    renderWithProviders(<HealthRoundProgress taskId={42} component="" onComponentChange={vi.fn()} onCohortChange={changed} />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select GOAT-8 for exclusion" }));
    const exclude = screen.getByRole("button", { name: "Exclude selected targets" });
    expect(exclude).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Reason for cohort change" }), { target: { value: "Vet deferred dose" } });
    fireEvent.click(exclude);
    await waitFor(() => expect(body).toEqual({ animal_ids: [8], reason: "Vet deferred dose" }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  });
});
