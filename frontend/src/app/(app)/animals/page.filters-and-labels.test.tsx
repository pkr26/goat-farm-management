/**
 * AnimalsPage — sentinel handling, display labels, list-URL contract and
 * dialog guards (complements page.test.tsx, page.extended.test.tsx,
 * page.races.test.tsx and page.pagination.test.tsx).
 *
 * Covers: the ALL sentinel never reaching the API, every Select trigger
 * rendering its display label instead of the raw enum, the create dialog's
 * bucket/sex parity guard and BREEDING thresholds, the URL contract for the
 * sex/status filters and a legacy `offset`, the interaction fence released
 * by the very URL the list dispatched, and the in-flight / post-create /
 * transport-failure states.
 */

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuth } from "@/lib/auth-context";
import { usePermissions } from "@/lib/use-permissions";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

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
let postBody: Record<string, unknown> | null;

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
    http.post("/api/animals", async ({ request }) => {
      postCalls += 1;
      postBody = (await request.json()) as Record<string, unknown>;
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

/** Switch the open dialog to the owner-only historical import and satisfy its
 *  audit reason, so later assertions see only the guard under test. */
async function startHistoricalImport(user: User, dialog: HTMLElement, reason: string) {
  await pickOption(
    user,
    within(dialog).getByLabelText("Source *"),
    "Historical born-on-farm import",
  );
  await user.type(within(dialog).getByLabelText("Historical import reason *"), reason);
}

/** Date inputs ignore typing in jsdom; set them the way the picker does. */
function setDate(input: HTMLElement, value: string) {
  fireEvent.change(input, { target: { value } });
}

function paramsKeyFromHref(href: string): string {
  const questionMark = href.indexOf("?");
  return questionMark === -1 ? "" : href.slice(questionMark + 1);
}

function lastReplacedParams(): URLSearchParams {
  const href = String(nav.replace.mock.calls.at(-1)?.[0] ?? "");
  return new URL(href, "https://goatfarm.invalid").searchParams;
}

/** Mounts the list only once the session and its permissions are resolved —
 *  the state a client-side navigation into /animals starts from, where the
 *  list query is enabled on the page's very first render. */
function WhenSessionReady({ children }: { children: ReactNode }) {
  const { farmId } = useAuth();
  const { can } = usePermissions();
  if (farmId === null || !can("animals.view")) return null;
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
  postBody = null;
  serveAnimals();
});

describe("AnimalsPage filter sentinel and labels", () => {
  it("never forwards an ALL sentinel filter value to the list API", async () => {
    // A hand-edited (or bookmarked) URL can carry the sentinel the selects
    // use for "no filter"; it means "unfiltered", never a bucket named ALL.
    nav.state.search = "bucket=ALL&sex=ALL&status=ALL";
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");

    expect(seenParams.at(-1)?.get("bucket")).toBeNull();
    expect(seenParams.at(-1)?.get("sex")).toBeNull();
    expect(seenParams.at(-1)?.get("status")).toBeNull();
    expect(seenParams.at(-1)?.get("include_all_statuses")).toBe("true");
    expect(screen.getByLabelText("Filter animals by bucket")).toHaveTextContent(
      "All buckets",
    );
    expect(screen.getByLabelText("Filter animals by sex")).toHaveTextContent("Both sexes");
    expect(screen.getByLabelText("Filter animals by status")).toHaveTextContent(
      "All statuses",
    );
  });

  it("labels the neutral bucket and sex filters and the chosen sex/status", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");

    expect(screen.getByLabelText("Filter animals by bucket")).toHaveTextContent(
      "All buckets",
    );
    expect(screen.getByLabelText("Filter animals by sex")).toHaveTextContent("Both sexes");

    await pickOption(user, screen.getByLabelText("Filter animals by sex"), "Male");
    expect(screen.getByLabelText("Filter animals by sex")).toHaveTextContent("Male");

    await pickOption(user, screen.getByLabelText("Filter animals by status"), "Sold");
    expect(screen.getByLabelText("Filter animals by status")).toHaveTextContent("Sold");
  });

  it("labels the dialog's source and bucket triggers with their display text", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");
    const dialog = await openCreateDialog(user);

    await pickOption(
      user,
      within(dialog).getByLabelText("Source *"),
      "Historical born-on-farm import",
    );
    expect(within(dialog).getByLabelText("Source *")).toHaveTextContent(
      "Historical born-on-farm import",
    );

    await pickOption(user, within(dialog).getByLabelText("Bucket *"), "Female kids");
    expect(within(dialog).getByLabelText("Bucket *")).toHaveTextContent("Female kids");
    expect(within(dialog).getByLabelText("Bucket *")).not.toHaveTextContent("FEMALE_KIDS");
  });

  it("shows the em-dash placeholder until a birth type is chosen", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");
    const dialog = await openCreateDialog(user);
    await startHistoricalImport(user, dialog, "Birth type placeholder fixture");

    expect(within(dialog).getByLabelText("Birth type")).toHaveTextContent("—");
    await pickOption(user, within(dialog).getByLabelText("Birth type"), "Twin");
    expect(within(dialog).getByLabelText("Birth type")).toHaveTextContent("Twin");
  });
});

