# Exhaustive backend semantic-review extension — 2026-10-03

All 85 assigned authored Python source files, totaling 38,390 lines, now have full semantic source reading and AST inspection. `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-audit-backend-files.txt` has 85 MANUAL_FULL rows and no remaining sampled/AST-only rows. This covers APIs, services, simulation, worker, and schemas after auth/team/shared reassignment to the security reviewer. Relevant tests were examined for selected contracts and regressions; the root audit records full-suite execution and mechanical test-file inventory; it does not claim complete manual test-source review. Application code was not edited.

This extends findings D1–D8 in `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/05-backend-domain.md` with fresh verified findings from the formerly sampled modules.

## D9 — MEDIUM: insurance renewal can deadlock an animal sale/death

- References: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/api/finance.py:933` acquires Policy FOR UPDATE first; `backend/app/services/finance.py:364` reads its animal without a row lock; `:201` inserts an animal-linked renewal Task. Animal exit acquires the animal first (`backend/app/api/animals.py:1141`, `:130`) then locks policies via `:1509` / `services/finance.py:482`.
- Verified PostgreSQL reproduction: renewal session holds Policy; exit session holds Animal and waits for Policy; renewal creates its linked Task and waits for the FK's Animal KEY SHARE lock. PostgreSQL detects 40P01, aborts one request, and rolls back its domain transition. `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-insurance-lock-probe.py` returned `['exit DBAPIError: 40P01', 'renewal committed']`.
- Impact: ordinary concurrent accountant/manager actions yield a 500 and failed exit instead of a reliable serializable business result. The route does not map/retry this deadlock.
- Improve: acquire the linked animal first, then policy in a common canonical order, reload the policy/animal under those locks and recheck eligibility. Test simultaneous renewal against sale/death, including creation of the future task.

## D10 — MEDIUM: price calibration ignores the authoritative sale weight

- References: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/services/simulation_calibration.py:305`–317 omits `Animal.sale_weight_kg`; `:817`–827 uses only the latest historical WeightRecord before sale, without a freshness gate.
- Verified PostgreSQL reproduction: five animals sold at 35 kg for ₹24,500 each (₹700/kg), with an old 20 kg routine weighing six months earlier, produced calibrated meat price ₹1,225/kg. Actual exit weight exists but is ignored. Probe `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-sale-weight-calibration-probe.py`.
- Impact: a 75% optimistic meat-price assumption silently flows into projections. Animals with explicit sale weight but no history cannot contribute at all.
- Improve: use sale_weight_kg first; restrict fallback weights by freshness and annotate estimated/stale evidence. Share the valuation convention with realized-sale finance reporting.

## D11 — MEDIUM: screening review notes accept PostgreSQL-invalid NUL text

- References: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/schemas/screening.py:91` uses plain str, unlike other PostgresText fields; `backend/app/api/screening.py:335` persists it directly.
- Verified reproduction: Pydantic accepts JSON review_note containing `\u0000`; actual route function against isolated PostgreSQL raises DBAPIError SQLSTATE22021. `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-screening-review-probe.py`.
- Impact: invalid input causes an uncaught server error and failed review instead of 422; inconsistent validation despite a strong common text boundary elsewhere.
- Improve: use the existing PostgresText type and test at the HTTP boundary. Lone-surrogate rejection was also tested and already rejected by Pydantic; this finding claims only NUL.

## D12 — MEDIUM: status-only review concurrency allows lost audit updates

- References: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/api/screening.py:300`–305 promises every racing review gets409; `:329` guards only status while `:333`–335 overwrites reviewer/time/note.
- Verified reproduction: start REJECTED, submit reviewer A with expected REJECTED and noteA, then stale reviewer B with the same expected REJECTED and noteB. Both requests succeed and B erases A's audit. Same-status edits do not change the concurrency token; CONFIRMED→REJECTED→CONFIRMED ABA is the same structural hole. Probe `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-screening-review-probe.py` verifies the same-status case.
- Impact: review provenance/training-corpus feedback can be overwritten without the documented conflict.
- Improve: revision or immutable review-event identity as the expected concurrency token; append review history and expose its author/date/version.

