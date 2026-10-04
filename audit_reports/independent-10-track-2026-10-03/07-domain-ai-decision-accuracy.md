# Track 7 — Farm-domain, AI, and decision accuracy

Audit date: 3 October 2026 (America/Phoenix)  
Audited commit: `1ca78768ed227182b1b84663bcc97c5ea9bee41b`

## Verdict

The transactional farm core is notably defensive: lifecycle transitions are shared between operations and simulation, breeding and kidding fail closed on key chronology/eligibility conditions, purchases join quarantine work to inventory and finance, feed mixing is exact and stock-guarded, and finance keeps source-linked correction and premium histories. The focused simulation/planner suite also passed in full.

The principal decision-accuracy risks are elsewhere: the active simulation help describes an AI breeding mode that the engine explicitly does not implement; calibration evidence for one reproductive dimension silently removes the parity model for another; and the image-screening workflow can neither guarantee that every goat in a partially detected photo was screened nor support full false-negative evaluation. Health-cadence and reference text also disagree on PPR and breeding age.

Finding count: **0 Critical, 0 High, 6 Medium, 2 Low**.

## Findings

### 07-D1 — The simulation help promises unlimited AI service, but the engine gives a zero-buck herd zero service capacity

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Reproduced with the pure simulation engine; no database or provider was used.
- **Impact:** A user following the field help can model an “AI-first” herd with zero bucks and automatic sire purchase disabled, believing all does will still breed. The executed projection instead has no conceptions or births, materially changing herd, revenue, feed, and viability outputs.
- **Preconditions:** The user selects zero opening bucks, disables automatic buck purchase, and relies on the help text to represent an AI programme.
- **Current evidence:** The active field-help entries say that zero bucks gives “unlimited” service capacity and that disabling purchases still breeds the whole herd (`frontend/src/lib/simulation-field-help.ts:99-101`, `frontend/src/lib/simulation-field-help.ts:132-134`); the simulation page uses these entries for its help dialog (`frontend/src/app/(app)/simulation/page.tsx:2009-2032`, `frontend/src/app/(app)/simulation/page.tsx:2059-2061`). The engine explicitly records that AI capacity was removed (`backend/app/simulation/engine.py:147-150`) and computes service capacity only as `bucks * buck_doe_ratio` (`backend/app/simulation/engine.py:1069-1095`). The operational breeding service also rejects AI and requires a herd buck (`backend/app/services/breeding.py:491-502`).
- **Reproduction:** A 24-month, open, 20-doe synthetic run produced:

  | Opening bucks | Auto-purchase | Births | Ending head | Purchases |
  |---:|:---:|---:|---:|---:|
  | 0 | off | 0.000000 | 14.440000 | 0.000000 |
  | 1 | off | 83.106749 | 52.094358 | 0.000000 |
  | 0 | on | 83.106749 | 52.191858 | 1.102368 |

- **Recommendation:** Remove the AI claims from all localized help and require natural-service capacity, unless a real AI mode with explicit capacity, cost, conception assumptions, and provenance is implemented. Add a UI-contract test for the zero-buck case.

### 07-D2 — Calibration of conception or litter evidence erases the unrelated parity adjustment

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Source-proven and reproduced with a pure synthetic simulation.
- **Impact:** Calibration changes a reproductive assumption for which the farm supplied no evidence. In the conception-only case, literature/default litter-size parity is removed; in the litter-only case, conception parity is removed. Forecast births and herd size can therefore move because of an undocumented, unevidenced second change.
- **Preconditions:** At least five assessed breeding records but fewer than five kiddings, or at least five kiddings without sufficient assessed breedings; the user then runs the calibrated assumptions.
- **Current evidence:** The model has distinct, non-flat litter and conception parity tables (`backend/app/simulation/assumptions.py:181-204`) and the engine consumes them independently (`backend/app/simulation/engine.py:518-526`, `backend/app/simulation/engine.py:1041-1050`, `backend/app/simulation/engine.py:1094-1104`). Nevertheless, conception calibration replaces **both** tables with `[1.0]` (`backend/app/services/simulation_calibration.py:492-509`), and litter calibration independently does the same (`backend/app/services/simulation_calibration.py:555-565`). The comments justify removing parity already represented in an observed herd average, but each branch also removes parity from the metric it did not observe.
- **Reproduction:** With identical 60-month assumptions and the same flattened conception table, preserving the default litter-parity table produced 425.316318 births and 107.788221 ending head. Applying the current branch's all-flat replacement produced 460.096327 births and 119.399117 ending head—34.780009 more births (about 8.18%) solely from flattening the uncalibrated litter dimension. This is a synthetic sensitivity demonstration, not a field-valid effect estimate.
- **Recommendation:** Flatten only `conception_rate` when calibrating conception and only `litter_size` when calibrating litter; flatten both only when both evidence thresholds are met. Return an evidence row for every parity-table mutation and add one-sided-evidence regression tests.

