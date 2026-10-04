# Herdly implementation plan: above 90 in every audit area

Created: 3 October 2026. Baseline commit: b285645b2a93709aa7294fc4698ee0f25839b13e.

**Objective:** fix every finding in the October audit and earn a fresh score of at least **91/100 in each of the ten areas**. The working targets below are 92–95. They are targets, not scores already achieved or a guarantee of clinical accuracy.

**Current status:** the independent audit of `fee30d5` reviewed all **83 original claims** and repaired **22 additional defects**. Current validation covers **5,166 unique backend passes with four skips**, **5,281 frontend passes** and **145 fresh Chromium/mobile/WebKit checks**, plus build, lint, strict typing and unchanged coverage floors. Backend combined coverage is **92.7538%**. The total reconciles a complete-suite snapshot and a later 212-case migration/screening sweep for the new f9 lock; [the independent report](audit_reports/independent-last-commit-2026-10-03/report.md) states exact source boundaries. Original Linux Firefox receipts were checked for integrity, without a fresh Firefox run. These fixes passed local verification; Git history records publication. The f9 migration is tested but has not upgraded the existing application database. **70/100 remains the historical baseline**, and the 91+ targets require fresh measurement and assessment.

The user has requested the fixes. Work should proceed through the batches below without repeatedly asking for permission for ordinary local implementation, tests or reversible changes. Missing staging access, representative data and independent reviewers should be recorded against the affected validation task while independent engineering continues.

## 1. What will change

1. Make worker actions durable: a success means the server accepted the write or the device actually persisted a pending operation.
2. Restrict tablet credentials and sessions to their intended worker, membership and farm; prevent abandoned manager setup from leaving authority behind.
3. Make clinical records truthful: unusable photos remain indeterminate, and incomplete treatment rounds remain partial.
4. Correct planning, subsidy, calibration, valuation and accounting calculations, with traceable inputs and independent examples.
5. Finish the worker/manager journeys and simplify mobile, Telugu and accessible use.
6. Repair database invariants, maintenance fairness, notification delivery, secret isolation, backup helpers and release evidence.
7. Make testing evidence trustworthy, then measure field usability, model accuracy, supported load and recovery.
8. Re-audit every area on the final commit. An improved average cannot hide an area below 91.

The current modular FastAPI/PostgreSQL and Next.js application will be retained. Refactoring will clarify contracts and state transitions. Billing, a microservice rewrite and speculative SaaS infrastructure are outside this improvement programme.

## 2. Baseline, scope and sources

The October audit accounted for 1,343 tracked files and fully reviewed all authored application, migration, operational, configuration and mutation-harness source. It recorded 4,978 backend tests passing with four skipped, 5,082 frontend tests passing, and 45 browser checks passing with six failures. Build, application typing and lint passed. Dependency scans failed. Historical mutation percentages have measurement limitations.

The [October audit](/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/00-report.md), [coverage method](/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/02-coverage-method.md) and [file ledger](/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/01-file-coverage.csv) are the evidence baseline. Earlier audit scores are historical; they do not supersede the newly reproduced defects.

The older [project playbook](/Users/pradeepreddy/Desktop/goat-farm-management-main/AUDIT_AND_IMPLEMENTATION_PLAYBOOK.md) describes an owner-operated system for roughly 20 farms. Use this as a working product assumption, not a measured capacity claim. Before performance testing, record the actual intended animals per farm, daily records/photos, concurrent workers, device classes, network conditions and history size. Check maintenance fairness above its current 1,000-farm boundary as a regression even if the deployment is smaller.

This plan maps **all 71 numbered findings**: FE1–13, DP1–4, D1–21, DB-01–09, platform sections 1–13, FM-01–04 and BM-01–07. PL-01–13 below are aliases for those platform section numbers. Dependency advisories, browser failures and wider product/validation work are additional tracked requirements.

## 3. Score targets and evidence needed

| Area | Baseline | Target | Conditions for an above-90 assessment |
| --- | ---: | ---: | --- |
| UI/UX, accessibility, localization | 75 | 92 | Complete role-based journeys; no dead-end actions; reviewed Telugu; usable mobile/dark/zoom states; keyboard and screen-reader journeys; observed field task success |
| Frontend architecture | 77 | 94 | Explicit ownership of requests/dialogs/drafts; shared scope and durable-write contracts; authoritative result snapshots; decomposed large routes with behaviour preserved |
| Backend/API | 84 | 95 | Correct transition coverage, field permissions, lock order and optimistic concurrency; safe input boundaries; idempotent retries; validated wire contracts |
| Database integrity | 86 | 95 | Review/provenance constraints; one migration head; preflighted upgrades and schema parity; bounded retention; faithful supported SQL export; tested data recovery |
| State/offline reliability | 40 | 94 | No silent loss, wrong-scope replay or false acknowledgement; restart recovery; safe handover; durable conflict/rejection receipts; reconnect reauthorization |
| Security/privacy | 65 | 94 | PIN-origin scope persists through refresh; credential race checks; safe manager setup; per-service secrets; deletion cleanup; documented advisory applicability and regression evidence |
| Domain/AI/decision accuracy | 58 | 92 | Correct conservation, finance and policy cases; truthful photo uncertainty; complete clinical evidence; independently reviewed husbandry/finance examples and real-photo evaluation |
| Performance/scalability | 72 | 92 | Declared supported envelope; measured API/device budgets; bounded/fair background jobs; query plans; load/backlog/restart soak; no unverified horizontal-scale claims |
| Testing/QA | 80 | 94 | All conventional gates green; original defects covered by meaningful regressions; credible mutation tool semantics and current campaigns; typing/coverage ratchets strengthened |
| Deployment/recovery/observability | 68 | 93 | Production-shaped boot/TLS/mount checks; private telemetry and working alerts; correct release digests; database/object/config restoration and measured RPO/RTO |