## D13 — MEDIUM: owner overview double-counts voided corrected ledger rows

- References: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/api/owner.py:166` does not filter voided_at; feed-cost benchmark `:313`–315 also misses voided_at and EXPENSE type. Single-farm finance and calibration already filter active ledger rows.
- Verified PostgreSQL reproduction: void a ₹1,000 FEED expense and add its ₹600 replacement. Owner month_expense reports ₹1,600 instead of ₹600; feed-cost ratio includes both rows. `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-owner-aggregate-probe.py`.
- Impact: multi-farm totals disagree with the accounting register and can make an improving farm look more expensive.
- Improve: shared active-ledger aggregation, explicit EXPENSE-only feed spend and reconciliation tests after correction. Insurance-premium omission is explicitly documented as ledger-only in schemas/owner.py, so it is a separate product-consistency opportunity rather than a contract violation.

## D14 — MEDIUM: owner weight-gain benchmarks turn actual loss into positive gain

- References: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/api/owner.py:275`–278 takes minimum/maximum weight independently from minimum/maximum date, then `:294`–298 computes the range over elapsed days plus one.
- Verified PostgreSQL reproduction: one animal goes 20 kg → 15 kg over 10 days. Owner avg_daily_gain_kg reports +0.454545 instead of −0.5; feed cost per kg gain remains positive. `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-owner-aggregate-probe.py`.
- Impact: the benchmark cannot represent negative growth and exaggerates nonmonotonic growth. This conceals a directly actionable welfare/performance signal.
- Improve: actual earliest/latest observations ordered(date,id), positive elapsed time, explicit negative/zero-gain handling, adequate sampling and clear feed-cost denominator semantics.

## D15 — HIGH: cadence farm failure isolation skips the rest of its page

- References: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/services/cadence.py:585` loads ORM Farm objects; `:598` keeps passing those objects after `:609` rolls back and expires them.
- Verified PostgreSQL reproduction: three farms with active animals, inject failure on the first farm, then invoke the real ensure_cadence_tasks for the others. Both later farms fail MissingGreenlet on expired attributes and receive no tasks. Function still returns(3,last_id), so caller advances the page. `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-cadence-isolation-probe.py`.
- Impact: one bad farm suppresses materialization for higher-id farms in the same batch every sweep, contradicting the explicit failure-isolation guarantee. Commit's expire_on_commit=False does not prevent rollback expiration.
- Improve: load each farm fresh by snapshotted ID after rollback, or isolate each farm in a separate session/transaction. Add a real-session expiration regression rather than only mocking failures.

## D16 — HIGH: backward-planner instructions divide litter size twice

- References: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/simulation/backward_planner.py:268` includes litter size in per_birth_of_sex; `:281` divides required kids by that; `:283` divides by litter size again.
- Verified pure-function reproduction: target 100 male growers, litter 2, female share 0.5, no stillbirth/mortality, conception1 yields instructions to breed 50 does, have 50 kiddings and 100 kids (both sexes). To sell 100 males under these assumptions requires 100 does and 200 total kids. Probe `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-simulation-extension-probes.py`.
- Impact: actionable resource/breeding requirements understate necessary does by the litter factor, contradicting forward feasibility/stage planning. More prolific assumed herds become increasingly under-provisioned.
- Related verified edge: `_effective_conception` at`:239` returns1.0 for unlimited retries before checking zero conception. With conception_rate 0 / cap 0, an impossible herd gets a successful analytic conception assumption rather than a clear rejection.
- Improve: separate live births of both sexes, births of the target sex, kidding does and service-ready does with units; divide by litter once. Check zero conception first and disclose finite lead-time retry assumptions. Add conservation identity tests for sex/litter/stillbirth/survival.

