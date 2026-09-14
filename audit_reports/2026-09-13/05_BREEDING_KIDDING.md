# Red Team Audit — Part D (Breeding, D1–D5) + Part E (Kidding, E1–E3)

Date: 2026-09-13 · Auditor: red-team unit 5 · Target: Herdly goat-farm SaaS monorepo
(`backend/app/`, FastAPI + async SQLAlchemy + PostgreSQL)

## 1. Methodology

End-to-end adversarial read of the implementation, verifying every enforcement claim
in code rather than in comments:

- `backend/app/api/breeding.py` (D1–D5 routes), `backend/app/api/kidding.py` (E1–E3 routes)
- `backend/app/services/breeding.py`, `backend/app/services/kidding.py` (full read)
- `backend/app/models/breeding.py` (BreedingRecord/KiddingRecord/KidEntry + all CHECKs/indexes),
  `backend/app/models/species.py`, `backend/app/models/helpers.py`, `backend/app/models/lifecycle.py`,
  `backend/app/models/constants.py`, relevant parts of `models/animals.py`
- `backend/app/schemas/breeding.py`, `backend/app/schemas/kidding.py`, `schemas/common.py` bounds
- Lock/consistency support: `services/_common.py`, `services/chronology.py`, `services/animals.py`
  (`move_animal`/`bucket_transition_error`/`_tag_exists`), `services/idempotency.py` (error path),
  `api/_shared.py`, `api/animals.py` `change_status` pregnancy auto-resolution + `move_bucket`
  override path, `permissions.py` `TASK_CATEGORY_ROLE_MAP`
- Migrations: `e7f9a1b3c5d8` (kidding trigger lock order + immutability guards),
  `d3b5f7c9e024` (farm-keyed tag-namespace advisory locks), `c3d4e5f6a7b1` (kid-entry tag index
  predicate), `b9e1c2d3f4a5` (pregnancy-loss audit + reproductive-integrity triggers),
  `f9b3c7d1e5a2` (kid-count CHECK widened to 4)
- Tests reviewed for known regressions: `test_breeding_bugs.py`, `test_breeding_extended.py`
  (241 tests), `test_kidding_due_pagination.py`, `test_e2e_scenario_gaps.py`,
  `test_lactation_curve.py` (simulation-only math; no D/E surface)

Attack hypotheses executed per unit: cross-tenant id injection, self-breeding (doe=buck),
sex/age/weight/VWP/open-breeding gate forgery, concurrency (same-doe double service, same-buck
ratio race, abort-vs-kidding, kidding-vs-sale, tag-namespace lock-upgrade), state-machine
replay (double ultrasound/abort/kidding), chronology backdating (before birth/purchase/scan,
future dates, out-of-gestation), litter caps, tag collision and collision-oracle abuse,
task-creation forgery/role assignment, pagination/offset abuse, int4 overflow → 500,
schema-vs-DB-CHECK parity (500-vs-422), precision/NaN/Inf, raw-SQL injection.

## 2. Findings summary

| ID | Severity | Unit | Title |
|----|----------|------|-------|
| RT-DE-1 | Medium | D4/D5/E3 × C4 | Owner history-override can strand an open service/pregnancy in a bucket with no workflow exit — kidding/abort/positive-ultrasound then fail permanently |
| RT-DE-2 | Low | D3 | Inbreeding fence is one generation deep — grandparent-grandchild and avuncular matings pass (half-sibling documented-permitted, the others not) |
| RT-DE-3 | Info | D4/E3 | Detected kid_count is never reconciled against the delivered litter (deliberate flexibility, undocumented) |
| RT-DE-4 | Info | E3 | A zero-delivery kidding is unrepresentable (kids min 1) — total fetal loss must be forced through abort semantics |
| RT-DE-5 | Info | D3 | AI/AI_SEXED + semen_sire_name remain on the wire/model but are hard-rejected (409) — dead surface, status-code mismatch |
| RT-DE-6 | Info | D4/E3 | Backdated kiddings and late positive ultrasounds spawn already-overdue duties |
| RT-DE-7 | Info | D3/D4/D5/E3 | Pure input-chronology violations are mapped to 409 (kidding) / 422 (abort) inconsistently |
| RT-DE-8 | Info | D3 | Client `heat_cycle_number` accepted on the wire and silently ignored (documented; cycle derived server-side) |

