# Independent audit: domain, finance, simulation, screening, database and concurrency

Audited commit: `7245368fa91333fb39387df789fa4ef9a58dbea3`. Date: 2026-10-04, America/Phoenix. Assigned tracks: 3, 4, 5, 6, 11, 12, 13. Application and existing test sources were not changed. Findings were derived from current source and independently constructed probes; old audit reports and archived conclusions were not used.

**Result: nine confirmed issues: eight P2 (medium), one P3 (low).** Every issue below has an executed reproducer. Two additional controls passed for database tenant integrity/catalog state and concurrent sale serialization. No new migration or concurrency defect was confirmed in the reviewed paths.

All new probes and their output are in [evidence/domain](evidence/domain). The probes deliberately assert the observed defective behavior, so a passing probe confirms reproduction, not remediation. No fixes have been implemented.

## Findings

### D26-01 — P2: A sale can record an animal as purchased before it was born

- **Tracks:** 3 goat domain; 11 database integrity.
- **Current source:** `backend/app/api/animals.py:1157` performs chronology checks before `backend/app/api/animals.py:1220` writes the newly supplied estimated DOB. `backend/app/models/animals.py:100` onward has source/status consistency checks but no purchase-versus-effective-DOB constraint.
- **Prerequisites:** An active male with no DOB, with an earlier recorded purchase date, outside quarantine; a user authorized to record its sale. The existing-herd import API can create this legitimate unknown-age starting record.
- **Reproduction:** Create an unknown-age male bought 730 days ago. Sell it today while supplying `estimated_dob` 365 days ago. `test_sale_dob_can_follow_purchase` uses only the public animal APIs.
- **Expected:** Reject the conflicting new date before committing; the estimate must be no later than acquisition and previously recorded facts.
- **Actual:** HTTP 200 persisted `purchase_date=2024-10-05`, `estimated_dob=2025-10-05`, `status=SOLD`, and a ₹1,000 sale. The goat is recorded as acquired a year before birth. See `DOB_CHRONOLOGY`, [final log](evidence/domain/probes-final.log), line 9.
- **Impact:** Permanent contradictory provenance reaches animal histories and age-based calibration. The normal animal-edit API cannot repair DOB after the terminal transition.
- **Recommended fix:** Validate the prospective estimated DOB against purchase and prior event dates before assignment. Add a database backstop for the same-row birth/acquisition invariant after auditing legacy data.
- **Confidence / verification:** High; API reproduction and persisted response confirmed. Open, not fixed.

### D26-02 — P2: Ledger truncation systematically underestimates recurring costs

- **Tracks:** 5 simulation/calibration; 4 finance.
- **Current source:** `backend/app/services/simulation_calibration.py:1003` limits all ledger rows, `:1027` truncates to 20,000, and `:1042` sums expenses only from that slice. The denominator comes from the full-window first expense at `:1015`; miscellaneous cost is assigned at `:1179`. Labour and veterinary cost use the same truncated totals.
- **Prerequisites:** More than 20,000 active ledger rows in the requested lookback. Recent income rows also consume the quota.
- **Reproduction:** Insert an older ₹60,000 OTHER expense 180 days ago, 19,999 newer OTHER income rows, and a newer ₹1,000 OTHER expense. The probe uses the actual production cap; it does not monkeypatch the limit.
- **Expected:** The six-month expense average is ₹61,000 / 6 = ₹10,166.67 per month. SQL aggregation can calculate this without loading an unbounded row set.
- **Actual:** Calibration returns ₹166.67 per month, counting only the latest ₹1,000. It is 98.36% below the complete recorded average. A truncation warning is present, but the biased value is still returned as a calibrated assumption. See `COST_TRUNCATION`, [final log](evidence/domain/probes-final.log), line 16. That log also shows the independent confidence-count defect D26-09.
- **Impact:** Large or income-heavy ledgers receive understated operating costs, changing forecast cash, viability, optimization and risk results.
- **Recommended fix:** Aggregate complete-window sums/counts by expense category and structured feed purchase class in SQL; reserve row caps for detail samples. If complete totals cannot be obtained, avoid presenting a truncated numerator as a full-history monthly cost.
- **Confidence / verification:** High; real 20,001-row PostgreSQL fixture and public calibration endpoint confirmed. Open, not fixed.

