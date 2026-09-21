/**
 * HealthPage — fresh-domain mutation campaign kills (2026-09): direct
 * schema-level kills for every superRefine gate (the dialog's native date
 * inputs sanitize malformed values, so several branches are unreachable
 * through the UI), plus the event-log offset re-homing behaviour.
 */

import { waitFor, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { MAX_WITHDRAWAL_DAYS } from "@/lib/backend-caps";
import { addDays, farmToday } from "@/lib/format";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import { eventSchema, default as HealthPage } from "./page";
import { settle } from "@/test/settle";

const toastMocks = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMocks }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/health",
  useSearchParams: () => new URLSearchParams(window.location.search),
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

const TODAY = farmToday();
const FAR_FUTURE = "2999-01-01";

/** Collect superRefine issues as "path: message" pairs. */
function issuesOf(input: Record<string, unknown>): Array<[string, unknown]> {
  const merged = {
    scope: "animal",
    animal_id: "5",
    type: "TREATMENT",
    suspected_scheduled_disease: false,
    date: TODAY,
    ...input,
  };
  const result = eventSchema.safeParse(merged);
  if (result.success) return [];
  return result.error.issues
    .filter((i) => i.code === "custom")
    .map((i) => [i.path.join("."), i.message]);
}

describe("eventSchema — campaign kills", () => {
  it("validates the cost grammar", () => {
    expect(issuesOf({ cost: "abc" })).toContainEqual(["cost", "Cost must be a number ≥ 0"]);
    expect(issuesOf({ cost: "-1" })).toContainEqual(["cost", "Cost must be a number ≥ 0"]);
    expect(issuesOf({ cost: "0.001" })).toContainEqual([
      "cost",
      "Amount must be ₹0 or at least ₹0.005",
    ]);
    expect(issuesOf({ cost: "1000000001" })).toContainEqual([
      "cost",
      "Cost cannot exceed ₹1,000,000,000",
    ]);
    expect(issuesOf({ cost: "" })).toEqual([]);
    expect(issuesOf({ cost: "120.5" })).toEqual([]);
  });

  it("requires a positive target id per scope", () => {
    expect(issuesOf({ animal_id: "abc" })).toContainEqual(["animal_id", "Pick an animal"]);
    expect(issuesOf({ animal_id: "0" })).toContainEqual(["animal_id", "Pick an animal"]);
    expect(issuesOf({ scope: "bucket", bucket: "" })).toContainEqual([
      "bucket",
      "Pick a bucket",
    ]);
    expect(issuesOf({ scope: "batch", purchase_batch_id: "x" })).toContainEqual([
      "purchase_batch_id",
      "Pick a batch",
    ]);
  });

  it("validates a linked duty id", () => {
    expect(issuesOf({ task_id: "abc" })).toContainEqual(["task_id", "Pick a valid duty"]);
    expect(issuesOf({ task_id: "8" })).toEqual([]);
  });

  it("rejects future event dates", () => {
    expect(issuesOf({ date: FAR_FUTURE })).toContainEqual([
      "date",
      "Date cannot be in the future",
    ]);
  });

  it("gates every next-due-date dependency", () => {
    // A well-ordered next-due date alone still demands its two dependencies.
    expect(issuesOf({ next_due_date: addDays(TODAY, 30) })).toContainEqual([
      "schedule_template_name",
      "Name the schedule used for a next-due date",
    ]);
    // A next-due equal to the event date is not "after".
    expect(issuesOf({ next_due_date: TODAY })).toContainEqual([
      "next_due_date",
      "Next due date must be after the event date",
    ]);
    expect(issuesOf({ next_due_date: addDays(TODAY, 5) })).toContainEqual([
      "schedule_template_name",
      "Name the schedule used for a next-due date",
    ]);
    expect(
      issuesOf({
        next_due_date: addDays(TODAY, 5),
        schedule_template_name: "FMD",
      }),
    ).toContainEqual([
      "next_due_authority",
      "Record the authority for this next-due date",
    ]);
    expect(
      issuesOf({
        next_due_date: addDays(TODAY, 5),
        schedule_template_name: "FMD",
        next_due_authority: "Local vet",
      }),
    ).toEqual([]);
  });

  it("gates product dating", () => {
    expect(issuesOf({ product_manufactured_on: FAR_FUTURE })).toContainEqual([
      "product_manufactured_on",
      "Manufacture date cannot be in the future",
    ]);
    expect(issuesOf({ product_manufactured_on: FAR_FUTURE })).toContainEqual([
      "product_manufactured_on",
      "Manufacture date cannot be after the event date",
    ]);
    expect(
      issuesOf({
        product_manufactured_on: addDays(TODAY, -10),
        product_expires_on: addDays(TODAY, -12),
      }),
    ).toContainEqual([
      "product_expires_on",
      "Expiry cannot be before manufacture date",
    ]);
    expect(issuesOf({ product_expires_on: addDays(TODAY, -1) })).toContainEqual([
      "product_expires_on",
      "Product was expired on the event date",
    ]);
  });

  it("gates vaccine validity against the event and the product", () => {
    expect(issuesOf({ vaccine_valid_until: addDays(TODAY, -1) })).toContainEqual([
      "vaccine_valid_until",
      "Vaccine validity cannot be before the event date",
    ]);
    expect(
      issuesOf({
        product_expires_on: addDays(TODAY, 5),
        vaccine_valid_until: addDays(TODAY, 10),
      }),
    ).toContainEqual([
      "vaccine_valid_until",
      "Vaccine validity cannot extend beyond product expiry",
    ]);
  });

  it("gates the withdrawal window on both ends", () => {
    expect(issuesOf({ withdrawal_until: addDays(TODAY, -1) })).toContainEqual([
      "withdrawal_until",
      "Withdrawal date cannot be before the event date",
    ]);
    expect(
      issuesOf({ withdrawal_until: addDays(TODAY, MAX_WITHDRAWAL_DAYS + 1) }),
    ).toContainEqual([
      "withdrawal_until",
      `Withdrawal date cannot be more than ${MAX_WITHDRAWAL_DAYS} days after the event`,
    ]);
    expect(issuesOf({ withdrawal_until: addDays(TODAY, 7) })).toEqual([]);
  });

  it("demands a suspected disease target", () => {
    expect(
      issuesOf({ suspected_scheduled_disease: true }),
    ).toContainEqual(["disease_target", "Name the suspected scheduled disease"]);
    expect(
      issuesOf({ suspected_scheduled_disease: true, disease_target: " FMD " }),
    ).toEqual([]);
  });

  it("rejects future authority/isolation timestamps", () => {
    expect(issuesOf({ authority_notified_at: FAR_FUTURE })).toContainEqual([
      "authority_notified_at",
      "Date cannot be in the future",
    ]);
    expect(issuesOf({ isolation_started_at: FAR_FUTURE })).toContainEqual([
      "isolation_started_at",
      "Date cannot be in the future",
    ]);
  });
});

