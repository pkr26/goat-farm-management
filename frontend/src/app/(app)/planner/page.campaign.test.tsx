/**
 * PlannerPage — fresh-domain mutation campaign kills (2026-09): snapshot and
 * calibration failure copy, the save-plan validation toast, the delete-plan
 * confirmation contract, the month-input guard, permission gating of the
 * reference queries, and the farm-switch guard on the save continuation.
 */

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { setCurrentFarmId } from "@/lib/api-client";
import { permissionsHandler, server, TEST_FARMS } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import PlannerPage, { NumberField } from "./page";
import { settle } from "@/test/settle";

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMocks }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/planner",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

const GOAT_DEFAULTS = {
  meta: { horizon_months: 60, start_year_month: "2026-01" },
  herd: { does: 50, bucks: 2 },
  reproduction: { gestation_months: 5, conception_rate: 0.85, litter_size: 1.6 },
  growth: { sale_age_months: 9 },
  mortality: { kid_pre_weaning: 0.15, kid_post_weaning: 0.05 },
  sales: { meat_price_per_kg: 400 },
};

const SAVED_PLAN = {
  id: 12,
  name: "Diwali sale push",
  start_year_month: "2026-01",
  revision: 3,
  valid: true,
  created_at: "2026-08-01T10:00:00",
  updated_at: "2026-08-02T10:00:00",
  targets: [{ year_month: "2027-01", animal_class: "doe", count: 120 }],
  assumptions: GOAT_DEFAULTS,
};

interface Options {
  permissions?: string[];
  snapshotNetworkError?: boolean;
  calibrationNetworkError?: boolean;
  savedPlans?: unknown[];
  breeds?: string[];
  onPlanRequest?: () => void;
}

function installHandlers(options: Options = {}) {
  server.use(
    http.get("/api/auth/farms", () => HttpResponse.json(TEST_FARMS)),
    ...(options.permissions ? [permissionsHandler(options.permissions)] : []),
    http.get("/api/simulation/defaults/breeds", () =>
      HttpResponse.json({
        breeds: options.breeds ?? ["osmanabadi", "sirohi"],
        systems: ["stall_fed", "semi_intensive"],
      }),
    ),
    http.get("/api/simulation/defaults", () => HttpResponse.json(GOAT_DEFAULTS)),
    http.get("/api/simulation/herd-snapshot", () =>
      options.snapshotNetworkError
        ? HttpResponse.error()
        : HttpResponse.json({
            does: 50,
            bucks: 2,
            f_kids: 3,
            f_weaners: 2,
            f_growers: 1,
            m_kids: 3,
            m_weaners: 2,
            m_growers: 1,
            total_head: 64,
          }),
    ),
    http.get("/api/simulation/calibration", () =>
      options.calibrationNetworkError
        ? HttpResponse.error()
        : HttpResponse.json({ assumptions: GOAT_DEFAULTS, evidence: [1, 2, 3] }),
    ),
    http.get("/api/planner/plans", () =>
      HttpResponse.json({
        items: options.savedPlans ?? [],
        total: (options.savedPlans ?? []).length,
        limit: 50,
        offset: 0,
      }),
    ),
    http.post("/api/planner/plans", () => {
      options.onPlanRequest?.();
      return new Promise(() => undefined);
    }),
  );
}

async function renderLoadedPlanner(options: Options = {}) {
  installHandlers(options);
  const result = renderWithProviders(<PlannerPage />);
  await screen.findByText("Plan basis");
  return result;
}