These conditions are necessary evidence, not an automatic scoring formula. A reviewer must evaluate the resulting application using the same audit dimensions. Pending field or deployment evidence remains explicitly pending; it does not receive an invented 90+ score.

## 4. Execution order

| Batch | Deliverable | Depends on | State |
| --- | --- | --- | --- |
| B0 | Reproducible baseline, browser gate repair, safe evidence tools and migration-history guard | Audit baseline | Implemented; harness contracts and exact Git-range guard probes pass; fresh full mutation campaigns pending |
| B1 | Dependencies, session boundaries, MFA races, privacy and secret isolation | B0 | Implemented; focused security, dependency and actual fake-secret isolation probes pass |
| B2 | Durable offline operations, safe handover and restart recovery | B0; B1 session contract | Verified locally; production cold offline/reload/reconnect/attribution journey passes |
| B3 | Clinical evidence, screening contract/budget, review provenance and cadence isolation | B0; B1 auth rules | Implemented; PostgreSQL and frontend regressions pass; real-photo/provider evaluation pending |
| B4 | Correct planning, finance, calibration, calendar and scenario conflicts | B0; coordinate shared API/migration changes with B3 | Implemented; numerical, locking and UI regressions pass; independent domain review pending |
| B5 | Complete workflows, translated/accessibility improvements and frontend/domain refactoring | B2–B4 contracts | Audited repairs verified locally; EN/TE accessibility and representative visual checks pass; broader product enhancements and field review pending |
| B6 | Reliable notifications, bounded maintenance, database operations, telemetry and performance | B1; B3 data invariants | Implemented; restart checkpoints, migration and private metrics checks pass; supported-load and staging drills pending |
| B7 | Final integration, field/provider/model evaluation, recovery/load drills and independent re-audit | B0–B6 | Local gates pass: 145 fresh browser checks and reconciled current backend/frontend evidence; original 216-check receipts preserved. Full mutation/load measurement, field/provider/staging validation and fresh assessment pending |

B2, B3 and B4 can overlap once their shared contracts are agreed. Independent work uses isolated worktrees or explicit file ownership; one integration owner controls the migration head, OpenAPI export and generated client. B5 should consume the settled contracts rather than refactor against moving interfaces. B6 telemetry must exist before its load and recovery evidence is collected.

### B0 — Establish reliable implementation and measurement

- Preserve the baseline audit and reproductions. Convert defect probes into focused regressions that fail for the actual faulty behaviour, not assertions that the fault still happens.
- Follow frontend/AGENTS.md and read the relevant installed Next.js guides before frontend code changes. Keep the current theme/component conventions and generated-contract workflow.
- Repair stale remote-picker and status assertions; follow the current accessible button/dialog contract and localized product labels. Do not alter product semantics just to satisfy obsolete selectors.
- Make mutation previews genuinely read-only, isolate backend mutants, enforce campaign identity, repair coverage refresh and verdict classification, and include valid function-return mutations. Do not run scored campaigns with the existing unsafe tools.
- Remove permanent filename-wide migration exemptions before introducing new schema changes.
- Record each check's commit, dependency locks, command, isolated database/configuration, result, logs and limitations. Distinguish current measurements from historical ones.

**Exit:** original regressions are reproducible; browser selectors reach their intended journeys; evidence-tool self-checks distinguish faults from infrastructure errors; migration immutability is enforced.

### B1 — Close identity, privacy and deployment boundaries

- Upgrade vulnerable direct/transitive dependencies to currently verified compatible fixes and regenerate both lockfiles. Evaluate every remaining advisory; replace or contain an affected dependency if no fix exists. Do not blindly upgrade to a version that leaves another advisory open.
- Define password-origin versus PIN-origin sessions. Bind PIN access and refresh to a live membership/farm, carry scope on refresh, and deny global identity/farm operations outside that scope. Legacy grants lack reliable origin metadata: when origin cannot be proved, invalidate all affected legacy unscoped access/refresh grants at cutover and require normal reauthentication. Test both old-token rejection and successful legitimate reauthentication; preserve pending worker operations through the cutover.
- Apply the same final locked token-generation check to all MFA mutations. Preserve legitimate password-authenticated owner behaviour and permitted worker tasks.
- Make tablet setup attempts own their result. Cancellation, navigation, unmount or a newer attempt must prevent late authority from installing; cancel/revoke any abandoned establishment safely.
- Give each service only its intended secret files/DB role. Exclude local secrets from the Docker context. Fix effective env/file precedence and blank-secret validation together.
- Scrub deleted-account MFA, recovery, PIN and recipient data using bounded restartable cleanup. Add an ownership-transfer/closure journey so owners can complete account deletion without erasing operational attribution.

**Exit:** session/permission/race matrices pass; fake-secret container checks prove isolation; scanner applicability is reviewed; deletion cleanup and legitimate onboarding work.

### B2 — Make offline success durable

