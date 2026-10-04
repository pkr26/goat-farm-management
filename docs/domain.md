# Domain model

[Documentation index](README.md) · [Project overview](../README.md)

Herdly's workflows model commercial Osmanabadi meat-goat herds in Telangana. The rules
below describe application behavior and seeded defaults. Veterinary teams should confirm
clinical schedules for the deployment's local program.

## Buckets and production stages

Every animal lives in exactly one **bucket** (pen/stage), and every move is recorded
with a timestamp and reason. Buckets mirror the production cycle:

`QUARANTINE → FOUNDATION → BREEDING → PREGNANCY_EARLY → PREGNANCY_LATE → DELIVERY →
RECOVERY → RESTING → BREEDING …`

Kids branch off at weaning (day 60) into `MALE_KIDS` (sold at 8–9 months, 24–28 kg) and
`FEMALE_KIDS` (grown to breeding-ready). New purchases sit in `QUARANTINE` for a 45-day
protocol (arrival inspection & rest/electrolytes → deworm → PPR → ET+TT → Goat Pox →
fecal exams → FMD → footbath) before joining `FOUNDATION`. The app auto-generates dated
tasks for every transition: ultrasounds, return-to-heat watch, pre-kidding vaccines, the
day-100 PREGNANCY_LATE move, birthing-kit check, the kidding-watch window, bucket moves,
weaning, post-kidding dam care and stall disinfection, and the whole quarantine
schedule.

**Sires are terminal residents of `BREEDING`.** A retained buck is promoted from
`MALE_KIDS` into `BREEDING` (12+ months, 25+ kg) and lives there for his whole working
life — the graph deliberately offers no bucket exit, because does cycle through the pens
while the buck stays with them. "Buck rotation" is a *herd* decision, not a move: rotate
a sire out by selling/culling him and restocking (exactly what the planning engine's
`buck_rotation_years` models); the owner-only history override exists for audited
corrections, and an inbreeding fence refuses parent-offspring and full-sibling pairings.
Seasonal buck separation is not modeled — a farm that needs it should record the
separation in the buck's notes.

**Sick animals never change buckets.** Each bucket building has its own vet area;
treatment is a health event, and a suspected scheduled disease instead freezes the
animal in place with a **movement restriction** (move, breeding, sale and cull all
refuse, fail-closed) until a vet records a referenced clearance. The dashboard's
*Movement restrictions* card is the farm-wide list of currently held animals.

## Duties & verification

- Auto-generated tasks (ultrasound, vaccine, bucket move, weaning, …) are assigned to
  the matching preset role automatically; owners can also create safe manual
  FEED/CLEANING/OTHER duties assigned to a role or a specific worker, with optional
  recurrence (`repeats every N days` — completing one schedules the next). Outstanding
  manual duties are capped per farm (5,000 by default) so a compromised task creator
  cannot grow the queue without bound; completed/skipped history and generated workflow
  duties do not count.
- **Cadence rounds**: the background maintenance worker materializes the farm's
  recurring husbandry calendar; task-board reads do not create duties. The calendar
  includes vaccination rounds by season (FMD Sep/Mar, ET+HS pre-monsoon, Goat Pox Nov,
  CCPP Jan), deworming rounds (Jun/Jan), hoof trimming and ectoparasite spraying
  (6-monthly), shed disinfection (quarterly), the monthly weighing round, the daily
  morning water/bunk routine, feed-reorder alerts when stock drops under an ingredient's
  reorder level, and buck-rotation reminders at 36 months. Herd-level VACCINE/DEWORMING
  rounds declare their target animals and required components. Recording a health event
  starts a round; completion requires coverage for every required animal/component or an
  attributed exclusion. One bucket or one vaccine cannot close an incomplete
  multi-bucket round.
- **Restart-safe maintenance**: cadence and hourly alert scans advance bounded keyset
  pages using PostgreSQL checkpoints. A separate transaction excludes concurrent
  schedulers while farm work commits independently. Only a finished page advances the
  cursor; crashes repeat idempotent work. An hourly scan is marked complete only after
  exhausting its farm rotation.
- Workers see only duties assigned to their role or to them; completing a duty stamps
  `completed_by`/`completed_at` — who did what is recorded.
- **Cleaning verification loop**: CLEANING duties marked done wait in the *Awaiting
  verification* tab until a `tasks.verify` holder **verifies** them (→ VERIFIED,
  attributed) or **rejects** them with a note, which sends them back to the worker's
  list. Someone other than the completer must verify (the farm owner is exempt).
- Form-linked duties (ultrasound, kidding, vaccines) can only be completed through their
  record forms, not the task list's complete button.
- Key records (bucket moves, health events, feeding, weights, breedings, kiddings,
  transactions) carry `created_by_id` for attribution.

## Application husbandry defaults

- Gestation **150 days** (kidding window 145–155); ultrasound scan at breeding + 32
  days; heat cycle 21 days. Kidding records are accepted only within 100–200 days of the
  breeding date.
- Breeding-ready doe: female, ≥12 months, ≥22 kg, not pregnant, in FOUNDATION /
  FEMALE_KIDS / RESTING. Two consecutive failed cycles → cull candidate.
- Weaning at day 60 (doe → RESTING; kids → MALE_KIDS / FEMALE_KIDS by sex). A doe must
  complete the 10-day RESTING dry-off/flush window before re-entering BREEDING (enforced
  on both the manual move and the service); her re-breed prompt arrives 30 days after
  weaning.
- Kidding care: the kidding form records colostrum-within-2 h, navel-dip and
  dam-rejection per kid, plus placenta passage, mastitis suspicion and derived parity;
  kidding spawns next-day post-kidding dam-check and stall-disinfection duties (and a
  bottle/colostrum-support duty when a kid missed colostrum or was rejected).
- Arrivals: purchase batches capture origin market, transport hours and the seller's
  health history, and accept per-head arrival weights; deaths carry a coded cause,
  disposal method and necropsy findings; sales capture weight-at-sale, ₹/kg (price
  derivable from weight × rate) and buyer.
- Feeding: TMR per bucket, 3 shifts split **40% (6:30 AM, sweep bunks) / 20% (1:30 PM) /
  40% (7:30 PM)**. Per-head amounts scale from the bucket's mean recorded weight (3–4%
  by class, clamped to 0.5–2× the flat default; flat fallback when no weights exist);
  breeding bucks carry a +0.5 kg supplement line. RESTING switches MAINTENANCE_75_25 →
  FLUSH_70_30 at day 10; MALE_KIDS switch LACTATING_60_40 → FATTENING_50_50 at day 91;
  creep feed ramps 0.1/0.2/0.3 kg by age band from day 14.
- Core vaccines: FMD (6-monthly, Sep/Mar), PPR (annual app default; confirm the local
  veterinary/public-health programme before deployment), ET (annual, pre-monsoon), HS
  (first dose 6 months), Goat Pox, TT, plus pre-kidding ET+TT 4–6 weeks before due date.
  Deworming every 6 months (June/January). Finance tracks a per-animal insurance
  register (renewal duties spawn 30 days ahead), a lifetime per-animal P&L, and a
  feed-stock memo value.

## Farm-local dates

Domain instants are stored as naive UTC datetimes; scheduler checkpoints use
timezone-aware PostgreSQL timestamps. Date-only business rules use the active farm's
IANA timezone (default `Asia/Kolkata`), so dashboards, due dates, feeding plans, and
daily records change day at the farm's midnight. Existing date validation retains one
day of clock-skew headroom for clients; genuinely future dates are still rejected.
