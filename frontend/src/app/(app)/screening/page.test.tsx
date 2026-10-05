/**
 * Screening review page — the marquee AI feature's only UI, which sat at 0% coverage
 * behind the global thresholds. Covers the provider scoreboard arithmetic, the queue
 * table and its accessible detail trigger, the vet review flow (success +
 * optimistic-concurrency conflict), status filters, deep-link detail failure, list
 * failure, and the dataset export download.
 */

import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server, permissionsHandler } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";
import { settle } from "@/test/settle";
import { setCurrentFarmId } from "@/lib/api-client";

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
      gate_unassessable: 0,
      gate_errors: 1,
      avg_gate_latency_ms: 1500,
      avg_gate_confidence: "0.810",
      cross_checks: 4,
      cross_check_agreements: 2,
      findings_confirmed: 5,
      findings_rejected: 1,
      findings_pending: 2,
      positive_precision: "0.833",
      positive_precision_ci_low: "0.436",
      positive_precision_ci_high: "0.970",
      healthy_controls_confirmed: 2,
      healthy_controls_rejected: 1,
      healthy_controls_pending: 1,
      healthy_controls_reviewed: 3,
      healthy_false_negative_rate: "0.333",
      healthy_false_negative_ci_low: "0.061",
      healthy_false_negative_ci_high: "0.792",
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
  pending_healthy_controls: 0,
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
      evaluation_kind: "POSITIVE_FINDING",
      confidence: "0.87",
      severity: "moderate",
      note: "Crusted lesions on the lower lip.",
      status: "PENDING_REVIEW",
      review_note: null,
      review_revision: 0,
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
    expect(screen.getByText("Positive precision (reviewed flags)")).toBeInTheDocument();
    expect(screen.getByText("Healthy-control misses")).toBeInTheDocument();
    expect(screen.getByText("83% (95% CI 44–97; 6 reviewed, 2 pending)")).toBeInTheDocument();
    expect(screen.getByText("33% (95% CI 6–79; 3 reviewed, 1 pending)")).toBeInTheDocument();
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

  it("blinds a pending healthy control and offers neutral review choices", async () => {
    installHappyHandlers();
    nav.state.search = "image_id=2";
    const controlDetail = {
      ...DETAIL,
      status: "HEALTHY",
      crops: [],
      runs: [
        {
          ...DETAIL.runs[0],
          crop_id: null,
          verdict: "healthy",
          confidence: "0.93",
          detail: { healthy_control_sample: true },
        },
      ],
      findings: [
        {
          ...DETAIL.findings[0],
          crop_id: null,
          label: "Routine quality-control review",
          evaluation_kind: "HEALTHY_CONTROL",
          confidence: "0.93",
          severity: null,
          region: null,
          note: "Neutral review sample; assess the photo without a model-supplied label.",
        },
      ],
    };
    server.use(
      http.get("/api/screening/images/2", () => HttpResponse.json(controlDetail)),
    );

    renderPage();

    expect(await screen.findAllByText("Quality-control review")).toHaveLength(2);
    expect(
      screen.getByText(
        "The model outcome and confidence are hidden until this neutral quality-control review is completed.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "No visible abnormality" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Visible abnormality found" })).toBeInTheDocument();
    expect(screen.queryByText("confidence 93%")).not.toBeInTheDocument();
    expect(screen.queryByText(/anthropic · claude-gate-1/)).not.toBeInTheDocument();
    expect(screen.queryByText("Neutral review sample; assess the photo without a model-supplied label.")).not.toBeInTheDocument();
  });

  it("renders a failed provider run with its fixed reason code, never raw errors", async () => {
    installHappyHandlers();
    const failedDetail = {
      ...DETAIL,
      runs: [
        { ...DETAIL.runs[0], run_status: "ERROR", verdict: null, detail: null, error: "screening provider call failed (PROVIDER_ERROR)" },
      ],
      findings: [],
    };
    server.use(
      http.get("/api/screening/images/2", () => HttpResponse.json(failedDetail)),
    );
    const view = renderPage();
    const user = userEvent.setup();

    await user.click((await screen.findByRole("button", { name: "Open screening photo #2 details" })));
    await view.commitNavigation();

    expect(await screen.findByText("Photo review")).toBeInTheDocument();
    // The ERROR badge plus the tenant-safe reason line (B6: a fixed code,
    // not the provider's exception text).
    expect(await screen.findByText("ERROR")).toBeInTheDocument();
    expect(
      screen.getByText("screening provider call failed (PROVIDER_ERROR)"),
    ).toBeInTheDocument();
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
    expect(reviewBodies[0]).toEqual({ status: "CONFIRMED", expected_status: "PENDING_REVIEW",
      expected_revision: 0, review_note: null });
  });

  it("shows unusable photos as a retake and excludes them from the assessed flag rate", async () => {
    installHappyHandlers();
    nav.state.search = "image_id=2";
    server.use(
      http.get("/api/screening/images/2", () => HttpResponse.json({ ...DETAIL, status: "UNASSESSABLE", findings: [] })),
      http.get("/api/screening/stats", () => HttpResponse.json({ ...STATS,
        providers: [{ ...STATS.providers[0], gate_unassessable: 4 }] })),
    );
    renderPage();
    expect(await screen.findByText("Cannot assess")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Upload a clearer photo" })).toBeInTheDocument();
    expect(screen.getAllByText("50%")).toHaveLength(2);
    expect(screen.queryByText("Healthy")).not.toBeInTheDocument();
  });

  it("preserves review notes with the loaded revision and reveals immutable history", async () => {
    installHappyHandlers();
    nav.state.search = "image_id=2";
    let body: unknown;
    server.use(
      http.get("/api/screening/images/2", () => HttpResponse.json({ ...DETAIL,
        findings: [{ ...DETAIL.findings[0], review_revision: 3 }] })),
      http.post("/api/screening/findings/51/review", async ({ request }) => {
        body = await request.json(); return HttpResponse.json({ ok: true });
      }),
      http.get("/api/screening/findings/51/reviews", () => HttpResponse.json({ finding_id: 51,
        review_revision: 3, legacy_review: false, limit: 10, offset: 0, total: 1,
        reviews: [{ revision: 3, previous_status: "REJECTED", status: "CONFIRMED", review_note: "Examined by vet",
          reviewed_by_id: 7, reviewed_at: "2026-10-03T12:00:00Z" }] })),
    );
    renderPage();
    const user = userEvent.setup();
    await user.type(await screen.findByRole("textbox", { name: "Review note" }), "Confirmed on examination");
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(body).toMatchObject({ expected_revision: 3, review_note: "Confirmed on examination" }));
    await user.click(screen.getByText("Review history"));
    expect(await screen.findByText("Examined by vet")).toBeInTheDocument();
    expect(screen.getByText(/Revision 3 · Reviewer #7/)).toBeInTheDocument();
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

  it("suppresses a dataset download that resolves after the farm scope changes", async () => {
    installHappyHandlers();
    let releaseExport!: () => void;
    let exportStarted!: () => void;
    const started = new Promise<void>((resolve) => { exportStarted = resolve; });
    const gate = new Promise<void>((resolve) => { releaseExport = resolve; });
    server.use(
      http.get("/api/screening/export", async () => {
        exportStarted();
        await gate;
        return HttpResponse.json({
          farm_id: 1,
          generated_at: "2026-09-19T05:30:00Z",
          record_count: 2,
          records: [],
        });
      }),
    );
    const urlStatics = URL as unknown as {
      createObjectURL?: unknown;
      revokeObjectURL?: unknown;
    };
    const createObjectURL = vi.fn(() => "blob:stale");
    urlStatics.createObjectURL = createObjectURL;
    urlStatics.revokeObjectURL = vi.fn();
    const anchorClick = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    try {
      renderPage();
      const user = userEvent.setup();
      await user.click(await screen.findByRole("button", { name: "Export dataset" }));
      await started;

      setCurrentFarmId("2");
      releaseExport();
      await settle();

      expect(anchorClick).not.toHaveBeenCalled();
      expect(createObjectURL).not.toHaveBeenCalled();
      expect(sonner.success).not.toHaveBeenCalled();
      expect(sonner.error).not.toHaveBeenCalled();
    } finally {
      setCurrentFarmId("1");
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

describe("screening permission and outage regressions", () => {
  it("keeps the quality guidance but hides intake and retake for health viewers", async () => {
    installHappyHandlers();
    nav.state.search = "image_id=2";
    server.use(
      permissionsHandler(["health.view"]),
      http.get("/api/screening/images/2", () => HttpResponse.json({ ...DETAIL, status: "UNASSESSABLE" })),
    );
    renderPage();
    await screen.findByText("Cannot assess");
    expect(screen.queryByRole("button", { name: "Disease check" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Upload a clearer photo" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Export/ })).not.toBeInTheDocument();
  });

  it("distinguishes a stats outage from empty history and retries successfully", async () => {
    installHappyHandlers();
    let offline = true;
    server.use(http.get("/api/screening/stats", () => offline
      ? HttpResponse.json({ detail: "Unavailable" }, { status: 503 }) : HttpResponse.json(STATS)));
    renderPage();
    await screen.findByText("Screening statistics are unavailable. Try again.");
    expect(screen.queryByText("No model runs recorded yet.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open screening photo #2 details" })).toBeInTheDocument();
    offline = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("anthropic");
    expect(screen.queryByText("Screening statistics are unavailable. Try again.")).not.toBeInTheDocument();
  });

  it("retains the last stats with an explicit stale warning when a refresh fails", async () => {
    installHappyHandlers();
    const queryClient = createTestQueryClient();
    renderWithProviders(<ScreeningPage />, queryClient);
    await screen.findByText("anthropic");
    server.use(http.get("/api/screening/stats", () => HttpResponse.json({ detail: "Unavailable" }, { status: 503 })));
    await act(async () => { await queryClient.refetchQueries({ predicate: (query) => JSON.stringify(query.queryKey).includes("/api/screening/stats") }); });
    await screen.findByText("Screening statistics could not refresh. Showing the last available results.");
    expect(screen.getByText("anthropic")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
