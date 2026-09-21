# Deep Line-by-Line Independent Audit — 2026-09-20

## Method and coverage

Every production source file was read line-by-line by 47 independent subagents, each verifying
function-level intent against implementation, cross-checking callers/callees, and (where useful)
reproducing suspected bugs empirically with the project's own venv (SQLAlchemy 2.0.51, Postgres,
pydantic 2.13.4) or by executing the simulation engine in-memory.

| Area | Files | Lines | Coverage |
|---|---|---|---|
| Backend `app/` (all modules, services, models, schemas, api, worker, seed, config) | 116 | ~49,600 | 100% |
| Alembic migrations + env.py + alembic.ini | 78 | ~10,500 | 100% |
| Frontend production source (pages, components, libs, i18n) | 116 | ~42,200 | 100% |
| Infra (Dockerfiles, compose ×2, nginx templates, entrypoint, CI ×5, gate script, env examples, pyproject) | 19 | ~3,000 | 100% |
| Cross-cutting sweeps | tenant isolation (478 query sites), OpenAPI contract (111 routes), backend tests (96 files, 14 read fully), frontend tests (261 files, 18 read fully) | — | systematic |

Severity scale: P0 = security breach / data loss in normal operation. P1 = functional break or
deterministic failure on valid input. P2 = correctness/robustness defect with real but bounded
impact. P3 = minor/consistency/hygiene.

**No P0 findings.** Tenant isolation, auth/token machinery, and ledger money integrity all held
under adversarial line-by-line review (details in "Verified correct" below).

---

## P1 findings (11)

### P1-1. Screening worker: read-after-rollback of an expired ORM object kills the whole cycle
- **Location:** `backend/app/services/screening/pipeline.py:486-488` (also `:477`, `:496`)
- **Function:** `run_screening_cycle` per-image `except Exception` boundary.
- **Intent:** one failed image → mark ERROR, commit, continue with the rest of the batch.
- **Reality:** after `await db.rollback()` (line 482) every instance in the identity map is
  expired (`expire_on_commit=False` only protects commits). Reading `image.screening_attempts`
  at :488 triggers a lazy refresh → `MissingGreenlet` under AsyncSession — the handler itself
  crashes before the ERROR write is committed. **Reproduced live** on the project venv. Second
  trigger path: a commit failure at :499 → rollback at :507 → the *next* loop iteration reads
  `image.id`/:488 → same crash; the "keep screening" comment at :501-505 is false.
- **Impact:** any DB-level per-image failure (statement timeout, transient connection loss,
  IntegrityError mid-cascade) strands every claimed image of the cycle as PROCESSING for the
  30-min stale horizon, burns attempt budgets, increments `consecutive_cycle_failures`, and at 3
  exits the worker (Docker restart loop under deterministic failure). Untested path.
- **Fix:** capture `attempts = image.screening_attempts` (and `image.id`) before the rollback, or
  re-read via `await db.refresh(image)` after it.

### P1-2. Screening: malformed-but-valid-JSON provider answers escape the fallback chains
- **Location:** `backend/app/services/screening/detect.py:93` (`parse_detection_response`),
  `backend/app/services/screening/specialists.py:167` (`parse_specialist_response`); escape
  points `pipeline.py:587` (`except (ProviderError, DetectionParseError)`) and `:713`.
- **Intent:** a bad answer from provider 1 falls through to provider 2 (that is exactly what
  happens for gate answers via `providers.py:217-222`).
- **Reality:** `raw.get("goats", [])[:MAX]` / `raw.get("conditions", [])[:MAX]` raise uncaught
  `KeyError`/`TypeError` when the value is `null`, an object, or a number. **Reproduced**:
  `parse_specialist_response('{"conditions": null}', …)` → `TypeError`. The gate parser handles
  this shape safely; these two don't. The exception is neither `ProviderError` nor
  `DetectionParseError`, so remaining providers are never tried and the generic per-image
  handler detonates (→ P1-1).
- **Impact:** one provider/gateway bug returning `{"conditions": null}` (a highly plausible LLM
  way of saying "nothing found") becomes a poison row: cascade re-runs + re-billing every 30 min
  until the attempt cap; audit rows rolled back; misclassified error state.
