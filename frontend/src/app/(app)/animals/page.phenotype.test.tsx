/**
 * Phenotype fields (coat_color / horned): the create dialog offers them as
 * optional selects, sends them only when the operator recorded a value (a
 * backend that predates the contract never sees the keys), and the detail
 * page renders only what the payload carries.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import AnimalsPage from "./page";

const { navState } = vi.hoisted(() => ({ navState: { search: "" } }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/animals",
  useSearchParams: () => new URLSearchParams(navState.search),
  useParams: () => ({}),
}));

const ANIMAL = {
  id: 1,
  tag_number: "G-001",
  name: null,
  breed: "Osmanabadi",
  sex: "F",
  date_of_birth: "2025-05-10",
  estimated_dob: null,
  birth_type: "TWINS",
  source: "BORN",
  dam_id: null,
  sire_id: null,
  birth_weight: 2.4,
  current_bucket: "FOUNDATION",
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

/** Open a Base UI select next to a labelled control and pick an option. */
async function pick(
  user: ReturnType<typeof userEvent.setup>,
  dialog: HTMLElement,
  label: string,
  option: string,
) {
  const control = within(dialog).getByLabelText(label);
  await user.click(control);
  await user.click(await screen.findByRole("option", { name: option }));
}

describe("AnimalsPage — phenotype fields", () => {
  let postBody: Record<string, unknown> | null;

  beforeEach(() => {
    postBody = null;
    navState.search = "";
    server.use(
      http.get("/api/animals", () =>
        HttpResponse.json({ animals: [ANIMAL], total: 1 }),
      ),
      http.post("/api/animals", async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...ANIMAL, id: 2 }, { status: 201 });
      }),
    );
  });

  it("omits coat_color/horned from the payload when left unrecorded", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    expect((await screen.findAllByText("G-001"))[0]).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Add animal" }));
    const dialog = await screen.findByRole("dialog");
    // Both selects default to "Not recorded".
    expect(within(dialog).getByLabelText("Coat colour")).toHaveTextContent("Not recorded");
    expect(within(dialog).getByLabelText("Horned")).toHaveTextContent("Not recorded");
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).not.toHaveProperty("coat_color");
    expect(postBody).not.toHaveProperty("horned");
  });

  it("sends coat_color and horned when the operator records them", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    expect((await screen.findAllByText("G-001"))[0]).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Add animal" }));
    const dialog = await screen.findByRole("dialog");
    await pick(user, dialog, "Coat colour", "Black with patches");
    await pick(user, dialog, "Horned", "Yes");
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ coat_color: "black_patched", horned: true });
  });
});
