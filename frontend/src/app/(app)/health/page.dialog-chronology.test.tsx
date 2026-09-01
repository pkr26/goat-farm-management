/**
 * Health page — the record-event dialog's decision points that the behaviour
 * suites leave unpinned: chronology boundaries that must stay ACCEPTED, which
 * duty queries the dialog is allowed to fire at all, which deep-linked duties
 * are eligible, the prefill bookkeeping when a linked duty is swapped or
 * unlinked (manual edits survive, the previous duty's guesses do not), the
 * seeded-vs-free-text schedule field, and dismissal navigation.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { HealthEventOut, TaskOut } from "@/api/generated/models";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { farmToday } from "@/lib/format";

import HealthPage from "./page";

const pushMock = vi.fn();
const replaceMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/health",
  useSearchParams: () => new URLSearchParams(window.location.search),
  useParams: () => ({}),
}));

type User = ReturnType<typeof userEvent.setup>;

beforeAll(() => {
  // jsdom lacks APIs Radix Select/Popper touch when opening the listbox.
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

const TODAY = farmToday();

const HEALTH_ANIMALS = [
  { id: 3, tag_number: "G-003", name: "Kaveri", current_bucket: "LACTATING" },
  { id: 4, tag_number: "G-004", name: null, current_bucket: "DRY" },
];
const HEALTH_BATCHES = [
  { id: 2, active_quarantine_animal_count: 12 },
  { id: 3, active_quarantine_animal_count: 7 },
];

const RECORDED_EVENT: HealthEventOut = {
  id: 99,
  animal_id: 3,
  purchase_batch_id: null,
  date: TODAY,
  type: "VACCINE",
  product_name: null,
  disease_target: null,
  dose: null,
  route: null,
  vet_name: null,
  cost: null,
  next_due_date: null,
  schedule_template_name: null,
  next_due_authority: null,
  product_lot: null,
  product_manufactured_on: null,
  product_expires_on: null,
  vaccine_valid_until: null,
  certificate_number: null,
  official_tag_number: null,
  administered_by: null,
  withdrawal_until: null,
  suspected_scheduled_disease: false,
  authority_notified_at: null,
  isolation_started_at: null,
  notes: null,
  animal_tag: "G-003",
};

function makeTask(overrides: Partial<TaskOut>): TaskOut {
  return {
    id: 1,
    title: "Task",
    due_date: TODAY,
    status: "PENDING",
    category: "OTHER",
    auto_generated: false,
    animal_id: null,
    purchase_batch_id: null,
    breeding_record_id: null,
    assigned_role_id: null,
    assigned_user_id: null,
    recur_days: null,
    recurring_series_id: null,
    completed_by_id: null,
    completed_at: null,
    verified_by_id: null,
    verified_at: null,
    verification_note: null,
    skipped_by_id: null,
    skipped_at: null,
    skip_reason: null,
    rejected_by_id: null,
    rejected_at: null,
    action_url: null,
    ...overrides,
  };
}

const VACCINE_TASK = makeTask({
  id: 5,
  title: "PPR vaccination",
  category: "VACCINE",
  animal_id: 3,
});
const DEWORM_ANIMAL_TASK = makeTask({
  id: 8,
  title: "Deworm Kaveri — Albendazole",
  category: "DEWORMING",
  animal_id: 3,
});
const DEWORM_BATCH_TASK = makeTask({
  id: 6,
  title: "Deworm batch #2",
  category: "DEWORMING",
  purchase_batch_id: 2,
});

function tasksPayload(tasks: TaskOut[]) {
  return {
    today: tasks,
    overdue: [],
    upcoming: [],
    awaiting: [],
    completed: [],
    completed_total: 0,
    completed_limit: 50,
    completed_offset: 0,
  };
}

async function pickOption(user: User, trigger: HTMLElement, name: string | RegExp) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

describe("HealthPage dialog branches", () => {
  let postBody: Record<string, unknown> | null;
  let recordCalls: number;
  let previewBodies: Record<string, unknown>[];
  let tabCalls: number;
  let taskLookups: string[];
  let tasks: TaskOut[];
  /** Duties reachable only through the exact-id endpoint (outside the tabs). */
  let unlistedTasks: TaskOut[];

  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    postBody = null;
    recordCalls = 0;
    previewBodies = [];
    tabCalls = 0;
    taskLookups = [];
    tasks = [VACCINE_TASK];
    unlistedTasks = [];
    server.use(
      http.get("/api/health/events", () =>
        HttpResponse.json({ events: [], total: 0, limit: 50, offset: 0 }),
      ),
      http.post("/api/health/events", async ({ request }) => {
        recordCalls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json([RECORDED_EVENT], { status: 201 });
      }),
      http.post("/api/health/events/preview", async ({ request }) => {
        const target = (await request.json()) as Record<string, unknown>;
        previewBodies.push(target);
        return HttpResponse.json({
          scope: target.scope,
          bucket: target.bucket ?? null,
          purchase_batch_id: target.purchase_batch_id ?? null,
          task_id: target.task_id ?? null,
          target_animal_ids: [3, 4],
          target_animals: [
            { id: 3, tag_number: "G-003", name: "Kaveri" },
            { id: 4, tag_number: "G-004", name: null },
          ],
          target_count: 2,
          max_targets: 1000,
        });
      }),
      http.get("/api/health/animals", () =>
        HttpResponse.json({
          animals: HEALTH_ANIMALS,
          total: HEALTH_ANIMALS.length,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get("/api/health/purchase-batches", () =>
        HttpResponse.json({
          batches: HEALTH_BATCHES,
          total: HEALTH_BATCHES.length,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get("/api/tasks", () => {
        tabCalls += 1;
        return HttpResponse.json(tasksPayload(tasks));
      }),
      http.get("/api/tasks/:taskId", ({ params }) => {
        taskLookups.push(String(params.taskId));
        const task = [...tasks, ...unlistedTasks].find(
          (candidate) => String(candidate.id) === params.taskId,
        );
        return task
          ? HttpResponse.json(task)
          : HttpResponse.json({ detail: "Task not found" }, { status: 404 });
      }),
    );
  });

  afterEach(() => {
    window.history.replaceState({}, "", "/health");
  });

  async function openDialog() {
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    await screen.findByText("Event log");
    await user.click(screen.getByRole("button", { name: "Add event" }));
    return { user, dialog: await screen.findByRole("dialog") };
  }

  async function openDeepLink(search: string) {
    window.history.replaceState({}, "", `/health?${search}`);
    const view = renderWithProviders(<HealthPage />);
    return { view, dialog: await screen.findByRole("dialog") };
  }

  // ---------- traceability chronology: the accepted side of each boundary ----------

  // The API only rejects dates that are actually out of order, and an
  // undated expiry is normal for a product whose label carries none. Each
  // guard below is an "is it BEFORE/AFTER" test, so a same-day value and a
  // blank counterpart must both save.
  it("accepts manufacture, validity and withdrawal dates that land on the event date", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Animal *" }),
      /G-003 · Kaveri/,
    );
    await user.click(within(dialog).getByText("Advanced traceability & compliance"));
    fireEvent.change(within(dialog).getByLabelText("Product manufactured"), {
      target: { value: TODAY },
    });
    fireEvent.change(within(dialog).getByLabelText("Vaccine valid until"), {
      target: { value: TODAY },
    });
    fireEvent.change(within(dialog).getByLabelText("Withdrawal until"), {
      target: { value: TODAY },
    });

    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      date: TODAY,
      product_manufactured_on: TODAY,
      product_expires_on: null,
      vaccine_valid_until: TODAY,
      withdrawal_until: TODAY,
    });
  });

  // ---------- the collapsed Advanced section ----------

  it("starts collapsed and stays collapsed when only a visible field fails", async () => {
    const { user, dialog } = await openDialog();
    expect(within(dialog).getByLabelText("Product lot/batch")).not.toBeVisible();

    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Pick an animal");
    // The only failing field is rendered right above the button — forcing the
    // compliance section open here would bury it under twelve empty inputs.
    expect(within(dialog).getByLabelText("Product lot/batch")).not.toBeVisible();
    expect(postBody).toBeNull();
  });

  // ---------- duty queries the dialog may fire ----------

  it("does not look a duty up by id when the dialog was opened without a deep link", async () => {
    const { dialog } = await openDialog();

    // The tab window has answered (it renders the picker), so any exact-id
    // lookup would have been dispatched by now.
    expect(await within(dialog).findByLabelText(/Linked duty/)).toBeInTheDocument();
    expect(taskLookups).toEqual([]);
  });

  it("queries no duties at all for a deep link the operator may not read", async () => {
    server.use(permissionsHandler(["health.view", "health.manage"]));
    const { dialog } = await openDeepLink("task_id=5&animal_id=3");

    await waitFor(() =>
      expect(within(dialog).getByRole("combobox", { name: "Animal *" })).toHaveTextContent(
        "G-003 · Kaveri",
      ),
    );
    expect(tabCalls).toBe(0);
    expect(taskLookups).toEqual([]);
    expect(within(dialog).queryByText(/Linked duty/)).not.toBeInTheDocument();
  });

  // ---------- which duties are linkable ----------

  it("keeps a duty that is no longer pending out of the picker", async () => {
    tasks = [
      VACCINE_TASK,
      makeTask({
        id: 13,
        title: "Already administered vaccination",
        category: "VACCINE",
        animal_id: 3,
        status: "COMPLETED",
      }),
    ];
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByLabelText(/Linked duty/));

    expect(await screen.findByRole("option", { name: /PPR vaccination/ })).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: /Already administered vaccination/ }),
    ).not.toBeInTheDocument();
  });

  it("refuses a deep-linked duty that is no longer pending", async () => {
    tasks = [];
    unlistedTasks = [
      makeTask({
        id: 734,
        title: "Completed PPR duty",
        category: "VACCINE",
        animal_id: 3,
        status: "COMPLETED",
      }),
    ];
    const { dialog } = await openDeepLink("task_id=734&animal_id=3");

    expect(await within(dialog).findByText(/Could not link duty #734/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/Completed PPR duty/)).not.toBeInTheDocument();
    await userEvent.setup().click(within(dialog).getByRole("button", { name: "Save event" }));
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ animal_id: 3 });
    expect(postBody!.task_id ?? null).toBeNull();
  });

  it("refuses a deep-linked duty that is not a vaccination or deworming duty", async () => {
    tasks = [];
    unlistedTasks = [
      makeTask({ id: 735, title: "Trim hooves", category: "HOOF_TRIM", animal_id: 3 }),
    ];
    const { dialog } = await openDeepLink("task_id=735&animal_id=3");

    expect(await within(dialog).findByText(/Could not link duty #735/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/Trim hooves/)).not.toBeInTheDocument();
    await userEvent.setup().click(within(dialog).getByRole("button", { name: "Save event" }));
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody!.task_id ?? null).toBeNull();
  });

  it("resolves a deep-linked deworming duty the task tabs do not carry", async () => {
    tasks = [];
    unlistedTasks = [
      makeTask({
        id: 812,
        title: "Quarantine deworm — Fenbendazole",
        category: "DEWORMING",
        animal_id: 3,
      }),
    ];
    const { dialog } = await openDeepLink("task_id=812");

    expect(await within(dialog).findByLabelText(/Linked duty/)).toHaveTextContent(
      "Quarantine deworm",
    );
    expect(within(dialog).queryByText(/Could not link duty/)).not.toBeInTheDocument();
    await userEvent.setup().click(within(dialog).getByRole("button", { name: "Save event" }));
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      task_id: 812,
      type: "DEWORMING",
      animal_id: 3,
      product_name: "Fenbendazole",
    });
  });

  it("lists a deep-linked duty the tabs already carry exactly once", async () => {
    tasks = [VACCINE_TASK];
    const { dialog } = await openDeepLink("task_id=5");
    const dutySelect = await within(dialog).findByLabelText(/Linked duty/);
    await waitFor(() => expect(dutySelect).toHaveTextContent("PPR vaccination"));

    await userEvent.setup().click(dutySelect);

    expect(await screen.findAllByRole("option", { name: /PPR vaccination/ })).toHaveLength(1);
  });

  // ---------- linked-duty prefill ----------

  it("prefills from the duty that was picked, not the first eligible one", async () => {
    tasks = [VACCINE_TASK, DEWORM_BATCH_TASK];
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByLabelText(/Linked duty/), /Deworm batch #2/);

    expect(within(dialog).getByRole("radio", { name: "Purchase batch" })).toBeChecked();
    expect(within(dialog).getByLabelText("Type")).toHaveTextContent("DEWORMING");
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));

    await waitFor(() =>
      expect(previewBodies).toEqual([{ scope: "batch", purchase_batch_id: 2, task_id: 6 }]),
    );
  });

  it("leaves the target alone for a duty scoped to neither an animal nor a batch", async () => {
    tasks = [
      makeTask({ id: 12, title: "Herd-wide deworm — Albendazole", category: "DEWORMING" }),
    ];
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByLabelText(/Linked duty/), /Herd-wide deworm/);

    // The duty still drives type and product…
    expect(within(dialog).getByLabelText("Type")).toHaveTextContent("DEWORMING");
    expect(within(dialog).getByLabelText(/product name/i)).toHaveValue("Albendazole");
    // …but it names no target, so the operator still picks one.
    expect(within(dialog).getByRole("radio", { name: "Single animal" })).toBeChecked();
    expect(within(dialog).queryByText("Purchase batch *")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("combobox", { name: "Animal *" })).toHaveTextContent(
      "Pick an animal",
    );
  });

  it("never overwrites product or disease text the operator already typed", async () => {
    tasks = [DEWORM_ANIMAL_TASK];
    const { user, dialog } = await openDialog();
    fireEvent.change(within(dialog).getByLabelText(/product name/i), {
      target: { value: "Ivermectin 1%" },
    });
    fireEvent.change(within(dialog).getByLabelText(/disease target/i), {
      target: { value: "Liver fluke" },
    });
    await pickOption(user, within(dialog).getByLabelText(/Linked duty/), /Deworm Kaveri/);

    expect(within(dialog).getByLabelText(/product name/i)).toHaveValue("Ivermectin 1%");
    expect(within(dialog).getByLabelText(/disease target/i)).toHaveValue("Liver fluke");
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      task_id: 8,
      product_name: "Ivermectin 1%",
      disease_target: "Liver fluke",
    });
  });

  it("replaces the previous duty's guesses when the linked duty is switched", async () => {
    tasks = [DEWORM_ANIMAL_TASK, VACCINE_TASK];
    const { user, dialog } = await openDialog();
    const dutySelect = within(dialog).getByLabelText(/Linked duty/);
    await pickOption(user, dutySelect, /Deworm Kaveri/);
    expect(within(dialog).getByLabelText(/product name/i)).toHaveValue("Albendazole");
    expect(within(dialog).getByLabelText(/disease target/i)).toHaveValue("Deworming");

    await pickOption(user, dutySelect, /PPR vaccination/);

    // The deworming drug and target belonged to duty #8 alone.
    expect(within(dialog).getByLabelText(/product name/i)).toHaveValue("");
    expect(within(dialog).getByLabelText(/disease target/i)).toHaveValue("PPR vaccination");
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      task_id: 5,
      type: "VACCINE",
      product_name: null,
      disease_target: "PPR vaccination",
    });
  });

  it("unlinking a duty keeps the animal and every value the operator edited", async () => {
    tasks = [DEWORM_ANIMAL_TASK];
    const { user, dialog } = await openDialog();
    const dutySelect = within(dialog).getByLabelText(/Linked duty/);
    await pickOption(user, dutySelect, /Deworm Kaveri/);
    await waitFor(() =>
      expect(within(dialog).getByRole("combobox", { name: "Animal *" })).toHaveTextContent(
        "G-003 · Kaveri",
      ),
    );
    fireEvent.change(within(dialog).getByLabelText(/product name/i), {
      target: { value: "Ivermectin 1%" },
    });
    fireEvent.change(within(dialog).getByLabelText(/disease target/i), {
      target: { value: "Liver fluke" },
    });
    await pickOption(user, within(dialog).getByLabelText("Type"), "TREATMENT");

    await pickOption(user, dutySelect, /none/);

    expect(dutySelect).toHaveTextContent("— none —");
    expect(within(dialog).getByRole("radio", { name: "Single animal" })).toBeChecked();
    expect(within(dialog).getByRole("combobox", { name: "Animal *" })).toHaveTextContent(
      "G-003 · Kaveri",
    );
    expect(within(dialog).getByLabelText("Type")).toHaveTextContent("TREATMENT");
    expect(within(dialog).getByLabelText(/product name/i)).toHaveValue("Ivermectin 1%");
    expect(within(dialog).getByLabelText(/disease target/i)).toHaveValue("Liver fluke");
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      animal_id: 3,
      type: "TREATMENT",
      product_name: "Ivermectin 1%",
      disease_target: "Liver fluke",
      task_id: null,
    });
  });

  // A duty id beyond the safe-integer range has already lost digits by the
  // time JSON.parse hands it over, so completing "it" would close some other
  // duty. Refuse the save instead of posting the rounded id.
  it("refuses to save against a duty id that cannot be canonicalised", async () => {
    tasks = [
      makeTask({
        id: 9_007_199_254_740_992,
        title: "Duty with an unsafe id",
        category: "VACCINE",
        animal_id: 3,
      }),
    ];
    const { user, dialog } = await openDialog();
    const dutySelect = within(dialog).getByLabelText(/Linked duty/);
    await pickOption(user, dutySelect, /Duty with an unsafe id/);
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    expect(postBody).toBeNull();

    await pickOption(user, dutySelect, /none/);
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ animal_id: 3, task_id: null });
    expect(recordCalls).toBe(1);
  });

  // ---------- schedule/template field ----------

  it("offers seeded programmes for deworming and free text for a treatment", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Animal *" }),
      /G-003 · Kaveri/,
    );
    await user.click(within(dialog).getByText("Advanced traceability & compliance"));

    await pickOption(user, within(dialog).getByLabelText("Type"), "DEWORMING");
    await pickOption(
      user,
      within(dialog).getByLabelText("Schedule/template name"),
      /^Deworming/,
    );
    expect(within(dialog).getByLabelText("Schedule/template name")).toHaveTextContent(
      "Deworming",
    );

    // TREATMENT is not template-validated by the API, so the same field
    // becomes free text rather than an empty, unusable programme list.
    await pickOption(user, within(dialog).getByLabelText("Type"), "TREATMENT");
    fireEvent.change(within(dialog).getByLabelText("Schedule/template name"), {
      target: { value: "Vet prescription 2026-08" },
    });
    expect(within(dialog).getByLabelText("Schedule/template name")).toHaveValue(
      "Vet prescription 2026-08",
    );
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      type: "TREATMENT",
      schedule_template_name: "Vet prescription 2026-08",
    });
  });

  // ---------- dismissal navigation ----------

  it("does not navigate when a dialog opened from the page itself is dismissed", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: "Close" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(pushMock).not.toHaveBeenCalled();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("drops the deep-link params when a dialog without a returnTo is dismissed", async () => {
    const { dialog } = await openDeepLink("animal_id=3");
    await userEvent.setup().click(within(dialog).getByRole("button", { name: "Close" }));

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/health"));
    expect(pushMock).not.toHaveBeenCalled();
  });

  // ---------- re-hydrating a query-only navigation ----------

  it("re-hydrates a deep link that changes only its animal", async () => {
    const { view, dialog } = await openDeepLink("animal_id=3");
    await waitFor(() =>
      expect(within(dialog).getByRole("combobox", { name: "Animal *" })).toHaveTextContent(
        "G-003 · Kaveri",
      ),
    );

    window.history.replaceState({}, "", "/health?animal_id=4");
    view.rerender(<HealthPage />);

    await waitFor(() =>
      expect(
        within(screen.getByRole("dialog")).getByRole("combobox", { name: "Animal *" }),
      ).toHaveTextContent("G-004"),
    );
  });

  it("re-hydrates a deep link that changes only its purchase batch", async () => {
    const { view, dialog } = await openDeepLink("purchase_batch_id=2");
    await waitFor(() =>
      expect(
        within(dialog).getByRole("combobox", { name: "Purchase batch *" }),
      ).toHaveTextContent("Batch #2"),
    );

    window.history.replaceState({}, "", "/health?purchase_batch_id=3");
    view.rerender(<HealthPage />);

    await waitFor(() =>
      expect(
        within(screen.getByRole("dialog")).getByRole("combobox", { name: "Purchase batch *" }),
      ).toHaveTextContent("Batch #3"),
    );
  });
});
