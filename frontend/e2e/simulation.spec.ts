import { expect, test, type Page } from "@playwright/test";

import { signIn, uniqueTag } from "./helpers";

/** Load the auto-fetched osmanabadi/stall_fed defaults and pick the 10-year preset. */
async function loadDefaultsWithTenYearHorizon(page: Page) {
  await page.goto("/simulation");
  // Defaults auto-load on mount; the horizon input appears with the meta section.
  const horizonInput = page.locator("#sim-meta-horizon_months");
  await expect(horizonInput).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "10 yr" }).click();
  await expect(horizonInput).toHaveValue("120");
  return horizonInput;
}

/** The monthly projection table inside the results. */
function monthlyTable(page: Page) {
  return page.locator("table", {
    has: page.getByRole("columnheader", { name: "Events" }),
  });
}

async function pageOverflow(page: Page) {
  return page.evaluate(() => {
    const root = document.documentElement;
    const containedByOverflowBoundary = (element: HTMLElement) => {
      let ancestor = element.parentElement;
      while (ancestor && ancestor !== document.body) {
        const style = getComputedStyle(ancestor);
        if (
          style.position === "fixed" ||
          ["auto", "clip", "hidden", "scroll"].includes(style.overflowX)
        )
          return true;
        ancestor = ancestor.parentElement;
      }
      return false;
    };
    const elements =
      root.scrollWidth <= root.clientWidth + 1
        ? []
        : [...document.querySelectorAll<HTMLElement>("body *")]
            .map((element) => {
              const rect = element.getBoundingClientRect();
              return {
                element,
                details: {
                  tag: element.tagName.toLowerCase(),
                  slot: element.dataset.slot ?? "",
                  className: typeof element.className === "string" ? element.className : "",
                  right: Math.round(rect.right),
                  width: Math.round(rect.width),
                  text: (element.textContent ?? "").trim().slice(0, 80),
                },
              };
            })
            .filter(
              ({ element, details }) =>
                details.width > 0 &&
                details.right > root.clientWidth + 1 &&
                !containedByOverflowBoundary(element),
            )
            .map(({ details }) => details)
            .slice(0, 20);
    return {
      overflow: root.scrollWidth - root.clientWidth,
      scrollX: window.scrollX,
      elements,
    };
  });
}