- **Fix:** coerce defensively (`items = raw.get(k) or []; if not isinstance(items, list): raise
  ParseError`) in both parsers; mirror the OpenAI adapter's wider except tuple in
  `providers.py:123-128` (Anthropic adapter has the same asymmetry).

### P1-3. Screening: attempt budget creates permanent zombie PROCESSING rows
- **Location:** `backend/app/services/screening/pipeline.py:329` (claim predicate), `:407`
  (attempts consumed+committed at claim time), terminal marker only at `:486-497`.
- **Intent:** "rows that exhaust the budget are never re-claimed, so a deterministic failure
  terminates."
- **Reality:** attempts are charged *before* processing. A row whose processing is repeatedly
  interrupted without reaching the boundary (P1-1 crash, SIGTERM mid-cycle, deploy restart) sits
  at `attempts == 5` **and** PROCESSING: excluded from all reclaim branches, never swept (sweep
  only touches PENDING+token), terminal marker never written. Stuck forever, invisible to
  operators. P1-1 accelerates this (each crash strands up to `max_images_per_cycle - 1` rows).
- **Fix:** allow one final stale-reclaim at `attempts >= MAX` that forces terminal ERROR, or add
  a PROCESSING-at-budget sweep like `_expire_abandoned_uploads`.

### P1-4. Daily-ops simulation: stale `milestone_floor` suppresses every milestone of a doe's second pregnancy
- **Location:** `backend/app/simulation/daily_ops.py:519, 608-610` (set once at init), gates at
  `:994, :1011, :1026, :1045-1046`; missing reset next to `:1186-1191`.
- **Intent:** a starter arriving mid-pregnancy must not retro-fire passed milestones.
- **Reality:** `_breeding` resets per-pregnancy flags on re-service but never `milestone_floor`,
  so a DELIVERY/PREGNANCY_LATE starter carries her start gestation day into every later
  pregnancy. **Executed repro:** second pregnancy never moves to PREGNANCY_LATE, never enters
  DELIVERY, receives **zero** pre-kidding vaccine tasks in a 365-day run; doe is underfed
  (MAINTENANCE ration instead of late-gestation) and kidded out of the wrong building.
- **Fix:** `doe.milestone_floor = -1` in the service assignment (line ~1185).

### P1-5. Simulation: sensitivity/optimization runs crash with 500 on schema-valid scenarios
- **Location:** `backend/app/simulation/assumptions.py:1017-1050`
  (`_event_age_within_class_chain`); consumers `montecarlo.py:621-622` (sale_age −2 case),
  `optimization.py:171-176, 276` (sale_age ± radius); unhandled in `api/simulation.py:189-202`.
- **Reality:** the cross-validator pins male-grower event ages to `(6, sale_age − 1)` at
  validation time, but sensitivity/optimization mutate `sale_age_months` and re-validate — a
  valid scenario (grower purchase at age 8 with sale_age 9) fails `run_sensitivity` with
  `ValidationError` → HTTP 500. **Reproduced.** Planner-adjacent scenarios sit exactly at the
  boundary (planner computes windows at `sale_age − 1`).
- **Fix:** clamp the sale-age variant below `max(event.age_months + 1 …)` in `run_case` and the
  optimizer grid (or skip-and-report infeasible candidates).

### P1-6. Migration `bd201c1cdc1b` deletes dairy `feed_recipes` before their lines — deterministic FK violation on deployed databases
- **Location:** `backend/alembic/versions/bd201c1cdc1b_goat_only_simplification.py:103` (wipe
  list :34-54 omits `feed_recipe_lines`).
- **Reality:** dairy-era seed inserted recipes **with lines** for `farm_type='BUFFALO_DAIRY'` on
  every boot (git `c25894d`); the FK `feed_recipe_lines_recipe_id_fkey` has no cascade. Fresh
  CI databases have zero dairy rows, so tests never see it; every deployed dairy-era database
  aborts `alembic upgrade head` mid-chain, blocking all 25 later revisions (tenant FKs, jsonb,
  screening, insurance). The same cleanup exists in `b3d7f1a5c9e2`'s *downgrade* (:189-197) —
  the upgrade forgot it.