describe("PlannerPage — campaign kills", () => {
  beforeEach(() => {
    toastMocks.error.mockClear();
    toastMocks.success.mockClear();
  });

  it("keeps the reference queries off without simulation.view", async () => {
    let defaultsRequests = 0;
    server.use(
      permissionsHandler(["dashboard.view"]),
      http.get("/api/simulation/defaults/breeds", () => {
        defaultsRequests += 1;
        return HttpResponse.json({ breeds: [], systems: [] });
      }),
      http.get("/api/simulation/defaults", () => {
        defaultsRequests += 1;
        return HttpResponse.json(GOAT_DEFAULTS);
      }),
    );
    renderWithProviders(<PlannerPage />);
    expect(
      await screen.findByText("You don't have access to this page."),
    ).toBeInTheDocument();
    await settle(50);
    expect(defaultsRequests).toBe(0);
  });

  it("surfaces the herd-snapshot failure copy", async () => {
    await renderLoadedPlanner({ snapshotNetworkError: true });

    await userEvent.click(screen.getByRole("button", { name: "Use my herd" }));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("Could not load the herd snapshot."),
    );
  });

  it("surfaces the calibration failure copy", async () => {
    await renderLoadedPlanner({ calibrationNetworkError: true });

    await userEvent.click(screen.getByRole("button", { name: "Use farm records" }));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("Could not calibrate from farm records."),
    );
  });

  it("keeps a farm switch mid-save from reporting on the new farm", async () => {
    const { waitForAuthIdle } = await renderLoadedPlanner({
      onPlanRequest: () => {
        // The write is on the wire: leave the farm before it settles.
        act(() => setCurrentFarmId("77"));
      },
    });
    await waitForAuthIdle();
    await userEvent.type(screen.getByLabelText("Plan name"), "Never lands");
    // A target row is required for the save to reach the network.
    await userEvent.click(screen.getByRole("button", { name: "Add target" }));

    await userEvent.click(screen.getByRole("button", { name: "Save plan" }));
    await settle(100);
    expect(toastMocks.success).not.toHaveBeenCalled();
    expect(toastMocks.error).not.toHaveBeenCalledWith("Could not save the plan.");
  });

  it("stays silent when the farm changes under an in-flight herd snapshot", async () => {
    let releaseSnapshot!: () => void;
    server.use(
      http.get("/api/simulation/herd-snapshot", () =>
        new Promise((resolve) => {
          releaseSnapshot = () => resolve(HttpResponse.json({ total_head: 64 }));
        }),
      ),
      http.get("/api/simulation/calibration", () =>
        HttpResponse.json({ assumptions: GOAT_DEFAULTS, evidence: [1] }),
      ),
      http.get("/api/simulation/defaults/breeds", () =>
        HttpResponse.json({ breeds: ["osmanabadi"], systems: ["stall_fed"] }),
      ),
      http.get("/api/simulation/defaults", () => HttpResponse.json(GOAT_DEFAULTS)),
      http.get("/api/planner/plans", () =>
        HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 }),
      ),
      http.post("/api/planner/plans", () => new Promise(() => undefined)),
    );
    const user = userEvent.setup();
    renderWithProviders(<PlannerPage />);
    await screen.findByText("Plan basis");

    await user.click(screen.getByRole("button", { name: "Use my herd" }));
    await waitFor(() => expect(releaseSnapshot).toBeDefined());
    act(() => setCurrentFarmId("77"));
    releaseSnapshot();
    await settle(100);
    expect(toastMocks.error).not.toHaveBeenCalled();
    expect(toastMocks.success).not.toHaveBeenCalled();
  });

  it("stays silent when the farm changes under an in-flight calibration", async () => {
    let releaseCalibration!: () => void;
    server.use(
      http.get("/api/simulation/herd-snapshot", () =>
        HttpResponse.json({ total_head: 64 }),
      ),
      http.get("/api/simulation/calibration", () =>
        new Promise((resolve) => {
          releaseCalibration = () =>
            resolve(HttpResponse.json({ assumptions: GOAT_DEFAULTS, evidence: [1] }));
        }),
      ),
      http.get("/api/simulation/defaults/breeds", () =>
        HttpResponse.json({ breeds: ["osmanabadi"], systems: ["stall_fed"] }),
      ),
      http.get("/api/simulation/defaults", () => HttpResponse.json(GOAT_DEFAULTS)),
      http.get("/api/planner/plans", () =>
        HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 }),
      ),
      http.post("/api/planner/plans", () => new Promise(() => undefined)),
    );
    const user = userEvent.setup();
    renderWithProviders(<PlannerPage />);
    await screen.findByText("Plan basis");

    await user.click(screen.getByRole("button", { name: "Use farm records" }));
    await waitFor(() => expect(releaseCalibration).toBeDefined());
    act(() => setCurrentFarmId("77"));
    releaseCalibration();
    await settle(100);
    expect(toastMocks.error).not.toHaveBeenCalled();
    expect(toastMocks.success).not.toHaveBeenCalled();
  });

  it("stays silent when the farm changes under an in-flight plan deletion", async () => {
    let releaseDelete!: () => void;
    server.use(
      http.get("/api/planner/plans", () =>
        HttpResponse.json({ items: [SAVED_PLAN], total: 1, limit: 50, offset: 0 }),
      ),
      http.delete("/api/planner/plans/12", () =>
        new Promise((resolve) => {
          releaseDelete = () => resolve(new HttpResponse(null, { status: 204 }));
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<PlannerPage />);
    await screen.findAllByText("Diwali sale push");

    await user.click(screen.getAllByRole("button", { name: "Delete plan Diwali sale push" })[0]!);
    const dialog = await screen.findByRole("dialog", { name: "Delete plan?" });
    await user.click(within(dialog).getByRole("button", { name: "Delete plan" }));
    await waitFor(() => expect(releaseDelete).toBeDefined());
    act(() => setCurrentFarmId("77"));
    releaseDelete();
    await settle(100);
    expect(toastMocks.success).not.toHaveBeenCalled();
  });

  it("stays silent when a herd snapshot FAILS after the farm changed", async () => {
    let failSnapshot!: () => void;
    server.use(
      http.get("/api/simulation/herd-snapshot", () =>
        new Promise((_resolve, reject) => {
          failSnapshot = () => reject(new Error("boom"));
        }),
      ),
      http.get("/api/simulation/calibration", () =>
        HttpResponse.json({ assumptions: GOAT_DEFAULTS, evidence: [1] }),
      ),
      http.get("/api/simulation/defaults/breeds", () =>
        HttpResponse.json({ breeds: ["osmanabadi"], systems: ["stall_fed"] }),
      ),
      http.get("/api/simulation/defaults", () => HttpResponse.json(GOAT_DEFAULTS)),
      http.get("/api/planner/plans", () =>
        HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 }),
      ),
      http.post("/api/planner/plans", () => new Promise(() => undefined)),
    );
    const user = userEvent.setup();
    renderWithProviders(<PlannerPage />);
    await screen.findByText("Plan basis");

    await user.click(screen.getByRole("button", { name: "Use my herd" }));
    await waitFor(() => expect(failSnapshot).toBeDefined());
    toastMocks.error.mockClear();
    act(() => setCurrentFarmId("77"));
    await act(async () => {
      failSnapshot();
      await settle(50);
    });
    expect(toastMocks.error).not.toHaveBeenCalled();
  });

  it("stays silent when a save SUCCEEDS after the farm changed", async () => {
    let releaseSave!: () => void;
    const { waitForAuthIdle } = await renderLoadedPlanner();
    await waitForAuthIdle();
    const user = userEvent.setup();
    // Registered AFTER the harness so this handler takes precedence.
    server.use(
      http.post("/api/planner/plans", () =>
        new Promise((resolve) => {
          releaseSave = () =>
            resolve(HttpResponse.json({ ...SAVED_PLAN, name: "Never lands" }, { status: 201 }));
        }),
      ),
    );
    await user.type(screen.getByLabelText("Plan name"), "Never lands");
    await user.click(screen.getByRole("button", { name: "Add target" }));
    await user.click(screen.getByRole("button", { name: "Save plan" }));
    await waitFor(() => expect(releaseSave).toBeDefined());
    toastMocks.success.mockClear();
    toastMocks.error.mockClear();
    act(() => setCurrentFarmId("77"));
    await act(async () => {
      releaseSave();
      await settle(50);
    });
    expect(toastMocks.success).not.toHaveBeenCalled();
  });

  it("stays silent when a save FAILS after the farm changed", async () => {
    let failSave!: () => void;
    const { waitForAuthIdle } = await renderLoadedPlanner();
    await waitForAuthIdle();
    const user = userEvent.setup();
    server.use(
      http.post("/api/planner/plans", () =>
        new Promise((_resolve, reject) => {
          failSave = () => reject(new Error("boom"));
        }),
      ),
    );
    await user.type(screen.getByLabelText("Plan name"), "Never lands");
    await user.click(screen.getByRole("button", { name: "Add target" }));
    await user.click(screen.getByRole("button", { name: "Save plan" }));
    await waitFor(() => expect(failSave).toBeDefined());
    toastMocks.error.mockClear();
    act(() => setCurrentFarmId("77"));
    await act(async () => {
      failSave();
      await settle(50);
    });
    expect(toastMocks.error).not.toHaveBeenCalled();
  });

  it("stays silent when a calibration FAILS after the farm changed", async () => {
    let failCalibration!: () => void;
    server.use(
      http.get("/api/simulation/calibration", () =>
        new Promise((_resolve, reject) => {
          failCalibration = () => reject(new Error("boom"));
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<PlannerPage />);
    await screen.findByText("Plan basis");

    await user.click(screen.getByRole("button", { name: "Use farm records" }));
    await waitFor(() => expect(failCalibration).toBeDefined());
    toastMocks.error.mockClear();
    act(() => setCurrentFarmId("77"));
    await act(async () => {
      failCalibration();
      await settle(50);
    });
    expect(toastMocks.error).not.toHaveBeenCalled();
  });

  it("keeps an invalid month spelling out of the plan state", async () => {
    await renderLoadedPlanner();
    const monthInput = screen.getByLabelText("Plan start");
    const before = (monthInput as HTMLInputElement).value;
    expect(before).toMatch(/^\d{4}-\d{2}$/);

    fireEvent.change(monthInput, { target: { value: "garbage" } });
    expect((monthInput as HTMLInputElement).value).toBe(before);
  });

  it("confirms deletions by name and cancels cleanly", async () => {
    await renderLoadedPlanner({ savedPlans: [SAVED_PLAN] });
    await screen.findAllByText("Diwali sale push");

    await userEvent.click(screen.getAllByRole("button", { name: "Delete plan Diwali sale push" })[0]!);
    const dialog = await screen.findByRole("dialog", { name: "Delete plan?" });
    expect(within(dialog).getByText("Diwali sale push")).toBeInTheDocument();
    expect(within(dialog).getByText(/and its saved assumptions/)).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Delete plan?" })).not.toBeInTheDocument(),
    );
    // The plan row is untouched after a cancelled deletion.
    expect(screen.getAllByText("Diwali sale push").length).toBeGreaterThan(0);
  });

  it("deletes the plan after confirmation", async () => {
    let deleted = 0;
    server.use(
      http.get("/api/planner/plans", () =>
        HttpResponse.json({ items: [SAVED_PLAN], total: 1, limit: 50, offset: 0 }),
      ),
      http.delete("/api/planner/plans/12", () => {
        deleted += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithProviders(<PlannerPage />);
    await screen.findAllByText("Diwali sale push");

    await userEvent.click(screen.getAllByRole("button", { name: "Delete plan Diwali sale push" })[0]!);
    const dialog = await screen.findByRole("dialog", { name: "Delete plan?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete plan" }));
    await waitFor(() => expect(deleted).toBe(1));
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("Deleted “Diwali sale push”."),
    );
  });

  it("closes the delete dialog on Escape and clears the staged plan", async () => {
    await renderLoadedPlanner({ savedPlans: [SAVED_PLAN] });
    await screen.findAllByText("Diwali sale push");

    await userEvent.click(screen.getAllByRole("button", { name: "Delete plan Diwali sale push" })[0]!);
    await screen.findByRole("dialog", { name: "Delete plan?" });
    await userEvent.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Delete plan?" })).not.toBeInTheDocument(),
    );
    // Reopening yields a fresh dialog — the staged plan was cleared on close.
    await userEvent.click(screen.getAllByRole("button", { name: "Delete plan Diwali sale push" })[0]!);
    expect(await screen.findByRole("dialog", { name: "Delete plan?" })).toBeInTheDocument();
  });

  it("shows the destructive copy with the plan name emphasized while deleting", async () => {
    let releaseDelete!: () => void;
    server.use(
      http.get("/api/planner/plans", () =>
        HttpResponse.json({ items: [SAVED_PLAN], total: 1, limit: 50, offset: 0 }),
      ),
      http.delete("/api/planner/plans/12", () =>
        new Promise((resolve) => {
          releaseDelete = () => resolve(new HttpResponse(null, { status: 204 }));
        }),
      ),
    );
    renderWithProviders(<PlannerPage />);
    await screen.findAllByText("Diwali sale push");

    await userEvent.click(screen.getAllByRole("button", { name: "Delete plan Diwali sale push" })[0]!);
    const dialog = await screen.findByRole("dialog", { name: "Delete plan?" });
    expect(within(dialog).getByText(/This permanently deletes/)).toBeInTheDocument();
    expect(within(dialog).getByText("Diwali sale push")).toHaveClass("font-medium");

    await userEvent.click(within(dialog).getByRole("button", { name: "Delete plan" }));
    expect(await within(dialog).findByRole("button", { name: "Deleting…" })).toBeDisabled();
    releaseDelete();
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("Deleted “Diwali sale push”."),
    );
  });

  it("accepts a valid start-month change and keeps garbage out", async () => {
    await renderLoadedPlanner();
    const monthInput = screen.getByLabelText("Plan start");
    const before = (monthInput as HTMLInputElement).value;

    fireEvent.change(monthInput, { target: { value: "2025-01" } });
    expect((monthInput as HTMLInputElement).value).toBe("2025-01");
    expect(screen.getByText(/starting Jan 2025/i)).toBeInTheDocument();

    fireEvent.change(monthInput, { target: { value: "garbage" } });
    expect((monthInput as HTMLInputElement).value).toBe("2025-01");
    expect(before).not.toBe("2025-01");
  });

  it("prefers the server's error detail over the fallback copy (snapshot + calibration)", async () => {
    const { waitForAuthIdle } = await renderLoadedPlanner();
    await waitForAuthIdle();
    await screen.findByText(/Starting from breed-preset defaults./);
    // Registered AFTER the harness so these handlers take precedence.
    server.use(
      http.get("/api/simulation/herd-snapshot", () =>
        HttpResponse.json({ detail: "Herd census unavailable." }, { status: 500 }),
      ),
      http.get("/api/simulation/calibration", () =>
        HttpResponse.json({ detail: "Records too sparse." }, { status: 500 }),
      ),
    );

    await userEvent.click(screen.getByRole("button", { name: "Use my herd" }));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("Herd census unavailable."),
    );

    await userEvent.click(screen.getByRole("button", { name: "Use farm records" }));
    await waitFor(() => expect(toastMocks.error).toHaveBeenCalledWith("Records too sparse."));
    expect(toastMocks.error).not.toHaveBeenCalledWith("Could not load the herd snapshot.");
    expect(toastMocks.error).not.toHaveBeenCalledWith("Could not calibrate from farm records.");
  });

  it("keeps an in-flight plan run from erroring on the new farm", async () => {
    let failPlan!: () => void;
    const { waitForAuthIdle } = await renderLoadedPlanner();
    await waitForAuthIdle();
    server.use(
      http.post("/api/planner/plan", () =>
        new Promise((_resolve, reject) => {
          failPlan = () => reject(new Error("boom"));
        }),
      ),
    );
    await userEvent.click(screen.getByRole("button", { name: "Add target" }));
    await userEvent.click(screen.getByRole("button", { name: /^Plan$/ }));
    await waitFor(() => expect(failPlan).toBeDefined());
    toastMocks.error.mockClear();
    act(() => setCurrentFarmId("77"));
    await act(async () => {
      failPlan();
      await settle(50);
    });
    expect(toastMocks.error).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps an in-flight update from erroring on the new farm", async () => {
    let failUpdate!: () => void;
    const { waitForAuthIdle } = await renderLoadedPlanner({ savedPlans: [SAVED_PLAN] });
    await waitForAuthIdle();
    server.use(
      http.patch("/api/planner/plans/12", () =>
        new Promise((_resolve, reject) => {
          failUpdate = () => reject(new Error("boom"));
        }),
      ),
    );
    await screen.findAllByText("Diwali sale push");
    await userEvent.click(screen.getAllByRole("button", { name: /^Open$/ })[0]!);
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith(
        "Opened “Diwali sale push” — press Plan to re-run it against today's biology.",
      ),
    );

    await userEvent.click(screen.getByRole("button", { name: "Update “Diwali sale push”" }));
    await waitFor(() => expect(failUpdate).toBeDefined());
    toastMocks.error.mockClear();
    act(() => setCurrentFarmId("77"));
    await act(async () => {
      failUpdate();
      await settle(50);
    });
    expect(toastMocks.error).not.toHaveBeenCalled();
  });

  it("keeps an in-flight deletion from erroring on the new farm", async () => {
    let failDelete!: () => void;
    server.use(
      http.get("/api/planner/plans", () =>
        HttpResponse.json({ items: [SAVED_PLAN], total: 1, limit: 50, offset: 0 }),
      ),
      http.delete("/api/planner/plans/12", () =>
        new Promise((_resolve, reject) => {
          failDelete = () => reject(new Error("boom"));
        }),
      ),
    );
    renderWithProviders(<PlannerPage />);
    await screen.findAllByText("Diwali sale push");

    await userEvent.click(screen.getAllByRole("button", { name: "Delete plan Diwali sale push" })[0]!);
    const dialog = await screen.findByRole("dialog", { name: "Delete plan?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete plan" }));
    await waitFor(() => expect(failDelete).toBeDefined());
    toastMocks.error.mockClear();
    act(() => setCurrentFarmId("77"));
    await act(async () => {
      failDelete();
      await settle(50);
    });
    expect(toastMocks.error).not.toHaveBeenCalled();
  });

  it("refuses to update a saved plan whose name was cleared", async () => {
    let patches = 0;
    server.use(
      http.get("/api/planner/plans", () =>
        HttpResponse.json({ items: [SAVED_PLAN], total: 1, limit: 50, offset: 0 }),
      ),
      http.patch("/api/planner/plans/12", () => {
        patches += 1;
        return HttpResponse.json(SAVED_PLAN);
      }),
    );
    renderWithProviders(<PlannerPage />);
    await screen.findAllByText("Diwali sale push");
    await userEvent.click(screen.getAllByRole("button", { name: /^Open$/ })[0]!);
    await screen.findByRole("button", { name: "Update “Diwali sale push”" });

    const nameInput = screen.getByLabelText("Plan name") as HTMLInputElement;
    await userEvent.clear(nameInput);
    // RT-P2-6: the cleared name disables Update outright (mirroring Save) —
    // a disabled button cannot submit, so no PATCH leaves the page.
    expect(
      screen.getByRole("button", { name: "Update “Diwali sale push”" }),
    ).toBeDisabled();
    expect(patches).toBe(0);
  });

  it("re-arms the preset adoption after opening a saved plan", async () => {
    await renderLoadedPlanner({
      savedPlans: [SAVED_PLAN],
      breeds: ["osmanabadi", "beetal_goat"],
    });
    await screen.findAllByText("Diwali sale push");
    await userEvent.click(screen.getAllByRole("button", { name: /^Open$/ })[0]!);
    expect(
      await screen.findByText(/Starting from a saved plan's assumptions/),
    ).toBeInTheDocument();

    // Switching the breed preset is a new intent: its defaults must be adopted.
    await userEvent.click(screen.getByRole("combobox", { name: "Breed preset" }));
    await userEvent.click(await screen.findByRole("option", { name: "beetal goat" }));
    expect(await screen.findByText(/Starting from breed-preset defaults./)).toBeInTheDocument();
  });

  it("names the breed with underscores spaced in the herd basis note", async () => {
    const { waitForAuthIdle } = await renderLoadedPlanner({
      breeds: ["osmanabadi", "beetal_goat"],
    });
    await waitForAuthIdle();
    await screen.findByText(/Starting from breed-preset defaults./);

    await userEvent.click(screen.getByRole("combobox", { name: "Breed preset" }));
    await userEvent.click(await screen.findByRole("option", { name: "beetal goat" }));
    await userEvent.click(screen.getByRole("button", { name: "Use my herd" }));
    await waitFor(() =>
      expect(
        screen.getByText(/on top of the beetal goat preset\./, { selector: '[role="note"]' }),
      ).toBeInTheDocument(),
    );
  });

  it("renders the full plan report and flags staleness after edits", async () => {
    const report = {
      start_year_month: "2025-06",
      plan: {
        gaps_closed: true,
        recommended_purchases: [
          { year_month: "2025-08", animal_class: "doe", count: 5 },
        ],
        before: {
          npv: 1000000.5,
          total_shortfall: 12,
          targets: [
            {
              month: "2027-09",
              animal_class: "male_grower",
              requested: 200,
              filled: 180,
              met: false,
              price_per_head: 4500.25,
            },
          ],
        },
        after: {
          npv: 1200000.75,
          total_shortfall: null,
          targets: [
            {
              month: "2027-09",
              animal_class: "male_grower",
              requested: 200,
              filled: 200,
              met: true,
              price_per_head: 4500.25,
            },
          ],
        },
        probabilities: [{ p_full: 0.872 }],
      },
      targets_echo: [{ year_month: "2027-09", animal_class: "male_grower", count: 200 }],
      actions: [
        { kind: "breed", year_month: "2025-01", headline: "Breed 40 does", detail: "now" },
        { kind: "retain", year_month: "2025-08", headline: "Retain 12 doelings", detail: "pick well" },
      ],
      chains: [
        {
          animal_class: "male_grower",
          year_month: "2027-09",
          count: 200,
          achievable: true,
          steps: [{ year_month: "2025-08", quantity: 40, label: "does bred" }],
          explanation: "Survival, sex share and conception worked backward.",
        },
      ],
      stage_plan: [
        {
          year_month: "2025-06",
          female_kids: null,
          male_kids: 3.25,
          female_weaners: null,
          male_weaners: 1,
          female_growers: 2,
          male_growers: 4,
          open_does: 40,
          pregnant_does: 10,
          lactating_does: 0,
          bucks: 2,
          total_head: 62.25,
          births: 9.9,
          deaths: 0.5,
          culls_head: 1,
          sales_head: 0,
          purchases_head: 5,
        },
      ],
      notes: ["Pasture lease assumes 2 acres per 10 does."],
    };
    // JSON can carry Infinity only as a literal exponent; births must reach
    // formatHead as Infinity so the non-finite arm is exercised for real.
    const reportJson = JSON.stringify(report).replace('"births":9.9', '"births":1e999');
    server.use(
      http.post(
        "/api/planner/plan",
        () =>
          new HttpResponse(reportJson, { headers: { "Content-Type": "application/json" } }),
      ),
    );
    const { waitForAuthIdle } = await renderLoadedPlanner();
    await waitForAuthIdle();
    await screen.findByText(/Starting from breed-preset defaults./);

    await userEvent.click(screen.getByRole("button", { name: "Add target" }));
    await userEvent.click(screen.getByRole("button", { name: /^Plan$/ }));

    // Fresh report: feasible title, no stale suffix, dash for a null shortfall.
    expect(await screen.findByText("The plan is feasible")).toBeInTheDocument();
    expect(screen.queryByText(/stale — re-run after edits/)).not.toBeInTheDocument();
    expect(screen.getByText(/Shortfall — head/)).toBeInTheDocument();
    expect(screen.getAllByText("87%")[0]!).toBeInTheDocument();
    expect(screen.getByText("does bred")).toBeInTheDocument();
    expect(screen.getByText("Pasture lease assumes 2 acres per 10 does.")).toBeInTheDocument();
    expect(screen.queryByText("Stryker was here")).not.toBeInTheDocument();

    // Missed deadlines tint against the REPORT's start month, not the input.
    const missedRow = screen.getByText("Breed 40 does").closest("div")?.parentElement;
    const onTimeRow = screen.getByText("Retain 12 doelings").closest("div")?.parentElement;
    expect(missedRow?.className).toContain("bg-warning-tint/30");
    expect(onTimeRow?.className).not.toContain("bg-warning-tint/30");

    // Stage table: null and non-finite cohort cells print as dashes.
    const stageTable = screen
      .getAllByRole("table")
      .find((table) => within(table).queryByText("Open does") !== null);
    expect(stageTable).toBeDefined();
    const stageRow = within(stageTable!).getAllByRole("row")[1]!;
    const stageCells = within(stageRow).getAllByRole("cell");
    expect(stageCells[1]!.textContent).toBe("—");
    expect(stageCells[2]!.textContent).toBe("3.3");
    expect(stageCells[12]!.textContent).toBe("—");

    // Editing an input marks the rendered report stale.
    fireEvent.change(screen.getByLabelText("Plan start"), { target: { value: "2025-01" } });
    expect(await screen.findByText(/stale — re-run after edits/)).toBeInTheDocument();
  });
});