describe("AnimalsPage create dialog guards", () => {
  it("spells out the BREEDING thresholds for the selected sex", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");
    const dialog = await openCreateDialog(user);
    await startHistoricalImport(user, dialog, "Breeding threshold hint fixture");

    // The hint only belongs to a BREEDING import.
    expect(within(dialog).queryByText(/BREEDING imports require/)).not.toBeInTheDocument();
    await pickOption(user, within(dialog).getByLabelText("Bucket *"), "Breeding");
    expect(within(dialog).getByText(/BREEDING imports require/)).toHaveTextContent(
      "BREEDING imports require a doe of at least 10 months and 22 kg.",
    );

    await pickOption(user, within(dialog).getByLabelText("Sex *"), "Male");
    expect(within(dialog).getByText(/BREEDING imports require/)).toHaveTextContent(
      "BREEDING imports require a buck of at least 12 months and 25 kg.",
    );
  });

  it("rejects a future entry-weight date inline instead of hitting the API", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");
    const dialog = await openCreateDialog(user);
    await startHistoricalImport(user, dialog, "Entry weight date fixture");
    // A weight can only have been taken on or before the farm's today.
    await user.type(within(dialog).getByLabelText("Entry weight (kg)"), "25");
    setDate(within(dialog).getByLabelText("Entry weight date"), "2099-01-01");
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    expect(
      await within(dialog).findByText("Date can't be in the future"),
    ).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("rejects a birth weight above the server's 1000 kg ceiling", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");
    const dialog = await openCreateDialog(user);
    await startHistoricalImport(user, dialog, "Birth weight ceiling fixture");
    await user.type(within(dialog).getByLabelText("Birth weight (kg)"), "1200");
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    expect(await within(dialog).findByText("At most 1000 kg")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Entry weight (kg)")).toHaveValue(null);
    expect(postCalls).toBe(0);
  });

  it("shows a Saving… submit button while the create request is in flight", async () => {
    let release: (() => void) | undefined;
    const parked = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post("/api/animals", async () => {
        postCalls += 1;
        await parked;
        return HttpResponse.json(animal(99), { status: 201 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");
    const dialog = await openCreateDialog(user);
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    const saving = await within(dialog).findByRole("button", { name: "Saving…" });
    expect(saving).toBeDisabled();
    expect(
      within(dialog).queryByRole("button", { name: "Save animal" }),
    ).not.toBeInTheDocument();

    release?.();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("reverts a historical import to Purchased when the owner grant is lost", async () => {
    const user = userEvent.setup();
    const view = renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");
    const dialog = await openCreateDialog(user);
    await startHistoricalImport(user, dialog, "Owner grant withdrawn fixture");
    expect(within(dialog).getByLabelText("Historical import reason *")).toBeInTheDocument();

    // The role is downgraded while the dialog is open: only owners may file a
    // historical born-on-farm import, so the open form must fall back.
    server.use(permissionsHandler(["animals.view", "animals.create"]));
    await act(async () => {
      await view.queryClient.invalidateQueries();
    });

    await waitFor(() =>
      expect(
        within(dialog).queryByLabelText("Historical import reason *"),
      ).not.toBeInTheDocument(),
    );
    expect(within(dialog).getByLabelText("Source *")).toHaveTextContent("Purchased");
    expect(within(dialog).getByLabelText(/purchase price/i)).toBeInTheDocument();
  });

  it("returns to page one and drops the page param after creating from a later page", async () => {
    serveAnimals(Array.from({ length: 60 }, (_, index) => animal(index + 1)));
    const user = userEvent.setup();
    nav.state.search = "page=2";
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("Showing 51–60 of 60 animals");

    const dialog = await openCreateDialog(user);
    await user.type(within(dialog).getByLabelText(/tag number/i), "G-NEW");
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));

    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toMatchObject({ tag_number: "G-NEW" });
    // A new animal lands on page one, so the list re-homes there instead of
    // leaving the operator on a page that no longer shows their entry.
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/animals"));
    expect(await screen.findByText("Showing 1–50 of 60 animals")).toBeInTheDocument();
  });
});

describe("AnimalsPage list URL contract", () => {
  it("records the chosen sex filter in the URL and clears it on reset", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");

    await pickOption(user, screen.getByLabelText("Filter animals by sex"), "Male");
    await waitFor(() => expect(nav.replace).toHaveBeenCalled());
    expect(lastReplacedParams().get("sex")).toBe("M");
    await waitFor(() => expect(seenParams.at(-1)?.get("sex")).toBe("M"));

    await pickOption(user, screen.getByLabelText("Filter animals by sex"), "Both sexes");
    await waitFor(() => expect(lastReplacedParams().get("sex")).toBeNull());
    await waitFor(() => expect(seenParams.at(-1)?.get("sex")).toBeNull());
  });

  it("records the chosen status filter in the URL and keeps it after the commit", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");

    await pickOption(user, screen.getByLabelText("Filter animals by status"), "Sold");
    await waitFor(() => expect(nav.replace).toHaveBeenCalled());
    expect(lastReplacedParams().get("status")).toBe("SOLD");

    // The committed URL is what the params-sync effect reads back, so a
    // status missing from it would silently reset the select.
    await waitFor(() => expect(seenParams.at(-1)?.get("status")).toBe("SOLD"));
    expect(screen.getByLabelText("Filter animals by status")).toHaveTextContent("Sold");
  });

  it("drops a legacy offset param from the list URL", async () => {
    const user = userEvent.setup();
    nav.state.search = "offset=100";
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");
    // `page` is the canonical browser state; the request offset comes from it.
    expect(seenParams[0].get("offset")).toBe("0");

    await pickOption(user, screen.getByLabelText("Filter animals by bucket"), "Female kids");
    await waitFor(() => expect(nav.replace).toHaveBeenCalled());
    expect(lastReplacedParams().get("bucket")).toBe("FEMALE_KIDS");
    expect(lastReplacedParams().get("offset")).toBeNull();
  });

  it("strips a ?new=1 flag that arrives by a later client navigation", async () => {
    const view = renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");
    expect(nav.replace).not.toHaveBeenCalled();

    nav.state.search = "new=1";
    view.rerender(<AnimalsPage />);

    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/animals"));
    expect(nav.state.search).toBe("");
  });
});

describe("AnimalsPage navigation fence", () => {
  it("releases the fence as soon as the search URL it dispatched commits", async () => {
    const user = userEvent.setup();
    nav.state.deferReplace = true;
    const view = renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");

    await user.type(
      screen.getByRole("searchbox", { name: "Search animals by tag" }),
      "G-0",
    );
    await waitFor(() => expect(nav.state.deferredReplacements).toHaveLength(1));
    await waitFor(() => expect(seenParams.at(-1)?.get("q")).toBe("G-0"));
    await waitFor(() => expect(view.queryClient.isFetching()).toBe(0));
    expect(screen.getByText("Loading animals…")).toBeInTheDocument();

    nav.state.search = paramsKeyFromHref(nav.state.deferredReplacements[0]);
    view.rerender(<AnimalsPage />);

    // The committed URL carries exactly the text in the box, so nothing newer
    // has been typed: the rows are the operator's own search and are live at
    // once, without waiting for another debounce tick.
    expect(screen.getByRole("link", { name: "G-001" })).toBeInTheDocument();
    expect(screen.queryByText("Loading animals…")).not.toBeInTheDocument();
  });

  it("releases the fence as soon as a filter URL with no search term commits", async () => {
    const user = userEvent.setup();
    nav.state.deferReplace = true;
    const view = renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");

    await pickOption(user, screen.getByLabelText("Filter animals by bucket"), "Female kids");
    await waitFor(() => expect(nav.state.deferredReplacements).toHaveLength(1));
    await waitFor(() => expect(seenParams.at(-1)?.get("bucket")).toBe("FEMALE_KIDS"));
    await waitFor(() => expect(view.queryClient.isFetching()).toBe(0));
    expect(screen.getByText("Loading animals…")).toBeInTheDocument();

    nav.state.search = paramsKeyFromHref(nav.state.deferredReplacements[0]);
    view.rerender(<AnimalsPage />);

    // An empty search box and a URL without `q` describe the same state, so
    // this commit owes the operator their rows immediately.
    expect(screen.getByRole("link", { name: "G-001" })).toBeInTheDocument();
    expect(screen.queryByText("Loading animals…")).not.toBeInTheDocument();
  });

  it("does not re-navigate or leave page two for a deep-linked search term", async () => {
    serveAnimals(Array.from({ length: 100 }, (_, index) => animal(index + 1)));
    nav.state.search = "q=G-77&page=2";
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("Showing 51–100 of 100 animals");

    // Let the debounce fire: the URL already describes the search box, so it
    // must not issue a replacement (which would reset the page to one).
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 400));
    });

    expect(nav.replace).not.toHaveBeenCalled();
    expect(nav.push).not.toHaveBeenCalled();
    expect(screen.getByText("Showing 51–100 of 100 animals")).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Search animals by tag" })).toHaveValue(
      "G-77",
    );
  });
});

