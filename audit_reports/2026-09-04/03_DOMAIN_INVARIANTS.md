# Red Team Audit #3 — "The Rogue Farmhand" — Business Logic & Domain Invariants

**Date:** 2026-09-04 · **Method:** read-only adversarial code audit. All paths relative to `backend/app`. Every finding verified against source; nothing modified. Persona: an insider submitting nonsense to the API to corrupt the herd's biological/operational records.

## FINDINGS (ranked by data-corruption impact)

### 1. Buck can be bred to his own daughter — no inbreeding/lineage check anywhere — HIGH
`services/breeding.py:420-487` (`create_breeding_record`), `services/breeding.py:217-262` (`is_breeding_candidate` / `is_buck_breeding_candidate`), `models/breeding.py:94-96`

Attack: `POST /api/breeding {"doe_id": <doe>, "buck_id": <her sire>}`. Eligibility checks only sex/age/weight/status/bucket/open-pregnancy/VWP/ratio. Lineage is never consulted, even though `Animal.dam_id`/`sire_id` exist and are written at kidding (`services/kidding.py:235-236`). Kidding then persists `dam_id`/`sire_id` that are parent-child (or full siblings — same check gap) → lineage graph permanently corrupted; daughter-retention finance logic (`api/finance.py:393`) and the README-promised "buck rotation (inbreeding avoidance)" silently void. The only related constraints block self-breeding: `ck_breeding_records_distinct_parents` (`"doe_id <> buck_id"`) and `ck_animals_parent_identity` (`models/animals.py:112-117`, not-self/not-equal-parents at birth). `buck_rotation_years` exists only as a *simulation* input (`simulation/assumptions.py:236`); no frontend check either.

**Fix:** in `create_breeding_record` (NATURAL), reject when `buck.id in (doe.sire_id, doe.dam_id)` or `doe.id in (buck.sire_id, buck.dam_id)` (optionally full-sibling via shared dam+sire), before the ratio check.

### 2. Milk yields are not tied to the lactation interval — pre-calving / dry-period yields accepted — HIGH
`api/milk.py:227-258` — `has_calved` is existence-only ("must have calved at least once"), the chronology call checks only birth/purchase date (`services/chronology.py:26-33`), and the milking-string check reads the animal's *current* bucket (`api/milk.py:213`).

Attack: buffalo calved 2026-08-01 (now in the milking string). `POST /api/milk/new {"animal_id":…, "date":"2026-01-15", "shift":"MORNING", "litres":15, "fat_pct":7.2}` → 201. The January date was mid-gestation/dry — a physiologically impossible yield is now in the parlour ledger. Repeat across months × herd: daily herd totals, litre-weighted fat averages (`services/milk.py:30-41, 177-253`), the parlour board, and any MILK-income provenance decisions (`schemas/finance.py:64-139` requires only internal consistency with the *fabricated* litres) are all poisoned. The 40 L/day cap (`services/milk.py:81-96`) bounds only one day, not cumulative fabrication. Only `milk.manage` is needed — exactly the rogue-farmhand persona.

**Fix:** require `payload.date >= max(KiddingRecord.date)` for that doe (imported-in-milk exception: `date >= purchase_date`), and reject dates inside a dry-period/DELIVERY bucket interval.

### 3. Breeding method is not species-gated; an "AI" claim bypasses the 1:20 buck:doe genetic-concentration cap — MEDIUM
`schemas/breeding.py:16-37` (`BreedingCreateIn.method: Literal["NATURAL","AI","AI_SEXED"]`, no farm-type constraint), `api/breeding.py:272-281` (`payload.method in ("AI", "AI_SEXED")` accepted with `buck=None`), `services/breeding.py:436-441, 480-487` (ratio check runs only `if buck is not None`)

