// REGRESSION TEST — bug fixed; this test pins the fix.
//
// Typed search text destroyed by the page-clamp navigation.
//
// The pending-navigation registry mapped `paramsKey -> searchEditRevision`,
// stamping the LIVE edit counter at dispatch time. The out-of-range clamp
// effect builds its replacement URL with `q: debouncedQ`, which lags the input
// by up to 300ms. So a keystroke made BEFORE the clamp dispatched was already
// folded into the stamp while being absent from the URL, and the guard's strict
// `>` comparison computed `2 > 2 === false` — i.e. "no newer edit" — so the
// commit ran setQ("") and wiped what the operator had typed.
//
// It was unrecoverable: the debounce then saw normalizedQ === urlQ === "" and
// early-returned, so no search was ever issued for the discarded text.
//
// The registry now records the `q` each URL actually CARRIES and compares that
// against the live input, which is blind to neither edit ordering nor the
// debounce lag.

import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import AnimalsPage from "./page";
import { settle } from "@/test/settle";

const nav = vi.hoisted(() => {
  const state = { search: "" };
  const applyUrl = (url: string) => {
    state.search = url.includes("?") ? url.slice(url.indexOf("?") + 1) : "";
  };
  const push = vi.fn(applyUrl);
  const replace = vi.fn(applyUrl);
  return { state, push, replace, router: { push, replace, prefetch: vi.fn() } };
});

vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  usePathname: () => "/animals",
  useSearchParams: () => new URLSearchParams(nav.state.search),
  useParams: () => ({}),
}));

beforeAll(() => {
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
  notes: null,
  created_at: "2026-01-01T05:30:00Z",
  age_months: 14,
  latest_weight_kg: 32.5,
});

describe("AnimalsPage — typing while the page clamp navigates", () => {
  beforeEach(() => {
    // A deep link to a page that no longer exists: 20 animals is one page, so
    // page=3 is out of range and the clamp effect will re-home the list.
    nav.state.search = "page=3";
    nav.push.mockClear();
    nav.replace.mockClear();
  });

  it("keeps search text typed before the clamp's URL commits", async () => {
    let releaseList: (() => void) | undefined;
    const parked = new Promise<void>((resolve) => {
      releaseList = resolve;
    });
    let listCalls = 0;
    const all = Array.from({ length: 20 }, (_, index) => animal(index + 1));
    server.use(
      http.get("/api/animals", async ({ request }) => {
        listCalls += 1;
        // Hold the FIRST response so the operator can type while page 3 loads.
        if (listCalls === 1) await parked;
        const params = new URL(request.url).searchParams;
        const offset = Number(params.get("offset"));
        const limit = Number(params.get("limit"));
        const q = params.get("q");
        const rows = q ? all.filter((a) => a.tag_number.includes(q)) : all;
        return HttpResponse.json({
          animals: rows.slice(offset, offset + limit),
          total: rows.length,
        });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);

    const search = await screen.findByRole("searchbox", {
      name: "Search animals by tag",
    });
    // Typed while page 3 is still in flight — i.e. before the clamp dispatches.
    await user.type(search, "G7");
    expect(search).toHaveValue("G7");

    // The page-3 response lands: total 20 -> one page -> the clamp fires and
    // replaces the URL with page=1 and NO q (debouncedQ has not caught up).
    releaseList?.();
    await waitFor(() => expect(nav.replace).toHaveBeenCalled());

    // Let the clamp's URL commit through the params-sync effect AND let the
    // 300ms search debounce run, so what we assert is the settled state rather
    // than an intermediate render.
    await act(async () => {
      await settle(600);
    });

    // The load-bearing assertion: committing a URL that does not describe the
    // operator's text must not overwrite it.
    expect(search).toHaveValue("G7");

    // ...and the search actually reaches the server, rather than being stranded
    // by a debounce that believes the URL already matches.
    const issued = [...nav.replace.mock.calls, ...nav.push.mock.calls].some(([url]) =>
      String(url).includes("q=G7"),
    );
    expect(issued).toBe(true);
  });
});

describe("AnimalsPage — create dialog lifecycle", () => {
  beforeEach(() => {
    nav.state.search = "";
    nav.push.mockClear();
    nav.replace.mockClear();
  });

  it("does not reopen the form while a dismissed create request can still reset it", async () => {
    let createCalls = 0;
    let releaseCreate: (() => void) | undefined;
    const parked = new Promise<void>((resolve) => {
      releaseCreate = resolve;
    });
    server.use(
      http.get("/api/animals", () =>
        HttpResponse.json({ animals: [animal(1)], total: 1 }),
      ),
      http.post("/api/animals", async () => {
        createCalls += 1;
        await parked;
        return HttpResponse.json(animal(2), { status: 201 });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    await screen.findAllByText("G-001");

    await user.click(screen.getByRole("button", { name: "Add animal" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Save animal" }));
    await waitFor(() => expect(createCalls).toBe(1));
    expect(dialog.querySelector("fieldset")).toBeDisabled();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // A late success calls reset() and setOpen(false). Until it settles, a
    // second dialog must not be allowed to open and lose a newly typed draft.
    const trigger = screen.getByRole("button", { name: "Add animal" });
    expect(trigger).toBeDisabled();
    await user.click(trigger);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    releaseCreate?.();
    await waitFor(() => expect(trigger).toBeEnabled());
    expect(createCalls).toBe(1);
  });
});

describe("AnimalsPage — rows stay inert until a dispatched URL commits", () => {
  beforeEach(() => {
    nav.state.search = "";
    nav.push.mockClear();
    nav.replace.mockClear();
  });

  /** Park router.replace so a dispatched URL never commits until released. */
  function holdCommits() {
    nav.replace.mockImplementation(() => undefined);
  }

  function releaseCommits() {
    nav.replace.mockImplementation((url: string) => {
      nav.state.search = url.includes("?") ? url.slice(url.indexOf("?") + 1) : "";
    });
  }

  it("drops the search guard once the input settles back onto the committed term", async () => {
    server.use(
      http.get("/api/animals", () =>
        HttpResponse.json({ animals: [animal(1)], total: 1 }),
      ),
    );
    holdCommits();
    const user = userEvent.setup();
    renderWithProviders(<AnimalsPage />);
    const search = await screen.findByRole("searchbox", {
      name: "Search animals by tag",
    });
    await screen.findAllByText("G-001");

    // Dispatch a search URL and keep it uncommitted: the guard is raised and
    // one pending navigation is recorded.
    await user.type(search, "G77");
    await act(async () => {
      await settle(450);
    });
    expect(screen.getByText("Loading animals…")).toBeInTheDocument();

    // The dispatched URL commits while the operator has already cleared the
    // box: the params-sync effect preserves their edit and keeps the guard
    // raised via hasNewerSearchEdit (no pending entries left). Settle the
    // input back onto exactly the committed term inside one debounce window
    // — the one spelling whose debounce must CLEAR that stale guard.
    nav.state.search = "q=G77";
    await user.clear(search);
    await user.type(search, "G77");
    releaseCommits();

    await act(async () => {
      await settle(600);
    });
    // The guard dropped: rows are interactive content again, not a spinner.
    expect(screen.queryByText("Loading animals…")).not.toBeInTheDocument();
    expect(screen.getAllByText("G-001").length).toBeGreaterThan(0);
  });
});
