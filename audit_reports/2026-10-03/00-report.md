# Herdly application audit and improvement plan

Audit date: 3 October 2026, America/Phoenix. Source commit: `b285645b2a93709aa7294fc4698ee0f25839b13e`.

**Overall score: 70/100.** Herdly has strong technical foundations and substantial farm-specific capability. Its main weakness is operational trust: several paths acknowledge work that was not durably saved, certify incomplete health evidence, or produce incorrect planning and financial conclusions. Those failures matter more than feature count or visual polish when workers and owners rely on the application to run a farm.

The audit did not change application code. This directory contains the report, detailed reviews, verification logs and a per-file coverage ledger. The score is an engineering and product judgment against reliable production farm use, not a percentile, certification, or claim to have compared every competing application.

## Score and assessment

The weights reflect the consequences of missing farm work, inaccurate records and insecure shared-device sessions. The weighted result is 70.07, rounded to 70. Source review cannot establish real-farm usability, screen-reader proficiency, veterinary screening accuracy or production performance; those dimensions remain provisional.

| Area | Score | Weight | Assessment |
| --- | ---: | ---: | --- |
| UI, workflows, accessibility and localization | 75 | 15% | Coherent design system, Telugu support and field surfaces; incomplete onboarding, English explanations and dead-end actions |
| Frontend architecture | 77 | 10% | Strict types, shared components and typed queries; very large pages and repeated asynchronous ownership policies |
| Backend and API foundations | 84 | 12% | Extensive validation, idempotency and concurrency safeguards; several remaining transition and lock-order defects |
| Database integrity | 86 | 10% | Exact money, tenant foreign keys, constraints and migrations; screening provenance/review and operational bounds need work |
| State and offline reliability | 40 | 12% | Reproduced loss of queued work, false persistence acknowledgements and limited restart resilience |
| Security and privacy | 65 | 12% | Strong cryptography and configuration safeguards; PIN/global-account boundary, late manager sessions and retained sensitive data |
| Domain, screening and decision accuracy | 58 | 10% | Broad livestock/finance model; incomplete health rounds, unusable-photo healthy outcomes, incorrect planner units and subsidy/calibration rules |
| Performance and scalability | 72 | 7% | Many bounded reads and admission controls; unfair cadence sweep, ineffective provider cap and long retention transactions |
| Tests and quality assurance | 80 | 7% | 10,060 passing unit/integration tests; stale browser tests and missed real workflow invariants |
| Deployment, recovery and observability | 68 | 5% | Mature image/build/backup safeguards; dependency scans fail, secret delivery needs isolation and production metrics are disabled |

The application does not need a wholesale rewrite. Its validation, transaction discipline, design system and domain investment are worth preserving. The next investment should make the existing promises dependable before adding more modules.

## Report navigation

- [Per-file coverage ledger](01-file-coverage.csv) and [coverage methods](02-coverage-method.md).
- [Frontend and worker state](03-frontend.md), [domain page workflows](04-frontend-domain-pages.md).
- [Backend screening, health and notifications](05-backend-domain.md), [planning, finance and additional backend defects](06-backend-domain-extension.md).
- [Database, migrations and recovery scripts](07-database.md), [platform, security and deployment](08-platform-security.md).
- [Mutation measurement method](09-mutation-method.md) and [verification/probe evidence](evidence/README.md).

## Scope and evidence

The initial inventory contains **1,343 tracked files**, approximately **37.45 MB** and **596,278 newline-delimited lines**. The ledger includes every tracked path. All tracked content was inventoried, Python files were parsed with the project's Python 3.13 interpreter, JSON artifacts were validated, and generated client/model and localization structure was checked. Detailed semantic reviews cover authored application source and the data/platform/deployment tracks; the individual ledgers identify review depth.

The merged ledger records **436 complete semantic source reads**. All **251 backend/frontend application paths** were fully read or empty, together with all **99 migration paths**, **23 operational script/template paths**, **33 build/deployment configuration paths** and **16 mutation harness source files**. It separately records 403 complete generated structural reviews and two locale parity checks with sampled language meaning. No nonempty authored code, configuration, operational script or mutation-harness source gaps remain. Tests and historical/generated artifacts retain the explicitly different depth described below.

