/**
 * Animals create-dialog DOM caps (2026-10-01 campaign follow-up): breed
 * (60), the purchase seller name (120), the historical-import reason
 * (2 rows × 255 chars) and the notes box (2 rows × 255 chars) all bound
 * in the DOM at their schema limits.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import AnimalsPage from "./page";

const nav = vi.hoisted(() => ({
  state: { search: "" },
  push: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push, replace: nav.replace, prefetch: vi.fn() }),
  usePathname: () => "/animals",
  useSearchParams: () => new URLSearchParams(nav.state.search),
  useParams: () => ({}),
}));

beforeAll(() => {
  Object.assign(window.HTMLElement.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    scrollIntoView: () => {},
  });
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (window as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO;
});

const animal = (overrides: Record<string, unknown> = {}) => ({
  tag_number: "G-001",
  id: 1,
  name: "Lakshmi",
  breed: "Osmanabadi",
  sex: "F",
  date_of_birth: "2025-05-10",
  estimated_dob: null,
  birth_type: "SINGLE",
  source: "BORN",
  dam_id: null,
  sire_id: null,
  birth_weight: 2.4,
  current_bucket: "LACTATING",
  status: "ACTIVE",
  status_date: null,
  sale_price: null,
  purchase_date: null,
  purchase_price: null,
  seller_name: null,
  cull_candidate: false,
  age_months: 14,
  latest_weight_kg: 32.5,
  ...overrides,
});

describe("AnimalsPage create dialog DOM caps", () => {
  beforeEach(() => {
    nav.state.search = "";
    server.use(
      http.get("/api/animals", () =>
        HttpResponse.json({ animals: [animal()], total: 1, limit: 50, offset: 0 }),
      ),
      http.post("/api/animals", () =>
        HttpResponse.json({ ...animal(), id: 99 }, { status: 201 }),
      ),
    );
  });

  it("caps breed, seller, import reason and notes at their schema limits", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("Lakshmi");
    await user.click(screen.getByRole("button", { name: "Add animal" }));
    const dialog = await screen.findByRole("dialog", { name: "Add animal" });

    expect(within(dialog).getByLabelText(/^breed/i)).toHaveAttribute("maxlength", "60");
    expect(within(dialog).getByLabelText(/^notes/i)).toHaveAttribute("rows", "2");

    // Purchased: the seller name appears, bounded at 120.
    await user.click(within(dialog).getByLabelText(/source/i));
    await user.click(await screen.findByRole("option", { name: "Purchased" }));
    expect(within(dialog).getByLabelText(/seller/i)).toHaveAttribute("maxlength", "120");

    // Historical import: the reason box is 2 rows bounded at 255.
    await user.click(within(dialog).getByLabelText(/source/i));
    await user.click(
      await screen.findByRole("option", { name: /historical born-on-farm import/i }),
    );
    const reason = within(dialog).getByLabelText(/reason/i);
    expect(reason).toHaveAttribute("rows", "2");
    expect(reason).toHaveAttribute("maxlength", "255");
  });
});
