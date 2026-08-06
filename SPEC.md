# Goat Management Software — Build Specification

Multi-farm SaaS web app for commercial Osmanabadi goat farming in Telangana, India.
Stack: **Python + FastAPI + SQLAlchemy + SQLite + Jinja2 templates + vanilla JS/CSS** (server-rendered, keep it simple). Auth via signed session cookies (itsdangerous or starlette SessionMiddleware). No external services.

## Multi-tenancy
- Users register (email + password, hashed with passlib/bcrypt or hashlib pbkdf2 — use what's available, prefer `passlib[bcrypt]` if installable, else `hashlib.pbkdf2_hmac`).
- A user can own multiple **Farms**. Every business entity is scoped by `farm_id`. User picks the active farm after login (store in session).
- Keep roles simple: farm has an owner (the user). No invite system for v1.

## Breed constants (Osmanabadi, from farm owner's research)
- Gestation: 150 days (kidding window 145–155)
- Heat cycle: 21 days; heat duration 24–48 h
- Kidding interval target: 8 months; ~2 kids/doe/year (high twin rate)
- First breeding age: 10–12 months; doe must be ≥22 kg (65% of mature weight) before breeding
- Adult doe 28–32 kg; adult buck 34–42 kg; buck:doe ratio 1:20 (rotate bucks: 7 days on / 7 days off)
- Slaughter/sale age for meat males: 8–9 months at 24–28 kg
- Reproductive lifespan: 5–6 productive years
- ~75–80% conceive on first cycle; after 2 failed cycles → flag as cull candidate

## The Bucket System (pens/stages)
Every animal lives in exactly one **Bucket**. All moves are recorded (bucket history with timestamps + reason).

| Bucket code | Name | Who lives here | Exit rule |
|---|---|---|---|
| QUARANTINE | Quarantine Ward | Newly purchased animals | 45-day protocol complete → FOUNDATION |
| FOUNDATION | Foundation / Grow-out | Purchased doelings 6–7 mo until breeding-ready (10–12 mo, ≥22 kg); also own female kids 2–10 mo | Breeding-ready → BREEDING |
| BREEDING | Breeding Bucket | Does ready to conceive; 1 buck per 20 does (rotate buck every 7 days) | Ultrasound-confirmed pregnant (day 30–35) → PREGNANCY_EARLY |
| PREGNANCY_EARLY | Pregnancy A (day 35–100) | Confirmed pregnant does; maintenance feed, do NOT overfeed | Day 100 → PREGNANCY_LATE |
| PREGNANCY_LATE | Pregnancy B (day 100–135) | Late gestation; 60/40 feed; optionally split by kid count (singles/twins/triplets) | Day ~135 (2 weeks before due) → DELIVERY |
| DELIVERY | Delivery Ward | Last ~2 weeks of pregnancy through ~5 days post-kidding (10 days for difficult births) | Day ~5–10 post-kidding → RECOVERY |
| RECOVERY | Recovery Ward | Doe + kids together, 2 months (until weaning day 60) | Kids weaned day 60 → doe to RESTING; kids to MALE_KIDS / FEMALE_KIDS |
| RESTING | Resting / Dry-off + Flush | Post-weaning does, ~30 days: days 1–10 dry-off (75/25 feed), days ~10–30 flush (70/30 feed) | After ~30 days → BREEDING |
| MALE_KIDS | Male Kids Growing | Male kids 2–8/9 months; 60/40 frame-builder till day 90, then 50/50 fattening | Sold at 8–9 months, 24–28 kg |
| FEMALE_KIDS | Female Kids Growing | Female kids 2–10 months; 60/40 mix steady | Breeding-ready (10–12 mo, ≥22 kg) → BREEDING |

## Core entities (data model)

### Animal
- tag_number (unique per farm), name (optional), breed (default "Osmanabadi"), sex (M/F), date_of_birth (nullable — purchased animals may have estimated age), birth_type (single/twin/triplet, nullable)
- source: BORN | PURCHASED
- If purchased: purchase_date, purchase_price, seller_name, purchase_batch_id (FK to PurchaseBatch)
- If born: dam_id, sire_id (self-FKs), birth_weight
- current_bucket (enum above), status: ACTIVE | SOLD | DEAD | CULLED
- weight records: (date, weight_kg, notes) — separate table WeightRecord
- body condition score records optional within weight record (bcs 1–5)
- notes, created_at, photo path optional (skip photos v1)

### PurchaseBatch (foundation stock buying, 50 animals every 2 months)
- date, supplier, count, avg_age_months, avg_weight_kg, total_price, notes
- Auto-creates a 45-day quarantine task schedule (below)

### BreedingRecord
- doe_id, buck_id, breeding_date, method (NATURAL), heat_cycle_number (1st, 2nd…)
- ultrasound_date (planned = breeding_date + 32), ultrasound_done (bool), pregnant (nullable bool), kid_count_detected (1/2/3 nullable)
- expected_kidding_date = breeding_date + 150 (if pregnant)
- outcome: PENDING | CONFIRMED_PREGNANT | FAILED | ABORTED
- A doe with 2 consecutive FAILED records → cull_candidate flag on Animal + alert

### KiddingRecord
- doe_id, date, breeding_record_id (link), ease (NORMAL | ASSISTED | DIFFICULT), notes
- KidEntry children: tag, sex, birth_weight, status (ALIVE | STILLBORN | DIED)
- Alive kids auto-create Animal records (source=BORN, dam/sire linked, bucket = RECOVERY with mother)

### HealthEvent (vaccination / deworming / treatment)
- animal_id (nullable if batch event), batch/purchase_batch nullable, date, type: VACCINE | DEWORMING | TREATMENT | FOOTBATH | VITAMIN
- product_name, disease_target, dose, route (SC/Oral/IM), vet_name, cost, next_due_date, notes

### Vaccination schedule templates (seeded reference data, generate due-date tasks per animal by age)
| Vaccine | First dose | Booster | Repeat | Timing |
|---|---|---|---|---|
| FMD | 3 months | 3–4 weeks later | Every 6 months | September & March |
| PPR | 3 months | None | Every 3 years | Core vaccine |
| Enterotoxaemia (ET) | 4 months (if dam vaccinated; else first week) | 3–4 weeks later | Annual | May–June (pre-monsoon) |
| Haemorrhagic Septicaemia (HS) | 3–5 months | 3–4 weeks later | Annual | May/June |
| Goat Pox | 3–5 months | 3–4 weeks later | Annual | Nov/Dec |
| Black Quarter | 6 months | None | Annual | Pre-monsoon |
| Johne's Disease | 6 months | None | Annual | Herd-history dependent |
| Anthrax | 6 months | None | Annual | Region-specific |
| ORF | 4 months | None | Every 6 months | — |
| CCPP | 3 months | None | Annual | January |
| ET + TT (Tetanus) pre-kidding | 4–6 weeks before kidding | Two doses 15 days apart | Each pregnancy | Passes colostrum immunity |
| Deworming | All animals | — | Every 6 months | June & January (kids: every 3 months) |
| Anti-coccidial drench | 1–3 months (Amprolium 5 days) | — | As needed | Coccidiosis peaks 1–6 months |

### 45-Day Quarantine Protocol (auto-generated tasks on PurchaseBatch creation)
- Days 1–3: Rest, electrolyte/jaggery water, dry roughage only, ZERO grain
- Day 4: Deworm — broad-spectrum oral (Albendazole/Closantel) + Ivermectin SC injection
- Days 5–9: Liver tonic (e.g. Brotone) in water + Vitamin AD3E injection
- Day 10: Vaccine PPR (live viral, SC)
- Day 20: Vaccine ET + Tetanus (bacterial toxoid, SC)
- Day 30: Vaccine Goat Pox (live viral, SC)
- Day 40: Vaccine FMD (killed, SC)
- Day 45: 10% Zinc Sulfate footbath → release to FOUNDATION bucket
- Never vaccinate an animal full of worms; separate live viral vaccines by 15–21 days; assume seller's vaccine claims are false ("zero trust")

### Feeding — TMR recipes (seeded; kg per 100 kg batch)
FeedRecipe has name, code, description, and lines (ingredient, kg_per_100kg, category: ROUGHAGE_WET | ROUGHAGE_DRY | CONCENTRATE).

1. **FATTENING_50_50** (male kids day 91 → sale): Green fodder 30, Dry stover/haulms 20, Crushed maize 17.5, Maize DDGS 10, Soya DOC 7.5, Mustard DOC 7.5, DORB 6, Mineral mix 1.5
2. **LACTATING_60_40** (lactating does, growing doelings, frame-builder kids day 61–90): Green 36, Dry 24, Maize 14, DDGS 4.8, Soya DOC 6, Mustard DOC 10, DORB 4, Mineral 1.2
3. **MAINTENANCE_75_25** (resting dry-off, breeding, early pregnancy, dry bucks): Green 45, Dry 30, Maize 7.5, DDGS 2.5, Soya DOC 3, Mustard DOC 3.75, DORB 7.5, Mineral 0.75
4. **FLUSH_70_30** (3–4 weeks pre-breeding): Green 42, Dry 28, Maize 10.5, DDGS 4.5, Soya DOC 4.5, Mustard DOC 4.5, DORB 5.1, Mineral 0.9
5. **CREEP** (kids weeks 2–8, dry concentrate only): Crushed maize, Soya DOC, DDGS (approx 60/30/10 + mineral) — simple recipe

Feeding schedule: 3× daily, split **40% 6:30 AM / 20% 1:30 PM / 40% 7:30 PM**. Morning shift includes bunk sweeping.

Feed allocation per bucket (drives the feeding page):
- QUARANTINE: dry roughage only (days 1–3) → transition to MAINTENANCE
- FOUNDATION, FEMALE_KIDS, kid frame-builder phase: LACTATING_60_40
- MALE_KIDS: day 61–90 LACTATING_60_40, day 91+ FATTENING_50_50
- BREEDING, PREGNANCY_EARLY, dry bucks: MAINTENANCE_75_25
- PREGNANCY_LATE, RECOVERY (lactating): LACTATING_60_40
- DELIVERY: LACTATING_60_40
- RESTING: days 1–10 MAINTENANCE_75_25, days 10–30 FLUSH_70_30

FeedingRecord: date, shift (MORNING/AFTERNOON/NIGHT), bucket, recipe, qty_kg dispensed.

### FeedInventory
- ingredient name, category, unit (kg), qty_on_hand, reorder_level, last_purchase_price_per_kg
- Purchase entry increases stock; mixing a batch decreases stock per recipe lines.

### Financial
- Transaction: date, type (INCOME | EXPENSE), category (ANIMAL_SALE, ANIMAL_PURCHASE, FEED, MEDICINE, VET, LABOUR, EQUIPMENT, MILK, MANURE, OTHER), amount, related_animal_id (nullable), notes
- Animal sale: picking an animal → mark SOLD, sale price, buyer, date → auto-creates INCOME transaction.

### Task / Alert engine
- Task: title, due_date, status (PENDING | DONE | SKIPPED), animal_id nullable, batch nullable, category (VACCINE, DEWORMING, ULTRASOUND, KIDDING_DUE, WEANING, BUCKET_MOVE, QUARANTINE, FEED, OTHER), auto_generated (bool)
- Auto-generate: ultrasound check (breeding + 32d), expected kidding (breeding + 145..150), pre-kidding ET+TT (due date − 4–6 weeks), move to DELIVERY (due − 15d), weaning (kidding + 60d), resting-end → breeding, vaccination schedule by age, quarantine protocol, deworming every 6 months (June/Jan)
- Dashboard shows today's + overdue tasks; completing a task writes the underlying record (e.g. completing ULTRASOUND opens the ultrasound form).

## Pages / routes (server-rendered Jinja2)
- `/register`, `/login`, `/logout`, farm switcher
- `/` dashboard: herd count by bucket, today's tasks, overdue alerts, kiddings due in 14 days, ultrasounds due, animals ready to move bucket (age/weight/day thresholds), recent events
- `/animals` list w/ filters (bucket, sex, status, search tag), `/animals/new`, `/animals/{id}` profile (full history: weights, breedings, health, moves, kids), move-bucket action, record weight, mark sold/dead/cull
- `/buckets` — board view of all buckets with counts and animals
- `/breeding` — breeding records, add breeding, record ultrasound result, cull-candidate list
- `/kidding` — record kidding (auto-creates kid animals), upcoming due list
- `/health` — health events log, add event (single or batch), vaccination schedule per animal with due dates
- `/purchases` — purchase batches + quarantine protocol tracker (45-day checklist per batch)
- `/feeding` — recipes view, today's 3-shift feeding plan per bucket (qty = per-head ration × headcount × shift %), record dispensing, feed inventory
- `/tasks` — task list, complete/skip
- `/finance` — transactions, simple P&L summary by month/category
- `/reports` — herd summary, breeding performance (conception rate, kids/doe), mortality

## Key computed logic
- `age_months` from DOB or estimated DOB
- Breeding-ready doe: female, ≥10 months, ≥22 kg, not currently pregnant, in FOUNDATION/FEMALE_KIDS/RESTING
- Expected kidding date = breeding_date + 150
- Days-in-bucket computed from last bucket-move timestamp; RESTING day 10+ → feed FLUSH; PREGNANCY_EARLY day 100 (by gestation day) → suggest move to LATE; LATE day 135 → suggest DELIVERY
- Conception rate = confirmed / total completed breedings
- Feeding plan quantity: per-head daily TMR ≈ 3.5% of bodyweight (dry matter ~ but keep simple: configurable per-head kg per bucket, default 1.0–1.5 kg concentrate+roughage per adult); keep it a simple configurable `daily_kg_per_head` setting per bucket, multiplied by headcount and split 40/20/40.

## Seed data
On first run: breed constants, 4+1 feed recipes with lines, vaccination schedule templates, feed inventory ingredients (Super Napier green fodder, dry jowar stover, groundnut haulms, crushed maize, maize DDGS, soya DOC, mustard DOC, DORB, mineral mix), bucket definitions. Demo script `scripts/seed_demo.py` creating a demo farm with ~60 animals across buckets, a purchase batch mid-quarantine, breedings, and kiddings.

## Non-functional
- SQLite file `goatfarm.db` at project root; SQLAlchemy models in `app/models.py`; routers in `app/routers/`; templates in `app/templates/`; static in `app/static/`
- Run with `uvicorn app.main:app --reload`; `requirements.txt`; README with setup
- Tests: pytest covering model logic (expected kidding date, breeding-ready check, conception rate, quarantine schedule generation) — keep to one test file, don't over-test
- Rupees (₹) for money, metric units, dates DD-MM-YYYY in UI

## RBAC / Team (built)

Role-based access control with farm-scoped custom roles, and a digitized
daily-duty workflow.

### Model
- `Role` (per farm): stable `code` for presets (MOVER, VET, CLEANER,
  CLEANER_MANAGER, FEEDER; custom roles have `code=NULL`), name, description,
  `permissions` = JSON list of `module.action` codes (catalog in
  `app/permissions.py`, ~24 codes: `animals.move`, `health.manage`,
  `tasks.verify`, `team.manage`, …).
- `FarmMembership`: user ↔ farm with a role + `is_active`. Farm owner is NOT
  a membership — `Farm.owner_id` implies all permissions.
- `User.name`; `Task` gains assignment (`assigned_role_id` /
  `assigned_user_id`), attribution (`completed_by_id`, `completed_at`,
  `verified_by_id`, `verified_at`, `verification_note`) and `recur_days`;
  `TaskStatus.VERIFIED`, `TaskCategory.CLEANING` added; record tables
  (bucket moves, health, feeding, weights, breedings, kiddings, transactions)
  gain `created_by_id`.

### Rules
- Every route declares `require_perm("module.action")`; owners pass, workers
  need the code in their role. Missing → 403 page; nav hides what the role
  can't open.
- Auto-generated tasks map to preset roles by category (ultrasound/vaccine →
  VET, bucket moves/weaning → MOVER, feed → FEEDER). Manual duties
  (`/tasks/new`, perm `tasks.create`) assign to a role or a worker, optional
  recurrence — completing a recurring duty spawns the next occurrence at
  `due_date + recur_days`.
- Worker task visibility: duties assigned to their role or to them only;
  unassigned duties are owner-only. Verification views (`awaiting`,
  `completed` tabs) are farm-wide for `tasks.verify` holders — a verifier
  reviews other roles' work.
- CLEANING duties: worker completes → DONE ("awaiting verification") →
  verifier confirms (VERIFIED, attributed) or rejects with a note → back to
  PENDING for the worker.
- Owner creates worker accounts from `/team` (min-8-char password), can
  change roles, deactivate, reset passwords. Role edits take effect on the
  next request. Roles with workers can't be deleted until reassigned.
- Worker login: exactly one accessible farm → straight in; more → picker
  (owned farms shown as Owner, memberships with role name). Cross-tenant
  switching is rejected server-side.
- Startup: `migrate()` adds new nullable columns idempotently; role presets
  seeded per farm (existing farms backfilled, edited presets untouched);
  pre-RBAC auto tasks backfilled with their category's role.
- Session cookie `SameSite=Lax` (CSRF posture). Passwords pbkdf2 + min length 8.

Tests: `tests/test_rbac.py` (TestClient, throwaway DB) — per-role 200/403
matrix, tenant isolation, worker lifecycle, verification loop, recurrence,
attribution, password policy.