### 07-D3 — A partial detector miss silently excludes goats from screening while the photo can still finish `HEALTHY`

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Source-proven failure path; inferred provider precondition.
- **Impact:** An abnormal goat omitted by detection receives no gate or specialist pass. If every detected crop is healthy, the aggregate photo is reported healthy, creating a false assurance that all goats pictured were screened.
- **Preconditions:** Crop detection is enabled; a photo contains multiple goats; the detector returns at least one valid box but misses another; the missed animal contains the relevant visible abnormality.
- **Current evidence:** The detection module claims that a detection miss can never leave the herd unscreened (`backend/app/services/screening/detect.py:1-11`). The fallback to a whole-photo cascade, however, runs only when the returned box list is empty (`backend/app/services/screening/pipeline.py:1627-1652`). Any non-empty list takes the crop-only branch (`backend/app/services/screening/pipeline.py:1654-1715`), and the image status is aggregated only from those crop statuses (`backend/app/services/screening/pipeline.py:1726-1736`). There is no coverage/completeness check or parallel whole-frame safety pass.
- **Recommendation:** Treat detector completeness as uncertain: add a whole-frame safety pass, a second coverage/count check, or an explicit incomplete-coverage/`UNASSESSABLE` outcome. Surface detected-count and coverage uncertainty rather than allowing “healthy” to imply complete enumeration.

### 07-D4 — Successful AI runs discard the evidence needed to reconstruct their conclusions

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Source-proven; no external provider calls were made.
- **Impact:** A vet or model reviewer cannot reconstruct why a healthy gate stopped, why a specialist replaced a gate observation, or why a cross-check disagreed. This weakens adverse-event review, confidence calibration, and provider/model comparison even though the final finding and model identity remain available.
- **Preconditions:** A later reviewer needs to inspect a successful gate/specialist/cross-check beyond its final label and confidence, especially after a disagreement or changed prompt/model.
- **Current evidence:** Provider and specialist call results carry bounded `raw_text` (`backend/app/services/screening/providers.py:113-120`, `backend/app/services/screening/providers.py:302-319`, `backend/app/services/screening/specialists.py:186-215`). `ScreeningRun.detail` is described as a bounded response summary containing observations and usage (`backend/app/models/screening.py:326-404`), but the pipeline persists only a gate observation count (`backend/app/services/screening/pipeline.py:940-963`), a specialist condition count (`backend/app/services/screening/pipeline.py:1040-1049`), and three cross-check flags (`backend/app/services/screening/pipeline.py:1107-1132`). Successful specialist output can replace the gate observations, and cross-check observations/reasoning are always lost. The run still retains provider, model, prompt version, confidence, and latency (`backend/app/models/screening.py:387-405`), which is useful but insufficient for conclusion-level audit.
- **Recommendation:** Persist a bounded, sanitized structured response or response digest for each successful stage—especially the gate observations and cross-check observations—plus usage where available. Full raw provider payload retention is not required and should be weighed against privacy/retention policy.

### 07-D5 — The AI feedback loop cannot measure healthy false negatives, while the product presents it as a measured provider comparison

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Source-proven structural limitation; downstream decision impact is inferred.
- **Impact:** The scoreboard and export can estimate disposition among emitted findings, but they cannot measure missed abnormalities, recall/sensitivity, specificity, or calibration of healthy verdicts. A provider that under-flags may appear inexpensive or low-volume without any ground truth on its misses, so these artifacts are not sufficient for provider selection or safety validation.
- **Preconditions:** Operators use the scoreboard or exported corpus to compare providers, tune the model, or infer end-to-end screening accuracy.
- **Current evidence:** A healthy gate stops the cascade and creates no reviewable finding (`backend/app/services/screening/pipeline.py:966-971`); the second-provider cross-check is reached only after a primary flag (`backend/app/services/screening/pipeline.py:973-975`, `backend/app/services/screening/pipeline.py:1099-1109`). The stats endpoint counts flagged behavior and confirmed/rejected/pending **findings** (`backend/app/api/screening.py:493-498`, `backend/app/api/screening.py:539-571`) and the export selects only `ScreeningFinding` rows (`backend/app/api/screening.py:640-680`). The UI describes “vet-confirmed precision” but renders raw confirmed/rejected counts, not that rate, and omits pending findings (`frontend/src/lib/i18n/en.ts:1331-1343`, `frontend/src/app/(app)/screening/page.tsx:276-314`). Precision among reviewed findings can be derived from those counts, but it is conditional positive evidence, not full screening accuracy.
- **Recommendation:** Label the existing metrics explicitly as conditional on emitted/reviewed findings. Add blinded sampling and review of healthy outcomes (including detector coverage), report denominators and confidence intervals, and export versioned negative/hard-negative examples before using the corpus for training or provider-quality decisions.

