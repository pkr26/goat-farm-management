import { fireEvent, screen, waitFor } from "@testing-library/react";
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
  return {
    state,
    push,
    replace,
    router: { push, replace, prefetch: vi.fn() },
  };
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
  purchase_date: null,
  purchase_price: null,
  seller_name: null,
  cull_candidate: false,
  notes: null,
  created_at: "2026-01-01T05:30:00Z",
  age_months: 14,
  latest_weight_kg: 32.5,
});

function hrefParams(mock: ReturnType<typeof vi.fn>): URLSearchParams {
  const href = String(mock.mock.calls.at(-1)?.[0] ?? "");
  return new URL(href, "https://goatfarm.invalid").searchParams;
}

function paramsKeyFromHref(href: string): string {
  const questionMark = href.indexOf("?");
  return questionMark === -1 ? "" : href.slice(questionMark + 1);
}

async function chooseOption(user: ReturnType<typeof userEvent.setup>, label: string, option: string) {
  await user.click(screen.getByRole("combobox", { name: label }));
  await user.click(await screen.findByRole("option", { name: option }));
}

describe("AnimalsPage finite pagination", () => {
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
  });

  function usePagedAnimals(animals: ReturnType<typeof animal>[]) {
    server.use(
      http.get("/api/animals", ({ request }) => {
        const params = new URL(request.url).searchParams;
        seenParams.push(params);
        const limit = Number(params.get("limit"));
        const offset = Number(params.get("offset"));
        return HttpResponse.json({
          animals: animals.slice(offset, offset + limit),
          total: animals.length,
        });
      }),
    );
  }

  it("always requests 50 rows and renders an honest partial final page", async () => {
    usePagedAnimals(Array.from({ length: 55 }, (_, index) => animal(index + 1)));
    renderWithProviders(<AnimalsPage />);

    expect(
      await screen.findByText("Page 1 of 2 · Showing 1–50 of 55"),
    ).toBeInTheDocument();
    expect(seenParams[0].get("limit")).toBe("50");
    expect(seenParams[0].get("offset")).toBe("0");
    expect(seenParams[0].get("include_all_statuses")).toBe("true");
    expect(screen.getByText("G-050")).toBeInTheDocument();
    expect(screen.queryByText("G-051")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();

    const nextButton = screen.getByRole("button", { name: "Next page" });
    fireEvent.click(nextButton);
    fireEvent.click(nextButton);

    expect(
      await screen.findByText("Page 2 of 2 · Showing 51–55 of 55"),
    ).toBeInTheDocument();
    expect(seenParams.at(-1)?.get("limit")).toBe("50");
    expect(seenParams.at(-1)?.get("offset")).toBe("50");
    expect(screen.getByText("G-055")).toBeInTheDocument();
    expect(screen.queryByText("G-001")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    expect(nav.push).toHaveBeenLastCalledWith("/animals?page=2");
    expect(nav.push).toHaveBeenCalledTimes(1);
  });

  it("unlocks pagination when a destination page is already fresh in the cache", async () => {
    const user = userEvent.setup();
    usePagedAnimals(Array.from({ length: 55 }, (_, index) => animal(index + 1)));
    const queryClient = createTestQueryClient();
    queryClient.setDefaultOptions({
      queries: { retry: false, refetchOnWindowFocus: false, staleTime: 60_000 },
      mutations: { retry: false },
    });
    renderWithProviders(<AnimalsPage />, queryClient);

    await screen.findByText("Page 1 of 2 · Showing 1–50 of 55");
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByText("Page 2 of 2 · Showing 51–55 of 55");
    await user.click(screen.getByRole("button", { name: "Previous page" }));
    await screen.findByText("Page 1 of 2 · Showing 1–50 of 55");

    // Page 2 is still fresh, so React Query does not toggle isFetching while
    // restoring it. The local double-click latch must nevertheless release.
    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(
      await screen.findByText("Page 2 of 2 · Showing 51–55 of 55"),
    ).toBeInTheDocument();
    expect(nav.push).toHaveBeenCalledTimes(3);
  });

  it("restores a filtered page from a deep link and browser history", async () => {
    const user = userEvent.setup();
    const animals = Array.from({ length: 120 }, (_, index) => animal(index + 1));
    usePagedAnimals(animals);
    nav.state.search = "bucket=RESTING&sex=M&status=SOLD&q=G-77&page=2";
    const view = renderWithProviders(<AnimalsPage />);

    expect(
      await screen.findByText("Page 2 of 3 · Showing 51–100 of 120"),
    ).toBeInTheDocument();
    expect(seenParams[0].get("bucket")).toBe("RESTING");
    expect(seenParams[0].get("sex")).toBe("M");
    expect(seenParams[0].get("status")).toBe("SOLD");
    expect(seenParams[0].get("q")).toBe("G-77");
    expect(seenParams[0].get("offset")).toBe("50");
    expect(screen.getByRole("searchbox", { name: "Search animals by tag" })).toHaveValue(
      "G-77",
    );

    await user.click(screen.getByRole("button", { name: "Previous page" }));
    expect(await screen.findByText("Page 1 of 3 · Showing 1–50 of 120")).toBeInTheDocument();
    const previousUrl = hrefParams(nav.push);
    expect(previousUrl.get("bucket")).toBe("RESTING");
    expect(previousUrl.get("sex")).toBe("M");
    expect(previousUrl.get("status")).toBe("SOLD");
    expect(previousUrl.get("q")).toBe("G-77");
    expect(previousUrl.get("page")).toBeNull();

    nav.state.search = "bucket=RESTING&sex=M&status=SOLD&q=G-77&page=2";
    view.rerender(<AnimalsPage />);
    expect(
      await screen.findByText("Page 2 of 3 · Showing 51–100 of 120"),
    ).toBeInTheDocument();
    expect(seenParams.at(-1)?.get("offset")).toBe("50");
  });

  it("resets to page one and preserves the other URL filters when a filter changes", async () => {
    const user = userEvent.setup();
    usePagedAnimals(Array.from({ length: 100 }, (_, index) => animal(index + 1)));
    nav.state.search = "sex=M&status=ACTIVE&q=G&page=2";
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("Page 2 of 2 · Showing 51–100 of 100");

    await chooseOption(user, "Filter animals by bucket", "FEMALE KIDS");

    expect(await screen.findByText("Page 1 of 2 · Showing 1–50 of 100")).toBeInTheDocument();
    expect(seenParams.at(-1)?.get("offset")).toBe("0");
    expect(seenParams.at(-1)?.get("bucket")).toBe("FEMALE_KIDS");
    const nextUrl = hrefParams(nav.replace);
    expect(nextUrl.get("bucket")).toBe("FEMALE_KIDS");
    expect(nextUrl.get("sex")).toBe("M");
    expect(nextUrl.get("status")).toBe("ACTIVE");
    expect(nextUrl.get("q")).toBe("G");
    expect(nextUrl.get("page")).toBeNull();
  });

  it("debounces search into the URL and resets its API offset to zero", async () => {
    const user = userEvent.setup();
    usePagedAnimals(Array.from({ length: 100 }, (_, index) => animal(index + 1)));
    nav.state.search = "bucket=RESTING&page=2";
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("Page 2 of 2 · Showing 51–100 of 100");

    await user.type(screen.getByRole("searchbox", { name: "Search animals by tag" }), "G-9");

    await waitFor(() => expect(seenParams.at(-1)?.get("q")).toBe("G-9"));
    expect(seenParams.at(-1)?.get("offset")).toBe("0");
    const nextUrl = hrefParams(nav.replace);
    expect(nextUrl.get("bucket")).toBe("RESTING");
    expect(nextUrl.get("q")).toBe("G-9");
    expect(nextUrl.get("page")).toBeNull();
  });

  it("does not let an older URL replacement overwrite newer search typing", async () => {
    const user = userEvent.setup();
    usePagedAnimals(Array.from({ length: 55 }, (_, index) => animal(index + 1)));
    nav.state.deferReplace = true;
    const view = renderWithProviders(<AnimalsPage />);
    await screen.findByText("Page 1 of 2 · Showing 1–50 of 55");
    const search = screen.getByRole("searchbox", { name: "Search animals by tag" });

    await user.type(search, "G");
    await waitFor(() => expect(nav.state.deferredReplacements).toHaveLength(1));
    await user.type(search, "-9");
    expect(search).toHaveValue("G-9");

    // Commit the older q=G navigation after the operator has continued
    // typing, matching a slow same-route Next.js replacement.
    const olderUrl = nav.state.deferredReplacements[0];
    nav.state.search = olderUrl.includes("?")
      ? olderUrl.slice(olderUrl.indexOf("?") + 1)
      : "";
    view.rerender(<AnimalsPage />);

    await waitFor(() => expect(search).toHaveValue("G-9"));
  });

  it("does not let a filter replacement that commits during typing overwrite the edit", async () => {
    const user = userEvent.setup();
    usePagedAnimals(Array.from({ length: 55 }, (_, index) => animal(index + 1)));
    nav.state.deferReplace = true;
    const view = renderWithProviders(<AnimalsPage />);
    await screen.findByText("Page 1 of 2 · Showing 1–50 of 55");

    await chooseOption(user, "Filter animals by bucket", "FEMALE KIDS");
    expect(nav.state.deferredReplacements).toHaveLength(1);
    const delayedFilterUrl = nav.state.deferredReplacements[0];

    const search = screen.getByRole("searchbox", { name: "Search animals by tag" });
    fireEvent.change(search, { target: { value: "G-9" } });
    expect(search).toHaveValue("G-9");

    // The filter URL commits after typing starts but before the debounce sends
    // a newer navigation. This is the real stale-commit window in Next 16;
    // once a newer navigation is dispatched, Next discards this pending one.
    nav.state.search = paramsKeyFromHref(delayedFilterUrl);
    view.rerender(<AnimalsPage />);

    await waitFor(() => expect(search).toHaveValue("G-9"));
    nav.state.deferReplace = false;
    await waitFor(() => {
      const latest = new URLSearchParams(nav.state.search);
      expect(latest.get("bucket")).toBe("FEMALE_KIDS");
      expect(latest.get("q")).toBe("G-9");
    });
  });

  it("does not let a page push that commits during typing overwrite the edit", async () => {
    const user = userEvent.setup();
    usePagedAnimals(Array.from({ length: 55 }, (_, index) => animal(index + 1)));
    nav.state.deferPush = true;
    const view = renderWithProviders(<AnimalsPage />);
    await screen.findByText("Page 1 of 2 · Showing 1–50 of 55");

    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(nav.state.deferredPushes).toHaveLength(1);
    const delayedPageUrl = nav.state.deferredPushes[0];

    const search = screen.getByRole("searchbox", { name: "Search animals by tag" });
    fireEvent.change(search, { target: { value: "G-9" } });
    expect(search).toHaveValue("G-9");

    // The page URL commits after typing starts but before the debounced search
    // replacement supersedes it. Preserve q and then canonicalize page one.
    nav.state.search = paramsKeyFromHref(delayedPageUrl);
    view.rerender(<AnimalsPage />);

    await waitFor(() => expect(search).toHaveValue("G-9"));
    await waitFor(() => {
      const latest = new URLSearchParams(nav.state.search);
      expect(latest.get("q")).toBe("G-9");
      expect(latest.get("page")).toBeNull();
    });
  });

  it("recovers an empty out-of-range filtered deep link to page one", async () => {
    usePagedAnimals([]);
    nav.state.search = "status=SOLD&page=4";
    renderWithProviders(<AnimalsPage />);

    expect(await screen.findByText("No animals match these filters.")).toBeInTheDocument();
    expect(screen.getByText("Page 1 of 1 · Showing 0–0 of 0")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    expect(seenParams.some((params) => params.get("offset") === "150")).toBe(true);
    expect(seenParams.at(-1)?.get("offset")).toBe("0");
    const recoveredUrl = hrefParams(nav.replace);
    expect(recoveredUrl.get("status")).toBe("SOLD");
    expect(recoveredUrl.get("page")).toBeNull();
  });

  it("hides stale page rows while an invalidated list is refetching", async () => {
    let refreshPending = false;
    let finishRefresh: (() => void) | undefined;
    server.use(
      http.get("/api/animals", ({ request }) => {
        seenParams.push(new URL(request.url).searchParams);
        if (!refreshPending) {
          return HttpResponse.json({ animals: [animal(1)], total: 1 });
        }
        return new Promise<Response>((resolve) => {
          finishRefresh = () =>
            resolve(HttpResponse.json({ animals: [animal(2)], total: 1 }));
        });
      }),
    );
    const { queryClient } = renderWithProviders(<AnimalsPage />);
    await screen.findByText("G-001");

    refreshPending = true;
    const invalidation = queryClient.invalidateQueries({ queryKey: ["/api/animals"] });

    expect(await screen.findByText("Updating animals…")).toBeInTheDocument();
    expect(screen.queryByText("G-001")).not.toBeInTheDocument();
    finishRefresh?.();
    await invalidation;
    expect(await screen.findByText("G-002")).toBeInTheDocument();
  });
});