describe("HealthPage event log offset — campaign kills", () => {
  let eventOffsets: number[];

  beforeEach(() => {
    window.history.replaceState({}, "", "/health");
    eventOffsets = [];
    server.use(
      http.get("/api/health/events", ({ request }) => {
        eventOffsets.push(Number(new URL(request.url).searchParams.get("offset") ?? 0));
        return HttpResponse.json({
          events: [],
          total: 120,
          limit: 50,
          offset: 0,
        });
      }),
    );
  });

  it("never leaves the first page for an on-range list", async () => {
    renderWithProviders(<HealthPage />);
    await waitFor(() => expect(eventOffsets.length).toBeGreaterThan(0));
    await settle(100);
    // A default (offset 0) view of a long list must not re-home itself.
    expect(eventOffsets.every((offset) => offset === 0)).toBe(true);
  });

  it("re-homes a stale deep-linked offset to the real last page", async () => {
    window.history.replaceState({}, "", "/health?offset=200");
    renderWithProviders(<HealthPage />);
    await waitFor(() =>
      expect(eventOffsets).toContain(200),
    );
    await waitFor(() => expect(eventOffsets).toContain(100));
    // The re-homed offset is the last real page for 120 rows at limit 50.
    expect(eventOffsets[eventOffsets.length - 1]).toBe(100);
  });

  it("re-homes an out-of-range offset on an empty log to zero", async () => {
    server.use(
      http.get("/api/health/events", ({ request }) => {
        eventOffsets.push(Number(new URL(request.url).searchParams.get("offset") ?? 0));
        return HttpResponse.json({ events: [], total: 0, limit: 50, offset: 0 });
      }),
    );
    window.history.replaceState({}, "", "/health?offset=200");
    renderWithProviders(<HealthPage />);
    await waitFor(() => expect(eventOffsets).toContain(0));
  });
});

/** Shared dialog harness for the campaign kills (mirrors the
 *  validation-mapping fixtures). */
function makeTask(overrides: Record<string, unknown>) {
  return {
    id: 1,
    title: "Deworm the new batch",
    title_key: null,
    title_args: {},
    due_date: TODAY,
    status: "PENDING",
    category: "DEWORMING",
    auto_generated: false,
    animal_id: null,
    purchase_batch_id: null,
    ...overrides,
  };
}

