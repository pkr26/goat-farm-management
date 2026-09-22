/**
 * Screening review page — the marquee AI feature's only UI, which sat at 0%
 * coverage behind the global thresholds (B8, 2026-09-21 audit). Covers the
 * provider scoreboard arithmetic, the queue table and its accessible detail
 * trigger, the vet review flow (success + optimistic-concurrency conflict),
 * status filters, deep-link detail failure, list failure, and the dataset
 * export download.
 */

import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server, permissionsHandler } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";
import { settle } from "@/test/settle";

import ScreeningPage from "./page";

const sonner = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("sonner", () => ({ toast: { success: sonner.success, error: sonner.error } }));

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
  usePathname: () => "/screening",
  useSearchParams: () => new URLSearchParams(nav.state.search),
  useParams: () => ({}),
}));

const STATS = {
  window_days: 30,
  providers: [
    {
      provider: "anthropic",
      model: "claude-gate-1",
      gate_runs: 10,
      gate_flagged: 3,
      gate_errors: 1,
      avg_gate_latency_ms: 1500,
      cross_checks: 4,
      cross_check_agreements: 2,
      findings_confirmed: 5,
      findings_rejected: 1,
      findings_pending: 2,
    },
  ],
};

const ROW = (id: number) => ({
  id,
  status: id % 2 === 0 ? "FLAGGED" : "HEALTHY",
  bucket: "BREEDING",
  batch_id: 7,
  s3_key: `raw/1/2026-09-18/BREEDING/7-${id}.jpg`,
  captured_date: "2026-09-18",
  width: 2000,
  height: 1000,
  byte_size: 400_000,
  error: null,
  created_at: "2026-09-18T05:30:00Z",
  latest_run:
    id % 2 === 0
      ? {
          id: 100 + id,
          image_id: id,
          crop_id: null,
          stage: "GATE",
          run_status: "OK",
          verdict: "flagged",
          confidence: "0.87",
          provider: "anthropic",
          model: "claude-gate-1",
          prompt_version: "gate-1",
          latency_ms: 1400,
          detail: null,
          error: null,
          created_at: "2026-09-18T05:31:00Z",
        }
      : null,
  pending_findings: id % 2 === 0 ? 2 : 0,
});

const DETAIL = {
  id: 2,
  status: "FLAGGED",
  bucket: "BREEDING",
  batch_id: 7,
  s3_key: "raw/1/2026-09-18/BREEDING/7-2.jpg",
  captured_date: "2026-09-18",
  width: 2000,
  height: 1000,
  byte_size: 400_000,
  error: null,
  created_at: "2026-09-18T05:30:00Z",
  image_url: "https://signed.example/normalized.jpg",
  crops: [
    {
      id: 21,
      crop_index: 0,
      box_x: 10,
      box_y: 10,
      box_w: 400,
      box_h: 400,
      status: "FLAGGED",
      error: null,
      created_at: "2026-09-18T05:31:00Z",
      image_url: "https://signed.example/crop.jpg",
    },
  ],
  runs: [
    {
      id: 101,
      image_id: 2,
      crop_id: 21,
      stage: "GATE",
      run_status: "OK",
      verdict: "flagged",
      confidence: "0.87",
      provider: "anthropic",
      model: "claude-gate-1",
      prompt_version: "gate-1",
      latency_ms: 1400,
      detail: null,
      error: null,
      created_at: "2026-09-18T05:31:00Z",
    },
    {
      id: 102,
      image_id: 2,
      crop_id: 21,
      stage: "CROSS_CHECK",
      run_status: "OK",
      verdict: "healthy",
      confidence: "0.71",
      provider: "openai_compatible",
      model: "glm-gate",
      prompt_version: "gate-1",
      latency_ms: 900,
      detail: { agrees: false },
      error: null,
      created_at: "2026-09-18T05:32:00Z",
    },
  ],
  findings: [
    {
      id: 51,
      run_id: 101,
      crop_id: 21,
      region: "lips",
      label: "Orf lesions",
      confidence: "0.87",
      severity: "moderate",
      note: "Crusted lesions on the lower lip.",
      status: "PENDING_REVIEW",
      review_note: null,
      reviewed_at: null,
      created_at: "2026-09-18T05:31:00Z",
    },
  ],
};

