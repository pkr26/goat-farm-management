# Livestock Domain Logic Audit (2026-10-01)

Auditor 02 — BUSINESS-LOGIC CORRECTNESS of the LIVESTOCK domains (animals, breeding,
kidding, health, screening). Independent line-by-line read of every in-scope file;
models/schemas cross-referenced for verification. No tests or servers were run; all
findings are verified by tracing callers/callees against the model layer.

## Executive summary

**Verdict: solid.** The five livestock domains implement an unusually disciplined
lifecycle state machine: date math is single-sourced from `GOAT_PROFILE`, age/weight
eligibility has verified-consistent SQL and Python twins (including month-end clamping
and leap-year boundaries), write paths take row locks in one documented canonical order
(animals by id → breeding → tasks) and re-check state after the lock, every
money-booking mutation is idempotent with a durable key, and kidding/breeding/health
cross-domain write-throughs were each traced to their counterpart and found coherent
(parity derived server-side, heat-cycle derived from failed streaks, open pregnancies
resolved on herd exit with UNASSESSED vs ABORTED semantics, orphan kids early-weaned,
batch duties swept when the last animal leaves).

No Critical and no High findings. Two Medium conditional-logic gaps (one in the
kidding↔lifecycle interaction, one in the screening cascade's review-queue fidelity),
four Low severity/consistency items, and several observations.

Counts: **Critical 0 · High 0 · Medium 2 · Low 4 · Info 5** (+ positives below).

## Findings

### [Medium] Kidding is impossible for a doe history-overridden into BREEDING while pregnant
Location: `backend/app/models/lifecycle.py:34-63`, `backend/app/api/animals.py:1000-1024`,
`backend/app/services/kidding.py:290-301`, `backend/app/services/animals.py:92-97` — confirmed.

Evidence (`api/animals.py:1000-1010`, the override guard):
```python
if (
    payload.history_override
    and animal.sex == "F"
    and payload.to_bucket
    not in {
        Bucket.BREEDING.value,
        Bucket.PREGNANCY_EARLY.value,
        Bucket.PREGNANCY_LATE.value,
        Bucket.DELIVERY.value,
    }
    and (computed.is_currently_pregnant or await doe_has_open_breeding(db, farm.id, animal.id))
):
```
Evidence (`models/lifecycle.py` — the only edges out of BREEDING):
```python
(Bucket.BREEDING.value, Bucket.PREGNANCY_EARLY.value): frozenset({"ultrasound"}),
# A doe history-overridden from PREGNANCY_* back to BREEDING still carries
# her confirmed pregnancy; recording its loss must remain possible.
(Bucket.BREEDING.value, Bucket.RESTING.value): frozenset({"manual", "abortion"}),
```
There is no `(BREEDING, RECOVERY)` edge. `record_kidding` moves the doe with
`context="kidding"` (`services/kidding.py:290-301`), so `require_bucket_transition`
raises "Illegal lifecycle transition BREEDING → RECOVERY; use the required breeding,
health, kidding or quarantine workflow" — the error names the exact workflow that is
being refused.

Impact: an owner who history-corrects a pregnant doe from PREGNANCY_* back into
BREEDING (a state the override guard deliberately permits, and which
`lifecycle.py:53-55` explicitly anticipates — but only for the *loss* path) creates a
pregnancy whose kidding cannot be recorded through the primary kidding-due flow at
EKD: every attempt 409s. The only exits are a second override (BREEDING→DELIVERY,
then kidding) or recording an abortion — the latter persisting a **false** loss
record that also corrupts conception statistics (`CONCEIVED_OUTCOMES` still counts
ABORTED as conceived, but the loss fact itself is fabricated).

Fix: add `(BREEDING, RECOVERY): frozenset({"kidding"})` to
`LEGAL_BUCKET_TRANSITIONS` (the kidding service already supplies the authoritative
facts that gate it), or extend the override guard at `api/animals.py:1000` to refuse
parking a pregnant doe in BREEDING (keep PREGNANCY_*/DELIVERY as override targets).

### [Medium] Screening: gate observations for regions whose specialist call failed are silently dropped from the vet queue
Location: `backend/app/services/screening/pipeline.py:929-995` — confirmed.

Evidence:
```python
    for kind, region in kinds:
        if serving is None:
            continue
        try:
            specialist: SpecialistCallResult = await run_specialist(serving, jpeg, kind)
        except ProviderError as exc:
            ...
            continue
        ...
    if specialist_conditions:
        for run_id, region, condition in specialist_conditions:
            db.add(_add_finding(image, run_id, ...))
    else:
        # No specialist output survived: the gate's own observations remain
        # the findings so the review queue is never silently empty.
        for observation in observations or []:
```
The gate-observation fallback fires only when **zero** specialist conditions survive.
If one specialist kind succeeds and another region's specialist call fails
(ProviderError → `continue`), the failed region's gate observation (label, region,
note) is never written as a finding — and it never will be: the crop/photo aggregates
to FLAGGED, which is terminal for retries (the claim query's retry arm for FLAGGED
requires a crop in ERROR, `pipeline.py:520-527`). The observation text survives only
in the operator-side `ScreeningRun.raw_text`, never in the tenant-facing queue.