test.describe("simulation", () => {
  test("defaults + herd events run end-to-end; save and re-run as a scenario", async ({
    page,
  }) => {
    test.setTimeout(180_000);
    await signIn(page);
    await loadDefaultsWithTenYearHorizon(page);

    // Two scheduled herd events. "Add event" defaults to a purchase of 10 does
    // at an auto price, so the first row only needs its month set to 14.
    await page.getByRole("button", { name: "Add event" }).click();
    const monthInputs = page.getByRole("spinbutton", { name: "Month", exact: true });
    await expect(monthInputs).toHaveCount(1);
    await monthInputs.nth(0).fill("14");

    await page.getByRole("button", { name: "Add event" }).click();
    await expect(monthInputs).toHaveCount(2);
    await page.getByLabel("Kind", { exact: true }).nth(1).click();
    await page.getByRole("option", { name: "Sale" }).click();
    await monthInputs.nth(1).fill("30");
    await page
      .getByRole("spinbutton", { name: "Count", exact: true })
      .nth(1)
      .fill("5");

    // Monte Carlo / Sensitivity stay off — the plain run is cheap.
    await page.getByRole("button", { name: "Run simulation" }).click();
    await expect(page.getByRole("heading", { name: "Results", exact: true })).toBeVisible({
      timeout: 120_000,
    });

    // All ten metric cards render.
    for (const label of [
      "NPV",
      "IRR",
      "BCR",
      "Avg DSCR",
      "Payback month",
      "Break-even meat (₹/kg)",
      "Project cost",
      "Loan",
      "Subsidy",
      "Equity",
    ]) {
      await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
    }

    // The narrative Report card shows all six sections plus the verdict badge.
    for (const title of [
      "Overview",
      "Herd trajectory",
      "Where the money comes from",
      "Where the money goes",
      "Bottom line",
      "Risks",
    ]) {
      await expect(
        page.getByRole("heading", { name: title, exact: true }),
      ).toBeVisible();
    }
    await expect(
      page
        .getByRole("heading", { name: "Bottom line", exact: true })
        .locator("xpath=../span"),
    ).toHaveText(/^(NOT )?VIABLE( WITH CAUTION)?$/);

    // The NPV explanation dialog explains the discount rate.
    await page.getByRole("button", { name: "Explain NPV" }).click();
    const explainDialog = page.getByRole("dialog", { name: "Net present value (NPV)" });
    await expect(explainDialog).toBeVisible();
    await expect(explainDialog.getByText(/discounted to today at/)).toBeVisible();
    await expect(
      explainDialog.getByText("Discount Rate Annual", { exact: true }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(explainDialog).toBeHidden();

    // The monthly table logs the two events at their months.
    await expect(monthlyTable(page).getByRole("row", { name: /^14\b/ })).toContainText(
      "Purchased 10 doe(s)",
    );
    await expect(monthlyTable(page).getByRole("row", { name: /^30\b/ })).toContainText(
      /Sold 5 doe\(s\)/,
    );

    // Save the run as a scenario, then re-run it from the scenario list.
    const scenarioName = uniqueTag("E2E scenario");
    await page.getByRole("button", { name: "Save as scenario" }).click();
    const saveDialog = page.getByRole("dialog", { name: "Save as scenario" });
    await expect(saveDialog).toBeVisible();
    await saveDialog.locator("#scenario-name").fill(scenarioName);
    await saveDialog.getByRole("button", { name: "Save scenario" }).click();
    await expect(page.getByText("Scenario saved.")).toBeVisible();
    await expect(saveDialog).toBeHidden();

    const scenarioRow = page.getByRole("row", { name: new RegExp(scenarioName) });
    await expect(scenarioRow).toBeVisible({ timeout: 15_000 });
    const runResponse = page.waitForResponse(
      (resp) =>
        resp.request().method() === "POST" &&
        /\/api\/simulation\/scenarios\/\d+\/run/.test(resp.url()),
      { timeout: 120_000 },
    );
    await scenarioRow.getByRole("button", { name: "Run", exact: true }).click();
    expect((await runResponse).status()).toBe(200);
    // Metrics render again from the scenario run.
    await expect(page.getByRole("button", { name: "Explain NPV" })).toBeVisible();
    await expect(page.getByText(/Scenario run failed/)).toHaveCount(0);
  });

  test("an event month beyond the horizon blocks the run with an inline error", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    await signIn(page);
    await loadDefaultsWithTenYearHorizon(page);

    await page.getByRole("button", { name: "Add event" }).click();
    await page.getByRole("spinbutton", { name: "Month", exact: true }).fill("121");

    await expect(page.getByText("Must be at most 120.", { exact: true })).toBeVisible();

    // The run is blocked client-side: the action is disabled, the inline
    // field error stays visible, and no result section is created.
    await expect(page.getByRole("button", { name: "Run simulation" })).toBeDisabled();
    await expect(page.getByRole("heading", { name: "Results", exact: true })).toHaveCount(0);
    await expect(page.getByText("Must be at most 120.", { exact: true })).toBeVisible();
  });

  test("farm calibration and all decision analyses render responsively", async ({
    page,
  }, testInfo) => {
    test.setTimeout(180_000);
    await signIn(page);
    await page.goto("/simulation");

    const horizonInput = page.locator("#sim-meta-horizon_months");
    await expect(horizonInput).toBeVisible({ timeout: 20_000 });
    await horizonInput.fill("24");
    await horizonInput.blur();

    await page.getByRole("button", { name: "Calibrate from farm" }).click();
    await expect(
      page.getByText("Farm calibration evidence", { exact: true }),
    ).toBeVisible({ timeout: 30_000 });

    await horizonInput.fill("24");
    await horizonInput.blur();
    await page.locator("#sim-herd-does").fill("20");
    await page.locator("#sim-herd-does").blur();
    await page.locator("#sim-herd-bucks").fill("1");
    await page.locator("#sim-herd-bucks").blur();

    // The breed/calibration festival-sale months span the default 120-month
    // horizon; the 24-month horizon leaves months > 24 invalid and gates Run.
    // Clear the list AFTER calibration (an empty list is legal — no festival
    // timing), because calibrating replaces the assumptions wholesale.
    const salesSection = page.locator("details", {
      has: page.locator("#sim-sales-festival_sale_months"),
    });
    await salesSection.locator("summary").click();
    const festivalMonths = salesSection.locator("#sim-sales-festival_sale_months");
    await festivalMonths.fill("");
    await festivalMonths.blur();

    // Section summaries now embed an "Explain … ?" help button, so anchor
    // each collapsible to one of its own field ids instead of summary text.
    const riskSection = page.locator("details", {
      has: page.locator("#sim-risk-monte_carlo_runs"),
    });
    await riskSection.locator("summary").click();
    await riskSection.locator("#sim-risk-monte_carlo_runs").fill("20");
    await riskSection.locator("#sim-risk-monte_carlo_runs").blur();

    const optimizationSection = page.locator("details", {
      has: page.locator("#sim-optimization-max_candidates"),
    });
    await optimizationSection.locator("summary").click();
    await optimizationSection.locator("#sim-optimization-max_candidates").fill("8");
    await optimizationSection.locator("#sim-optimization-max_candidates").blur();

    for (const option of ["Monte Carlo", "Sensitivity", "Optimization"]) {
      await page.getByRole("checkbox", { name: option, exact: true }).click();
    }

    await page.getByRole("button", { name: "Run simulation" }).click();
    const resultsHeading = page.getByRole("heading", { name: "Results", exact: true });
    await expect(resultsHeading).toBeVisible({ timeout: 120_000 });
    const monteCarloTitle = page.getByText(/Monte Carlo \(20 runs/);
    await expect(monteCarloTitle).toBeVisible();
    await expect(page.getByLabel("NPV histogram")).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Annual uncertainty checkpoints" }),
    ).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Does / bucks / ceiling" })).toBeVisible();
    await expect(page.getByText("Sensitivity (ΔNPV)", { exact: true })).toBeVisible();

    await page.setViewportSize({ width: 1440, height: 1000 });
    expect(await pageOverflow(page)).toEqual({ overflow: 0, scrollX: 0, elements: [] });
    await resultsHeading.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("results-desktop.png") });
    await monteCarloTitle.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("advanced-desktop.png") });

    await page.setViewportSize({ width: 390, height: 844 });
    await resultsHeading.scrollIntoViewIfNeeded();
    expect(await pageOverflow(page)).toEqual({ overflow: 0, scrollX: 0, elements: [] });
    await page.screenshot({ path: testInfo.outputPath("results-mobile.png") });
    await monteCarloTitle.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("advanced-mobile.png") });
  });
});