- Use a versioned durable operation store with an actor/farm namespace, stable operation/idempotency identity, payload, expected revision, attempt history and receipt state. IndexedDB is the preferred implementation; include migration/recovery for existing queue records.
- Persist before presenting an offline success. Storage denial, quota exhaustion, corruption or unavailable storage must produce a recoverable error and restore visible task state. Capacity must never silently evict unsent work.
- Capture request scope explicitly and revalidate the live actor, farm and session generation before every send. Abort drains when that scope changes. Never use a new farm header for an old operation.
- Keep rejected/conflicted operations reviewable with a recovery action. A 404/409/4xx is not automatic permission to erase the only evidence of work.
- Read live queue state at End shift. Sync, retain/lock pending work to its actor, or use an explicit recorded discard decision. No implicit wipe or stale-badge decision; another worker must not see or replay the previous worker's data.
- Preserve minimum authorized task snapshots across reload/restart with clear stale/offline labels. New login, MFA and credential resets remain online. Cached authority is bounded; all reconnect writes must pass current server authorization.
- Exercise disconnect-before-send, server-accepted/response-lost, restart, farm switch, user switch, revocation, expired session, two-tab drain, duplicate retry and immediate handover.
- Update frontend/AGENTS.md and worker documentation to reflect the new queue/handover contract instead of retaining the current unconditional-wipe guidance.

**Exit:** every operation is either durably pending, accepted, explicitly conflicted/rejected or explicitly cancelled; none is silently lost, duplicated or replayed under another scope.

### B3 — Make clinical and screening evidence dependable

- Send detector/gate/specialist prompts using the actual provider contract and verify request content for every stage.
- Introduce clear unusable/indeterminate/reupload outcomes. Insufficient image evidence must never imply a healthy animal. Preserve human-review overrides and their provenance.
- Snapshot treatment-round targets and required components. Record per-animal/component coverage; only complete a duty when its required evidence is complete. Handle exits, additions and explicitly justified exclusions.
- Reserve provider calls atomically across workers, stages, retries and fallbacks. A daily cap must bound actual outbound attempts, including those in one batch.
- Validate review text with the existing PostgreSQL-safe boundary. Add a revision/history contract for reviews and database constraints for complete attribution and common image/crop/run ancestry.
- Isolate cadence farm transactions and retain fair progress. A failure in one farm must not expire or skip all later farm objects.
- Add an append-only correction/void workflow for clinical records, with schedule/withdrawal facts recomputed through domain rules. Do not rewrite immutable clinical history invisibly.

**Exit:** the original clinical/provider/cadence probes pass as corrected invariants; direct SQL cannot insert inconsistent review/provenance states; legacy questionable outcomes are identified for review without inventing medical facts.

### B4 — Make decisions and financial results trustworthy

- Correct the backward requirement chain with explicit units; reconcile births, sex share, litter size, survival and required does. Zero conception must not become guaranteed success through an unlimited-retry shortcut.
- Use sale weight as the authoritative price-calibration input; constrain and label stale fallback weighings. Match mortality conversions to annual versus whole-phase units.
- Share opening/purchase/terminal valuation conventions. Recompute delivery destinations after actual simulated movements rather than reusing morning occupancy.
- Implement versioned NLM eligibility, eligible-cost exclusions, subsidy ceilings and approved staged receipts. Separate unapproved estimates from approved funds and model bridge financing; preserve a subsidy-free baseline.
- Correct owner ledger aggregation and chronologically ordered weight gain. Reconcile owner summaries against corrected farm registers and handle negative/zero gain explicitly.
- Resolve insurance lock order consistently across renewal and sale/death.
- Make IRR ambiguity explicit and prefer a traceable NPV/cashflow view when returns are nonunique or undefined. The audit's synthetic multi-root case is a numerical contract test, not proof that that exact scenario is currently reachable.
- Support multiple festival occurrences in a Gregorian year and explicit date overrides/provenance. Future local observation dates must remain qualified.
- Bind a displayed result to the actual executed assumption snapshot, revision and model version. Separate current server contents from local drafts on conflict; provide search/pagination for all saved plans.

**Exit:** independent calculation fixtures, conservation identities and concurrency cases pass; displayed results trace to the executed inputs and source records; policy estimates never appear as guaranteed initial funding.

### B5 — Finish the product journeys and simplify the code

- Complete UI PIN creation/reset and first-password rotation for task-only workers. Paginate the roster and ensure users after its first 100 entries remain selectable.
- Standardize form attempt ownership, pending-edit policy and stale-completion behaviour. An old response cannot dismiss a newer draft.
- Repair finance URL pagination adoption, health create links/context, owner refresh errors and upload timeout compatibility.
- Translate field/section explanations, uncertainty, sync states, recovery actions and validation messages. Retain key parity and obtain field review of Telugu meaning.
- Decompose Simulation, Planner and other large pages into domain forms, queries/commands, selectors and result views. Extract backend decision/accounting units where this makes invariants clearer. Preserve generated contracts and shared components.
- Provide a role-specific Today view, guided basic planning, progressive advanced settings, tag lookup/scan, batch previews and explicit correction history. Keep advanced metrics/provenance available without making them the first mobile decision surface.
- Preserve theme tokens, dark pairs and the existing shared component system. Verify narrow/mobile/tablet/desktop layouts, focus order, keyboard, zoom, contrast and screen-reader status announcements.

