# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: tasks-guards.spec.ts >> tasks guards >> auto duties are form-linked; a completed duty lands in awaiting verification
- Location: e2e/tasks-guards.spec.ts:15:3

# Error details

```
Error: page.goto: NS_BINDING_ABORTED
Call log:
  - navigating to "http://localhost:3000/breeding", waiting until "load"

```

# Page snapshot

```yaml
- generic [active] [ref=f5e1]:
  - generic [ref=f5e2]:
    - link "Skip to content" [ref=f5e3] [cursor=pointer]:
      - /url: "#main-content"
    - generic [ref=f5e6]:
      - link "Herdly — go to Dashboard" [ref=f5e8] [cursor=pointer]:
        - /url: /dashboard
        - generic [ref=f5e9]: Herdly
      - navigation "Primary navigation" [ref=f5e20]:
        - generic [ref=f5e21]:
          - generic [ref=f5e22]: Overview
          - list [ref=f5e24]:
            - listitem [ref=f5e25]:
              - link "Dashboard" [ref=f5e26] [cursor=pointer]:
                - /url: /dashboard
            - listitem [ref=f5e33]:
              - link "All farms" [ref=f5e34] [cursor=pointer]:
                - /url: /owner
        - generic [ref=f5e41]:
          - generic [ref=f5e42]: Herd
          - list [ref=f5e44]:
            - listitem [ref=f5e45]:
              - link "Animals" [ref=f5e46] [cursor=pointer]:
                - /url: /animals
            - listitem [ref=f5e53]:
              - link "Buckets" [ref=f5e54] [cursor=pointer]:
                - /url: /buckets
            - listitem [ref=f5e69]:
              - link "Breeding" [ref=f5e70] [cursor=pointer]:
                - /url: /breeding
            - listitem [ref=f5e75]:
              - link "Kidding" [ref=f5e76] [cursor=pointer]:
                - /url: /kidding
        - generic [ref=f5e83]:
          - generic [ref=f5e84]: Health & Feed
          - list [ref=f5e86]:
            - listitem [ref=f5e87]:
              - link "Health" [ref=f5e88] [cursor=pointer]:
                - /url: /health
            - listitem [ref=f5e96]:
              - link "Photo screening" [ref=f5e97] [cursor=pointer]:
                - /url: /screening
            - listitem [ref=f5e102]:
              - link "Feeding" [ref=f5e103] [cursor=pointer]:
                - /url: /feeding
        - generic [ref=f5e114]:
          - generic [ref=f5e115]: Operations
          - list [ref=f5e117]:
            - listitem [ref=f5e118]:
              - link "Purchases" [ref=f5e119] [cursor=pointer]:
                - /url: /purchases
            - listitem [ref=f5e125]:
              - link "Tasks" [ref=f5e126] [cursor=pointer]:
                - /url: /tasks
        - generic [ref=f5e135]:
          - generic [ref=f5e136]: Business
          - list [ref=f5e138]:
            - listitem [ref=f5e139]:
              - link "Finance" [ref=f5e140] [cursor=pointer]:
                - /url: /finance
            - listitem [ref=f5e148]:
              - link "Planner" [ref=f5e149] [cursor=pointer]:
                - /url: /planner
            - listitem [ref=f5e157]:
              - link "Simulation" [ref=f5e158] [cursor=pointer]:
                - /url: /simulation
            - listitem [ref=f5e164]:
              - link "Ops Simulation" [ref=f5e165] [cursor=pointer]:
                - /url: /ops-simulation
            - listitem [ref=f5e174]:
              - link "Reports" [ref=f5e175] [cursor=pointer]:
                - /url: /reports
            - listitem [ref=f5e182]:
              - link "Team" [ref=f5e183] [cursor=pointer]:
                - /url: /team
      - paragraph [ref=f5e191]: Goat farm management
    - main [ref=f5e192]:
      - generic [ref=f5e193]:
        - button "Toggle Sidebar" [ref=f5e194]
        - 'link "Switch farm — current: E2E Farm mussy5a0b4" [ref=f5e196] [cursor=pointer]':
          - /url: /farm-select?returnTo=%2Ftasks%3Ftab%3Doverdue
          - generic [ref=f5e197]: E2E Farm mussy5a0b4
          - generic [ref=f5e198]: Goat farm
        - generic [ref=f5e203]:
          - group "Language / భాష" [ref=f5e204]:
            - button "EN" [pressed] [ref=f5e205]
            - button "తెలుగు" [ref=f5e206]
          - button "Switch to dark theme" [ref=f5e207]
          - button "Account — E2E Runner" [ref=f5e209]:
            - generic [ref=f5e210]: E2
            - generic [ref=f5e211]: E2E Runner
          - button "Logout" [ref=f5e212]
      - main [ref=f5e215]:
        - generic [ref=f5e216]:
          - generic [ref=f5e217]:
            - generic [ref=f5e218]:
              - heading "Tasks" [level=1] [ref=f5e219]
              - paragraph [ref=f5e220]: Duties and auto-generated protocol tasks, grouped by when they're due.
            - button "New duty" [ref=f5e222]
          - generic [ref=f5e223]:
            - tablist [ref=f5e224]:
              - tab "Today (2)" [ref=f5e225]
              - tab "Overdue (2)" [selected] [ref=f5e226]
              - tab "Upcoming (24)" [ref=f5e227]
              - tab "Awaiting verification (0)" [ref=f5e228]
              - tab "Completed (16)" [ref=f5e229]
            - tabpanel "Overdue (2)" [ref=f5e230]:
              - table [ref=f5e235]:
                - rowgroup [ref=f5e236]:
                  - row [ref=f5e237]:
                    - columnheader "Due" [ref=f5e238]
                    - columnheader "Task" [ref=f5e239]
                    - columnheader "Category" [ref=f5e240]
                    - columnheader "Assigned to" [ref=f5e241]
                    - columnheader "Animal" [ref=f5e242]
                    - columnheader [ref=f5e243]
                - rowgroup [ref=f5e244]:
                  - row [ref=f5e245]:
                    - cell "11 Sep 2026 (23d late)" [ref=f5e246]:
                      - generic [ref=f5e247]: 11 Sep 2026
                      - generic [ref=f5e248]: (23d late)
                    - 'cell "Return-to-heat watch: E2E-TDOE-must1ki3yo — days 18–21 post-service; a standing heat means the service failed; record the observation early" [ref=f5e249]'
                    - cell "Heat watch" [ref=f5e250]
                    - cell "Cleaner" [ref=f5e252]
                    - cell [ref=f5e253]:
                      - link "E2E-TDOE-must1ki3yo" [ref=f5e254] [cursor=pointer]:
                        - /url: /animals/174?returnTo=%2Ftasks%3Ftab%3Doverdue
                    - cell [ref=f5e255]:
                      - generic [ref=f5e256]:
                        - button "Complete" [ref=f5e257]
                        - button "Skip" [ref=f5e258]
                  - row [ref=f5e259]:
                    - cell "25 Sep 2026 (9d late)" [ref=f5e260]:
                      - generic [ref=f5e261]: 25 Sep 2026
                      - generic [ref=f5e262]: (9d late)
                    - 'cell "Pregnancy check: E2E-TDOE-must1ki3yo (bred 24 Aug 2026)" [ref=f5e263]'
                    - cell "Ultrasound" [ref=f5e264]
                    - cell "Veterinarian" [ref=f5e266]
                    - cell [ref=f5e267]:
                      - link "E2E-TDOE-must1ki3yo" [ref=f5e268] [cursor=pointer]:
                        - /url: /animals/174?returnTo=%2Ftasks%3Ftab%3Doverdue
                    - cell [ref=f5e269]:
                      - link "Open form" [ref=f5e271] [cursor=pointer]:
                        - /url: /breeding/37/ultrasound?returnTo=%2Ftasks%3Ftab%3Doverdue
              - navigation "overdue tasks pagination" [ref=f5e272]:
                - paragraph [ref=f5e273]: Showing 1–2 of 2 overdue tasks
                - generic [ref=f5e274]:
                  - button "Previous" [disabled]
                  - button "Next" [disabled]
  - region "Notifications alt+T"
  - alert [ref=f5e275]
```

