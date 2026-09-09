# End-to-End Testing & Lifecycle Audit — 2026-09-08

> **RESOLUTION UPDATE (same day):** all four findings from the testing pass
> have been fixed and verified — G1 skip guard (backend + tests), G2
> restricted-animals dashboard view (API + UI + tests + OpenAPI/orval), G3
> documented in the README, G4 all 15 drifted e2e specs updated to the
> worker-first labels with chromium re-run green. The sections below keep the
> original findings for the record; see §6 for the fix inventory.

**Scope:** every user-visible path of the Herdly goat-farm SaaS — auth/team,
animals, bucket lifecycle, breeding → ultrasound → kidding → weaning, health /
vet restrictions, purchases & the 45-day quarantine protocol, feeding, finance,
tasks/duties, dashboard, RBAC, plus concurrency and "stuck animal" scenarios.
Special focus, per the request: *"animals not moved from a bucket; vet visit —
someone moves animals bucket → vet → back; did we miss any logical steps?"*

**Method:** run every existing suite (backend integration, frontend unit,
browser e2e), then write and run an independent, scenario-driven **lifecycle
audit suite** (`backend/tests/test_e2e_lifecycle_audit.py`, 21 tests) that
walks each business path end-to-end through the public API, asserting business
outcomes (bucket occupancy, duty states, ledger rows), not just status codes.
Every anomaly was root-caused in the code before being classified.

---

## 1. Verdict

| Layer | Result |
|---|---|
| Backend integration suite | ✅ **4,081 passed, 4 skipped, 0 failed** (includes the 38 new audit tests, post-fix) |
| Frontend lint + typecheck + unit | ✅ **all green** (4,176 tests incl. the new restricted-animals card tests) |
| New independent lifecycle audit (22 scenarios) | ✅ **22/22 pass** |
| Scenario-gap audit (16 micro-scenarios) | ✅ **16/16 pass** |
| Browser e2e (Playwright, chromium, real stack) | ✅ **38/38 pass** after the G4 spec fixes (was 15 failed / 19 passed / 4 not run) |
| mypy --strict (app) / ruff / OpenAPI + orval freshness | ✅ clean, regenerated and in sync |
| CI (GitHub Actions, `ci.yml`) | ❌ **red on `main` since commit `8ea85ef`** — e2e job has not completed green since the label change; earlier failures (backend test, frontend timeout, docker uv-cache) were fixed by `7d4aeff` but the e2e drift was not |

**Product logic verdict:** the lifecycle state machine is complete and
watertight in every path I could construct — no orphaned states, no lost
animals, no double ledger effects. The genuine logical gaps found are
operational *surfacing* gaps, not correctness bugs (§3).

---

## 2. The "vet bucket" question — answered

The system deliberately has **no vet bucket**, and the daily-ops model states
why: *one building per bucket, each with its dedicated vet area*
(`app/simulation/daily_ops.py`). The vet comes to the animal; the animal never
travels for treatment. The implemented equivalent of "move to vet and back" is:

1. **Vet visit** = a `HealthEvent` (VACCINE/DEWORMING/TREATMENT/FOOTBATH/
   VITAMIN) + auto-generated duties routed to the VET role.
2. **Sick animal** = a **movement restriction** (`suspected_scheduled_disease`
   → the animal is frozen in its current bucket; every exit — move, breeding,
   sale, cull, quarantine release — is refused, fail-closed).
3. **"Vet to their bucket"** = `POST /api/health/restrictions/{id}/clear`
   (attributed, referenced clearance with optimistic version check) → all
   lifecycle paths reopen.

I verified this whole interplay end-to-end, including the subtle case: a kid
under a hold **freezes the entire weaning duty** (dam + kid stay in RECOVERY
until the vet clears), and after clearance the same duty completes and everyone
moves (`test_weaning_blocked_while_kid_under_movement_hold`). That is exactly
the "someone has to move the animal back" step the question asked about — it
exists, it is enforced, and it cannot be skipped around.

---

## 3. Logical gaps found (deep-researched, with evidence)

### G1 — A skipped ultrasound duty strands the doe invisibly (medium)

