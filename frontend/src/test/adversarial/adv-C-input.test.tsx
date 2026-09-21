/**
 * ADVERSARIAL AUDIT C1/C2/C5 — input attacks through real forms (executed).
 *
 * C1  Numeric extremes against the money field: 1e308, -1, 0.004, 0.001 —
 *     none may reach the wire; each must be caught inline.
 * C2  Overlong free text: the animal-create dialog caps name at 80 chars in
 *     zod but renders no error and sets no maxLength → a 100-char name is a
 *     SILENT block. → expected to CONFIRM open finding M-4.
 * C2b Unicode smuggling: emoji, RTL overrides and combining diacritics must
 *     round-trip to the wire verbatim (no mangling, no strip).
 * C5  Markup in every rendered free-text surface must render as text.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import AnimalsPage from "@/app/(app)/animals/page";
import FinancePage from "@/app/(app)/finance/page";
import { StatusBadge } from "@/components/status-badge";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/msw-server";
import { settle } from "@/test/settle";

const { toastMock } = vi.hoisted(() => ({
  toastMock: { success: vi.fn(), error: vi.fn() },
}));
vi.mock("sonner", () => ({ toast: toastMock }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/finance",
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

const FINANCE_PAYLOAD = {
  transactions: [],
  transactions_total: 0,
  limit: 50,
  offset: 0,
  total_income: 0,
  total_expense: 0,
  pnl: [],
};

describe("ADV C1: numeric extremes never reach the money wire", () => {
  let posts: Array<{ amount?: number; notes?: string | null }>;
  beforeEach(() => {
    posts = [];
    server.use(
      http.get("/api/finance", () => HttpResponse.json(FINANCE_PAYLOAD)),
      http.post("/api/finance/new", async ({ request }) => {
        posts.push((await request.json()) as { amount?: number });
        return HttpResponse.json({ ok: true }, { status: 201 });
      }),
    );
  });

  it.each([
    ["1e308 (float overflow bait)", "1e308"],
    ["-1 (negative)", "-1"],
    ["0.004 (below ₹0.005 floor)", "0.004"],
    ["0.001 (rounds to zero paise)", "0.001"],
    ["1e9+1 (above ₹1,000,000,000 cap)", "1000000001"],
  ])("%s is blocked inline with a visible reason", async (_label, evil) => {
    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);
    expect(await screen.findByText("Monthly P&L (last 12 months)")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "New transaction" }));
    const dialog = await screen.findByRole("dialog");
    // Overwrite any pre-existing value in the number box.
    const amount = within(dialog).getByLabelText("Amount (₹) *") as HTMLInputElement;
    await user.clear(amount);
    await user.type(amount, evil);
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    await waitFor(() =>
      expect(within(dialog).getAllByRole("alert").length).toBeGreaterThan(0),
    );
    expect(posts).toHaveLength(0);
  });

  it("a legitimate amount reaches the wire unmangled", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);
    expect(await screen.findByText("Monthly P&L (last 12 months)")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "New transaction" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Amount (₹) *"), "150.25");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].amount).toBe(150.25);
  });
});

describe("ADV C2b: hostile unicode round-trips verbatim through free text", () => {
  it("emoji + RTL override + combining marks survive to the request body", async () => {
    const hostile = "goat\u202Eevil\ud83d\udc10e\u0301\u0301";
    let captured: { notes?: string | null } | null = null;
    server.use(
      http.get("/api/finance", () => HttpResponse.json(FINANCE_PAYLOAD)),
      http.post("/api/finance/new", async ({ request }) => {
        captured = (await request.json()) as { notes?: string | null };
        return HttpResponse.json({ ok: true }, { status: 201 });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);
    expect(await screen.findByText("Monthly P&L (last 12 months)")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "New transaction" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Amount (₹) *"), "10");
    await user.type(within(dialog).getByLabelText("Notes"), hostile);
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));
    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured!.notes).toBe(hostile);
  });
});

describe("ADV C2: overlong animal name is a SILENT block (expected to CONFIRM M-4)", () => {
  let creates: number;
  beforeEach(() => {
    creates = 0;
    server.use(
      http.get("/api/animals", () =>
        HttpResponse.json({ animals: [], total: 0, limit: 50, offset: 0 }),
      ),
      http.post("/api/animals", () => {
        creates += 1;
        return HttpResponse.json({ id: 1 }, { status: 201 });
      }),
    );
  });

  it("DEFENDED: a 100-character name is blocked AND explained inline", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    expect(await screen.findByText("Add animal")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Add animal" }));
    const dialog = await screen.findByRole("dialog");
    const nameInput = within(dialog).getByLabelText("Name") as HTMLInputElement;
    // The input no longer accepts more than the schema allows…
    expect(nameInput.maxLength).toBe(80);
    // …and a programmatic overlong value (bypassing maxLength) is blocked
    // WITH a visible, announced error.
    fireEvent.change(nameInput, { target: { value: "N".repeat(100) } });
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    // Give any would-be request a moment to fire.
    await settle(150);
    expect(creates).toBe(0);
    await waitFor(() =>
      expect(within(dialog).getAllByRole("alert").length).toBeGreaterThan(0),
    );
    expect(nameInput).toHaveAttribute("aria-invalid", "true");
    expect(nameInput.getAttribute("aria-describedby")).toContain("create-name-error");
  });

  it("the same attack on tag_number IS caught visibly (the in-repo standard)", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    expect(await screen.findByText("Add animal")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add animal" }));
    const dialog = await screen.findByRole("dialog");
    const tag = within(dialog).getByLabelText("Tag number") as HTMLInputElement;
    // Bypass the input (no maxLength either) with a programmatic value + submit,
    // proving the difference is the missing error NODE, not the schema.
    fireEvent.change(tag, { target: { value: "T".repeat(60) } });
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));
    await waitFor(() =>
      expect(within(dialog).getByText(/50/)).toBeInTheDocument(),
    );
    // FIXED (N-3): the tag error is announced (role="alert") and wired via
    // aria-describedby to the input.
    expect(within(dialog).getAllByRole("alert").length).toBeGreaterThan(0);
  });
});

describe("ADV C5: markup in rendered free text stays text", () => {
  it("StatusBadge with a hostile enum renders escaped content", () => {
    const { container } = renderWithProviders(
      <StatusBadge status={'<img src=x onerror="alert(1)">' as "ACTIVE"} />,
    );
    expect(container.textContent).toMatch(/<img src=x/i);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
  });
});
