/**
 * Extended AnimalsPage coverage (complements page.test.tsx): list rendering
 * details and null fallbacks, loading/empty/error states, bucket/sex/status/
 * search filter wiring to GET query params, RBAC gating (animals.view /
 * animals.create), and the Add-animal dialog's zod validation boundaries
 * (tag length, non-negative/positive weights), PURCHASED-only fields, and
 * server error paths.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import AnimalsPage from "./page";

const nav = vi.hoisted(() => ({ searchParams: new URLSearchParams() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/animals",
  useSearchParams: () => nav.searchParams,
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const toastMock = toast as unknown as { success: ReturnType<typeof vi.fn>; error: ReturnType<typeof vi.fn> };

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

const SPARSE_ANIMAL = {
  ...ANIMAL,
  id: 2,
  tag_number: "G-002",
  name: null,
  status: "SOLD",
  age_months: null,
  latest_weight_kg: null,
};

beforeAll(() => {
  // jsdom lacks the pointer-capture / observer APIs Radix Select relies on.
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

function setDate(input: HTMLElement, value: string) {
  fireEvent.change(input, { target: { value } });
}

describe("AnimalsPage extended", () => {
  let seenParams: URLSearchParams[];
  let postCalls: number;
  let postBody: Record<string, unknown> | null;

  function setupListHandler(animals: unknown[] = [ANIMAL]) {
    server.use(
      http.get("/api/animals", ({ request }) => {
        seenParams.push(new URL(request.url).searchParams);
        return HttpResponse.json({ animals, total: animals.length });
      }),
      http.post("/api/animals", async ({ request }) => {
        postCalls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          { ...ANIMAL, id: 99, tag_number: String(postBody.tag_number) },
          { status: 201 },
        );
      }),
    );
  }

  beforeEach(() => {
    nav.searchParams = new URLSearchParams();
    seenParams = [];
    postCalls = 0;
    postBody = null;
    vi.clearAllMocks();
    setupListHandler();
  });

  async function renderLoaded(animals?: unknown[]) {
    if (animals) setupListHandler(animals);
    renderWithProviders(<AnimalsPage />);
    const rows = animals ?? [ANIMAL];
    if (rows.length === 0) {
      await screen.findByText("No animals match these filters.");
    } else {
      await screen.findByText(`${rows.length} animal(s)`);
    }
  }

  async function openCreateDialog(user: User) {
    await user.click(screen.getByRole("button", { name: "Add animal" }));
    return await screen.findByRole("dialog");
  }

  describe("list rendering", () => {
    it("renders every cell with display formatting (bucket spaces, weight kg)", async () => {
      await renderLoaded();
      const row = screen.getByText("G-001").closest("tr") as HTMLElement;
      const cells = within(row).getAllByRole("cell");
      expect(cells[1]).toHaveTextContent("Lakshmi");
      expect(cells[2]).toHaveTextContent("F");
      expect(cells[3]).toHaveTextContent("Osmanabadi");
      expect(cells[4]).toHaveTextContent("PREGNANCY EARLY");
      expect(cells[5]).toHaveTextContent("ACTIVE");
      expect(cells[6]).toHaveTextContent("14");
      expect(cells[7]).toHaveTextContent("32.5 kg");
    });

    it("links the tag number to the animal profile", async () => {
      await renderLoaded();
      expect(screen.getByRole("link", { name: "G-001" })).toHaveAttribute("href", "/animals/1");
    });

    it("renders em dashes for null name, age and weight", async () => {
      await renderLoaded([SPARSE_ANIMAL]);
      const row = screen.getByText("G-002").closest("tr") as HTMLElement;
      const cells = within(row).getAllByRole("cell");
      expect(cells[1]).toHaveTextContent("—");
      expect(cells[6]).toHaveTextContent("—");
      expect(cells[7]).toHaveTextContent("—");
    });

    it("renders non-ACTIVE statuses with their status text", async () => {
      await renderLoaded([SPARSE_ANIMAL]);
      const row = screen.getByText("G-002").closest("tr") as HTMLElement;
      expect(within(row).getByText("SOLD")).toBeInTheDocument();
    });

    it("renders all rows and the total count", async () => {
      await renderLoaded([ANIMAL, SPARSE_ANIMAL, { ...ANIMAL, id: 3, tag_number: "G-003" }]);
      expect(screen.getByText("3 animal(s)")).toBeInTheDocument();
      expect(screen.getByText("G-003")).toBeInTheDocument();
    });
  });

  describe("states", () => {
    it("shows a loading indicator while the list request is pending", async () => {
      server.use(http.get("/api/animals", () => new Promise<Response>(() => {})));
      renderWithProviders(<AnimalsPage />);
      expect((await screen.findAllByText("Loading…")).length).toBeGreaterThan(0);
    });

    it("shows the server detail on a 400 response", async () => {
      server.use(
        http.get("/api/animals", () =>
          HttpResponse.json({ detail: "Unknown bucket filter" }, { status: 400 }),
        ),
      );
      renderWithProviders(<AnimalsPage />);
      expect(await screen.findByText("Unknown bucket filter")).toBeInTheDocument();
    });

    it("shows the server detail on a 500 response", async () => {
      server.use(
        http.get("/api/animals", () =>
          HttpResponse.json({ detail: "Database unavailable" }, { status: 500 }),
        ),
      );
      renderWithProviders(<AnimalsPage />);
      expect(await screen.findByText("Database unavailable")).toBeInTheDocument();
    });

    it("shows the no-match empty state when the list is empty", async () => {
      await renderLoaded([]);
      expect(await screen.findByText("No animals match these filters.")).toBeInTheDocument();
    });
  });

  describe("filters and search", () => {
    it("sends the initial URL search params as GET query params", async () => {
      nav.searchParams = new URLSearchParams("bucket=RESTING&sex=M&status=SOLD&q=G-77");
      renderWithProviders(<AnimalsPage />);
      await screen.findByText("1 animal(s)");
      const params = seenParams[0];
      expect(params.get("bucket")).toBe("RESTING");
      expect(params.get("sex")).toBe("M");
      expect(params.get("status")).toBe("SOLD");
      expect(params.get("q")).toBe("G-77");
    });

    it("sends no filter params when nothing is selected", async () => {
      await renderLoaded();
      const params = seenParams[0];
      expect(params.get("bucket")).toBeNull();
      expect(params.get("sex")).toBeNull();
      expect(params.get("status")).toBeNull();
      expect(params.get("q")).toBeNull();
    });

    it("sends the trimmed search text as q", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      await user.type(screen.getByPlaceholderText("Search tag or name…"), " G-9 ");
      await waitFor(() => {
        expect(seenParams.at(-1)?.get("q")).toBe("G-9");
      });
    });

    it("drops the q param when the search box is cleared", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      const input = screen.getByPlaceholderText("Search tag or name…");
      await user.type(input, "G-9");
      await waitFor(() => expect(seenParams.at(-1)?.get("q")).toBe("G-9"));
      await user.clear(input);
      await waitFor(() => expect(seenParams.at(-1)?.get("q")).toBeNull());
    });

    it("sends the chosen bucket filter", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      await pickOption(user, screen.getAllByRole("combobox")[0], "FEMALE KIDS");
      await waitFor(() => expect(seenParams.at(-1)?.get("bucket")).toBe("FEMALE_KIDS"));
    });

    it("sends the chosen sex filter", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      await pickOption(user, screen.getAllByRole("combobox")[1], "Male");
      await waitFor(() => expect(seenParams.at(-1)?.get("sex")).toBe("M"));
    });

    it("sends the chosen status filter", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      await pickOption(user, screen.getAllByRole("combobox")[2], "SOLD");
      await waitFor(() => expect(seenParams.at(-1)?.get("status")).toBe("SOLD"));
    });

    it("removes the status param when reset to All statuses", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      await pickOption(user, screen.getAllByRole("combobox")[2], "SOLD");
      await waitFor(() => expect(seenParams.at(-1)?.get("status")).toBe("SOLD"));
      await pickOption(user, screen.getAllByRole("combobox")[2], "All statuses");
      await waitFor(() => expect(seenParams.at(-1)?.get("status")).toBeNull());
    });
  });

  describe("RBAC", () => {
    it("denies access without animals.view and never calls the API", async () => {
      server.use(permissionsHandler(["dashboard.view"]));
      renderWithProviders(<AnimalsPage />);
      expect(
        await screen.findByText(/don't have access to this page/),
      ).toBeInTheDocument();
      expect(seenParams).toHaveLength(0);
    });

    it("hides the Add animal button without animals.create", async () => {
      server.use(permissionsHandler(["animals.view"]));
      renderWithProviders(<AnimalsPage />);
      await screen.findByText("1 animal(s)");
      expect(screen.queryByRole("button", { name: "Add animal" })).not.toBeInTheDocument();
    });

    it("shows the Add animal button with animals.create", async () => {
      server.use(permissionsHandler(["animals.view", "animals.create"]));
      renderWithProviders(<AnimalsPage />);
      await screen.findByText("1 animal(s)");
      expect(screen.getByRole("button", { name: "Add animal" })).toBeInTheDocument();
    });

    it("shows a loading indicator while permissions resolve", async () => {
      server.use(http.get("/api/auth/permissions", () => new Promise<Response>(() => {})));
      renderWithProviders(<AnimalsPage />);
      expect((await screen.findAllByText("Loading…")).length).toBeGreaterThan(0);
    });
  });

  describe("create dialog", () => {
    it("opens with the default breed, sex, source and bucket preselected", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      const dialog = await openCreateDialog(user);
      expect(within(dialog).getByLabelText("Breed")).toHaveValue("Osmanabadi");
      // NOTE: the closed Base UI Select renders the raw value, not the item
      // label (e.g. "F" instead of "Female") — see select-label.bugs.test.tsx.
      // Only the bucket default is asserted here, where value == label.
      const combos = within(dialog).getAllByRole("combobox");
      expect(combos).toHaveLength(4);
      expect(combos[2]).toHaveTextContent("QUARANTINE");
      expect(within(dialog).queryByLabelText(/purchase price/i)).not.toBeInTheDocument();
    });

    it("reveals purchase fields only for source PURCHASED", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      const dialog = await openCreateDialog(user);
      await pickOption(user, within(dialog).getAllByRole("combobox")[1], "Purchased");
      expect(within(dialog).getByLabelText(/purchase date/i)).toBeInTheDocument();
      expect(within(dialog).getByLabelText(/purchase price/i)).toBeInTheDocument();
      expect(within(dialog).getByLabelText(/seller name/i)).toBeInTheDocument();
    });

    it("hides purchase fields again when switching back to Born on farm", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      const dialog = await openCreateDialog(user);
      const sourceCombo = () => within(dialog).getAllByRole("combobox")[1];
      await pickOption(user, sourceCombo(), "Purchased");
      await pickOption(user, sourceCombo(), "Born on farm");
      expect(within(dialog).queryByLabelText(/purchase price/i)).not.toBeInTheDocument();
    });

    it("rejects a tag longer than 50 characters without a POST", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      const dialog = await openCreateDialog(user);
      await user.type(within(dialog).getByLabelText(/tag number/i), "X".repeat(51));
      await user.click(within(dialog).getByRole("button", { name: "Save animal" }));
      expect(
        await within(dialog).findByText(/expected string to have <=50 characters/),
      ).toBeInTheDocument();
      expect(postCalls).toBe(0);
    });

    it("accepts a tag of exactly 50 characters", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      const dialog = await openCreateDialog(user);
      const tag = "T".repeat(50);
      await user.type(within(dialog).getByLabelText(/tag number/i), tag);
      await user.click(within(dialog).getByRole("button", { name: "Save animal" }));
      await waitFor(() => expect(postCalls).toBe(1));
      expect(postBody).toMatchObject({ tag_number: tag });
    });

    it("rejects a negative birth weight without a POST", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      const dialog = await openCreateDialog(user);
      await user.type(within(dialog).getByLabelText(/tag number/i), "G-50");
      setDate(within(dialog).getByLabelText(/birth weight/i), "-0.5");
      await user.click(within(dialog).getByRole("button", { name: "Save animal" }));
      expect(
        await within(dialog).findByText(/expected number to be >=0/),
      ).toBeInTheDocument();
      expect(postCalls).toBe(0);
    });

    it("rejects a zero entry weight (must be positive) without a POST", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      const dialog = await openCreateDialog(user);
      await user.type(within(dialog).getByLabelText(/tag number/i), "G-51");
      setDate(within(dialog).getByLabelText(/entry weight/i), "0");
      await user.click(within(dialog).getByRole("button", { name: "Save animal" }));
      await waitFor(() => expect(postCalls).toBe(0));
      expect(
        await within(dialog).findByText(/expected number to be >0/),
      ).toBeInTheDocument();
    });

    it("accepts the smallest positive entry weight (0.01 kg)", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      const dialog = await openCreateDialog(user);
      await user.type(within(dialog).getByLabelText(/tag number/i), "G-52");
      setDate(within(dialog).getByLabelText(/entry weight/i), "0.01");
      await user.click(within(dialog).getByRole("button", { name: "Save animal" }));
      await waitFor(() => expect(postCalls).toBe(1));
      expect(postBody).toMatchObject({ weight_kg: 0.01 });
    });

    it("submits sex M when Male is selected", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      const dialog = await openCreateDialog(user);
      await user.type(within(dialog).getByLabelText(/tag number/i), "G-60");
      await pickOption(user, within(dialog).getAllByRole("combobox")[0], "Male");
      await user.click(within(dialog).getByRole("button", { name: "Save animal" }));
      await waitFor(() => expect(postCalls).toBe(1));
      expect(postBody).toMatchObject({ sex: "M" });
    });

    it("submits purchase fields for a PURCHASED animal", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      const dialog = await openCreateDialog(user);
      await user.type(within(dialog).getByLabelText(/tag number/i), "G-70");
      await pickOption(user, within(dialog).getAllByRole("combobox")[1], "Purchased");
      setDate(within(dialog).getByLabelText(/purchase date/i), "2026-01-15");
      setDate(within(dialog).getByLabelText(/purchase price/i), "12500");
      await user.type(within(dialog).getByLabelText(/seller name/i), "Raju Pawar");
      await user.click(within(dialog).getByRole("button", { name: "Save animal" }));
      await waitFor(() => expect(postCalls).toBe(1));
      expect(postBody).toMatchObject({
        source: "PURCHASED",
        purchase_date: "2026-01-15",
        purchase_price: 12500,
        seller_name: "Raju Pawar",
      });
    });

    it("submits all optional fields when provided", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      const dialog = await openCreateDialog(user);
      await user.type(within(dialog).getByLabelText(/tag number/i), "G-80");
      await user.type(within(dialog).getByLabelText("Name"), "Gauri");
      setDate(within(dialog).getByLabelText(/date of birth/i), "2025-12-01");
      setDate(within(dialog).getByLabelText(/estimated dob/i), "2025-12-02");
      await pickOption(user, within(dialog).getAllByRole("combobox")[3], "TWIN");
      setDate(within(dialog).getByLabelText(/birth weight/i), "2.5");
      await user.type(within(dialog).getByLabelText(/notes/i), "Healthy twin");
      await user.click(within(dialog).getByRole("button", { name: "Save animal" }));
      await waitFor(() => expect(postCalls).toBe(1));
      expect(postBody).toMatchObject({
        name: "Gauri",
        date_of_birth: "2025-12-01",
        estimated_dob: "2025-12-02",
        birth_type: "TWIN",
        birth_weight: 2.5,
        notes: "Healthy twin",
      });
    });

    it("falls back to the Osmanabadi default when the breed is cleared", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      const dialog = await openCreateDialog(user);
      await user.type(within(dialog).getByLabelText(/tag number/i), "G-90");
      await user.clear(within(dialog).getByLabelText("Breed"));
      await user.click(within(dialog).getByRole("button", { name: "Save animal" }));
      await waitFor(() => expect(postCalls).toBe(1));
      expect(postBody).toMatchObject({ breed: "Osmanabadi" });
    });

    it("shows the server detail as a toast and keeps the dialog open on 409", async () => {
      server.use(
        http.post("/api/animals", () => {
          postCalls += 1;
          return HttpResponse.json({ detail: "Tag number already exists" }, { status: 409 });
        }),
      );
      const user = userEvent.setup();
      await renderLoaded();
      const dialog = await openCreateDialog(user);
      await user.type(within(dialog).getByLabelText(/tag number/i), "G-001");
      await user.click(within(dialog).getByRole("button", { name: "Save animal" }));
      await waitFor(() =>
        expect(toastMock.error).toHaveBeenCalledWith("Tag number already exists"),
      );
      expect(postCalls).toBe(1);
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    it("shows a generic toast on a network failure and keeps the dialog open", async () => {
      server.use(
        http.post("/api/animals", () => {
          postCalls += 1;
          return HttpResponse.error();
        }),
      );
      const user = userEvent.setup();
      await renderLoaded();
      const dialog = await openCreateDialog(user);
      await user.type(within(dialog).getByLabelText(/tag number/i), "G-91");
      await user.click(within(dialog).getByRole("button", { name: "Save animal" }));
      await waitFor(() =>
        expect(toastMock.error).toHaveBeenCalledWith("Something went wrong"),
      );
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    it("toasts success and closes the dialog after a successful create", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      const dialog = await openCreateDialog(user);
      await user.type(within(dialog).getByLabelText(/tag number/i), "G-95");
      await user.click(within(dialog).getByRole("button", { name: "Save animal" }));
      await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith("Animal added."));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });
  });
});