const DEWORM_BATCH_TASK = makeTask({ id: 6, purchase_batch_id: 2 });

function tasksPayloadOf(tasks: unknown[]) {
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

describe("HealthPage dialog — campaign kills", () => {
  let postBodies: Array<{ detail?: unknown }>;

  beforeEach(() => {
    window.history.replaceState({}, "", "/health");
    toastMocks.success.mockClear();
    toastMocks.error.mockClear();
    postBodies = [];
    server.use(
      http.get("/api/health/events", () =>
        HttpResponse.json({
          events: [
            {
              id: 1,
              animal_id: 3,
              purchase_batch_id: null,
              date: TODAY,
              type: "VACCINE",
              product_name: "PPR vaccine",
              disease_target: "PPR",
              next_due_date: addDays(TODAY, 30),
              schedule_template_name: "PPR",
              next_due_authority: null,
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.post("/api/health/events", async ({ request }) => {
        postBodies.push((await request.json()) as { detail?: unknown });
        return HttpResponse.json([{ id: 99 }], { status: 201 });
      }),
      http.post("/api/health/events/preview", async ({ request }) => {
        const target = (await request.json()) as Record<string, unknown>;
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
          animals: [
            {
              id: 3,
              tag_number: "G-003",
              name: "Kaveri",
              current_bucket: "LACTATING",
              movement_restricted: false,
              restriction_version: 0,
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get("/api/health/purchase-batches", () =>
        HttpResponse.json({
          batches: [
            { id: 2, active_quarantine_animal_count: 2 },
            { id: 3, active_quarantine_animal_count: 1 },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get("/api/tasks", () =>
        HttpResponse.json(
          tasksPayloadOf([DEWORM_BATCH_TASK, makeTask({ id: 7, title: "Hoof check", purchase_batch_id: null })]),
        ),
      ),
    );
  });

  async function openDialog() {
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    await screen.findByText("Event log");
    await user.click(screen.getByRole("button", { name: "Add event" }));
    return { user, dialog: await screen.findByRole("dialog") };
  }

  async function pickOption(
    user: ReturnType<typeof userEvent.setup>,
    trigger: HTMLElement,
    name: string | RegExp,
  ) {
    await user.click(trigger);
    await user.click(await screen.findByRole("option", { name }));
  }

  it("renders the event-log schedule line without an authority tail", async () => {
    renderWithProviders(<HealthPage />);
    // next_due_authority is null: the schedule span must be exactly the
    // template name, with no placeholder tail of any kind.
    const templateSpans = await screen.findAllByText("PPR", { selector: "span.block" });
    expect(templateSpans.length).toBeGreaterThanOrEqual(1);
    for (const span of templateSpans) {
      expect(span.textContent).toBe("PPR");
    }
    expect(screen.queryByText(/Stryker was here/)).not.toBeInTheDocument();
  });

  it("keeps the Advanced compliance section collapsed on a fresh dialog", async () => {
    const { dialog } = await openDialog();
    const details = within(dialog)
      .getByText("Advanced traceability & compliance")
      .closest("details");
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute("open");
  });

  it("toasts the singular message for an animal-scoped record", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Animal *" }), /G-003/);
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    await waitFor(() => expect(toastMocks.success).toHaveBeenCalledTimes(1));
    expect(toastMocks.success).toHaveBeenCalledWith("Health event recorded.");
  });

  it("joins multiple unmapped 422 issues into one banner sentence", async () => {
    const { user, dialog } = await openDialog();
    server.use(
      http.post("/api/health/events", () =>
        HttpResponse.json(
          {
            detail: [
              // One mapped issue routes the rest through the unmapped-joiner
              // branch (mappedFields.length === 0 takes the raw-detail path).
              { loc: ["body", "dose"], msg: "Dose is required." },
              { loc: ["body", "drifted_a"], msg: "First problem." },
              { loc: ["body", "drifted_b"], msg: "Second problem." },
            ],
          },
          { status: 422 },
        ),
      ),
    );
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Animal *" }), /G-003/);
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    expect(
      await within(dialog).findByText("First problem.; Second problem.", { exact: false }),
    ).toBeInTheDocument();
    // Inline only: the banner inside the open dialog is the single failure
    // surface (P3, 2026-09-20 audit — no duplicate toast).
    expect(toastMocks.error).not.toHaveBeenCalled();
  });

  it("opens the Advanced section when a 422 lands on a compliance field", async () => {
    const { user, dialog } = await openDialog();
    server.use(
      http.post("/api/health/events", () =>
        HttpResponse.json(
          {
            detail: [
              { loc: ["body", "dose"], msg: "Dose is required." },
              { loc: ["body", "certificate_number"], msg: "Certificate is malformed." },
            ],
          },
          { status: 422 },
        ),
      ),
    );
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Animal *" }), /G-003/);
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    expect(await within(dialog).findByText("Dose is required.")).toBeInTheDocument();
    const details = within(dialog)
      .getByText("Advanced traceability & compliance")
      .closest("details");
    expect(details).not.toBeNull();
    await waitFor(() => expect(details).toHaveAttribute("open"));
  });

  it("retries the event log from the stale-data notice", async () => {
    let eventsCalls = 0;
    server.use(
      http.get("/api/health/events", () => {
        eventsCalls += 1;
        if (eventsCalls === 1) {
          return HttpResponse.json({ events: [], total: 0, limit: 50, offset: 0 });
        }
        return HttpResponse.json({ detail: "boom" }, { status: 500 });
      }),
    );
    const { user, dialog } = await openDialog();
    // A successful record invalidates the log; that background refetch fails
    // while the old data stays on screen — the stale-data notice path.
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Animal *" }), /G-003/);
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    await waitFor(() => expect(eventsCalls).toBe(2));

    const notice = await screen.findByRole("status");
    const retry = within(notice).getByRole("button", { name: "Retry" });
    await user.click(retry);
    await waitFor(() => expect(eventsCalls).toBe(3));
  });

  it("clears the bulk preview whenever the linked duty changes", async () => {
    const { user, dialog } = await openDialog();
    // Scope to a purchase batch and review its targets.
    await user.click(within(dialog).getByRole("radio", { name: /batch/i }));
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Purchase batch *" }),
      /2/,
    );
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    expect(
      await within(dialog).findByRole("button", { name: /Confirm for 2\s+animals/ }),
    ).toBeInTheDocument();

    // Attaching a duty must drop that stale preview back to review state.
    // The herd-level duty's only sanctioned scope is the whole bucket
    // (P1-8), so attaching it re-scopes the form there and drops the batch
    // selection with the preview.
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: /Linked duty/ }),
      /Hoof/,
    );
    expect(
      await within(dialog).findByRole("button", { name: "Review target animals" }),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("radio", { name: /whole bucket/i })).toBeChecked();
    expect(within(dialog).getByRole("radio", { name: /batch/i })).toBeDisabled();

    // Unlink the duty: same preview reset, and the form returns to its
    // default single-animal scope.
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: /Linked duty/ }),
      /none/,
    );
    expect(
      await within(dialog).findByRole("button", { name: "Save event" }),
    ).toBeInTheDocument();

    // Retargeting a bulk scope through its picker must clear the preview via
    // the same helper — this path has no other clear (no duty attached, no
    // scope change), so a stale preview would keep announcing "Confirm".
    await user.click(within(dialog).getByRole("radio", { name: /batch/i }));
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Purchase batch *" }),
      /2/,
    );
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    expect(
      await within(dialog).findByRole("button", { name: /Confirm for 2\s+animals/ }),
    ).toBeInTheDocument();
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Purchase batch *" }),
      /Batch #3/,
    );
    expect(
      await within(dialog).findByRole("button", { name: "Review target animals" }),
    ).toBeInTheDocument();

    // Review once more, then switch the scope radio itself: the scope change
    // is the only clear on that path and must drop the preview too.
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    expect(
      await within(dialog).findByRole("button", { name: /Confirm for 2\s+animals/ }),
    ).toBeInTheDocument();
    await user.click(within(dialog).getByRole("radio", { name: /bucket/i }));
    expect(
      await within(dialog).findByRole("button", { name: "Review target animals" }),
    ).toBeInTheDocument();
  });

  it("drops the bulk preview when the record write fails", async () => {
    server.use(
      http.post("/api/health/events", () =>
        HttpResponse.json(
          { detail: [{ loc: ["body", "dose"], msg: "Dose is required." }] },
          { status: 422 },
        ),
      ),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: /bucket/i }));
    await pickOption(user, within(dialog).getByRole("combobox", { name: /Bucket/ }), /Lactating|Breeding/);
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    expect(
      await within(dialog).findByRole("button", { name: /Confirm for 2\s+animals/ }),
    ).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: /Confirm for 2\s+animals/ }));
    // The failed write drops the preview: the next attempt must re-review,
    // not confirm against a stale target set.
    expect(
      await within(dialog).findByRole("button", { name: "Review target animals" }),
    ).toBeInTheDocument();
  });
});
