/**
 * Animal profile page — the presentation and guard branches the main suite
 * does not pin down: the header description, the detail-block conditionals
 * (mortality reporting, the cleared-restriction pair, the notes paragraph),
 * the movement-restriction banner, the restriction audit card's
 * loading/error/empty gating and its server-echoed page window, the clearance
 * dialog's dismissal guard and conflict handling, the status dialog's
 * invalid-field wiring plus the withdrawn scheduled-disease report, and the
 * teardown a 404/403/401 background refresh must force.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday } from "@/lib/format";

import AnimalProfilePage from "./page";

const nav = vi.hoisted(() => ({ id: "1", search: "" }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/animals/1",
  useSearchParams: () => new URLSearchParams(nav.search),
  useParams: () => ({ id: nav.id }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const toastMock = toast as unknown as {
  success: ReturnType<typeof vi.fn>;
  error: ReturnType<typeof vi.fn>;
};

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

/** One immutable restriction action row, shaped like the audit endpoint's. */
function auditAction(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    restriction_version: 1,
    action: "PLACED",
    acted_at: "2026-07-01T09:00:00Z",
    acted_by_id: 7,
    action_reference: "HEALTH-EVENT-41",
    disease_target: "PPR",
    health_event_id: 41,
    ...overrides,
  };
}

const RESTRICTED = {
  movement_restricted: true,
  restriction_reason: "Scheduled-disease suspicion",
  restriction_version: 1,
};

