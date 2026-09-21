/**
 * AnimalsPage — the guards the other animals suites leave open: the submit
 * button's own locked state during validation, the notes aria-invalid
 * contract, the filters carried by the very first list request of a
 * client-side navigation, the interaction fence raised by typing (and the
 * pagination controls it must take with it), the registry of dispatched-but-
 * uncommitted list URLs, repeat ?new=1 stripping, and the page-ceiling edge
 * where the URL already names the destination page.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuth } from "@/lib/auth-context";
import { usePermissions } from "@/lib/use-permissions";
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
  // jsdom lacks the pointer-capture / observer APIs the Select relies on.
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

const animal = (id: number) => ({
  id,
  tag_number: `G-${String(id).padStart(3, "0")}`,
  name: `Goat ${id}`,
  breed: "Osmanabadi",
  sex: id % 2 ? "F" : "M",
  date_of_birth: "2025-05-10",
  estimated_dob: null,
  birth_type: "SINGLE",
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
  notes: null,
  created_at: "2026-01-01T05:30:00Z",
  age_months: 14,
  latest_weight_kg: 32.5,
});

type User = ReturnType<typeof userEvent.setup>;

let seenParams: URLSearchParams[];
let postCalls: number;

function serveAnimals(animals: ReturnType<typeof animal>[] = [animal(1)]) {
  server.use(
    http.get("/api/animals", ({ request }) => {
      const params = new URL(request.url).searchParams;
      seenParams.push(params);
      const offset = Number(params.get("offset"));
      const limit = Number(params.get("limit"));
      return HttpResponse.json({
        animals: animals.slice(offset, offset + limit),
        total: animals.length,
      });
    }),
    http.post("/api/animals", () => {
      postCalls += 1;
      return HttpResponse.json({ ...animal(99), id: 99 }, { status: 201 });
    }),
  );
}

async function pickOption(user: User, trigger: HTMLElement, name: string) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

async function openCreateDialog(user: User) {
  await user.click(screen.getByRole("button", { name: "Add animal" }));
  return await screen.findByRole("dialog");
}

function searchBox(): HTMLElement {
  return screen.getByRole("searchbox", { name: "Search animals by tag" });
}

/** Mounts the list only once the session and its permissions have resolved —
 *  the state a client-side navigation into /animals starts from, where the
 *  list query is already enabled on the page's very first render. */
function AfterSessionBootstrap({ children }: { children: ReactNode }) {
  const { farmId } = useAuth();
  const { loading, can } = usePermissions();
  if (farmId === null || loading || !can("animals.view")) return null;
  return <>{children}</>;
}

beforeEach(() => {
  nav.state.search = "";
  nav.state.deferReplace = false;
  nav.state.deferredReplacements = [];
  nav.push.mockClear();
  nav.replace.mockClear();
  seenParams = [];
  postCalls = 0;
  serveAnimals();
});

describe("AnimalsPage create dialog submit state", () => {
  it("locks its own submit button while the form is still validating", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");
    const dialog = await openCreateDialog(user);

    fireEvent.submit(dialog.querySelector("form") as HTMLFormElement);

    // Validation is async, so the request has not started yet. The button
    // must already announce and enforce the submit — the enclosing fieldset
    // would otherwise be the only thing stopping a second dispatch.
    const submit = within(dialog).getByRole("button", { name: "Saving…" });
    expect(submit).toHaveAttribute("disabled");
    expect(
      within(dialog).queryByRole("button", { name: "Save animal" }),
    ).not.toBeInTheDocument();

    await waitFor(() => expect(postCalls).toBe(1));
  });

  it("flags the notes box invalid only once it exceeds the 4000-character cap", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");
    const dialog = await openCreateDialog(user);
    const notes = within(dialog).getByLabelText("Notes");
    // An untouched field is not "valid" or "invalid" — assistive tech should
    // hear nothing at all until the operator has actually broken the rule.
    expect(notes).not.toHaveAttribute("aria-invalid");

    fireEvent.change(notes, { target: { value: "n".repeat(4001) } });
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    expect(await within(dialog).findByText("Max 4000 characters")).toBeInTheDocument();
    expect(notes).toHaveAttribute("aria-invalid", "true");
    expect(postCalls).toBe(0);
  });
});