describe("NumberField — campaign kills", () => {
  it("renders an empty string for a nullish stored value and commits typed numbers", async () => {
    const onCommitNumber = vi.fn();
    render(<NumberField aria-label="count" value={null as unknown as number} onCommitNumber={onCommitNumber} />);
    const input = screen.getByLabelText("count") as HTMLInputElement;
    expect(input.value).toBe("");

    const user = userEvent.setup();
    await user.type(input, "42");
    expect(onCommitNumber).toHaveBeenLastCalledWith(42);

    await user.clear(input);
    expect(onCommitNumber).not.toHaveBeenCalledWith(NaN);
  });
});

describe("PlannerPage — null-evaluation report guard", () => {
  it("renders nothing for a report whose before and after plans are both null", async () => {
    const emptyReport = {
      start_year_month: "2025-06",
      plan: {
        gaps_closed: false,
        recommended_purchases: [],
        before: null,
        after: null,
      },
      targets_echo: [],
      actions: [],
      chains: [],
      stage_plan: [],
      notes: [],
    };
    server.use(
      http.post(
        "/api/planner/plan",
        () => new HttpResponse(JSON.stringify(emptyReport), { headers: { "Content-Type": "application/json" } }),
      ),
      http.get("/api/planner/plans", () =>
        HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<PlannerPage />);
    await screen.findByText("Plan basis");
    await screen.findByText(/Starting from breed-preset defaults\./);

    await user.click(screen.getByRole("button", { name: /^Add target$/ }));
    await user.click(screen.getByRole("button", { name: /^Plan$/ }));
    await settle(100);
    // A report with neither a before nor an after plan renders no report
    // section — dereferencing a null evaluation must never happen.
    expect(screen.queryByText("What to do and when")).not.toBeInTheDocument();
    expect(screen.queryByText("Why these numbers")).not.toBeInTheDocument();
  });
});
