// REGRESSION TESTS — bug fixed; these tests pin the fix.
//
// Closed Base UI Select triggers used to render the raw option VALUE instead
// of the option's label. Every Select in the animals pages passes nice
// display labels as SelectItem children ("Female", "Purchased", "FEMALE
// KIDS"), but Base UI's Select.Value only resolves labels from the root
// `items` prop or a children render function (see
// @base-ui/react/internals/resolveValueLabel: `resolveSelectedLabel` falls
// back to `stringifyAsLabel(value)` when no `items` map is provided). The
// animals pages did neither, so the trigger showed the raw enum: "F",
// "BORN", "PREGNANCY_EARLY" (with underscores).
//
// The pages now pass a value→label `items` map on every Select root; these
// tests pin the labeled trigger.

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import AnimalsPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/animals",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

beforeAll(() => {
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
  current_bucket: "PREGNANCY_EARLY",
  status: "ACTIVE",
  status_date: null,
  sale_price: null,
  purchase_date: null,
  purchase_price: null,
  seller_name: null,
  cull_candidate: false,
  notes: null,
  created_at: "2026-01-01T05:30:00Z",
  age_months: 14,
  latest_weight_kg: 32.5,
};

beforeEach(() => {
  server.use(
    http.get("/api/animals", () => HttpResponse.json({ animals: [ANIMAL], total: 1 })),
  );
});

describe("Select trigger labels (suspected bug: raw value shown instead of label)", () => {
  it("filter select shows 'Female kids' after picking that option, not 'FEMALE_KIDS'", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");
    const trigger = screen.getAllByRole("combobox")[0];
    await user.click(trigger);
    await user.click(await screen.findByRole("option", { name: "Female kids" }));
    expect(trigger).toHaveTextContent("Female kids");
    expect(trigger).not.toHaveTextContent("FEMALE_KIDS");
  });

  it("filter select shows 'Female' after picking it, not 'F'", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");
    const trigger = screen.getAllByRole("combobox")[1];
    await user.click(trigger);
    await user.click(await screen.findByRole("option", { name: "Female" }));
    expect(trigger).toHaveTextContent("Female");
  });

  it("status filter shows 'All statuses' by default, not the raw 'ALL' sentinel", async () => {
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");
    expect(screen.getByLabelText("Filter animals by status")).toHaveTextContent(
      "All statuses",
    );
  });

  it("create dialog defaults show 'Female' and 'Purchased', not raw enum values", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");
    await user.click(screen.getByRole("button", { name: "Add animal" }));
    const dialog = await screen.findByRole("dialog");
    const combos = within(dialog).getAllByRole("combobox");
    expect(combos[0]).toHaveTextContent("Female");
    expect(combos[1]).toHaveTextContent("Purchased");
  });
});
