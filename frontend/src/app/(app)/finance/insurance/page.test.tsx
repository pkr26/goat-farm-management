/**
 * Insurance register page: the list's columns and animal links, the create
 * dialog's backend-parity validations and payload shape, the renew action's
 * forward-only horizon rule, and the finance/animals permission gates.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { InsurancePolicyOut } from "@/api/generated/models";
import { addDays, farmToday, formatDate } from "@/lib/format";
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

type User = ReturnType<typeof userEvent.setup>;

function makePolicy(overrides: Partial<InsurancePolicyOut> = {}): InsurancePolicyOut {
  return {
    id: 1,
    policy_number: "POL-2026-001",
    insurer: "Oriental Insurance",
    animal_id: 11,
    animal_tag: "G-011",
    sum_insured: 12000,
    premium: 480,
    start_date: "2026-01-10",
    renewal_date: addDays(farmToday(), 20),
    status: "active",
    notes: null,
    created_at: "2026-01-10T05:30:00Z",
    ...overrides,
  };
}

const HERD_POLICY = makePolicy({
  id: 2,
  policy_number: "HERD-2026",
  animal_id: null,
  animal_tag: null,
});

describe("InsurancePage", () => {
  let postBody: Record<string, unknown> | null;
  let renewBodies: Record<string, unknown>[];

  beforeEach(() => {
    postBody = null;
    renewBodies = [];
    server.use(
      http.get("/api/finance/insurance", ({ request }) => {
        const params = new URL(request.url).searchParams;
        return HttpResponse.json({
          policies: [makePolicy(), HERD_POLICY],
          total: 2,
          limit: Number(params.get("limit") ?? 50),
          offset: Number(params.get("offset") ?? 0),
        });
      }),
      http.post("/api/finance/insurance", async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makePolicy({ id: 99 }), { status: 201 });
      }),
      http.post("/api/finance/insurance/:policyId/renew", async ({ request }) => {
        renewBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json(makePolicy());
      }),
    );
  });

  async function renderLoaded() {
    renderWithProviders(<InsurancePage />);
    await screen.findByText("POL-2026-001");
  }

  function rowOf(text: string): HTMLElement {
    const row = screen.getByText(text).closest("tr");
    expect(row).not.toBeNull();
    return row as HTMLElement;
  }

  it("lists every policy column with the animal tag when present", async () => {
    await renderLoaded();

    const row = rowOf("POL-2026-001");
    expect(within(row).getByRole("link", { name: "G-011" })).toHaveAttribute(
      "href",
      "/animals/11",
    );
    expect(within(row).getByText("Oriental Insurance")).toBeInTheDocument();
    expect(within(row).getByText("₹12,000")).toBeInTheDocument();
    expect(within(row).getByText("₹480")).toBeInTheDocument();
    expect(within(row).getByText("Active")).toBeInTheDocument();
    // A herd-level policy has no tag to link.
    const herdRow = rowOf("HERD-2026");
    expect(within(herdRow).getAllByText("—").length).toBeGreaterThan(0);
    expect(within(herdRow).queryByRole("link")).not.toBeInTheDocument();
  });

  it("prints tags as plain text without animals.view", async () => {
    server.use(permissionsHandler(["finance.view"]));
    await renderLoaded();

    expect(screen.getByText("G-011")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "G-011" })).not.toBeInTheDocument();
  });

  it("hides the register behind finance.view", async () => {
    server.use(permissionsHandler(["animals.view"]));
    renderWithProviders(<InsurancePage />);

    expect(
      await screen.findByText("You don't have access to this page."),
    ).toBeInTheDocument();
    expect(screen.queryByText("POL-2026-001")).not.toBeInTheDocument();
  });

  it("hides the create and renew actions without finance.manage", async () => {
    server.use(permissionsHandler(["finance.view", "animals.view"]));
    await renderLoaded();

    expect(screen.queryByRole("button", { name: /register policy/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Renew" })).not.toBeInTheDocument();
  });

  describe("create dialog", () => {
    async function openCreate(user: User) {
      await user.click(screen.getByRole("button", { name: /register policy/i }));
      return screen.findByRole("dialog");
    }

    it("registers a policy with the exact backend payload", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      const dialog = await openCreate(user);

      await user.type(within(dialog).getByLabelText(/policy number/i), "POL-2026-042");
      await user.type(within(dialog).getByLabelText(/insurer/i), "Bajaj Allianz");
      fireEvent.change(within(dialog).getByLabelText(/sum insured/i), { target: { value: "15000" } });
      fireEvent.change(within(dialog).getByLabelText(/^premium/i), { target: { value: "600" } });
      fireEvent.change(within(dialog).getByLabelText(/start date/i), {
        target: { value: "2026-01-10" },
      });
      fireEvent.change(within(dialog).getByLabelText(/renewal date/i), {
        target: { value: "2027-01-10" },
      });
      await user.click(within(dialog).getByRole("button", { name: /register policy/i }));

      await waitFor(() => expect(postBody).not.toBeNull());
      expect(postBody).toEqual({
        policy_number: "POL-2026-042",
        insurer: "Bajaj Allianz",
        sum_insured: 15000,
        premium: 600,
        start_date: "2026-01-10",
        renewal_date: "2027-01-10",
        animal_id: null,
        notes: null,
      });
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });

    it("rejects a blank identifier and an early renewal inline", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      const dialog = await openCreate(user);

      // Only money is filled: the identifiers and dates must fail by name.
      fireEvent.change(within(dialog).getByLabelText(/sum insured/i), { target: { value: "15000" } });
      fireEvent.change(within(dialog).getByLabelText(/^premium/i), { target: { value: "600" } });
      fireEvent.change(within(dialog).getByLabelText(/start date/i), {
        target: { value: "2026-01-10" },
      });
      fireEvent.change(within(dialog).getByLabelText(/renewal date/i), {
        target: { value: "2026-01-09" },
      });
      await user.click(within(dialog).getByRole("button", { name: /register policy/i }));

      expect(await within(dialog).findByText("Policy number is required")).toBeInTheDocument();
      expect(within(dialog).getByText("Insurer is required")).toBeInTheDocument();
      expect(
        within(dialog).getByText("Renewal date cannot be before the start date"),
      ).toBeInTheDocument();
      expect(postBody).toBeNull();
    });

    it("rejects a future start date like the wire schema does", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      const dialog = await openCreate(user);

      fireEvent.change(within(dialog).getByLabelText(/start date/i), {
        target: { value: addDays(farmToday(), 1) },
      });
      await user.click(within(dialog).getByRole("button", { name: /register policy/i }));

      expect(await within(dialog).findByText("Start date can't be in the future")).toBeInTheDocument();
      expect(postBody).toBeNull();
    });
  });

  describe("renew action", () => {
    async function openRenew(user: User, policyNumber: string) {
      const row = rowOf(policyNumber);
      await user.click(
        within(row).getByRole("button", { name: "Renew" }) ??
          within(row).getAllByRole("button", { name: "Renew" })[0],
      );
      return screen.findByRole("dialog");
    }

    it("renews forward with an optional corrected premium", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      const dialog = await openRenew(user, "POL-2026-001");

      const policy = makePolicy();
      fireEvent.change(within(dialog).getByLabelText(/new renewal date/i), {
        target: { value: addDays(policy.renewal_date, 365) },
      });
      fireEvent.change(within(dialog).getByLabelText(/corrected premium/i), {
        target: { value: "510" },
      });
      await user.click(within(dialog).getByRole("button", { name: /renew policy/i }));

      await waitFor(() => expect(renewBodies).toHaveLength(1));
      expect(renewBodies[0]).toEqual({
        renewal_date: addDays(policy.renewal_date, 365),
        premium: 510,
      });
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });

    it("refuses to move the horizon backwards", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      const dialog = await openRenew(user, "POL-2026-001");

      const policy = makePolicy();
      fireEvent.change(within(dialog).getByLabelText(/new renewal date/i), {
        target: { value: addDays(policy.renewal_date, -1) },
      });
      await user.click(within(dialog).getByRole("button", { name: /renew policy/i }));

      expect(
        await within(dialog).findByText(
          `Cannot be before the current renewal date ${formatDate(policy.renewal_date)}`,
        ),
      ).toBeInTheDocument();
      expect(renewBodies).toHaveLength(0);
    });
  });
});