Impact: a visible abnormality (e.g. the gate saw an eye lesion but the eye specialist
timed out while the skin specialist answered) produces a FLAGGED photo with no
reviewable finding for that region — the vet sees "flagged" with nothing to confirm
or reject for the missed region. Edge-case loss of review-queue data on partial
provider failure.

Fix: per-kind fallback — for each kind whose specialist errored, emit the gate
observation(s) that mapped to that kind as findings (attributed to the gate run),
instead of an all-or-nothing `specialist_conditions` check.

### [Low] Provider-call spend undercounted: fallback-chain attempts record at most one run row
Location: `backend/app/services/screening/pipeline.py:790-846` (detect chain),
`backend/app/services/screening/rotation.py:96-117` (gate chain),
`backend/app/services/screening/pipeline.py:464-494` (budget) — confirmed.

Evidence (`_record_run` docstring vs. detect chain behaviour):
```python
    # One provider call = one run row, whatever its outcome: the spend
    # metrics and the daily per-farm budget both count it here (ITEM 6).
```
but in `_detect_with_fallback` each failed provider is appended to `failures` and the
loop continues; only the eventual success (or one terminal ERROR row for the primary)
is recorded. `gate_with_fallback` likewise returns only the serving result. The
per-farm daily budget counts `ScreeningRun` rows, so with N providers a fully-failing
detect/gate chain spends N calls while accounting for 1; the worst-case reservation
(`1 + max_crops * 7`) does not budget the fallback overhead either. Under provider
outages the realized call spend can exceed `screening_daily_call_budget_per_farm`
(the comment's claim "Errored calls count too (the provider was paid)" does not hold
for the intermediate failures).

Fix: record an ERROR run row for each failed provider attempt in both chains, or add
the fallback overhead to `_worst_case_calls_per_image`.

### [Low] SQL latest-weight twin diverges from the model for animals with no DOB
Location: `backend/app/services/breeding.py:91-96` vs `backend/app/models/animals.py:371-379` — confirmed.

Evidence (SQL):
```python
    effective_dob = func.coalesce(Animal.date_of_birth, Animal.estimated_dob)
    birth_weight_available = case(
        (effective_dob <= reference_date, Animal.birth_weight),
        else_=None,
    )
    return func.coalesce(latest_recorded, birth_weight_available)
```
Evidence (model):
```python
        dob = self.effective_dob
        return (
            self.birth_weight
            if self.birth_weight is not None and (dob is None or dob <= reference_date)
            else None
        )
```
When `effective_dob` is NULL, SQL `NULL <= date` is NULL → the case yields NULL, so
`_latest_weight_as_of` returns NULL while `latest_weight_kg_on` returns the birth
weight. Immaterial for eligibility today (both candidate paths additionally require a
non-null DOB for the age gate), but `breeding_weights_as_of` feeds
`doe_latest_weight_kg`/`buck` facts into `bucket_transition_error`, where the twins
silently disagree — a consistency trap if the DOB requirement is ever relaxed.

Fix: `case((effective_dob.is_(None), Animal.birth_weight), (effective_dob <= reference_date, Animal.birth_weight), else_=None)`.

### [Low] Kidding gestation-window violation returns 409 while the sibling litter-size violation returns 422
Location: `backend/app/api/kidding.py:389-396`, `backend/app/services/kidding.py:97-114` — confirmed.

Evidence:
```python
        except LitterSizeError as exc:
            # A litter above the species cap is input-shape validation.
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except ValueError as exc:
            # Every other ValueError here is a raced lifecycle state → conflict.
            raise lifecycle_conflict(detail=str(exc)) from None
```
An absurd kidding date (`gestation < 100` or `> 200` days) is exactly as much an
input-shape error as an over-cap litter, yet it surfaces as 409 "lifecycle conflict".
Misleading error class for the client; no data impact.

Fix: raise `LitterSizeError` (or a sibling `InputShapeError`) for the gestation-window
branch in `record_kidding`.

