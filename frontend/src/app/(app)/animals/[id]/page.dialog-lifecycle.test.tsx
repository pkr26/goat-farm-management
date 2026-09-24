/**
 * Animal profile page — behaviour the main suite leaves unpinned: each
 * lifecycle dialog emptying its form after a completed write (plus the bucket
 * select's humanised trigger label), the statutory suspected-disease field
 * rejecting whitespace, the clearance dialog's blank-reference guard, error
 * clearing, stale-episode lock, post-clearance refresh and dismissal paths,
 * the restriction audit's no-content and retry-after-failure paths, and the
 * module-aware back label for return destinations that carry page state.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import AnimalProfilePage from "./page";
import { settle } from "@/test/settle";

const nav = vi.hoisted(() => ({ id: "1", search: "" }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/animals/1",
  useSearchParams: () => new URLSearchParams(nav.search),
  useParams: () => ({ id: nav.id }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

beforeAll(() => {
  // jsdom lacks the pointer-capture / observer APIs Base UI Select relies on.
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  Element.prototype.scrollIntoView = () => {};
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (window as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO;
});

type User = ReturnType<typeof userEvent.setup>;

async function pickOption(user: User, trigger: HTMLElement, name: string) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

function setInput(input: HTMLElement, value: string) {
  fireEvent.change(input, { target: { value } });
}

const ANIMAL = {
  id: 1,
  tag_number: "G-001",
  name: "Lakshmi",
  breed: "Osmanabadi",
  sex: "F",
  date_of_birth: "2025-05-10",
  estimated_dob: null,
  birth_type: "TWIN",
  source: "BORN",
  dam_id: 7,
  sire_id: 8,
  birth_weight: 2.4,
  current_bucket: "PREGNANCY_EARLY",
  status: "ACTIVE",
  status_date: null,
  sale_price: null,
  sale_weight_kg: null,
  sale_price_per_kg: null,
  purchase_date: null,
  purchase_price: null,
  seller_name: null,
  cull_candidate: false,
  movement_restricted: false,
  restriction_reason: null,
  suspected_scheduled_disease: false,
  suspected_disease: null,
  authority_notified_at: null,
  restriction_cleared_at: null,
  restriction_cleared_by_id: null,
  restriction_clearance_reference: null,
  restriction_version: 0,
  mortality_cause: null,
  mortality_cause_code: null,
  disposal_method: null,
  necropsy_done: false,
  necropsy_findings: null,
  mortality_reported_at: null,
  notes: "Calm doe, good milker.",
  created_at: "2026-01-01T05:30:00Z",
  age_months: 14,
  latest_weight_kg: 32.5,
  days_in_current_bucket: 12,
};

const PROFILE = {
  animal: ANIMAL,
  kids: [],
  kids_total: 0,
  kids_offset: 0,
  weights: [],
  weights_total: 0,
  weights_offset: 0,
  moves: [],
  moves_total: 0,
  moves_offset: 0,
  health_events: [],
  health_events_total: 0,
  health_events_offset: 0,
  breedings: [],
  breedings_total: 0,
  breedings_offset: 0,
  history_limit: 25,
};

function profileWith(animal: Record<string, unknown>) {
  return { ...PROFILE, animal: { ...ANIMAL, ...animal } };
}

/** A live health hold, so the clearance dialog is offered. */
function restrictedProfile(restrictionVersion: number) {
  return profileWith({
    movement_restricted: true,
    restriction_reason: "Scheduled-disease suspicion",
    suspected_disease: "PPR",
    restriction_version: restrictionVersion,
  });
}

