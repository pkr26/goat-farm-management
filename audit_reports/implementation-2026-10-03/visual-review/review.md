# Production visual review

Ten representative Chromium screenshots were captured from the running production frontend at `localhost:3000`, backed by the synthetic E2E API at `localhost:8000`. Every PNG below was opened with `view_image` before these observations were written. The initial build ID was `3GeTyHSvkULabo_qc8Ny6`.

This is a review of the displayed states at 1440 × 1100 desktop and 390 × 844 mobile viewports. It does not establish human usability, fluent Telugu, all viewport/browser combinations, or successful production deployment. Required-password worker UI was not captured here; its separate focused and browser regressions remain the evidence for that flow.

## Before captures and observations

| Screenshot | Displayed state | Observed result |
| --- | --- | --- |
| [01](01-simulation-en-expanded.png) | English simulation editor, Meta and Herd open | Fields and borders align; section help is visibly beside the disclosure heading. |
| [02](02-simulation-en-collapsed-help.png) | English Herd help opened with Enter while Herd is collapsed | Dialog body wraps inside its bounds; close control is visible; disclosure remains collapsed. |
| [03](03-simulation-te-expanded.png) | Telugu simulation editor, Meta and Herd open | Telugu glyphs render and field labels fit. **Issue:** `Meta` and `Reproduction` headings remain English. |
| [04](04-simulation-te-collapsed-help.png) | Telugu Herd help opened with Enter while Herd is collapsed | Telugu body and title fit; close control is visible; disclosure remains collapsed. |
| [05](05-team-pin-add-en.png) | English Add worker dialog, Tablet PIN chosen, fields empty | Explanation, credential mode, PIN controls, role field, and footer fit without overlap. |
| [06](06-team-pin-add-te.png) | Telugu Add worker dialog, Tablet PIN chosen, fields empty | Longer Telugu copy wraps within the dialog; controls and footer remain visible. |
| [07](07-ownership-transfer-en.png) | English ownership transfer dialog, no recipient/password/acknowledgement entered | Warning, password field, checkbox, and disabled submit fit. **Issue:** recipient selector is a narrow chevron-only box with no prompt. |
| [08](08-ownership-transfer-te.png) | Telugu ownership transfer dialog, no input or submission | Warning and acknowledgement fit. **Issue:** the same narrow recipient selector lacks a visible prompt. |
| [09](09-mobile-login-en-dark.png) | Empty English web login in dark mode | Header, labels, inputs, language controls, submit, and links fit the mobile viewport. |
| [10](10-mobile-worker-setup-te-dark.png) | Empty Telugu tablet setup credentials in dark mode | Heading, explanation, labels, large inputs, continue, and cancel fit the mobile viewport. |

The capture also checked the actual simulation DOM in both languages: zero disclosure summaries contained interactive descendants. Help was opened using keyboard Enter after collapsing Herd; the details element remained collapsed.

## Confirmed corrections

The supported `SimulationAssumptions` contract exposes twelve editor sections. Static cross-check found six heading labels missing from the original localization path: `meta`, `reproduction`, `culling`, `sales`, `finance`, and `optimization`. The approved correction adds a section-label helper covering every current generated section key, shared by displayed headings and help names/titles, and preserves the generic fallback for unknown saved-payload sections.

The ownership correction makes the recipient trigger full width, provides a localized visible placeholder, and explicitly represents an unselected value as `null` at the Base UI boundary. Its guarded string state and transfer eligibility checks are retained. Real Base UI tests cover English and Telugu placeholder display, keyboard option selection, focus return, disabled transfer before password/acknowledgement, and placeholder reset after reopening. No hidden-control or Select mock substitutes for these tests.

Four affected screenshots were recaptured from corrected production build `CWwqRg-tXDDZQ8xWOAWpZ` and opened with `view_image`. The original images above remain the before evidence. No source changes beyond the two approved corrections were made during the review.

| After screenshot | Visual verification |
| --- | --- |
| [03](after/03-simulation-te-expanded.png) | `Meta` now displays `ప్రాథమిక వివరాలు`; `Reproduction` displays `సంతానోత్పత్తి`. Both labels fit their section headings; the field grid and sibling help placement remain aligned. |
| [04](after/04-simulation-te-collapsed-help.png) | Telugu section help remains readable and centered, with a visible close control; real Enter activation keeps Herd collapsed. |
| [07](after/07-ownership-transfer-en.png) | The recipient selector spans the same form width as the password field and visibly says `Choose a new farm owner`; transfer remains disabled and unsubmitted. |
| [08](after/08-ownership-transfer-te.png) | The full-width selector visibly says `కొత్త ఫారం యజమానిని ఎంచుకోండి`; the longer Telugu prompt fits, and transfer remains disabled and unsubmitted. |

The [after receipt](after/capture-results.json) records the production build ID, localized prompts, and measured recipient trigger bounding-box widths above 450 pixels in both languages during capture. The final PNGs show the aligned form-width controls. No additional concrete clipping or overlap was observed in these four corrected states. All twelve section labels are covered by the focused real-page tests; this visual sample directly displays the two originally visible untranslated headings.

The corrections passed [28 focused tests](focused-tests.log) and [33 adjacent stored-payload tests](stored-payload-tests.log), plus [full TypeScript checking](typecheck.log) and [scoped ESLint](eslint.log). [focused-verification.json](focused-verification.json) records these receipts. Root owns the separate final serial browser and full-suite gates; no result for those gates is inferred from this capture.

## Capture boundaries

`capture.mjs` launches direct private Chromium contexts. It does not invoke Playwright's runner or `globalSetup`, write `.e2e-state.json`, record traces/storage-state, create workers/PINs/roles/farms, or submit ownership transfer. Existing synthetic credentials are loaded into memory and redacted from error output; they are not copied into the artifacts. The authenticated review context signs itself out after capture. Recorded non-GET requests are limited to session login, refresh, and logout; zero domain form submissions occurred.

Reproduce the initial representative states with:

```sh
node audit_reports/implementation-2026-10-03/visual-review/capture.mjs
```

After a corrected build is running, capture only the affected Telugu editor/help and English/Telugu ownership states without overwriting the originals:

```sh
node audit_reports/implementation-2026-10-03/visual-review/capture.mjs --after
```

The receipt [capture-results.json](capture-results.json) records state, viewport, routes, mutation paths, and disclosure structure checks. This review does not replace the serial functional or axe browser suites.