**Exit:** all five essential journeys in section 7 work entirely through the UI; no English-only explanation in Telugu mode; observed users can recover from failures without API tools or developer guidance.

### B6 — Operate predictably and prove capacity

- Use a durable notification outbox for one-shot events and quiet-hour deferral. Bound farm cohorts, isolate failures and advance progress after successful processing. Count exact totals separately from examples.
- Refactor retention discovery, cohort transactions and commit-only counters together. Protect active/unresolved screening evidence; bound cascade/lock waits and retries.
- Deliver a faithful supported offline SQL-export path without casual edits to applied revisions. Preserve preflight guards, rehearse affected upgrade/downgrade exports and correct lock-window documentation.
- Fix private-CA fallback in backup/restore and run the actual private-CA path. Reconcile configuration guards with the effective Compose values.
- Enable private operational metric collection and alert on queue age, job failure, sweep lag, provider budget, notification deferral/failure and restore freshness. Separate collection from public exposure.
- Make release reruns update their owned digest block and keep image, SBOM/provenance and release notes consistent.
- Profile APIs, SQL plans, frontend bundles and CPU simulations under the declared workload. Use process/job isolation, cancellation and shared admission controls where needed to meet that workload; do not claim multi-replica coordination from local memory.
- Build database plus image/object/config backup recovery with the keys required to restore it, then measure recovery objectives.

**Exit:** maintenance remains bounded and fair under backlog/failure/restart; staged alerts actually fire; release receipts agree; workload and recovery results meet the published targets.

### B7 — Validate the whole app and earn new scores

- Run final backend/frontend unit, typing, lint, coverage, build, migration and API/client-parity gates against the same commit.
- Run Chromium, mobile Chromium, Firefox and WebKit journeys. Resolve failures at their cause; do not skip a failing test to make the dashboard green.
- Run fresh mutation campaigns only after their tools are repaired. Publish covered/uncovered, sampled/full, invalid/inconclusive and reviewed-equivalent counts separately. Fix meaningful survivors; do not chase a headline percentage with tautological tests.
- Maintain the achieved zero-error backend test typing gate without broad ignores or disabled checks. Report separate line/branch metrics and retain the existing coverage floors.
- Conduct representative operator, Telugu/accessibility, veterinarian/photo and husbandry/finance review; run provider sandbox/integration checks, load/soak and full restoration drills.
- Create a new dated assessment with current evidence, remaining limitations and a score per area. Keep the original audit unchanged for comparison.

**Exit:** no unresolved audited defect; every necessary gate has evidence; each area independently earns at least 91. If external evidence is missing, report engineering completion and the pending validation rather than claiming the target.

## 5. Complete finding-to-fix ledger

The rows below preserve the intended fixes and acceptance criteria. Current implementation, evidence and dispositions live in the [finding ledger](audit_reports/implementation-2026-10-03/finding-ledger.md). The changes have passed local verification; Git history records publication, and deployment validation remains separate. A required gate is complete only when its receipt passes; broad suite totals do not substitute for missing direct proof.

### Frontend and worker state

Source: [frontend review](/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/03-frontend.md).

| Finding | Batch | Concrete fix | Required proof |
| --- | --- | --- | --- |
| FE1 | B2 | Freeze and revalidate actor/farm/session scope for each replay; retain interrupted operations | Switch farms between two sends; second old-farm operation stays pending and uses no new-farm header |
| FE2 | B2 | Return a persistence receipt only after durable storage; restore optimistic state on failure | Denied/quota/corrupt storage never displays saved or loses the pending operation |
| FE3 | B2 | Subscribe to queue changes and read live depth at handover; remove implicit wipe | Enqueue then immediately End shift; pending-work handling cannot be bypassed by badge lag |
| FE4 | B1 | Fence/cancel manager setup by attempt and mount ownership; revoke abandoned establishment | Cancel/navigation/new attempt before response leaves no manager authority on the tablet |
| FE5 | B5 | Add permission-correct PIN provisioning/reset to Team UI | Owner creates/resets a PIN worker and that worker signs in using only the UI |
| FE6 | B5 | Provide required password rotation before the task-only worker landing | First password login completes rotation without inaccessible routes or extra privileges |
| FE7 | B4 | Separate draft from authoritative server contents; offer deliberate conflict resolution | Two managers edit; stale retry cannot silently overwrite the newer plan |
| FE8 | B5 | Localize all simulation field/section help and review Telugu meaning | English/Telugu parity plus complete-screen language review |
| FE9 | B4 | Return and use the actual executed snapshot/revision/fingerprint | Concurrent assumption change cannot label results with a stale cached fingerprint |
| FE10 | B4 | Add searchable paginated saved-plan retrieval and explicit legacy-invalid handling | Find/open a plan beyond row 50; invalid older content has a recoverable state |
| FE11 | B2 | Persist minimum authorized task snapshots and operation state for bounded offline restart | Reload/restart while disconnected restores the correct worker/farm state without another actor's data |
| FE12 | B5 | Show refresh failure and stale timestamps separately from an empty owner dataset | Failed refresh preserves labeled old data and offers recovery |
| FE13 | B5 | Route photo upload through the shared timeout/cancellation compatibility policy | Upload works when native timeout support is absent; timeout/abort remains visible |

Source: [domain page review](/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/04-frontend-domain-pages.md).

