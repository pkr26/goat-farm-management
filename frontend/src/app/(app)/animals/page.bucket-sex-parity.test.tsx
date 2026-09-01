/**
 * Paths the other animals suites never execute: the create dialog's
 * bucket/sex parity guard (the mirror of the ck_animals_bucket_sex CHECK,
 * reachable when the sex flips in the same render pass as the submit), the
 * bucket picker's exclusion of the workflow-owned buckets, and the two page
 * navigation paths that only open when a click lands in the same render pass
 * as an earlier dispatch — the double-step latch and the same-URL
 * cancellation of a superseded filter navigation.
 */

import { act, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import AnimalsPage from "./page";

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
  sex: "F",
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

async function pickOption(user: User, trigger: HTMLElement, name: string) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

/** The Select commits its value on pointer-up, so replaying the whole gesture
 *  is what selects an option when the batch below cannot await userEvent. */
function commitOption(option: HTMLElement) {
  fireEvent.pointerDown(option, { pointerType: "mouse", button: 0, isPrimary: true });
  fireEvent.mouseDown(option);
  fireEvent.pointerUp(option, { pointerType: "mouse", button: 0, isPrimary: true });
  fireEvent.mouseUp(option);
  fireEvent.click(option);
}

describe("AnimalsPage bucket/sex parity guard", () => {
  let postCalls: number;

  beforeEach(() => {
    nav.state.search = "";
    nav.push.mockClear();
    nav.replace.mockClear();
    postCalls = 0;
    server.use(
      http.get("/api/animals", () =>
        HttpResponse.json({ animals: [animal(1)], total: 1 }),
      ),
      http.post("/api/animals", () => {
        postCalls += 1;
        return HttpResponse.json({ ...animal(2), id: 2 }, { status: 201 });
      }),
    );
  });

  /** Dialog switched to the owner-only historical import, audit reason filled. */
  async function openImportDialog(user: User) {
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("1 animal(s)");
    await user.click(screen.getByRole("button", { name: "Add animal" }));
    const dialog = await screen.findByRole("dialog");
    await pickOption(
      user,
      within(dialog).getByLabelText("Source *"),
      "Historical born-on-farm import",
    );
    await user.type(
      within(dialog).getByLabelText("Historical import reason *"),
      "Paper herd register import",
    );
    return dialog;
  }

  /**
   * Picking a sex-reserved bucket and then flipping the sex normally lets the
   * picker fall back to a legal bucket first. Submitting in the same render
   * pass as the flip is the one ordering that reaches the schema's own parity
   * guard — the mirror of backend/app/schemas/animals.py.
   */
  async function flipSexAndSubmit(dialog: HTMLElement, user: User, sex: string) {
    await user.click(within(dialog).getByLabelText("Sex *"));
    const option = await screen.findByRole("option", { name: sex });
    const form = dialog.querySelector("form") as HTMLFormElement;
    act(() => {
      commitOption(option);
      fireEvent.submit(form);
    });
  }

  function bucketField(dialog: HTMLElement): HTMLElement {
    return within(dialog).getByLabelText("Bucket *").closest("div") as HTMLElement;
  }

  it("refuses a doe-only bucket for a buck instead of POSTing it", async () => {
    const user = userEvent.setup();
    const dialog = await openImportDialog(user);
    await pickOption(user, within(dialog).getByLabelText("Bucket *"), "Female kids");

    await flipSexAndSubmit(dialog, user, "Male");

    expect(
      await within(bucketField(dialog)).findByText(
        "Only female animals may enter FEMALE_KIDS",
      ),
    ).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("names the bucket the guard rejected, so RESTING reads as doe-only too", async () => {
    const user = userEvent.setup();
    const dialog = await openImportDialog(user);
    await pickOption(user, within(dialog).getByLabelText("Bucket *"), "Resting");

    await flipSexAndSubmit(dialog, user, "Male");

    expect(
      await within(bucketField(dialog)).findByText(
        "Only female animals may enter RESTING",
      ),
    ).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("refuses a buck-only bucket for a doe instead of POSTing it", async () => {
    const user = userEvent.setup();
    const dialog = await openImportDialog(user);
    await pickOption(user, within(dialog).getByLabelText("Sex *"), "Male");
    await pickOption(user, within(dialog).getByLabelText("Bucket *"), "Male kids");

    await flipSexAndSubmit(dialog, user, "Female");

    expect(
      await within(bucketField(dialog)).findByText(
        "Only male animals may enter MALE_KIDS",
      ),
    ).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("never offers the workflow-owned buckets to a historical import", async () => {
    // PREGNANCY_*, DELIVERY and RECOVERY are entered by the breeding and
    // kidding workflows, which create the linked records an animal in those
    // buckets must have. Offering one here would file an orphan.
    const user = userEvent.setup();
    const dialog = await openImportDialog(user);
    // Options now carry the humanised enum labels.
    const workflowOnly = ["Pregnancy A", "Pregnancy B", "Delivery", "Recovery"];

    await user.click(within(dialog).getByLabelText("Bucket *"));
    expect(await screen.findByRole("option", { name: "Quarantine" })).toBeInTheDocument();
    for (const bucket of workflowOnly) {
      expect(screen.queryByRole("option", { name: bucket })).not.toBeInTheDocument();
    }

    await user.keyboard("{Escape}");
    await pickOption(user, within(dialog).getByLabelText("Sex *"), "Male");
    await user.click(within(dialog).getByLabelText("Bucket *"));
    expect(await screen.findByRole("option", { name: "Male kids" })).toBeInTheDocument();
    for (const bucket of workflowOnly) {
      expect(screen.queryByRole("option", { name: bucket })).not.toBeInTheDocument();
    }
  });
});

describe("AnimalsPage page steps dispatched in one render pass", () => {
  let seenParams: URLSearchParams[];

  beforeEach(() => {
    nav.state.search = "";
    nav.push.mockClear();
    nav.replace.mockClear();
    seenParams = [];
  });

  it("advances a single page when two Next clicks share a render pass", async () => {
    server.use(
      http.get("/api/animals", ({ request }) => {
        const params = new URL(request.url).searchParams;
        seenParams.push(params);
        const offset = Number(params.get("offset"));
        return HttpResponse.json({
          animals: [{ ...animal(offset + 1), tag_number: `G-${offset + 1}` }],
          total: 200,
        });
      }),
    );
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("Showing 1–50 of 200 animals");

    // Both clicks read the same render, so the second one still sees page 1
    // and an enabled button: only the in-flight latch stops it from pushing a
    // duplicate history entry for the page it is already navigating to.
    const next = screen.getByRole("button", { name: "Next" });
    act(() => {
      fireEvent.click(next);
      fireEvent.click(next);
    });

    expect(await screen.findByText("Showing 51–100 of 200 animals")).toBeInTheDocument();
    expect(nav.push).toHaveBeenCalledTimes(1);
    expect(nav.push).toHaveBeenCalledWith("/animals?page=2");
    expect(nav.state.search).toBe("page=2");
    expect(seenParams.at(-1)?.get("offset")).toBe("50");
  });

  it("cancels a superseded filter navigation back to the page URL already committed", async () => {
    // A deep link past the endpoint's offset ceiling clamps to MAX_PAGE, so
    // the URL names page 20002 while the list sits on 20001 — stepping
    // forward therefore targets the URL Next is already on.
    server.use(
      http.get("/api/animals", ({ request }) => {
        const params = new URL(request.url).searchParams;
        seenParams.push(params);
        const offset = Number(params.get("offset"));
        return HttpResponse.json({
          animals: [{ ...animal(offset + 1), tag_number: `G-${offset + 1}` }],
          total: 1_100_000,
        });
      }),
    );
    const queryClient = createTestQueryClient();
    queryClient.setDefaultOptions({
      queries: { retry: false, refetchOnWindowFocus: false, staleTime: 60_000 },
      mutations: { retry: false },
    });
    nav.state.search = "bucket=FEMALE_KIDS&page=20002";
    const user = userEvent.setup();
    const view = renderWithProviders(<AnimalsPage />, queryClient);
    await screen.findByText("Showing 1000001–1000050 of 1100000 animals");

    // Warm the FEMALE_KIDS page-20002 rows and then leave that filter behind,
    // so the cancellation below settles from cache rather than a refetch and
    // the fence is the only thing that could still hide the list.
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("Showing 1000051–1000100 of 1100000 animals");
    nav.state.search = "page=20002";
    view.rerender(<AnimalsPage />);
    await screen.findByText("Showing 1000001–1000050 of 1100000 animals");
    nav.push.mockClear();
    nav.replace.mockClear();

    await user.click(screen.getByRole("combobox", { name: "Filter animals by bucket" }));
    const femaleKids = await screen.findByRole("option", { name: "Female kids" });
    const next = screen.getByRole("button", { name: "Next" });
    // The page step lands in the same render pass as the filter replacement it
    // supersedes. Next discards the older action but has no commit to deliver
    // for the URL it is already on, so the page must cancel back to it.
    act(() => {
      commitOption(femaleKids);
      fireEvent.click(next);
    });

    // Replaced, never pushed: cancelling back to the committed URL must not
    // leave a second history entry behind.
    expect(nav.replace.mock.calls.map(([url]) => url)).toEqual([
      "/animals?bucket=FEMALE_KIDS",
      "/animals?page=20002",
    ]);
    expect(nav.push).not.toHaveBeenCalled();
    expect(nav.state.search).toBe("page=20002");

    // Dropping the superseded registry and lowering the fence are both part of
    // the cancellation: no later commit arrives to repair either one.
    expect(
      screen.getByText("Showing 1000051–1000100 of 1100000 animals"),
    ).toBeInTheDocument();
    // The tag is rendered by both the mobile card list and the table.
    expect(screen.getAllByText("G-1000051")[0]).toBeInTheDocument();
    expect(screen.queryByText("Loading animals…")).not.toBeInTheDocument();
    expect(screen.queryByText("Updating animals…")).not.toBeInTheDocument();
    await new Promise((resolve) => window.setTimeout(resolve, 350));
    expect(screen.queryByText("Loading animals…")).not.toBeInTheDocument();
    expect(screen.queryByText("Updating animals…")).not.toBeInTheDocument();
    expect(screen.getAllByText("G-1000051")[0]).toBeInTheDocument();
  });
});
