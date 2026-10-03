# Herdly improvement implementation status

Updated 3 October 2026. **All 83 tracked issues—71 original audit findings and 12 additional issues—have implemented fixes and passing local regression evidence.** Final backend, frontend, production build, typing, lint and the complete 216-check browser matrix pass. Firefox uses the official version-matched Linux runtime; the native macOS launcher limitation remains explicitly documented.

Changes have passed local verification; Git history records publication. The production preview is running at `http://localhost:3000`, with FastAPI at `http://localhost:8000`, against synthetic local data. No release or production data operation has occurred.

The [improvement plan](APP_IMPROVEMENT_PLAN.md) defines the 91+ targets and acceptance criteria. The [83-finding ledger](audit_reports/implementation-2026-10-03/finding-ledger.md) maps each finding to source and regression evidence. The [70/100 baseline audit](audit_reports/2026-10-03/00-report.md) remains unchanged; that historical score does not rate the modified application. **A fresh above-90 score has not been assigned.**

## What changed

| Area | Implemented result | Evidence and practical boundary |
| --- | --- | --- |
| Identity, security and privacy | Farm-bound PIN grants and live session families, final locked MFA generation checks, abandoned-setup cancellation, exact-session logout, owner transfer, eligible PIN provisioning/reset, paginated roster and deletion scrubbing | Actual authorization/race/SQL regressions and owner/worker browser journeys pass. Runtime production dependencies scan clean; one development-only upstream advisory has a verified local patch, explicitly documented. |
| Worker state and offline work | Transactional IndexedDB receipts persisted before acknowledgement, original retry identity, retained unresolved/rejected/expired work, scope and cross-tab fences, accepted-receipt cleanup and credential-free cached shifts | Actual production mobile cold install, disconnected completion/reload, reauthentication, reconnect and original-worker attribution pass. Offline recovery is limited to the previously authorized current-tab snapshot with a fixed 12-hour expiry; new sign-in remains online. |
| Clinical evidence and screening | Unassessable image quality, real prompt contracts and provenance, durable actual-call budget reservations, append-only review history, declared round targets/components and immutable coverage evidence | PostgreSQL/provider-contract/frontend regressions pass. Real-photo veterinarian-labeled evaluation and actual provider receipts remain pending. |
| Planning, finance and decisions | Corrected births/exposure, valuation, calibration/gain and delivery math, truthful IRR states, policy eligibility/caps/staged approved receipts, festival provenance and executed-input snapshots | Numerical, conservation, actual SQL lock/concurrency and UI regressions pass. Independent husbandry/finance review remains pending. |
| UI, frontend and Telugu | PIN creation/reset, task-only first-password rotation, transfer journey, preserved conflict drafts, saved-plan pagination, scoped dialogs/URL state, health context and upload timeout, translated field/section/narrative help and accessible disclosures | Full frontend tests and real UI/axe gates pass. Visual review preserves 10 before screens and verifies four corrected screens. Manual screen-reader/zoom and fluent Telugu/field usability remain pending; wider product ideas are tracked in the plan. |
| Database | Four forward migrations with scoped-session, review/ancestry, frozen evidence, budget, notification-outbox and durable maintenance-checkpoint invariants | Sole head `f8e2f6a0c5d3`; all 97 applied migration modules are unchanged. Fresh/old-head upgrade, safe empty roundtrip/model parity and 55 populated historical checks pass. Unsupported historical offline SQL export fails before partial output; a faithful exporter is not claimed. |
| Operations and reliability | Service-specific secret mounts/roles, private protected metrics, fair bounded retention, durable cross-process maintenance progress, deferred notifications, private-CA helpers, release reruns and migration-history CI guard | Actual synthetic Docker context/mount probes, protected ASGI metrics, process death/restart/concurrency and exact CI-shell fixtures pass. Normal production boot, received alerts, supported-load/soak, provider and staging drills remain pending. |
| Testing and maintainability | Safe isolated mutation attempts, trustworthy baseline/verdict/identity/coverage/resume rules, precise typed boundaries and permanent zero-debt test typing | 24 backend and 17 frontend mutation-harness contracts pass. Strict typing is clean across 158 source and 142 test files. Historical mutation scores remain untrusted; full fresh campaigns are still needed. |

## Final local receipts