| Finding | Batch | Concrete fix | Required proof |
| --- | --- | --- | --- |
| DP1 | B5 | Lift insurance mutation/dialog identity to the owner; close only the originating form | Old Add/Renew/Claim completion cannot dismiss a newly opened draft |
| DP2 | B5 | Freeze phenotype inputs during captured save or retain later edits explicitly | Pending save cannot accept and silently discard newer visible values |
| DP3 | B5 | Reset/adopt finance pagination with externally adopted filters | Page-two to narrow-filter navigation fetches the correct first page |
| DP4 | B5 | Preserve animal context and recognized create intent in health links | Both empty-state CTAs open the intended form; profile link preselects its animal |

### Backend, clinical and decision correctness

Sources: [backend domain review](/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/05-backend-domain.md), [extended review](/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/06-backend-domain-extension.md).

| Finding | Batch | Concrete fix | Required proof |
| --- | --- | --- | --- |
| D1 | B3 | Send each stage's system prompt through the OpenAI-compatible adapter | Captured requests contain the correct distinct prompt plus image/request contract |
| D2 | B3 | Preserve quality failure as unusable/indeterminate throughout API/UI | Quality-problem photos never produce a healthy conclusion; reupload action is available |
| D3 | B3 | Snapshot round targets/components and aggregate real coverage | One pen/one vaccine cannot complete a two-pen ET+HS duty |
| D4 | B3 | Atomically reserve actual outbound calls across stages/workers | Cap eight cannot produce ten attempts in a concurrent batch |
| D5 | B4 | Handle missing/multiple IRR roots and disclose solver domain; prioritize NPV | Independent multi-root/no-root/ordinary cases return truthful ambiguity/values |
| D6 | B6 | Query exact aggregate counts independently of bounded examples | Twelve low-stock items and seventy overdue duties retain their true totals |
| D7 | B6 | Persist quiet-hour events with due time, dedupe and retry visibility | A one-shot night alert survives restart and becomes eligible in the next delivery window |
| D8 | B4 | Store occurrence lists/date overrides with source uncertainty | Two occurrences in one Gregorian year are retained and scheduled distinctly |
| D9 | B4 | Lock Animal then Policy consistently and revalidate after locks | Concurrent renewal versus sale/death yields a valid serial result without uncaught deadlock |
| D10 | B4 | Prefer sale weight; apply freshness/evidence rules to fallback weighings | Five 35kg sales at ₹700/kg calibrate correctly despite stale 20kg records |
| D11 | B3 | Apply PostgreSQL-safe text validation to review notes | NUL input is rejected at the HTTP boundary with validation error, not database 500 |
| D12 | B3 | Add review revision and append-only history | Stale same-status/ABA review cannot overwrite another reviewer's evidence |
| D13 | B4 | Share active-ledger/type filters in owner aggregates | Voided ₹1,000 plus ₹600 replacement reports ₹600 and reconciles to the ledger |
| D14 | B4 | Use ordered first/last weighings and explicit zero/negative gain semantics | 20→15kg over ten days reports loss; nonmonotonic observations use correct endpoints |
| D15 | B3 | Use fresh IDs/sessions after each farm failure and honest progress accounting | First-farm failure still permits later farms in that page to receive their duties |
| D16 | B4 | Divide litter size once; distinguish sex births/does/services; reject zero conception | Target 100 males at litter two/equal sex/no loss requires 100 does and 200 total kids |
| D17 | B4 | Convert post-weaner exposure to its phase length | Recorded exposure produces the correct three-month probability rather than annualized input |
| D18 | B4 | Share sex/class weight curves for opening, event and terminal values | Equivalent male stock is valued consistently across entry and exit |
| D19 | B4 | Recompute shift destinations after movements/exits/births | Afternoon/night feed goes to occupied post-movement buildings and reconciles |
| D20 | B4 | Version NLM eligibility/caps/cost exclusions and staged receipts | Ineligible unit receives no estimate; national cap holds; unapproved full grant never erases initial cash needs |
| D21 | B1 | Apply animal/clinical permissions to all sibling aggregate fields | Dashboard/report-only role cannot recover a withheld herd count from status dictionaries |

### Database and operational scripts

Source: [database review](/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/07-database.md).

| Finding | Batch | Concrete fix | Required proof |
| --- | --- | --- | --- |
| DB-01 | B6 | Implement a faithful offline SQL-export compatibility path for historical preflights; preserve applied history | Supported full-chain and affected downgrade SQL render/apply correctly with guards; unsupported ranges fail before partial output |
| DB-02 | B6 | Resolve missing optional CA from the pinned dotenv snapshot while preserving explicit precedence | Exported SSL classification plus dotenv-only CA succeeds with verify-full; wrong CA fails |
| DB-03 | B6 | Commit bounded root/dependent cohorts; bound cascade waits and pass work | Large backlog/locked child has finite transactions and retries without FK damage |
| DB-04 | B6 | Discover unique farms in bounded SQL keyset pages | Query transfer/memory scales with the farm page, not every expired row |
| DB-05 | B6 | Merge deletion counts only after commit; catch commit failures within isolation | Failure after deletion or at commit reports no phantom durable deletion |
| DB-06 | B3 | Append preflighted explicit pending/both-null or reviewed/both-present CHECK | Direct SQL rejects either partial reviewer/time pair; existing bad rows trigger a clear preflight |
| DB-07 | B3 | Enforce same image/crop/run/finding ancestry, including whole-photo cases | Same-farm wrong-parent writes fail; valid pipeline and retention paths still work |
| DB-08 | B1 | Respect env key presence including explicit empty overrides | Guard agrees with resolved Compose for plain/FILE/empty/absent combinations |
| DB-09 | B6 | Correct lock documentation; use intentional validation transaction boundaries for future revisions | Runbook makes no false nonblocking claim; representative lock-window rehearsal is recorded |

