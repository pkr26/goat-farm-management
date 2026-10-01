/**
 * Insurance register error-field wiring (2026-09-30 fresh mutation
 * campaign): every create-policy field and both renewal fields must carry
 * aria-invalid="true" plus an aria-describedby pointing at its own
 * role="alert" paragraph while a validation error is set — and neither
 * attribute once the field is clean.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { InsurancePolicyOut } from "@/api/generated/models";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import InsurancePage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/finance/insurance",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

beforeAll(() => {
  // jsdom lacks the pointer-capture / observer APIs Base UI Select relies on.
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  Element.prototype.scrollIntoView = vi.fn();
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (window as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO;
});

function makePolicy(overrides: Partial<InsurancePolicyOut> = {}): InsurancePolicyOut {
  return {
    id: 1,
    policy_number: "POL-2026-001",
    insurer: "Dairy Trust",
    animal_id: null,
    animal_tag: null,
    sum_insured: 50000,
    premium: 2500,
    start_date: "2026-01-01",
    renewal_date: "2027-01-01",
    status: "active",
    claimed_at: null,
    notes: null,
    created_at: "2026-01-01T00:00:00Z",
    claim_date: null,
    claimed_by_id: null,
    ...overrides,
  };
}

function wired(id: string, errorId: string) {
  const el = document.getElementById(id)!;
  expect(el.getAttribute("aria-invalid")).toBe("true");
  expect(el.getAttribute("aria-describedby")).toBe(errorId);
  expect(document.getElementById(errorId)).not.toBeNull();
}

function notWired(id: string, errorId: string) {
  const el = document.getElementById(id)!;
  expect(el.getAttribute("aria-invalid")).toBeNull();
  expect(el.getAttribute("aria-describedby")).toBeNull();
  expect(document.getElementById(errorId)).toBeNull();
}

describe("InsurancePage error-field wiring", () => {
  beforeEach(() => {
    server.use(
      permissionsHandler(["finance.view", "finance.manage", "animals.view"]),
      http.get("/api/finance/insurance", ({ request }) => {
        const params = new URL(request.url).searchParams;
        return HttpResponse.json({
          policies: [makePolicy()],
          total: 1,
          limit: Number(params.get("limit") ?? 50),
          offset: Number(params.get("offset") ?? 0),
        });
      }),
      http.post("/api/finance/insurance", () =>
        HttpResponse.json(makePolicy({ id: 99 }), { status: 201 }),
      ),
      http.post("/api/finance/insurance/:policyId/renew", () =>
        HttpResponse.json(makePolicy()),
      ),
    );
  });

  async function renderLoaded() {
    renderWithProviders(<InsurancePage />);
    await screen.findAllByText("POL-2026-001");
  }

  it("create dialog wires every required field on empty submit, unwired while clean", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: /register policy/i }));
    const dialog = await screen.findByRole("dialog");

    for (const id of ["policy_number", "insurer", "sum_insured", "premium", "start_date", "renewal_date"]) {
      notWired(
        id,
        id === "sum_insured" ? "sum-insured-error" : id === "policy_number" ? "policy-number-error" : id === "start_date" ? "start-date-error" : id === "renewal_date" ? "renewal-date-error" : `${id}-error`,
      );
    }

    // start_date defaults to today; clear it so its required error shows too.
    await user.clear(within(dialog).getByLabelText(/start date/i));
    await user.click(within(dialog).getByRole("button", { name: /^register policy$/i }));
    // zodResolver validates asynchronously: wait for the first wiring, then
    // pin every field.
    await waitFor(() => wired("policy_number", "policy-number-error"));
    wired("insurer", "insurer-error");
    wired("sum_insured", "sum-insured-error");
    wired("premium", "premium-error");
    wired("start_date", "start-date-error");
    wired("renewal_date", "renewal-date-error");
  });

  it("create dialog wires the notes field when its 500-char cap is exceeded", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: /register policy/i }));
    const dialog = await screen.findByRole("dialog");

    // The textarea's own maxLength clips interactive typing at 500, so the
    // cap error is only reachable via a programmatic over-long value.
    fireEvent.change(within(dialog).getByLabelText(/^notes/i), {
      target: { value: "n".repeat(4_001) },
    });
    await user.click(within(dialog).getByRole("button", { name: /^register policy$/i }));
    await waitFor(() => wired("policy_notes", "policy-notes-error"));
  });

  it("renewal dialog wires both fields on empty submit", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getAllByRole("button", { name: /^renew$/i })[0]!);
    const dialog = await screen.findByRole("dialog");

    // Premium is optional on renewal, but a negative value is rejected;
    // the new date may arrive defaulted, so clear it for the required error.
    await user.clear(within(dialog).getByLabelText(/new renewal date/i));
    fireEvent.change(within(dialog).getByLabelText(/premium/i), {
      target: { value: "-1" },
    });
    await user.click(within(dialog).getByRole("button", { name: /^renew policy$/i }));
    await waitFor(() => wired("new_renewal_date", "new-renewal-date-error"));
    wired("renewal_premium", "renewal-premium-error");
  });
});