Attack (a): on a GOAT farm — whose domain model is natural cover with buck rotation — any `breeding.manage` holder submits `method:"AI_SEXED", semen_sire_name:"X"`. Kids from that pregnancy get `sire_id=None` (`services/kidding.py:236`), erasing sire lineage. Attack (b): on any farm, claiming AI skips `_buck_open_service_count` entirely, so one physical buck can be recorded as covering unlimited does by alternating AI-labeled records — the inbreeding-avoidance ratio control is opt-in. (Buffalo: NATURAL with a herd buck is likewise accepted on a dairy whose protocol is AI — softer.)

**Fix:** restrict `method` by `farm.farm_type` (NATURAL-only for goat, or require an owner override), and count AI records attributed to the same `semen_sire_name` toward a sire cap.

### 4. Cross-species numeric band: kid/calf birth weight (and goat weights) accepted up to 1000 kg, propagating into eligibility and dashboards — MEDIUM
`schemas/kidding.py:29` (`birth_weight: NonNegativeWeightKgFloat` = 0–1000, `schemas/common.py:154`), `models/breeding.py:251-265` (`ck_kid_entries_birth_weight ... BETWEEN 0 AND 1000`), `models/animals.py:118-123` (same for `Animal.birth_weight`); same 1000 kg ceiling on `WeightIn.weight_kg` (`schemas/animals.py:158`)

Attack: record a kidding with `birth_weight: 950` → stored on the auto-created Animal; `_latest_weight_as_of` coalesces birth weight as the animal's weight (`services/breeding.py:69-74`), so breeding weight gates (`min_breeding_weight_kg`) and `is_breeding_ready` (`api/_shared.py:119-123`) are satisfied forever by one fabricated number; dashboard "latest weight" and market-ready counts inherit it (`services/dashboard.py:141-142, 226-235`). A goat kid at 950 kg or a weight record of 999 kg for a 30-kg doe is indistinguishable from real data. Zero is also accepted (0-kg newborn).

**Fix:** per-species birth-weight band from `SpeciesProfile` (goat ~1–6 kg, buffalo ~20–60) and per-species adult weight cap.

### 5. "3-service cull rule" is advisory — owner can breed past it indefinitely, and conception resets the counter — MEDIUM
`services/breeding.py:451-458` (`if doe.cull_candidate and not actor_is_owner: raise` — only non-owners blocked), `services/breeding.py:786-805` (flag set after N consecutive FAILED), `services/breeding.py:646` (`doe.cull_candidate = False` on any conception)

Attack: the farm owner (or anyone who owns the farm — a multi-farm owner is "owner" on each) records service #4, #5, … for a 3×-failed Murrah; one intervening pregnancy forgives the whole failed streak. The README's "3-service cull rule" is a UI flag, never a hard cap on open services. Deliberate per code comments, but it is a domain rule that the API will not enforce for the most privileged actor.

**Fix:** hard-cap lifetime consecutive-failed services regardless of actor, or log/attribute every owner override as an audited exception row.

### 6. Dry-off 60-days-before-calving and 90-day weaning are scheduled duties, not enforced gates — LOW
`services/breeding.py:663-688` (dry-off only creates VACCINE + BUCKET_MOVE tasks at EKD−60); `services/kidding.py:266-279` (kidding is accepted directly from PREGNANCY_EARLY/LATE — the DELIVERY/dry-pen move is never required); skip restrictions cover only batch gates and RECOVERY-stranding duties (`api/tasks.py:668-684`; dairy weaning duties are skippable since calves sit in calf-shed buckets, and FEMALE_KIDS→FOUNDATION is manually legal, `models/lifecycle.py:45`).

Attack: skip/ignore the dry-off duties, calve the buffalo, and keep milking her through the pre-calving window — the README's "dry-off 60 days before calving" invariant exists only as reminder tasks; no data-level check (e.g., "milk record within 60 days of a subsequent calving") exists.

**Fix:** reject milk records dated inside `expected_kidding_date − prepartum_move_lead_days` of a confirmed pregnancy (also closes part of finding 2).