describe("AnimalsPage first request after a client-side navigation", () => {
  it("applies the deep-linked filters and search to the very first request", async () => {
    nav.state.search = "bucket=RESTING&sex=M&status=SOLD&q=G-77";
    renderWithProviders(
      <WhenSessionReady>
        <AnimalsPage />
      </WhenSessionReady>,
    );
    await screen.findByText("1 animal(s)");

    // Permissions are already cached on an in-session navigation, so the very
    // first request must be the filtered one — no unfiltered herd fetch first.
    expect(seenParams).toHaveLength(1);
    expect(seenParams[0].get("bucket")).toBe("RESTING");
    expect(seenParams[0].get("sex")).toBe("M");
    expect(seenParams[0].get("status")).toBe("SOLD");
    expect(seenParams[0].get("q")).toBe("G-77");
  });

  it("sends no search term on the first request when the URL carries none", async () => {
    nav.state.search = "bucket=RESTING";
    renderWithProviders(
      <WhenSessionReady>
        <AnimalsPage />
      </WhenSessionReady>,
    );
    await screen.findByText("1 animal(s)");

    expect(seenParams).toHaveLength(1);
    expect(seenParams[0].get("q")).toBeNull();
    expect(screen.getByRole("searchbox", { name: "Search animals by tag" })).toHaveValue("");
  });
});

describe("AnimalsPage list failures", () => {
  it("shows the generic message when the list fails without a server detail", async () => {
    server.use(http.get("/api/animals", () => HttpResponse.error()));
    renderWithProviders(<AnimalsPage />);

    expect(await screen.findByText("Could not load animals.")).toBeInTheDocument();
  });
});
