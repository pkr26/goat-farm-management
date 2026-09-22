import { expect, test, type Page } from "@playwright/test";

import { signIn } from "./helpers";

/**
 * Screening review journey with a stubbed outcome (B8, 2026-09-21 audit).
 *
 * The real screening pipeline needs S3 + a provisioned vision provider, none
 * of which exist in the e2e environment — so this journey pins the API
 * responses at the network layer and drives the REAL page code: scoreboard
 * rendering, the queue row's accessible detail trigger, the vet review
 * flow, and the optimistic-concurrency conflict toast. Everything outside
 * /api/screening (auth, permissions, farm context) is the real stack.
 */

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

const IMAGE_ID = 2;

const LIST = {
  images: [
    {
      id: IMAGE_ID,
      status: "FLAGGED",
      bucket: "BREEDING",
      batch_id: 7,
      s3_key: "raw/1/2026-09-18/BREEDING/7-2.jpg",
      captured_date: "2026-09-18",
      width: 2000,
      height: 1000,
      byte_size: 400000,
      error: null,
      created_at: "2026-09-18T05:30:00Z",
      latest_run: {
        id: 101,
        image_id: IMAGE_ID,
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
      },
      pending_findings: 1,
    },
  ],
  total: 1,
  limit: 25,
  offset: 0,
};

const DETAIL = {
  id: IMAGE_ID,
  status: "FLAGGED",
  bucket: "BREEDING",
  batch_id: 7,
  s3_key: "raw/1/2026-09-18/BREEDING/7-2.jpg",
  captured_date: "2026-09-18",
  width: 2000,
  height: 1000,
  byte_size: 400000,
  error: null,
  created_at: "2026-09-18T05:30:00Z",
  // No image_url: the deployment has no screening storage, and the page must
  // say so instead of rendering a broken image.
  image_url: null,
  crops: [],
  runs: [
    {
      id: 101,
      image_id: IMAGE_ID,
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
    },
  ],
  findings: [
    {
      id: 51,
      run_id: 101,
      crop_id: null,
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

/** Fulfill every /api/screening/* call from fixtures. The one mutable knob is
 *  the review POST's answer, so the journey can first confirm (200) and then
 *  replay a verdict race (409). */
function stubScreening(page: Page, reviewStatus: { code: number }) {
  return page.route("**/api/screening/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/screening/stats") {
      return route.fulfill({ json: STATS });
    }
    if (url.pathname === "/api/screening/images") {
      return route.fulfill({ json: LIST });
    }
    if (url.pathname === `/api/screening/images/${IMAGE_ID}`) {
      return route.fulfill({ json: DETAIL });
    }
    if (url.pathname === "/api/screening/findings/51/review") {
      if (reviewStatus.code === 200) {
        return route.fulfill({ json: { ok: true } });
      }
      return route.fulfill({
        status: reviewStatus.code,
        json: { detail: "Finding already reviewed" },
      });
    }
    if (url.pathname === "/api/screening/export") {
      return route.fulfill({
        json: { farm_id: 1, generated_at: "2026-09-19T05:30:00Z", record_count: 1, records: [] },
      });
    }
    return route.fulfill({ status: 404, json: { detail: "not stubbed" } });
  });
}

test.describe("screening review", () => {
  test("scoreboard → queue → detail → confirm → conflict toast, all stubbed at the API", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    const reviewStatus = { code: 200 };
    await stubScreening(page, reviewStatus);
    await signIn(page);

    await page.goto("/screening");
    // Provider scoreboard with derived figures (30% flag rate, 1.5s latency).
    await expect(page.getByRole("cell", { name: /anthropic/ }).first()).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByRole("cell", { name: "30%" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "1.5s" })).toBeVisible();

    // Queue row: status badge plus the pending-findings count.
    await expect(page.getByRole("button", { name: "Open screening photo #2 details" })).toBeVisible();
    await expect(page.getByText("Orf lesions").first()).toBeHidden(); // list row has no finding labels

    // The row's status button opens the detail view (URL carries image_id).
    await page.getByRole("button", { name: "Open screening photo #2 details" }).click();
    await expect(page).toHaveURL(new RegExp(`image_id=${IMAGE_ID}`));
    await expect(page.getByText("Photo review", { exact: true })).toBeVisible();
    // No screening storage on this deployment: the page says so plainly.
    await expect(
      page.getByText("Image preview unavailable (screening storage not configured)."),
    ).toBeVisible();
    await expect(page.getByText("Orf lesions")).toBeVisible();
    await expect(page.getByText("confidence 87%").first()).toBeVisible();

    // Vet confirms the finding through the real review mutation (stubbed 200).
    await page.getByRole("button", { name: "Confirm", exact: true }).click();
    await expect(page.getByText("Reviewed")).toBeVisible({ timeout: 15_000 });

    // A second reviewer racing the same verdict gets the 409 conflict, and
    // the page answers with its own conflict message — never raw server text.
    reviewStatus.code = 409;
    await page.getByRole("button", { name: "Reject", exact: true }).click();
    await expect(
      page.getByText("Already reviewed — refresh to see the current status."),
    ).toBeVisible({ timeout: 15_000 });

    // Back to list returns to the queue.
    await page.getByRole("button", { name: "Back to list" }).click();
    await expect(page).not.toHaveURL(new RegExp("image_id"));
    await expect(page.getByRole("button", { name: "Open screening photo #2 details" })).toBeVisible();
  });
});