function listPayload() {
  return {
    images: [ROW(1), ROW(2)],
    total: 2,
    limit: 25,
    offset: 0,
  };
}

function installHappyHandlers() {
  server.use(
    http.get("/api/screening/stats", () => HttpResponse.json(STATS)),
    http.get("/api/screening/images", () => HttpResponse.json(listPayload())),
    http.get("/api/screening/images/2", () => HttpResponse.json(DETAIL)),
  );
}

/** router.replace in the navigation mock updates the URL string but nothing
 *  schedules a React render for it; a rerender plays the role of the App
 *  Router committing the soft navigation. */
function renderPage() {
  const view = renderWithProviders(<ScreeningPage />, createTestQueryClient());
  return {
    ...view,
    async commitNavigation() {
      await act(async () => {
        view.rerender(<ScreeningPage />);
      });
    },
  };
}

beforeAll(() => {
  Element.prototype.scrollIntoView = () => {};
});

beforeEach(() => {
  nav.state.search = "";
  nav.push.mockClear();
  nav.replace.mockClear();
  sonner.success.mockClear();
  sonner.error.mockClear();
});

describe("ScreeningPage", () => {
  it("renders the provider scoreboard with derived rates and latency", async () => {
    installHappyHandlers();
    renderPage();

    expect(await screen.findByText("anthropic")).toBeInTheDocument();
    // 3 flagged of 10 gate runs → 30%; 2 of 4 cross-checks agree → 50%.
    expect(screen.getByText("30%")).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();
    expect(screen.getByText("1.5s")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  it("renders the review queue rows with status, pending-findings badge and model", async () => {
    installHappyHandlers();
    renderPage();

    expect((await screen.findByRole("button", { name: "Open screening photo #2 details" }))).toBeInTheDocument();
    expect(screen.getByText("Flagged")).toBeInTheDocument();
    expect(screen.getByText("Healthy")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument(); // pending findings badge
    expect(screen.getByText(/anthropic · claude-gate-1/)).toBeInTheDocument();
  });

  it("shows the empty scoreboard note when no provider has runs", async () => {
    server.use(
      http.get("/api/screening/stats", () => HttpResponse.json({ window_days: 30, providers: [] })),
      http.get("/api/screening/images", () => HttpResponse.json(listPayload())),
    );
    renderPage();

    expect(await screen.findByText("No model runs recorded yet.")).toBeInTheDocument();
  });

  it("opens the detail view through the row's accessible button and renders the full review payload", async () => {
    installHappyHandlers();
    const view = renderPage();
    const user = userEvent.setup();

    await user.click((await screen.findByRole("button", { name: "Open screening photo #2 details" })));
    await view.commitNavigation();

    expect(await screen.findByText("Photo review")).toBeInTheDocument();
    // Crop chip, finding with localized severity + rounded confidence,
    // cross-check disagreement badge, and the run's provider/model line.
    expect((await screen.findAllByText("Goat 1")).length).toBeGreaterThan(0);
    expect(screen.getByText("Orf lesions")).toBeInTheDocument();
    expect(screen.getByText("Moderate")).toBeInTheDocument();
    expect(screen.getAllByText("confidence 87%").length).toBeGreaterThan(0);
    expect(screen.getByText("Models disagree")).toBeInTheDocument();
    expect(screen.getByText(/openai_compatible · glm-gate/)).toBeInTheDocument();
    expect(nav.state.search).toContain("image_id=2");
  });

  it("confirms a finding, refetches the board and toasts the verdict", async () => {
    installHappyHandlers();
    const reviewBodies: object[] = [];
    server.use(
      http.post("/api/screening/findings/51/review", async ({ request }) => {
        reviewBodies.push((await request.json()) as object);
        return HttpResponse.json({ ok: true });
      }),
    );
    const view = renderPage();
    const user = userEvent.setup();

    await user.click((await screen.findByRole("button", { name: "Open screening photo #2 details" })));
    await view.commitNavigation();
    await user.click(await screen.findByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(reviewBodies).toHaveLength(1));
    expect(reviewBodies[0]).toEqual({ status: "CONFIRMED", expected_status: "PENDING_REVIEW" });
  });

  it("surfaces the optimistic-concurrency conflict as its own toast", async () => {
    installHappyHandlers();
    server.use(
      http.post("/api/screening/findings/51/review", () =>
        HttpResponse.json({ detail: "Finding already reviewed" }, { status: 409 }),
      ),
    );
    const view = renderPage();
    const user = userEvent.setup();

    await user.click((await screen.findByRole("button", { name: "Open screening photo #2 details" })));
    await view.commitNavigation();
    await user.click(await screen.findByRole("button", { name: "Reject" }));

    await waitFor(() =>
      expect(sonner.error).toHaveBeenCalledWith(
        "Already reviewed — refresh to see the current status.",
      ),
    );
  });

  it("applies the status filter through URL state", async () => {
    installHappyHandlers();
    renderPage();
    const user = userEvent.setup();

    await screen.findByText("Flagged only");
    await user.click(screen.getByRole("button", { name: "Flagged only" }));

    expect(nav.state.search).toContain("status=FLAGGED");
  });

  it("offers retry when the queue list fails to load", async () => {
    server.use(
      http.get("/api/screening/stats", () => HttpResponse.json(STATS)),
      http.get("/api/screening/images", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    renderPage();

    expect(await screen.findByText("Retry")).toBeInTheDocument();
  });

  it("explains a dead deep-linked detail and offers a way back", async () => {
    server.use(
      http.get("/api/screening/stats", () => HttpResponse.json(STATS)),
      http.get("/api/screening/images", () => HttpResponse.json(listPayload())),
      http.get("/api/screening/images/99", () =>
        HttpResponse.json({ detail: "Screening image not found" }, { status: 404 }),
      ),
    );
    nav.state.search = "image_id=99";
    renderPage();

    expect(await screen.findByText("Screening image not found")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back to list" })).toBeInTheDocument();
  });

  it("downloads the dataset export and announces the record count", async () => {
    installHappyHandlers();
    server.use(
      http.get("/api/screening/export", () =>
        HttpResponse.json({
          farm_id: 1,
          generated_at: "2026-09-19T05:30:00Z",
          record_count: 2,
          records: [],
        }),
      ),
    );
    // jsdom ships neither static; add them on the real URL class so the
    // rest of the app (and the URL constructor) keeps working.
    const urlStatics = URL as unknown as {
      createObjectURL?: unknown;
      revokeObjectURL?: unknown;
    };
    const createObjectURL = vi.fn(() => "blob:mock");
    const revokeObjectURL = vi.fn();
    urlStatics.createObjectURL = createObjectURL;
    urlStatics.revokeObjectURL = revokeObjectURL;
    const anchorClick = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    try {
      renderPage();
      const user = userEvent.setup();

      await user.click(await screen.findByRole("button", { name: "Export dataset" }));

      await waitFor(() => expect(anchorClick).toHaveBeenCalled());
      expect(createObjectURL).toHaveBeenCalled();
      expect(revokeObjectURL).toHaveBeenCalled();
      await waitFor(() =>
        expect(sonner.success).toHaveBeenCalledWith("Dataset exported (2 records)."),
      );
    } finally {
      delete urlStatics.createObjectURL;
      delete urlStatics.revokeObjectURL;
      anchorClick.mockRestore();
    }
  });

  it("hides the export control from a health.view-only role", async () => {
    installHappyHandlers();
    server.use(permissionsHandler(["health.view"]));
    renderPage();

    (await screen.findByRole("button", { name: "Open screening photo #2 details" }));
    expect(screen.queryByRole("button", { name: "Export dataset" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
  });

  it("retries the list from the failure state", async () => {
    let failures = 0;
    server.use(
      http.get("/api/screening/stats", () => HttpResponse.json(STATS)),
      http.get("/api/screening/images", () => {
        failures += 1;
        if (failures === 1) return HttpResponse.json({ detail: "boom" }, { status: 500 });
        return HttpResponse.json(listPayload());
      }),
    );
    renderPage();

    const retry = await screen.findByRole("button", { name: "Retry" });
    fireEvent.click(retry);
    await settle(50);
    expect((await screen.findByRole("button", { name: "Open screening photo #2 details" }))).toBeInTheDocument();
  });
});