Quarantine duties refuse skip (would deadlock the release gate) and
RECOVERY-exit duties refuse skip (would strand doe + kids) — but the
**ULTRASOUND duty can be skipped**, and the system then goes quiet:

- the breeding record stays `PENDING` (one-open-pregnancy rule),
- re-service is refused (`409`),
- **no dashboard suggestion covers a doe idling in BREEDING**
  (`app/services/dashboard.py:160-215` has rules for FOUNDATION /
  FEMALE_KIDS / RESTING / PREGNANCY_EARLY / PREGNANCY_LATE / MALE_KIDS —
  never BREEDING as a source),
- nothing regenerates a follow-up pregnancy-check duty.

Recovery is possible (record the scan later — the form never required the
duty — or sell/cull the doe), but nothing *prompts* anyone to do it.
Proven end-to-end by
`test_skipped_ultrasound_strands_open_pregnancy_until_resolved`.
**Recommendation:** refuse skip for a due ULTRASOUND duty whose breeding
record is still PENDING (mirroring the two existing skip guards), or add a
BREEDING-source suggestion ("pregnancy check overdue") to the dashboard.

### G2 — No farm-wide view of animals under an active movement restriction (medium)

Movement restrictions correctly fail every exit closed, but visibility is
per-animal only: the history endpoint is `GET /api/health/restrictions/{id}`
(you must already know the animal), the dashboard *excludes* held animals
from suggestions (`dashboard.py:202`) without listing them anywhere, and no
duty is generated to follow up. An un-cleared hold therefore silently blocks
move/breed/sale forever. The animal profile shows a restriction banner with
the clearance action (`frontend/src/app/(app)/animals/[id]/page.tsx:1085`) —
but you have to find the animal first.
**Recommendation:** a "restricted animals" card on the dashboard (or a
`?restricted=true` filter on the animals list) listing held animals with
their hold age.

### G3 — A buck promoted into BREEDING has no legal bucket exit (low, design observation)

`LEGAL_BUCKET_TRANSITIONS` gives BREEDING exactly two exits:
`→PREGNANCY_EARLY` (doe-only, via ultrasound) and `→RESTING` (female-only).
A buck that enters BREEDING can never leave through the graph — verified
exhaustively in `test_buck_in_breeding_bucket_has_no_manual_exit`. This is
*consistent* with the domain model (the breeding buck lives in the pen;
"buck rotation" in the planning engine = cull the sire battery and re-staff,
`app/simulation/engine.py:1271`), and the owner-only history override exists
as the escape hatch. But: no UI copy explains it, seasonal buck separation is
impossible, and no suggestion ever mentions a BREEDING buck. Worth either a
documented product decision or a `BREEDING → FOUNDATION` manual edge for
males.

### G4 — main's browser e2e suite is red (high, test debt — not a product bug)

15 of 38 chromium e2e tests fail on HEAD. Root cause for every one examined:
commit `8ea85ef` ("worker-first UX") changed user-visible labels
(`FOUNDATION` → "Foundation", new dashboard hero caption "active animals"),
but the older specs still assert the old strings. The e2e CI job has not
completed since (cancelled behind earlier failures), so the drift was never
caught. Details and the fix list in §4. **The product itself behaves
correctly in every failing scenario** — I verified each underlying flow
through the API in the audit suite.

---

## 4. e2e failure classification (all 15, deterministic on an idle machine)

Every failure root-caused from Playwright traces/error-contexts. **None is a
product bug** — the underlying flows all work (proven through the API by the
audit suite). Five drift patterns:

| # | Pattern | Tests | Evidence |
|---|---|---|---|
| A | **Enum-code → human-label drift**: specs wait for `option 'FOUNDATION'/'QUARANTINE'/'FEED'/'CLEANING'` but pickers now render "Foundation"/"Quarantine"/"Feed"/"Cleaning" | animals, breeding-flow, kidding-flow, feeding-finance ×2, tasks-guards, tasks-verify ×2 (8 tests) | `getByRole('option', {name:'FOUNDATION', exact:true})` timeout; listbox snapshot shows `option "Foundation"` |
| B | **Strict-mode duplicate match** from new responsive/hero UI | register (`'Active animals'` now also matches hero caption `active animals`, `dashboard/page.tsx:644`), team (`'Add worker'` button rendered twice — desktop + mobile variants) (2 tests) | strict-mode violation errors listing both elements |
| C | **UI copy/structure drift** | farm-switch (empty-state copy `'No animals match these filters.'` changed), health-flow (`'+ Add event'` button renamed) (2 tests) | locator-not-found after UI restructure |
| D | **Spec predates a hardening contract** | api-contract-domain:661 — POSTs `/api/finance/new` without the now-mandatory `Idempotency-Key` (422) (1 test) | server's own clear error message |
| E | **Dev-only environment difference** | api-contract-auth-team:229 — expects `/readyz` `content-type: application/json`; correct from the backend (verified by curl) but the **Next dev-server rewrite proxy** returns `text/plain;charset=UTF-8`. CI's e2e job uses the prod standalone server, where this passes | backend curl shows `application/json` (1 test) |
| — | Simulation page restructure | simulation:202 — `details/summary` 'Risk' accordion structure changed (1 test) | locator timeout on `details` element |

Fix for A–D: update spec locators to the current labels with exact matchers
and add the `Idempotency-Key` header — no assertion weakening needed. E is
dev-only; optionally pin the expectation to the prod server or assert on
`content-type` containing `json` only when `NODE_ENV=production`. The 4
"did not run" tests are downstream of the same cascade.

---

## 5. What was exercised and found correct (the "everything else")

**Lifecycle state machine (exhaustive).** For one eligible animal in every
reachable bucket, all 10 targets attempted: exactly the legal manual edges
succeed, everything else 409 — including sex-mapping (males never enter
female buckets), live-pregnancy guard on DELIVERY entry, and same-bucket
no-ops. RECOVERY is closed except through its own weaning/postpartum duties —
and those duties refuse skip precisely so no one is ever stranded there.

**Full doe cycle.** breed → FOUNDATION→BREEDING → scan+ → PREGNANCY_EARLY →
2 vaccine duties closed through the health form → delivery duty → DELIVERY →
kidding (twins) → RECOVERY → weaning day-60 → kids split MALE/FEMALE_KIDS,
dam → RESTING → re-service next cycle. Includes the meat-sale window guard
(<8-month male kid sale refused) and same-day-after-scan re-service
chronology refusal.

**Purchases & quarantine.** Batch → 8 protocol duties on days 1–45 → release
refused while prerequisites outstanding or any animal held → skip refused
while the batch has live animals → day-45 release moves everyone to
FOUNDATION (exactly one audit-trail move per animal even under concurrent
double-completion) → released doe integrates (weight → breed). Purchase
expense lands in the ledger with batch provenance.

**Pregnancy failure paths.** Negative scan → FAILED, doe re-serviceable;
abortion from PREGNANCY_LATE → RESTING with every litter duty swept; doe sold
while CONFIRMED → record auto-closes ABORTED (PENDING → UNASSESSED) with no
orphan duties.

**Kidding edge cases.** All-stillborn litter → no kid animals, postpartum
duty +14d → RESTING; kids dying post-partum → weaning auto-skipped, dam's
postpartum duty replanned to last-death+14; dam dies before weaning →
surviving kid early-weaned out of RECOVERY automatically (no stranded
orphans).

**Vet/health.** Restrictions freeze move/breed/sell/cull; VET role can place
and clear, MOVER can neither; withdrawal windows block sale until expired;
quarantine health duties close only through the health form.

**Genetics fences (real lineage, farm-grown litters).** Sire×daughter and
full-sibling services are refused with an inbreeding error; half-siblings
(shared sire, different dams) remain permitted practice. The AI/AI_SEXED
methods are refused for goats. A buck's 21st open service is refused (1:20
buck:doe policy).