| Gate | Result | Receipt |
| --- | --- | --- |
| Complete backend collection | 5,124 passed, 4 skipped; zero failures/errors; every one of 5,128 collected cases accounted for across disjoint isolated databases | [Final backend](audit_reports/implementation-2026-10-03/final-backend/README.md) |
| Backend coverage | 92.7189% combined, enforcing the unchanged 92% floor; 94.5437% line / 85.9931% branch | [Coverage and source verification](audit_reports/implementation-2026-10-03/final-backend/summary.json) |
| Complete frontend suite | 326 files / 5,269 tests passed; all existing global and scoped floors passed | [Frontend receipt](audit_reports/implementation-2026-10-03/final-verification/frontend-coverage-receipt.json) |
| Frontend coverage | 94.30% statement / 91.98% branch / 94.33% function / 96.47% line | [Coverage log](audit_reports/implementation-2026-10-03/final-verification/frontend-coverage.log) |
| Production build, frontend lint and TypeScript | Passed on unchanged production source; full lint/types also pass after the E2E-only assertions | [Build](audit_reports/implementation-2026-10-03/final-verification/frontend-build.log), [lint](audit_reports/implementation-2026-10-03/final-verification/frontend-lint.log), [types](audit_reports/implementation-2026-10-03/final-verification/frontend-typecheck.log) |
| Chromium + mobile | 74 passed, zero retries/failures/skips | [Browser receipt](audit_reports/implementation-2026-10-03/final-verification/chromium-receipt.json) |
| WebKit | 71 passed, zero retries/failures/skips | [Browser receipt](audit_reports/implementation-2026-10-03/final-verification/webkit-receipt.json) |
| Firefox (Linux Firefox 153.0) | 71 passed, zero retries/failures/skips; native macOS launcher remains limited | [Browser receipt](audit_reports/implementation-2026-10-03/final-verification/firefox-receipt.json) |
| Backend static/dependency lock gates | Nine commands passed; Ruff checks 404 files; strict typing zero; sole final migration head | [Static manifest](audit_reports/implementation-2026-10-03/final-static/manifest.json) |
| Populated historical upgrade | 55 checks passed; all original fixture facts preserved; invalid preflights fully roll back | [Migration rehearsal](audit_reports/implementation-2026-10-03/populated-migration-rehearsal-f8/README.md) |
| Local synthetic PostgreSQL restore | 49 tables / 2,458 rows; exact snapshot counts and row digests match; model parity and constraints pass | [Recovery rehearsal](audit_reports/implementation-2026-10-03/final-recovery/README.md) |
| Sampled production visual review | 10 before + 4 inspected corrected screenshots; Telugu headings and ownership-selector width/prompt corrected | [Review](audit_reports/implementation-2026-10-03/visual-review/review.md) |

Coverage percentages measure test execution, not application quality. Skips, advisory mitigation, unsupported operations and untested deployment/field conditions are explicitly recorded in the receipts.

## Remaining work to earn 91+ in every area

1. Run fresh full mutation campaigns with the repaired harnesses and independently review surviving mutations. This is remaining local validation; it is not blocked solely by external access.
2. Declare the supported farm/history/device/concurrency envelope, then profile queries, APIs, CPU simulations, bundles and backlog/restart soak against it.
3. Obtain fluent Telugu and manual accessibility review plus observed representative manager/worker task completion and recovery.
4. Independently evaluate husbandry/finance examples and veterinarian-labeled real screening photos; record uncertainty, model versions and provider sandbox receipts.
5. Complete a staging boot/TLS/secret-role/alert drill and database + object/image + configuration/key recovery with measured objectives. The local synthetic restore does not establish production RPO/RTO.
6. Evaluate the broader product improvements in the plan: guided basic planning, tag scanning, deeper page decomposition and observed task simplification.
7. Re-audit all ten areas using current implementation and these receipts. Above-90 targets remain unassessed until the necessary evidence exists.

The complete final matrix is **Chromium/mobile 74 + Linux Firefox 71 + WebKit 71 = 216 passed**, with zero failures, skips or retries. [The final verification index](audit_reports/implementation-2026-10-03/final-verification/README.md) links every gate, and [the independent integrity review](audit_reports/implementation-2026-10-03/final-integrity/README.md) preserves the migration/baseline/source and bounded artifact checks. The two E2E-only URL assertions are [ADD12](audit_reports/implementation-2026-10-03/browser-runtime/firefox-navigation-diagnosis.md); all production/unit sources remain unchanged from the complete build/coverage snapshot. The owned remote browser container was removed after the successful gate. The initial Firefox runtime timeout is retained transparently in [browser-runtime](audit_reports/implementation-2026-10-03/browser-runtime/initial-receipt.json); it is not counted as a passing application test.