Generated contracts, lockfiles, historical audit prose, mutation result data and the large test estate have different review methods. **This is not a claim that every line of every test, generated file or historical artifact received manual semantic review.** The ledger makes that distinction explicit. Dependencies, build caches, local credentials and `.git` internals are not project-authored source and are excluded from the tracked-file count. No credential contents were included in the report.

| Check performed on current source | Result |
| --- | --- |
| Full backend suite, separate throwaway PostgreSQL database | 4,978 passed, 4 skipped; 27 minutes 53 seconds |
| Full default frontend suite | 5,082 passed across 314 files; 4 minutes 38 seconds |
| Backend lint and format | Passed; 377 files formatted |
| Backend strict mypy over app, scripts and mutation harness | Passed; 148 source files |
| Backend test typing ratchet | Holds at 1,778 existing typing errors; this is not fully typed tests |
| Frontend strict type check and lint | Passed |
| Frontend production build | Passed |
| Backend lockfile consistency | Passed |
| Live Chromium and mobile Chromium browser suite | 45 passed, 6 failed; 3 minutes |
| Accessibility browser gate | All six selected pages passed the serious/critical violation gate |
| OpenAPI current-source comparison | Exact structural equality; 101 paths, 235 schemas |
| Generated interface structural comparison | 231 schema interfaces checked; no findings in that check |
| English and Telugu key/placeholder parity | 2,797 keys in each; no missing, empty or mismatched placeholders |
| Migration head and current-schema comparison | One head; upgrade succeeded; `alembic check` detected no new operations |
| Frontend dependency audit | Failed: 36 findings including development dependencies; production-only scan reports 3 |
| Backend dependency audit | Failed: 13 known advisories against PyJWT 2.13.0 |

The six browser failures trace to stale tests: remote picker helpers expect a combobox although the current dialog trigger is a button; another assertion expects `VERIFIED` while the interface renders `Verified`. These are still release-gate failures. Fix the tests and execute the blocked journeys before claiming those workflows are browser-verified. Additional raw breeding and administration labels in the fixtures also need updating. A failed test blocked by an obsolete selector is not evidence that the underlying product workflow failed.

Committed mutation results independently recompute to **98.79% backend** and **80.82% frontend**, after taking the latest verdict for each mutant. These percentages exclude uncovered mutants and count timeouts as kills. They are historical campaign arithmetic, not new campaigns against today's source. Full harness review found additional measurement limitations: backend workers mutate different files in one shared checkout, nonzero test exits can be recorded as kills without a clean selection baseline, resumed verdicts lack source/test/coverage identity, and standalone compilation silently excludes direct function-return mutations. The frontend uses isolated in-memory mutation transforms, but still has error-classification and stale-campaign issues. **Do not treat these percentages as a verified estimate of today's test effectiveness.** See the [mutation method review](09-mutation-method.md). Current coverage percentages were not remeasured. Passing the existing suites does not establish the clinical or operational invariants exposed by the new probes.

## Issues to fix before broader production use

Each item links to the source responsible for the behavior. High means a material loss of work, misleading operational evidence or account/session boundary problem. Medium means a narrower correctness, workflow, cost or scaling defect. Source-confirmed mechanisms are distinguished from executable reproductions in the detailed reviews.

