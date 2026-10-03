# Backend/domain audit — current source, 2026-10-03

This is a scoped contribution to the project audit, not an assertion that all application behavior is proven correct. Project application code was not modified. Complete semantic source reading and AST inspection now cover all 85 assigned authored Python files (38,390 lines) after auth/team/shared reallocation. The review ledger has no remaining sampled/AST-only rows. Fresh findings D9–D21 and revised category scores from the full extension are recorded in `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/06-backend-domain-extension.md`. Relevant tests were read selectively; the root audit records full-suite execution and mechanical test-file inventory; it does not claim complete manual test-source review.

## Verified high-priority findings

### D1 — HIGH: OpenAI-compatible screening drops every stage's actual prompt

- Location: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/services/screening/providers.py:226`, request construction at 231–243.
- `OpenAICompatibleProvider.complete(image_jpeg, system_prompt)` never reads `system_prompt`. The request contains only a generic user text, "Analyze this photo. JSON only.", and the image.
- Detector bounding-box instructions, veterinary gate rules, gate response schema, specialist disease vocabularies and cross-check instructions are all omitted. All three stages emit identical wire bodies for the same photo, while their audit runs still record different prompt versions.
- Proof: httpx MockTransport captured three requests for distinct detection/gate/specialist sentinel prompts; all request bodies were identical and no sentinel appeared in any request.
- Consequence: configured OpenAI-compatible/GLM/GPT gateways cannot reliably execute the intended pipeline. Schema failures can consume retries and budget; accidental schema-shaped outputs are unguided, and provenance claims a prompt the model did not receive.
- Improvement: send the stage prompt in a supported system/developer message (or the provider's explicit supported equivalent), and assert the entire instruction and schema at the transport seam for every adapter/stage. Existing `test_screening_provider_gaps.py:61` checks only the token budget for this adapter, unlike the Anthropic test that checks its system prompt.

### D2 — HIGH: unusable photographs become green HEALTHY results

- Location: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/services/screening/pipeline.py:909`, return at 919–921; gate quality rules `/backend/app/services/screening/gate.py:45`.
- Gate prompt explicitly instructs a dark, blurry or goat-free photo to return `flagged=false, quality_problem=true`. `_run_cascade` persists quality in run detail but unconditionally returns HEALTHY whenever `flagged` is false.
- Proof: isolated PostgreSQL integration probe returned `quality_problem=true, flagged=false, confidence=0.1` for ten distinct images; every persisted image was HEALTHY.
- Frontend agent independently verified `quality_problem` is never read in frontend source. `frontend/src/app/(app)/screening/page.tsx:365` and `:620` show HEALTHY with green success; model-run detail only interprets cross-check agreement, with no quality warning or retake action.
- Consequence: absence of usable visual evidence is represented as a successful health clearance, including when the operator opens details.
- Improvement: an explicit UNASSESSABLE/NEEDS_RETAKE outcome, clear camera-quality guidance, new-upload retry flow, and exclusion of such photos from healthy/coverage rates. Preserve the original gate detail.

### D3 — HIGH: full-herd health round becomes DONE after only one pen and one component

- Location: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/api/health.py:926`–955; ET/HS acceptance `/backend/app/services/health.py:224`–230; suppressing future materialization `/backend/app/services/cadence.py:261`–270.
- A herd-level task has no animal/batch target. Any valid bucket/batch-scoped event passes the round gate and calls `complete_task` immediately. The combined "ET + HS ... all animals" round accepts either ET or HS alone.
- Proof: a real ASGI API probe, using actual Alembic-backed test fixtures on a separate throwaway database, created two active goats in different pens. Recording HS for the one FOUNDATION goat returned 201, marked the full round DONE, and left the FEMALE_KIDS goat without a health event and the entire herd without an ET event. The probe passed as a demonstration of the current behavior.
- Current tests explicitly codify the component shortcut (`backend/tests/test_health_safety.py:1897`), so a green suite does not establish complete round coverage.
- Consequence: farm-wide completion and reminder suppression can hide untreated animals or an unfinished component, despite the duty promising "all animals".
- Improvement: model a round with a stable eligible-animal snapshot, component requirements, recorded exclusions/reasons and per-animal progress. Finish only when its coverage contract is met, or rename/split duties to match what they actually certify.

### D4 — MEDIUM: screening's per-farm daily call cap is exceeded within one cycle

- Location: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/services/screening/pipeline.py:475`–492 and claim selection 556–586; reservation formula 390–393.
- Eligibility checks settled run count plus one image's reservation before selecting all images in a cycle. There is no per-farm accumulated reservation in the batch, no durable reservation for other PROCESSING rows, and no budget re-check before each call.
- Proof: with budget 8, crop detection disabled and ten distinct healthy photos, one cycle claimed all ten and made ten actual provider calls, storing ten run rows. This works even without concurrency, fallbacks, specialists or transport retries.
- Existing `test_screening.py:2276` explicitly allows two images to be claimed before settled spend is checked again. It does not exercise a batch large enough to cross the actual cap.
- Consequence: the advertised spend boundary fails under burst intake and can diverge more with multiple workers or large flagged crop cascades. The worst-case formula also does not price provider fallbacks or transport retries.
- Improvement: atomically reserve farm/day units for every admitted image, count in-flight reservations, and enforce call admission at the provider seam. Distinguish logical runs from billable transport attempts.