### Authentication, privacy and platform

Source: [platform review](/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/08-platform-security.md). PL numbers correspond to its section numbers.

| Finding | Batch | Concrete fix | Required proof |
| --- | --- | --- | --- |
| PL-01 | B1 | Bind PIN-origin access/refresh to live membership/farm; define global-route policy and legacy invalidation | Login/refresh cannot authorize another affiliation or global identity changes; permitted worker actions succeed |
| PL-02 | B1 | Project individual secret files/roles per service | Disposable containers with fake sentinels can read only their allowed secrets; intended boot succeeds |
| PL-03 | B1 | Scrub deletion credential/recipient material and backfill tombstones | Interrupted cleanup resumes; deleted identities retain attribution but no dormant secret/phone linkage |
| PL-04 | B3 | Preserve fair cadence cursor across ticks/restarts and wrap on exhaustion | Above one-tick capacity, later IDs receive service despite early failure |
| PL-05 | B1 | Compare authenticated generation under final MFA locks | Concurrent revocation blocks stale confirm/disable/recovery-regeneration writes |
| PL-06 | B6 | Bound/resume hourly farm cohorts and mark completion after work | One error or restart cannot suppress later farms for the hour |
| PL-07 | B1 | Exclude local deployment secrets from Docker build contexts | Fake sentinel is absent from builder-visible context; normal image builds |
| PL-08 | B6 | Collect private metrics independently of public endpoint exposure | Internal scraper works; unauthorized exposure fails; failed-job alert actually triggers |
| PL-09 | B5 | Construct PIN-reset membership response using acting owner | Immediate response matches roster eligibility for allowed and denied targets |
| PL-10 | B5 | Paginate supported worker roster and wire UI navigation | Worker beyond first 100 is selectable; pages contain every eligible worker once |
| PL-11 | B1 | Require nonblank plain/file notification credentials | None/empty/whitespace rejected; valid fixture configuration works without logging secret |
| PL-12 | B6 | Replace release-owned digest notes idempotently on rerun | Digest A→B updates notes/artifacts while preserving unrelated notes |
| PL-13 | B0 | Remove broad migration-path exceptions or bind incident exceptions to exact content | Unrelated edit/delete/rename rejected; new revision accepted |

### Test measurement tools

Source: [mutation method review](/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/09-mutation-method.md).

| Finding | Batch | Concrete fix | Required proof |
| --- | --- | --- | --- |
| FM-01 | B0 | Require clean selection baseline and structured behavioural/invalid/infra/timeout verdicts | Baseline/config/import/no-tests faults never improve kill numerator |
| FM-02 | B0 | Bind IDs/resume/reverify to source/test/coverage/config/lock/edit identity; guard shared execution; explicitly retry infrastructure/error/inconclusive attempts | Changed source/tests invalidate reuse; stale offsets refused; retryable failures never count as completed experiments; compatible terminals resume |
| FM-03 | B0 | Replace per-test coverage contributions; remove stale/deleted entries; publish atomically | Removed branch/test loses old coverage; failed refresh cannot silently publish a complete map |
| FM-04 | B0 | Make full mode complete or explicitly sampled; make smoke fail correctly | Every recorded coverer runs in full mode, including fixtures beyond sixty; missing/error/timeout baseline cannot pass smoke |
| BM-01 | B0 | Isolate each mutant checkout/database, or serialize checkout-wide | Concurrent imported modules contain only the intended mutant; source restoration survives failure |
| BM-02 | B0 | Distinguish assertion kills from pytest/infra/invalid/timeout failures using baseline | Only reproducible mutant-specific behavioural failure is scored as a kill |
| BM-03 | B0 | Verify source/original statement and immutable campaign identity before apply/resume | Stale source/tests reject reuse and mutation before a source write |
| BM-04 | B0 | Make final-pass dry-run exit before writes/process launch | Sources/results byte-identical; no test subprocess spawned |
| BM-05 | B0 | Fold only current target attempts using their own stored status policy | Earlier timeout records cannot be reclassified by a different current pass |
| BM-06 | B0 | Score latest compatible result per mutant/campaign | Repeated/reverified append rows cannot double-count |
| BM-07 | B0 | Compile mutations in valid original function/file context | Valid return/await/yield expression sites appear; truly invalid mutants remain excluded |

## 6. Wider improvements required for the targets

