/**
 * Behaviour the other animals suites leave unpinned: how a search term is
 * normalised between the input, the URL and the API; the local page/query
 * state each navigation applies BEFORE Next commits its URL; and what the
 * create dialog actually sends once its inputs are trimmed.
 */

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import AnimalsPage from "./page";

const nav = vi.hoisted(() => {
  const state = {
    search: "",
    deferReplace: false,
    deferPush: false,
    deferredReplacements: [] as string[],
    deferredPushes: [] as string[],
  };
  const applyUrl = (url: string) => {
    state.search = url.includes("?") ? url.slice(url.indexOf("?") + 1) : "";
  };
  const push = vi.fn((url: string) => {
    if (state.deferPush) state.deferredPushes.push(url);
    else applyUrl(url);
  });
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

type User = ReturnType<typeof userEvent.setup>;

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

/** URL a navigation was dispatched to, as query params. */
function paramsOf(href: string): URLSearchParams {
  return new URL(href, "https://goatfarm.invalid").searchParams;
}

/** Serialized query string of a dispatched URL, as useSearchParams sees it. */
function paramsKeyOf(href: string): string {
  const questionMark = href.indexOf("?");
  return questionMark === -1 ? "" : href.slice(questionMark + 1);
}

async function pickOption(user: User, trigger: HTMLElement, name: string) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

function searchBox(): HTMLElement {
  return screen.getByRole("searchbox", { name: "Search animals by tag" });
}

async function settleAct(ms: number) {
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, ms));
  });
}

describe("AnimalsPage search term normalisation", () => {
  let seenParams: URLSearchParams[];

  beforeEach(() => {
    nav.state.search = "";
    nav.state.deferReplace = false;
    nav.state.deferPush = false;
    nav.state.deferredReplacements = [];
    nav.state.deferredPushes = [];
    nav.push.mockClear();
    nav.replace.mockClear();
    seenParams = [];
    server.use(
      http.get("/api/animals", ({ request }) => {
        seenParams.push(new URL(request.url).searchParams);
        return HttpResponse.json({ animals: [animal(1)], total: 1 });
      }),
    );
  });

  it("queries a padded search deep link by its trimmed term and stays put", async () => {
    nav.state.search = "q=%20G-001%20";
    renderWithProviders(<AnimalsPage />);

    // The padding belongs to the URL the operator arrived on, never to the
    // term the endpoint is asked about.
    expect((await screen.findAllByText("G-001"))[0]).toBeInTheDocument();
    expect(seenParams[0].get("q")).toBe("G-001");

    // That term already matches the URL, so the debounce has no replacement
    // to issue and the rows must not be fenced behind a phantom navigation.
    await settleAct(400);
    expect(nav.replace).not.toHaveBeenCalled();
    expect(nav.push).not.toHaveBeenCalled();
    expect(screen.getAllByText("G-001")[0]).toBeInTheDocument();
    expect(screen.queryByText("Loading animals…")).not.toBeInTheDocument();
    expect(screen.queryByText("Updating animals…")).not.toBeInTheDocument();
  });

  it("clamps an over-long tag deep link to the 60 characters the endpoint accepts", async () => {
    // Mirrors backend/app/api/animals.py `q: Query(max_length=60)`: a longer
    // term is a 422, so the deep link is normalised instead of forwarded.
    const clamped = "G".repeat(60);
    nav.state.search = `q=${"G".repeat(75)}`;
    renderWithProviders(<AnimalsPage />);

    expect((await screen.findAllByText("G-001"))[0]).toBeInTheDocument();
    expect(seenParams[0].get("q")).toBe(clamped);
    expect(searchBox()).toHaveValue(clamped);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith(`/animals?q=${clamped}`));
  });

  it("rehydrates the search box from the term its own URL carries", async () => {
    renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("G-001");

    fireEvent.change(searchBox(), { target: { value: "  G7  " } });

    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/animals?q=G7"));
    // The commit describes exactly the term the operator asked for, so it is
    // not a newer edit to protect: the box adopts the URL's own spelling.
    await waitFor(() => expect(searchBox()).toHaveValue("G7"));
    await waitFor(() => expect(screen.getAllByText("G-001")[0]).toBeInTheDocument());
    expect(screen.queryByText("Loading animals…")).not.toBeInTheDocument();
    expect(screen.queryByText("Updating animals…")).not.toBeInTheDocument();
  });

  it("adopts a padded search term from a history navigation as a trimmed query", async () => {
    const view = renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("G-001");
    const before = seenParams.length;

    // Back/forward onto a URL whose q carries padding: the request that URL
    // describes is the trimmed one.
    nav.state.search = "q=%20G-001%20";
    view.rerender(<AnimalsPage />);

    await waitFor(() => expect(seenParams.length).toBeGreaterThan(before));
    expect(seenParams[before].get("q")).toBe("G-001");
    await settleAct(400);
    expect(nav.replace).not.toHaveBeenCalled();
    expect(screen.getAllByText("G-001")[0]).toBeInTheDocument();
  });

  it("keeps the list interactive when a ?new=1 strip commits a padded term", async () => {
    nav.state.search = "q=%20G-001%20&new=1";
    nav.state.deferReplace = true;
    const view = renderWithProviders(<AnimalsPage />);
    await screen.findByRole("dialog");
    await waitFor(() => expect(nav.state.deferredReplacements).toHaveLength(1));
    // The strip rewrites only the flag; the operator's padding rides along.
    expect(nav.state.deferredReplacements[0]).toBe("/animals?q=+G-001+");
    await settleAct(50);

    // Next commits it. The URL still describes the term the box holds, so the
    // commit releases the fence instead of being read as a newer edit.
    nav.state.search = paramsKeyOf(nav.state.deferredReplacements[0]);
    view.rerender(<AnimalsPage />);

    expect(screen.queryByText("Loading animals…")).not.toBeInTheDocument();
    expect(screen.queryByText("Updating animals…")).not.toBeInTheDocument();
    expect(screen.getAllByText("G-001")[0]).toBeInTheDocument();
  });

  it("re-arms the ?new=1 strip after the first one commits", async () => {
    const view = renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("G-001");
    expect(nav.replace).not.toHaveBeenCalled();

    nav.state.search = "new=1";
    view.rerender(<AnimalsPage />);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/animals"));
    expect(nav.state.search).toBe("");

    // A later visit to the flagged URL must be stripped again: the latch that
    // suppresses Strict Mode's replay is re-armed once the strip commits.
    nav.replace.mockClear();
    nav.state.search = "new=1";
    view.rerender(<AnimalsPage />);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/animals"));
  });
});

