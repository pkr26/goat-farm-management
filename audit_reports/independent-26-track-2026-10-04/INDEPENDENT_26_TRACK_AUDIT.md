# Independent 26-track project audit

**Project:** Herdly / goat-farm-management-main  
**Source revision:** `7245368fa91333fb39387df789fa4ef9a58dbea3`  
**Audit date:** 4 October 2026 (America/Phoenix)  
**Scope:** all 26 requested tracks; source review plus the explicitly recorded isolated runtime checks.  
**Status:** audit findings documented; application fixes were not requested or implemented.

## Result

**29 distinct confirmed issues:** 0 Critical, 1 High, 17 Medium and 11 Low. IDs are stable; an issue mapped to multiple tracks is counted only once. Severity is based on the documented trigger, prerequisites and demonstrated impact, not merely on a tool label.

The highest-priority application failure is the production screening worker's use of API-only settings while recording provider telemetry. Its full-cycle reproduction loses valid provider results and exhausts the image's attempt budget. Other confirmed issues affect forecast calibration/accounting, reviewable screening evidence, offline Telugu availability, deployment/release safety and the integrity of audit evidence. The index and complete details below include every confirmed issue.

Passing existing tests is not treated as proof that the application has no defects. Conversely, a validation gap is not promoted to a vulnerability. The report does not claim exhaustive discovery of every possible bug, production certification, veterinary accuracy, regulatory compliance or a complete external penetration test.

## Independence, scope and evidence rules

Three fresh reviewers worked independently on domain/database, frontend, and operations/testing scopes. They were instructed not to consult earlier audit conclusions. The coordinating reviewer covered authentication, authorization, privacy and contracts and ran shared validation. The earlier planning conversation had identified historical reports; those historical results were not imported as present-day findings or test passes. Cross-review happened after primary findings were written. New claims required current source locations and a concrete failure mode; all listed findings have fresh execution evidence at the level specified in their details.

The starting worktree was clean at the revision above. The manifest records 1902 tracked paths and hashes 1453 current tracked files outside historical audit/archive bundles. Review was a repository-wide inventory with focused source inspection across every subsystem, not a claim that every line of all generated clients and historical reports was manually reviewed. No application or existing test source was changed. New probes and reports live only in this new audit directory.

Database probes used named disposable databases with explicit application and migration targets. Browser checks used owned isolated servers and a disposable database, with automatic reuse of unrelated running servers disabled. Provider, SMS, object-store and registry-delete reproductions used local doubles unless an individual receipt explicitly says otherwise. No real messages were sent, no production data was changed, and no release was published. Any synthetic build-input changes were confined to disposable audit contexts.

Eight complementary methods were used: manual review; static/security scanning; real-DB and browser tests; adversarial/boundary probes; review and adversarial testing of mutation evidence; synthetic capacity/concurrency controls; controlled failure injection; and specialist/device validation-gap assessment. A new full-project mutation campaign and real farm/veterinary field trial were not run.

## All 26 audit tracks

| # | Track | Work performed | Findings / boundary |
| --- | --- | --- | --- |
| 1 | Architecture and maintainability | API/service/model boundaries, large frontend modules, shared state and worker configuration dependencies. | O26-01; see frontend architecture review |
| 2 | Functional user journeys | Owner/worker/browser journeys, record entry, screening capture/review and error states. | D26-01; F26-02,04,05,08,09 |
| 3 | Goat-farm business rules | Animal chronology, reproductive and bucket transitions, mortality attribution, health and task rules. | D26-01,06 |
| 4 | Financial and inventory accuracy | Ledger/inventory writes, insurance and source attribution; first-day feeding; disposal accounting. | D26-02,04,07 |
| 5 | Simulation and forecasting | Calibration, cohort/financial transformations, seasonal prices and independently constructed numerical controls. | D26-02,06,07,08,09 |
| 6 | AI photo-screening quality | Image/provider contracts, gate/specialist evidence preservation, reviewability and full synthetic worker cycles. | D26-03,05; F26-04; O26-01 |
| 7 | Authentication and sessions | Password/PIN/TOTP, refresh and revocation, exact-session ownership and credential reset. | S26-02; no new authentication bypass reproduced |
| 8 | Permissions and farm isolation | Recursive route/dependency inventory, membership/role locks, owner-only routes and real-DB regression matrix. | No new tenant-isolation defect confirmed |
| 9 | Application security | Credential/origin handling, input/body limits, browser security headers, dangerous execution/HTML source searches. | S26-01 |
| 10 | Privacy and data lifecycle | Account exports/tombstones, photo/credential retention, notification-phone scrubbing and traceability. | S26-02; deployment/legal retention decisions not certified |
| 11 | Database integrity | Constraints/tenant relationships, direct SQL backstops, catalog state and persisted chronology. | D26-01; cross-tenant FK control passed |
| 12 | Database migrations | Revision inventory, explicit-target preflights, complete empty-schema upgrade/downgrade/upgrade plus drift checks and existing migration regressions. | Full empty-schema roundtrip passed; no new migration defect confirmed; populated historical upgrades not exhaustively rehearsed |
| 13 | Concurrency and duplicate prevention | Authorization/domain lock order, idempotency, outboxes and independent concurrent-sale probe. | No new race confirmed in tested paths; sale control produced 200/409 and one transaction |
| 14 | API contracts and integration | Fresh OpenAPI equality and 126 runtime-route dependency records, generated transport and frontend permissions. | S26-01; F26-08; contract equality passed |
| 15 | Frontend state correctness | Farm changes, async ownership, active uploads, localization render order, unavailable-data states. | F26-04,05,06,09 |
| 16 | Offline and PWA reliability | Actual service-worker execution, IndexedDB/outbox rules, cached shells, selected locale and conditional cold reload. | F26-01 |
| 17 | Usability and device compatibility | Responsive journeys, shared-tablet onboarding, mobile/tablet browser projects, eight retained screenshot inspections and recovery controls. | F26-01,02,04,05,08,09; browser limits below |
| 18 | Accessibility | Configured route-level axe tests plus independent offline-state landmark scan. | F26-07; no complete manual assistive-technology certification |
| 19 | Localization and time handling | English/Telugu loading and persistence, formatting, UTC-naive instants and farm-local date boundaries. | F26-02,03,06; D26-04 |
| 20 | Performance and capacity | Bounded SQL/query surfaces, simulation/password admission, cancellation leases, capacity controls and cold-route JavaScript budgets. | Synthetic capacity controls passed; no target production scale supplied |
| 21 | Background jobs and external services | Worker cycles, notification/outbox settlement, retries, budget reservations and maintenance. | O26-01,02; providers/storage doubled, not paid/live |
| 22 | Deployment and configuration | Docker/Compose settings separation, secrets/proxy/TLS checks, real build attempts and static validation. | O26-01,07,08,09 |
| 23 | Backup and disaster recovery | Archive/manifest binding, metadata authentication, freshness and restore guards. | O26-04; whole-system restore/RPO/RTO not proven |
| 24 | Monitoring and operational readiness | Private metrics, cross-process visibility, heartbeat, security ledger and freshness alerts. | O26-01,02,04; S26-02 |
| 25 | Dependencies, CI and release integrity | Fresh dependency/container checks, patch/build inputs, release preflight/cleanup, signatures and evidence gates. | O26-03,05,06,07,08,09 |
| 26 | Test quality and audit evidence | Fresh suite receipts, unchanged gates, new independent probes, mutation/SARIF validator adversarial inputs. | O26-05,06; this report retains failures and incomplete evidence |

## Fresh validation

[Coordinating commands and isolated browser settings](evidence/root/baseline-commands.md) · [Host/runtime versions](evidence/root/validation-environment.json).

| Check | Actual outcome | Evidence |
| --- | --- | --- |
| Backend complete suite and branch coverage | PASS: 5,307 passed, 4 skipped, 0 failed, 0 errors; combined line/branch coverage 92.31% against 92% floor (lines 94.18%, branches 85.45%); 2983.27 seconds; exit 0 | [Final JUnit/coverage summary](evidence/root/backend-validation.json) |
| Frontend complete configured coverage gate | PASS: 326 files, 5,248 tests; statements 94.47%, branches 91.85%, functions 94.46%, lines 96.72%; all global and 10 scoped floors passed unchanged | [Coverage log](evidence/frontend/coverage-gate.log) |
| Frontend ordinary baseline | PASS: 326 files, 5,248 tests; overlaps the later coverage run and is not added to unique test totals | [Baseline log](evidence/frontend/vitest-baseline.log) |
| Frontend lint, typecheck and standalone production build | PASS for all three local commands; this does not establish a successful Docker image build | [Build receipt](evidence/frontend-build.log) |
| Chromium desktop/mobile/tablet configured browser projects | PASS: 105 tests, zero retries, 236.88 seconds | [Machine-readable result](evidence/browser-chromium-results.json) |
| WebKit full browser baseline | FAILED: 101 passed, 1 login navigation timeout before target-page/axe evaluation, zero retries; 326.86 seconds | [Full baseline result](evidence/browser-webkit-results.json) |
| WebKit exact-case focused diagnostic | PASS: 1 test in a separate invocation, zero retries, 5.72 seconds; original full baseline remains failed | [Timeout analysis and focused result](evidence/webkit-timeout-review.json) |
| Firefox configured browser baseline | INCOMPLETE: 2 browser-launch timeouts, 1 interrupted test, 97 not run; stopped after independent app-free launch failure reproduced the host/browser problem | [Baseline log](evidence/browser-firefox.log) |
| Firefox application-independent control | FAILED to launch within 15 seconds before any page: sandbox extension/graphics errors; probe-owned descendants cleaned | [Control receipt](evidence/root/firefox-launch-control.md) |
| Independent domain/database probes | 11 distinct test cases passed: 9 issues reproduced plus tenant-integrity and concurrent-sale positive controls; final 10-test batch plus isolated confidence case | [Final batch](evidence/domain/probes-final.log) |
| Independent financial numerical controls | PASS: 66 known-polynomial annual/monthly IRR roots, 4 solver-domain checks and 5 loan/NPV controls; 75 controls total | [Numerical oracle output](evidence/domain/numerical-controls-output.json) |
| Independent security/API probes | 3 passed, reproducing the 2 documented issues with real PostgreSQL; no authentication bypass shown | [Final probe log](evidence/root/security-probes.log) |
| Independent frontend component probes | 7 files / 9 test cases passed; additional actual service-worker and production-Chromium cache/locale/accessibility reproductions retained | [Component probe log](evidence/frontend/targeted-probes.log) |
| Production-shaped screening worker cycles | 2 passed reproduction tests: provider success and provider failure both reach terminal ERROR after 5 attempts; not evidence of healthy operation | [Real-DB pipeline receipt](evidence/ops/worker-cycle-pytest.txt) |
| OpenAPI and dependency inventory | PASS: fresh contract equals committed artifact; 108 documented paths, 125 documented operations, 126 runtime route records including hidden metrics | [Contract and route inventory](evidence/root/api-contract-and-dependencies.json) |
| Backend lint/type/static workflow checks | PASS: Ruff check/format; exact separate CI mypy runs (154 application/tool, 12 mutation, 153 test files); actionlint; 5 shell syntax checks | [Operations review and receipts](ops.md) |
| Backend dependency audit | PASS: 59 dependencies, no known vulnerabilities in the fresh package audit; excludes OS packages | [Package audit](evidence/ops/backend-dependency-audit.json) |
| Frontend dependency and local patch audit | PASS under repository policy: 0 unmitigated, 1 locally mitigated braces advisory; 19 depth-guard and 4 ordinary-pattern controls passed | [Audit and patch verification](evidence/ops/frontend-dependency-verified.txt) |
| Actual current container builds | Backend and edge PASS. Frontend FAILS at Corepack; semver-only temporary correction exposes a separate missing-patch install failure, with a patch-copy positive control | [Build-input evidence](evidence/ops/frontend-container-input-results.json) |
| Fresh ARM64 container vulnerability policy gates | FAIL: current backend 20 HIGH/CRITICAL package records and pinned Postgres 8 HIGH records after repository exclusions; current edge PASS. Scanner severity is not a verified application exploit | [Raw/policy summary](evidence/ops/container-scan-summary.txt) |
| Operational adversarial/capacity controls | Reproduced process-local metrics omission, release-cleanup ownership error and mutation/SARIF verifier errors; admission/cancellation controls passed | [Operations findings and receipts](ops.md) |
| Signed backup fixture and unsigned sidecar tamper | Reproduced freshness failure with a genuine GPG-signed fixture: identical valid signed payload, timestamp-only sidecar change flips stale exit 2 to fresh exit 0; no database restore drill | [Signed-fixture receipt](evidence/ops/signed-backup-freshness-result.json) |
| Frontend cold-route JavaScript budgets | PASS: login 446,517 gzip bytes / 465,920-byte budget; simulation 529,323 / 547,840; lazy Telugu catalog excluded from these initial chunks | [Budget receipt](evidence/frontend/route-js-budget.log) |
| Frontend mutation-harness contract suite | PASS: 22 tests, 0 failures, 17.31 seconds. Includes deliberate failing-smoke negative controls; this is not a full application mutation campaign | [Contract-suite receipt](evidence/frontend/mutation-harness-contracts.log) |
| Complete empty-schema migration roundtrip and metadata drift | PASS: upgrade head, check, downgrade base, upgrade head, check; all 5 commands exit 0 (11.04 seconds). Head fe5f6a7b8c9d and 52 tables after upgrades; own database dropped | [Roundtrip receipt](evidence/ops/migration-roundtrip.json) |