### [Low] Breeding chronology violations (service predates participant birth/purchase) surface as 409 instead of 422
Location: `backend/app/api/breeding.py:327-331`, `backend/app/services/breeding.py:525-531` — confirmed.

Evidence:
```python
        except ValueError as exc:
            # Lifecycle/biology conflict (unresolved breeding, VWP, inbreeding
            # fence, species protocol) — raced or forged request.
            await db.rollback()
            raise lifecycle_conflict(detail=str(exc)) from None
```
The blanket catch folds "breeding date cannot predate its recorded birth/purchase
date" (a deterministic chronology fact, 422 everywhere else — e.g.
`api/animals.py:391-392`, `api/kidding.py:290-291`) into the 409 lifecycle-conflict
bucket together with genuine raced states. Wrong status code class; no data impact.

Fix: split chronology `ValueError`s (birth/purchase predate) from state conflicts in
`create_breeding_record`, or re-check them in the router before `mutate()` like
`require_farm_not_future`.

### [Info] Confirmed screening findings do not write through into the health domain
Location: `backend/app/api/screening.py:349-359` — observation, by design.

A vet's CONFIRMED verdict only emits a best-effort notification
(`emit_alert(... "SCREENING_FLAG" ...)`). No `HealthEvent`, no `suspected_disease`
flag, no movement restriction is created; the health write remains a manual step.
This is a defensible separation (the model's guess is not a diagnosis), but it means
a confirmed scheduled-disease suspect (e.g. FMD_SUSPECT) relies entirely on the owner
acting on the push alert — worth confirming this is the intended product contract.

### [Info] `_not_future` schema bound uses the deployment-default timezone, not the farm's
Location: `backend/app/schemas/common.py:55-61` — observation, no bug.

`PastOrTodayDate` compares against `today()` (Asia/Kolkata default) with +1 day
headroom; routers re-check with `require_farm_not_future(..., farm, ...)`. Because
the maximum timezone offset difference from IST is ~8.5h < the 24h headroom, a farm's
local "today" always passes the schema bound, and the farm-relative check is the
stricter one everywhere. Correct, but only by this arithmetic argument — worth a
comment so the headroom is not "simplified" away.

### [Info] `is_breeding_ready` vs `is_breeding_eligible` bucket sets differ deliberately
Location: `backend/app/models/animals.py:414-463`, `backend/app/api/_shared.py:192-203`,
`backend/app/services/breeding.py:99-152` — observation.

`is_breeding_ready_on` excludes the BREEDING bucket; `is_breeding_eligible_on` and both
SQL candidate twins include it (re-service after a failed cycle). The API's computed
`is_breeding_ready` mirrors the *ready* variant while the pickers use the *eligible*
variant. Consistent as designed, but the near-identical names invite future drift —
the parity surface is spread across four implementations (2 model properties, 2 SQL
builders, 1 serializer overlay).

### [Info] Detect failure falls back to whole-photo screening — safety net verified
Location: `backend/app/services/screening/pipeline.py:1505-1527` — positive-by-design
observation recorded because a detection miss can never leave a herd un-screened;
verified `_detect_with_fallback` returns `None` only after every provider failed and
the caller then runs the full cascade on the whole photo.

### [Info] Screening budget window correctly uses per-farm local midnight
Location: `backend/app/services/screening/pipeline.py:396-415` — observation. The
day-start computation converts naive-UTC "now" through each farm's stored timezone
(corrupt values falling back to the documented default), avoiding the classic
"budget resets at UTC midnight mid-local-day" error. Verified the naive/aware
handling is consistent (`now.replace(tzinfo=dt.UTC).astimezone(tz)... .replace(tzinfo=None)`).

## Coverage manifest

Every in-scope file, read completely line-by-line unless noted:

| File | Lines | Status |
|---|---|---|
| backend/app/api/animals.py | 1552 | full |
| backend/app/api/breeding.py | 456 | full |
| backend/app/api/kidding.py | 435 | full |
| backend/app/api/health.py | 1085 | full |
| backend/app/api/screening.py | 997 | full |
| backend/app/services/animals.py | 412 | full |
| backend/app/services/breeding.py | 1130 | full |
| backend/app/services/kidding.py | 640 | full |
| backend/app/services/health.py | 747 | full |
| backend/app/services/_common.py | 176 | full |
| backend/app/services/__init__.py | 278 | full |
| backend/app/services/screening/__init__.py | 135 | full |
| backend/app/services/screening/detect.py | 119 | full |
| backend/app/services/screening/gate.py | 169 | full |
| backend/app/services/screening/images.py | 179 | full |
| backend/app/services/screening/pipeline.py | 1606 | full (2 chunks) |
| backend/app/services/screening/providers.py | 307 | full |
| backend/app/services/screening/rotation.py | 126 | full |
| backend/app/services/screening/s3.py | 384 | full |
| backend/app/services/screening/specialists.py | 216 | full |

Coverage: **100%** of in-scope files, fully read.

Cross-referenced for verification (findings about these belong to other auditors
except where the bug is in the interaction, which is reported above):
`models/animals.py`, `models/breeding.py`, `models/health.py`, `models/screening.py`,
`models/lifecycle.py`, `models/species.py`, `models/helpers.py`, `models/constants.py`,
`models/enums.py` (vocabularies), `utils.py` (date/money helpers),
`api/_shared.py` (animal_computed_facts / breeding_out / visible_to),
`schemas/common.py`, `schemas/animals.py`, `schemas/breeding.py`, `schemas/kidding.py`,
`schemas/health.py`, `schemas/screening.py`, `services/chronology.py` (full),
`services/tasks.py` (complete_task lifecycle section, lines 340-669 — cross-domain
verification of weaning/postpartum/delivery move contexts),
`services/idempotency.py` (commit/rollback contract only, grep-level).

## Positive observations

1. **Age/eligibility math is provably consistent between SQL and Python.**
   `add_months(reference, -min_age)` cutoffs in `_candidate_filters` were checked
   against `age_months_on` month-difference-with-day-adjustment at month-end and
   leap-year boundaries (2024-02-29 → 2025-02-28 etc.): identical verdicts at every
   boundary probed. Weight gates, pregnancy exclusion and bucket sets mirror the
   Python predicates exactly.

2. **One canonical lock order, documented and re-checked after the lock.**
   create-breading locks both parents by id then works on scalars; kidding locks
   dam+sire then the breeding row; ultrasound/abort lock doe→breeding;
   change_status locks animal→breedings→tasks; the replan dam lock uses
   `nowait=True` and maps SQLSTATE 55P03 to a retryable 409 instead of deadlocking
   against dam retirement. Task side effects consistently use locked fetch +
   non-PENDING re-check so a concurrently committed skip/completion is never
   clobbered.

3. **Cross-domain write-throughs are complete where they must be.** Kidding
   completes/cancels exactly the linked breeding's duty set (KIDDING_DUE done,
   leftovers skipped, post-kidding duties added *after* the sweep);
   herd-exit resolves PENDING→UNASSESSED vs CONFIRMED→ABORTED with biologically
   honest semantics and a `ck_`-consistent administrative-close exception;
   derived values clients cannot forge (`parity`, `heat_cycle_number`,
   `expected_kidding_date`, kid tags) are all server-computed.