describe("AnimalsPage state applied before a URL commits", () => {
  let seenParams: URLSearchParams[];

  const serveAnimals = (animals: ReturnType<typeof animal>[]) => {
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
    );
  };

  beforeEach(() => {
    nav.state.search = "";
    nav.state.deferReplace = false;
    nav.state.deferPush = false;
    nav.state.deferredReplacements = [];
    nav.state.deferredPushes = [];
    nav.push.mockClear();
    nav.replace.mockClear();
    seenParams = [];
  });

  it("requests the deep-linked page in its very first request", async () => {
    serveAnimals(Array.from({ length: 155 }, (_, index) => animal(index + 1)));
    nav.state.search = "page=3";
    renderWithProviders(<AnimalsPage />);

    expect(
      await screen.findByText("Showing 101–150 of 155 animals"),
    ).toBeInTheDocument();
    // A first request for page one would be a wasted round trip whose rows
    // the operator never asked for.
    expect(seenParams).toHaveLength(1);
    expect(seenParams[0].get("offset")).toBe("100");
  });

  it("requests page one for a new search term before its URL commits", async () => {
    serveAnimals(Array.from({ length: 155 }, (_, index) => animal(index + 1)));
    nav.state.search = "page=3";
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("Showing 101–150 of 155 animals");

    nav.state.deferReplace = true;
    const before = seenParams.length;
    fireEvent.change(searchBox(), { target: { value: "G-1" } });

    // A search is answered from the first page of ITS results, not from the
    // offset the operator happened to be reading.
    await waitFor(() => expect(seenParams.length).toBeGreaterThan(before));
    expect(seenParams[before].get("q")).toBe("G-1");
    expect(seenParams[before].get("offset")).toBe("0");
    expect(nav.state.deferredReplacements).toEqual(["/animals?q=G-1"]);
  });

  it("requests page one when a filter changes before its URL commits", async () => {
    const user = userEvent.setup();
    serveAnimals(Array.from({ length: 155 }, (_, index) => animal(index + 1)));
    nav.state.search = "page=3";
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("Showing 101–150 of 155 animals");

    nav.state.deferReplace = true;
    const before = seenParams.length;
    await pickOption(user, screen.getByLabelText("Filter animals by bucket"), "Female kids");

    await waitFor(() => expect(seenParams.length).toBeGreaterThan(before));
    expect(seenParams[before].get("bucket")).toBe("FEMALE_KIDS");
    expect(seenParams[before].get("offset")).toBe("0");
    expect(nav.state.deferredReplacements).toEqual(["/animals?bucket=FEMALE_KIDS"]);
  });

  it("sends a padded search box term trimmed when a filter changes", async () => {
    const user = userEvent.setup();
    serveAnimals([animal(1)]);
    nav.state.search = "q=G7";
    renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("G-001");

    // Padding the term already searched for produces no new URL, so the
    // padding survives in the box until something else navigates.
    fireEvent.change(searchBox(), { target: { value: "  G7  " } });
    await settleAct(400);
    expect(nav.replace).not.toHaveBeenCalled();
    expect(searchBox()).toHaveValue("  G7  ");

    nav.state.deferReplace = true;
    const before = seenParams.length;
    await pickOption(user, screen.getByLabelText("Filter animals by bucket"), "Female kids");

    await waitFor(() => expect(seenParams.length).toBeGreaterThan(before));
    expect(seenParams[before].get("q")).toBe("G7");
    expect(paramsOf(nav.state.deferredReplacements[0]).get("q")).toBe("G7");
  });

  it("carries a just-typed term into a filter change made before the debounce", async () => {
    const user = userEvent.setup();
    serveAnimals([animal(1)]);
    renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("G-001");

    nav.state.deferReplace = true;
    fireEvent.change(searchBox(), { target: { value: "G8" } });
    await pickOption(user, screen.getByLabelText("Filter animals by sex"), "Female");

    // The filter edit publishes the pending term itself; waiting for the
    // debounce would ask the server for the new filter with the old search.
    await waitFor(() => expect(seenParams.some((p) => p.get("sex") === "F")).toBe(true));
    expect(seenParams.find((p) => p.get("sex") === "F")?.get("q")).toBe("G8");
  });

  it("fetches the pushed page before Next commits its URL", async () => {
    const user = userEvent.setup();
    serveAnimals(Array.from({ length: 155 }, (_, index) => animal(index + 1)));
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("Showing 1–50 of 155 animals");

    nav.state.deferPush = true;
    const before = seenParams.length;
    await user.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() => expect(seenParams.length).toBeGreaterThan(before));
    expect(seenParams[before].get("offset")).toBe("50");
    expect(nav.state.deferredPushes).toEqual(["/animals?page=2"]);
    // Rows stay fenced until Next commits the push. The wrapper and the
    // InlineLoading label are both polite status regions; the uncommitted
    // navigation folds into dataLoading, so the label reads "Loading".
    expect(screen.getAllByRole("status")[0]).toHaveTextContent("Loading animals…");
  });

  it("releases the fence when a browser navigation supersedes an uncommitted one", async () => {
    const user = userEvent.setup();
    serveAnimals([animal(1)]);
    const view = renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("G-001");

    nav.state.deferReplace = true;
    await pickOption(user, screen.getByLabelText("Filter animals by bucket"), "Female kids");
    expect(nav.state.deferredReplacements).toEqual(["/animals?bucket=FEMALE_KIDS"]);
    expect(screen.queryByText("G-001")).not.toBeInTheDocument();

    // The operator navigates with the browser instead. Next discards the
    // list's own pending action, so the registry entry it left behind must go
    // with it — otherwise nothing ever lowers the fence again.
    nav.state.search = "status=SOLD";
    view.rerender(<AnimalsPage />);

    expect((await screen.findAllByText("G-001"))[0]).toBeInTheDocument();
    await settleAct(400);
    expect(screen.queryByText("Loading animals…")).not.toBeInTheDocument();
    expect(screen.queryByText("Updating animals…")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Filter animals by status")).toHaveTextContent("Sold");
    expect(screen.getByLabelText("Filter animals by bucket")).toHaveTextContent("All buckets");
  });

  it("never re-dispatches a page navigation to the URL already committed", async () => {
    // A bookmarked page past the backend's offset ceiling clamps to MAX_PAGE,
    // so the URL still names page 20002 while the list sits on 20001. Next
    // cannot commit a navigation to the URL it is already on: dispatching one
    // would strand the list behind a fence no commit ever lowers.
    const user = userEvent.setup();
    // A herd far past the ceiling: every offset answers with a full page.
    server.use(
      http.get("/api/animals", ({ request }) => {
        seenParams.push(new URL(request.url).searchParams);
        return HttpResponse.json({
          animals: Array.from({ length: 50 }, (_, index) => animal(index + 1)),
          total: 1_100_000,
        });
      }),
    );
    nav.state.search = "page=202";
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("Showing 10001–10050 of 1100000 animals");
    expect(seenParams[0].get("offset")).toBe("10000");

    await user.click(screen.getByRole("button", { name: "Next" }));

    expect(nav.push).not.toHaveBeenCalled();
    expect(nav.replace).not.toHaveBeenCalled();
    expect(
      await screen.findByText("Showing 10051–10100 of 1100000 animals"),
    ).toBeInTheDocument();
  });
});