## D17 — MEDIUM: post-weaner calibration annualizes a three-month phase rate

- References: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/services/simulation_calibration.py:761`–781 applies an annual conversion to adult, grower AND kid_post_weaning. `backend/app/simulation/assumptions.py:299` defines kid_post_weaning per phase, consumed over 3 months at`engine.py:722`–724.
- Verified PostgreSQL reproduction:10animals exposed to the post-weaner class,1death: calibration reports0.336939 whole-phase loss; the same measured hazard converted to3months gives0.097622. Evidence calls it annual and confidence medium. Probe `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-mortality-calibration-probe.py`.
- Impact: mortality is inflated about3.45x in a field designed for the class-phase probability, materially depressing forecast survival and driving excessive stock purchases.
- Improve: keep explicit time units in calibrated fields; use3month exposure conversion for this class and annual conversion only for grower/adult. The immediately preceding pre-weaning calculation already has the correct whole-phase principle.

## D18 — MEDIUM: opening male young stock is valued below its modeled weight

- References: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/simulation/engine.py:1604`–1608 combines male/female kids and weaners at the female weight and uses weight_at_age for male growers. Event purchases use male_weight_at_age at`:758`,`:766`,`:782`; terminal stock also applies the premium.
- Verified pure-function reproduction: default10male kids result in opening stock_cost₹22,200; the model's own male age/weight/price gives₹24,420 (10% premium). Probe `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-simulation-extension-probes.py`.
- Impact: starting capital/debt/equity are understated and early-sale returns get an artificial gain simply because the same animal is priced by different curves on entry and exit.
- Improve: separate male/female per-class opening valuations and share them with event/default/terminal valuation.

## D19 — MEDIUM: daily-ops simulation sends later feed to the animal's old building

