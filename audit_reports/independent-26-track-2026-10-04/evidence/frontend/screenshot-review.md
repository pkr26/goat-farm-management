# Retained simulation screenshot review

Reviewed 2026-10-04 using `view_image`, without launching a browser, server, or new test. Eight already-retained screenshots were visually inspected. `frontend/e2e/simulation.spec.ts:270-282` records desktop CSS viewport 1440×1000 and mobile 390×844; WebKit’s PNGs use two device pixels per CSS pixel.

| Browser | View | Viewport | Evidence |
|---|---|---|---|
| Chromium | results | desktop | [Screenshot](../browser-artifacts/simulation-simulation-farm-6185a-nalyses-render-responsively-chromium/results-desktop.png) |
| Chromium | results | mobile | [Screenshot](../browser-artifacts/simulation-simulation-farm-6185a-nalyses-render-responsively-chromium/results-mobile.png) |
| Chromium | advanced | desktop | [Screenshot](../browser-artifacts/simulation-simulation-farm-6185a-nalyses-render-responsively-chromium/advanced-desktop.png) |
| Chromium | advanced | mobile | [Screenshot](../browser-artifacts/simulation-simulation-farm-6185a-nalyses-render-responsively-chromium/advanced-mobile.png) |
| WebKit | results | desktop | [Screenshot](../browser-webkit-artifacts/simulation-simulation-farm-6185a-nalyses-render-responsively-webkit/results-desktop.png) |
| WebKit | results | mobile | [Screenshot](../browser-webkit-artifacts/simulation-simulation-farm-6185a-nalyses-render-responsively-webkit/results-mobile.png) |
| WebKit | advanced | desktop | [Screenshot](../browser-webkit-artifacts/simulation-simulation-farm-6185a-nalyses-render-responsively-webkit/advanced-desktop.png) |
| WebKit | advanced | mobile | [Screenshot](../browser-webkit-artifacts/simulation-simulation-farm-6185a-nalyses-render-responsively-webkit/advanced-mobile.png) |

The visible desktop result and Monte Carlo cards align in columns, and their mobile counterparts stack in one column with readable metric labels and values. The run-option checkboxes wrap within their card. The visible paragraph text and headings wrap within the mobile content width. No persistent layout or usability defect is established by these captures.

A specific transient state is visible: the calibration notification’s leading text/icon is clipped past the left mobile edge in both results captures and the Chromium advanced capture. The later WebKit advanced capture shows the entire notification within the viewport. The test switches directly from desktop to mobile immediately before the captures (`frontend/e2e/simulation.spec.ts:277-282`), while Sonner’s installed stylesheet transitions the toaster transform for 400ms (`frontend/node_modules/sonner/dist/styles.css:51`) and switches a centered `translateX(-50%)` to `transform: none` at the mobile breakpoint (`:68-70`, `:425-458`). This supports a resize-transition explanation; the screenshots do not establish persistent clipping after the transition or clipping on a fresh mobile load. The observation is retained rather than counted as a new confirmed persistent-layout finding.

Only part of the mobile in-page section navigation and wide data table appears in the screenshots. The section navigation explicitly uses `overflow-x-auto` (`frontend/src/app/(app)/simulation/page.tsx:3154`), so a partially visible next item alone is not evidence of inaccessible navigation. These still images cannot establish horizontal-scroll operability or keyboard focus behavior. Sticky navigation covering already-scrolled content and the viewport ending partway through lower cards are likewise not evidence that those rows/cards are inaccessible.

Limits: these are two scroll positions on one English, light-theme simulation journey at two viewport sizes per engine. No physical device, dynamic orientation change, mobile keyboard, touch target measurement, zoom/reflow at 200–400%, screen reader, contrast measurement, dark theme, Telugu layout, or interaction was exercised by this visual review. The full WebKit suite’s separate login timeout remains failed; inspecting these successful simulation captures does not change that outcome. Firefox remains unvalidated.
