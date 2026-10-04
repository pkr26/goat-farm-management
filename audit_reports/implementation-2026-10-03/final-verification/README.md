# Final local verification index

This is the final conventional release-gate evidence for the locally verified October fixes. The application is Next.js 16.3.8 / FastAPI / PostgreSQL. The preserved baseline is commit `b285645b2a93709aa7294fc4698ee0f25839b13e`; its original report remains unchanged. See [implementation status](../../../docs/archive/IMPLEMENTATION_STATUS.md), [plan](../../../docs/archive/APP_IMPROVEMENT_PLAN.md) and [all tracked findings](../finding-ledger.md).

## Application and test identity

[frontend-source-manifest.json](frontend-source-manifest.json) records all 964 repository-visible frontend source/test/configuration/dependency entries when the successful production build and complete unit coverage ran. [frontend-final-browser-manifest.json](frontend-final-browser-manifest.json) records the final browser-test snapshot: only the purchase and task-guard browser specs changed afterward, adding committed-query-URL assertions. All other 962 entries, including every production and unit-test file, remain identical. The browser matrix is rerun on that final test snapshot; build/unit evidence remains applicable to unchanged code. No coverage threshold was lowered.

The [final backend manifest](../final-backend/manifest.json) independently verifies all 410 backend/shared/README inputs against the complete 5,128-case collection and both isolated coverage partitions.

## Complete conventional checks

- Backend: **5,124 passed, 4 skipped**, zero failures/errors. [Full-suite/coverage/source/database reconciliation](../final-backend/README.md).
- Backend combined coverage **92.7189%**, enforcing the existing **92% floor**, with separate **94.5437% lines / 85.9931% branches**.
- Frontend: **326 test files / 5,269 passing tests**, zero failed or skipped. [Receipt](frontend-coverage-receipt.json), [log](frontend-coverage.log), [full V8 counters](frontend-coverage-final.json).
- Frontend coverage: **94.30% statements / 91.98% branches / 94.33% functions / 96.47% lines**. Every unchanged global, scoped and individual-file floor passed.
- [Production build](frontend-build.log), [full lint](frontend-lint.log) and [TypeScript](frontend-typecheck.log) passed after the visual fixes. After the two E2E-only assertions, [full lint](frontend-lint-post-e2e.log) and [TypeScript](frontend-typecheck-post-e2e.log) passed again.
- [Backend static commands](../final-static/manifest.json) passed all nine gates, including Ruff/format, zero strict source/test typing, dependency lock and sole final migration head.
- Final complete browser matrix: **216 passed, zero failures/retries/skips**, using the final test snapshot: [Chromium/mobile 74](chromium-receipt.json), [Linux Firefox 71](firefox-receipt.json) and [WebKit 71](webkit-receipt.json). [Combined summary](summary.json). Earlier Chromium/WebKit receipts are retained separately under `../prior-integration/`. The owned remote Firefox container was stopped and removed after its gate.

## Additional direct evidence

- [55 populated historical upgrade checks](../populated-migration-rehearsal-f8/README.md), [fresh/old-head/empty-roundtrip/model parity](migration-roundtrip.log), [actual checkpoint process-death/restart/concurrency regressions](../maintenance-progress/focused-pytest-receipt.json).
- [Local consistent-snapshot PostgreSQL restore](../final-recovery/README.md): 49 tables / 2,458 synthetic rows, exact counts and row SHA-256 digests, final head and constraints/model parity. Production RPO/RTO and object/config/key recovery are not implied.
- [Actual fake-only Docker context/service-secret probes](../final-ops-probes/README.md), [protected production-settings ASGI metrics](../final-operations/protected-metrics-asgi.json), [exact CI migration guard and PIN-reset response checks](../final-evidence/README.md).
- [Production frontend dependency scan](frontend-production-dependency-audit.json): zero advisories. [Verified full frontend audit](frontend-verified-dependency-audit.log): zero unmitigated findings, one explicitly verified development-only local patch. [Backend runtime audit](../final-operations/runtime-pip-audit-receipt.json): zero known advisories across 44 runtime dependencies.
- [Sampled production visual review](../visual-review/review.md): ten before screens and four inspected corrections, with bilingual real-select/keyboard checks. [Cold service-worker install diagnosis/fix](../service-worker-cold-install/README.md).
- [Frontend mutation-harness contracts](frontend-mutation-harness.log): 17 passed; the 24 backend contracts are in the complete backend suite. Historical campaign scores are untrusted; a fresh full campaign score is not claimed.
- [Independent integrity verifier](../final-integrity/README.md): 97 historical migrations unchanged, four forward additions, 63 original bundle files unchanged, complete backend reconciliation and bounded credential/artifact hygiene checks.

## Browser runtime transparency

Native macOS Firefox failed before app activity. [The version-matched official Linux runtime record](../browser-runtime/firefox-linux-readme.md) preserves that limitation and the successful 153.0 launcher/loopback probe. [The first reached full Firefox run](../browser-runtime/firefox-first-full-receipt.json) recorded 69 passes and two navigation aborts. [Sanitized timeline analysis](../browser-runtime/firefox-navigation-diagnosis.md) proves the two browser tests interrupted still-pending query replacements; [the two stronger URL assertions passed](../browser-runtime/firefox-url-commit-focused-receipt.json). No app code changed, tests were not skipped, retries/catches/sleeps were not added, and raw authenticated traces are not included in this evidence bundle.

This index reports local engineering evidence. Fresh mutation campaigns, supported-load/soak, manual accessibility/Telugu and field review, real-photo/domain evaluation, provider sandbox receipts, full staging recovery/alerts and a fresh ten-area score assessment remain separate tasks in the plan.