### 07-D6 — PPR has an annual policy, a three-year reference, and no farm-level annual task

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Source-proven; missed-reminder impact is inferred because no production farm was exercised.
- **Impact:** A farm following the task board can receive no annual PPR round, while a user following the README can reasonably wait three years. The only authoritative annual due state is a per-animal page, so a core schedule can be missed unless staff open every animal's schedule.
- **Preconditions:** Staff rely on the task board or README rather than individually opening each animal's vaccination schedule.
- **Current evidence:** Seed data defines PPR as a 12-month repeat and calls annual revaccination the department-camp schedule (`backend/app/seed.py:202-211`); a migration deliberately propagated 36-to-12 months to existing farms (`backend/alembic/versions/b5d7f9a1c3e5_backfill_species_reference_data.py:1-49`). The due calculation is exposed only by the per-animal route (`backend/app/api/health.py:1395-1418`). The farm-level task cadence enumerates FMD, ET+HS, Goat Pox, CCPP, and deworming but not PPR (`backend/app/services/cadence.py:70-111`), while the README still says PPR is three-yearly (`README.md:1334-1336`).
- **Recommendation:** Decide the intended policy with a qualified local veterinary/public-health owner, store its authority/version once, and make every surface derive from it. If annual remains the product policy, create an annual farm-level due rollup/task and update the README. This finding establishes internal inconsistency; it does not independently validate annual versus triennial vaccination.

### 07-D7 — Breeding guidance still invites 10–11-month service that the canonical rule rejects

- **Severity:** Low
- **Confidence:** High
- **Evidence status:** Source-proven.
- **Impact:** Users can move or attempt to serve a 10–11-month, weight-qualified doe based on the bucket board or README, only to be rejected by the backend. The enforcement fails closed, so this is workflow confusion rather than evidence of an under-age service being accepted.
- **Preconditions:** A doe is 10–11 months old and at least 22 kg, and staff follow the bucket text/reference rather than the enforced profile.
- **Current evidence:** The canonical profile requires 12 months (`backend/app/models/species.py:87-93`) and `is_breeding_ready_on` enforces that profile (`backend/app/models/animals.py:425-440`). Its adjacent docstring still says at least 10 months (`backend/app/models/animals.py:420-423`); fresh seed text says female kids through 10 months and “Breeding-ready (10–12 mo)” (`backend/app/seed.py:62-66`, `backend/app/seed.py:118-122`); the API returns those persisted strings and the board renders them (`backend/app/api/buckets.py:128-140`, `frontend/src/app/(app)/buckets/page.tsx:63-73`). The README also states at least 10 months (`README.md:1306-1313`).
- **Recommendation:** Make the profile value the source for generated guidance, correct the docstring/README, and use a guarded migration to update existing seeded bucket text.

### 07-D8 — A withheld mortality-report value renders as a blank cell rather than “withheld”

- **Severity:** Low
- **Confidence:** High
- **Evidence status:** Source-proven; frontend runtime was not launched.
- **Impact:** A user with `reports.view` but without `health.view` sees no value or explanation for “kids born,” while neighboring clinical figures explicitly say access was withheld. Blank is ambiguous between missing data, a load defect, and authorization.
- **Preconditions:** The caller can view reports but lacks `health.view`.
- **Current evidence:** The reports API intentionally returns `total_kids_born=None` without health access (`backend/app/api/dashboard.py:743-752`), and the schema documents null as withheld (`backend/app/schemas/dashboard.py:140-148`). The reports page renders `mortality.total_kids_born` directly (`frontend/src/app/(app)/reports/page.tsx:338-341`), unlike total deaths, stillborn, and stillborn rate, which use the `Withheld` component (`frontend/src/app/(app)/reports/page.tsx:332-350`). React renders the null value as an empty cell.
- **Recommendation:** Apply the same `healthWithheld`/`Withheld` rendering used by the sibling metrics and cover the permission combination in a page test.

## Positive controls and strengths