### D26-03 — P2: A valid flagged cascade can end with no reviewable finding

- **Track:** 6 AI photo screening.
- **Current source:** `backend/app/services/screening/gate.py:82` permits an empty observation list; `backend/app/services/screening/pipeline.py:1149` falls back to a general specialist, while `:1257` falls back to the same empty observations if the specialist returns no conditions. The cascade returns FLAGGED at `:1327`.
- **Prerequisites:** The gate returns `flagged=true`, `quality_problem=false`, `confidence=0.9`, `observations=[]`, followed by `conditions=[]` from the general specialist. Both responses satisfy the current parsers.
- **Reproduction:** Run the real worker cycle with in-memory storage, a generated JPEG and a synthetic provider emitting these responses (`test_flagged_without_observations_has_no_reviewable_finding`).
- **Expected:** Preserve a generic reviewable gate concern, or classify the incomplete result for retry/manual assessment. A flagged photo should have a usable review action.
- **Actual:** The image is terminal `FLAGGED`, the cycle increments its flagged count, and the database has zero `ScreeningFinding` rows. Review endpoints operate on findings, so this flag cannot be confirmed or rejected. See `EMPTY_FLAG`, [final log](evidence/domain/probes-final.log), line 21.
- **Impact:** The alert and review records disagree; the screening queue contains a flag with no auditable review path.
- **Recommended fix:** Enforce a flagged-result reviewability invariant, with a neutral generic finding when the gate supplies no structured observation, or retain an explicitly unresolved/retryable status.
- **Confidence / verification:** High for software behavior. Synthetic contract validation only; no claim about actual provider frequency or clinical accuracy. Open, not fixed.

### D26-04 — P2: New western-timezone farms cannot record feeding on their first local day

- **Track:** 4 finance/inventory/feeding.
- **Current source:** `backend/app/api/feeding.py:153` compares a farm-local date with `farm.created_at.date()` in UTC. The source comment at `:155` acknowledges the offset, but the preceding future-date check also prevents the suggested next-date workaround.
- **Prerequisites:** A farm created after UTC midnight but before local midnight in a western timezone. The probe uses America/Phoenix, a supported timezone, with available dry-feed stock.
- **Reproduction:** Farm creation is `2026-10-04T00:30:00` UTC, corresponding to October 3 in Phoenix. With the deterministic farm-local clock at October 3, submit a normal October 3 dispense.
- **Expected:** Accept feeding on the farm's creation date in its business timezone.
- **Actual:** HTTP 422: `Dispensing date cannot be before the farm was created.` See `DISPENSE_TIMEZONE`, [final log](evidence/domain/probes-final.log), line 28. The clock is deliberately controlled in this test; this is not a claim that the test farm was created at the current wall-clock instant.
- **Impact:** Valid first-day feeding cannot be booked and inventory cannot be debited through its operational workflow until the calendar catches up. Later backfill of that first local day remains rejected.
- **Recommended fix:** Convert the UTC timestamp using the existing `business_date(farm.created_at, farm.timezone)` helper before comparing dates.
- **Confidence / verification:** High; public API reproduction with controlled dates and real persisted farm/stock. Open, not fixed.

### D26-05 — P2: A malformed specialist response can silently erase one observed region