**Kidding micro-scenarios.** Mixed litter (live + stillborn): the stillborn
never becomes an animal, the survivor weans normally. One-of-two kid death
post-partum: weaning still completes for the survivor. Gestation window:
a 99-day delivery is refused, a 100-day delivery lands on the accepted
boundary. A DIED-at-entry kid keeps a **DEAD-status** animal row for lineage
traceability and routes the dam to the 14-day postpartum path — weaning is
never created. A blank-tag live kid gets an auto-generated tag. A doe SOLD
from RECOVERY early-weans her surviving kid.

**Timing / eligibility micro-guards.** Auto duties refuse completion before
their due date; breeding is refused while the doe sits in DELIVERY; breeding
eligibility (age/weight) is evaluated **on the historical breeding date**, so
impossible backdated histories cannot be recorded; tags are unique per farm
but reusable across farms.

**Health & money micro-scenarios.** Withdrawal windows longer than the
730-day cap are refused; a bucket-scope vaccine event splits its cost exactly
across the target animals and books one aggregated MEDICINE ledger row;
`next_due_date` drives the schedule row to UPCOMING, then OVERDUE once past;
a cull with a sale price books the ANIMAL_SALE income row.

**Feeding.** Plan lines track bucket occupancy (heads per bucket); stock →
mix → dispense round-trip; over-dispense refused.

**Finance.** Purchase ₹21,000 expense, treatment ₹500 VET expense, sale
₹7,500 income all reconcile by category/source; corrections void-and-replace
with the audit trail intact; idempotency keys enforced on the money paths.

**Concurrency & idempotency.** Double task completion → single bucket-move
effect (exactly one BucketMove row); conflicting parallel moves → one winner,
animal never in an illegal state.

**RBAC.** MOVER moves, FEEDER/VET cannot (403); preset role permissions
verified through the API as provisioned.

---

## 6. Artifacts & how to re-run

**Fixes applied after this audit (all verified by the full suites):**

- **G1** — `app/api/tasks.py`: the skip route now refuses a due ULTRASOUND
  duty whose breeding record is still PENDING while the doe is active,
  mirroring the quarantine and RECOVERY skip gates. Tests:
  `test_open_pregnancy_check_refuses_skip_until_resolved` (audit suite) and
  the updated `test_skip_form_linked_duty_allowed` (form-linked duties
  remain skippable — pinned via a VACCINE duty).
- **G2** — `GET /api/dashboard` now returns `restricted_animals` +
  `restricted_animals_total` (identity behind `animals.view`, clinical
  reason behind `health.view`, withheld as `null` total otherwise), and the
  dashboard renders a "Movement restrictions" warning card. OpenAPI export
  and the orval client regenerated. Tests: backend
  `test_dashboard_lists_restricted_animals_farm_wide`; frontend
  `DashboardPage — movement restrictions` describe-block.
- **G3** — README "The bucket system" now documents that sires are terminal
  residents of BREEDING (rotation = cull + restock), that sick animals never
  change buckets (movement restriction + clearance), and points at the new
  dashboard card.
- **G4** — all 15 e2e drifts fixed in the specs (label map helper in
  `e2e/helpers.ts`, per-spec copy/locator updates, idempotency key for the
  finance contract test, plain-text `/healthz` handled, simulation
  festival-months ordering); no product code changed, no assertions weakened.

- New audit suites: `backend/tests/test_e2e_lifecycle_audit.py` (22 tests)
  and `backend/tests/test_e2e_scenario_gaps.py` (16 tests) — both ruff-clean
  and part of the standard suite run.
  ```bash
  cd backend && ./.venv/bin/python -m pytest tests/test_e2e_lifecycle_audit.py tests/test_e2e_scenario_gaps.py -q
  ```
- Browser e2e:
  ```bash
  cd frontend && GOATFARM_DATABASE_URL=postgresql+asyncpg://localhost:5432/goatfarm_e2e \
    GOATFARM_AUTH_RATE_LIMIT_ENABLED=false pnpm exec playwright test --project=chromium
  ```
- Traces of the 15 e2e failures: `frontend/test-results/*/trace.zip`
  (`pnpm exec playwright show-trace <path>`).

*Report by the independent E2E testing pass, 2026-09-08.*