Counts: **0 Critical, 0 High, 1 Medium, 1 Low, 6 Info.**

---

## 3. Detailed findings

### RT-DE-1 — Medium — Owner history-override strands open services outside every workflow edge

**Units:** D4 (`record_ultrasound_result` positive path), D5 (`mark_aborted`), E3 (`record_kidding`)
**Evidence:**
- `backend/app/api/animals.py:769-770` — `history_override` is owner-only; `backend/app/services/animals.py:68-69`
  (`bucket_transition_error`) — `context == "history_override": return None` bypasses
  `LEGAL_BUCKET_TRANSITIONS` entirely (documented, deliberate).
- `backend/app/api/animals.py:854-859` — the move endpoint's pregnancy guard only fires for
  `to_bucket in {PREGNANCY_LATE, DELIVERY}`; there is no pregnant-doe guard for overrides into
  FOUNDATION / FEMALE_KIDS / QUARANTINE / RECOVERY (this is the RT-C-5 family gap, confirmed
  still open for non-pregnancy target buckets).
- `backend/app/models/lifecycle.py:34-63` — the map has `kidding`-context edges into RECOVERY only
  from PREGNANCY_EARLY/PREGNANCY_LATE/DELIVERY, and `abortion`-context edges into RESTING only
  from PREGNANCY_EARLY/PREGNANCY_LATE/BREEDING/DELIVERY. There are **no** edges from FOUNDATION,
  FEMALE_KIDS, QUARANTINE or RECOVERY to RECOVERY/RESTING. The BREEDING→RESTING `abortion` edge
  (line 55, with comment) shows the authors already compensated for exactly this scenario — but
  only for one bucket.
- Consequences: `services/kidding.py:262-273` (`move_animal(..., RECOVERY, context="kidding")`
  raises) → `api/kidding.py:355-358` maps the `ValueError` to **409**; `services/breeding.py:858-870`
  (abort move) → `api/breeding.py:448-450` maps to **422**; `services/breeding.py:685-697`
  (positive ultrasound move to PREGNANCY_EARLY) → `api/breeding.py:410-415` maps to **409**.

**Exploit sketch:** Owner history-overrides a CONFIRMED_PREGNANT doe from PREGNANCY_EARLY into
FOUNDATION (e.g. "correcting" a mistaken move). Her pregnancy now has no legal kidding date path:
`POST /api/kidding` 409s ("Illegal lifecycle transition FOUNDATION → RECOVERY"), `POST /api/breeding/{id}/abort`
422s, and re-breeding is blocked by the open-pregnancy check. She remains ACTIVE+CONFIRMED on the
overdue list forever (E1 `api/kidding.py:82-90` keeps listing her). The same owner action on a
PENDING service (override her out of BREEDING) blocks only the *positive* ultrasound path
(the negative path performs no bucket move, `services/breeding.py:736-742`).

**Impact:** Lifecycle workflow deadlock reachable through an authorized, deliberately unguarded
tool. No data corruption is written (all writers refuse), and recovery exists (re-override into
BREEDING/PREGNANCY_*, or SOLD/DEAD/CULLED, whose auto-abort skips the move for non-ACTIVE does —
`api/animals.py:1085-1151`). Owner-only, so not High.