describe("AnimalsPage first list request", () => {
  it("applies a deep link's filters to its very first request", async () => {
    nav.state.search = "bucket=RESTING&sex=M&status=SOLD";
    renderWithProviders(
      <AfterSessionBootstrap>
        <AnimalsPage />
      </AfterSessionBootstrap>,
    );

    expect(await screen.findByText("1 animal(s)")).toBeInTheDocument();
    // Arriving with permissions already cached (any in-app link into
    // /animals) enables the list query on the first render, so the initial
    // request has to be the filtered one — an unfiltered round trip would
    // briefly show rows the URL excludes.
    expect(seenParams).toHaveLength(1);
    expect(seenParams[0].get("bucket")).toBe("RESTING");
    expect(seenParams[0].get("sex")).toBe("M");
    expect(seenParams[0].get("status")).toBe("SOLD");
    // A chosen status is authoritative: asking for every status alongside it
    // would re-admit the rows the filter exists to hide.
    expect(seenParams[0].get("include_all_statuses")).toBeNull();
  });
});

describe("AnimalsPage interaction fence", () => {
  it("stands the rows and pagination down the moment a tag search is typed", async () => {
    serveAnimals(Array.from({ length: 55 }, (_, index) => animal(index + 1)));
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("Showing 1–50 of 55 animals");

    fireEvent.change(searchBox(), { target: { value: "G-0" } });

    // The debounce has not fired yet, so every row on screen describes a
    // different query than the one the operator is typing. Neither the rows
    // nor the page controls may act on the stale result set. The pending
    // search folds into dataLoading, so the polite label reads "Loading".
    expect(screen.getByText("Loading animals…")).toBeInTheDocument();
    expect(screen.queryByText("G-001")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("navigation", { name: "animals pagination" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();

    await waitFor(() => expect(seenParams.at(-1)?.get("q")).toBe("G-0"));
  });

  it("normalises the search box to the term its own URL commit carries", async () => {
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");

    fireEvent.change(searchBox(), { target: { value: "G-001 " } });

    await waitFor(() => expect(seenParams.at(-1)?.get("q")).toBe("G-001"));
    expect(nav.state.search).toBe("q=G-001");
    // The committed URL is the source of truth once it lands: the box shows
    // the term that was actually searched, not the padded draft.
    await waitFor(() => expect(searchBox()).toHaveValue("G-001"));
  });

  it("rehydrates the filters and the search box from a browser navigation", async () => {
    const view = renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");

    nav.state.search = "bucket=RESTING&sex=F&status=ACTIVE&q=G-002";
    view.rerender(<AnimalsPage />);

    await waitFor(() => expect(seenParams.at(-1)?.get("q")).toBe("G-002"));
    expect(searchBox()).toHaveValue("G-002");
    expect(screen.getByLabelText("Filter animals by bucket")).toHaveTextContent("Resting");
    expect(screen.getByLabelText("Filter animals by sex")).toHaveTextContent("Female");
    expect(screen.getByLabelText("Filter animals by status")).toHaveTextContent("Active");
  });

  it("drops navigations the browser overtook instead of fencing the list forever", async () => {
    const view = renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");

    nav.state.deferReplace = true;
    fireEvent.change(searchBox(), { target: { value: "G-9" } });
    await waitFor(() => expect(nav.state.deferredReplacements).toEqual(["/animals?q=G-9"]));

    // The operator navigates back before Next commits that search URL. The
    // URL that wins describes a different list, so the dispatched one is dead
    // and must not keep the rows behind the fence.
    nav.state.search = "bucket=RESTING";
    view.rerender(<AnimalsPage />);

    expect((await screen.findAllByText("G-001"))[0]).toBeInTheDocument();
    expect(screen.queryByText("Loading animals…")).not.toBeInTheDocument();
    expect(screen.queryByText("Updating animals…")).not.toBeInTheDocument();
    expect(searchBox()).toHaveValue("");
  });

  it("puts cached rows back on screen the instant a repeated filter URL commits", async () => {
    const user = userEvent.setup();
    const queryClient = createTestQueryClient();
    queryClient.setDefaultOptions({
      queries: { retry: false, refetchOnWindowFocus: false, staleTime: 60_000 },
      mutations: { retry: false },
    });
    renderWithProviders(<AnimalsPage />, queryClient);
    await screen.findByText("1 animal(s)");
    const bucketFilter = () => screen.getByLabelText("Filter animals by bucket");

    await pickOption(user, bucketFilter(), "Female kids");
    await waitFor(() => expect(seenParams.at(-1)?.get("bucket")).toBe("FEMALE_KIDS"));
    await pickOption(user, bucketFilter(), "All buckets");
    await waitFor(() => expect(nav.state.search).toBe(""));

    await pickOption(user, bucketFilter(), "Female kids");

    // This destination is still fresh in the cache and its URL has committed,
    // so there is nothing left to wait for: holding the fence up here would
    // leave the list behind the loading label until the search debounce
    // happened to run.
    expect(screen.getAllByText("G-001")[0]).toBeInTheDocument();
    expect(screen.queryByText("Loading animals…")).not.toBeInTheDocument();
    expect(screen.queryByText("Updating animals…")).not.toBeInTheDocument();
  });
});

describe("AnimalsPage ?new=1 stripping", () => {
  it("strips a second ?new=1 flag that arrives later in the same session", async () => {
    nav.state.search = "new=1";
    const view = renderWithProviders(<AnimalsPage />);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    await waitFor(() => expect(nav.state.search).toBe(""));
    expect(nav.replace).toHaveBeenCalledTimes(1);

    // A second /animals/new deep link in the same session must be cleaned up
    // exactly like the first; the flag can never be allowed to survive a
    // reload.
    nav.state.search = "new=1";
    view.rerender(<AnimalsPage />);

    await waitFor(() => expect(nav.replace).toHaveBeenCalledTimes(2));
    expect(nav.replace).toHaveBeenLastCalledWith("/animals");
    expect(nav.state.search).toBe("");
  });

  it("does not dispatch a second strip while the first is still in flight", async () => {
    const view = renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");

    nav.state.deferReplace = true;
    nav.state.search = "new=1";
    view.rerender(<AnimalsPage />);
    await waitFor(() => expect(nav.state.deferredReplacements).toEqual(["/animals"]));

    // The operator searches while Next is still working through the strip, so
    // the debounced URL carries the flag forward and commits first.
    fireEvent.change(searchBox(), { target: { value: "G-0" } });
    await waitFor(() => expect(nav.state.deferredReplacements).toHaveLength(2));
    expect(nav.state.deferredReplacements[1]).toBe("/animals?new=1&q=G-0");

    nav.state.search = "new=1&q=G-0";
    view.rerender(<AnimalsPage />);

    // Re-dispatching the still-outstanding strip would register a pending
    // navigation no commit can ever match, fencing the list forever.
    expect((await screen.findAllByText("G-001"))[0]).toBeInTheDocument();
    expect(nav.state.deferredReplacements).toHaveLength(2);
  });
});

describe("AnimalsPage refresh after a create", () => {
  it("re-homes to page one and keeps the filters after creating from a later page", async () => {
    const user = userEvent.setup();
    serveAnimals(Array.from({ length: 60 }, (_, index) => animal(index + 1)));
    nav.state.search = "bucket=RESTING&page=2";
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("Showing 51–60 of 60 animals");

    const dialog = await openCreateDialog(user);
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    await waitFor(() => expect(postCalls).toBe(1));
    // The new animal sorts onto page one, so the list re-homes there while
    // keeping the bucket the operator was working in.
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/animals?bucket=RESTING"));
    expect(await screen.findByText("Showing 1–50 of 60 animals")).toBeInTheDocument();
  });

  it("refreshes without navigating when the create happens on page one", async () => {
    const user = userEvent.setup();
    nav.state.search = "page=1";
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");
    expect(nav.replace).not.toHaveBeenCalled();
    const requestsBefore = seenParams.length;

    const dialog = await openCreateDialog(user);
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    await waitFor(() => expect(postCalls).toBe(1));
    await waitFor(() => expect(seenParams.length).toBeGreaterThan(requestsBefore));
    // Already home: refetch the list, but leave the history alone.
    expect(nav.replace).not.toHaveBeenCalled();
    expect(nav.push).not.toHaveBeenCalled();
    expect(nav.state.search).toBe("page=1");
  });
});

describe("AnimalsPage page ceiling", () => {
  it("steps into the page its URL already names without a second history entry", async () => {
    // A deep link past the endpoint's offset ceiling is clamped to MAX_PAGE
    // (offset 1_000_000, clamped to the 10_000 ceiling), so the list can be showing a deep page while the URL
    // still names 20002. Stepping forward then needs no navigation at all.
    const user = userEvent.setup();
    server.use(
      http.get("/api/animals", ({ request }) => {
        const params = new URL(request.url).searchParams;
        seenParams.push(params);
        const offset = Number(params.get("offset"));
        return HttpResponse.json({
          animals: [{ ...animal(offset + 1), tag_number: `G-${offset + 1}` }],
          total: 1_000_051,
        });
      }),
    );
    nav.state.search = "page=202";
    renderWithProviders(<AnimalsPage />);

    expect(
      await screen.findByText("Showing 10001–10050 of 1000051 animals"),
    ).toBeInTheDocument();
    expect(seenParams[0].get("offset")).toBe("10000");

    await user.click(screen.getByRole("button", { name: "Next" }));

    expect(
      await screen.findByText("Showing 10051–10100 of 1000051 animals"),
    ).toBeInTheDocument();
    // The tag is rendered by both the mobile card list and the table.
    expect(screen.getAllByText("G-10051")[0]).toBeInTheDocument();
    expect(nav.push).not.toHaveBeenCalled();
    expect(nav.replace).not.toHaveBeenCalled();
    expect(nav.state.search).toBe("page=202");
  });
});