| Requirement | Batch | Completion evidence |
| --- | --- | --- |
| Dependency remediation | B1 | Current production and build/dev scans; compatible lockfiles; reachable Critical/High issues resolved; any remaining advisory has explicit applicability, containment, owner and review date |
| Browser release gates | B0/B7 | Corrected helpers plus completed real workflows in all supported browser engines, without hiding failures |
| Ownership transfer/account closure | B1/B5 | Owner can transfer/close safely; recipient ownership accepted; no orphaned farm or lost attribution |
| Clinical corrections and retention policy | B3/B6 | Original evidence retained, corrections traceable, derived facts recomputed, unresolved cases protected |
| Understandable mobile planning | B5 | Plain-language decision/next actions before detailed metrics; assumptions, uncertainty and comparisons remain discoverable |
| Accessible and translated field work | B5/B7 | Reviewed Telugu screens/errors/help; dark/zoom/mobile/keyboard/screen-reader journeys; field-sized touch targets |
| Measured architecture/performance | B5/B6/B7 | Explicit request/draft ownership; measured bundle/API/query/CPU budgets; shared controls if deployment has multiple replicas |
| Test typing and behavioural coverage | B7 | Strict test error count driven to zero; separate line/branch reports; important failure/permission/concurrency paths proven |
| Trusted model/provider evidence | B3/B4/B7 | Versioned real-photo and independent finance/husbandry evaluation with declared limitations |
| Complete disaster recovery | B6/B7 | Restore reconciles database, images/objects, configuration, keys and post-deletion cleanup; timestamps prove objectives |

Optional voice notes or other new integrations should follow demonstrated field demand and basic journey completion. They must not displace a remaining correctness or reliability fix.

## 7. Essential end-to-end acceptance journeys

1. **Manager provisions a worker:** create through Team UI, choose password/PIN mode, set permissions, perform first rotation if needed, and verify the correct landing and available duties.
2. **Manager pins a shared tablet:** authenticate with required second factor, choose farm, leave no manager session, cancel safely at every pending step, then let a PIN worker sign in.
3. **Worker performs a disconnected shift:** capture duties, survive response loss/reload/restart, observe pending receipts, reconnect with current authority, and resolve rejection/conflict without losing evidence.
4. **Safe handover:** immediately after enqueue, End shift shows current work; a different worker receives neither the previous user's data nor authority; eventual sync does not duplicate records.
5. **Manager reconciles clinical and financial work:** partial rounds remain partial, poor images require reupload/review, corrections reconcile totals, competing plan/review edits conflict honestly, and results display their executed inputs.

Run these on representative mobile/tablet hardware in English and Telugu, including keyboard/screen-reader variants where applicable. A UI journey is not complete because its API can be called manually.

## 8. Measurement and release gates

### Evidence that can be established locally

Use disposable PostgreSQL databases, invented accounts/photos and mocked/sandbox providers. Prove tenant/field/session matrices, concurrency, durable queue faults, numerical identities, real SQL constraints, migrations, effective configuration, fake-secret mounts, builder context, mutation-tool semantics and release-note reruns. Do not connect regression fixtures to ordinary development or production data.

For each implementation batch, first run its focused tests and relevant static checks. Run full integration suites at shared-contract/batch boundaries and against the final commit; do not repeat unchanged long suites without a reason. Simple copy/style/documentation changes use the existing checks and visual review instead of mirrored tests.

The following are current project entry points; dependency or command changes must be reflected in CI and this plan:

~~~sh
# From backend/, using a newly provisioned unique disposable database:
GOATFARM_TEST_DB=herdly_implementation_batch_test .venv/bin/python -m pytest -q --cov=app --cov-branch --cov-report=term --cov-report=xml
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/python -m mypy --strict app scripts mutation
.venv/bin/python -m mypy --strict tests
.venv/bin/python scripts/mypy_tests_ratchet.py
uv lock --check

# From frontend/:
pnpm lint
pnpm typecheck
pnpm test:coverage
pnpm build
pnpm exec playwright test
pnpm audit
pnpm audit --prod
pnpm audit:verified
node --test mutation/mutate_harness.test.mjs
~~~

Provision unique per-run database names rather than reusing the illustrative name concurrently. Migration commands must also point explicitly at an isolated URL: test fresh upgrade, representative upgrade, safe supported downgrade/re-upgrade, one head and Alembic model parity. Regenerate OpenAPI/client through the project's export/Orval workflow and verify generated diffs rather than editing generated interfaces by hand. Use the pinned backend audit toolchain and frozen exported requirements for backend dependency scans.

### Evidence requiring deployment, providers or people

| Validation | Responsible role | Artifact required |
| --- | --- | --- |
| Real worker/manager usability | Product owner plus representative operators | Task completion, time/errors/recovery notes and recorded issue resolutions |
| Telugu and accessibility | Fluent field reviewer and accessibility reviewer | Screen-by-screen language review; keyboard/zoom/screen-reader checklist and resolved defects |
| Photo screening | Veterinarian/domain reviewer plus engineering | Independently labeled consented dataset; per-class/error/quality results, disagreement review, versioned provider/prompt |
| Husbandry/finance model | Domain and finance reviewer | Independent example calculations, assumptions/units/policy review, reconciled source scenarios |
| Provider integration | Engineering with sandbox/test access | Request/response/delivery/cost/error receipts; no uncontrolled messages or paid calls |
| Production-shaped performance | Engineering/operations | Declared workload/hardware/network, latency/resource/lock results, backlog and restart soak |
| Recovery and alerts | Operations/product owner | Restored database/object/config reconciliation, key availability, measured timings, actual test-alert receipt |

These roles describe required evidence, not a promise that those people or accounts are already available. Missing external inputs block only the affected gate, not unrelated fixes. Mocked tests do not stand in for these results.

### Proposed measurable targets