describe("AnimalProfilePage guards", () => {
  let getCalls: number;
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
    statusBodies = [];
    clearanceBodies = [];
    vi.clearAllMocks();
    useProfileHandler();
    server.use(
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
    await screen.findByRole("heading", { level: 1, name: /G-00/ });
    return view;
  }

  async function openDialog(user: User, button: string) {
    await user.click(screen.getByRole("button", { name: button }));
    return await screen.findByRole("dialog");
  }

  function detailsCard(): HTMLElement {
    return screen.getByText("Details").closest('[data-slot="card"]') as HTMLElement;
  }

  /** The <dd> paired with a detail's <dt> label inside the Details card. */
  function detailValue(label: string): string {
    const term = within(detailsCard()).getByText(label);
    return (term.parentElement?.querySelector("dd")?.textContent ?? "").trim();
  }

  describe("header description", () => {
    it("describes the doe by her own breed, sex and bucket", async () => {
      await renderProfile();

      expect(screen.getByText("Osmanabadi · Female · Pregnancy A")).toBeInTheDocument();
    });

    it("describes a buck as Male", async () => {
      useProfileHandler(profileWith({ sex: "M", current_bucket: "MALE_KIDS" }));
      await renderProfile();

      expect(screen.getByText("Osmanabadi · Male · Male kids")).toBeInTheDocument();
    });
  });

  describe("details card", () => {
    it("renders the notes as their own muted paragraph below the detail grid", async () => {
      await renderProfile();

      const notes = within(detailsCard()).getByText("Calm doe, good milker.");
      expect(notes.tagName).toBe("P");
      expect(notes).toHaveClass("text-muted-foreground");
    });

    it("shows the breeding empty state instead of an empty list", async () => {
      await renderProfile();

      expect(screen.getByText("Breeding history (0)")).toBeInTheDocument();
      expect(screen.getByText("No breeding records.")).toBeInTheDocument();
    });

    it("omits the mortality block for a status change that is not a death", async () => {
      useProfileHandler(
        profileWith({ status: "SOLD", status_date: "2026-07-30", sale_price: 9000 }),
      );
      await renderProfile();

      expect(screen.getByText("Status date")).toBeInTheDocument();
      expect(screen.queryByText("Mortality cause")).not.toBeInTheDocument();
      expect(screen.queryByText("Mortality reported")).not.toBeInTheDocument();
      expect(screen.queryByText("Scheduled disease suspected")).not.toBeInTheDocument();
    });

    it("shows the recorded sale weight next to the sale price", async () => {
      useProfileHandler(
        profileWith({
          status: "SOLD",
          status_date: "2026-07-30",
          sale_price: 9000,
          sale_weight_kg: 40,
        }),
      );
      await renderProfile();

      expect(detailValue("Sale price")).toBe("₹9,000");
      expect(detailValue("Sale weight")).toBe("40 kg");
    });

    it("renders the lifetime P&L card with its farm-level feed note", async () => {
      server.use(
        http.get("/api/finance/animals/1/lifetime-pnl", () =>
          HttpResponse.json({
            animal_id: 1,
            tag_number: "G-001",
            purchase_cost: 5000,
            health_cost: 300,
            insurance_premiums: 120,
            sale_income: 9000,
            net: 3580,
            note: "Feed costs are not attributed per animal (farm-level dispensing).",
          }),
        ),
      );
      await renderProfile();

      const card = screen.getByText("Lifetime P&L").closest('[data-slot="card"]') as HTMLElement;
      await waitFor(() => expect(within(card).getByText("Purchase cost")).toBeInTheDocument());
      expect(within(card).getByText("Insurance premiums")).toBeInTheDocument();
      expect(within(card).getByText("Net")).toBeInTheDocument();
      expect(within(card).getByText("₹3,580")).toBeInTheDocument();
      expect(
        within(card).getByText(/Feed costs are not attributed per animal/),
      ).toBeInTheDocument();
    });

    it("shows the coded cause, disposal and necropsy facts for a death", async () => {
      useProfileHandler(
        profileWith({
          status: "DEAD",
          status_date: "2026-07-30",
          mortality_cause: "enterotoxaemia",
          mortality_cause_code: "ENTEROTOXAEMIA",
          disposal_method: "buried",
          necropsy_done: true,
          necropsy_findings: "Gut haemorrhage consistent with ET",
          mortality_reported_at: "2026-07-31",
        }),
      );
      await renderProfile();

      expect(detailValue("Cause code")).toBe("Enterotoxaemia");
      expect(detailValue("Disposal method")).toBe("buried");
      expect(detailValue("Necropsy performed")).toBe("Yes");
      expect(detailValue("Necropsy findings")).toBe("Gut haemorrhage consistent with ET");
    });

    it("shows the buyer next to the sale economics and hides absent facts", async () => {
      useProfileHandler(
        profileWith({
          status: "SOLD",
          status_date: "2026-07-30",
          sale_price: 9000,
          sale_weight_kg: 40,
          buyer_name: "Kurla trader",
        }),
      );
      await renderProfile();

      expect(detailValue("Sale price")).toBe("₹9,000");
      expect(detailValue("Sale weight")).toBe("40 kg");
      expect(detailValue("Buyer")).toBe("Kurla trader");
      // Death-audit rows never render for a sale.
      expect(screen.queryByText("Disposal method")).not.toBeInTheDocument();
      expect(screen.queryByText("Necropsy performed")).not.toBeInTheDocument();
    });

    it("reports a suspected scheduled disease for a death", async () => {
      useProfileHandler(
        profileWith({
          status: "DEAD",
          status_date: "2026-07-30",
          mortality_cause: "Sudden fever",
          mortality_cause_code: null,
          disposal_method: null,
          necropsy_done: false,
          necropsy_findings: null,
          mortality_reported_at: "2026-07-31",
          suspected_scheduled_disease: true,
          suspected_disease: "PPR",
        }),
      );
      await renderProfile();

      expect(detailValue("Scheduled disease suspected")).toBe("Yes");
      expect(detailValue("Suspected disease")).toBe("PPR");
    });

    it("records the absence of a suspicion without inventing a disease row", async () => {
      useProfileHandler(
        profileWith({
          status: "DEAD",
          status_date: "2026-07-30",
          mortality_cause: null,
          mortality_cause_code: null,
          disposal_method: null,
          necropsy_done: false,
          necropsy_findings: null,
          suspected_scheduled_disease: false,
          suspected_disease: null,
        }),
      );
      await renderProfile();

      expect(detailValue("Scheduled disease suspected")).toBe("No");
      expect(within(detailsCard()).queryByText("Suspected disease")).not.toBeInTheDocument();
      expect(detailValue("Mortality cause")).toBe("—");
    });

    it("withholds the scheduled-disease verdict from readers without health.view", async () => {
      server.use(permissionsHandler(["animals.view"]));
      useProfileHandler(
        profileWith({
          status: "DEAD",
          status_date: "2026-07-30",
          suspected_scheduled_disease: false,
        }),
      );
      await renderProfile();

      expect(detailValue("Scheduled disease suspected")).toBe("—");
    });

    it("shows the clearance timestamp and reference once a restriction is cleared", async () => {
      useProfileHandler(
        profileWith({
          restriction_cleared_at: "2026-08-07T10:15:00Z",
          restriction_clearance_reference: "VET-CLEAR-9",
        }),
      );
      await renderProfile();

      expect(detailValue("Restriction cleared")).toBe("7 Aug 2026, 3:45 pm");
      expect(detailValue("Clearance reference")).toBe("VET-CLEAR-9");
    });

    it("falls back to an em dash when a cleared restriction carries no reference", async () => {
      useProfileHandler(
        profileWith({
          restriction_cleared_at: "2026-08-07T10:15:00Z",
          restriction_clearance_reference: null,
        }),
      );
      await renderProfile();

      expect(detailValue("Clearance reference")).toBe("—");
    });

    it("hides the clearance pair while no restriction has ever been cleared", async () => {
      await renderProfile();

      expect(within(detailsCard()).queryByText("Restriction cleared")).not.toBeInTheDocument();
      expect(within(detailsCard()).queryByText("Clearance reference")).not.toBeInTheDocument();
    });
  });

  describe("movement restriction banner", () => {
    it("shows the recorded hold reason and the authority notification date", async () => {
      useProfileHandler(
        profileWith({ ...RESTRICTED, authority_notified_at: "2026-08-06" }),
      );
      await renderProfile();

      expect(screen.getByText("Scheduled-disease suspicion")).toBeInTheDocument();
      expect(screen.getByText("Authority notified 6 Aug 2026")).toBeInTheDocument();
    });

    it("falls back to the generic hold text and stays silent about an un-notified authority", async () => {
      useProfileHandler(
        profileWith({
          movement_restricted: true,
          restriction_reason: null,
          authority_notified_at: null,
        }),
      );
      await renderProfile();

      expect(screen.getByText("A health hold is active for this animal.")).toBeInTheDocument();
      expect(screen.queryByText(/Authority notified/)).not.toBeInTheDocument();
    });
  });

  describe("movement restriction audit card", () => {
    it("hides the audit card once an empty history has loaded", async () => {
      let auditCalls = 0;
      server.use(
        http.get("/api/health/restrictions/1", () => {
          auditCalls += 1;
          return HttpResponse.json({
            animal_id: 1,
            restriction_version: 0,
            active: false,
            actions: [],
            total: 0,
            limit: 25,
            offset: 0,
          });
        }),
      );
      await renderProfile();

      await waitFor(() => expect(auditCalls).toBe(1));
      await waitFor(() =>
        expect(screen.queryByText(/Movement restriction audit/)).not.toBeInTheDocument(),
      );
    });

    it("keeps the audit card away from readers without health.view", async () => {
      let auditCalls = 0;
      server.use(
        permissionsHandler(["animals.view"]),
        http.get("/api/health/restrictions/1", () => {
          auditCalls += 1;
          return HttpResponse.json({
            animal_id: 1,
            restriction_version: 1,
            active: true,
            actions: [auditAction()],
            total: 1,
            limit: 25,
            offset: 0,
          });
        }),
      );
      useProfileHandler(profileWith(RESTRICTED));
      await renderProfile();

      expect(screen.getByText("Movement restricted")).toBeInTheDocument();
      expect(screen.queryByText(/Movement restriction audit/)).not.toBeInTheDocument();
      expect(screen.queryByText("Loading restriction audit…")).not.toBeInTheDocument();
      expect(auditCalls).toBe(0);
    });

    it("keeps the audit card mounted while its history is still loading", async () => {
      server.use(
        http.get("/api/health/restrictions/1", () => new Promise<Response>(() => {})),
      );
      await renderProfile();

      expect(await screen.findByText("Movement restriction audit (0)")).toBeInTheDocument();
      expect(screen.getByText("Loading restriction audit…")).toBeInTheDocument();
    });

    it("keeps the audit card mounted when its history fails, and retries on demand", async () => {
      let auditCalls = 0;
      server.use(
        http.get("/api/health/restrictions/1", () => {
          auditCalls += 1;
          return auditCalls === 1
            ? HttpResponse.json({ detail: "Restriction audit unavailable" }, { status: 503 })
            : HttpResponse.json({
                animal_id: 1,
                restriction_version: 1,
                active: false,
                actions: [auditAction({ action_reference: "VET-CLEAR-7" })],
                total: 1,
                limit: 25,
                offset: 0,
              });
        }),
      );
      const user = userEvent.setup();
      await renderProfile();

      expect(await screen.findByText("Restriction audit unavailable")).toBeInTheDocument();
      expect(screen.getByText("Movement restriction audit (0)")).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Retry audit" }));

      expect(await screen.findByText("VET-CLEAR-7")).toBeInTheDocument();
      expect(screen.getByText("Movement restriction audit (1)")).toBeInTheDocument();
    });

    it("renders each audited action's disease target with an em dash fallback", async () => {
      server.use(
        http.get("/api/health/restrictions/1", () =>
          HttpResponse.json({
            animal_id: 1,
            restriction_version: 2,
            active: false,
            actions: [
              auditAction({ id: 1, action_reference: "HEALTH-EVENT-41", disease_target: "PPR" }),
              auditAction({
                id: 2,
                restriction_version: 2,
                action: "CLEARED",
                action_reference: "VET-CLEAR-3",
                disease_target: null,
              }),
            ],
            total: 2,
            limit: 25,
            offset: 0,
          }),
        ),
      );
      await renderProfile();

      const placed = (await screen.findByText("HEALTH-EVENT-41")).closest("tr") as HTMLElement;
      const cleared = screen.getByText("VET-CLEAR-3").closest("tr") as HTMLElement;
      expect(within(placed).getByText("PPR")).toBeInTheDocument();
      expect(within(cleared).getByText("—")).toBeInTheDocument();
    });

    it("pages the audit by the window the server echoes back", async () => {
      const requested: Array<{ limit: number; offset: number }> = [];
      server.use(
        http.get("/api/health/restrictions/1", ({ request }) => {
          const params = new URL(request.url).searchParams;
          const offset = Number(params.get("offset") ?? 0);
          requested.push({ limit: Number(params.get("limit") ?? 0), offset });
          // The API owns the effective window: it caps this page at ten rows
          // and, once the audit has shrunk under a stale offset, clamps the
          // request back onto the last real page.
          const shrunk = offset >= 20;
          const echoedOffset = shrunk ? 10 : offset;
          return HttpResponse.json({
            animal_id: 1,
            restriction_version: 3,
            active: false,
            actions: [
              auditAction({ id: echoedOffset + 1, action_reference: `REF-${echoedOffset}` }),
            ],
            total: shrunk ? 13 : 42,
            limit: 10,
            offset: echoedOffset,
          });
        }),
      );
      const user = userEvent.setup();
      await renderProfile();
      const pager = () =>
        screen.getByRole("navigation", { name: "movement restriction actions pagination" });

      expect(
        await screen.findByText("Showing 1–10 of 42 movement restriction actions"),
      ).toBeInTheDocument();

      await user.click(within(pager()).getByRole("button", { name: "Next" }));
      await waitFor(() => expect(requested).toContainEqual({ limit: 25, offset: 10 }));
      expect(
        await screen.findByText("Showing 11–20 of 42 movement restriction actions"),
      ).toBeInTheDocument();

      await user.click(within(pager()).getByRole("button", { name: "Next" }));
      await waitFor(() => expect(requested).toContainEqual({ limit: 25, offset: 20 }));
      expect(
        await screen.findByText("Showing 11–13 of 13 movement restriction actions"),
      ).toBeInTheDocument();
    });
  });

  describe("clearance dialog", () => {
    it("closes on Cancel and on Escape while no write is in flight", async () => {
      useProfileHandler(profileWith(RESTRICTED));
      const user = userEvent.setup();
      await renderProfile();

      const dialog = await openDialog(user, "Record clearance");
      await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

      await openDialog(user, "Record clearance");
      await user.keyboard("{Escape}");
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });

    it("refuses to be dismissed while its clearance write is on the wire", async () => {
      let releaseClearance: (() => void) | undefined;
      const parked = new Promise<void>((resolve) => {
        releaseClearance = resolve;
      });
      useProfileHandler(profileWith(RESTRICTED));
      server.use(
        http.post("/api/health/restrictions/1/clear", async ({ request }) => {
          clearanceBodies.push((await request.json()) as Record<string, unknown>);
          await parked;
          return new HttpResponse(null, { status: 204 });
        }),
      );
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record clearance");
      await user.type(within(dialog).getByLabelText("Clearance reference *"), "VET-PARKED-4");
      await user.click(within(dialog).getByRole("button", { name: "Confirm clearance" }));
      await waitFor(() => expect(clearanceBodies).toHaveLength(1));

      await user.keyboard("{Escape}");
      await new Promise((resolve) => window.setTimeout(resolve, 75));
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();

      releaseClearance?.();
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(toastMock.success).toHaveBeenCalledWith(
        "Movement restriction cleared with an audit reference.",
      );
    });

    it("marks the clearance reference invalid only once an attempt has failed", async () => {
      useProfileHandler(profileWith(RESTRICTED));
      server.use(http.post("/api/health/restrictions/1/clear", () => HttpResponse.error()));
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record clearance");
      const reference = within(dialog).getByLabelText("Clearance reference *");

      expect(reference).not.toHaveAttribute("aria-invalid");

      await user.type(reference, "VET-NETWORK-3");
      await user.click(within(dialog).getByRole("button", { name: "Confirm clearance" }));

      expect(await within(dialog).findByRole("alert")).toHaveTextContent(
        "Could not clear the restriction.",
      );
      expect(reference).toHaveAttribute("aria-invalid", "true");
      expect(reference).toHaveAccessibleDescription("Could not clear the restriction.");
    });

    it("keeps a non-conflict rejection retryable without an episode refresh", async () => {
      useProfileHandler(profileWith(RESTRICTED));
      server.use(
        http.post("/api/health/restrictions/1/clear", () =>
          HttpResponse.json(
            { detail: "Clearance reference is already recorded." },
            { status: 400 },
          ),
        ),
      );
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record clearance");
      await user.type(within(dialog).getByLabelText("Clearance reference *"), "VET-DUP-1");
      await user.click(within(dialog).getByRole("button", { name: "Confirm clearance" }));

      const alert = await within(dialog).findByRole("alert");
      expect(alert.textContent).toBe("Clearance reference is already recorded.");
      const retry = within(dialog).getByRole("button", { name: "Retry clearance" });
      expect(retry).toBeEnabled();
      expect(within(dialog).getByLabelText("Clearance reference *")).toBeEnabled();
      expect(
        within(dialog).queryByRole("button", { name: "Refresh episode" }),
      ).not.toBeInTheDocument();
    });

    it("waits for the current episode when a conflict leaves the version unchanged", async () => {
      useProfileHandler(profileWith(RESTRICTED));
      server.use(
        http.post("/api/health/restrictions/1/clear", () =>
          HttpResponse.json(
            { detail: "Movement restriction was superseded." },
            { status: 409 },
          ),
        ),
      );
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record clearance");
      await user.type(within(dialog).getByLabelText("Clearance reference *"), "VET-STALE-2");
      await user.click(within(dialog).getByRole("button", { name: "Confirm clearance" }));

      expect(await within(dialog).findByRole("alert")).toHaveTextContent(
        "Movement restriction was superseded. Refreshing the current restriction episode before retrying.",
      );
      // The refreshed profile still reports version 1, so the operator must not
      // be offered a retry that would resubmit the same superseded version.
      const waiting = await within(dialog).findByRole("button", {
        name: "Waiting for current episode…",
      });
      expect(waiting).toBeDisabled();
      expect(within(dialog).getByLabelText("Clearance reference *")).toBeDisabled();

      const callsBeforeRefresh = getCalls;
      await user.click(within(dialog).getByRole("button", { name: "Refresh episode" }));
      await waitFor(() => expect(getCalls).toBeGreaterThan(callsBeforeRefresh));
    });
  });

  describe("status dialog", () => {
    it("leaves the mortality reporting fields unflagged before submission", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      await pickOption(user, within(dialog).getByLabelText(/new status/i), "DEAD");
      await user.click(
        within(dialog).getByRole("checkbox", {
          name: "Suspected scheduled/notifiable disease",
        }),
      );

      expect(within(dialog).getByLabelText("Suspected disease *")).not.toHaveAttribute(
        "aria-invalid",
      );
      expect(within(dialog).getByLabelText("Authority notified date")).not.toHaveAttribute(
        "aria-invalid",
      );
      expect(within(dialog).getByLabelText(/^Notes$/)).not.toHaveAttribute("aria-invalid");
    });

    it("flags every rejected mortality field and the notes as invalid", async () => {
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
      const authority = within(dialog).getByLabelText("Authority notified date");
      const notes = within(dialog).getByLabelText(/^Notes$/);
      setInput(authority, addDays(farmToday(), 1));
      setInput(notes, "n".repeat(256));
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

      expect(
        await within(dialog).findByText("Identify the suspected scheduled disease"),
      ).toBeInTheDocument();
      expect(disease).toHaveAttribute("aria-invalid", "true");
      expect(disease).toHaveAccessibleDescription("Identify the suspected scheduled disease");
      expect(authority).toHaveAttribute("aria-invalid", "true");
      expect(authority).toHaveAccessibleDescription("Date can't be in the future");
      expect(notes).toHaveAttribute("aria-invalid", "true");
      expect(notes).toHaveAccessibleDescription("Notes cannot exceed 255 characters");
      expect(statusBodies).toHaveLength(0);
    });

    it("drops a withdrawn scheduled-disease report instead of validating its hidden dates", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      await pickOption(user, within(dialog).getByLabelText(/new status/i), "DEAD");
      const checkbox = within(dialog).getByRole("checkbox", {
        name: "Suspected scheduled/notifiable disease",
      });
      await user.click(checkbox);
      await user.type(within(dialog).getByLabelText("Suspected disease *"), "PPR");
      setInput(
        within(dialog).getByLabelText("Authority notified date"),
        addDays(farmToday(), 1),
      );

      await user.click(checkbox);
      expect(
        within(dialog).queryByLabelText("Authority notified date"),
      ).not.toBeInTheDocument();

      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

      await waitFor(() => expect(statusBodies).toHaveLength(1));
      expect(statusBodies[0]).toMatchObject({
        new_status: "DEAD",
        suspected_scheduled_disease: false,
        suspected_disease: null,
        authority_notified_at: null,
      });
    });

    it("keeps the confirm button reporting the parked write when a duplicate submit is refused", async () => {
      let releaseStatus: (() => void) | undefined;
      const parked = new Promise<void>((resolve) => {
        releaseStatus = resolve;
      });
      server.use(
        http.post("/api/animals/1/status", async ({ request }) => {
          statusBodies.push((await request.json()) as Record<string, unknown>);
          await parked;
          return HttpResponse.json({}, { status: 201 });
        }),
      );
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
      await waitFor(() => expect(statusBodies).toHaveLength(1));

      expect(within(dialog).getByRole("button", { name: "Saving…" })).toBeInTheDocument();

      // The shared flight refuses the duplicate submit, but the footer must
      // keep reporting the write that is still on the wire rather than
      // inviting a second confirmation.
      fireEvent.submit(dialog.querySelector("form") as HTMLFormElement);
      await new Promise((resolve) => window.setTimeout(resolve, 75));
      expect(statusBodies).toHaveLength(1);
      expect(within(dialog).getByRole("button", { name: "Saving…" })).toBeInTheDocument();

      releaseStatus?.();
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });
  });

  describe("fatal background refresh failures", () => {
    it.each([
      [404, "Animal not found"],
      [403, "Animal belongs to another farm"],
      [401, "Session is no longer valid"],
    ])(
      "tears the profile down when a background refresh answers %i",
      async (status, detail) => {
        const { queryClient } = await renderProfile();
        expect(screen.getByRole("button", { name: "Record weight" })).toBeInTheDocument();
        server.use(
          http.get("/api/animals/1", () => HttpResponse.json({ detail }, { status })),
        );

        await queryClient.refetchQueries({ queryKey: ["/api/animals/1"] });

        await waitFor(() =>
          expect(screen.queryByRole("heading", { level: 1, name: /G-001/ })).not
            .toBeInTheDocument(),
        );
        expect(screen.getByRole("alert")).toHaveTextContent(detail);
        expect(
          screen.getByRole("button", { name: "Retry animal profile" }),
        ).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Record weight" })).not.toBeInTheDocument();
      },
    );

    it("keeps the profile mounted when a background refresh fails transiently", async () => {
      const { queryClient } = await renderProfile();
      server.use(
        http.get("/api/animals/1", () =>
          HttpResponse.json({ detail: "Profile refresh failed" }, { status: 503 }),
        ),
      );

      await queryClient.refetchQueries({ queryKey: ["/api/animals/1"] });

      // Assert on the banner copy itself: a sibling polite status (the
      // restriction-audit InlineLoading) can still be committing at this
      // instant and would win an undirected role query.
      expect(
        await screen.findByText("Could not refresh this profile — showing the last loaded data."),
      ).toBeInTheDocument();
      expect(screen.getByRole("heading", { level: 1, name: /G-001/ })).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Retry animal profile" }),
      ).not.toBeInTheDocument();
    });
  });
});