| Priority | Finding and observed consequence | Evidence and location | Required change |
| --- | --- | --- | --- |
| High | Switching farms during an offline drain sends the next old-farm write with the new farm header; the resulting 404 permanently drops it | Actual unmodified client/queue probe; [worker layout](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/worker/layout.tsx:129), [queue](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/lib/offline-queue.ts:401) | Live actor/farm scope, generation cancellation and validation immediately before each send; retain unprocessed records |
| High | Queue storage failure returns saved=true with zero persisted records, leaving the duty optimistically complete | Storage-denial probe; [queue](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/lib/offline-queue.ts:79) | Propagate persistence failure and roll back acknowledgement; never silently discard unsent records for capacity |
| High | Immediate End shift uses a badge refreshed every 1.5 seconds and can erase newly queued work without its confirmation | Current-component reproduction; [worker layout](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/worker/layout.tsx:154) | Read live scoped pending work at handover and subscribe to queue changes |
| High | Cancelling or leaving pending manager tablet setup can install a late manager session on the shared tablet | Delayed-response actual-function probe; [tablet login](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/worker/login/page.tsx:156) | Attempt/mount ownership, cancellation and revocation of abandoned session establishment |
| High | OpenAI-compatible screening drops the supplied detector, gate and specialist system prompts | Three stages produce identical outbound request bodies; [provider](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/services/screening/providers.py:226) | Send the appropriate prompt and verify the full request contract for each provider/stage |
| High | Unusable photos with quality_problem=true and flagged=false become HEALTHY, reinforced by green UI | Ten-photo real PostgreSQL probe; [pipeline](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/services/screening/pipeline.py:919), [screening UI](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/(app)/screening/page.tsx:364) | Explicit unusable/indeterminate/reupload outcomes; no healthy conclusion from insufficient evidence |
| High | An ET+HS all-herd duty becomes DONE after recording HS for one pen, leaving another pen untreated and no ET evidence | Actual API probe; [health API](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/api/health.py:926) | Round target snapshot, per-animal/component evidence and explicit partial completion |
| High | Backward planning divides litter size twice: a target of 100 males at litter size 2 and equal sex share instructs 50 breeding does instead of 100 | Pure-function reproduction with no mortality and full conception; [backward planner](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/simulation/backward_planner.py:268) | Explicit units and conservation checks for births, sex share, kidding does and service-ready does; reject impossible zero-conception cases |
| High | NLM subsidy is modeled for ineligible small units, without the national cap, and entirely reduces initial cash needs | Pure-function reproduction and official policy comparison; [simulation engine](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/simulation/engine.py:1633) | Versioned eligibility/eligible-cost rules, correct subsidy ceilings and approved staged receipts; include bridge funding and subsidy-free baseline |
| High | One farm error expires the remaining preloaded farm objects; subsequent farms fail and the sweep nevertheless advances | Three-farm real PostgreSQL reproduction; [cadence service](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/services/cadence.py:585) | Fresh per-farm loads or isolated sessions, successful-work accounting and failure/retry visibility |
| High | Membership-local PIN administration creates ordinary global user sessions, extending an owner's credential control beyond that farm | Source trace; API flow partially exercised, final permissions assertion not completed; [PIN reset](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/api/team.py:384), [PIN login](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/api/auth.py:1354) | Farm/membership/device-scoped PIN sessions and reset policy; restrict global identity operations from tablet credentials |
| High, conditional deployment exposure | Default secrets/app directory is gitignored but absent from the root Docker exclusions, allowing local/remote build context delivery | Source-confirmed configuration gap; [.dockerignore](/Users/pradeepreddy/Desktop/goat-farm-management-main/.dockerignore), [production Compose](/Users/pradeepreddy/Desktop/goat-farm-management-main/docker-compose.production.yml:40) | Exclude secrets and minimize build context; keep secret paths outside checkout |
| Medium | Recorded sale weight is ignored by farm calibration; a 35kg sale at ₹700/kg with a stale 20kg routine weight calibrates to ₹1,225/kg | Five-sale isolated database probe; [calibration](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/services/simulation_calibration.py) | Prefer actual sale weight; use dated fallback only with a freshness and evidence warning |
| Medium | A three-month post-weaner mortality probability receives an annualized calibrated value: 9.76% becomes 33.69% in the reproduced exposure case | Real PostgreSQL calibration probe; [calibration](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/services/simulation_calibration.py:761), [assumption units](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/simulation/assumptions.py:299) | Explicit time units throughout model inputs; annual conversion only for annual fields |
| Medium | Owner summaries count voided expenses and turn weight loss into positive growth | Real PostgreSQL probe: ₹1,000 voided + ₹600 replacement reports ₹1,600; 20→15kg over ten days reports positive gain; [owner overview](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/api/owner.py:166) | Shared active-ledger aggregation and chronologically ordered first/last weighings; reconcile owner totals to farm registers |
| Medium | Insurance renewal and animal exit use conflicting lock order; concurrent execution produces PostgreSQL 40P01 | Two-session reproduction; [finance API](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/api/finance.py), [animal service](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/services/animals.py) | Canonical Animal then Policy locking and post-lock validation; meaningful concurrency regression |
| Medium | Provider daily cap 8 still permits 10 calls in a single batch | Actual pipeline/database probe; [pipeline](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/services/screening/pipeline.py:475) | Atomic call reservations across batch, stages, fallbacks and workers |
| Medium | A planner conflict adopts the new revision number but retains old contents; the instructed retry overwrites another manager's change | Actual-function payload probe; [planner](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/(app)/planner/page.tsx:629) | Preserve draft separately, load full authoritative content and require deliberate merge/overwrite |
| Medium | Team UI cannot provision/reset PIN workers; a password worker with task-only access lacks the first-password-rotation flow on its automatic landing | Full source review; [Team](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/(app)/team/page.tsx:665), [landing policy](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/lib/permission-navigation.ts:12) | Complete both credential modes and first-login journeys through the UI |
| Medium | One-shot screening/restriction alerts skipped at night have no durable morning replay | Source-confirmed delivery lifecycle; [notification hooks](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/services/notifications/hooks.py:23) | Transactional event outbox, deferred delivery, retries and visible delivery state |
| Medium | SMS headlines report capped samples as totals: 12 low-stock items becomes 10; 70 overdue duties becomes 50 | Real PostgreSQL probes; [notification service](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/services/notifications/service.py:692) | Separate exact aggregate counts from bounded examples |
| Medium, configured scale threshold | Cadence cursor resets each tick; farms beyond batch_size × max_batches are never visited | Source-confirmed; defaults imply 1,000-farm threshold; [maintenance loop](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/main.py:503) | Persist sweep cursor across ticks and wrap fairly; measure per-farm sweep lag |