**Fix:** Either (a) extend `bucket_transition_error` to refuse `history_override` moves of a doe
with an open PENDING/CONFIRMED_PREGNANT service out of {BREEDING, PREGNANCY_EARLY, PREGNANCY_LATE,
DELIVERY}, or (b) give `kidding`/`abortion`/`ultrasound` contexts legal edges from the
override-reachable buckets (mirroring the BREEDING→RESTING precedent), or (c) skip the bucket move
(with an explicit `from == to` BucketMove marker, as the auto-abort already does) when the move
would be illegal, and let the reproductive fact commit.
**Regression test:** override a confirmed doe to FOUNDATION; assert kidding (or abort) succeeds and
the doe lands in RECOVERY/RESTING (or is left with an audit marker), not a 409.

### RT-DE-2 — Low — Inbreeding fence depth is exactly one generation

**Unit:** D3 · **Evidence:** `backend/app/services/breeding.py:449-468` — the fence compares only
`buck.id ∈ {doe.sire_id, doe.dam_id}`, `doe.id ∈ {buck.sire_id, buck.dam_id}` and full-sibling
equality. **Exploit sketch:** a buck mated to his own granddaughter passes (her sire_id is his son,
not him); an uncle-niece pairing passes; half-siblings pass (explicitly documented as permitted).
**Impact:** the "buck rotation promise" (the code's own words) is enforceable only against
parent-offspring and full-sibling pairs; 25%-relatedness pairings accumulate in the lineage graph
that kidding writes (`sire_id`/`dam_id`) and downstream retention decisions inherit. Genetics
quality issue, not corruption of the data model. **Fix:** walk two generations of ancestry
(the pedigree is already in `animals.sire_id/dam_id`) or document the depth-1 scope next to the
half-sibling note.

### RT-DE-3 — Info — Scan count never reconciled with delivered litter

**Unit:** D4/E3 · **Evidence:** `services/breeding.py:622-628` caps detected fetuses at 4;
`services/kidding.py:102-106` caps delivered kids at 4; no cross-check exists between
`kid_count_detected` and `len(kids)` — a scan of 4 may deliver 1–4 (or be aborted), and vice versa.
Appears deliberately flexible (scan counts are approximate). Unconfirmed as intentional only in
the sense that no comment addresses it. No action required beyond documenting the policy.

### RT-DE-4 — Info — Zero-delivery kidding unrepresentable

**Unit:** E3 · **Evidence:** `schemas/kidding.py:49` `kids: Field(min_length=1, ...)`. A confirmed
pregnancy that delivers nothing at term (mummified/resorbed fetuses) cannot be recorded as a
kidding; the operator must use `/abort` with a loss cause, which is semantically a pregnancy loss
rather than a delivery event. Consistent with the CHECKs (`models/breeding.py:215-230` — one
kidding per breeding) but a domain-modeling observation.

### RT-DE-5 — Info — Dead AI wire surface; 409 for an input-shape rejection

**Unit:** D3 · **Evidence:** `schemas/breeding.py:18,29` and `models/breeding.py:85-90`
(`ck_breeding_records_sire_identity`) still model AI/AI_SEXED + `semen_sire_name`, but
`services/breeding.py:434-442` hard-rejects every non-NATURAL method ("not part of the goat
protocol") and the API maps that `ValueError` to **409** (`api/breeding.py:320-324`).
`semen_sire_name` is unreachable storage (always None for NATURAL). Not exploitable; a 422 would
be the truer status for an unsupported method, and the dead wire field invites client confusion.

### RT-DE-6 — Info — Backdated reproductive events spawn already-overdue duties

**Unit:** D4/E3 · **Evidence:** `services/breeding.py:698-735` derives vaccine/move/KIDDING_DUE
due dates from `expected_kidding_date = breeding_date + 150` even when the positive ultrasound is
recorded near day 200 (allowed window, lines 605-621); `services/kidding.py:306-315` schedules
WEANING at `kidding_date + 60` even for a kidding recorded months after the fact. Overdue-at-birth
tasks are operational noise (and G4's early-completion block already tolerates overdue duties),
not corruption.

### RT-DE-7 — Info — Inconsistent status mapping for identical chronology failures

**Unit:** D3/D4/D5/E3 · **Evidence:** the same class of error (a date inconsistent with recorded
lifecycle facts) is a **409** from kidding (`api/kidding.py:355-358`), a **422** from abort
(`api/breeding.py:448-450`), and a **409** from breeding-create (`api/breeding.py:320-324`) and
ultrasound (`api/breeding.py:410-415`). Each mapping is internally documented; clients must
handle both. Cosmetic contract inconsistency only.

### RT-DE-8 — Info — Client `heat_cycle_number` accepted and ignored

**Unit:** D3 · **Evidence:** `schemas/breeding.py:31-35` accepts `heat_cycle_number` (1..99) but
`create_breeding_record` derives the value server-side from the doe's own consecutive FAILED
streak (`services/breeding.py:386-415,530`), so a forged cycle index cannot inflate the
first-cycle KPI. Schema documents this. Correct behavior; noting the wire field for contract
cleanliness.

---

## 4. Per-unit attacked-and-held notes

### D1 — Breeding list + detail (`GET /api/breeding`, `GET /api/breeding/{id}`)
- `breeding.view` enforced on both (`api/breeding.py:123,211`); every query farm-scoped
  (`BreedingRecord.farm_id == farm.id`).
- int4 guard: forged ids above 2^31-1 → 404, never asyncpg DataError 500
  (`api/breeding.py:63-64,99-100`), backed by `MAX_INT32_ID` pre-checks.
- Pagination: `limit` 1..200, `offset` 0..1,000,000; stable ordering
  (`breeding_date desc, id desc`). Total = one count query, farm-filtered.
- Availability aggregates (`eligible_doe_count/buck_count`) disclosed only to `breeding.manage`
  (`api/breeding.py:146-151`); view-only callers get `null`. Counts computed in bounded SQL
  (`services/breeding.py:195-213`).
- No cross-farm oracle: detail 404s uniformly for missing/cross-farm/int-overflow.

### D2 — Candidate search (`GET /api/breeding/candidates`)
- `breeding.manage` required; `kind` is a required Literal (missing → 422).
- `q` ≤ 60 chars, LIKE metacharacters escaped (`\\`, `%`, `_`) with explicit `escape="\\"`
  (`services/breeding.py:136-145`) — no wildcard abuse.
- All eligibility in SQL (farm, ACTIVE, sex, unrestricted/healthy, bucket, age via
  `effective_dob`, latest weight as-of reference date, no unresolved pregnancy —
  `services/breeding.py:80-133`); least-privilege projection (id/tag/name/age/latest weight only).
- Caps 1..100/offset 1e6; count via subquery. Held.

### D3 — Service recording (`POST /api/breeding`)
- **Cross-farm ids:** participant lock query is farm-filtered; missing/cross-farm → 404
  "Doe or buck not found" (uniform) (`api/breeding.py:249-260`).
- **Self-breeding:** doe_id == buck_id collapses to one locked id; `is_breeding_candidate` needs
  sex F and `is_buck_breeding_candidate` sex M — same animal fails one of them → 400; plus
  `ck_breeding_records_distinct_parents` (`models/breeding.py:100-102`). Held.
- **Sex validation:** buck must be male, ACTIVE, unrestricted, FOUNDATION/BREEDING bucket, min
  sire age 12 mo, min sire weight 25 kg — all from CURRENT data (latest weight as of the
  breeding date, correlated SQL) (`services/breeding.py:244-260,263-274`).
- **Eligibility gates (doe):** min age 10 mo (from `effective_dob`, as of breeding_date), min
  weight 22 kg (latest recorded ≤ breeding date, coalescing birth weight only after dob),
  open-breeding exclusion, buckets = FOUNDATION/FEMALE_KIDS/RESTING/BREEDING (QUARANTINE and
  RECOVERY does excluded), movement-restriction and scheduled-disease-suspicion excluded,
  cull-candidate does owner-only (`services/breeding.py:216-241,478-485`). Server-side; identical
  predicates in the picker SQL (`_candidate_filters`) — parity verified.
- **Same-doe race:** doe (and buck) locked `FOR UPDATE` in one id-ordered statement
  (`api/breeding.py:249-258`) — same statement/order as `create_kidding`'s parent lock, so no
  lock-order inversion; loser re-reads committed state (`doe_has_open_breeding` runs after the
  lock) → 400; any residual race trips the partial unique `uq_breeding_open_pregnancy`
  (`models/breeding.py:158-163`) → caught `IntegrityError` → 409 with the doe's tag. Held.
- **Buck ratio 1:20:** `_buck_open_service_count` counts PENDING + confirmed-undelivered services
  for the sire under his row lock — concurrent same-buck services serialize and the count is
  re-read post-lock (READ COMMITTED) → 21st service 409 (`services/breeding.py:311-337,507-514`).
- **Inbreeding fence:** parent-offspring + full-sibling blocked (`services/breeding.py:449-468`) —
  see RT-DE-2 for depth.
- **Method:** AI/AI_SEXED refused (409) — see RT-DE-5; `semen_sire_name` never stored.
- **Chronology:** breeding date must be ≥ each participant's effective dob and purchase date, not
  in the farm's future (farm timezone), strictly after the doe's latest reproductive boundary
  (max of latest breeding/ultrasound-result/loss/kidding dates — append-only workflow), and
  outside the 14-day VWP after her latest kidding (`services/breeding.py:469-506`). Year-1 dates
  are accepted for animals without dob/purchase but cannot overflow date math (guards in
  `add_months`/`age_months_on`).
- **cycle_number:** derived server-side (`derived_heat_cycle_number`), capped at the 1..99 CHECK;
  client value ignored (RT-DE-8).
- **Task creation:** exactly one ULTRASOUND duty (planned +32d) per record, auto-assigned to the
  VET preset role via `TASK_CATEGORY_ROLE_MAP` (`services/_common.py:71-93`) — no user-controllable
  assignment, no forgery vector.
- Deterministic pre-claim checks (future date, int4) run before the idempotency claim; everything
  state-dependent inside `mutate()` — replays never re-evaluate. Held.

### D4 — Ultrasound recording (`POST /api/breeding/{id}/ultrasound`)
- **Locking:** `_lock_doe_then_breeding_record` takes doe → breeding row in canonical
  animal→breeding→task order, with a scalar pre-check so the locked fetch is the first ORM load
  (`api/breeding.py:88-116`). Matches `create_kidding` (parents id-ordered → breeding → tasks) and
  `change_status` — no inversion found.
- **State preconditions:** double ultrasound → 409 (re-checked under the row lock,
  `api/breeding.py:382-386` + service PENDING guard `services/breeding.py:594-595`); UNASSESSED
  (doe left herd) → 409 with the real reason (`api/breeding.py:371-381`); cross-farm → 404.
- **kid_count:** schema 1..4 (`schemas/breeding.py:56`), service cap `max_litter_size=4` →
  `LitterSizeError` → 422 (`services/breeding.py:622-628`), DB CHECK 1..4
  (`models/breeding.py:126-127`, migration `f9b3c7d1e5a2`) — **parity: no 500 path** (the
  historical quadruplet 500 is fixed). `kid_count` with pregnant=false → 422 at schema
  (`schemas/breeding.py:58-62`); pregnant with no count allowed (unknown litter).
- **Chronology:** result ≥ breeding date; result ≤ breeding+200 both directions; positive results
  additionally cannot predate the planned +32d check (negative early results allowed from day 18,
  and days 0-1 for observed service failure; the unobservable 2..17-day gap is rejected —
  `services/breeding.py:603-647`); future dates rejected against the farm timezone
  (`api/breeding.py:387-391`).
- **Pregnant cascade (atomic, one transaction):** outcome=CONFIRMED_PREGNANT, cull flag cleared,
  expected date = breeding+150 (from service date, correct), doe → PREGNANCY_EARLY (legal only
  from BREEDING; `allow_restricted_reclassification=True` deliberately lets the authoritative
  fact commit under a disease hold), 4 tasks spawned (2 pre-kidding vaccines at ekd-40/-25 for
  VET, DELIVERY move at ekd-15 for MOVER, KIDDING_DUE at ekd for VET). Duplicate task generation
  is impossible: replay blocked by the PENDING re-check under the row lock.
- **Negative cascade:** outcome=FAILED, `kid_count_detected` nulled, ULTRASOUND duty DONE with
  attribution, cull streak re-evaluated after an explicit flush (load-bearing ordering),
  pending-task states never clobbered (locked + re-checked `FOR UPDATE` with status filter).
  Doe stays in BREEDING for re-service — correct (no bucket restore needed).
- **Deadlock:** trigger `lock_kidding_parents_in_lifecycle_order` (migration `e7f9a1b3c5d8`) makes
  any direct `kidding_records` INSERT take animal→breeding order, matching the API.
- Gap: RT-DE-1 (illegal-from buckets via history_override).

### D5 — Pregnancy abort (`POST /api/breeding/{id}/abort`)
- **Preconditions:** CONFIRMED_PREGNANT and no kidding record, re-checked under doe+breeding locks
  (`api/breeding.py:429-437`); service re-checks outcome + `_kidding_record_of` and the DB trigger
  `enforce_reproductive_outcome_integrity` blocks abort-after-kidding and kidding-after-abort at
  the row level (migration `b9e1c2d3f4a5`) — replayed abort → 409.
- **Chronology:** loss date ≥ breeding date, ≥ ultrasound confirmation, ≤ breeding+200 (overdue
  abortions allowed inside the window; only the internal ANIMAL_STATUS_CHANGE close may exceed it,
  via `allow_late_administrative_close=True` — never wire-reachable, `PregnancyLossIn` excludes
  the server-owned cause).
- **Cause/notes:** Literal 6-cause enum (server-owned ANIMAL_STATUS_CHANGE excluded from input,
  `schemas/breeding.py:95-105`), notes ≤ 4000 (schema + service + Text column parity).
- **RESTING move:** legal from PREGNANCY_EARLY/PREGNANCY_LATE/BREEDING/DELIVERY with context
  `abortion` (`models/lifecycle.py:51-56`); skipped (not faked) for non-ACTIVE does.
- **Tasks:** every remaining PENDING duty for the breeding (KIDDING_DUE, vaccines, move) skipped
  with reason and attribution, locked+re-checked — committed completions survive.
- **Race vs kidding:** both endpoints lock the doe before the breeding row → serialized; loser
  re-reads and 409s. Race vs sale/death: `change_status` auto-resolves the pregnancy first with
  the same lock order (`api/animals.py:1085-1151`); manual abort then 409s. Held.
- Gap: RT-DE-1 (FOUNDATION etc.).

### E1 — Kidding register (`GET /api/kidding`)
- `kidding.view` enforced; tri-page structure with independent bounded pages: history
  limit ≤ 200, upcoming/overdue ≤ 100, all offsets ≤ 1e6; stable orderings.
- Upcoming = expected date in [farm-today, farm-today+30]; overdue = < farm-today
  (`today(farm.timezone)` — correct farm-timezone semantics; stored dates are calendar facts).
- Farm-scoped throughout; phantom pregnancies excluded twice (outcome filter + ACTIVE doe join).
- Total counts are per-farm subquery counts. No leak of other farms' pregnancies found.

### E2 — Pregnancy resolver (`GET /api/kidding/pregnancies/{id}`)
- `kidding.view` enforced; returns ONLY a live pregnancy (CONFIRMED_PREGNANT, expected date set,
  ACTIVE doe, no kidding record) in the caller's farm; everything else — cross-farm, resolved,
  unconfirmed, int-overflow — is a uniform 404 (`api/kidding.py:167-193`). No enumeration oracle
  beyond the 404/200 distinction itself (id must be a valid breeding id of the same farm).

### E3 — Record kidding (`POST /api/kidding`) — critical unit
- **Authz/tenancy:** `kidding.manage`; breeding fetched with `farm_id` filter; cross-farm → 404.
- **Locking:** scalar pre-check → both parents (doe + sire, id-ordered, single statement) →
  breeding row `FOR UPDATE` → task locks later inside the service (`api/kidding.py:216-263`) —
  identical animal-id order to `create_breeding`, so no doe/buck lock inversion; the
  `trg_00_kidding_insert_lock_order` trigger covers direct-SQL inserts.
- **Double kidding:** three layers — `br.kidding_record` under lock (409), service
  `_kidding_record_of` re-check, and the DB `uq_kidding_breeding_record` UNIQUE whose violation
  maps to 409 (`api/kidding.py:359-374`). Completion is atomic: the KiddingRecord, kid entries,
  kid animals, dam move, task states and the WEANING duty are one transaction (there is no
  COMPLETED outcome — `outcome` stays CONFIRMED_PREGNANT and `has_kidding` is the completion
  marker; verified consistent across CHECKs and due-list filters).
- **Preconditions:** CONFIRMED_PREGNANT only (400 otherwise), ACTIVE doe (409), kidding date not
  future (farm timezone, 422), ≥ breeding date (400), gestation window 100–200 days (409), ≥
  ultrasound confirmation date (409). No near-expected-date requirement — the ±50-day window is
  the deliberate recording band (`services/kidding.py:89-101`).
- **Litter:** ≥1 and ≤4 kids (`kids` schema 1..10, then `LitterSizeError` → 422 above the species
  cap); per-kid sex enum M/F; per-kid birth weight banded 0.5–8.0 kg → 422 (protects the
  weight-gate coalescing downstream); NaN/Inf impossible (finite float + DB CHECK
  `ck_kid_entries_birth_weight`); DIED requires mortality date within [kidding date, farm-today],
  non-DIED must not carry one (schema, router, service AND the
  `enforce_kid_mortality_chronology` trigger + `ck_kid_entries_mortality_matches_status`).
- **Kid creation:** ALIVE/DIED kids create Animal rows (source=BORN, dam_id, sire_id=buck of the
  breeding — AI is unreachable so sire is always a herd buck; date_of_birth=kidding date;
  DIED kids get a DEAD-status animal for lineage); STILLBORN kids create only a KidEntry. Kids
  enter RECOVERY with the dam (not MALE/FEMALE_KIDS — the scope hypothesis was wrong; consistent
  with `young_stay_with_dam=True` and `ck_animals_bucket_sex`, which puts no sex constraint on
  RECOVERY).
- **Tags:** auto tags `<doe>-K<n>` with cryptographic `-A<hex>` fallback (bounded retries, no
  sequential-suffix DoS); explicit tags checked intra-request and against both namespaces
  (animals + all kid entries incl. stillborn), races covered by `uq_animal_tag_per_farm`,
  `uq_kid_entries_farm_tag` and the stillborn cross-namespace trigger — all mapped to 400, never
  a bare 500 (`api/kidding.py:326-341,359-374`, `services/kidding.py:156-213`). The
  farm-keyed advisory-lock triggers (`d3b5f7c9e024`) take the lock *before* the check in both
  directions; the service pre-acquires the EXCLUSIVE farm lock when any stillborn is present,
  eliminating the SHARE→EXCLUSIVE upgrade deadlock (comment block, `services/kidding.py:119-130`).
- **Dam:** → RECOVERY from PREGNANCY_EARLY/PREGNANCY_LATE/DELIVERY (all legal, context `kidding`),
  authoritative even under a movement hold.
- **Tasks:** KIDDING_DUE auto-completed with attribution (locked + status-filtered so a committed
  user skip survives); all other pending pre-kidding duties skipped with reason; WEANING duty at
  kidding+60 for MOVER when any survivor exists, else a postpartum recovery BUCKET_MOVE at
  anchor+14 (anchor = max of kidding and neonatal mortality dates). Duplicate WEANING duties are
  impossible (one kidding per breeding; `replan_dam_after_last_kid_death` touches only this
  litter's duty, with due-date fallback for legacy rows and a nowait dam lock whose 55P03 is
  mapped to a retryable 409).
- Gaps: RT-DE-1 (override-stranded buckets), RT-DE-3/4/6/7 (info-level).

## 5. Cross-domain checks

- **Migration `b9e1c2d3f4a5`:** loss-audit CHECKs (`ck_breeding_loss_*`,
  `ck_breeding_loss_metadata_matches_outcome`) and the
  `enforce_reproductive_outcome_integrity` triggers. API parity verified: `mark_aborted` writes
  every required field (incl. `loss_recorded_at`), sets `pregnant=False`, and its loss-metadata
  immutability matches the trigger's; loss-audit rows cannot be rewritten through any D/E endpoint
  (no update path exists at all). No 500-reachable CHECK was found: every CHECK the API could hit
  has an earlier, stricter service-layer validation mapped to 4xx (kid_count 1..4 ≤ schema 1..4;
  loss ≤ breeding+200 = service bound; result ≤ breeding+200 = service bound; heat cycle 1..99 =
  derived cap; distinct parents = eligibility).
- **Migration `f9b3c7d1e5a2`:** the widened 1..4 CHECK exactly equals schema + profile caps — the
  former quadruplet-500 is closed on all three layers.
- **Migration `e7f9a1b3c5d8`:** lock-order trigger + relationship-immutability guards on both
  `breeding_records` and `kidding_records` — no D/E endpoint updates parent identity, so the
  guards are pure defense-in-depth for direct SQL.
- **State-machine soundness:** PENDING → CONFIRMED_PREGNANT | FAILED | UNASSESSED;
  CONFIRMED_PREGNANT → (kidding | ABORTED). A PENDING service can no longer strand on a departed
  doe (`change_status` closes it UNASSESSED, `api/animals.py:1085-1114`); a CONFIRMED pregnancy is
  auto-aborted on sale/death/cull with the server-owned cause and a synthetic same-bucket
  BucketMove marker. `ck_breeding_records_outcome_state` / `..._expected_state` make every
  illegal combination unwritable. The only illegal-but-reachable shape found is the
  override-created one (RT-DE-1).
- **Numbers/precision:** all reproductive math is `date` arithmetic (no datetime/timezone drift);
  `utcnow()` only for audit stamps; weights are finite-bounded floats on both layers; cycle
  numbers capped to the CHECK.
- **Raw SQL:** none in the D/E request path — everything is SQLAlchemy expression language; the
  LIKE escape and the enum `sql_in_values` builders are injection-safe; migration SQL is static.

## 6. Verification depth / unconfirmed items

- All findings above are code-evidenced with file:line. Nothing was assumed from comments alone;
  where a comment claimed lock ordering, the actual statements were compared.
- Not dynamically executed (static audit): the multi-transaction deadlock matrix (kidding vs
  abort vs sale vs kid-death replan) was verified by lock-order reasoning over the exact
  statements; the existing tests cover the known races (twice-bred doe, double ultrasound,
  sold-pregnant-doe auto-abort, tag collisions, mixed-litter weaning).
- RT-DE-3's "deliberate flexibility" is inferred from the absence of any check plus the schema
  comment trail; no design doc contradicts it. Marked Info/unconfirmed-intent.