describe("AnimalProfilePage behaviour", () => {
  let getCalls: number;
  let weightBodies: Record<string, unknown>[];
  let moveBodies: Record<string, unknown>[];
  let statusBodies: Record<string, unknown>[];
  let clearanceBodies: Record<string, unknown>[];

  function useProfileHandler(profile: Record<string, unknown> = PROFILE) {
    server.use(
      http.get("/api/animals/1", () => {
        getCalls += 1;
        return HttpResponse.json(profile);
      }),
    );
  }

  beforeEach(() => {
    nav.id = "1";
    nav.search = "";
    getCalls = 0;
    weightBodies = [];
    moveBodies = [];
    statusBodies = [];
    clearanceBodies = [];
    vi.clearAllMocks();
    useProfileHandler();
    server.use(
      http.post("/api/animals/1/weight", async ({ request }) => {
        weightBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json(
          { id: 99, date: "2026-08-06", weight_kg: 30, bcs: null, notes: null },
          { status: 201 },
        );
      }),
      http.post("/api/animals/1/move", async ({ request }) => {
        moveBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({}, { status: 201 });
      }),
      http.post("/api/animals/1/status", async ({ request }) => {
        statusBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({}, { status: 201 });
      }),
      http.post("/api/health/restrictions/1/clear", async ({ request }) => {
        clearanceBodies.push((await request.json()) as Record<string, unknown>);
        return new HttpResponse(null, { status: 204 });
      }),
      http.get("/api/health/restrictions/1", ({ request }) => {
        const params = new URL(request.url).searchParams;
        return HttpResponse.json({
          animal_id: 1,
          restriction_version: 0,
          active: false,
          actions: [],
          total: 0,
          limit: Number(params.get("limit") ?? 25),
          offset: Number(params.get("offset") ?? 0),
        });
      }),
    );
  });

  async function renderProfile() {
    const view = renderWithProviders(<AnimalProfilePage />);
    await screen.findByRole("heading", { level: 1, name: /G-001/ });
    return view;
  }

  async function openDialog(user: User, button: string) {
    const trigger = screen.getByRole("button", { name: button });
    await waitFor(() => expect(trigger).toBeEnabled());
    await user.click(trigger);
    return await screen.findByRole("dialog");
  }

  describe("dialogs after a completed write", () => {
    it("empties the weight form so the next entry starts blank", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record weight");
      setInput(within(dialog).getByLabelText(/date/i), "2026-08-05");
      setInput(within(dialog).getByLabelText(/weight \(kg\)/i), "30.5");
      setInput(within(dialog).getByLabelText(/bcs/i), "4");
      await user.type(within(dialog).getByLabelText(/notes/i), "After flush feeding");
      await user.click(within(dialog).getByRole("button", { name: "Save" }));

      await waitFor(() => expect(weightBodies).toHaveLength(1));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

      const reopened = await openDialog(user, "Record weight");
      expect(within(reopened).getByLabelText(/date/i)).toHaveValue("");
      expect(within(reopened).getByLabelText(/weight \(kg\)/i)).toHaveValue(null);
      expect(within(reopened).getByLabelText(/bcs/i)).toHaveValue(null);
      expect(within(reopened).getByLabelText(/notes/i)).toHaveValue("");
      // A stale weight must never be resubmitted by a bare second Save.
      await user.click(within(reopened).getByRole("button", { name: "Save" }));
      expect(
        await within(reopened).findByText("Weight must be greater than 0"),
      ).toBeInTheDocument();
      expect(weightBodies).toHaveLength(1);
    });

    it("returns the move form to its unselected default after a completed move", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Move bucket");
      await pickOption(user, within(dialog).getByRole("combobox"), "Pregnancy B");
      // The closed trigger shows the humanised label, not the raw enum value.
      expect(within(dialog).getByRole("combobox")).toHaveTextContent("Pregnancy B");
      await user.type(within(dialog).getByLabelText(/reason/i), "Scan confirms late gestation");
      await user.click(within(dialog).getByRole("button", { name: "Move" }));

      await waitFor(() =>
        expect(moveBodies).toEqual([
          { to_bucket: "PREGNANCY_LATE", reason: "Scan confirms late gestation" },
        ]),
      );
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

      const reopened = await openDialog(user, "Move bucket");
      expect(within(reopened).getByRole("combobox")).toHaveTextContent("Choose bucket…");
      expect(within(reopened).getByLabelText(/reason/i)).toHaveValue("");
      // Nothing is selected, so a bare second Move cannot repeat the last one.
      await user.click(within(reopened).getByRole("button", { name: "Move" }));
      await waitFor(() =>
        expect(within(reopened).getByRole("combobox")).toHaveAttribute("aria-invalid", "true"),
      );
      expect(moveBodies).toHaveLength(1);
    });

    it("restores the status form defaults after a recorded status change", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      await pickOption(user, within(dialog).getByLabelText(/new status/i), "DEAD");
      await user.type(within(dialog).getByLabelText("Mortality cause"), "Sudden fever");
      await user.click(
        within(dialog).getByRole("checkbox", {
          name: "Suspected scheduled/notifiable disease",
        }),
      );
      setInput(within(dialog).getByLabelText("Suspected disease *"), "PPR");
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

      await waitFor(() => expect(statusBodies).toHaveLength(1));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

      const reopened = await openDialog(user, "Change status");
      expect(within(reopened).getByLabelText(/new status/i)).toHaveTextContent("SOLD");
      expect(within(reopened).queryByLabelText("Mortality cause")).not.toBeInTheDocument();
      expect(
        within(reopened).queryByRole("checkbox", {
          name: "Suspected scheduled/notifiable disease",
        }),
      ).not.toBeInTheDocument();
      expect(within(reopened).getByLabelText(/sale price/i)).toHaveValue(null);
      expect(within(reopened).getByLabelText(/buyer name/i)).toHaveValue("");
    });

    it("rejects a whitespace-only suspected disease for a notifiable death", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      await pickOption(user, within(dialog).getByLabelText(/new status/i), "DEAD");
      await user.click(
        within(dialog).getByRole("checkbox", {
          name: "Suspected scheduled/notifiable disease",
        }),
      );
      const disease = within(dialog).getByLabelText("Suspected disease *");
      setInput(disease, "   ");
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

      expect(
        await within(dialog).findByText("Identify the suspected scheduled disease"),
      ).toBeInTheDocument();
      expect(disease).toHaveAccessibleDescription("Identify the suspected scheduled disease");
      expect(statusBodies).toHaveLength(0);

      setInput(disease, "PPR");
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
      await waitFor(() => expect(statusBodies).toHaveLength(1));
      expect(statusBodies[0]).toMatchObject({
        new_status: "DEAD",
        suspected_scheduled_disease: true,
        suspected_disease: "PPR",
      });
    });
  });

  describe("movement restriction clearance", () => {
    it("keeps a whitespace-only clearance reference out of the audit trail", async () => {
      useProfileHandler(restrictedProfile(1));
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record clearance");
      const reference = within(dialog).getByLabelText("Clearance reference *");
      const confirm = within(dialog).getByRole("button", { name: "Confirm clearance" });

      // Spaces are not a clearance reference: the audit trail would record an
      // empty release issued by nobody.
      setInput(reference, "   ");
      expect(confirm).toBeDisabled();
      await user.click(confirm);
      await settle(75);
      expect(clearanceBodies).toHaveLength(0);
      expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();

      setInput(reference, "VET-CLEAR-31");
      expect(confirm).toBeEnabled();
      await user.click(confirm);
      await waitFor(() =>
        expect(clearanceBodies).toEqual([
          { clearance_reference: "VET-CLEAR-31", expected_restriction_version: 1 },
        ]),
      );
    });

    it("drops the previous failure as soon as the retry request starts", async () => {
      useProfileHandler(restrictedProfile(1));
      server.use(
        http.post("/api/health/restrictions/1/clear", () =>
          HttpResponse.json({ detail: "Clearance service is unavailable." }, { status: 503 }),
        ),
      );
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record clearance");
      const reference = within(dialog).getByLabelText("Clearance reference *");
      setInput(reference, "VET-CLEAR-2026-77");
      await user.click(within(dialog).getByRole("button", { name: "Confirm clearance" }));

      expect(await within(dialog).findByRole("alert")).toHaveTextContent(
        "Clearance service is unavailable.",
      );

      let releaseClearance!: () => void;
      const parked = new Promise<void>((resolve) => {
        releaseClearance = resolve;
      });
      server.use(
        http.post("/api/health/restrictions/1/clear", async ({ request }) => {
          clearanceBodies.push((await request.json()) as Record<string, unknown>);
          await parked;
          return new HttpResponse(null, { status: 204 });
        }),
      );
      await user.click(within(dialog).getByRole("button", { name: "Retry clearance" }));

      // The stale failure must not be left standing over an in-flight retry.
      await waitFor(() => expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument());
      expect(reference).not.toHaveAttribute("aria-invalid");
      expect(within(dialog).getByRole("button", { name: "Recording…" })).toBeDisabled();

      releaseClearance();
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(clearanceBodies).toEqual([
        { clearance_reference: "VET-CLEAR-2026-77", expected_restriction_version: 1 },
      ]);
    });

    it("locks the retry until a superseded episode has actually been refreshed", async () => {
      useProfileHandler(restrictedProfile(1));
      server.use(
        http.post("/api/health/restrictions/1/clear", async ({ request }) => {
          clearanceBodies.push((await request.json()) as Record<string, unknown>);
          return HttpResponse.json(
            { detail: "Movement restriction was superseded; refresh the current episode." },
            { status: 409 },
          );
        }),
      );
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record clearance");
      const reference = within(dialog).getByLabelText("Clearance reference *");
      setInput(reference, "VET-STALE-9");
      await user.click(within(dialog).getByRole("button", { name: "Confirm clearance" }));

      expect(await within(dialog).findByRole("alert")).toHaveTextContent(
        "Refreshing the current restriction episode before retrying.",
      );
      // The refreshed profile still carries episode 1, so the client knows the
      // version it would resubmit is the one the server just rejected.
      const waiting = await within(dialog).findByRole("button", {
        name: "Waiting for current episode…",
      });
      expect(waiting).toBeDisabled();
      expect(reference).toBeDisabled();
      const refresh = within(dialog).getByRole("button", { name: "Refresh episode" });

      useProfileHandler(restrictedProfile(2));
      await user.click(refresh);

      const retry = await within(dialog).findByRole("button", { name: "Retry clearance" });
      expect(retry).toBeEnabled();
      expect(reference).toBeEnabled();
      expect(
        within(dialog).queryByRole("button", { name: "Refresh episode" }),
      ).not.toBeInTheDocument();
      expect(clearanceBodies).toEqual([
        { clearance_reference: "VET-STALE-9", expected_restriction_version: 1 },
      ]);
    });

    it("refreshes the profile once a clearance is recorded", async () => {
      let restricted = true;
      server.use(
        http.get("/api/animals/1", () => {
          getCalls += 1;
          return HttpResponse.json(restricted ? restrictedProfile(1) : PROFILE);
        }),
        http.post("/api/health/restrictions/1/clear", async ({ request }) => {
          clearanceBodies.push((await request.json()) as Record<string, unknown>);
          restricted = false;
          return new HttpResponse(null, { status: 204 });
        }),
      );
      const user = userEvent.setup();
      await renderProfile();
      expect(screen.getByText("Movement restricted")).toBeInTheDocument();
      const loadsBeforeClearance = getCalls;

      const dialog = await openDialog(user, "Record clearance");
      setInput(within(dialog).getByLabelText("Clearance reference *"), "VET-CLEAR-2026-42");
      await user.click(within(dialog).getByRole("button", { name: "Confirm clearance" }));

      await waitFor(() => expect(clearanceBodies).toHaveLength(1));
      // Without the refetch the operator keeps looking at a hold the server
      // has already released — and stays locked out of bucket movement.
      await waitFor(() => expect(getCalls).toBeGreaterThan(loadsBeforeClearance));
      await waitFor(() =>
        expect(screen.queryByText("Movement restricted")).not.toBeInTheDocument(),
      );
      expect(screen.getByRole("button", { name: "Move bucket" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Record clearance" })).not.toBeInTheDocument();
    });

    it("dismisses the clearance dialog with Escape and with Cancel", async () => {
      useProfileHandler(restrictedProfile(1));
      const user = userEvent.setup();
      await renderProfile();

      const dialog = await openDialog(user, "Record clearance");
      setInput(within(dialog).getByLabelText("Clearance reference *"), "VET-ABANDONED");
      await user.keyboard("{Escape}");
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

      const reopened = await openDialog(user, "Record clearance");
      await user.click(within(reopened).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(clearanceBodies).toHaveLength(0);
    });
  });

  describe("movement restriction audit", () => {
    it("hides the audit card when the history endpoint answers with no content", async () => {
      server.use(
        http.get("/api/health/restrictions/1", () => new HttpResponse(null, { status: 204 })),
      );
      await renderProfile();

      await waitFor(() =>
        expect(screen.queryByText("Loading restriction audit…")).not.toBeInTheDocument(),
      );
      expect(screen.queryByText(/Movement restriction audit/)).not.toBeInTheDocument();
      // The profile itself must survive an envelope it cannot read.
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("G-001");
      expect(screen.getByText("Details")).toBeInTheDocument();
    });

    it("reloads the audit from its own retry after a failed load", async () => {
      let auditFails = true;
      server.use(
        http.get("/api/health/restrictions/1", ({ request }) => {
          if (auditFails) {
            return HttpResponse.json(
              { detail: "Restriction audit is temporarily unavailable." },
              { status: 503 },
            );
          }
          const params = new URL(request.url).searchParams;
          return HttpResponse.json({
            animal_id: 1,
            restriction_version: 2,
            active: false,
            actions: [
              {
                id: 5,
                restriction_version: 2,
                action: "CLEARED",
                acted_at: "2026-08-07T10:15:00Z",
                acted_by_id: 7,
                action_reference: "VET-CLEAR-7",
                disease_target: "PPR",
                health_event_id: 41,
              },
            ],
            total: 1,
            limit: Number(params.get("limit") ?? 25),
            offset: Number(params.get("offset") ?? 0),
          });
        }),
      );
      const user = userEvent.setup();
      await renderProfile();

      const failure = await screen.findByRole("alert");
      expect(failure).toHaveTextContent("Restriction audit is temporarily unavailable.");

      auditFails = false;
      await user.click(within(failure).getByRole("button", { name: "Retry audit" }));

      expect(await screen.findByText("VET-CLEAR-7")).toBeInTheDocument();
      expect(screen.getByText("Movement restriction audit (1)")).toBeInTheDocument();
      expect(screen.getByText("PPR")).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
  });

  describe("back link", () => {
    it("names the module of a return destination that carries page state", async () => {
      const cases = [
        { returnTo: "/dashboard?range=90d", label: "Back to dashboard" },
        { returnTo: "/tasks?tab=verification", label: "Back to tasks" },
        { returnTo: "/health/schedule?due=overdue", label: "Back to health" },
        { returnTo: "/animals?bucket=RESTING", label: "Back to animals" },
      ];
      for (const { returnTo, label } of cases) {
        nav.search = `?returnTo=${encodeURIComponent(returnTo)}`;
        const view = renderWithProviders(<AnimalProfilePage />);
        const link = await screen.findByRole("link", { name: label });
        expect(link).toHaveAttribute("href", returnTo);
        view.unmount();
      }
    });
  });
it("accepts and rejects the sale facts at their exact caps", async () => {
  const user = userEvent.setup();
  await renderProfile();
  const dialog = await openDialog(user, "Change status");

  // SOLD exposes the sale facts; every value at its cap is legal.
  await pickOption(user, within(dialog).getByLabelText(/new status/i), "SOLD");
  setInput(within(dialog).getByLabelText(/sale price/i), "1000000000");
  setInput(within(dialog).getByLabelText(/weight.*kg/i), "1000");
  setInput(within(dialog).getByLabelText(/price per kg/i), "1000000000");
  setInput(within(dialog).getByLabelText(/buyer/i), "b".repeat(120));
  setInput(within(dialog).getByLabelText(/notes/i), "n".repeat(255));
  await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(statusBodies).toHaveLength(1));
  expect(statusBodies[0]).toMatchObject({
    sale_price: 1_000_000_000,
    sale_weight_kg: 1000,
    sale_price_per_kg: 1_000_000_000,
  });
});

it("rejects each sale fact one unit past its cap, by name", async () => {
  const user = userEvent.setup();
  await renderProfile();
  const dialog = await openDialog(user, "Change status");
  await pickOption(user, within(dialog).getByLabelText(/new status/i), "SOLD");

  setInput(within(dialog).getByLabelText(/sale price/i), "1000000001");
  setInput(within(dialog).getByLabelText(/weight.*kg/i), "1001");
  setInput(within(dialog).getByLabelText(/price per kg/i), "1000000001");
  setInput(within(dialog).getByLabelText(/buyer/i), "b".repeat(121));
  setInput(within(dialog).getByLabelText(/notes/i), "n".repeat(256));
  await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
  await waitFor(() =>
    expect(within(dialog).getByText("Sale price cannot exceed ₹1,000,000,000")).toBeInTheDocument(),
  );
  expect(within(dialog).getByText("Weight must be at most 1000 kg")).toBeInTheDocument();
  expect(
    within(dialog).getByText("Price per kg cannot exceed ₹1,000,000,000"),
  ).toBeInTheDocument();
  expect(within(dialog).getByText("Buyer name cannot exceed 120 characters")).toBeInTheDocument();
  expect(within(dialog).getByText("Notes cannot exceed 255 characters")).toBeInTheDocument();
  expect(statusBodies).toHaveLength(0); // nothing left the dialog
});

it("caps mortality and necropsy text at their schema limits", async () => {
  const user = userEvent.setup();
  await renderProfile();
  const dialog = await openDialog(user, "Change status");
  await pickOption(user, within(dialog).getByLabelText(/new status/i), "DEAD");

  setInput(within(dialog).getByLabelText("Mortality cause"), "c".repeat(120));
  await user.click(within(dialog).getByRole("checkbox", { name: /necropsy/i }));
  setInput(within(dialog).getByLabelText(/necropsy findings/i), "f".repeat(4000));
  await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(statusBodies).toHaveLength(1));

  // One past each cap stays in the dialog with the named errors.
  const reopened = await openDialog(user, "Change status");
  await pickOption(user, within(reopened).getByLabelText(/new status/i), "DEAD");
  setInput(within(reopened).getByLabelText("Mortality cause"), "c".repeat(121));
  await user.click(within(reopened).getByRole("checkbox", { name: /necropsy/i }));
  setInput(within(reopened).getByLabelText(/necropsy findings/i), "f".repeat(4001));
  await user.click(within(reopened).getByRole("button", { name: "Confirm" }));
  expect(
    await within(reopened).findByText("Mortality cause cannot exceed 120 characters"),
  ).toBeInTheDocument();
  expect(
    within(reopened).getByText("Necropsy findings cannot exceed 4000 characters"),
  ).toBeInTheDocument();
  expect(statusBodies).toHaveLength(1);
});

it("weight-dialog notes cap at 255 characters", async () => {
  const user = userEvent.setup();
  await renderProfile();
  const dialog = await openDialog(user, "Record weight");
  const notes = within(dialog).getByLabelText(/notes/i) as HTMLTextAreaElement;
  await user.type(notes, "n".repeat(256));
  expect(notes.value.length).toBe(255);
});

it("move-dialog reason and clearance reference cap at 255 characters", async () => {
  const user = userEvent.setup();
  await renderProfile();
  const dialog = await openDialog(user, "Move bucket");
  const reason = within(dialog).getByLabelText(/reason/i) as HTMLTextAreaElement;
  await user.type(reason, "r".repeat(256));
  expect(reason.value.length).toBe(255);
});

});