The source does not show arbitrary cross-farm task mutation from the offline replay bug: tenant filtering rejects the request. The demonstrated harm is deletion of queued work. Similarly, the PIN finding has TOTP-active and required-password-rotation guards; the issue is the remaining credential scope mismatch, not a claim that every owner account is accessible through PIN login.

The NLM source specifies a minimum 100-female/5-male unit, subsidy ceilings rising from ₹10 lakh to ₹50 lakh, and two releases tied to financing/completion milestones. Herdly instead treats approximate published subsidy ceilings as eligible-cost ceilings, halves them again, omits minimum/global-cap enforcement and subtracts the full modeled award at inception. Actual applicant approval was not evaluated. [DAHD January 2025 operational guidelines, printed pages 13–14](https://www.dahd.gov.in/sites/default/files/2026-04/NLMGuidelinesJan2025.pdf).

## Further correctness and completeness work

The detailed track reports retain exact locations, evidence and limitations for these additional issues:

- Insurance forms can be dismissed/reopened while a save is pending; the old completion closes the newer form and discards its draft. Animal phenotype controls accept visible changes during a captured save and then discard them. Finance URL filter adoption retains a stale pagination offset.
- Health Add event links can lose their intent/context. Older saved plans beyond the first 50 lack an app retrieval path. Telugu simulation explanations remain English despite catalog parity. Owner views can conflate failed refresh with empty/current data.
- Saved-scenario results can be associated with cached assumptions rather than the actual executed snapshot. IRR's approximate scan can report one rate for a synthetic series with three valid roots; reachability of that exact series through current scenarios is unverified. Festival storage allows only one date per Gregorian year and misses a second projected 2039 event.
- Opening young male stock is valued using a lower weight curve than the model's purchase/terminal valuation. Daily operations reuse morning occupancy when generating later feed deliveries, sending feed to the old building after a movement. Share valuation conventions and recompute delivery destinations by shift.
- Screening review input permits PostgreSQL-invalid NUL text. Same-status review edits lack a revision guard/history and can overwrite one another. Screening provenance foreign keys guarantee farm consistency more strongly than common image/run ancestry.
- Dashboard/report responses withhold the named herd total without animals.view but expose the same number in status dictionaries. Apply field permissions consistently across aggregate response fields.
- Account tombstones retain additional MFA/PIN/notification data. Farm ownership currently blocks account deletion because ownership transfer/farm deletion is missing. Remaining MFA mutation paths need the same token-generation revalidation as other credential changes.
- Every service mounting the shared secret directory can read every file in it, weakening the intended API/worker/migration separation. Use separate mounts and database identities. Production disables both Prometheus exposure and collection; replace this with authenticated/private observability.
- Retention farm discovery materializes all eligible row IDs before deduplication, and repeated batches remain inside a whole-farm transaction. Its counters can include rolled-back deletes. Define unresolved screening evidence protection, bounded transactions and successful-commit accounting.
- Offline migration SQL generation fails in several revisions despite advertised support. Backup/restore helpers skip dotenv CA fallback when both SSL settings are already exported; private-CA deployments can then fail verification. Online upgrade and schema checks passed, but that does not establish offline rendering or restore success.
- Hourly notification maintenance treats the hour as processed before completing its unbounded farm sweep; one exception can suppress the remaining farms until the next hour. Add bounded cohorts, per-farm isolation and durable completion/retry state.
- Mutation tooling needs exclusive single-mutant execution, clean baselines, campaign fingerprints and separate inconclusive/error states. The backend final-pass --dry-run flag currently still rewrites results and executes mutations; verification can fold stale records from previous passes. This is source-confirmed; no mutator was executed during this audit.
- Single-process memory controls limit horizontal availability. CPU simulations need isolated job execution, shared admission controls and cancellation before multi-replica scale. Adopt these when deployment requirements justify them, retaining the working modular monolith for ordinary requests.

The projected dual festival occurrence is supported by the calendar author's [2039 lunar calendar](https://www.al-habib.info/islamic-calendar/global_pdf/global-islamic-calendar-year-2039-ce.pdf); future local observation dates remain uncertain. The structural issue is the inability to store two occurrences, independent of exact observed days.

## Dependency findings and applicability

`pnpm audit` reports 36 findings: 1 critical, 10 high, 19 moderate and 6 low. The production-only graph reports 1 critical and 2 moderate. The critical advisory covers pinned Next.js 16.3.3; the maintainer identifies 16.3.6 as patched and limits the affected path to Node ImageResponse handling of untrusted SVG values. A search found no application `next/og` or ImageResponse usage, so **this audit has not established reachable remote code execution in Herdly**. Upgrade and re-lock anyway, and document applicability rather than treating the scanner count as an exploit count. [Next.js maintainer advisory](https://github.com/vercel/next.js/security/advisories/GHSA-vcvr-r3jv-pc5j).

The two production moderate findings involve transitive fast-uri 4.1.4; the installed graph also contains vulnerable development-tool dependencies including undici and brace-expansion. Test/build dependencies matter to CI, but are not automatically vulnerabilities in the shipped browser interface. The complete scanner logs are in evidence.

The backend scan reports 13 advisories against PyJWT 2.13.0. Many concern JWK/HMAC or mixed algorithm configurations, while Herdly pins RS256 and local keys, so a package-level warning alone does not prove authentication bypass here. Assess each advisory, upgrade to a tested patched release, regenerate the lock and rerun the scanner; do not assume upgrading to one version resolves every advisory, because one scanner entry lists no fix. [PyJWT maintainer advisories](https://github.com/jpadilla/pyjwt/security/advisories), [release changelog](https://pyjwt.readthedocs.io/en/2.15.0/changelog.html).

## Improvement sequence

These phases describe dependencies and completion criteria. They are not calendar or staffing promises.

| Phase | Deliverable | Evidence required to move on |
| --- | --- | --- |
| 1 Restore trust | Resolve high-priority work-loss, shared-device session, screening, treatment-round, planning/subsidy and cadence defects; remediate dependency/build-secret findings | Every recorded write is either durably pending or server-confirmed; unusable photos never look healthy; incomplete rounds remain partial; planner conservation/policy cases and account/farm scope tests pass; one farm failure cannot suppress another |
| 2 Complete real workflows | UI PIN provisioning/reset, password rotation, offline restart/resume, queue reconciliation, contextual clinical actions, plan search/history and conflict resolution | A manager and worker complete each journey through the UI without API tools; reload/disconnect/handover/concurrent-edit probes pass; all browser gates green |
| 3 Simplify field work | Role-specific Today view, scan animal tags, fast bulk records with preview, contextual help, correction/undo, Telugu notes and explanations | Observe real operators completing top five tasks; record time, errors, abandonment and recovery; field-reviewed translations and keyboard/screen-reader checks |
| 4 Make data useful | Append-only clinical/review corrections, execution provenance, reconciled inventory/finance, trusted calibration and measurable model uncertainty | A displayed figure traces to source facts, units, dates and model version; corrected ledger totals reconcile; mortality periods and valuation conventions agree; changed assumptions are visibly stale; independent domain evaluation passes |
| 5 Operate reliably | Private metrics, delivery/job outboxes, fair background work, bounded retention, recovery drills, performance budgets and load tests | Defined service targets are met; failed jobs are visible/recoverable; tested backup restores match records/objects; supported farm size and worker load are measured |
| 6 Expand from proven demand | Scale shared control storage/process jobs, external integrations and additional market segments only after field evidence | Demonstrated customer need, sustainable support/operating cost and no regression of established reliability targets |

For frontend maintainability, split orchestration, domain forms and results into explicit boundaries, especially the 4,251-line Simulation page. Standardize form attempt ownership, conflict policy, query freshness and durable-write receipts. Avoid moving code into many files unless the resulting boundaries make state transitions easier to reason about.

For backend/data design, keep the current API and transaction core. Centralize domain invariants such as treatment coverage, canonical lock order and provenance. Add explicit corrections and durable outboxes where records and delivery must survive restarts. Do not replace exact monetary accounting with floating-point calculations or an unreviewed event-system rewrite.

For UI, the existing screenshots show a consistent, readable desktop style and responsive cards. The mobile decision surface still asks users to understand many financial acronyms and scroll through stacked metrics. Lead with a plain-language result and three useful next actions, then reveal assumptions, cash needs, risks and detailed tables. Keep model version/fingerprint in a provenance detail view unless the user is comparing/reconciling runs. Add a guided basic planning mode with advanced controls available progressively.

For accessibility, use large touch targets for field work and verify actual keyboard, focus, zoom and screen-reader journeys, including Telugu. WCAG 2.2's minimum target criterion is 24 CSS pixels with exceptions; a 44-pixel field target is a proposed usability goal, not an assertion that every smaller current control fails the standard. [W3C target-size guidance](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum).

## Definition of an excellent farm application

There is no test that establishes best application ever. A defensible ambition is to be the application these farm operators trust most because it saves time, preserves evidence and improves decisions. Set measurable targets before expanding features:

- **Work integrity:** zero silently lost or mis-scoped recorded actions in disconnect/restart/farm-switch/handover tests; visible pending, accepted, conflicted and rejected receipts.
- **Usability:** proposed field targets are a first routine task within 10 minutes of onboarding and a common repeat action within 15 seconds. Validate with representative workers rather than accepting these estimates as achieved performance.
- **Clinical evidence:** no completed round without its required coverage; unusable/uncertain images explicitly labeled; screening evaluation against independently reviewed real farm photos and veterinarian-approved interpretation.
- **Decision integrity:** authoritative sale weight, calibrated source windows, cash and units, versioned assumptions, uncertainty and accessible correction history.
- **Performance:** target p75 LCP at or below 2.5 seconds, INP at or below 200ms and CLS at or below 0.1 on supported field devices/networks. These are accepted Core Web Vitals thresholds, not measurements obtained by this audit. [Google Web Vitals guidance](https://web.dev/articles/defining-core-web-vitals-thresholds).
- **Operations:** propose recovery objectives such as 15-minute data recovery point and one-hour restoration only after backup architecture and drills prove them; include uploaded images and configuration, not database rows alone.
- **Security:** regression-tested account/session/farm boundaries and a documented verification baseline such as [OWASP ASVS](https://owasp.org/projects/asvs?tab=main); no claim of ASVS compliance is made here.
- **Product value:** track active farms, successful first shifts, task completion/recovery, retained weekly users, support incidents, reconciliation effort and real farm outcomes with appropriate consent. Do not use more screens or more tests as a substitute for operator success.

The immediate path toward a 90+ assessment is demonstrable completion of the first four phases and measured field/recovery results. Fixing a list alone does not entitle the application to a higher score; re-audit the resulting behavior and evidence.

## Limits and remaining validation

This audit did not run a new mutation campaign, production load soak, full screen-reader campaign, real SMS gateway delivery test, object-storage disaster recovery drill or real-photo clinical evaluation. Firefox and WebKit suites were not executed in this run. Manual visual assessment used current browser-test desktop/mobile screenshots, and does not certify every dialog or responsive state. A local stack used separate disposable test data; production infrastructure and live farm data were not audited. File coverage depth and any remaining semantic gaps are documented in the ledger.

The temporary audit servers were stopped and the isolated browser-test database was removed after verification. The shared PostgreSQL/Colima service and other databases were left in place. Only the new audit report/evidence directory remains as a workspace change.