describe("AnimalsPage create dialog payload", () => {
  let seenParams: URLSearchParams[];
  let postCalls: number;
  let postBody: Record<string, unknown> | null;

  const serveAnimals = (animals: ReturnType<typeof animal>[]) => {
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
    );
  };

  beforeEach(() => {
    nav.state.search = "";
    nav.state.deferReplace = false;
    nav.state.deferPush = false;
    nav.state.deferredReplacements = [];
    nav.state.deferredPushes = [];
    nav.push.mockClear();
    nav.replace.mockClear();
    seenParams = [];
    postCalls = 0;
    postBody = null;
    serveAnimals([animal(1)]);
    server.use(
      http.post("/api/animals", async ({ request }) => {
        postCalls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...animal(2), id: 2 }, { status: 201 });
      }),
    );
  });

  async function openDialog(user: User) {
    await user.click(screen.getByRole("button", { name: "Add animal" }));
    return await screen.findByRole("dialog");
  }

  async function startImport(user: User, dialog: HTMLElement) {
    await pickOption(
      user,
      within(dialog).getByLabelText("Source *"),
      "Historical born-on-farm import",
    );
  }

  it("trims the tag number and the audit reason it POSTs", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("G-001");
    const dialog = await openDialog(user);

    await user.type(within(dialog).getByLabelText("Tag number"), "  G-101  ");
    await startImport(user, dialog);
    await user.type(
      within(dialog).getByLabelText("Historical import reason *"),
      "  Paper herd register  ",
    );
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    await waitFor(() => expect(postCalls).toBe(1));
    // Stray padding would become part of the stored tag and of the audit
    // trail the import is justified by.
    expect(postBody).toMatchObject({
      tag_number: "G-101",
      source: "BORN",
      historical_import_reason: "Paper herd register",
    });
  });

  it("rejects a whitespace-only historical import reason", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("G-001");
    const dialog = await openDialog(user);
    await startImport(user, dialog);

    await user.type(within(dialog).getByLabelText("Historical import reason *"), "   ");
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    expect(
      await within(dialog).findByText("Explain why this historical animal is being imported"),
    ).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("re-homes to page one after a create even before the URL commits", async () => {
    const user = userEvent.setup();
    serveAnimals(Array.from({ length: 60 }, (_, index) => animal(index + 1)));
    nav.state.search = "page=2";
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("Showing 51–60 of 60 animals");

    nav.state.deferReplace = true;
    const dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText("Tag number"), "G-NEW");
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));
    await waitFor(() => expect(postCalls).toBe(1));

    // The new animal lands on page one, so the refreshed list is fetched
    // there rather than at the offset the operator was reading.
    expect(nav.state.deferredReplacements).toEqual(["/animals"]);
    await waitFor(() => expect(seenParams.at(-1)?.get("offset")).toBe("0"));
  });

  it("restores the dialog defaults after a successful create", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("G-001");
    let dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText("Tag number"), "G-777");
    await pickOption(user, within(dialog).getByLabelText("Sex *"), "Male");
    await startImport(user, dialog);
    await user.type(
      within(dialog).getByLabelText("Historical import reason *"),
      "Paper herd register",
    );
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));
    await waitFor(() => expect(postCalls).toBe(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    dialog = await openDialog(user);
    expect(within(dialog).getByLabelText("Tag number")).toHaveValue("");
    expect(within(dialog).getByLabelText("Sex *")).toHaveTextContent("Female");
    expect(within(dialog).getByLabelText("Source *")).toHaveTextContent("Purchased");
    expect(
      within(dialog).queryByLabelText("Historical import reason *"),
    ).not.toBeInTheDocument();
  });

  it("returns the bucket to QUARANTINE when the source switches back", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("G-001");
    const dialog = await openDialog(user);
    await startImport(user, dialog);
    await pickOption(user, within(dialog).getByLabelText("Bucket *"), "Breeding");
    expect(within(dialog).getByLabelText("Bucket *")).toHaveTextContent("Breeding");

    await pickOption(user, within(dialog).getByLabelText("Source *"), "Purchased");
    await pickOption(
      user,
      within(dialog).getByLabelText("Source *"),
      "Historical born-on-farm import",
    );

    // A purchase is pinned to QUARANTINE, so returning to an import must not
    // silently re-offer the bucket chosen before that detour.
    expect(within(dialog).getByLabelText("Bucket *")).toHaveTextContent("Quarantine");
    expect(within(dialog).queryByText(/BREEDING imports require/)).not.toBeInTheDocument();
  });
});