- **Lifecycle, breeding, lineage, and kidding:** The legal bucket graph is a shared pure source for both operations and daily simulation (`backend/app/models/lifecycle.py:1-7`, `backend/app/models/lifecycle.py:31-68`). Natural breeding checks open pregnancy, chronology, postpartum waiting, sire capacity, and close-kin relationships before writing (`backend/app/services/breeding.py:505-588`). Kidding requires a confirmed, undelivered pregnancy, bounds gestation and litter size, derives parity, and writes sire/dam lineage for live-born and neonatal-death animals (`backend/app/services/kidding.py:90-126`, `backend/app/services/kidding.py:152-165`, `backend/app/services/kidding.py:241-298`).
- **Purchases and quarantine:** Batch count/sex/weights are validated, per-head money allocation preserves the exact total, created animals enter quarantine, arrival weights and moves are attributed, the 45-day duties are generated, and the ledger expense is booked in the same workflow (`backend/app/services/purchases.py:103-128`, `backend/app/services/purchases.py:147-250`).
- **Health, movement, and finance linkage:** Recording health costs creates a source-linked expense and suspected scheduled disease places a movement restriction (`backend/app/services/health.py:350-433`). Sale/cull status changes query medicine withdrawal before allowing exit, and corrections re-run the same fence (`backend/app/api/animals.py:1176-1188`, `backend/app/api/finance.py:403-415`). These are strong cross-module fail-closed controls.
- **Feeding and inventory:** Recipe totals must equal 100, gram allocation is exact, stock rows are locked in canonical order, shortages prevent writes, and ingredient depletion and finished stock update together (`backend/app/services/feeding.py:512-602`). Feed purchases retain quantity/unit-price provenance in the financial ledger (`backend/app/services/feeding.py:605-662`).
- **Finance and insurance:** Monetary facts use `Decimal`/fixed numeric columns, source-pair and correction constraints prevent contradictory active ledger entries (`backend/app/models/finance.py:34-99`, `backend/app/models/finance.py:110-134`), and insurance premiums have an immutable per-period history with a durable uniqueness key (`backend/app/models/finance.py:267-320`).
- **Simulation and planner mechanics:** The public run revalidates the complete assumptions document before execution (`backend/app/simulation/engine.py:2172-2194`). The focused engine, financial, planner, goat-domain, calibration-curve, and cull-price tests all passed (326 tests). The findings above are contract/calibration gaps around that otherwise well-tested core, not evidence that all model numerics are generally unstable.
- **Screening safeguards:** Image hashes and normalized derivatives give byte-level provenance and duplicate-cost control (`backend/app/models/screening.py:173-190`, `backend/app/models/screening.py:229-263`). Per-farm daily call admission is serialized and each provider attempt is reserved before use (`backend/app/services/screening/budget.py:55-105`). Quality problems become `UNASSESSABLE`, findings require human confirmation/rejection, and the UI plainly states that screening is not a diagnosis (`backend/app/services/screening/pipeline.py:940-971`, `frontend/src/lib/i18n/en.ts:1309-1310`).

## Verification performed

- Confirmed the audited checkout is commit `1ca78768ed227182b1b84663bcc97c5ea9bee41b`.
- From `backend/`, ran the focused pure/model test set covering simulation engine, financials, backward planner, planner, goat domain, calibration-curve math, and cull-price calibration: **326 passed in 51.66s**.
- Ran two pure-Python simulation probes for D1 and D2 with break-even disabled; their outputs are recorded above.
- Did **not** start the app, open/migrate/write the existing goat-farm database, make an external AI-provider call, or edit application code.

## Limits and validation still needed

- No production data, live AI provider, real image corpus, field workflow, notification delivery, or production configuration was exercised. D3's partial-miss occurrence and D5's downstream model-selection effect therefore remain inferred even though their code paths are explicit.
- No veterinary SME validated vaccine schedules, drug combinations/routes, quarantine instructions, rations, water guidance, disease vocabulary, or breeding thresholds. The PPR and breeding-age findings are internal-consistency findings, not independent clinical endorsements. The hard-coded clinical task text at `backend/app/models/helpers.py:69-129` and `backend/app/services/cadence.py:117-161` especially needs a named source/version/local approval review before it is treated as prescribing guidance.
- No accountant, insurer, lender, or agricultural economist validated taxation, subsidy, price, insurance, depreciation, NPV/IRR, or market assumptions. Passing numeric tests establishes implementation consistency, not financial suitability or predictive calibration.
- Database-backed concurrency, seeded-data migration on a disposable PostgreSQL instance, browser rendering, Telugu fluency, and end-to-end reports were outside this track's safe execution. D6-D8 are based on current source traces.

## Final counts

| Severity | Count |
|---|---:|
| Critical | 0 |
| High | 0 |
| Medium | 6 |
| Low | 2 |
| **Total** | **8** |