All defect probes deliberately assert the currently observed failure, so their passing status confirms reproduction and does not mean the product is fixed. Existing baseline/coverage runs and repeated diagnostic controls overlap; totals are not summed as unique tests. Initial harness/configuration errors are retained with explicit names and are excluded from product findings. The WebKit failure remains an unresolved intermittent login/browser timeout despite the separate focused pass. Firefox is unvalidated on this host because browser launch fails without the application. Container policy failures and build failures remain failures despite passing package-only and local standalone-build checks. The backend's four skips are the existing manual finance category cases for ANIMAL_SALE/ANIMAL_PURCHASE, which are system-generated-only categories; the exact test names/reasons are retained in the backend summary.

## Findings index

| ID | Severity | Issue |
| --- | --- | --- |
| [O26-01](#o26-01) | High | Production screening consumes provider calls but terminally fails provider-processed images |
| [D26-01](#d26-01) | Medium | A sale can record an animal as purchased before it was born |
| [D26-02](#d26-02) | Medium | Ledger truncation systematically underestimates recurring costs |
| [D26-03](#d26-03) | Medium | A valid flagged cascade can end with no reviewable finding |
| [D26-04](#d26-04) | Medium | New western-timezone farms cannot record feeding on their first local day |
| [D26-05](#d26-05) | Medium | A malformed specialist response can silently erase one observed region |
| [D26-06](#d26-06) | Medium | Deaths after day-60 weaning vanish from the model's first-three-month mortality |
| [D26-07](#d26-07) | Medium | Disposed breeding stock retains book value and creates tax on a sale at cost |
| [D26-08](#d26-08) | Medium | Seasonal normalization changes the observed price level without adjusting the base |
| [F26-01](#f26-01) | Medium | Same-build shell refreshes evict live lazy assets; an offline Telugu reload can become a blank screen |
| [F26-02](#f26-02) | Medium | First-time worker setup defaults to English instead of Telugu |
| [F26-04](#f26-04) | Medium | Finishing a walkthrough can abort an actively uploading photo while reporting success |
| [O26-02](#o26-02) | Medium | Screening counters are collected in a process that has no scrape endpoint |
| [O26-03](#o26-03) | Medium | Re-running an existing release deletes package versions owned by the earlier successful run |
| [O26-04](#o26-04) | Medium | Unsigned recovery metadata can make an unchanged stale backup appear fresh |
| [O26-07](#o26-07) | Medium | Current frontend Docker build fails on malformed Corepack package-manager declaration |
| [O26-08](#o26-08) | Medium | Frontend dependency layer omits the required local vulnerability patch |
| [O26-09](#o26-09) | Medium | Pinned backend and PostgreSQL images fail the current vulnerability policy |
| [D26-09](#d26-09) | Low | Income rows falsely increase confidence in expense estimates |
| [F26-03](#f26-03) | Low | UTC-naive health snapshot timestamps display a browser-dependent farm date |
| [F26-05](#f26-05) | Low | Switching farms preserves tenant-specific record IDs in root-route query state |
| [F26-06](#f26-06) | Low | Language-sensitive formatting lags a locale switch until another render |
| [F26-07](#f26-07) | Low | Offline worker page nests duplicate main landmarks |
| [F26-08](#f26-08) | Low | Health viewers are offered a screening workflow they cannot submit |
| [F26-09](#f26-09) | Low | Screening statistics outages are presented as an empty history |
| [O26-05](#o26-05) | Low | Mutation gate trusts summary scores that contradict the raw attempts |
| [O26-06](#o26-06) | Low | SARIF gate reports a passed scan for zero runs or an explicitly failed invocation |
| [S26-01](#s26-01) | Low | Oversized numeric farm header produces an internal server error |
| [S26-02](#s26-02) | Low | Successful logout revocations have no durable attributed security event |

## Evidence limits and outstanding external validation

- **Clinical/model validity:** synthetic provider responses and mathematical controls establish software behavior, not disease sensitivity/specificity or forecast agreement with an actual farm. Representative veterinarian-labeled photos, independent farm outcomes and policy assumptions still need specialist validation.
- **Real services:** no paid AI/SMS calls, real object-store deletion, production credentials, hosted release execution or production deployment were performed. Release cleanup was reproduced against a fake registry command, not GitHub.
- **Capacity:** no operating-scale targets were supplied in response to the optional audit question. Admission/concurrency controls and query structure were checked, but production p95 latency, soak stability and capacity at a stated herd/farm/user population remain unmeasured. Concurrent audit workloads also make incidental local timings unsuitable as production SLOs.
- **Recovery and operations:** no coordinated database + versioned objects + key-escrow disaster-recovery drill or live alert-receiver acceptance test was completed. Local cryptographic/freshness probes are explicitly narrower than a whole-system restore.
- **Devices and language:** browser emulation and controlled cache removal do not replace physical tablet sleep/restart/storage-pressure testing, camera testing, manual screen-reader testing or fluent Telugu review.
- **Coverage breadth:** fresh upgrades and selected migration/constraint controls do not cover every historical populated upgrade/downgrade. Existing suite and sampled adversarial tests do not constitute a full new mutation campaign or proof of all possible interleavings.
- **Browser reliability:** failed/interrupted browser runs remain failed/incomplete in the validation table. A later focused pass does not convert a failed full run into a clean full-browser verdict.

## Recommended repair order

1. Correct production screening configuration/telemetry and any reproduced clean-build blockers; verify the actual deployed worker/build environment, not just API-process unit tests.
2. Correct financial/calibration transformations and preserve all flagged screening concerns through parsing and human review.
3. Repair worker offline asset retention, first-use locale selection and upload/finish serialization; verify restart/offline behavior after browser HTTP-cache loss.
4. Authenticate recovery manifests and constrain release cleanup to objects created by the current attempt; make worker monitoring observable across processes.
5. Close smaller chronology, navigation, localization, accessibility and audit-ledger gaps; harden evidence gates and reproduce each original failure as a regression.

## Complete finding details and primary review records

The following records are included in full so this single Markdown file contains every issue, reproduction, impact, recommendation and stated limitation. File/line references are relative to the repository root. Raw logs and executable probes remain in the linked evidence directory. Prior successful probe iterations are not added to final unique test counts.

---

## Independent security, privacy, and API audit

Source: `7245368fa91333fb39387df789fa4ef9a58dbea3`. Date: 4 October 2026, America/Phoenix.

This review derived findings from current implementation and fresh probes. Earlier audit conclusions were not used as findings. No application source was changed.

### Coverage

| Track | Review and verification | Outcome / boundary |
| --- | --- | --- |
| 1 Architecture (backend portion) | API/service/schema/model separation; central authentication and permissions dependencies; background and worker configuration boundaries | Configuration coupling is covered by O26-01 in the operations report. This is not a full refactoring proposal. |
| 7 Authentication and sessions | Password/PIN/TOTP flows, refresh-family lifecycle, exact-session cancellation, credential-change revalidation, worker provisioning provenance, rate limits, key handling | S26-02 is a traceability gap. No new authentication bypass was reproduced. Full regression results are in the consolidated report. |
| 8 Permissions and farm isolation | Recursively inventoried all included routers and dependency trees; reviewed farm/membership/user/role locking; ownership transfer; owner-only credential reset | 126 runtime route records, including hidden metrics; only `/api/auth/permissions` has farm context without a `require_perm` closure, intentionally returning the caller's permissions. No cross-farm data disclosure was reproduced. |
| 9 Application security | Strict bearer/cookie handling, CSRF origin checks, body and target limits, input boundaries, CSP and security headers; source search for dangerous execution/HTML sinks | S26-01 reproduced. This is not an external penetration test of a deployed environment. |
| 10 Privacy and lifecycle | Account exports/tombstones, delayed membership/PIN/TOTP/notification-phone cleanup, immutable security-event records and projection | S26-02 reproduced. No legal-compliance certification or deployment-specific retention decision is claimed. |
| 14 API contracts | Compared freshly generated in-memory OpenAPI with committed contract; recursively inventoried routes and dependencies | Exact contract equality: 108 documented paths, 125 documented operations; hidden `/metrics` makes 126 runtime route records. No generated artifact was rewritten. |

<a id="s26-01"></a>

### S26-01 — Oversized numeric farm header produces an internal server error

- **Severity:** Low (P3).
- **Tracks:** 9, 14.
- **Verification:** Reproduced with the real ASGI application, a valid registered bearer session, and a freshly migrated disposable PostgreSQL database.
- **Confidence:** High.
- **Source:** `backend/app/deps.py:716–720`, particularly the unguarded `int(x_farm_id)` at line 718.
- **Prerequisite:** An authenticated caller can deliver a 5,000-digit ASCII `X-Farm-Id` header to the backend. Any deployed edge's independent header limit still applies.
- **Trigger:** `GET /api/animals` with a valid bearer and `X-Farm-Id` set to 5,000 copies of `9`.
- **Expected:** A bounded 4xx validation response, with no attempted lookup of an impossible farm ID.
- **Actual:** The regex accepts the header; Python's integer-string conversion limit raises `ValueError` before the explicit range guard. The application returns HTTP 500.
- **Impact:** An invalid tenant selector becomes a server error and exception-log event. No unauthorized access, persistent data corruption, or service-wide outage was established; this is why the finding is Low.
- **Correction:** Bound header length before integer conversion, preserving the intended handling of ordinary out-of-range IDs; handle conversion failure as a client error. Add an HTTP-level regression asserting a 4xx for oversized numeric input.
- **Evidence:** `evidence/root/test_security_probes.py::test_oversized_numeric_farm_header_returns_500`; `evidence/root/security-probes.log`; `evidence/root/security-probes.xml`.

<a id="s26-02"></a>

### S26-02 — Successful logout revocations have no durable attributed security event

- **Severity:** Low (P3).
- **Tracks:** 7, 10, 24.
- **Verification:** Reproduced independently for both logout routes with real sessions and PostgreSQL.
- **Confidence:** High for the omission; its operational priority depends on the required audit trail.
- **Source:** `backend/app/api/auth.py:1610–1637` and `backend/app/api/auth.py:1735–1854`; the helper `backend/app/deps.py:357` mutates session rows without emitting an event. `docs/architecture.md` describes durable successful security-state transitions.
- **Prerequisite:** A live user completes `POST /api/auth/logout-session` or `POST /api/auth/logout`.
- **Trigger:** Register a disposable user, count `SecurityEvent` rows, log out, attempt `/api/auth/me` with the previous bearer, and count the ledger again.
- **Expected:** A successful revocation has an attributed, transactional event, while duplicate/no-op or invalid logout attempts do not create attacker-amplifiable event rows.
- **Actual:** Both endpoints return 204 and the previous bearer receives 401, but the security-event row count increases by zero.
- **Impact:** Revocation itself works. Request logs show method/path/status, but do not supply the durable actor/family attribution used for other security lifecycle events; operators cannot reconstruct a logout from the immutable event ledger.
- **Correction:** Emit one event in the same transaction only when a real revocation/version change occurs, with bounded identifiers and no token material. Verify repeated no-op logout does not add events.
- **Evidence:** Both parameterizations of `evidence/root/test_security_probes.py::test_logout_does_not_record_durable_event`; `evidence/root/security-probes.log` and XML receipt.

### Commands and evidence

The final reproduction run used an explicitly owned `goatfarm_test_a26_rootprobe_7c82490a` database, with both application and migration URLs set to that database. From `backend/`:

```sh
GOATFARM_TEST_DB=goatfarm_test_a26_rootprobe_7c82490a \
GOATFARM_DATABASE_URL=postgresql+asyncpg://localhost:5432/goatfarm_test_a26_rootprobe_7c82490a \
GOATFARM_MIGRATION_DATABASE_URL=postgresql+asyncpg://localhost:5432/goatfarm_test_a26_rootprobe_7c82490a \
.venv/bin/python -m pytest -c pyproject.toml -p tests.conftest \
../audit_reports/independent-26-track-2026-10-04/evidence/root/test_security_probes.py -q -s
```

**Result: 3 passed.** These tests intentionally assert the observed defective behavior so they serve as reproduction receipts; they are not acceptance tests for a corrected product. The fixture removed its owned database. The initial invocation omitted the explicit pytest configuration, causing two asynchronous fixture setup errors; that harness mistake is preserved in `security-probes-harness-error.log` and is not an application finding. An earlier two-probe successful run is also retained; it is not added to the final unique count.

`evidence/root/api-contract-and-dependencies.json` contains the current contract equality result and the recursively flattened dependency inventory. An initial inventory looked only at top-level FastAPI routes; the final inventory corrects this for nested included routers and contains all 126 records. No two-route sample is presented as full route coverage.

The consolidated report contains the fresh complete-suite, browser, static, build, and dependency results and their limitations.


---

## Independent audit: domain, finance, simulation, screening, database and concurrency

Audited commit: `7245368fa91333fb39387df789fa4ef9a58dbea3`. Date: 2026-10-04, America/Phoenix. Assigned tracks: 3, 4, 5, 6, 11, 12, 13. Application and existing test sources were not changed. Findings were derived from current source and independently constructed probes; old audit reports and archived conclusions were not used.

**Result: nine confirmed issues: eight P2 (medium), one P3 (low).** Every issue below has an executed reproducer. Two additional controls passed for database tenant integrity/catalog state and concurrent sale serialization. No new migration or concurrency defect was confirmed in the reviewed paths.

All new probes and their output are in [evidence/domain](evidence/domain). The probes deliberately assert the observed defective behavior, so a passing probe confirms reproduction, not remediation. No fixes have been implemented.

### Findings

<a id="d26-01"></a>

#### D26-01 — P2: A sale can record an animal as purchased before it was born

- **Tracks:** 3 goat domain; 11 database integrity.
- **Current source:** `backend/app/api/animals.py:1157` performs chronology checks before `backend/app/api/animals.py:1220` writes the newly supplied estimated DOB. `backend/app/models/animals.py:100` onward has source/status consistency checks but no purchase-versus-effective-DOB constraint.
- **Prerequisites:** An active male with no DOB, with an earlier recorded purchase date, outside quarantine; a user authorized to record its sale. The existing-herd import API can create this legitimate unknown-age starting record.
- **Reproduction:** Create an unknown-age male bought 730 days ago. Sell it today while supplying `estimated_dob` 365 days ago. `test_sale_dob_can_follow_purchase` uses only the public animal APIs.
- **Expected:** Reject the conflicting new date before committing; the estimate must be no later than acquisition and previously recorded facts.
- **Actual:** HTTP 200 persisted `purchase_date=2024-10-05`, `estimated_dob=2025-10-05`, `status=SOLD`, and a ₹1,000 sale. The goat is recorded as acquired a year before birth. See `DOB_CHRONOLOGY`, [final log](evidence/domain/probes-final.log), line 9.
- **Impact:** Permanent contradictory provenance reaches animal histories and age-based calibration. The normal animal-edit API cannot repair DOB after the terminal transition.
- **Recommended fix:** Validate the prospective estimated DOB against purchase and prior event dates before assignment. Add a database backstop for the same-row birth/acquisition invariant after auditing legacy data.
- **Confidence / verification:** High; API reproduction and persisted response confirmed. Open, not fixed.

<a id="d26-02"></a>

#### D26-02 — P2: Ledger truncation systematically underestimates recurring costs

- **Tracks:** 5 simulation/calibration; 4 finance.
- **Current source:** `backend/app/services/simulation_calibration.py:1003` limits all ledger rows, `:1027` truncates to 20,000, and `:1042` sums expenses only from that slice. The denominator comes from the full-window first expense at `:1015`; miscellaneous cost is assigned at `:1179`. Labour and veterinary cost use the same truncated totals.
- **Prerequisites:** More than 20,000 active ledger rows in the requested lookback. Recent income rows also consume the quota.
- **Reproduction:** Insert an older ₹60,000 OTHER expense 180 days ago, 19,999 newer OTHER income rows, and a newer ₹1,000 OTHER expense. The probe uses the actual production cap; it does not monkeypatch the limit.
- **Expected:** The six-month expense average is ₹61,000 / 6 = ₹10,166.67 per month. SQL aggregation can calculate this without loading an unbounded row set.
- **Actual:** Calibration returns ₹166.67 per month, counting only the latest ₹1,000. It is 98.36% below the complete recorded average. A truncation warning is present, but the biased value is still returned as a calibrated assumption. See `COST_TRUNCATION`, [final log](evidence/domain/probes-final.log), line 16. That log also shows the independent confidence-count defect D26-09.
- **Impact:** Large or income-heavy ledgers receive understated operating costs, changing forecast cash, viability, optimization and risk results.
- **Recommended fix:** Aggregate complete-window sums/counts by expense category and structured feed purchase class in SQL; reserve row caps for detail samples. If complete totals cannot be obtained, avoid presenting a truncated numerator as a full-history monthly cost.
- **Confidence / verification:** High; real 20,001-row PostgreSQL fixture and public calibration endpoint confirmed. Open, not fixed.

<a id="d26-03"></a>

#### D26-03 — P2: A valid flagged cascade can end with no reviewable finding

- **Track:** 6 AI photo screening.
- **Current source:** `backend/app/services/screening/gate.py:82` permits an empty observation list; `backend/app/services/screening/pipeline.py:1149` falls back to a general specialist, while `:1257` falls back to the same empty observations if the specialist returns no conditions. The cascade returns FLAGGED at `:1327`.
- **Prerequisites:** The gate returns `flagged=true`, `quality_problem=false`, `confidence=0.9`, `observations=[]`, followed by `conditions=[]` from the general specialist. Both responses satisfy the current parsers.
- **Reproduction:** Run the real worker cycle with in-memory storage, a generated JPEG and a synthetic provider emitting these responses (`test_flagged_without_observations_has_no_reviewable_finding`).
- **Expected:** Preserve a generic reviewable gate concern, or classify the incomplete result for retry/manual assessment. A flagged photo should have a usable review action.
- **Actual:** The image is terminal `FLAGGED`, the cycle increments its flagged count, and the database has zero `ScreeningFinding` rows. Review endpoints operate on findings, so this flag cannot be confirmed or rejected. See `EMPTY_FLAG`, [final log](evidence/domain/probes-final.log), line 21.
- **Impact:** The alert and review records disagree; the screening queue contains a flag with no auditable review path.
- **Recommended fix:** Enforce a flagged-result reviewability invariant, with a neutral generic finding when the gate supplies no structured observation, or retain an explicitly unresolved/retryable status.
- **Confidence / verification:** High for software behavior. Synthetic contract validation only; no claim about actual provider frequency or clinical accuracy. Open, not fixed.

<a id="d26-04"></a>

#### D26-04 — P2: New western-timezone farms cannot record feeding on their first local day

- **Track:** 4 finance/inventory/feeding.
- **Current source:** `backend/app/api/feeding.py:153` compares a farm-local date with `farm.created_at.date()` in UTC. The source comment at `:155` acknowledges the offset, but the preceding future-date check also prevents the suggested next-date workaround.
- **Prerequisites:** A farm created after UTC midnight but before local midnight in a western timezone. The probe uses America/Phoenix, a supported timezone, with available dry-feed stock.
- **Reproduction:** Farm creation is `2026-10-04T00:30:00` UTC, corresponding to October 3 in Phoenix. With the deterministic farm-local clock at October 3, submit a normal October 3 dispense.
- **Expected:** Accept feeding on the farm's creation date in its business timezone.
- **Actual:** HTTP 422: `Dispensing date cannot be before the farm was created.` See `DISPENSE_TIMEZONE`, [final log](evidence/domain/probes-final.log), line 28. The clock is deliberately controlled in this test; this is not a claim that the test farm was created at the current wall-clock instant.
- **Impact:** Valid first-day feeding cannot be booked and inventory cannot be debited through its operational workflow until the calendar catches up. Later backfill of that first local day remains rejected.
- **Recommended fix:** Convert the UTC timestamp using the existing `business_date(farm.created_at, farm.timezone)` helper before comparing dates.
- **Confidence / verification:** High; public API reproduction with controlled dates and real persisted farm/stock. Open, not fixed.

<a id="d26-05"></a>

#### D26-05 — P2: A malformed specialist response can silently erase one observed region

- **Track:** 6 AI photo screening.
- **Current source:** `backend/app/services/screening/specialists.py:176` silently drops malformed conditions and returns a successful empty response. `backend/app/services/screening/pipeline.py:1182` preserves gate observations only on a raised provider error. At `:1226`, any successful condition from another specialist suppresses the global gate-observation fallback.
- **Prerequisites:** The gate identifies more than one region. One specialist returns a valid finding; another returns malformed condition data. This is distinct from D26-03: the gate has explicit observations, but invalid refinement destroys part of their review coverage.
- **Reproduction:** Gate reports mouth lesion and severe eye injury. Skin specialist returns valid ORF; eye specialist returns EYE_TRAUMA with `confidence="high"`, which violates the numeric contract.
- **Expected:** Treat the malformed eye response as an error or preserve the eye gate observation for human review.
- **Actual:** The parser drops the eye condition without signaling failure. Only `(mouth, ORF)` reaches `ScreeningFinding`; the eye observation disappears from the reviewable findings while the image becomes terminal FLAGGED. See `LOST_REGION`, [final log](evidence/domain/probes-final.log), line 33.
- **Impact:** Reviewers can miss a recorded concern because an invalid specialist answer is treated like a successful negative refinement. The raw gate evidence remains in run detail, but it no longer participates in the normal finding review workflow.
- **Recommended fix:** Distinguish a valid empty negative assessment from an invalid/nonempty response whose conditions were all rejected. Surface the latter through the existing provider-error fallback, preserving per-region gate findings.
- **Confidence / verification:** High for software evidence propagation. Generated image and synthetic responses only; no clinical sensitivity/specificity measurement. Open, not fixed.

<a id="d26-06"></a>

#### D26-06 — P2: Deaths after day-60 weaning vanish from the model's first-three-month mortality

- **Tracks:** 5 simulation/calibration; 3 goat domain integration.
- **Current source:** `backend/app/services/kidding.py:465` deliberately leaves the ALIVE birth outcome unchanged after operational weaning. `backend/app/services/simulation_calibration.py:698` counts only DIED birth entries for the model's full three-month young-kid phase. Its animal-based next phase starts at three months at `:734`. `backend/app/simulation/engine.py:35` documents the model's three-month class versus operational day-60 weaning.
- **Prerequisites:** Kids weaned before three months, followed by a recorded death before their three-month birthday; at least ten kid records trigger calibration.
- **Reproduction:** Seed ten live births, with legitimate day-60 RECOVERY-to-FEMALE_KIDS moves. Record one child's day-70 death through the public status API. Calibrate after all births are six months old. Historical reproductive fixtures use valid constrained ORM rows; the death and calibration are actual API operations.
- **Expected:** Preserve the correct immutable live-birth outcome, but derive the model's three-month phase mortality from the linked animals' actual death dates. The observed first-three-month loss is 1 / 10 = 10%.
- **Actual:** The death is accepted; the birth entry correctly remains ALIVE; calibration replaces the preset 15% rate with **0%**, marked medium confidence with sample size 10. The death also lies before the next animal-exposure class. See `MISSED_KID_DEATH`, [final log](evidence/domain/probes-final.log), line 42.
- **Impact:** Normal weaning followed by mortality makes the farm look safer than its records show, increasing modeled survival and future sale stock.
- **Recommended fix:** Reconcile mortality by actual age/death facts across the linked Animal records and birth entries, avoiding double counting. Align the calibration window with the model class; do not mutate historical birth outcomes to compensate.
- **Confidence / verification:** High; PostgreSQL history fixture plus API death and calibration confirmed. This is an integration defect, not a claim that the documented monthly-resolution approximation itself is invalid. Open, not fixed.

<a id="d26-07"></a>

#### D26-07 — P2: Disposed breeding stock retains book value and creates tax on a sale at cost

- **Tracks:** 5 simulation financial accounting; 4 finance.
- **Current source:** `backend/app/simulation/engine.py:1677` depreciates every purchase vintage until useful life/horizon without reducing it for sales, culls or deaths. `:1751` calculates taxable profit using those incomplete depreciation charges. At `:1699` and `:1721`, stale residuals are presented as offsetting terminal livestock/breeding-stock values.
- **Prerequisites:** A purchased breeding animal exits before the end of its configured useful life. A positive configured income-tax rate affects cash/NPV; accounting and terminal-component problems remain at zero tax.
- **Reproduction:** Empty opening herd; month 1 purchase one doe for ₹100,000 and sell it in that same month for ₹100,000. Twelve-month horizon, sixty-month useful life, 30% configured tax, no facilities, financing, overhead, family-labour cash or selling costs. The submitted model validates.
- **Expected:** Zero gain on a same-month sale at cost; no continuing asset book value for the disposed animal. Any full-month depreciation convention must be balanced by derecognizing the remaining carrying amount at disposal.
- **Actual:** Closing herd is zero, but total depreciation is only ₹20,000, modeled tax is **₹24,000**, and terminal components include approximately +₹72,000 breeding stock and −₹72,000 livestock. The terminal components cancel in total cash, so this is not a double-counted terminal payout; the tax and accounting are still wrong. See `DISPOSAL_TAX`, [final log](evidence/domain/probes-final.log), line 43.
- **Impact:** Overstated profits/tax and distorted DSCR/NPV for early sales, culls and deaths; misleading residual asset components even with an empty herd.
- **Recommended fix:** Track surviving carrying amounts by stock vintage, derecognize allocated book value at disposition/death, stop its subsequent depreciation, and separately recognize disposal gains/losses.
- **Confidence / verification:** High; pure deterministic engine with schema-validated assumptions and a zero-economic-gain control. The expected result is internal accounting consistency, not jurisdiction-specific tax advice. Open, not fixed.

<a id="d26-08"></a>

#### D26-08 — P2: Seasonal normalization changes the observed price level without adjusting the base

- **Track:** 5 simulation/calibration.
- **Current source:** `backend/app/services/simulation_calibration.py:933` sets the base price to the overall median. `:951` builds month/base ratios and `:957` normalizes their mean to one, leaving that base unchanged. `backend/app/simulation/market.py:45` multiplies the normalized ratio by the unchanged base.
- **Prerequisites:** At least twelve priced, weighed sales covering four months; the mean of the raw month ratios differs from one.
- **Reproduction:** Three sales each in January, February, March and April 2026, at ₹100, ₹100, ₹100 and ₹400/kg respectively. These are non-festival months in the embedded calendar. All values are within the accepted ratio clamp.
- **Expected:** Normalizing a seasonal curve should retain the fitted price levels, with any normalization scale transferred to the base price. For this fixture, the imputed twelve-month mean is ₹125 and ratios `[0.8, 0.8, 0.8, 3.2, ...]` then reproduce the observations.
- **Actual:** Base remains ₹100. Ratios become `[0.8, 0.8, 0.8, 3.2, ...]`, so the model implies **₹80, ₹80, ₹80, ₹320/kg**, uniformly 20% below the observed monthly levels before growth. See `SEASONAL_LEVEL_DRIFT`, [final log](evidence/domain/probes-final.log), line 50.
- **Impact:** Calibration changes revenue levels as an artifact of expressing seasonality; the error can point either direction depending on the sample's monthly distribution.
- **Recommended fix:** Compute base and normalized curve together; carry the removed mean into the base and update both evidence entries consistently. Preserve any intended clamp policy explicitly.
- **Confidence / verification:** High; public calibration endpoint plus direct reconstruction of its monthly prices. Open, not fixed.

<a id="d26-09"></a>

#### D26-09 — P3: Income rows falsely increase confidence in expense estimates

- **Track:** 5 simulation/calibration evidence.
- **Current source:** `backend/app/services/simulation_calibration.py:1040` correctly excludes income from expense totals, but sample counts at `:1125`, `:1143`, `:1166` and `:1182` filter only category. `_confidence` at `:63` promotes a count of 30 to high.
- **Prerequisites:** Income and expense rows share a permitted category, especially OTHER; only a small number of actual expense observations exist.
- **Reproduction:** One ₹1,000 OTHER expense and thirty ₹100 OTHER income rows, well below the history cap. Call calibration (`test_income_rows_inflate_expense_calibration_confidence`).
- **Expected:** Expense evidence sample size 1, low confidence under the current thresholds.
- **Actual:** `costs.misc_overhead_per_month` has sample size **31** and **high** confidence. See `INCOME_INFLATES_COST_CONFIDENCE`, [confidence log](evidence/domain/probe-confidence.log). D26-02 independently demonstrates high confidence from one included expense plus 19,999 incomes.
- **Impact:** The reliability metadata overstates the actual expense evidence and can suppress the appropriate low-confidence cue. The monetary estimate itself is unaffected in this isolated below-cap example.
- **Recommended fix:** Derive sample counts from the same expense-filtered set as the numerator, preferably the same aggregate query.
- **Confidence / verification:** High; standalone below-cap database/API reproduction. Open, not fixed.

### Coverage and evidence

| Track | Work performed | Result and boundary |
|---|---|---|
| 3 Goat domain | Reviewed creation/status chronology, sex/bucket transitions, service eligibility, kinship and chronology checks, kidding/neonatal updates, quarantine procurement and task cascades. Probed unknown-age sale and post-weaning mortality. | D26-01, D26-06. No veterinary efficacy validation; biology-policy constants were treated as application policy. |
| 4 Finance/inventory | Reviewed exact-money allocation, automatic source provenance, transaction corrections, insurance locking/premium history, per-animal/farm totals, feed gram allocation and ingredient/finished-stock locks. Probed date floor and simulation disposition accounting. | D26-04, with D26-02/D26-07 crossing the financial modeling boundary. No live finance data or external accounting system used. |
| 5 Simulation/forecasting/calibration | Reviewed assumption bounds, cohort initialization/events, mortality phase conversion, purchase capitalization/tax/terminal values, calendar price functions, Monte Carlo transformation and calibration evidence/aggregation. Probed real cap, phase deaths, zero-gain disposition, seasonal fit and confidence counts. Added 66 independent polynomial IRR controls, four explicit solver-domain controls and five loan/NPV conservation controls. | D26-02, D26-06, D26-07, D26-08, D26-09. No claim of full independent mathematical verification of every optimizer, planner or IRR branch; parent baseline suite covers existing tests. |
| 6 AI photo screening | Reviewed gate/specialist/detection parsers, cascade fallback and finding generation, crop safety/retry aggregation, provider rotation and budget admission paths, image normalization, synthetic live-contract probe design. Ran two real worker cycles with fake storage/providers. | D26-03, D26-05. Entirely synthetic software-contract evidence; no vet-labeled image dataset, paid provider calls or live S3 writes. Clinical accuracy remains unmeasured by this audit. |
| 11 Database integrity | Reviewed animal, reproductive, ledger and screening model constraints; inspected key tenant and trigger migrations; fresh PostgreSQL catalog and direct cross-tenant ledger-FK rejection. | D26-01 crosses this track. Head `fe5f6a7b8c9d`, no invalid public indexes, no unvalidated public constraints, cross-tenant link rejected. Passing controls do not prove every cross-table invariant. |
| 12 Migrations | Inventoried the revision chain; read migration environment target/quiescence/locking preflights and selected numeric, tenant, screening and reproductive trigger revisions; each fixture session replayed fresh database to head. | Fresh upgrades succeeded. No new migration bug confirmed. Did not rehearse every historical populated upgrade/downgrade or replay against real production data. |
| 13 Concurrency | Reviewed canonical animal→breeding→task ordering, batch retirement cleanup, insurance animal/policy locks, inventory serialization, direct-SQL kidding trigger order and screening claims. Independently raced two sale requests. | Responses 200/409; exactly one sale transaction. No new race confirmed. This targeted test is not a high-load/multi-worker soak test. |

Primary source files inspected (focused sections where large):

- `backend/app/api/{animals,breeding,kidding,feeding,finance,screening}.py`.
- `backend/app/services/{animals,breeding,kidding,health,chronology,purchases,feeding,finance,simulation_calibration}.py`.
- `backend/app/services/screening/{gate,specialists,detect,images,pipeline,live_contract}.py`.
- `backend/app/models/{animals,breeding,finance}.py`; `backend/app/schemas/{animals,feeding,finance}.py`.
- `backend/app/simulation/{assumptions,engine,finance,market,montecarlo,snapshot}.py`.
- `backend/alembic/env.py`; selected revisions `e7f9a1b3c5d8`, `f3a4b5c6d7e8`, plus constraint/revision inventory searches.
- Test helpers/contracts inspected: `backend/tests/conftest.py`, `test_screening.py`, `test_screening_clinical_integrity.py`, `test_simulation_api.py`, `test_simulation_audit_remediation.py`, `test_cull_price_calibration.py`, `test_domain_audit_fixes.py`; concurrency/schema/migration test inventories were also reviewed. Existing tests were not edited or rerun wholesale by this subaudit.

### Reproduction command and outcomes

Run from `backend/`:

```sh
GOATFARM_TEST_DB=goatfarm_test_a26_domain_104 \
GOATFARM_DATABASE_URL=postgresql+asyncpg://localhost:5432/goatfarm_test_a26_domain_104 \
GOATFARM_MIGRATION_DATABASE_URL=postgresql+asyncpg://localhost:5432/goatfarm_test_a26_domain_104 \
.venv/bin/python -m pytest -c pyproject.toml -p tests.conftest \
../audit_reports/independent-26-track-2026-10-04/evidence/domain/test_domain_probes.py -q -s
```

The repository fixture explicitly creates, migrates, seeds and drops only this named disposable database. Both database URLs were set before app imports. No existing `goatfarm` database was used.

Executed outcomes:

- `probes-final.log`: **10 passed in 12.35s**, containing eight issue reproductions and two positive controls.
- `probe-confidence.log`: the subsequently added independent confidence probe, **1 passed**. Thus all eleven currently saved probe functions have an executed passing observation; the first ten were not unnecessarily rerun after adding the isolated eleventh.
- `probe-disposal.log`: isolated disposal reproduction also passed before inclusion in the final ten-test run.
- `probes-initial.log`: harness configuration error from invoking external test paths without `-c pyproject.toml`; corrected invocation is recorded in `probes-initial-fixed.log` (4 passed). This was an audit harness error, not an application finding.
- `probes-complete.log`: six passed plus an audit-probe typo referencing a nonexistent commission field; corrected to the actual `selling_cost_fraction` before the successful final run. This was not an application finding.

The generated JPEGs are geometry-only fixtures. They do not establish disease detection accuracy, safe treatment selection, real-world model agreement, generalization across goats/cameras, or veterinarian-validated sensitivity/specificity. These remain validation limitations, separate from the confirmed parser/review defects.

### Supplemental independent numerical controls

After the primary probes, track 5 received a separate finite numerical check in [numerical-controls.py](evidence/domain/numerical-controls.py), with complete retained [output](evidence/domain/numerical-controls-output.json). This does not reuse the existing test suite's expected answers or a second floating-point root solver.

The oracle expands products of known linear factors with Python `Fraction`. Every tested cashflow coefficient is asserted to be exactly representable in binary64, avoiding the false expectation that a rounded polynomial preserves a multiple root. For period count `p`, a positive polynomial root `q` independently implies annual return `q**(-p) - 1`. The same cashflows are evaluated at annual and monthly rational times (`index / p`). Expected roots are filtered to the documented `[-0.99, 10]` annual-return bracket, with boundary cases deliberately separated from ambiguous floating-point endpoints.

- **66 root controls passed:** unique crossings, two crossings, a unique tangency, two tangencies, a triple flat crossing plus another root, tangency plus a crossing, close roots separated by `2**-19`, positive quadratics with no real root, one real root despite extra sign variations, and roots excluded by the configured bracket. Each runs at both annual/monthly periods and three exact binary scales (`2**-32`, `1`, `2**32`). `assess_irr` and `irr_roots` agree on statuses and complete expected root sets. Maximum annual-rate error was `3.997e-15`; maximum NPV residual divided by absolute coefficient sum was `1.016e-16`.
- **Four solver-domain controls passed:** identically zero NPV, a nonconventional 25-term series and unsupported irrational periods correctly return `indeterminate` (and raise `IRRIsolationUnsupported` from the lower-level root function). Equal-timestamp terms aggregate to the independently known unique 100% annual return.
- **Five loan controls passed:** schema-valid zero interest, zero-interest 12-month moratorium followed by one repayment, ordinary 12.5% interest, maximum configured 50% interest/180-month term/60-month moratorium, and a tiny positive rate on a one-paise principal. Principal conservation, payment decomposition, balance chaining, interest-only moratorium, zero final balance and lender NPV at the effective annual rate all passed. Nominal `annual_rate / 12` accrual is correctly converted to effective annual discounting in the independent NPV check.

Run from repository root:

```sh
backend/.venv/bin/python audit_reports/independent-26-track-2026-10-04/evidence/domain/numerical-controls.py > audit_reports/independent-26-track-2026-10-04/evidence/domain/numerical-controls-output.json
```

Exit 0; approximately 0.4 seconds including interpreter startup. No database connection or external service was used. Both database URLs and an isolated test database name are explicitly set before imports.

**Precision limit, separated from business findings:** Two additional stress observations scale the two-root polynomial to cashflows of approximately `1.245e-60` and `1.132e-72` rupees. The solver's `max(1, scale) * 1e-64` Decimal zero threshold then dominates: the first result drifts by about `3.63e-4` in rate and the second returns bracket endpoints instead of the actual roots. Exact binary inputs rule out input-coefficient rounding as the cause. This means universal scale invariance is not established. These magnitudes are vastly below monetary precision, and no material, reachable farm-forecast impact was demonstrated, so this is retained as a numerical limitation rather than a new business finding. Existing D26-01 through D26-09 remain unchanged.

These are focused finite controls, not a proof for every cashflow, a full theorem-level review of the root-isolation algorithm, or a high-volume Monte Carlo/optimizer validation.


---

## Independent frontend audit — 2026-10-04

Commit: `7245368fa91333fb39387df789fa4ef9a58dbea3`. No application source edits or fixes. Findings were derived from current source and new probes, without consulting historical audit conclusions. `frontend/AGENTS.md` and installed Next 16.3.8 layout/PWA guidance were read. All new artifacts are in this report directory. No development database or pre-existing application service was used; browser probes used the coordinating auditor's isolated production stack at localhost:3000, in independent Chromium processes and fresh browser contexts, with no account or farm mutations.

### Coverage and boundaries

| Track | Independent work and result |
|---|---|
| 1 Architecture / frontend maintainability | Reviewed Next server/client layout boundaries, provider composition, generated API transport, shared permission/navigation/formatting/picker components, query invalidation and farm remounting. Substantive risks are reflected in concrete findings below. Large page modules are a maintenance concern, not proof of a behavior defect: simulation 4,330 lines; animal profile 2,421; health 2,144; team 2,108. No separate size-only defect was manufactured. |
| 2 Functional journeys | Reviewed login/farm selection, worker handover and task completion, screening intake/review, health round and deep-link entry, breeding/kidding entry, animal filtering, finance/report rendering. Existing 5,248-test baseline passed. New component probes reproduced interrupted screening uploads, stale farm-switch query references, read-only screening action mismatch, and misleading outage copy. Coordinating report owns full real-stack journey coverage. |
| 14 Frontend API boundary | Reviewed real-status response envelopes, null parameter serialization, approved same-origin paths, timeout/session/farm epochs, generated hooks, backend contracts for the specific reproduced findings. Permission mismatch in F26-08 is a UI defect; server enforcement remains in place. No tenant isolation bypass was established. |
| 15 State / races | Reviewed auth and farm epochs, query cancellation/clear, keyed app remount, optimistic duty rollback, URL-state composition, dialog attempt ownership, and worker outbox transaction/lease semantics. New probes focus on provider-effect ordering, live upload/finish interaction, and tenant-specific query parameters. |
| 16 Offline / PWA | Read complete service worker, offline snapshot/outbox and worker page/shell. Executed actual service-worker source in isolated VM; reproduced cache deletion and conditional blank offline reload in production Chromium. Reviewed actor/farm scoping, persistent write-before-send, replay keys, review receipts, end-shift preservation, TTL/clock validation and import quarantine. No new outbox data-loss defect established. |
| 17 Usability / browsers / devices | Fresh Chromium context at 390×844; worker startup, Telugu selection, repeated reload, offline recovery. Reviewed responsive components, mobile cards, touch controls and retry states. Physical Android/iPad camera, installation, browser storage eviction behavior, battery suspension, poor RF connectivity and assistive hardware were not exercised. |
| 18 Accessibility | Reviewed dialog/table/picker primitives and worker semantics; real axe scan of /worker/offline found three landmark rules stemming from one nested-main defect. Keyboard/screen reader and manual contrast/zoom coverage is not exhaustive. |
| 19 Localization / time | Reviewed catalog loading/persistence, language toggle, formatting module and farm timezone propagation. Independently reproduced initial-worker English, stale language-sensitive formatting, and UTC-naive timestamp day drift. Translation quality/terminology still needs native Telugu review; key parity is not a fluency review. |

### Confirmed findings

<a id="f26-01"></a>

#### F26-01 — Medium — Same-build shell refreshes evict live lazy assets; an offline Telugu reload can become a blank screen

- **Location:** `frontend/public/sw.js:64–78,82–101`; `frontend/src/lib/i18n/index.tsx:151–163,202–203`.
- **Trigger:** Load the worker online, choose Telugu, perform two successful document reloads of the same deployed worker shell. Then cold-reload offline after the ordinary browser HTTP cache no longer holds the lazy Telugu chunk.
- **Expected / actual:** The current build's required locale and runtime assets remain durably available for the offline shell. Instead `publishAssetGeneration` calls each HTML refresh a generation, replaces `current` with HTML-discovered dependencies, shifts the prior `current` to `previous`, and deletes runtime-only chunks on the second refresh. The Telugu catalog is a dynamic import absent from shell HTML. Initial catalog rejection is swallowed while every child, including language/retry controls, stays hidden behind an empty `aria-busy` div.
- **Evidence:** `sw-runtime-asset-probe.mjs/.log` executes actual sw.js and observes cached=true → true → false. `browser-worker-probe.json` confirms actual production chunk `/_next/static/chunks/1ske97k1fzh5d.js` disappears after navigation 2. With only the independent browser's HTTP cache cleared (SW CacheStorage retained), offline reload produced empty body text, one busy container and zero buttons. `worker-offline-locale-cache-evicted.png` visually confirms blank page. `locale-failure.probe.test.tsx` independently confirms rejection has no visible recovery and an `online` event does not recover it.
- **Important limit:** This is **not** an unconditional failure immediately after two reloads. The first real-browser run remained usable because ordinary HTTP cache rescued the missing chunk; that successful control is preserved in `browser-worker-http-cache-intact.json/.log`. Cache clearing models an absent/evicted HTTP entry; it does not claim a measured eviction frequency on physical tablets. Runtime font assets were also absent in the cache-cleared run.
- **Impact:** Offline worker access depends on a secondary cache despite a previously successful online warmup; the entire Telugu surface can be unavailable.
- **Fix direction:** Track actual build generations and retain their runtime dependencies; durably precache/retain the selected locale. Provide visible retry/language recovery outside the gated content if a catalog fails.
- **Confidence:** High (source + VM + real production Chromium, with explicit HTTP-cache condition).

<a id="f26-02"></a>

#### F26-02 — Medium — First-time worker setup defaults to English instead of Telugu

- **Location:** `frontend/src/lib/i18n/index.tsx:151–157`; `frontend/src/app/worker/layout.tsx:116–125`; `frontend/src/app/layout.tsx:53–63`.
- **Trigger:** A fresh tablet context visits `/worker/login` without a locale cookie or localStorage preference.
- **Expected / actual:** The explicit worker contract in `frontend/AGENTS.md` and WorkerShell calls for Telugu when the preference is unset. Root layout passes `initialLanguage=null`; LanguageProvider hides its children, selects and persists English, then mounts WorkerShell. Its null-only Telugu default sees the newly written English and cannot run.
- **Evidence:** Actual-provider/actual-shell `worker-default-language.probe.test.tsx`; fresh-context production browser output: `htmlLang=en`, `herdly.language=en`, English setup instructions.
- **Impact:** The intended Telugu-first shared-tablet onboarding starts in English, obstructing workers who rely on the default.
- **Fix direction:** Decide the route-specific initial locale before persisting a fallback/mounting the shell, while preserving deliberate existing preferences.
- **Confidence:** High (component + browser).

<a id="f26-03"></a>

#### F26-03 — Low — UTC-naive health snapshot timestamps display a browser-dependent farm date

- **Location:** `frontend/src/lib/format.ts:237–245`; actual caller `frontend/src/components/health-round-progress.tsx:87`; source contract `backend/app/models/health.py:329–331`, `backend/app/schemas/health.py:127`.
- **Trigger:** View a health round with UTC-naive `snapshot_at="2026-08-05T16:00:00"` for an Asia/Kolkata farm in an America/Phoenix browser.
- **Expected / actual:** Snapshot farm date is 5 Aug 2026. `formatDate` parses the offsetless timestamp in the browser timezone, then converts it to farm time, displaying **6 Aug 2026**. The companion `formatFarmDateTime` correctly gives 5 Aug, 9:30 pm for the same input. The model's UTC-naive timestamp and unmodified datetime schema make this a real caller path, not only hypothetical helper input.
- **Evidence:** `naive-timestamp.probe.test.tsx`, run with `TZ=America/Phoenix`.
- **Impact:** Snapshot/audit chronology is a day wrong for some timezone/time combinations. Date-only values are unaffected; no data mutation was shown.
- **Fix direction:** Normalize offsetless backend datetimes to UTC consistently before converting to the selected farm timezone (or emit explicit UTC offsets throughout the wire contract).
- **Confidence:** High.

<a id="f26-04"></a>

#### F26-04 — Medium — Finishing a walkthrough can abort an actively uploading photo while reporting success

- **Location:** `frontend/src/components/screening-check-dialog.tsx:97–99,244–267,376–378`; backend submit semantics `backend/app/api/screening.py:1209–1258`.
- **Trigger:** Successfully upload one image, start a second slow upload, then select “Finish & process” while the second upload is pending.
- **Expected / actual:** Finish waits for the pending upload or explicitly asks to discard it. The button is disabled only by submit state/zero prior uploads, so it remains enabled. Submission succeeds, closes the dialog, and the open-state cleanup aborts the current object-store request. The walkthrough reports success although the chosen second photo was interrupted.
- **Evidence:** `screening-upload.probe.test.tsx` uses the actual dialog with deterministic API/object-store doubles. The second upload's signal starts un-aborted; Finish is enabled; submit receives batch 99; onFinished fires; that signal becomes aborted. Source review confirms submit accepts registered image rows without waiting for their upload requests.
- **Impact:** A worker can finish a batch with an omitted/interrupted photo and lose their pending capture without a discard warning.
- **Fix direction:** Serialize finish with active uploads and pending selected files, with an explicit discard path if needed; guard the handler as well as button state.
- **Confidence:** High for UI cancellation; no live S3 service was used, so object-store persistence after abort is not asserted.

<a id="f26-05"></a>

#### F26-05 — Low — Switching farms preserves tenant-specific record IDs in root-route query state

- **Location:** `frontend/src/lib/permission-navigation.ts:161–165`; actual navigation `frontend/src/app/farm-select/page.tsx:124–128`; consumers `frontend/src/app/(app)/screening/page.tsx:119,200–205,374–414`, `health/page.tsx:850–881`, `breeding/page.tsx:895`, `kidding/page.tsx:977`.
- **Trigger:** Switch from a farm while viewing `/screening?image_id=71` (also health `task_id`/`animal_id`/`purchase_batch_id`, breeding `ultrasound_id`, kidding `breeding_id`).
- **Expected / actual:** The new farm opens its corresponding module without old-farm record context, just as the helper already drops `/animals/7` style path IDs. Root routes retain the entire query string. Screening requests the prior image ID under the new farm and opens an error/detail panel; health can reopen an old-record prefilled form.
- **Evidence:** `farm-switch-query.probe.test.tsx` verifies all four concrete preserved destinations and contrasts path-ID stripping. `screening-boundaries.probe.test.tsx` feeds the actual helper output into the actual page: image ID 71 is requested and its simulated inaccessible-record response displays the detail error and “Back to list”. Backend get-image filters both image and farm, so 404 is the expected real cross-farm response.
- **Impact:** Confusing stale-record errors and unusable form context after a valid farm switch. **No cross-tenant data access or mutation bypass was established.** Component test simulates the 404 rather than provisioning two farms.
- **Fix direction:** Preserve only farm-agnostic filter/pagination keys across farm changes; strip record IDs and nested return destinations tied to the old farm.
- **Confidence:** High for destination/consumer behavior; medium-high for complete end-to-end symptom (source-supported, not a new real-DB two-farm probe).

<a id="f26-06"></a>

#### F26-06 — Low — Language-sensitive formatting lags a locale switch until another render

- **Location:** `frontend/src/lib/i18n/index.tsx:169–173,175–188`; `frontend/src/lib/format.ts:226–258` and other helpers consulting `getActiveLanguage()`.
- **Trigger:** A mounted page calls `formatDate` without an explicit language and the user switches English to Telugu.
- **Expected / actual:** Text and formatted dates switch together. The context change rerenders children before the provider's effect updates the module-level active language. The visible date remains English (`5 Aug 2026`) even after context and module state are Telugu. Calling the helper afterward yields Telugu, but no render is scheduled to replace the visible stale result.
- **Evidence:** `locale-format-render.probe.test.tsx` uses actual LanguageProvider, locale loader and formatter; verifies context=te, active module=te, DOM still English, direct formatter result Telugu.
- **Impact:** Mixed-language date/number/enum presentation until unrelated state changes; startup with a loaded non-default catalog has the same ordering hazard. The probe demonstrates date formatting specifically.
- **Fix direction:** Pass the current context locale into rendering helpers or expose the language through a render-consistent subscribed state rather than an after-render side effect.
- **Confidence:** High.

<a id="f26-07"></a>

#### F26-07 — Low — Offline worker page nests duplicate main landmarks

- **Location:** `frontend/src/app/worker/layout.tsx:209`; `frontend/src/app/worker/offline/page.tsx:73`.
- **Trigger:** Open `/worker/offline` once loaded.
- **Expected / actual:** One top-level main landmark. Both shell and page render `<main>`, producing `main > main` with no distinguishing label.
- **Evidence:** Production Chromium measured two mains, one nested. `browser-worker-axe.json` independently reports `landmark-main-is-top-level`, `landmark-no-duplicate-main`, and `landmark-unique` (axe moderate impact); 32 checks passed and no incomplete checks on this tested state.
- **Impact:** Screen-reader landmark navigation exposes ambiguous/nested primary regions. This is one root defect, not three separately counted issues or a claim of complete WCAG conformance testing.
- **Fix direction:** Assign main ownership to the shell or page and use a non-landmark container for the other.
- **Confidence:** High.

<a id="f26-08"></a>

#### F26-08 — Low — Health viewers are offered a screening workflow they cannot submit

- **Location:** `frontend/src/app/(app)/screening/page.tsx:267–269,282–287` and retake action `:421–425`; backend create/upload/submit gates `backend/app/api/screening.py:1104,1213,1270`.
- **Trigger:** A custom role has `health.view` but lacks `health.manage` and opens Screening.
- **Expected / actual:** Management-only intake/retake controls are hidden or explain missing access. “Disease check” is always rendered, although export is correctly manage-gated. It opens the capture workflow, then backend batch creation/upload/submit is denied. The dialog converts creation failure to a generic “Could not start the walkthrough — try again.”
- **Evidence:** `screening-boundaries.probe.test.tsx` renders the actual page with view-only permissions and confirms the Disease check button while export is absent; backend signatures establish enforcement. No forbidden server write is possible in this finding.
- **Impact:** Misleading dead-end action and futile retries for valid read-only users.
- **Fix direction:** Apply `health.manage` to intake and retake controls and retain appropriate permission error feedback.
- **Confidence:** High.

<a id="f26-09"></a>

#### F26-09 — Low — Screening statistics outages are presented as an empty history

- **Location:** `frontend/src/app/(app)/screening/page.tsx:294–299`; wording `frontend/src/lib/i18n/en.ts:1351`.
- **Trigger:** `/api/screening/stats` fails before returning data while the images list is available.
- **Expected / actual:** An actionable unavailable/retry state, distinguishable from a successful empty result. `!stats` shares the empty branch and displays “No model runs recorded yet.” There is no statistics error notice or dedicated retry control.
- **Evidence:** `screening-boundaries.probe.test.tsx` passes an errored stats query with a successful list into the actual page and observes the empty-history copy with no alert.
- **Impact:** Operators mistake an unavailable metrics service for an absence of screening history and have no local recovery action. No incorrect model accuracy calculation was alleged.
- **Fix direction:** Handle stats error separately, and show stale-data status if prior stats exist.
- **Confidence:** High.

### Reproducible validation receipts

Commands run from repository root unless a directory is stated. Probe tests intentionally assert the observed defective behavior; passing them confirms reproduction, not correctness of the application.

| Command | Result / evidence |
|---|---|
| `cd frontend && pnpm test` | PASS: 326 files, 5,248 tests, 310.05s. `evidence/frontend/vitest-baseline.log`. Existing jsdom “scrollTo/navigation not implemented” warnings; no failures. |
| `cd frontend && pnpm lint` | PASS, exit 0. `evidence/frontend/lint.log`. |
| `cd frontend && pnpm typecheck` | PASS, exit 0. `evidence/frontend/typecheck.log`. |
| `TZ=America/Phoenix frontend/node_modules/.bin/vitest run --config audit_reports/independent-26-track-2026-10-04/evidence/frontend/vitest-audit.config.mjs` | PASS: 7 new files, 9 independent reproduction test cases, 3.89s. `evidence/frontend/targeted-probes.log`. Config explicitly includes only evidence-directory probes and bypasses baseline setup that preloads Telugu. |
| `node audit_reports/independent-26-track-2026-10-04/evidence/frontend/sw-runtime-asset-probe.mjs` | PASS (reproduced cache loss); `.log` saved. No network/database. |
| `node audit_reports/independent-26-track-2026-10-04/evidence/frontend/browser-worker-probe.mjs` | Completed against coordinating auditor's isolated production stack. Actual cache retention/deletion, blank offline state and landmarks saved in `.json/.log` plus screenshot. Successful HTTP-cache control also retained separately. |
| `node audit_reports/independent-26-track-2026-10-04/evidence/frontend/browser-worker-axe.mjs` | Completed: three axe findings from F26-07, 32 passing checks, no incomplete checks. `.json/.log` saved. |

Harness corrections are preserved rather than mislabeled as product failures: the first component harness did not resolve Next's mocked navigation module (`targeted-probes-harness-error.log`); adding an explicit Next alias fixed it. Initial browser navigation used `networkidle` and timed out at 90s (`browser-worker-probe-incomplete.log`); the successful probe uses DOMContentLoaded plus semantic readiness. Initial axe runner used `browser.newPage`, which axe rejected; switching the probe to an explicit new browser context fixed it (`browser-worker-axe-harness-error.log`).

### Remaining test gaps, not additional defects

- Real camera/S3 upload and interruption semantics need a disposable object-store integration test; F26-04 proves the frontend abort, not whether an already-transmitted S3 request finishes after cancellation.
- Durable offline behavior under OS storage pressure, physical device sleep, process kill, installation/update and actual hardware clock changes was not exercised. Controlled HTTP-cache removal establishes F26-01's causal condition but not field frequency.
- This worker-focused browser pass is Chromium only; the coordinating report must identify any additional Firefox/WebKit evidence. No blanket compatibility claim is made.
- Full native Telugu linguistic review, manual screen reader usability, low-vision/zoom, touch accuracy and actual farm usability remain human/device work.
- Baseline tests preload the Telugu catalog, which conceals production lazy-loader ordering/failure conditions unless separate production-style provider tests are used. All baseline green results coexist with the independent confirmed findings above.


### Fresh configured coverage gate — track 26 addendum

A separate fresh coverage run completed successfully after the plain test baseline; the earlier baseline alone did not enforce coverage floors. This was one gate execution, with no retry, changed threshold, or application source edit. Only report destination/reporters were overridden; all includes, exclusions, worker count and thresholds came from the committed `frontend/vitest.config.ts`.

Exact command, from `frontend`:

```sh
pnpm exec vitest run --coverage --coverage.reportsDirectory=../audit_reports/independent-26-track-2026-10-04/evidence/frontend/coverage --coverage.reporter=text --coverage.reporter=json --coverage.reporter=json-summary --coverage.reporter=html
```

**PASS, exit 0:** 326 test files and 5,248 tests; 466.13 seconds. V8 coverage includes 138 instrumented report entries. Global totals: statements **9,311/9,855 (94.47%)**, branches **9,808/10,678 (91.85%)**, functions **2,475/2,620 (94.46%)**, lines **8,452/8,738 (96.72%)**. All configured global, layer and entrypoint floors passed. Only the existing jsdom scroll/navigation warnings appeared; no test or threshold failure required investigation or retry.

| Configured scope | Files | Statements actual / floor | Branches actual / floor | Functions actual / floor | Lines actual / floor | Result |
|---|---:|---:|---:|---:|---:|---|
| `Global` | 138 | 94.47% / 90% | 91.85% / 87% | 94.46% / 90% | 96.72% / 90% | PASS |
| `src/app/[(]app[)]/**` | 40 | 94.75% / 80% | 92.16% / 60% | 95.29% / 80% | 96.86% / 80% | PASS |
| `src/app/**` | 53 | 93.88% / 55% | 91.34% / 50% | 94.46% / 55% | 96.46% / 55% | PASS |
| `src/components/**` | 44 | 96.37% / 75% | 93.92% / 70% | 97.12% / 80% | 98.11% / 75% | PASS |
| `src/lib/**` | 38 | 95.12% / 85% | 92.89% / 85% | 92.95% / 85% | 96.70% / 85% | PASS |
| `src/proxy.ts` | 1 | 100.00% / 80% | 100.00% / 80% | 100.00% / 80% | 100.00% / 80% | PASS |
| `src/app/error.tsx` | 1 | 100.00% / 80% | 100.00% / 80% | 100.00% / 80% | 100.00% / 80% | PASS |
| `src/app/manifest.ts` | 1 | 100.00% / 80% | 100.00% / 80% | 100.00% / 80% | 100.00% / 80% | PASS |
| `src/app/healthz/route.ts` | 1 | 100.00% / 80% | 100.00% / 80% | 100.00% / 80% | 100.00% / 80% | PASS |
| `src/app/[(]app[)]/layout.tsx` | 1 | 100.00% / 80% | 100.00% / 80% | 100.00% / 80% | 100.00% / 80% | PASS |
| `src/app/worker/layout.tsx` | 1 | 81.11% / 80% | 74.19% / 70% | 91.66% / 70% | 88.29% / 80% | PASS |

Evidence:

- Full console log: `evidence/frontend/coverage-gate.log`.
- Machine-readable aggregate/per-file coverage: `evidence/frontend/coverage/coverage-summary.json`.
- Raw Istanbul-compatible coverage: `evidence/frontend/coverage/coverage-final.json`.
- Navigable HTML coverage: `evidence/frontend/coverage/index.html`.
- All configured threshold comparisons with numerator/denominator: `evidence/frontend/coverage-threshold-results.json`; deterministic summary script `evidence/frontend/summarize-coverage.py`.
- Config snapshot: `evidence/frontend/coverage-config-excerpt.txt`.

The per-scope table is independently summed from the new JSON summary using the exact configured prefix/file scopes, with Istanbul-style truncation to two percentage decimals. Installed Vitest's threshold implementation was checked: global coverage includes all files, overlapping glob scopes are independently checked, and these thresholds are aggregate per scope (`perFile` is unset). This independently corroborates the gate's exit status. The worker layout statement floor is the narrowest margin (81.11% actual versus 80% minimum); it still passed. Coverage success does not invalidate the separately reproduced product defects above.


### Coordinating reviewer supplement: build budgets and mutation-harness contracts

Both checks ran once against the unchanged current source and existing fresh production build:

- `pnpm check:route-js-budget`: **PASS**, exit 0. `/login` initial JavaScript is 446,517 gzip bytes against 465,920; `/simulation` is 529,323 against 547,840. The script's lazy-Telugu checks also passed. These are bundle-size gates, not measured device latency or production capacity. [Receipt](evidence/frontend/route-js-budget.log).
- `node --test mutation/mutate_harness.test.mjs`: **PASS**, exit 0, 22 tests, no skips/failures, 17.31 seconds. The `smoke: failed` line is output from an intentional negative control; its enclosing assertion passed. This validates the harness contracts, not a new full-project mutation campaign. [Receipt](evidence/frontend/mutation-harness-contracts.log).


### Retained simulation screenshot review (track 17 supplement)

Visually inspected all eight retained Chromium/WebKit simulation screenshots: results and advanced analysis at desktop 1440×1000 and mobile 390×844 CSS viewports. Metric cards, text, and run options visibly reflow within the mobile width. No additional persistent layout defect was established. A calibration toast is visibly clipped during the immediate desktop-to-mobile resize in three captures; it is fully visible in the later WebKit capture, consistent with the installed 400ms toaster transform transition. This observation is documented with its timing limit, not counted as a new stable layout finding.

[The screenshot review and all eight image links](evidence/frontend/screenshot-review.md) record the examined views, source-supported transition explanation, and limits. This was inspection of existing images only: no browser/server/test was rerun. The captures do not establish physical-device, touch/keyboard, zoom, screen-reader, dark-theme, Telugu, or Firefox behavior, and do not change the failed full WebKit baseline.


---

## Independent audit: tracks 20–26

Audited commit: `7245368fa91333fb39387df789fa4ef9a58dbea3` on 2026-10-04. This review inspected current application code, operations scripts, Compose manifests, Dockerfiles, workflows and tests. It did not use previous audit conclusions. Application source was not changed. All external-provider and release-deletion probes used local fakes; the actual screening pipeline used only the disposable `goatfarm_test_a26_ops_worker` database, which the fixture removed after the run.

### Coverage and fresh verification

| Track | Substantive coverage | Result and limits |
|---|---|---|
| 20. Performance and capacity | Shared simulation admission, CPU-budget bookkeeping, cancellation/native-thread leases, SQL-side dashboard/feeding aggregation, capped owner/history queries, password-worker admission, database pool bounds, Compose CPU/memory limits. | Synthetic admission probe confirmed two occupied slots reject the third request with 429 in about 3 ms; cancelling the HTTP task does not release still-running native work; all keyed locks clear on completion. The HTTP-method metric label is explicitly normalized to `OTHER`, and route labels use templates. No additional defect confirmed. Production-scale latency, concurrent population, memory high-water marks and database query plans were not measured. |
| 21. Background work and external services | Screening worker configuration/heartbeat/restart policy, actual pipeline success and provider-error paths, retry budgets, provider HTTP adapters, notification send/outbox concurrency and ambiguous-delivery policy, retention maintenance. | O26-01. Full screening probe: **2 passed**, observing the failure with both successful and failed fake providers. No live S3, AI or SMS provider was contacted. |
| 22. Deployment and configuration | Development/production Compose isolation, least-privilege settings projections, secret-file/inline ambiguity guard, TLS requirements, container hardening, fresh current-revision container builds, configuration validation and runtime commands. | O26-01 crosses the worker projection boundary; O26-07 and O26-08 independently block the frontend Docker build. Current backend/edge images build. Shell syntax and workflow parsing pass. This was not an actual production deployment or verification of deployed secrets/TLS. |
| 23. Backup and recovery | Backup producer locking, atomic publication, GPG scope, checksum/inventory binding, freshness calculation, restore signature/empty-target/atomic transaction checks, restore floor, systemd schedules and current runbooks. | O26-04. Timestamp-tamper probe reproduced false freshness, then was strengthened with a genuine 72-hour-old GPG signature/encryption over an audit fixture using an owned temporary keyring. No database restore or off-site recovery drill was performed; production RPO/RTO remains unverified. |
| 24. Monitoring and operations | Private API metrics, process-local collectors, worker heartbeat, maintenance counters, cAdvisor/blackbox/node-exporter scrape configuration, alert rules, freshness textfile and off-host log requirements. | O26-02 and O26-04; O26-01 is also masked by the worker's normal `ok` cycle heartbeat because individual image errors do not throw from the cycle. Alert delivery and deployed exporters were not exercised. |
| 25. Dependencies, CI and releases | Frozen dependency manifests, hash-pinned bootstrap, patched dependency verifier, pinned workflow actions/container inputs, CI gates, release preflight, per-architecture scanning/signing and cleanup ownership. | O26-03, O26-07–09. Fresh backend Python dependency audit: **59 dependencies, no known vulnerabilities**. Frontend: **0 unmitigated, 1 locally mitigated braces advisory**, with 19 depth-guard and 4 ordinary-pattern checks passing. Fresh Trivy DB and digest-pinned scanner scanned four shipped bases/services and two current source builds: backend and PostgreSQL fail the repository's vulnerability policy; current edge passes. Current frontend image scan is blocked by O26-07/08. No actual release was performed. |
| 26. Test and evidence integrity | Existing integration-fixture boundaries, CI coverage/mutation evidence flow, raw attempt/summary agreement, failed/empty SARIF, scan/coverage artifact handling, immutable revision/OpenAPI gates. | O26-05 and O26-06. Exact CI Ruff check/format pass; exact separate mypy commands pass for application/scripts/CI tools (**154 files**), mutation harness (**12 files**), and tests (**153 files**). Full backend/frontend suites are covered by the other current audit tracks, not duplicated here. |

Evidence directory: [evidence/ops](evidence/ops). `actionlint.txt` is empty because actionlint passed. `shell-syntax.json` records five successful shell checks. The initial combined mypy command encountered duplicate module naming; the three exact, separate CI invocations then passed. That invocation issue is not an application finding. Dependency-audit cache warnings explicitly say the cache entries were ignored; the audit exited successfully and retained its full fresh result.

Container controls used the already available Docker/Colima daemon and pinned scanner/GPG tool images; no host scanner or GPG installation was needed. Docker's `/tmp` bind mount is VM-local in this environment, so the valid probes used a shared home directory and checked input-file presence before invoking the package manager. All audit-owned image tags/leaf images, the stopped failed-build container, scanner archives/database caches, temporary package fixtures, GPG keyring and temporary pip-audit environment were removed; [container-probe-cleanup.json](evidence/ops/container-probe-cleanup.json) records exact targets/results. Existing base/tool/app images and user stacks were untouched; no global prune was run, and ordinary Docker build-cache parent layers may remain. Independent peer review in [peer-review-ops.md](peer-review-ops.md) corroborates O26-01–04 and O26-07/08, including fresh countercontrols for both new build failures.

**Supplementary track 12 migration receipt:** The exact CI sequence `alembic upgrade head` → `alembic check` → `alembic downgrade base` → `alembic upgrade head` → `alembic check` passed all five commands on newly created `goatfarm_test_a26_migration_9b83417e`. Both database URL variables explicitly named only that disposable target, the ambient backend `.env` was verified absent, and the probe refused a pre-existing database. Each step independently checked `current_database()` and captured table/revision state; downgrade left only the empty Alembic marker table, and both metadata checks found no new upgrade operations. The owned database was dropped without FORCE and its absence verified. Full command/output/schema/cleanup receipts: [migration-roundtrip.log](evidence/ops/migration-roundtrip.log), [migration-roundtrip.json](evidence/ops/migration-roundtrip.json), [reproduction script](evidence/ops/migration-roundtrip.py). This adds empty-schema migration assurance; it does not establish populated historical-data upgrade/downgrade compatibility or production lock/TLS behavior. No additional finding resulted.

<a id="o26-01"></a>

### O26-01 — High — Production screening consumes provider calls but terminally fails provider-processed images

**Tracks:** 21, 22, 24. **Confidence:** high, actual PostgreSQL pipeline reproduction.

**Prerequisite:** Enable screening in the supplied production worker topology. No attacker is required. The worker receives database/storage/provider settings, while API cookie, HMAC, TOTP and CORS settings are deliberately absent.

**Location:** `backend/app/metrics.py:89` (`enabled()` calls full API `get_settings()`), `backend/app/metrics.py:138`, `backend/app/services/screening/pipeline.py:875`, `backend/app/core/config.py:1714`, `docker-compose.production.yml:308`. Failure settlement is `backend/app/services/screening/pipeline.py:797`; terminal attempt handling is line 825. `backend/app/worker/__init__.py:203` publishes `ok` after this returned cycle.

**Reproduction:** From `backend`, run the retained `probe_worker_metrics.py` with `PYTHONPATH=.` and the venv interpreter. Valid production `ScreeningWorkerSettings` is accepted, but `_record_run` raises `ValidationError` when it constructs full API settings. The stronger retained `test_production_worker_cycle.py` drives the real `run_screening_cycle`, with the existing in-memory storage and provider doubles, on its own explicitly named test database. It tests a healthy provider and a provider outage over all five permitted attempts.

**Expected versus actual:** A valid worker configuration should produce a durable screening verdict/run or a durable provider-error run. Both actual cases instead produce image `ERROR`, zero `ScreeningRun` rows, and one durable call reservation per attempted provider call. After five attempts, the image is terminal with `unexpected screening failure; terminal after 5 attempts`. The provider-success case loses a valid healthy result. The provider-error case also fails while trying to record the failed attempt; fallback/error recording therefore does not avoid the defect. Independent peer review also reproduced the configuration failure with `GOATFARM_METRICS_ENABLED=false`: reading that flag itself constructs the invalid API settings. Images rejected before provider work are outside this failure claim.

**Impact:** Production screening is unusable under the documented credential separation; repeated real model calls would consume budget and money without a usable review result. The cycle catches the settings exception per image, so the worker can continue reporting healthy cycles instead of its consecutive-cycle-failure restart path firing.

**Recommendation:** Give metrics a worker-safe configuration source, or pass an explicit metrics enablement setting into the worker-owned collector. Do not make the worker receive unrelated API secrets to satisfy telemetry. Add an integration regression that runs the whole pipeline with only the production worker's delivered environment, for both successful and failed providers.

**Evidence:** [worker-metrics-result.json](evidence/ops/worker-metrics-result.json), [worker-cycle-pytest.txt](evidence/ops/worker-cycle-pytest.txt), [worker-cycle-provider-success.json](evidence/ops/worker-cycle-provider-success.json), [worker-cycle-provider-failure.json](evidence/ops/worker-cycle-provider-failure.json), [test_production_worker_cycle.py](evidence/ops/test_production_worker_cycle.py).

<a id="o26-02"></a>

### O26-02 — Medium — Screening counters are collected in a process that has no scrape endpoint

**Tracks:** 24, 21. **Confidence:** high.

**Prerequisite:** Screening work runs in the separate worker process. This applies to development today and remains after O26-01 is corrected in production.

**Location:** `backend/app/metrics.py:27` creates a private process-local registry; lines 115–142 register/increment screening call and cost counters. `backend/app/api/operational_metrics.py:16` exports only the API process registry. `backend/app/worker/__init__.py:250` starts only the worker loop. `ops/prometheus/prometheus.scrape.example.yml:1` has an API scrape and no worker metrics scrape/exporter.

**Reproduction:** Run [probe_process_metrics.py](evidence/ops/probe_process_metrics.py). A worker-shaped Python process records a successful call and exposes a local sample with value 1. An independent API-shaped Python process imports and renders its registry; it contains no screening call sample.

**Expected versus actual:** The supplied screening call/error/spend telemetry should be available to the supplied scraper. Actual increments remain in worker memory; no worker HTTP exporter, shared multiprocess collector or durable API collector transfers them to `/metrics`.

**Impact:** Operators cannot use these advertised metrics to observe screening call failures or estimated spend through the shipped scrape path. The database call-budget ledger continues to enforce admission; this finding is about unavailable monitoring, not a budget bypass.

**Recommendation:** Expose a protected worker registry and configure its scrape, or export durable screening aggregates from the API. Use an integration check involving two actual processes rather than inspecting one registry in a test process.

**Evidence:** [process-metrics-result.json](evidence/ops/process-metrics-result.json).

<a id="o26-03"></a>

### O26-03 — Medium — Re-running an existing release deletes package versions owned by the earlier successful run

**Tracks:** 25. **Confidence:** high for emitted deletion requests; deployed final-index availability was not tested.

**Prerequisite:** A successful version retains its `<TAG>-publish-amd64` and `<TAG>-publish-arm64` tags, an operator reruns that release, and the workflow token is allowed to delete package versions. Successful runs currently leave those temporary tags in place.

**Location:** `.github/workflows/release.yml:118` rejects the existing release; lines 508–538 then run cleanup on any failure. Only final `<TAG>` cleanup is gated by the assembly ownership marker; per-architecture publish tags at line 529 are always selected.

**Reproduction:** [probe_release_cleanup.py](evidence/ops/probe_release_cleanup.py) extracts the current preflight and cleanup shell scripts, substitutes a fake `gh` executable, and supplies a previously successful release plus its existing publish tags. Preflight exits 1 before any build or push. With no assembly ownership marker, cleanup nevertheless emits **six package-version DELETE requests**, two for each image repository.

**Expected versus actual:** Refusing to rerun an immutable version should leave that prior release's artifacts unchanged. Actual failure cleanup deletes pre-existing package versions it never created in this attempt. The inline claim that cleanup deletes only this run's temporary tags is therefore false.

**Impact:** Previously verified release artifacts/architecture references can be lost during a harmless rejected rerun. GHCR deletion operates on package versions, not just the matching tag, so additional tags attached to the selected version are also at risk. Whether the final assembled deployment index loses accessible child manifests depends on registry reference/garbage-collection behavior and is not claimed as reproduced here.

**Recommendation:** Name temporary references per run/attempt, record the exact version/digest objects successfully published by that attempt, and delete only that owned set. All cleanup must be a no-op when preflight fails.

**Evidence:** [release-cleanup-result.json](evidence/ops/release-cleanup-result.json). No real GitHub or registry mutation occurred.

<a id="o26-04"></a>

### O26-04 — Medium — Unsigned recovery metadata can make an unchanged stale backup appear fresh

**Tracks:** 23, 24. **Confidence:** high.

**Prerequisite:** Ability to replace or manually rewrite the backup's `.recovery.json` sidecar in the local/off-site backup set. The normal `capture` and `bind` commands refuse overwriting existing output; this probe does not claim they perform that rewrite themselves. This is not an unauthenticated web exploit and does not require the GPG signing/decryption key. A trusted host with immutable authenticated sidecars would reduce this risk, but the shipped set does not authenticate them.

**Location:** `backend/scripts/backup.sh:431` signs/encrypts only the database dump; lines 458–474 bind and publish the recovery JSON afterward. `backend/scripts/recovery_inventory.py:287` validates its self-declared fields and archive digest but no signature. `backend/scripts/check_backup_freshness.py:93` ranks by that inventory's `generated_at`; line 185 uses it as recovery age.

**Reproduction:** [probe_backup_freshness.py](evidence/ops/probe_backup_freshness.py) creates synthetic archive/checksum/inventory files with a 72-hour-old inventory. The checker exits 2. Changing **only** `generated_at` in the unsigned inventory to now makes the checker exit 0 and print `fresh complete backup ... (0.0h old)`. Archive bytes/hash and object recovery-point/receipt remain unchanged. The stronger [probe_signed_backup_freshness.py](evidence/ops/probe_signed_backup_freshness.py) then generated a real RSA key in an owned temporary keyring and genuinely signed/encrypted an audit fixture using GPG 2.4.7 inside a pinned tool image. GPG's clock was set back 72 hours for key/signature creation; both before and after the sidecar rewrite GPG reported the exact same `GOODSIG`/`VALIDSIG` with the old cryptographic timestamp. Freshness again changed from exit 2 at 72 hours to exit 0 at 0 hours. Private key material was confined to and removed with the owned temporary directory. This verifies the cryptographic-envelope boundary; the plaintext was a clearly labeled fixture, not a PostgreSQL archive, and no restore is claimed.

**Expected versus actual:** Mutating untrusted backup metadata should not establish a newer authenticated recovery point. Actual verification proves only that metadata names the archive's current digest; that one-way hash binding does not authenticate the metadata, its timestamp, object manifest, or key-escrow identities. `generated_at` is inventory capture time, not an independently verified object snapshot or database dump timestamp.

**Impact:** The whole-system RPO alert can be suppressed while the actual recovery point is stale, and recovery instructions/escrow identities can be substituted independently of an authentic database payload. The database GPG authentication itself remains intact.

**Recommendation:** Authenticate a manifest containing the exact archive digest, database recovery timestamp, object recovery point and escrow identities, and verify it before freshness/restore decisions. Calculate the whole-system age from authenticated component recovery times, not solely the time a descriptive inventory was generated.

**Evidence:** [backup-freshness-result.json](evidence/ops/backup-freshness-result.json), [signed-backup-freshness-result.json](evidence/ops/signed-backup-freshness-result.json).

<a id="o26-05"></a>

### O26-05 — Low — Mutation gate trusts summary scores that contradict the raw attempts

**Tracks:** 26, 25. **Confidence:** high for validator behavior; no claim that the current report producer emitted a false passing report.

**Prerequisite:** An erroneous, stale, malformed or altered aggregate report with matching target IDs/campaign identity reaches the final mutation gate. Normal current producers may avoid these values, but the purported independent evidence check does not detect them.

**Location:** `.github/scripts/check_mutation_report.py:89` validates raw record count/IDs/campaign but never their verdicts or complete-selection metadata; lines 127–140 trust measured count and score from the summary, including non-finite floats. The checked-in acceptance test in `backend/tests/test_ci_mutation_gate.py` also supplies records without verdicts.

**Reproduction:** Run [probe_evidence_gates.py](evidence/ops/probe_evidence_gates.py). A plan containing one target passes with (a) an `INCONCLUSIVE_TIMEOUT` raw attempt plus a `complete_measured=1, score=100` summary, (b) a `SURVIVED` raw attempt plus score 100, and (c) a `SURVIVED` raw attempt plus JSON `NaN` score. All three exit 0.

**Expected versus actual:** The gate should derive complete measured attempts and kill ratio from the exact raw evidence, reject contradictions, and reject non-finite/out-of-range scores. Actual checks accept the summary's assertion even though the retained raw evidence disproves it; NaN bypasses the numeric threshold comparison.

**Impact:** A reporting/provenance integration failure can turn inconclusive or ineffective mutation testing into a passing assurance result. This is evidence hardening, not a demonstrated bypass by ordinary application users.

**Recommendation:** Recompute measured status and score from raw complete-selection records, verify all summary fields match, and require finite bounded numeric values. Add the contradictory-evidence cases as contract regressions.

**Evidence:** [evidence-gates-result.json](evidence/ops/evidence-gates-result.json).

<a id="o26-06"></a>

### O26-06 — Low — SARIF gate reports a passed scan for zero runs or an explicitly failed invocation

**Tracks:** 26, 25. **Confidence:** high for validator behavior; upstream CodeQL action failure remains an independent guard.

**Prerequisite:** An incomplete or failed SARIF document reaches the local gate while the upstream workflow step has not already failed. This was tested only with synthetic reports; it does not establish that the current CodeQL action produces such a green workflow.

**Location:** `.github/scripts/gate_sarif.py:113` accepts an empty `runs` array and skips malformed/missing results; line 147 treats an empty finding list as passed without checking `invocations[].executionSuccessful` or analysis error notifications.

**Reproduction:** The retained evidence-gate probe invokes the CLI with `{"version":"2.1.0","runs":[]}` and with a run containing `executionSuccessful:false` and `results:[]`. Both exit 0 and print `SARIF gate passed: no error/high findings in 1 file(s).`

**Expected versus actual:** Incomplete/failed analysis should produce an inconclusive/rejected result. Actual behavior equates lack of findings with successful analysis.

**Impact:** A scanner-output or workflow integration regression can retain a misleading passed security verdict despite no completed analysis. Ordinary CodeQL action exit failures still fail the workflow, limiting present exploitability.

**Recommendation:** Require at least one structurally valid expected-tool run, reject explicit unsuccessful invocations/error-level execution notifications, and distinguish a successful zero-findings scan from missing or failed analysis.

**Evidence:** [evidence-gates-result.json](evidence/ops/evidence-gates-result.json).


<a id="o26-07"></a>

### O26-07 — Medium — Current frontend Docker build fails on malformed Corepack package-manager declaration

**Tracks:** 22, 25. **Confidence:** high, actual current-source Docker build failure.

**Prerequisite:** Build the supplied frontend Dockerfile using its pinned Node image and committed package manifest. No attacker or unusual input is needed.

**Location:** `frontend/package.json:63`; `frontend/Dockerfile:4` enables Corepack and line 6 invokes the shim. The builder's later `pnpm build` at line 18 uses the same declaration.

**Reproduction:** `docker build -t goatfarm-a26-frontend:7245368 -f frontend/Dockerfile frontend` at the audited commit. The exact pinned Node image reaches dependency stage step 5, then exits 1: `Invalid package manager specification in package.json (pnpm@9.15.9+sha512-...==); expected a semver version`. A disposable copy with only `packageManager` changed to `pnpm@9.15.9` lets Corepack run the intended pnpm 9.15.9, exposing the distinct O26-08 failure. Source files were not edited.

**Expected versus actual:** The repository's own pinned build should bootstrap the declared package manager. Actual Corepack rejects the npm-style integrity suffix before package installation begins. A preinstalled local pnpm and successful local Next build do not exercise this path.

**Impact:** Fresh production frontend image builds and release workflows using this Dockerfile are blocked. This does not establish an outage in an already-deployed image.

**Recommendation:** Generate a valid Corepack `packageManager` declaration with the intended version and supported integrity format, and verify an actual clean Docker build in CI. Preserve integrity pinning rather than simply deleting it in the final fix.

**Evidence:** [frontend-container-build.txt](evidence/ops/frontend-container-build.txt), [frontend-container-input-results.json](evidence/ops/frontend-container-input-results.json), [probe_frontend_container_inputs.py](evidence/ops/probe_frontend_container_inputs.py).

<a id="o26-08"></a>

### O26-08 — Medium — Frontend dependency layer omits the required local vulnerability patch

**Tracks:** 22, 25. **Confidence:** high, isolated failing case and passing control.

**Prerequisite:** O26-07 is corrected or otherwise bypassed so pnpm 9.15.9 reaches installation. This is a separate, latent build blocker, not the first error in the unmodified current build.

**Location:** `frontend/Dockerfile:5` copies only `package.json` and `pnpm-lock.yaml` before line 6 installs them; `frontend/package.json:78` and `frontend/pnpm-lock.yaml:22` require `patches/braces@3.0.3.patch`. The later `COPY . .` at Dockerfile line 17 occurs in the next stage, after installation must already have succeeded.

**Reproduction:** The retained probe copies exactly the two dependency-stage inputs into an owned temporary directory and corrects only the malformed package-manager value there. In the same pinned Node image, `corepack enable && pnpm --version && pnpm install --frozen-lockfile` reports 9.15.9 and exits 254 with `ENOENT: no such file or directory, open '/app/patches/braces@3.0.3.patch'`. Copying the existing repository `patches/` directory into that same fixture makes installation exit 0. The probe checks that the manifest bind mount exists; temporary files and installed dependencies were removed afterward.

**Expected versus actual:** All lockfile inputs needed for frozen installation should be present in the dependency layer. The required security patch is absent, so correcting O26-07 alone still cannot build the frontend image.

**Impact:** The production image cannot incorporate the committed dependency mitigation because its installation never completes. This finding does not assert that a successfully produced current image silently omitted the patch.

**Recommendation:** Copy the committed patch directory before `pnpm install` and include it in dependency-cache invalidation. Verify a clean Docker installation and the existing patched-dependency checks.

**Evidence:** [frontend-container-missing-patch.txt](evidence/ops/frontend-container-missing-patch.txt), [frontend-container-input-positive-control.txt](evidence/ops/frontend-container-input-positive-control.txt), [frontend-container-input-results.json](evidence/ops/frontend-container-input-results.json).

<a id="o26-09"></a>

### O26-09 — Medium — Pinned backend and PostgreSQL images fail the current vulnerability policy

**Tracks:** 25, 22. **Confidence:** high for scanner/package-version and policy-gate results; runtime reachability/exploitation was not established.

**Prerequisite:** Build the current backend Dockerfile or use the current Compose PostgreSQL digest, then scan with the fresh 2026-10-04 vulnerability database and the repository's declared HIGH/CRITICAL, fixable-only policy.

**Location:** `Dockerfile:10` pins the backend Python base and deliberately does not upgrade its OS packages. `docker-compose.yml:54` and `.github/workflows/security.yml:140` pin PostgreSQL. The policy gates are `.github/workflows/security.yml:205` and line 221; `.trivyignore.compose-images` is scoped only to PostgreSQL.

**Reproduction:** Current backend and edge Dockerfiles were built directly from audited commit `7245368fa91333fb39387df789fa4ef9a58dbea3`, without source edits, deployment or registry writes. [scan_container_images.py](evidence/ops/scan_container_images.py) exports immutable local image IDs and scans their tarballs using `aquasec/trivy:0.74.0@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969`. The DB was freshly downloaded at `2026-10-04T22:37:24Z`, with `UpdatedAt=2026-10-04T19:39:34Z`; scans were then offline. All scanned images are Linux ARM64, so this is not evidence for the untested AMD64 variant. Exact image IDs/digests, DB SHA-256, commands, policy-file SHA-256 and all results are retained in [container-scan-manifest.json](evidence/ops/container-scan-manifest.json).

**Policy:** Vulnerability-only scans, HIGH/CRITICAL, `--ignore-unfixed`, exit code 1 for remaining findings. Only the PostgreSQL policy run loads the exact preserved `.trivyignore.compose-images`; backend/edge load no suppression file. Each image also has an unfiltered all-severity/all-fix-status report with no suppressions. The workflow's snakeoil-key exclusion concerns secret scanning; these runs scan vulnerabilities only and make no secret-scan claim.

**Expected versus actual:** The declared gate requires zero unsuppressed fixable HIGH/CRITICAL records. The actual current backend has **20 records (17 HIGH, 3 CRITICAL; 16 distinct advisories)** and exits 1. PostgreSQL still has **8 HIGH records (4 distinct advisories)** after its documented suppressions and exits 1. Current edge exits 0 with zero HIGH/CRITICAL records. The four raw pinned base/service scans are also preserved; base-only findings are not automatically attributed to a final application image that removes build tools. A current full frontend runtime could not be built because of O26-07/08, so its base scan is not represented as a completed runtime scan.

**Impact and limits:** These reproducible failures block the repository's stated security acceptance policy and show stale vulnerable package versions in shipped runtime inputs. Scanner severity is not the audit's application severity: no remote code execution, unauthenticated attack path or exposure of every listed component is established. For example, the Perl 32-bit advisory does not prove applicability to this ARM64 run; source-package matching can also cover optional modules. The confirmed finding is image freshness/policy noncompliance, assessed Medium, rather than 28 independently proven exploitable application vulnerabilities. Existing CI is expected to fail on this evidence; a gate bypass is not claimed.

**Recommendation:** Refresh the affected immutable bases/service digest to reviewed images with the fixes, or remove unneeded components in a reproducible build; rerun scans for both release architectures. Where an advisory is not applicable, document a component-specific, bounded exception with evidence instead of treating a scanner label as exploitability proof. Do not extend broad suppressions merely to make the gate green.

All unsuppressed package/advisory records failing those two runtime gates follow. Fixed versions are the fresh scanner's package-manager recommendations and have not been installed by this audit.

#### Current backend image

| Package | Advisory | Installed | Fixed version | Scanner severity |
|---|---|---|---|---|
| `gzip` | CVE-2026-41992 | `1.13-1` | `1.13-1+deb13u1` | HIGH |
| `libpcre2-8-0` | CVE-2026-103111 | `10.46-1~deb13u1` | `10.46-1~deb13u3` | HIGH |
| `libpcre2-8-0` | CVE-2026-86145 | `10.46-1~deb13u1` | `10.46-1~deb13u2` | HIGH |
| `libpcre2-8-0` | CVE-2026-89157 | `10.46-1~deb13u1` | `10.46-1~deb13u2` | HIGH |
| `libpcre2-8-0` | CVE-2026-89161 | `10.46-1~deb13u1` | `10.46-1~deb13u2` | HIGH |
| `libsqlite3-0` | CVE-2026-11822 | `3.46.1-7+deb13u1` | `3.46.1-7+deb13u2` | HIGH |
| `libsqlite3-0` | CVE-2026-11824 | `3.46.1-7+deb13u1` | `3.46.1-7+deb13u2` | HIGH |
| `libssl3t64` | CVE-2026-75804 | `3.5.7-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `libssl3t64` | CVE-2026-84782 | `3.5.7-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `openssl` | CVE-2026-75804 | `3.5.7-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `openssl` | CVE-2026-84782 | `3.5.7-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `openssl-provider-legacy` | CVE-2026-75804 | `3.5.7-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `openssl-provider-legacy` | CVE-2026-84782 | `3.5.7-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `perl-base` | CVE-2026-13221 | `5.40.1-6` | `5.40.1-6+deb13u1` | CRITICAL |
| `perl-base` | CVE-2026-42496 | `5.40.1-6` | `5.40.1-6+deb13u1` | CRITICAL |
| `perl-base` | CVE-2026-8376 | `5.40.1-6` | `5.40.1-6+deb13u1` | CRITICAL |
| `perl-base` | CVE-2026-42497 | `5.40.1-6` | `5.40.1-6+deb13u1` | HIGH |
| `perl-base` | CVE-2026-48962 | `5.40.1-6` | `5.40.1-6+deb13u1` | HIGH |
| `perl-base` | CVE-2026-57432 | `5.40.1-6` | `5.40.1-6+deb13u1` | HIGH |
| `perl-base` | CVE-2026-57433 | `5.40.1-6` | `5.40.1-6+deb13u1` | HIGH |

#### Pinned PostgreSQL service image, after repository suppressions

| Package | Advisory | Installed | Fixed version | Scanner severity |
|---|---|---|---|---|
| `libpcre2-8-0` | CVE-2026-103111 | `10.46-1~deb13u1` | `10.46-1~deb13u3` | HIGH |
| `libpcre2-8-0` | CVE-2026-89157 | `10.46-1~deb13u1` | `10.46-1~deb13u2` | HIGH |
| `libssl3t64` | CVE-2026-75804 | `3.5.6-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `libssl3t64` | CVE-2026-84782 | `3.5.6-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `openssl` | CVE-2026-75804 | `3.5.6-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `openssl` | CVE-2026-84782 | `3.5.6-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `openssl-provider-legacy` | CVE-2026-75804 | `3.5.6-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `openssl-provider-legacy` | CVE-2026-84782 | `3.5.6-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |

**Evidence:** [backend-container-build.txt](evidence/ops/backend-container-build.txt), [edge-container-build.txt](evidence/ops/edge-container-build.txt), [container-scan-summary.txt](evidence/ops/container-scan-summary.txt), [container-scan-manifest.json](evidence/ops/container-scan-manifest.json), [complete raw scanner inventory](container-vulnerability-inventory.md), and the individual `trivy-*-raw.json` / `trivy-*-repository-policy.json` reports in [evidence/ops](evidence/ops). No pre-existing app image was used as current-build evidence.


## Independent challenge records

- [Domain findings challenged by operations reviewer](peer-review-domain.md).
- [Operations findings challenged by domain reviewer](peer-review-ops.md).
- [Frontend findings challenged by coordinating reviewer](peer-review-frontend.md).
- [Security findings challenged by frontend reviewer](peer-review-security.md).
- [Consolidated report completeness and evidence review](peer-review-consolidated.md).

## Source integrity and cleanup

Final verification found no tracked source changes: all 1,453 snapshotted current-file SHA-256 hashes match, all tracked paths have an empty Git diff, and HEAD remains `7245368fa91333fb39387df789fa4ef9a58dbea3`. Only this new audit directory is untracked. All audit databases under the owned `goatfarm_test_a26_` prefix are absent; owned browser servers are stopped and ports 3000/8000 are free. The pre-existing ignored E2E state was restored byte-for-byte, and temporary JWT/keyring/scan directories were removed. The operations reviewer removed owned container tags/leaf images and temporary fixtures without a global prune; reusable Docker parent build-cache layers may remain. Local ignored build/coverage outputs also remain as ordinary validation artifacts. See [final integrity receipt](evidence/root/source-integrity-check.json), [browser cleanup](evidence/root/browser-cleanup.json), and [container cleanup](evidence/ops/container-probe-cleanup.json).

[Source manifest](evidence/source-manifest.json) · [Validation summary](evidence/validation-summary.json) · [Machine-readable finding index](evidence/findings.json)