### 7. Pregnancy-loss chronology trusts a wide 350-day cross-species gestation band in the DB, species band only at service layer — LOW
`models/breeding.py:60-64` (`loss_date <= breeding_date + 350` for any cause ≠ `ANIMAL_STATUS_CHANGE`); the tight per-species window is only in `mark_aborted` (`services/breeding.py:836-845`). Same pattern for `kid_count_detected` (DB 1–4, species cap only in `record_ultrasound_result:585-592`). Defense-in-depth gap, not directly exploitable through the API today — a direct DB write or a future code path bypassing the service would land inside the CHECK.

**Fix:** nothing urgent; optionally make the CHECKs species-aware via the farm join.

No other attack succeeded: cross-species event registration is blocked where it matters (milk is dairy-only `api/milk.py:53-65`; ops-sim is goat-only `api/ops_simulation.py:63-64`; everything else is species-*adaptive*, not mislabel-able); there are no DELETE endpoints on animals/breeding/milk/feeding (append/correct-only ledger); temporal impossibilities (calving before insemination, weaning before birth, future dates, backdated status before recorded facts) are all refused server-side; state-machine skips require the owner-only, reason-stamped `history_override` sentinel that four downstream predicates recognize (`schemas/animals.py:182-199`).

## INVERIANTS VERIFIED AS ENFORCED SERVER-SIDE (top 5)

1. **Species fence on dairy data**: every `/api/milk` route calls `_require_dairy_farm` (`api/milk.py:53-65`, applied at lines 87, 126, 180); ops-sim refuses buffalo (`api/ops_simulation.py:63-64`).
2. **Reproductive chronology**: kidding gestation window per species + no calving before the confirming scan (`services/kidding.py:89-101`); ultrasound result windows incl. a heat-observability floor for negatives (`services/breeding.py:566-623`); all client dates checked against the farm's business date (`services/chronology.py:20-33`); terminal status cannot predate recorded facts (`services/chronology.py:36-119`).
3. **One open pregnancy per doe / one kidding per pregnancy**: DB-level partial unique `uq_breeding_open_pregnancy` (`models/breeding.py:154-159`) + `UNIQUE(breeding_record_id)` on kiddings (`models/breeding.py:209`), all under a canonical animal→breeding→task `FOR UPDATE` lock order.
4. **VWP + state machine**: the 60-day (dairy) / 14-day (goat) voluntary waiting period is a hard server check (`services/breeding.py:470-479`); bucket moves must follow `LEGAL_BUCKET_TRANSITIONS` with workflow-only edges (`models/lifecycle.py:34-65`, enforced at `services/animals.py:71-76`); pregnancy buckets require a live pregnancy (`api/animals.py:856-861`).
5. **Ledger/inventory conservation**: feed mixes/dispenses debit `FOR UPDATE`-locked stock and refuse shortages atomically (`services/feeding.py:438-462, 580-609`); purchase money is allocated in integer paise summing exactly to the booked expense (`services/purchases.py:136-142`); milk-income rows must reproduce their provenance price within one paisa with fat bounded 3–12% (`schemas/finance.py:43-139`); NaN/Inf/string-typed numbers and negative money/quantities are rejected schema-wide (`schemas/common.py:40-154`), so dashboard/simulation inputs cannot be poisoned by non-finite values.

## VERDICT

Domain integrity is **unusually strong** — most of the mandate (state-machine holes, double-spending rations, orphaned tasks, negative stock, calving-before-insemination, dead-animal writes) is defended in depth at schema, service, and DB-constraint layers with consistent lock ordering. The two genuine wounds are **lineage integrity (finding 1)** — the README's buck-rotation/inbreeding-avoidance promise has zero server-side enforcement and silently corrupts the pedigree graph — and **lactation-interval linkage (finding 2)** — a parlour recorder can fabricate months of physiologically impossible dairy yield that no downstream aggregate can distinguish from real milk. Both are cheap to fix at exactly one choke point each (`create_breeding_record`, `add_milk_record`).