### D5 — MEDIUM: IRR can falsely report a unique return despite three valid roots

- Location: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/simulation/finance.py:420`–440, fallback at 480–481, uniqueness inference at 522–523.
- Non-integral-period and long cash-flow series use 256 samples uniformly in transformed `x=1/(1+r)`, across roughly 0.09..100. This has large gaps where ordinary positive return roots cluster.
- Proof: `flows=[-1.08,3.27,-3.2,1.0]`, `times_years=[0,.5,1,1.5]` returns `irr=-0.555555555555556`, and reports one root. Exact zero-NPV roots also include `0.5625` and `0.23456790123456783`; residuals were 0 or under 9e-16. The first two roots lie between adjacent scan points and disappear.
- This is an acknowledged internal approximation (384–391), but public IRR docs promise to return None for genuinely multiple roots. UI/result consumers receive no uniqueness/solver-quality qualification.
- Consequence: investment comparison may present a misleading unique rate. This probe verifies the public helper; no claim that the exact synthetic series is reachable from today's goat-engine inputs.
- Improvement: prove uniqueness for conventional one-sign-change flows; return ambiguity/unknown when an approximate solver cannot prove uniqueness; use robust/adaptive isolation and prefer NPV/MIRR for ambiguous streams.

### D6 — MEDIUM: operational SMS headlines report sampled counts as totals

- Location: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/services/notifications/service.py:692`–703 and 740–758.
- Reorder rows are limited to ten and overdue dates to fifty, then `len(rows)` / `len(critical)` is presented as the true count.
- Proof: actual PostgreSQL queries over 12 low-stock items and 70 duties four days overdue produced messages saying "10 feed items" and "50 duties".
- Consequence: worsening workloads above the ceiling remain understated; dedupe payload also plateaus. Similar digest undercounts were fixed in this source, but the two alert siblings remain.
- Improvement: query exact SQL aggregate totals separately from bounded title samples; include exact total or an explicit "50+" in both messaging and stable dedupe design.

### D7 — MEDIUM: one-shot disease/restriction alerts dropped during quiet hours have no replay worker

- Location: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/services/notifications/service.py:236`–267; hook `/backend/app/services/notifications/hooks.py:23`–54; background dispatch `/backend/app/main.py:431`–477.
- Quiet-hours attempts become SKIPPED_QUIET. Such placeholders are only cleaned and delivered if the original alert is called again later. SCREENING_FLAG and MOVEMENT_RESTRICTION are emitted once after the source mutation. The scheduled loop retries digests and regenerates overdue/kidding/feed alerts; it never dispatches pending one-shot alert placeholders. Payload/message content is not persisted in the log, so there is no recoverable deferred message.
- Consequence: a condition flagged at 22:00 has no morning notification if the originating mutation is not repeated. Hook failures and an API process crash after domain commit have a similar at-most-once delivery gap.
- Improvement: transactional notification outbox storing event identity/message routing, due time, priority and delivery state. Resume deferred alerts after quiet hours; define a product policy for critical alerts and provider-accepted/ambiguous transport outcomes. Do not assume retries of a timeout guarantee paid-SMS exactly once.
- This finding is source-verified, not a real gateway delivery test. Existing comments deliberately call such alerts same-day/best-effort; that tradeoff should be explicit in the user-facing feature contract.

### D8 — MEDIUM: the one-date-per-year festival table misses the second 2039 event

- Location: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/simulation/market.py:106`–125 and iterator at 159.
- A `dict[GregorianYear,(month,day)]` cannot hold two occurrences of the same lunar holiday in one Gregorian year. The table contains January 2039, then December 2040, with no December 2039 occurrence.
- Proof: `bakrid_festival_months('2039-01',12)` returns `[1]`. Alhabib's calendar, authored from global crescent-moon sighting probabilities, projects Eid al-Adha in both January and December 2039: https://www.al-habib.info/islamic-calendar/global_pdf/global-islamic-calendar-year-2039-ce.pdf (event tables on pages 5–6). Their 2040 calendar projects December 15: https://www.al-habib.info/islamic-calendar/global_pdf/global-islamic-calendar-year-2040-ce.pdf. Exact local observed days remain future projections; the missing second month is the structural defect.
- Consequence: allowed 20-year scenarios extending through 2039 miss a festival hold/sale opportunity and its seasonal price uplift.
- Improvement: store a sequence of dated occurrences, with region, source, projection uncertainty and coverage metadata; allow multiple same-year occurrences. Add coverage tests for same-year lunar cycles and month-boundary uncertainty. The 2044 table comment also says October31/November1 are "same month", contradicting the month-resolution premise.

