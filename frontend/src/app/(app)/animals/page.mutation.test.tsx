/**
 * AnimalsPage mutation-hardening — pins behaviour the other suites leave
 * open: the strict page-param grammar and enum validation of URL filters,
 * payload trimming of padded free-text, cleared optionals meaning "absent",
 * the create dialog's aria error contract, the in-flight Cancel lock,
 * client-side sorting with aria-sort, mobile card and desktop dash
 * fallbacks, per-filter empty states, the empty-herd CTA contract, error /
 * permissions retry wiring, and the full historical-import sentence.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import AnimalsPage from "./page";

const nav = vi.hoisted(() => {
  const state = {
    search: "",
    deferReplace: false,
    deferredReplacements: [] as string[],
  };
  const applyUrl = (url: string) => {
    state.search = url.includes("?") ? url.slice(url.indexOf("?") + 1) : "";
  };
  const push = vi.fn(applyUrl);
  const replace = vi.fn((url: string) => {
    if (state.deferReplace) state.deferredReplacements.push(url);
    else applyUrl(url);
  });
  return { state, push, replace, router: { push, replace, prefetch: vi.fn() } };
});

vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  usePathname: () => "/animals",
  useSearchParams: () => new URLSearchParams(nav.state.search),
  useParams: () => ({}),
}));

beforeAll(() => {
  // jsdom lacks the pointer-capture / observer APIs Base UI Select relies on.
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  Element.prototype.scrollIntoView = () => {};
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (window as unknown as { ResizeObserver: typeof ResizeObserverStub }).ResizeObserver =
    ResizeObserverStub;
});

const animal = (overrides: Record<string, unknown> = {}) => ({
  id: 1,
  tag_number: "G-001",
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
  notes: null,
  created_at: "2026-01-01T05:30:00Z",
  age_months: 14,
  latest_weight_kg: 32.5,
  ...overrides,
});

const HERD = [
  animal({ id: 3, tag_number: "G-003", age_months: 9, latest_weight_kg: 10 }),
  animal({ id: 1 }),
  animal({ id: 2, tag_number: "G-002", age_months: 30, latest_weight_kg: 20 }),
];

const SPARSE = animal({
  id: 4,
  tag_number: "G-004",
  name: null,
  sex: "M",
  current_bucket: "FEMALE_KIDS",
  age_months: null,
  latest_weight_kg: null,
});

type User = ReturnType<typeof userEvent.setup>;

describe("AnimalsPage mutation hardening", () => {
  let seenParams: URLSearchParams[];
  let postCalls: number;
  let postBody: Record<string, unknown> | null;

  beforeEach(() => {
    nav.state.search = "";
    nav.state.deferReplace = false;
    nav.state.deferredReplacements = [];
    nav.push.mockClear();
    nav.replace.mockClear();
    seenParams = [];
    postCalls = 0;
    postBody = null;
    server.use(
      http.get("/api/animals", ({ request }) => {
        seenParams.push(new URL(request.url).searchParams);
        const offset = Number(new URL(request.url).searchParams.get("offset"));
        const limit = Number(new URL(request.url).searchParams.get("limit"));
        return HttpResponse.json({
          animals: HERD.slice(offset, offset + limit),
          total: HERD.length,
        });
      }),
      http.post("/api/animals", async ({ request }) => {
        postCalls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...animal(), id: 99 }, { status: 201 });
      }),
    );
  });

  async function openCreateDialog(user: User) {
    await user.click(screen.getByRole("button", { name: "Add animal" }));
    return await screen.findByRole("dialog");
  }

  /** Dialog switched to the owner-only historical import, audit reason filled. */
  async function openImportDialog(user: User, reason = "Paper herd register import") {
    const dialog = await openCreateDialog(user);
    await pickOption(user, within(dialog).getByLabelText("Source *"), "Historical born-on-farm import");
    await user.type(within(dialog).getByLabelText("Historical import reason *"), reason);
    return dialog;
  }

  async function pickOption(user: User, trigger: HTMLElement, name: string) {
    await user.click(trigger);
    await user.click(await screen.findByRole("option", { name }));
  }

  describe("URL page-param grammar", () => {
    it("rejects an exponent-style page param instead of coercing it deep into the register", async () => {
      nav.state.search = "page=1e2";
      renderWithProviders(<AnimalsPage />);

      expect(await screen.findByText("3 animal(s)")).toBeInTheDocument();
      expect(seenParams[0].get("offset")).toBe("0");
    });

    it("treats page=0 as page one, never a negative offset", async () => {
      nav.state.search = "page=0";
      renderWithProviders(<AnimalsPage />);

      expect(await screen.findByText("3 animal(s)")).toBeInTheDocument();
      expect(seenParams[0].get("offset")).toBe("0");
    });

    it("honours a plain page deep link in the request offset", async () => {
      // A padded herd so page 3 exists without any recovery navigation.
      const many = Array.from({ length: 120 }, (_, index) =>
        animal({ id: index + 1, tag_number: `G-${String(index + 1).padStart(3, "0")}` }),
      );
      server.use(
        http.get("/api/animals", ({ request }) => {
          const params = new URL(request.url).searchParams;
          seenParams.push(params);
          const offset = Number(params.get("offset"));
          const limit = Number(params.get("limit"));
          return HttpResponse.json({
            animals: many.slice(offset, offset + limit),
            total: many.length,
          });
        }),
      );
      nav.state.search = "page=3";
      renderWithProviders(<AnimalsPage />);

      expect(await screen.findByText("Showing 101–120 of 120 animals")).toBeInTheDocument();
      expect(seenParams[0].get("offset")).toBe("100");
    });
  });

  describe("URL filter validation", () => {
    it("falls back to the unfiltered list for enum values a hostile link invents", async () => {
      nav.state.search = "bucket=GIBBERISH&sex=X&status=NOPE";
      renderWithProviders(<AnimalsPage />);

      expect(await screen.findByText("3 animal(s)")).toBeInTheDocument();
      expect(seenParams[0].get("bucket")).toBeNull();
      expect(seenParams[0].get("sex")).toBeNull();
      expect(seenParams[0].get("status")).toBeNull();
      // Unfiltered still means the explicit include-all-statuses contract.
      expect(seenParams[0].get("include_all_statuses")).toBe("true");
    });

    it("queries a padded search deep link by its trimmed term", async () => {
      nav.state.search = "q=%20%20G-1%20%20";
      renderWithProviders(<AnimalsPage />);

      expect(await screen.findByText("3 animal(s)")).toBeInTheDocument();
      expect(seenParams[0].get("q")).toBe("G-1");
    });
  });

  describe("create dialog payload mapping", () => {
    it("trims every padded free-text field before it reaches the API", async () => {
      const user = userEvent.setup();
      renderWithProviders(<AnimalsPage />);
      await screen.findByText("3 animal(s)");
      const dialog = await openCreateDialog(user);

      await user.type(within(dialog).getByLabelText("Tag number"), "  G-9  ");
      await user.type(within(dialog).getByLabelText("Name"), "  Lakshmi  ");
      await user.type(within(dialog).getByLabelText("Breed"), "  Osmanabadi  ");
      await user.type(within(dialog).getByLabelText("Seller name"), "  Raju Pawar  ");
      await user.type(within(dialog).getByLabelText("Notes"), "  Good milker  ");
      await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

      await waitFor(() => expect(postCalls).toBe(1));
      expect(postBody).toMatchObject({
        tag_number: "G-9",
        name: "Lakshmi",
        breed: "Osmanabadi",
        seller_name: "Raju Pawar",
        notes: "Good milker",
      });
    });

    it("treats a birth weight that was typed and then cleared as absent", async () => {
      const user = userEvent.setup();
      renderWithProviders(<AnimalsPage />);
      await screen.findByText("3 animal(s)");
      const dialog = await openImportDialog(user);

      const birthWeight = within(dialog).getByLabelText("Birth weight (kg)");
      fireEvent.change(birthWeight, { target: { value: "2.5" } });
      fireEvent.change(birthWeight, { target: { value: "" } });
      await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

      await waitFor(() => expect(postCalls).toBe(1));
      expect(postBody).toMatchObject({ birth_weight: null });
    });
  });

  describe("create dialog aria error contract", () => {
    it("keeps every optional field free of invalid state before a submit", async () => {
      const user = userEvent.setup();
      renderWithProviders(<AnimalsPage />);
      await screen.findByText("3 animal(s)");
      const dialog = await openCreateDialog(user);

      for (const label of ["Tag number", "Name", "Breed", "Entry weight (kg)", "Seller name"]) {
        const input = within(dialog).getByLabelText(label);
        expect(input).not.toHaveAttribute("aria-invalid");
        expect(input).not.toHaveAttribute("aria-describedby");
      }
      expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    });

    it("flags an overlong tag invalid and links its message", async () => {
      const user = userEvent.setup();
      renderWithProviders(<AnimalsPage />);
      await screen.findByText("3 animal(s)");
      const dialog = await openCreateDialog(user);
      const tag = within(dialog).getByLabelText("Tag number");
      fireEvent.change(tag, { target: { value: "X".repeat(51) } });
      await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

      const message = await within(dialog).findByText(/expected string to have <=50 characters/);
      expect(tag).toHaveAttribute("aria-invalid", "true");
      expect(tag).toHaveAttribute("aria-describedby", "create-tag-error");
      expect(message).toHaveAttribute("id", "create-tag-error");
      expect(postCalls).toBe(0);
    });

    it("flags an overlong name invalid and links its message", async () => {
      const user = userEvent.setup();
      renderWithProviders(<AnimalsPage />);
      await screen.findByText("3 animal(s)");
      const dialog = await openCreateDialog(user);
      const name = within(dialog).getByLabelText("Name");
      fireEvent.change(name, { target: { value: "N".repeat(81) } });
      await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

      await waitFor(() => expect(within(dialog).getByRole("alert")).toBeInTheDocument());
      expect(name).toHaveAttribute("aria-invalid", "true");
      expect(name).toHaveAttribute("aria-describedby", "create-name-error");
      expect(postCalls).toBe(0);
    });

    it("flags an overlong breed invalid and links its message", async () => {
      const user = userEvent.setup();
      renderWithProviders(<AnimalsPage />);
      await screen.findByText("3 animal(s)");
      const dialog = await openCreateDialog(user);
      const breed = within(dialog).getByLabelText("Breed");
      fireEvent.change(breed, { target: { value: "B".repeat(61) } });
      await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

      const message = await within(dialog).findByText(/expected string to have <=60 characters/);
      expect(breed).toHaveAttribute("aria-invalid", "true");
      expect(breed).toHaveAttribute("aria-describedby", "create-breed-error");
      expect(message).toHaveAttribute("id", "create-breed-error");
      expect(postCalls).toBe(0);
    });

    it("flags a zero entry weight invalid and links its message", async () => {
      const user = userEvent.setup();
      renderWithProviders(<AnimalsPage />);
      await screen.findByText("3 animal(s)");
      const dialog = await openCreateDialog(user);
      const weight = within(dialog).getByLabelText("Entry weight (kg)");
      fireEvent.change(weight, { target: { value: "0" } });
      await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

      await waitFor(() => expect(within(dialog).getByRole("alert")).toBeInTheDocument());
      expect(weight).toHaveAttribute("aria-invalid", "true");
      expect(weight).toHaveAttribute("aria-describedby", "create-weight-error");
      expect(postCalls).toBe(0);
    });

    it("flags an overlong seller name invalid and links its message", async () => {
      const user = userEvent.setup();
      renderWithProviders(<AnimalsPage />);
      await screen.findByText("3 animal(s)");
      const dialog = await openCreateDialog(user);
      const seller = within(dialog).getByLabelText("Seller name");
      fireEvent.change(seller, { target: { value: "S".repeat(121) } });
      await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

      await waitFor(() => expect(within(dialog).getByRole("alert")).toBeInTheDocument());
      expect(seller).toHaveAttribute("aria-invalid", "true");
      expect(seller).toHaveAttribute("aria-describedby", "create-seller-error");
      expect(postCalls).toBe(0);
    });
  });

  describe("create dialog in-flight lock", () => {
    it("locks Cancel while the create request is on the wire and releases it after", async () => {
      let release: (() => void) | undefined;
      const parked = new Promise<void>((resolve) => {
        release = resolve;
      });
      server.use(
        http.post("/api/animals", async () => {
          postCalls += 1;
          await parked;
          return HttpResponse.json(animal({ id: 99 }), { status: 201 });
        }),
      );
      const user = userEvent.setup();
      renderWithProviders(<AnimalsPage />);
      await screen.findByText("3 animal(s)");
      const dialog = await openCreateDialog(user);
      const cancel = within(dialog).getByRole("button", { name: "Cancel" });
      expect(cancel).toBeEnabled();

      await user.click(within(dialog).getByRole("button", { name: "Save animal" }));
      expect(await within(dialog).findByRole("button", { name: "Saving…" })).toBeDisabled();
      expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();

      release?.();
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });
  });

  describe("historical import copy", () => {
    it("renders the complete historical-import sentence", async () => {
      const user = userEvent.setup();
      renderWithProviders(<AnimalsPage />);
      await screen.findByText("3 animal(s)");
      const dialog = await openImportDialog(user);

      expect(within(dialog).getByText(/Historical import only\./).textContent).toBe(
        "Historical import only. Normal births must be recorded through the kidding register so the dam, sire and kidding record remain linked.",
      );
    });
  });

  describe("client-side sorting", () => {
    function tableHead(index: number) {
      return screen.getByRole("table").querySelectorAll("th")[index] as HTMLElement;
    }

    function tagColumnOrder() {
      return Array.from(screen.getByRole("table").querySelectorAll("tbody tr")).map(
        (row) => row.querySelector("td")?.textContent,
      );
    }

    it("sorts by tag ascending, then descending, then back to the server order", async () => {
      const user = userEvent.setup();
      renderWithProviders(<AnimalsPage />);
      await screen.findByText("3 animal(s)");
      expect(tagColumnOrder()).toEqual(["G-003", "G-001", "G-002"]);
      expect(tableHead(0)).toHaveAttribute("aria-sort", "none");

      await user.click(within(tableHead(0)).getByRole("button"));
      expect(tagColumnOrder()).toEqual(["G-001", "G-002", "G-003"]);
      expect(tableHead(0)).toHaveAttribute("aria-sort", "ascending");

      await user.click(within(tableHead(0)).getByRole("button"));
      expect(tagColumnOrder()).toEqual(["G-003", "G-002", "G-001"]);
      expect(tableHead(0)).toHaveAttribute("aria-sort", "descending");

      await user.click(within(tableHead(0)).getByRole("button"));
      expect(tagColumnOrder()).toEqual(["G-003", "G-001", "G-002"]);
      expect(tableHead(0)).toHaveAttribute("aria-sort", "none");
    });

    it("sorts by age and weight and marks only the active column", async () => {
      const user = userEvent.setup();
      renderWithProviders(<AnimalsPage />);
      await screen.findByText("3 animal(s)");

      await user.click(within(tableHead(6)).getByRole("button"));
      expect(
        Array.from(screen.getByRole("table").querySelectorAll("tbody tr")).map(
          (row) => row.querySelectorAll("td")[6]?.textContent,
        ),
      ).toEqual(["9", "14", "30"]);
      expect(tableHead(6)).toHaveAttribute("aria-sort", "ascending");
      expect(tableHead(0)).toHaveAttribute("aria-sort", "none");
      expect(tableHead(7)).toHaveAttribute("aria-sort", "none");

      await user.click(within(tableHead(7)).getByRole("button"));
      expect(
        Array.from(screen.getByRole("table").querySelectorAll("tbody tr")).map(
          (row) => row.querySelectorAll("td")[7]?.textContent,
        ),
      ).toEqual(["10.0 kg", "20.0 kg", "32.5 kg"]);
      expect(tableHead(7)).toHaveAttribute("aria-sort", "ascending");
      expect(tableHead(6)).toHaveAttribute("aria-sort", "none");
    });

    it("announces the within-page sort in the card description", async () => {
      const user = userEvent.setup();
      renderWithProviders(<AnimalsPage />);
      await screen.findByText("3 animal(s)");
      expect(
        screen.queryByText("3 animal(s) · sorted within the current page"),
      ).not.toBeInTheDocument();

      await user.click(within(tableHead(0)).getByRole("button"));
      expect(
        await screen.findByText("3 animal(s) · sorted within the current page"),
      ).toBeInTheDocument();
    });

    it("sorts an unaged animal before an aged one on age", async () => {
      const pair = [
        animal({ id: 5, tag_number: "G-00A", age_months: 5, latest_weight_kg: 5 }),
        animal({ id: 6, tag_number: "G-00B", age_months: null, latest_weight_kg: null }),
      ];
      server.use(
        http.get("/api/animals", () => HttpResponse.json({ animals: pair, total: 2 })),
      );
      const user = userEvent.setup();
      renderWithProviders(<AnimalsPage />);
      await screen.findByText("2 animal(s)");

      await user.click(within(tableHead(6)).getByRole("button"));
      expect(tagColumnOrder()).toEqual(["G-00B", "G-00A"]);
    });
  });

  describe("row rendering fallbacks", () => {
    it("spells out the full mobile card: name, age, herd line and weight", async () => {
      server.use(
        http.get("/api/animals", () =>
          HttpResponse.json({ animals: [animal()], total: 1 }),
        ),
      );
      const view = renderWithProviders(<AnimalsPage />);
      await screen.findByText("1 animal(s)");
      const mobile = view.container.querySelector("div.md\\:hidden") as HTMLElement;
      const card = mobile.querySelector("a") as HTMLElement;
      expect(card.getAttribute("href")).toBe("/animals/1");
      expect(card.textContent).toContain("Lakshmi");
      expect(card.textContent).toContain("14 mo · ");
      expect(card.textContent).toContain("Female · Lactating · Osmanabadi");
      // The age/weight line is exactly those two facts.
      expect(card.querySelectorAll("p")[card.querySelectorAll("p").length - 1].textContent).toBe(
        "14 mo · 32.5 kg",
      );
    });

    it("keeps the mobile card truthful for a sparse animal", async () => {
      server.use(
        http.get("/api/animals", () =>
          HttpResponse.json({ animals: [SPARSE], total: 1 }),
        ),
      );
      const view = renderWithProviders(<AnimalsPage />);
      await screen.findByText("1 animal(s)");
      const mobile = view.container.querySelector("div.md\\:hidden") as HTMLElement;
      const card = mobile.querySelector("a") as HTMLElement;
      // The two-word bucket label must stay lowercase ("Female kids", not the
      // naive title-case "Female Kids").
      expect(card.textContent).toContain("Male · Female kids · Osmanabadi");
      const paragraphs = card.querySelectorAll("p");
      // Only the two detail lines — no name paragraph for an unnamed animal.
      expect(paragraphs).toHaveLength(2);
      // No age prefix, and the weight line says exactly why.
      expect(paragraphs[1].textContent).toBe("weight not recorded");
      expect(card.textContent).not.toContain("kg");
    });

    it("renders em dashes in the desktop table for null name, age and weight", async () => {
      server.use(
        http.get("/api/animals", () =>
          HttpResponse.json({ animals: [SPARSE], total: 1 }),
        ),
      );
      renderWithProviders(<AnimalsPage />);
      await screen.findByText("1 animal(s)");
      const row = within(screen.getByRole("table")).getByText("G-004").closest("tr") as HTMLElement;
      const cells = within(row).getAllByRole("cell");
      expect(cells[1]).toHaveTextContent("—");
      expect(cells[6]).toHaveTextContent("—");
      expect(cells[7]).toHaveTextContent("—");
    });
  });

  describe("filtered empty states", () => {
    it.each([
      ["bucket=RESTING", "bucket"],
      ["sex=M", "sex"],
      ["status=SOLD", "status"],
      ["q=ZZZ", "q"],
    ])("offers to clear filters when only %s filters the herd to nothing", async (search) => {
      server.use(
        http.get("/api/animals", () => HttpResponse.json({ animals: [], total: 0 })),
      );
      nav.state.search = search;
      renderWithProviders(<AnimalsPage />);

      expect(await screen.findByText("No animals match these filters.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Clear filters" })).toBeInTheDocument();
    });

    it("offers the herd-register CTA when the empty herd is unfiltered", async () => {
      server.use(
        http.get("/api/animals", () => HttpResponse.json({ animals: [], total: 0 })),
      );
      renderWithProviders(<AnimalsPage />);

      expect(await screen.findByText("No animals yet")).toBeInTheDocument();
      expect(screen.queryByText("No animals match these filters.")).not.toBeInTheDocument();
      const cta = screen.getByRole("link", { name: "Add animal" });
      expect(cta).toHaveAttribute("href", "/animals/new");
      // A small primary button — not the outline or ghost variant.
      expect(cta.className).toContain("bg-primary");
      expect(cta.className).toContain("text-[0.8rem]");
    });
  });

  describe("search navigation fence", () => {
    it("keeps cached rows fenced from the debounce dispatch until the URL commits", async () => {
      const queryClient = createTestQueryClient();
      queryClient.setDefaultOptions({
        queries: { retry: false, refetchOnWindowFocus: false, staleTime: 60_000 },
        mutations: { retry: false },
      });
      const view = renderWithProviders(<AnimalsPage />, queryClient);
      await screen.findByText("3 animal(s)");
      const search = screen.getByRole("searchbox", { name: "Search animals by tag" });

      // Warm the q=G-9 destination and return to the unfiltered URL.
      fireEvent.change(search, { target: { value: "G-9" } });
      await waitFor(() => expect(nav.state.search).toBe("q=G-9"));
      fireEvent.change(search, { target: { value: "" } });
      await waitFor(() => expect(nav.state.search).toBe(""));
      await screen.findByText("3 animal(s)");

      // Re-dispatch the cached destination, but hold its URL commit back.
      nav.state.deferReplace = true;
      nav.state.deferredReplacements = [];
      fireEvent.change(search, { target: { value: "G-9" } });
      await new Promise((resolve) => window.setTimeout(resolve, 400));

      // The debounced replacement is on the wire: the term matches its URL,
      // so only the navigation fence can keep the (cached) rows stood down.
      expect(screen.getByText("Loading animals…")).toBeInTheDocument();
      expect(screen.queryByText("G-001")).not.toBeInTheDocument();

      // Committing the URL releases the fence and restores the rows.
      const deferred = nav.state.deferredReplacements[0];
      nav.state.search = deferred.includes("?") ? deferred.slice(deferred.indexOf("?") + 1) : "";
      nav.state.deferReplace = false;
      view.rerender(<AnimalsPage />);
      expect((await screen.findAllByText("G-001"))[0]).toBeInTheDocument();
      expect(screen.queryByText("Loading animals…")).not.toBeInTheDocument();
    });
  });

  describe("error recovery", () => {
    it("reloads the list through the Retry animals button", async () => {
      let attempts = 0;
      server.use(
        http.get("/api/animals", () => {
          attempts += 1;
          seenParams.push(new URLSearchParams());
          if (attempts === 1) {
            return HttpResponse.json({ detail: "Herd service down" }, { status: 500 });
          }
          return HttpResponse.json({ animals: HERD, total: HERD.length });
        }),
      );
      renderWithProviders(<AnimalsPage />);

      expect(await screen.findByText("Herd service down")).toBeInTheDocument();
      // No filters are active, so the error must not offer to clear any.
      expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
      await userEvent.setup().click(screen.getByRole("button", { name: "Retry animals" }));

      expect(await screen.findByText("3 animal(s)")).toBeInTheDocument();
      expect(attempts).toBe(2);
    });

    it("recovers through the in-place permissions retry", async () => {
      let permCalls = 0;
      server.use(
        http.get("/api/auth/permissions", () => {
          permCalls += 1;
          if (permCalls === 1) {
            return HttpResponse.json({ detail: "permissions unavailable" }, { status: 503 });
          }
          return HttpResponse.json({ is_owner: true, permissions: ["animals.view"] });
        }),
      );
      renderWithProviders(<AnimalsPage />);

      expect(await screen.findByText(/Could not load your permissions/)).toBeInTheDocument();
      await userEvent.setup().click(screen.getByRole("button", { name: "Retry permissions" }));

      await waitFor(() => expect(permCalls).toBe(2));
      expect(await screen.findByText("3 animal(s)")).toBeInTheDocument();
    });
  });
});