- **Integrity:** zero silent operation loss, wrong-scope replay or duplicate accepted writes in the fault matrix; all conflicts/rejections have durable, understandable receipts.
- **Field usability:** initial targets are first routine task within ten minutes of onboarding and a common repeat action within fifteen seconds; at least 95% observed unaided completion of the five core journeys in a documented representative study. Publish sample size/device/language and failure cases; this is not a universal population claim.
- **Accessibility:** evaluate applicable WCAG 2.2 AA requirements for full journeys, including human checks. Use 44px field touch areas where practical; automated axe success alone is insufficient. [W3C WCAG 2.2](https://www.w3.org/TR/WCAG22/).
- **Frontend performance:** p75 LCP ≤2.5s, INP ≤200ms, CLS ≤0.1 on the supported field-device/network cohort. Measure production-shaped builds and then field results; local desktop speed is not enough. [Core Web Vitals thresholds](https://web.dev/articles/defining-core-web-vitals-thresholds).
- **API/background work:** publish p95 read/write latency, queue/sweep age, memory/DB-lock and per-pass budgets from a baseline before accepting capacity. Show no starvation or unbounded work at that declared envelope.
- **Recovery:** target RPO ≤15 minutes and RTO ≤1 hour only after WAL/PITR or equivalent frequent recovery architecture and full drills establish them. The current documented nightly/four-hour baseline does not already meet this target.
- **Clinical evaluation:** the veterinarian defines acceptable recall, missed-risk and indeterminate outcomes for the intended screening use. Publish sensitivity/error rates with uncertainty and representative photo conditions; do not invent a 95% medical-accuracy target to match the product score.
- **Security:** verify applicable controls using a documented ASVS baseline, explicit positive/negative role/session tests and deployment evidence. Do not claim ASVS certification from this plan. [OWASP ASVS](https://owasp.org/projects/asvs).

## 9. Migration, historical data and rollout discipline

- Add forward migrations and preflight existing inconsistent data. Report and quarantine ambiguous clinical/provenance cases; never fabricate reviewer identities, treatment evidence or ancestry.
- Before any data repair, produce exact affected counts, proposed transformations and a rollback/recovery approach. Reconcile the resulting records. Preserve immutable originals when corrections are the appropriate domain action.
- Any unavoidable historical migration compatibility exception must be narrowly tied to the reviewed content, preserve online behaviour and be tested. A permanent filename exemption or silently skipped guard is not acceptable.
- Version session, queue, result and policy contracts. Specify behaviour for old tokens, pending queue entries, cached scenarios and old clients; invalidate or migrate deliberately.
- Rehearse rollout with representative synthetic/history fixtures and backups, then use staged release and monitored verification. Keep previous releases/data restore paths available.
- Privacy cleanup must also be replayed after older backup restoration; document retention exceptions and expiry across backups/object stores.

## 10. Definition of done and progress record

A patch being written is **Implemented**. Its required checks passing makes it **Verified locally**. Deployment/field gates, where required, make it **Validated in use**. A finding is **Closed** only when its actual consequence is resolved and evidence is linked. A deliberate limitation needs an explicit supported contract and justified mitigation; it cannot silently become Done.

| Milestone | Current status |
| --- | --- |
| Root plan and full finding mapping | Complete |
| Code/schema/dependency fixes | Implemented across all 71 original findings and 12 additional issues; core local verification passes |
| Updated regression and browser gates | Verified locally: reconciled current backend/frontend suites and 145 fresh Chromium/mobile/WebKit checks pass; original 216-check matrix preserved, Firefox not rerun |
| Current trustworthy coverage/mutation evidence | Harness contracts and final conventional coverage floors pass; full fresh mutation campaigns pending; no campaign score claimed |
| Field/model/provider/load/recovery validation | Local populated upgrades, restart/process and snapshot recovery checks pass; actual field/load/deployment receipts still required |
| Every area re-audited above 90 | Not assessed |

Maintain a tracker with: finding ID, batch, owner, status, fixing commit, verification receipt, migration/compatibility notes and remaining external evidence. Append a progress summary to this file after each completed batch.

The final assessment must answer three questions with evidence: **Was every audited consequence resolved? Can real operators finish and recover their work? Does every area independently score at least 91 on the current app?** Until those answers are established, the target remains a plan rather than an achieved result.

### Implementation update — 3 October 2026

The independent audit reviewed all 71 original finding IDs and 12 additional issues, then repaired 22 residual application/verification defects. The new forward migration reaches `f9a3b7c1d5e2`, preserving all 101 audited migration modules and known legacy review facts. A complete corrected-source suite passed 5,165 tests with four skips; subsequent migration lock hardening passed the 212-case affected sweep. Deduplicated current coverage accounts for 5,166 passes and four skips across all 5,170 collected cases, with 92.7538% combined app coverage. Frontend passed 5,281 tests, 96.61% line and 92.09% branch coverage, build and static gates. Fresh Chromium/mobile74 and WebKit71 checks passed; original Linux Firefox71 receipts remain historical evidence. The [independent report](audit_reports/independent-last-commit-2026-10-03/report.md) and [implementation status](IMPLEMENTATION_STATUS.md) record validation limits and the separate deployment state.

The wider B5 product ideas—guided basic planning, tag scanning, deeper page decomposition and observed task simplification—are follow-up improvements, not implemented fixes. Fresh full mutation campaigns, supported-load/soak measurements, manual accessibility/Telugu review, real-photo/domain evaluation, provider receipts and full staging recovery remain necessary for the target assessment.