# Test source

```ts
  245 |   await page.goto("/animals");
  246 |   await page.getByPlaceholder("Search by tag…").fill(tag);
  247 |   await page.getByRole("link", { name: tag, exact: true }).click();
  248 |   await expect(page).toHaveURL(/\/animals\/\d+$/, { timeout: 15_000 });
  249 |   await expect(page.getByRole("heading", { name: new RegExp(tag) })).toBeVisible({
  250 |     timeout: 15_000,
  251 |   });
  252 | }
  253 | 
  254 | /** Locator for the <dd> value of a Details-card <dt> on the animal profile. */
  255 | export function profileDetail(page: Page, label: string): Locator {
  256 |   return page
  257 |     .getByText(label, { exact: true })
  258 |     .locator("xpath=following-sibling::dd[1]");
  259 | }
  260 | 
  261 | /**
  262 |  * Create a breeding record for `doeTag` through the Breeding page dialog.
  263 |  * Ensures an active buck exists first (creates one via the Animals dialog if
  264 |  * the farm has none). Breeding date stays at the default (today) unless
  265 |  * `breedingDate` (YYYY-MM-DD) is given — e.g. an overdue pregnancy for the
  266 |  * kidding flow needs a breeding ~150+ days in the past.
  267 |  */
  268 | export async function createBreeding(
  269 |   page: Page,
  270 |   doeTag: string,
  271 |   breedingDate?: string,
  272 | ): Promise<void> {
  273 |   // A buck that is eligible today may have no weight (or insufficient age) as
  274 |   // of a backdated breeding. Create and select a date-correct buck explicitly
  275 |   // so serial specs cannot accidentally reuse a later-only fixture.
  276 |   let preferredBuckTag: string | undefined;
  277 |   if (breedingDate !== undefined) {
  278 |     preferredBuckTag = uniqueTag("E2E-BUCK");
  279 |     await createAnimal(page, {
  280 |       tag: preferredBuckTag,
  281 |       historicalImportReason: "E2E backdated breeding buck fixture",
  282 |       sex: "M",
  283 |       bucket: "BREEDING",
  284 |       dateOfBirth: monthsAgo(20),
  285 |       entryWeightKg: 30,
  286 |       entryWeightDate: breedingDate,
  287 |     });
  288 |   }
  289 | 
  290 |   await page.goto("/breeding");
  291 |   await page.getByRole("button", { name: "Add breeding" }).first().click();
  292 |   let dialog = page.getByRole("dialog", { name: "Add breeding" });
  293 |   const noEligibleBuck = dialog.getByText(/No eligible .+ are available/);
  294 |   let buckPicker = dialog.getByRole("button", { name: "Buck *", exact: true });
  295 |   // Buck eligibility is checked against the canonical scoped endpoint. Wait
  296 |   // until it either enables the picker or reports a truthful empty state.
  297 |   await expect
  298 |     .poll(
  299 |       async () => (await buckPicker.isEnabled()) || (await noEligibleBuck.isVisible()),
  300 |       { timeout: 20_000 },
  301 |     )
  302 |     .toBe(true);
  303 | 
  304 |   if (await noEligibleBuck.isVisible()) {
  305 |     await page.keyboard.press("Escape");
  306 |     await expect(dialog).toBeHidden();
  307 |     preferredBuckTag = uniqueTag("E2E-BUCK");
  308 |     await createAnimal(page, {
  309 |       tag: preferredBuckTag,
  310 |       historicalImportReason: "E2E breeding buck fixture",
  311 |       sex: "M",
  312 |       bucket: "BREEDING",
  313 |       dateOfBirth: monthsAgo(20),
  314 |       entryWeightKg: 30,
  315 |       entryWeightDate: breedingDate,
  316 |     });
  317 |     await page.goto("/breeding");
  318 |     await page.getByRole("button", { name: "Add breeding" }).first().click();
  319 |     dialog = page.getByRole("dialog", { name: "Add breeding" });
  320 |     buckPicker = dialog.getByRole("button", { name: "Buck *", exact: true });
  321 |     await expect(buckPicker).toBeEnabled({ timeout: 20_000 });
  322 |   }
  323 | 
  324 |   await pickRemoteOption(dialog, "Doe *", new RegExp(doeTag), doeTag);
  325 |   if (preferredBuckTag !== undefined) {
  326 |     await pickRemoteOption(
  327 |       dialog,
  328 |       "Buck *",
  329 |       new RegExp(preferredBuckTag),
  330 |       preferredBuckTag,
  331 |     );
  332 |   } else {
  333 |     await pickRemoteOption(dialog, "Buck *", null);
  334 |   }
  335 |   if (breedingDate) {
  336 |     await dialog.getByLabel("Breeding date *").fill(breedingDate);
  337 |   }
  338 |   await dialog.getByRole("button", { name: "Save breeding" }).click();
  339 |   await expect(page.getByText("Breeding saved.")).toBeVisible();
  340 |   await expect(dialog).toBeHidden();
  341 | }
  342 | 
  343 | /** Record a pregnant ultrasound (default kid count 2) for the doe's PENDING record. */
  344 | export async function recordUltrasoundPregnant(page: Page, doeTag: string): Promise<void> {
> 345 |   await page.goto("/breeding");
      |              ^ Error: page.goto: NS_BINDING_ABORTED
  346 |   const row = page.getByRole("row", { name: new RegExp(doeTag) });
  347 |   await expect(row).toBeVisible({ timeout: 15_000 });
  348 |   await row.getByRole("button", { name: "Ultrasound result" }).click();
  349 |   const dialog = page.getByRole("dialog", { name: "Ultrasound result" });
  350 |   await expect(dialog).toBeVisible();
  351 |   const pregnantCheckbox = dialog.getByRole("checkbox", { name: "Pregnant — confirmed" });
  352 |   await expect(pregnantCheckbox).not.toBeChecked();
  353 |   await pregnantCheckbox.click();
  354 |   await expect(pregnantCheckbox).toBeChecked();
  355 |   // Kid count defaults to 2 — assert the default rather than re-picking it.
  356 |   await expect(selectTrigger(dialog, "Kid count detected")).toContainText("2");
  357 |   await dialog.getByRole("button", { name: "Save result" }).click();
  358 |   await expect(page.getByText("Ultrasound result saved.")).toBeVisible();
  359 |   await expect(dialog).toBeHidden();
  360 | }
  361 | 
```