- **Track:** 6 AI photo screening.
- **Current source:** `backend/app/services/screening/specialists.py:176` silently drops malformed conditions and returns a successful empty response. `backend/app/services/screening/pipeline.py:1182` preserves gate observations only on a raised provider error. At `:1226`, any successful condition from another specialist suppresses the global gate-observation fallback.
- **Prerequisites:** The gate identifies more than one region. One specialist returns a valid finding; another returns malformed condition data. This is distinct from D26-03: the gate has explicit observations, but invalid refinement destroys part of their review coverage.
- **Reproduction:** Gate reports mouth lesion and severe eye injury. Skin specialist returns valid ORF; eye specialist returns EYE_TRAUMA with `confidence="high"`, which violates the numeric contract.
- **Expected:** Treat the malformed eye response as an error or preserve the eye gate observation for human review.
- **Actual:** The parser drops the eye condition without signaling failure. Only `(mouth, ORF)` reaches `ScreeningFinding`; the eye observation disappears from the reviewable findings while the image becomes terminal FLAGGED. See `LOST_REGION`, [final log](evidence/domain/probes-final.log), line 33.
- **Impact:** Reviewers can miss a recorded concern because an invalid specialist answer is treated like a successful negative refinement. The raw gate evidence remains in run detail, but it no longer participates in the normal finding review workflow.
- **Recommended fix:** Distinguish a valid empty negative assessment from an invalid/nonempty response whose conditions were all rejected. Surface the latter through the existing provider-error fallback, preserving per-region gate findings.
- **Confidence / verification:** High for software evidence propagation. Generated image and synthetic responses only; no clinical sensitivity/specificity measurement. Open, not fixed.

### D26-06 — P2: Deaths after day-60 weaning vanish from the model's first-three-month mortality

- **Tracks:** 5 simulation/calibration; 3 goat domain integration.
- **Current source:** `backend/app/services/kidding.py:465` deliberately leaves the ALIVE birth outcome unchanged after operational weaning. `backend/app/services/simulation_calibration.py:698` counts only DIED birth entries for the model's full three-month young-kid phase. Its animal-based next phase starts at three months at `:734`. `backend/app/simulation/engine.py:35` documents the model's three-month class versus operational day-60 weaning.
- **Prerequisites:** Kids weaned before three months, followed by a recorded death before their three-month birthday; at least ten kid records trigger calibration.
- **Reproduction:** Seed ten live births, with legitimate day-60 RECOVERY-to-FEMALE_KIDS moves. Record one child's day-70 death through the public status API. Calibrate after all births are six months old. Historical reproductive fixtures use valid constrained ORM rows; the death and calibration are actual API operations.
- **Expected:** Preserve the correct immutable live-birth outcome, but derive the model's three-month phase mortality from the linked animals' actual death dates. The observed first-three-month loss is 1 / 10 = 10%.
- **Actual:** The death is accepted; the birth entry correctly remains ALIVE; calibration replaces the preset 15% rate with **0%**, marked medium confidence with sample size 10. The death also lies before the next animal-exposure class. See `MISSED_KID_DEATH`, [final log](evidence/domain/probes-final.log), line 42.
- **Impact:** Normal weaning followed by mortality makes the farm look safer than its records show, increasing modeled survival and future sale stock.
- **Recommended fix:** Reconcile mortality by actual age/death facts across the linked Animal records and birth entries, avoiding double counting. Align the calibration window with the model class; do not mutate historical birth outcomes to compensate.
- **Confidence / verification:** High; PostgreSQL history fixture plus API death and calibration confirmed. This is an integration defect, not a claim that the documented monthly-resolution approximation itself is invalid. Open, not fixed.

### D26-07 — P2: Disposed breeding stock retains book value and creates tax on a sale at cost

- **Tracks:** 5 simulation financial accounting; 4 finance.
- **Current source:** `backend/app/simulation/engine.py:1677` depreciates every purchase vintage until useful life/horizon without reducing it for sales, culls or deaths. `:1751` calculates taxable profit using those incomplete depreciation charges. At `:1699` and `:1721`, stale residuals are presented as offsetting terminal livestock/breeding-stock values.
- **Prerequisites:** A purchased breeding animal exits before the end of its configured useful life. A positive configured income-tax rate affects cash/NPV; accounting and terminal-component problems remain at zero tax.
- **Reproduction:** Empty opening herd; month 1 purchase one doe for ₹100,000 and sell it in that same month for ₹100,000. Twelve-month horizon, sixty-month useful life, 30% configured tax, no facilities, financing, overhead, family-labour cash or selling costs. The submitted model validates.
- **Expected:** Zero gain on a same-month sale at cost; no continuing asset book value for the disposed animal. Any full-month depreciation convention must be balanced by derecognizing the remaining carrying amount at disposal.
- **Actual:** Closing herd is zero, but total depreciation is only ₹20,000, modeled tax is **₹24,000**, and terminal components include approximately +₹72,000 breeding stock and −₹72,000 livestock. The terminal components cancel in total cash, so this is not a double-counted terminal payout; the tax and accounting are still wrong. See `DISPOSAL_TAX`, [final log](evidence/domain/probes-final.log), line 43.
- **Impact:** Overstated profits/tax and distorted DSCR/NPV for early sales, culls and deaths; misleading residual asset components even with an empty herd.
- **Recommended fix:** Track surviving carrying amounts by stock vintage, derecognize allocated book value at disposition/death, stop its subsequent depreciation, and separately recognize disposal gains/losses.
- **Confidence / verification:** High; pure deterministic engine with schema-validated assumptions and a zero-economic-gain control. The expected result is internal accounting consistency, not jurisdiction-specific tax advice. Open, not fixed.