- **Fix:** delete lines before recipes in this migration (already-applied DBs need a manual
  pre-check).

### P1-7. Frontend: DOB-less male sale can never be recorded from the UI
- **Location:** `frontend/src/app/(app)/animals/[id]/page.tsx:615-715` (statusSchema),
  `:768-841` (payload); backend requirement at `backend/app/api/animals.py:1177-1188`.
- **Reality:** backend 422s a male SOLD with no `effective_dob` unless `estimated_dob` is sent
  ("Send estimated_dob with the sale"). The generated `StatusChangeIn` has the field; the dialog
  has no input for it anywhere on the detail page. Such animals are reachable (create allows
  PURCHASED without DOB; DOB not editable afterwards). The only UI workaround misrecords the
  exit as CULLED.
- **Fix:** optional `estimated_dob` date input on the SOLD branch when `sex === "M"` and no DOB
  on file.

### P1-8. Frontend: deep-linked herd-level health round duty is silently unlinked
- **Location:** `frontend/src/app/(app)/health/page.tsx:787-809` (`changeScope` →
  `clearLinkedTaskPrefill`), backend intent at `backend/app/api/_shared.py:266-277`.
- **Reality:** backend generates `/health/new?task_id=N` for herd-level vaccination/deworming
  rounds expecting the operator to pick the scope. Deep link opens in default *animal* scope
  (guaranteed 422 — no client mirror of the herd-round rule), and switching the scope radio
  **silently drops the duty link** (`setValue("task_id", NONE)` unconditionally, no warning).
  The event then records unlinked with a success toast while the duty stays PENDING forever
  (its only close path is the form). Aggravating backend quirk: herd rounds can only close via
  *bucket* scope (preview 422s batch scope for herd duties).
- **Fix:** keep the link when the task imposes no target; add an inline mirror of the
  bucket/batch-scope rule for herd duties.