- References: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/simulation/daily_ops.py:1540` creates one morning snapshot;`:843` reuses it;`:1552`–1553 performs afternoon/night delivery after bucket moves/births/sales/deaths.
- Verified pure-function reproduction: Q1 enters quarantine with44days already there;09:00day45release moves her toFOUNDATION.13:30and19:30tasks still deliver0.22kg/0.44kg to emptyQUARANTINE, with no delivery at the occupiedFOUNDATION building. Probe `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-simulation-extension-probes.py`.
- Impact: the promised operational location/timing ledger is wrong on transition days; exits similarly receive later rations. The result notes disclose weight proxies but do not disclose this stale snapshot.
- Improve: hold the daily mixing manifest separately from per-shift destination/recipe requirements, recompute each shift after actual moves, record leftovers/transfers, and reconcile physical delivered feed to occupancy.

## D20 — HIGH: named NLM subsidy has wrong eligibility, caps and cash timing

- References: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/simulation/engine.py:151`–156 calls published subsidy limits eligible capital;`:1633`–1643 halves that ceiling, lacks minimum qualifying unit/global cap, and immediately subtracts all subsidy from equity; cashflows at`:1906` have no later subsidy receipts. User report`:184`–189 in`simulation/explain.py` calls it back-ended, while equity explanation`:212`–214 tells the user this is money not needed at the start.
- Official verification: the current DAHD scheme page, updatedOct1,2026, links [January2025 comprehensive NLM guidelines](https://dahd.gov.in/sites/default/files/2026-04/NLMGuidelinesJan2025.pdf). Printedpp13–14 specify minimum100females+5males, maximum capital SUBSIDY₹10lakh for that unit rising to₹50lakh for500females+25males, and two installments with final release after verified completion. General fund-flow sections also require prior loan/expenditure milestones. The tables describe subsidy maxima, rather than eligible-cost maxima. PDF downloaded/read from that exact primary source, linked above; the policy PDF is not duplicated in this evidence bundle.
- Verified pure-function probes with no debt haircut:3F+1M receives₹20,000 despite being below minimum;100F+5M receives₹5.25lakh;500F+25M₹26.25lakh;2000F+100M₹105lakh exceeds the national₹50lakh cap. All reduce month0cash outflow in full. Probe `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-simulation-extension-probes.py`.
- Impact: ineligible small farms get promised subsidy, eligible mid-size farms get wrongly reduced ceilings and large farms get over-limit grants; required up-front/bridge funds and subsidy receipt timing are absent. Exact award depends on eligible item costs and approval; the audit does not assert a particular applicant's approved award.
- Improve: versioned scheme policy with eligibility bands, cap on subsidy rather than half a head-cost proxy, eligible-cost exclusions, approved award and staged receipt dates. Distinguish subsidy-free baseline, application estimate and approved funds, and include bridge financing in liquidity/NPV. Keep effective date/source in report provenance.

## D21 — MEDIUM: withheld animal totals are recoverable from dashboard/status dictionaries

- References: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/api/dashboard.py:240`–243 deliberately withholds active herd size withoutanimals.view;`:416`–426 always computes the same count;`:498`–505 only removes clinical statuses based onhealth.view. The reports sibling repeats the gap at`:764`–771.
- Verified PostgreSQL function probe: dashboard.view-only gives total_activeNone but status_totals{'ACTIVE':1}; reports.view-only gives total_activeNone but status_counts{'ACTIVE':1}. `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-dashboard-permission-probe.py`. Cleaner-style permissions support this real authorization shape; no employee identities/photos were accessed.
- Impact: exact inventory size is disclosed through a sibling field despite the explicit permission contract. This is modest field-level confidentiality, not a cross-farm access finding.
- Improve: apply animals.view to inventory-status aggregates, health.view to clinical-status aggregates, and expose unavailable fields consistently. Validate role-specific response properties, not only whether the route returns200/403.

## Revised scoped scores

| Category | Score/100 | Reason after full semantic reading |
|---|---:|---|
| API/backend foundations |83| Strong common validation/idempotency; remaining concurrency-token, NUL and permission-field gaps |
| Operational domain correctness |69| Herd-round coverage, cadence fault isolation, owner benchmarks and policy-lock inconsistency |
| AI screening reliability |45| Stage prompts omitted; unusable photos return green healthy; burst spend cap inaccurate |
| Notification reliability |67| Good scoped digest/dedupe; quiet one-shot gaps and undercounted operational workload |
| Simulation/model trust |62| Useful deterministic/risk machinery, but wrong backward units, calibration units, named subsidy policy and inconsistent valuations |
| Performance/scalability |72| Many bounded reads/admission guards; cross-farm failure amplification, long retention transactions and same-process CPU |
| Maintainability/test quality |76| Substantial meaningful regression estate; large decision modules and several tests preserve incorrect business contracts |

These are judgment scores for this reviewer’s scope, not overall product scores or clinical/financial certification. The parent integrates UI/UX, security, data, operations and live verification.

## Probe inventory and verification limits

- Original probes and real health API test remain in`/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/05-backend-domain.md`.
- New isolated PostgreSQL scripts:`/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-insurance-lock-probe.py`,`/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-sale-weight-calibration-probe.py`,`/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-screening-review-probe.py`,`/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-owner-aggregate-probe.py`,`/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-cadence-isolation-probe.py`,`/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-mortality-calibration-probe.py`,`/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-dashboard-permission-probe.py`. Each creates a UUID-named throwaway database, uses complete model metadata, and force-drops it in finally. No shared fixture/live database is touched.
- `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-simulation-extension-probes.py` preserves pure backward-chain, opening-value, stale-feed-destination and NLM calculations. No real provider or payment/message sends.
- Clinical accuracy, actual subsidy approval, real SMS/provider behavior, sustained load, failover and production recovery remain unverified. Green mocked/schema/domain suites cannot supply those forms of assurance.