### D26-08 — P2: Seasonal normalization changes the observed price level without adjusting the base

- **Track:** 5 simulation/calibration.
- **Current source:** `backend/app/services/simulation_calibration.py:933` sets the base price to the overall median. `:951` builds month/base ratios and `:957` normalizes their mean to one, leaving that base unchanged. `backend/app/simulation/market.py:45` multiplies the normalized ratio by the unchanged base.
- **Prerequisites:** At least twelve priced, weighed sales covering four months; the mean of the raw month ratios differs from one.
- **Reproduction:** Three sales each in January, February, March and April 2026, at ₹100, ₹100, ₹100 and ₹400/kg respectively. These are non-festival months in the embedded calendar. All values are within the accepted ratio clamp.
- **Expected:** Normalizing a seasonal curve should retain the fitted price levels, with any normalization scale transferred to the base price. For this fixture, the imputed twelve-month mean is ₹125 and ratios `[0.8, 0.8, 0.8, 3.2, ...]` then reproduce the observations.
- **Actual:** Base remains ₹100. Ratios become `[0.8, 0.8, 0.8, 3.2, ...]`, so the model implies **₹80, ₹80, ₹80, ₹320/kg**, uniformly 20% below the observed monthly levels before growth. See `SEASONAL_LEVEL_DRIFT`, [final log](evidence/domain/probes-final.log), line 50.
- **Impact:** Calibration changes revenue levels as an artifact of expressing seasonality; the error can point either direction depending on the sample's monthly distribution.
- **Recommended fix:** Compute base and normalized curve together; carry the removed mean into the base and update both evidence entries consistently. Preserve any intended clamp policy explicitly.
- **Confidence / verification:** High; public calibration endpoint plus direct reconstruction of its monthly prices. Open, not fixed.

### D26-09 — P3: Income rows falsely increase confidence in expense estimates

- **Track:** 5 simulation/calibration evidence.
- **Current source:** `backend/app/services/simulation_calibration.py:1040` correctly excludes income from expense totals, but sample counts at `:1125`, `:1143`, `:1166` and `:1182` filter only category. `_confidence` at `:63` promotes a count of 30 to high.
- **Prerequisites:** Income and expense rows share a permitted category, especially OTHER; only a small number of actual expense observations exist.
- **Reproduction:** One ₹1,000 OTHER expense and thirty ₹100 OTHER income rows, well below the history cap. Call calibration (`test_income_rows_inflate_expense_calibration_confidence`).
- **Expected:** Expense evidence sample size 1, low confidence under the current thresholds.
- **Actual:** `costs.misc_overhead_per_month` has sample size **31** and **high** confidence. See `INCOME_INFLATES_COST_CONFIDENCE`, [confidence log](evidence/domain/probe-confidence.log). D26-02 independently demonstrates high confidence from one included expense plus 19,999 incomes.
- **Impact:** The reliability metadata overstates the actual expense evidence and can suppress the appropriate low-confidence cue. The monetary estimate itself is unaffected in this isolated below-cap example.
- **Recommended fix:** Derive sample counts from the same expense-filtered set as the numerator, preferably the same aggregate query.
- **Confidence / verification:** High; standalone below-cap database/API reproduction. Open, not fixed.

## Coverage and evidence

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

## Reproduction command and outcomes

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

## Supplemental independent numerical controls

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