### P1-9. Frontend: breeding dialog offers AI / AI_SEXED methods the backend always rejects
- **Location:** `frontend/src/app/(app)/breeding/page.tsx:134, 356-382`; backend raises
  unconditionally at `backend/app/services/breeding.py:473-481` ("AI and sexed-semen services
  are not part of the goat protocol"), mapped to 409.
- **Reality:** every AI submission fails after the operator filled the form (including the
  semen-sire field). Frontend tests mock the POST as succeeding, so the suite never sees the
  mismatch.
- **Fix:** hide/disable the AI radios until the backend supports them.

### P1-10. Frontend: stale invalid TOTP draft dead-ends the login form
- **Location:** `frontend/src/app/login/page.tsx:51-56, 257-277, 300-303`.
- **Reality:** React-Hook-Form retains the `totp` value when the user presses "Back" to the
  password step. If any invalid code draft remains (1-5 digits or cleared-to-`""`), the next
  submit fails zod validation silently — `onSubmit` never runs and `errors.totp` only renders
  inside the `mfaToken !== null` branch. Sign-in becomes a no-op with zero feedback until page
  reload. Existing test asserts "backs out" but never resubmits.
- **Fix:** `setValue("totp", undefined)` / `unregister("totp")` on Back, or render the error
  outside the step conditional.

### P1-11. Frontend mutation-testing gate exists as config but is enforced nowhere
- **Location:** `frontend/stryker.*.config.mjs` (5 files, thresholds `break: 75`); no CI
  workflow, script, or doc invokes the shards — `test:mutation` still runs the monolithic config.
  Only local, gitignored, 2.5-week-stale reports exist. Measured: app1 93.5%, app2 92.6%,
  components 97.8%, **lib 77.1% (below its own `low: 80`)**, ~1,250 surviving mutants total.
  Additionally the lib shard's glob (`src/lib/**/*.ts`) never mutates `auth-context.tsx` or
  `i18n/index.tsx` — the session core is excluded from the score it appears to cover.
- **Fix:** scheduled workflow running the 4 shards + trend file (mirror the backend mutmut
  posture); fix the glob to `*.{ts,tsx}`.

---

## P2 findings (20)

### Backend correctness

**P2-1. Historical-import purchase books an expense with no mandatory Idempotency-Key.**
`backend/app/api/animals.py:385-407` guard vs `:587-602` booking. The RT-C-4 guard ("a keyless
retry would double-book the expense") only covers `managed_purchase`; the historical-import
branch (PURCHASED + price, owner-only) also books a real `ANIMAL_PURCHASE` Transaction through
`execute_idempotent(key=None)` (plain mutate+commit). With an auto-generated tag (blank tag
input) a network-timeout retry creates a second animal + second expense. Extend the 422 guard to
every money-booking branch.

**P2-2. Clearing a mortality-placed disease hold can destroy the authority-notification date.**
`backend/app/api/health.py:412-424`. The null-guard checks for *any* HealthEvent with
`authority_notified_at` (animal-lifetime scope), but the mortality path places the hold with no
HealthEvent — the animal column is the only copy. An earlier event-backed episode satisfies the
guard and the later mortality episode's statutory date is permanently lost. Scope the probe to
the current episode or copy the date onto the CLEARED action row.

**P2-3. Mortality memo's realized ₹/kg is diluted by unpriced weighed sales.**
`backend/app/services/finance.py:612-638`. Rate filter requires weight but not
`sale_price IS NOT NULL`; `SUM(sale_price)` skips NULLs while `SUM(weight)` counts their kg
(sale path explicitly permits weighed-but-unpriced sales). One priced ₹50,000/100 kg sale plus
one unpriced 100 kg sale reports ₹250/kg instead of ₹500 — `estimated_loss` understated by any
factor. Add the price predicate to both numerator and denominator.

**P2-4. Labour calibration divisor diverges from the engine's labour basis.**
`backend/app/services/simulation_calibration.py:1034-1040` vs `engine.py:1417-1426`. Engine
charges half-attendants (`max(0.5, ceil(2·does/threshold)/2)`, 0 when does=0/family labour);
calibration divides by whole attendants and skips the zero-doe case. Executed: 30 does → modelled
labour 50% of actual bill; 0 does → labour line vanishes entirely. Understates recurring cost
up to 2× for small flocks — the direction that makes an unviable project look financeable.

**P2-5. Engine doe-age ledger over-deflates on repeat-breeder culls.**
`backend/app/simulation/engine.py:1049, 1100-1102`. Scaling denominator omits `sum(lact)`;
instrumented deficit vs. the documented invariant is −1.07% at month 12, −4.46% at month 120.
Max-age culls under-fire ~4% at defaults; cull revenue understated ₹17.5k / NPV drift −₹8.5k
over 120 months on defaults, unbounded with horizon. One-line fix (include `sum(lact)`).

**P2-6. Daily-ops: male starter dependent kids are never weaned — stranded in RECOVERY for life.**
`backend/app/simulation/daily_ops.py:1285-1286` (female-only loop) vs `:1340-1363` (age-wean
fallback built for exactly this case). Executed: male dependent kid produces zero hops, no feed
line after age 60, and draws kid mortality hazard (≈63%/yr) for life. The validator explicitly
permits the input. Run the fallback for both sexes (`RECOVERY→MALE_KIDS "weaning"` edge exists).

**P2-7. `apply_system("semi_intensive")` never applies the semi-intensive growth regime.**
`backend/app/simulation/defaults.py:29-53` vs the CIRG field curve at
`assumptions.py:322-336, 349-365`. Every semi-intensive preset keeps the stall-fed weight curve
**plus** the 30% grazing discount — systematically optimistic in a compounding direction
(heavier sales, cheaper feed) for a lender-facing product. Empirically confirmed for all breeds.

**P2-8. Festival-month pruning is destructive across horizon round-trips.**
`backend/app/simulation/assumptions.py:1052-1078`. Shrink 120→24 months prunes the list in
place; extending back to 120 never re-anchors (auto-fill only when `None`). Reproduced: decade
plan permanently keeps 2 of 10 Bakrid months, with **no warning** (coverage warning only fires
past 2050). Same root cause affects `backward_planner.py:473-476`'s horizon extension.

**P2-9. Planner `close_gaps` recommends accumulating piles of useless doe purchases.**
`backend/app/simulation/planner.py:101-108, 270-381, 478-531`. Two legal configurations
(`sale_age_months == 6` / `afb == 6` where the engine's grower chain is empty; `bucks == 0` with
auto-purchase off) make the marginal model report phantom yields; the loop never detects zero
progress and returns escalating purchases (executed: 160 and 296 does) with `gaps_closed=False`,
and the backward planner leads its report with that recommendation.

**P2-10. Screening processing-lease can be outrun under legal configs → double-processing.**
`pipeline.py:663, 687, 1241, 1286-1333` + `config.py:803/809` (no cross-validator). Lease
touches are transactional (invisible until per-image commit); worst no-durable-write window is
one full cascade `(2N+6)×timeout`. With `T=600` (allowed max) or `stale=600` (allowed min) a
second worker stale-reclaims mid-flight: duplicate runs/findings, double provider billing.
Flush after every `_touch_processing_lease` and cross-validate the stale horizon against
`rotation × timeout`.

**P2-11. Screening: empty-string `note`/`review_note` pass Pydantic but violate DB CHECKs.**
`specialists.py:91`, `schemas/screening.py:80`, `api/screening.py:309` vs
`models/screening.py:427-438`. Model-triggered: an LLM `note: ""` → CHECK violation → rollback
→ retry/repay loop until attempt cap. Client-triggered: `POST /findings/{id}/review` with
whitespace `review_note` → unhandled IntegrityError → 500 instead of 422. Map blank → `None`.

**P2-12. Config URL validator never reads `.port` — malformed provider/S3 URLs boot cleanly.**
`backend/app/core/config.py:246-263` (`_https_or_loopback_url`). `urlsplit(...).hostname`
doesn't raise on out-of-range/non-numeric ports; `https://host:99999` passes Settings in any
environment then fails per-request. Sibling validators deliberately read `.port` (config.py:1017,
:1226). Same class of bug the docstring says was already fixed once.

**P2-13. Migration `b5d7f9a1c3e5` ORF-template delete ignores the `health_events` FK.**
`backend/alembic/versions/b5d7f9a1c3e5_backfill_species_reference_data.py:51`. Farms that
recorded ORF vaccinations (template seeded from day one) hold FK references; the DELETE aborts
the upgrade with a raw 23503, contradicting the chain's fail-closed-with-row-ids convention.
NULL the links first.

**P2-14. (Historical, caveat-documented) Applied revision `c8f1d3a5e709` was edited in place; the original variant destroyed ledger data.**
The first shipped form of this revision ran `UPDATE transactions SET amount = 0 WHERE amount::text
IN ('NaN','Infinity',…)` and nulled five price columns (git `baee088`); current code refuses
instead. Any DB that ran the original variant has silently rewritten ledger rows with no
detection query. Retroactive fix impossible — add a detection count to ops runbooks and a
revision-immutability CI check.

### Infra

**P2-15. Production nginx auth rate-limit zone aggregates every client into one bucket.**
`docker/edge-proxy.production.conf.template:5, 32-34` with the mandated TLS-terminator topology
(`edge-entrypoint.sh:236-238`, loopback-only ports). `$binary_remote_addr` is always the
terminator's single address → one shared 5 r/s / burst-20 bucket for **all** users of
`/api/auth/*`. ~25 r/s of junk 429s every legitimate login/refresh fleet-wide (cheap auth DoS),
plus self-DoS at shift-start bursts. The dev template documents exactly this caveat; the prod
template doesn't, and the rates aren't env-tunable. Key the zone on the trusted forwarded value.

### Frontend

**P2-16. `MAX_PAGE_OFFSET` mirrors are 100× the backend constant.**
`frontend/src/lib/use-url-state.ts:25` and a second drifted copy at
`animals/page.tsx:90` — both `1_000_000` vs backend `10_000`
(`schemas/common.py:26`). Deep links with offset ∈ (10k, 1M] pass the clamp and 422 the API
with no self-heal (recovery effects need a successful response). Affects animals, health,
tasks, feeding, purchases, simulation, screening pages. Backend lowered the constant in commit
`3364771`; the frontend was never updated.

**P2-17. Breeding-entry age gate says 10 months; the backend enforces 12.**
`frontend/src/lib/farm-vocabulary.ts:91, 95` vs `backend/app/models/species.py:90`
(`min_breeding_age_months=12`, with a comment justifying 12). The wrong value is a client-side
zod gate (animals create dialog) + operator copy (animals + breeding pages) + the simulation
field-help anchor ("~10 months", `simulation-field-help.ts:149`, model default 12). Pinned by
contradictory tests on each side. A 10-11-month doe passes client validation, hint says she's
eligible, submit 422s. (Rated P1 by one agent for the guidance angle; consolidated as P2 since
the server rejects the write — no data risk.)

**P2-18. Dashboard renders overdue insurance renewals as "(in -5d)".**
`frontend/src/app/(app)/dashboard/page.tsx:848-853` vs backend deliberately including
past-renewal-date ACTIVE policies on the panel. The panel's most urgent rows show negative-day
nonsense. Mirror the overdue pattern used by the adjacent cards.

**P2-19. Kidding deep link to a resolved pregnancy shows a permanent, unretryable load error.**
`frontend/src/app/(app)/kidding/page.tsx:949-981, 1097-1131`. The informative
"already kidded / no longer confirmed" explainer requires a record the 404ing endpoint never
returns. Revisiting a completed task URL yields a destructive error banner whose Retry always
fails. Treat the 404 as the stale-link case.

**P2-20. Simulation compare dead-ends when a selected scenario row vanishes.**
`frontend/src/app/(app)/simulation/page.tsx:1125, 1233-1236, 1817-1840, 3862-3887`. Off-page
selected ids are retained by design but never pruned when a refetch drops the row; compare then
404s on every attempt until reload, with a misleading "(N selected)" label. Prune on refetch or
render removable chips.

*Also noted at P2 severity by wave-5 agents:* cull-candidate does appear in the breeding picker
with no flag (guaranteed 409 for non-owner managers — add `cull_candidate` to
`BreedingCandidateOut`); recurring-complete confirm dialog states the wrong next-occurrence
date (uses `due_date + recur_days` instead of the backend's `max(due, today)` anchor, and
CLEANING series spawn only at verification); `status-badge.tsx` has no `ERROR` mapping so failed
screening runs render a neutral chip; stryker lib shard glob excludes `auth-context.tsx`
(folded into P1-11).

---

## P3 findings (grouped)

Roughly 60 P3s were reported. Grouped by theme, the recurrent ones:

- **Frontend/backend contract mirrors drifting:** `MAX_PAGE_OFFSET` (P2-16), doe age 10 vs 12
  (P2-17), buck weight anchor 42 vs 35 kg and labour threshold 50 vs 60 in
  `simulation-field-help.ts`, `TaskCategory.WATER` missing from both enum-label maps (English
  leaks into the Telugu UI), `CONFIRMED`/`PLACED`/`CLEARED` unmapped in status-badge, dead
  `milk.*` entries in the team permission-dependency map, `enum-labels` docstring overstates
  the byte-pin safety net (mortality + all 5 screening vocabularies + role preset codes are not
  pinned by tests).
- **Robustness/fail-mode gaps:** `utils.today/business_date` catch only
  `ZoneInfoNotFoundError`, not `ValueError` (corrupt `farm.timezone` 500s the tenant's API);
  whitespace-only `suspected_disease` on DEAD status change → 500; health `next_due_date`
  10-year ceiling not re-checked after farm-date resolution → DB CHECK → 500; `InsurancePolicyIn.notes`
  lacks `max_length`; claim_policy 409-vs-422 dispatched by substring match on error text;
  `InsuranceExpiringOut.animal_id` always None; dead `"renewed"` insurance status in the DB
  vocabulary; `_SENSITIVITY_PASSES` comment says 17, code runs 19; `cost_months` +1-month bias
  at whole-month boundaries; explicit empty `festival_sale_months=[]` fails to disable the
  legacy eid uplift; user-supplied meat-price multipliers not normalized to mean 1.0.
- **Screening:** cross-checker selection ignores who served the gate and who already failed
  (second opinion silently skipped in 3-provider rotations); API mints a new boto3 client per
  request instead of the process-wide instance; `prompt_version` column exactly 20/20 chars
  used; `export_dataset` default contradicts docstring (defaults to ALL incl. pending);
  `object_info` reads `ContentLength` outside its error wrapper.
- **Migrations/DB:** three CHECK swaps take ACCESS EXCLUSIVE over full scans (chain's own NOT
  VALID+VALIDATE pattern not used); legacy rejection notes and malformed kidding links
  destroyed without archive; jsonb preflight loads whole tables into Python; env.py lacks a
  metadata naming convention and `fileConfig` may silence host loggers; insurance backfill
  skips zero-length policy periods.
- **Infra hygiene:** Dockerfile bakes uv's wheel cache into the image (every wheel shipped
  twice, ~90 MB+); `uv sync --frozen` doesn't assert lock↔pyproject consistency (`--locked`
  does); `.dockerignore` misses coverage.xml/audit_reports; pnpm `lint` can't fail on
  warn-severity rules (no `--max-warnings 0`); tabs `orientation` prop silently dropped
  (stock-shadcn inherited); graceful shutdown cancels but never awaits `throttle_summary_task`;
  `TrustedHostMiddleware` innermost so CORS preflights bypass Host validation (fingerprinting
  only); `_configure_logging` forces root INFO despite "no-op" docstring.
- **Frontend UX/consistency:** number inputs never validate stored values on mount (gate
  blind to loaded out-of-bounds data); calibration triggers a useless defaults fetch that
  disables the whole editor; `age_months` on herd events invisible/uneditable; retired
  `male_calf_*` keys missing from `DAIRY_HIDDEN_FIELDS`; URL-driven page change doesn't clear
  the rendered comparison; error messages surfaced twice (toast + inline); insurance dialogs
  block dismissal during in-flight writes contradicting their own comments; finance stat cards
  show all-time totals above a filtered ledger without labels; planner delete-409 leaves stale
  revision (unretryable until reload); ops-sim has no client-side coherence validation and no
  stale-results flag; screening rows mouse-only; provider scoreboard not refreshed after
  reviews; `/screening` missing from the permission-route map (returnTo silently dropped);
  health-log pagination lost on animal deep-links; tasks offset-normalization effect runs every
  render; four dashboard sections skip the fail-closed permission OR the page itself
  establishes; no cross-tab sync of farm selection/logout; generated queries get no client-side
  timeout (caller signal disables it); bootstrap treats transient refresh failure as sign-out.
- **Test hygiene:** backend coverage floor `fail_under=75` vs measured 94/86 (ratchet never
  bites); per-test TRUNCATE has no `lock_timeout` (a leaked lock hangs the suite); a handful of
  regression tests assert disjunctive status sets instead of the shipped contract; frontend
  minority suites use arbitrary `setTimeout` absence-waits; ~57 assertions couple to Tailwind
  classes.
- **Simulation narrative layer (explain.py):** "payback by liquidation" sentence prints with
  ₹0 terminal value; `debt_years` figure counts the balloon-only year the engine excludes;
  family-labour imputation understates the forgone wage 1.29× on defaults; MC paragraph
  describes toggleable features as unconditional; litter-expectation guard tests the wrong
  variable; `prob_npv_negative_se` returns 0.0 for a single run against its documented `None`
  contract.
- **Dead/misleading code:** dead `BucketBoardRow` duplicate schema; write-only
  `status_notes`; `MULTIPLET` birth type unproducible; dead AI-handling branches in
  `create_breeding_record`; `replan_dam…` docstring is a no-op expression; dead `preserve`
  branch in `_clean_permissions`; dead `latest_weight` projection in dashboard; duplicated
  audit event on idempotent replay of worker creation; API bypass for the phenotype PATCH
  justified by a stale "generated client lags" comment (client is byte-current).

---

## Verified correct (load-bearing invariants that held)

1. **Tenant isolation — clean.** All 478 query sites across api/services are farm-scoped,
   farm-scoped-via-parent, or provably-safe global-PK lookups backed by composite
   `(farm_id, id)` FKs; cross-farm payload references are rejected everywhere they were
   probed; no RLS exists but the composite-FK layer makes cross-farm child rows unrepresentable
   (exhaustively pinned by `test_tenant_foreign_keys.py`). The only note: `purchases.py::
   _batch_out` omits the explicit farm predicate its health twin carries (defense-in-depth
   inconsistency, not a leak).
2. **OpenAPI contract — zero drift.** Deep diff of `create_app().openapi()` vs
   `shared/openapi.json`: 0 differences across 94 paths / 223 schemas; orval regeneration is
   byte-identical for models and exact for all 110 endpoint functions/124 URLs; the only
   code-route absent from the spec is `/metrics` (deliberate); every frontend network bypass is
   accounted-for and typed.
3. **Auth/session machinery.** Refresh rotation race-safe (lock order User→Session, grace
   window byte-identical re-mint), replay → family revocation, `token_version` revalidated on
   every locked write path, TOTP RFC-6238-exact with race-proof guess budgets and AES-GCM at
   rest, timing-equalized login, cookie hygiene (`__Host-`, Sec-Fetch-Site, dual-cookie
   rejection), single-flight cross-tab refresh via Web Locks, fail-closed farm switching with
   cache cancel+clear (no cross-farm UI leak).
4. **Money integrity.** No double-booking path found in the ledger: all persisted money is
   Decimal-quantized through `money()`, corrections are atomic void+replace under partial
   unique indexes, premiums are append-only with durable unique keys, per-head allocation sums
   exactly, insurance lapse/renew/claim serialize correctly against animal exit.
5. **Idempotency machinery.** Claim/response commit atomically; concurrent duplicates arbitrate
   at the unique index; expiry is inclusive everywhere and re-clocked after waits; frontend key
   lifecycle (mint/retain-on-ambiguity/release-on-success, reload recovery actor+farm scoped)
   matches the backend contract on all six required-key routes.
6. **Engine numerical core.** Mass-balance identity holds to 1e-14 over 120 months;
   non-negativity across a 13-case boundary battery; bit-identical determinism; percentile
   machinery uniform (numpy-linear); copula correlations exact; IRR/MIRR/EMI/DSCR formulas
   verified against hand computations.
7. **i18n.** 1113 keys per catalog, 0 missing/extra/mismatched placeholders; all 40 backend
   `title_key`s covered; Telugu fallback chain sound.
8. **Backend test suite.** Real DB via alembic, per-test truncation isolation, genuine
   `asyncio.gather` concurrency with lock-wait barriers (not sleeps), zero tautologies/assert-
   free tests/mocks-bypassing-code; mutation testing scoped and sound on the backend.

---

## Recommended fix order

1. **P1-1 + P1-2 + P1-3** (screening worker crash trio) — one PR: capture attributes before
   rollback, harden both parsers, add the PROCESSING-at-budget sweep. Highest operational risk;
   all three compound.
2. **P1-6** (migration FK abort) — blocks upgrades on deployed dairy-era DBs; two-line fix.
3. **P1-4 / P1-5** (simulation correctness on valid input) — one-line reset + clamp/skip logic.
4. **P1-7…P1-10** (frontend dead-ends) — small, isolated dialog/form fixes.
5. **P2-1/P2-2/P2-3** (money/audit integrity edges) then **P2-4…P2-9** (simulation bias
   cluster — labour divisor, ledger denominator, stranded kids, semi-intensive regime,
   festival pruning, planner loop guard).
6. **P2-15** (prod auth rate-limit aggregation) before the next production deploy.
7. **P2-16/P2-17** (contract-mirror drift) + a generated-constants parity test to stop the
   class.
8. P1-11/P2-20 and the P3 backlog by theme.