4. **Money and idempotency discipline.** Every ledger-booking mutation (animal
   create with price, status change sale/cull, health events, screening
   batch/upload) runs under `execute_idempotent` with a durable claim; the animal
   create path keeps the claim alive across a tag-collision savepoint retry;
   `allocate_money` guarantees exact-paise non-negative shares; sale price
   derivation (weight × ₹/kg) re-checks the ledger ceiling explicitly.

5. **The kidding tag-namespace deadlock analysis is real and fixed.** The upfront
   `pg_advisory_xact_lock(farm.id)` before the kid loop removes the documented
   SHARE→EXCLUSIVE upgrade deadlock between two same-farm kiddings; the single-key
   keyspace does not collide with the namespaced two-key locks used elsewhere
   (screening 4716, planner, simulation, team) — verified against every
   `pg_advisory*` call site in the app.

6. **Screening intake is hardened coherently:** presigned POST policy binds
   content-type + token + size with the multipart-envelope allowance matched to the
   worker's download cap; downloads are ETag/version-conditional with streamed
   ceilings; decode caps (25 MP / 10k edge) with decompression-bomb promotion;
   tenant-facing errors are fixed reason codes only.

7. **Pagination and filters are honest.** Every list endpoint returns a `total`
   computed from the same predicate as its page (verified for animals, breeding
   history/candidates, kidding due/history, health events/animals/batches,
   screening images/batches); LIKE patterns are escaped; offsets/limits bounded;
   `MAX_PAGE_OFFSET` caps deep scans.

8. **Date/time hygiene.** All business dates are farm-timezone-resolved
   (`today(farm.timezone)`) with a documented ban on the deployment-default
   fallback in the move/bulk-target gates; stored instants are naive UTC with
   `business_date` conversions; the screening budget window uses farm-local
   midnight; `booster_interval` rounds half-weeks with ROUND_HALF_UP.