## Additional verified-source improvement opportunities

- Retention is bounded per SQL statement but loops until each entire farm cohort is exhausted before a commit (`services/retention.py:98`,`:169`,`:188`), so whole-farm transaction duration, WAL volume and row-lock footprint are unbounded. Farm discovery also pulls every matching farm_id, including duplicates, before Python set dedupe. Use keyset farm discovery and a row/time budget per transaction.
- Screening retention anchors only on root `created_at` (`services/retention.py:216`), without excluding active PROCESSING/unreviewed FLAGGED cases or newly created findings on old photos. Define explicit unresolved-case and evidence-retention protection before enabling it at scale. The feature is opt-in; this is policy/scaling risk, not a claim that default deployments currently delete such evidence.
- Retention increments shared summary counters before commit and does not undo them if the farm rolls back, so deleted-row telemetry can over-report failed transactions. Merge a per-farm summary only after successful commit.
- Healthcare events are immutable without a correction/void workflow, while financial transactions have audited correction support. Product improvements should include append-only clinical corrections that preserve original evidence and recompute withdrawal/schedule facts. Clinician review is needed for the eligibility and component rules; the audit does not prescribe vaccine schedules or doses.
- AI screening needs evaluation against independently labeled real farm photos, including unusable photos, detection recall, missed abnormalities, and disagreement cases. Today's transport mocks and contract checks are useful but do not measure clinical sensitivity or real provider instruction compliance.
- Simulation is deterministic and bounded with good input validation/admission controls, but most runs are still GIL-bound worker-thread tasks in the API process. Scale-intensive Monte Carlo/planner work through a durable job API and dedicated process workers with cancellation, progress, quotas and result-version provenance.
- Large domain modules concentrate many coupled decisions (`simulation/engine.py` 2260 lines, `daily_ops.py` 1999, `screening/pipeline.py`1647). Reduce decision units by extracting explicit pure transition/accounting functions and centralized invariant tests; avoid fragmentation that merely moves code without clarifying contracts.

## Strengths observed

- Extensive strict wire validation: finite values, money/quantity precision, PostgreSQL-safe text, extra-field rejection, magnitude limits and many date/cross-field rules.
- Farm-local operational dates, animal-birth/acquisition chronology, row-locked factual transitions, canonical lock orders and immutable event attribution.
- Database-bound HTTP idempotency, drift-aware cached-response validation, revision-aware saves, bounded pagination and aggregation rather than lifetime history hydration in many read paths.
- Careful image security boundary: constrained registered uploads, token/content-type/magic agreement, conditional object snapshot, decode/byte limits and stripped EXIF before external inference.
- Screening worker durable claims, stale recovery, finite attempt budget, per-image fault isolation and signal-aware shutdown/heartbeats.
- Real PostgreSQL integration tests and focused concurrency/domain regression suites. The new findings show why behavior-oriented probes are still necessary despite their breadth.

## Scoped scores (judgment, not certification)

| Category | Score /100 | Main reason |
| --- | ---: | --- |
| API/backend foundations | 86 | Strong validation/idempotency/authorization seams; large coupled code and production-scale limits remain |
| Operational domain correctness | 77 | Many lifecycle/chronology safeguards; herd-round completion can certify incomplete evidence |
| AI screening reliability | 45 | Prompt omission and unusable-photo healthy results are feature-blocking |
| Notification reliability | 67 | Good day dedupe/digest scoping; missing outbox and misleading workload counts |
| Simulation/model trust | 78 | Rich assumptions, risk model and numeric defenses; IRR uncertainty and future-calendar defect |
| Performance/scalability | 73 | Bounded reads/admission controls; ineffective screening cap, long retention transactions, same-process CPU |
| Maintainability/test quality | 78 | Rich regression coverage; tests pin several wrong business shortcuts and giant decision modules |

## Verification artifacts and limits

- `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-domain-audit-probes.py`: transport prompt probe, mathematical IRR probe, isolated PostgreSQL screening/cost/quality/count probes. Creates a UUID-named temporary database and removes it even on failure. No network provider calls.
- `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-health-round-audit-test.py`: real API/alembic fixture probe for partial full-herd-round completion; result `1 passed in 3.61s` using `GOATFARM_TEST_DB=goatfarm_test_domain_audit_20261003`. Fixture removes that throwaway DB afterward.
- `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-audit-backend-files.txt`: transparent full-source static-inspection inventory.
- Full backend test suite is coordinated by root. This contributor did not run a competing full suite or edit application code.
- Production provider behavior, real SMS deliverability, veterinary correctness/sensitivity, long-duration production load and recovery are unverified. Helper-only IRR reproduction does not prove its exact synthetic flow pattern is reachable through current scenario inputs.
