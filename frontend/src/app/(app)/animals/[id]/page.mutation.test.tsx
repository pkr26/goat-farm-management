/**
 * Animal profile mutation-hardening — pins behaviour the wider suites leave
 * open: discarding dialog drafts on cancel (weight/move/status), treating a
 * cleared numeric input as "unset" rather than 0, the health-event CTA's
 * permission gate and returnTo link, the permission-aware wording of the
 * weight empty state, exact em-dash fallbacks in the Details grid, the kids
 * card's humanised cells and copy, and the permissions dead-end's retry.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import AnimalProfilePage from "./page";

const nav = vi.hoisted(() => ({ id: "1", search: "" }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/animals/1",
  useSearchParams: () => new URLSearchParams(nav.search),
  useParams: () => ({ id: nav.id }),
}));

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
  dam_id: null,
  sire_id: null,
  birth_weight: 2.4,
  current_bucket: "RESTING",
  status: "ACTIVE",
  status_date: null,
  sale_price: null,
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
  mortality_reported_at: null,
  notes: null,
  created_at: "2026-01-01T05:30:00Z",
  age_months: 14,
  latest_weight_kg: 32.5,
  days_in_current_bucket: 12,
};

const KID = {
  ...ANIMAL,
  id: 21,
  tag_number: "G-021",
  name: null,
  sex: "M",
  date_of_birth: "2026-06-01",
  status: "ACTIVE",
  dam_id: 1,
  sire_id: null,
  notes: null,
  days_in_current_bucket: 5,
};

const EMPTY_PROFILE = {
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

const PROFILE_WITH_KID = { ...EMPTY_PROFILE, kids: [KID], kids_total: 1 };

describe("AnimalProfilePage mutation hardening", () => {
  let weightBodies: Record<string, unknown>[];
  let clearances: Record<string, unknown>[];

  const clearanceBodies = () => clearances;

  beforeEach(() => {
    nav.id = "1";
    nav.search = "";
    weightBodies = [];
    clearances = [];
    vi.clearAllMocks();
    server.use(
      http.get("/api/animals/1", () => HttpResponse.json(PROFILE_WITH_KID)),
      http.post("/api/animals/1/weight", async ({ request }) => {
        weightBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json(
          { id: 99, date: "2026-08-06", weight_kg: 30, bcs: null, notes: null },
          { status: 201 },
        );
      }),
      http.post("/api/health/restrictions/1/clear", async ({ request }) => {
        clearances.push((await request.json()) as Record<string, unknown>);
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

  async function renderProfile(profile: Record<string, unknown> = PROFILE_WITH_KID) {
    server.use(http.get("/api/animals/1", () => HttpResponse.json(profile)));
    const view = renderWithProviders(<AnimalProfilePage />);
    await screen.findByRole("heading", { level: 1, name: /G-001/ });
    return view;
  }

  async function openDialog(user: User, button: string) {
    await user.click(screen.getByRole("button", { name: button }));
    return await screen.findByRole("dialog");
  }

  describe("cancel discards the abandoned draft", () => {
    it("clears a typed weight entry when the weight dialog is dismissed", async () => {
      const user = userEvent.setup();
      await renderProfile();
      let dialog = await openDialog(user, "Record weight");
      setInput(within(dialog).getByLabelText(/weight \(kg\)/i), "44.5");
      setInput(within(dialog).getByLabelText(/notes/i), "drafted then abandoned");

      await user.keyboard("{Escape}");
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

      // Reopening must not resurface the abandoned draft (L10).
      dialog = await openDialog(user, "Record weight");
      expect(within(dialog).getByLabelText(/weight \(kg\)/i)).toHaveValue(null);
      expect(within(dialog).getByLabelText(/notes/i)).toHaveValue("");
    });

    it("clears a chosen bucket and reason when the move dialog is dismissed", async () => {
      const user = userEvent.setup();
      await renderProfile();
      let dialog = await openDialog(user, "Move bucket");
      await pickOption(user, within(dialog).getByRole("combobox"), "Breeding");
      await user.type(within(dialog).getByLabelText(/reason/i), "drafted reason");

      await user.keyboard("{Escape}");
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

      dialog = await openDialog(user, "Move bucket");
      expect(within(dialog).getByRole("combobox")).toHaveTextContent("Choose bucket…");
      expect(within(dialog).getByLabelText(/reason/i)).toHaveValue("");
    });

    it("restores the SOLD default when a DEAD draft is dismissed", async () => {
      const user = userEvent.setup();
      await renderProfile();
      let dialog = await openDialog(user, "Change status");
      await pickOption(user, within(dialog).getByRole("combobox"), "DEAD");
      await user.type(within(dialog).getByLabelText("Mortality cause"), "drafted cause");

      await user.keyboard("{Escape}");
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

      dialog = await openDialog(user, "Change status");
      expect(within(dialog).getByRole("combobox")).toHaveTextContent("SOLD");
      expect(within(dialog).queryByLabelText("Mortality cause")).not.toBeInTheDocument();
      expect(within(dialog).getByLabelText(/sale price/i)).toBeInTheDocument();
    });
  });

  describe("cleared numeric inputs mean “unset”, not 0", () => {
    it("submits a null BCS after a typed value is cleared back to empty", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record weight");
      const bcs = within(dialog).getByLabelText(/bcs/i);
      setInput(within(dialog).getByLabelText(/weight \(kg\)/i), "30");
      setInput(bcs, "4");
      setInput(bcs, "");

      await user.click(within(dialog).getByRole("button", { name: "Save" }));

      await waitFor(() => expect(weightBodies).toHaveLength(1));
      expect(weightBodies[0]).toEqual({
        date: null,
        weight_kg: 30,
        bcs: null,
        notes: null,
      });
    });

    it("does not flag the cleared BCS as a below-minimum number", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record weight");
      const bcs = within(dialog).getByLabelText(/bcs/i);
      setInput(within(dialog).getByLabelText(/weight \(kg\)/i), "30");
      setInput(bcs, "4");
      setInput(bcs, "");
      await user.click(within(dialog).getByRole("button", { name: "Save" }));

      await waitFor(() => expect(weightBodies).toHaveLength(1));
      expect(within(dialog).queryByText(/expected number/)).not.toBeInTheDocument();
    });
  });

  describe("health events CTA", () => {
    it("links managers to the prefilled health-event form for this animal", async () => {
      await renderProfile(EMPTY_PROFILE as unknown as Record<string, unknown>);

      const cta = screen.getByRole("link", { name: "Add a health event" });
      expect(cta).toHaveAttribute("href", "/health/new?returnTo=%2Fanimals%2F1");
      // Rendered as a small outline button, not the primary variant.
      expect(cta.className).toContain("border-border");
      expect(cta.className).toContain("text-[0.8rem]");
      expect(cta.className).not.toContain("bg-primary");
    });

    it("hides the CTA from viewers without health.manage", async () => {
      server.use(permissionsHandler(["animals.view", "health.view"]));
      await renderProfile(EMPTY_PROFILE as unknown as Record<string, unknown>);

      expect(screen.queryByRole("link", { name: "Add a health event" })).not.toBeInTheDocument();
    });
  });

  describe("weight empty state wording", () => {
    it("points permitted users at the Record weight action", async () => {
      await renderProfile(EMPTY_PROFILE as unknown as Record<string, unknown>);

      expect(
        screen.getByText(
          "Use the “Record weight” action above to log the first entry and start the growth curve.",
        ),
      ).toBeInTheDocument();
    });

    it("tells unpermitted viewers who can log the first entry", async () => {
      server.use(permissionsHandler(["animals.view"]));
      await renderProfile(EMPTY_PROFILE as unknown as Record<string, unknown>);

      expect(
        screen.getByText(
          "A user with the weight permission can log the first entry and start the growth curve.",
        ),
      ).toBeInTheDocument();
    });
  });

  describe("details grid fallbacks", () => {
    it("renders an em dash exactly when the birth type is unknown", async () => {
      server.use(
        http.get("/api/animals/1", () =>
          HttpResponse.json({
            ...PROFILE_WITH_KID,
            animal: { ...ANIMAL, birth_type: null },
          }),
        ),
      );
      renderWithProviders(<AnimalProfilePage />);
      await screen.findByRole("heading", { level: 1, name: /G-001/ });

      const label = screen.getByText("Birth type");
      expect(label.nextElementSibling?.textContent).toBe("—");
    });
  });

  describe("kids card", () => {
    it("humanises the kid's sex instead of showing the raw code", async () => {
      await renderProfile();

      const card = screen.getByText("Kids (1)").closest('[data-slot="card"]') as HTMLElement;
      expect(within(card).getByText("Male")).toBeInTheDocument();
    });

    it("explains in farm vocabulary how offspring appear", async () => {
      server.use(
        http.get("/api/animals/1", () =>
          HttpResponse.json({
            ...PROFILE_WITH_KID,
            kids: [],
            kids_total: 0,
          }),
        ),
      );
      renderWithProviders(<AnimalProfilePage />);
      expect(
        await screen.findByText(
          "Offspring from this animal appear here as kidding records are added.",
        ),
      ).toBeInTheDocument();
    });
  });

  describe("permissions dead-end", () => {
    it("recovers through the in-place retry once permissions load again", async () => {
      let permCalls = 0;
      server.use(
        http.get("/api/auth/permissions", () => {
          permCalls += 1;
          if (permCalls === 1) {
            return HttpResponse.json({ detail: "permissions unavailable" }, { status: 503 });
          }
          return HttpResponse.json({ is_owner: false, permissions: [] });
        }),
      );
      renderWithProviders(<AnimalProfilePage />);

      expect(
        await screen.findByText(/Could not load your permissions/),
      ).toBeInTheDocument();
      await userEvent.setup().click(screen.getByRole("button", { name: "Retry permissions" }));

      // The refetch resolves the denial path: still no profile request.
      await waitFor(() => expect(permCalls).toBe(2));
      expect(await screen.findByText(/don't have access to this page/)).toBeInTheDocument();
    });
  });

  describe("clearance guards", () => {
    const RESTRICTED = {
      ...PROFILE_WITH_KID,
      animal: {
        ...ANIMAL,
        movement_restricted: true,
        restriction_reason: "Scheduled-disease suspicion",
        restriction_version: 1,
      },
    };

    it("locks the dialog while the profile snapshot is refreshing and unlocks after", async () => {
      let profileCalls = 0;
      let releaseRefresh: (() => void) | undefined;
      const refreshGate = new Promise<void>((resolve) => {
        releaseRefresh = resolve;
      });
      server.use(
        http.get("/api/animals/1", async () => {
          profileCalls += 1;
          if (profileCalls > 1) await refreshGate;
          return HttpResponse.json(RESTRICTED);
        }),
      );
      const user = userEvent.setup();
      const { queryClient } = renderWithProviders(<AnimalProfilePage />);
      await screen.findByRole("heading", { level: 1, name: /G-001/ });

      const dialog = await openDialog(user, "Record clearance");
      const reference = within(dialog).getByLabelText("Clearance reference *");
      await user.type(reference, "VET-HELD-1");
      expect(within(dialog).getByRole("button", { name: "Confirm clearance" })).toBeEnabled();

      const refetching = queryClient.refetchQueries({ type: "active" });
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "Record clearance", hidden: true }),
        ).toBeDisabled(),
      );
      expect(reference).toBeDisabled();
      expect(within(dialog).getByRole("button", { name: "Confirm clearance" })).toBeDisabled();

      releaseRefresh?.();
      await refetching;
      await waitFor(() => expect(reference).toBeEnabled());
      expect(within(dialog).getByRole("button", { name: "Confirm clearance" })).toBeEnabled();
      expect(clearanceBodies()).toHaveLength(0);
    });
  });
});
