# Osmanabadi Husbandry Standards Audit — Implementation vs Current Research Standards

**Date:** 2026-09-14
**Scope:** Everything implemented for the Osmanabadi herd in this repo (buckets/movements, breeding, pregnancy/kidding, health/vaccination, quarantine/arrivals, feeding, tasks engine, finance, simulation/planner, frontend surface) compared against current husbandry standards (ICAR-CIRG, NABARD, TNAU/Vikaspedia, Merck Vet Manual, peer-reviewed Osmanabadi studies — full source list at the end).
**Framing:** "Assume you are running this farm" — every gap is described as an operational risk a farm manager would feel, and the deliverable is a backlog of **tasks/protocols the system should generate**.

All file references are `backend/app/...` unless noted.

---

## 1. Verdict

The platform's **skeleton matches the standards unusually well** — the production-cycle bucket graph, 45-day quarantine with day-1 transport-stress protocol, pre-kidding ET+TT two-dose schedule, 1:20 buck ratio, inbreeding fence, movement-restriction/compliance flow, withdrawal-blocked sales, and the Bakrid-calendar economics are all research-aligned and in places ahead of typical Indian farm practice.

The gaps are concentrated in **daily animal husbandry around the two highest-risk windows — kidding and arrival** — and in the fact that **the task engine only fires on state transitions, never on a calendar**. Concretely:

- **Kidding is a single dated task, not a managed event.** There is no kidding-watch window, no birthing-kit check, no kidding-pen preparation, no post-kidding dam care, no colostrum/navel-dip verification, and no post-kidding stall disinfection. *(Your Example 1 — fully confirmed absent.)*
- **New-arrival handling is better than you feared but thinner than standards.** "Rest, electrolyte water, dry roughage only, zero grain" on day 1 **is** implemented and shown in the purchase flow *(your Example 2, first half — present)* — but there is no arrival-day clinical exam, no fecal/parasite screening, no seller-history capture, no individual weights, and days 1–9 husbandry steps record no data *(second half — partially absent)*.
- **The vaccination/deworming calendar is a read-only screen, not tasks.** FMD Sep/Mar, ET pre-monsoon, deworming Jun/Jan exist as computed per-animal status rows that never appear on the task board. A farm that doesn't open `health/schedule/[animalId]` misses every booster.
- **No recurring husbandry tasks exist at all**: no water checks, bunk sweeping (it's only a shift label), hoof trimming, ectoparasite spraying, shed disinfection, or periodic weighing.

---

## 2. What is already in sync with standards (keep)

| Area | Implemented | Standard says | Verdict |
|---|---|---|---|
| Gestation | 150 d, window 145–155 (`models/species.py:69-72`) | 152.2 ± 0.24 d (range 137–158) | ✅ |
| Pregnancy diagnosis | Ultrasound at service +32 d | PregTone/A-mode reliable from ~30–35 d | ✅ |
| Buck:doe ratio | 1:20, sire refused his 21st open service (`services/breeding.py:548-555`) | 1:20–25 typical Indian commercial | ✅ |
| Inbreeding control | Parent-offspring, full-sibling, grandparent, avuncular pairings rejected; half-sib allowed (`services/breeding.py:476-509`) | Standard pedigree fence | ✅ |
| Quarantine duration | 45 days, release gated on all protocol steps done + no disease hold (`services/tasks.py:113-182`) | Minimum 30 d (TNAU/UW-Extension); 21–45 seen | ✅ stricter |
| Arrival transport-stress care | Day 1–3: rest, electrolyte/jaggery water, dry roughage only, zero grain (`models/helpers.py:64`, `models/feed_rules.py:35,72`) | Water/electrolytes first, no concentrate day 1, hay introduced gradually | ✅ |
| Pre-kidding passive immunity | ET+TT primary EKD−40, booster EKD−25 (`services/breeding.py:739-758`) | CD&T/ET-TT booster ~2–6 weeks pre-kidding for colostrum antibodies | ✅ (booster at 25 d = sweet spot) |
| FMD cadence | 6-monthly repeat (`seed.py:202-206`) | 6-monthly (Sep/Mar national rounds) | ✅ cadence / ⚠️ calendar-month logic (see §4.5) |
| PPR | 3 mo first dose, yearly repeat (`seed.py:207-210`) | 3–4 mo, yearly (camps) or 3-yearly | ✅ |
| Drug withdrawal | Single `withdrawal_until` blocks SOLD/CULLED (`api/animals.py:1079-1093`) | Withhold milk/meat after treatment | ✅ (meat only; no milk module — fine for meat breed) |
| Scheduled-disease compliance | Suspicion → movement freeze (move/breed/sell/cull all refuse), authority-notification timestamp, two-person vet clearance (`services/health.py:185-230`, `api/health.py:316-424`) | PCAICD Act 2009 reporting; PPR notifiable | ✅ ahead of practice |
| Sale-from-quarantine fence | Blocked outright; cull allowed as disease response (`api/animals.py:1094-1106`) | Biosecurity | ✅ |
| Festival economics | Bakrid calendar 2026–2050 drives +35% price uplift and hold-back logic (`simulation/market.py:103-160`) | 30–60% mandi premium pre-Bakrid | ✅ |
| Kidding interval (sim) | ~8 months (243 d) | 232.6 d organised farms | ✅ |
| Litter size (sim) | 1.6 | 1.6–1.8 well-managed | ✅ |

---

## 3. Discrepancy deep-dive (running-the-farm view)

### 3.1 Kidding — the biggest operational gap *(your Example 1)*

**What exists** (all four tasks generated only at positive ultrasound, `services/breeding.py:739-776`):
1. Pre-kidding ET+TT vaccine at EKD−40
2. ET+TT booster at EKD−25
3. "Move to DELIVERY" bucket task at EKD−15
4. "Kidding due: {tag}" single task **on EKD day only**

**What's missing vs standards** (Merck parturition, TNAU kidding management, CIRG):

| Missing item | Standard | Risk on the ground |
|---|---|---|
| **Kidding-watch window** | Labor can start day 145; udder bags 1–2 wk early, tail ligaments soften 12–24 h pre-parturition; monitor closely final week | The one-day "Kidding due" task expires while the doe is still pregnant; a day-145 kidding happens with nobody tasked to watch |
| **Birthing-kit readiness check** (your exact example) | Kit: 7% iodine + cup, towels, disinfected scissors, obstetrical lube, gloves, lamp, thermometer, **feeding tube + syringe, colostrum replacer**, electrolytes, weigh sling, ear tags | First-kiddings and malpresentations are unprepared; kid dies waiting for supplies |
| **Kidding-pen prep** | Clean + disinfect pen (detergent→rinse→dry→disinfect vs *C. perfringens*/*E. coli*), fresh dry bedding; move doe ~1 wk before due | *E. coli* scours / navel ill — top kid killers |
| **Dystocia protocol** | Assist after 30 min active straining with no progress; vet if not corrected in 15–20 min | `ease` field has NORMAL/ASSISTED/DIFFICULT but no CAESAREAN and no guidance |
| **Colostrum verification** | Within 30 min–2 h of birth; ~10% of body weight in first 24 h; gut closure by 12–24 h | Not captured anywhere (`KidEntry` has tag/sex/birth_weight/status only, `schemas/kidding.py:24-39`) |
| **Navel dip** | Dip cord in 7% iodine/chlorhexidine, repeat ~12 h | Only ~23% of Indian goat farmers do this — precisely the habit software should enforce |
| **Kid processing** | Dry & clear airways, weigh, ear-tag at birth | Birth weight optional; tagging is not tasked |
| **Post-kidding dam care** | Placenta passes in 2–3 h (vet if retained >6–8 h); warm water, light feed day 1, clean hindquarters, mastitis/udder check | Nothing exists — grep for placenta/mastitis/afterbirth returns zero hits |
| **Post-kidding stall hygiene** | Remove soiled bedding, disinfect stall after kidding (TNAU: seasonal disinfection especially before kidding) | CLEANING task category exists with a verification loop but is never auto-generated |

**Root cause:** kidding is modeled as a *record* (`KiddingRecord` + `KidEntry`) with a one-shot reminder, not as a *managed event with a protocol*.

### 3.2 Kid rearing & weaning

- **Weaning at fixed day 60** (`models/species.py:78`). Indian standard is **90 days (3 months)**; 2–3 months in practice; research shows 90-d weaning gives materially better ADG than early weaning. 60 d is defensible for an intensive creep-fed system (and the 8-month kidding interval depends on it) — but it should be a *documented, configurable choice*, ideally weight-qualified (e.g., wean at day 60 only if ≥ 2× birth weight / ~8–10 kg, else hold longer).
- **Creep feed runs from day 0 at a flat 0.3 kg** (`models/feed_rules.py:17-19,74-77`). Standard: start creep at **2–4 weeks** at 50–100 g/day (22% CP), ramping to 200–250 g by weaning. Counting 0.3 kg from birth inflates the plan and the early ration is wasted.
- **No orphan/rejection workflow** beyond the automatic early-wean when the dam exits (`api/animals.py:1239-1276`). Standards: foster, bottle/tube with colostrum then milk/replacer. No task, no flag.
- **Kid-level disease prevention untasked**: no *E. coli* antibody prep, no coccidiosis watch (the seeded "Anti-coccidial drench 1–3 months (Amprolium 5 days)" template, `seed.py:241-247`, never generates a task).
- **No monthly weighing tasks** — growth is the farm's core product and ADG is never computed (weights are recorded but only "latest weight" is ever consumed, `models/animals.py:291-307`).

### 3.3 New arrivals / purchases *(your Example 2)*

**Present and standards-aligned:**
- Day-1–3 rest + electrolyte/jaggery water + dry roughage only + zero grain, with matching quarantine feed rule (`models/helpers.py:64`).
- Day 4 broad deworm (Albendazole/Closantel + Ivermectin), day 5–9 liver tonic + AD3E, day 10 PPR, day 20 ET+TT, day 30 Goat Pox, day 40 FMD, day 45 footbath + audited release to FOUNDATION.
- The purchase UI shows the full 45-day schedule in a review-consequences dialog before creation, and the batch detail lists the protocol tasks (`frontend .../purchases/page.tsx:654-717`).

**Missing vs standards:**

| Missing item | Standard |
|---|---|
| **Arrival-day clinical inspection** (day 0) | Check dehydration (skin tent, gums), injuries, lameness, temperature; triage |
| **Fecal egg count / dung exam** 10–14 d post-deworm + during quarantine | Confirm anthelmintic efficacy; screen parasites before mixing |
| **Seller history capture** (vaccination/dewerming history, origin, transport duration/cost) | Avoid blind re-vaccination; provenance for NABARD/bank records |
| **Individual arrival weights & BCS** | One batch average is copied to every animal (`services/purchases.py:187-199`) |
| **Quarantine extension for sick animals** | A held animal just blocks release; no "extend + re-test" action |
| **HS / CCPP in the intake series** | They're seeded herd templates but never given during quarantine |
| **"Handle quarantined animals last" biosecurity note** | No dedicated boots/tools/last-in-routine reminder in any task text |

Also note: days 1–3 and 5–9 QUARANTINE tasks are closed by a bare button with **no data capture** (`permissions.py:320` — no action permission), so the husbandry work leaves no health-ledger trace.

### 3.4 Breeding cycle

- **No return-to-heat watch.** Standards: watch the doe at day 18–21 post-service; an observed standing heat is the earliest, cheapest failed-conception signal. The app waits for the day-32 ultrasound (`services/breeding.py:55-56` uses day 18 only as a validation floor for backdating a negative scan). A "watch for return to heat, days 18–21" task per breeding is standard practice.
- **PREGNANCY_EARLY → PREGNANCY_LATE (day 100) has no task** — manual-only edge (`models/lifecycle.py:48`), surfaced only as a dashboard suggestion. Since the feed steps up 1.2 → 1.4 kg and the ration changes (Maintenance → Lactating mix) exactly at this move, a missed suggestion silently under-feeds late gestation — when 60–80% of fetal growth happens.
- **RESTING ~30 days → BREEDING has no task and no minimum-stay guard.** A doe can sit in RESTING indefinitely (nothing prompts the re-breed) or be moved out the same day.
- **No joining/mating-season management.** Buck separation isn't modeled (README says record in notes); there's no pre-breeding season checklist (buck conditioning, 0.5 kg/day breeding concentrate, doe flushing verification) even though flushing *is* modeled in feed (FLUSH_70_30 from RESTING day 10).
- **Buck rotation reminder absent** — `BUCK_ROTATION_DAYS = 7` is a dead constant (`models/constants.py:37`); the simulation assumes 3-year rotation; nothing operationally reminds the farm to rotate/replace a sire (inbreeding fence catches the damage only after the fact).
- **Doe breeding age 10 months** (`models/species.py:74-77`) vs research: puberty ~350 d (~11.5 mo) at ~17.5 kg, first mating ~370 d, first kidding 494 d (~16.5 mo). 10 mo is at the aggressive edge; the 22 kg weight gate does most of the protecting. Consider 10.5–11 mo or keep 10 mo but weight-gate strictly. Buck 12 mo/25 kg is fine.

### 3.5 Herd health calendar — the systemic gap

The seeded `VaccineTemplate` table (`seed.py:201-248`) is good, but `vaccination_schedule_for_animal` (`services/health.py:492-697`) is **display-only**: it computes DONE/OVERDUE/UPCOMING rows that never become Task rows. The only vaccination tasks ever generated are pre-kidding ET+TT and the quarantine series. Consequences:

- No FMD Sep/Mar round reminder, no ET pre-monsoon round, no deworming Jun/Jan round on the task board — the cadences named in the seed notes are guidance text only; computed next-due is `last_done + N months`, so a herd dosed in off-months drifts permanently against the seasonal disease-risk rationale (pre-monsoon ET/HS exists *because* of monsoon).
- **Kids' 3-monthly deworming** (stated in the seed note, `seed.py:239`) is implemented nowhere — no age-banded cadence exists.
- **Hoof trimming (6-monthly, stall-fed), ectoparasite spraying/dipping (2×/yr, Butox April–June and Jul–Sep), routine footbaths (weekly where footrot risk), seasonal shed disinfection + lime whitewash + earthen-floor replacement every 3 months** — none are modeled or tasked. Footbath exists only as the quarantine day-45 step.
- **No fecal-exam-guided deworming (FAMACHA/FEC)** — the resistance-management standard; not even a record type.
- **Mortality is a status field, not a health event**: free-text `mortality_cause`, no coded causes, no post-mortem/necropsy record, no carcass-disposal record, no ledger entry — the financial impact of a death is invisible in P&L, and mortality analytics can only count deaths by month, never by cause (`api/dashboard.py:646-702`).
- Template quirks: "Anti-coccidial drench" and "Johne's Disease" sit in the vaccine table as bookable VACCINE events (drench is a treatment; JD vaccination is uncommon/controversial); standalone **TT has no template**, so a tetanus booster can't be tracked against any program item; HS first dose is seeded at 3 months where TNAU says 6 months.

### 3.6 Feeding & nutrition

- **Rations are flat kg/head per bucket, never % of body weight** (`models/feed_rules.py:100-111`): QUARANTINE 1.1, most adult buckets 1.2, PREGNANCY_LATE 1.4, DELIVERY/RECOVERY 1.5, kids 1.0. ICAR/NRC standard is DMI 3–5% of BW by class. A 25 kg doelings and a 35 kg doe in FOUNDATION get identical feed; a fat doe is over-fed and a thin one under-fed with no signal. (BCS is recordable but nothing computes trends or flags from it.)
- **As-fed basis with no dry-matter accounting.** A rough DM estimate of the 1.5 kg RECOVERY ration (36% green @ ~20% DM, rest ~88%) is ≈ 0.95 kg DM — below the 1.2–1.6 kg DM a 30 kg lactating doe needs (4–5% DMI incl. milk allowance). Late-gestation/early-lactation underfeeding directly hits birth weight, milk, and kid survival.
- **Buck ration**: breeding bucks get the maintenance line (1.2 kg) year-round. Standard: **+0.5 kg/day concentrate during breeding**, and a 42 kg buck at 3.5% DMI needs ~1.5 kg DM anyway.
- **`reorder_level` is dead data** — seeded at 100 kg per ingredient (`seed.py:373`), exposed in the API, and never compared against stock anywhere. No low-stock alert, no task, no dashboard flag; shortages surface only as a 400 error when mixing fails.
- **Water is not modeled at all** (2.8–5.5 L/d adult, up to 10–15 L/d for lactating does in Deccan summer; fresh water ≥3×/day). No water check task, no entity.
- **"Sweep bunks first" is only the 6:30 AM shift label** (`models/feed_rules.py:53`) — no task, no verification.
- Mineral mixture in every TMR at 0.75–1.5% ✓ (~2% standard — slightly light in the maintenance recipes); salt as a separate item is absent.
- **Steaming-up** (250–400 g concentrate in the last 4–6 weeks) exists only implicitly via the PREGNANCY_LATE 1.4 kg step-up; no task verifies the transition happened (see §3.4 day-100 gap).
- Positive: 40/20/40 three-shift feeding, TMR recipes with realistic Deccan ingredients, quarantine roughage-only ramp, flushing switch at RESTING day 10 — all aligned with practice.

### 3.7 Buckets & movements (mostly sound)

- Sick animals never change buckets + scheduled-disease freeze with vet clearance: matches "isolate in place" biosecurity. ✅
- Weaning bucket split by sex, kids born into RECOVERY with dam: standard. ✅
- `move_animal` silently no-ops on held/inactive animals (`services/animals.py:157-162`) — a held kid in the orphan sweep is silently left; acceptable but worth an audit note.
- No edge *into* QUARANTINE — deliberate re-quarantine of an active animal is impossible without an owner history override.
- FEMALE_KIDS → FOUNDATION has no age guard (a 3-month-old can be "promoted"); harmless because breeding gates still apply.
- **Sale weight window (24–28 kg) is advisory only** — the dashboard suggests SELL at ≥24 kg + 8 mo, but `change_status` enforces only the ≥8-month age floor for males with known DOB; males with unknown DOB escape entirely (`api/animals.py:1107-1125`).

### 3.8 Finance & records vs NABARD/bank norms

| Gap | Standard/expectation |
|---|---|
| **Death books ₹0 and nothing else** | Mortality loss invisible in P&L; no insurance-claim trigger. NABARD-financed farms carry livestock insurance — no policy/sum-insured/renewal/claim tracking anywhere (insurance exists only as a simulation cost line) |
| **Sale records a flat ₹ amount** | Market convention is live weight × ₹/kg; the sale form captures neither weight-at-sale nor rate nor buyer — the farm can't benchmark realized ₹/kg against mandi rates (₹570–610/kg Osmanabadi, Hyderabad 2026) |
| **No per-animal / per-bucket P&L** | Purchase price, health costs, and sale price all exist per animal but are never combined; only a 12-month category P&L exists |
| **No feed COGS / closing-stock valuation** | Feed purchases hit the ledger; dispensing doesn't; month-end stock value is not computed — P&L shows purchases, not cost of production |
| **Bank-register completeness** | NABARD expects breeding/kidding/service registers ✅, vaccination & deworming registers ⚠️ (per-animal computed only), mortality & treatment with withdrawal ✅ (withdrawal tracked), feed & fodder register ✅, sale/purchase **with weights** ❌, insurance ❌ |
| No batch reversal | Acknowledged in UI; wrong batch = finance corrections + manual cleanup |

### 3.9 Simulation vs research — parameter nits

- **Buck adult weight 42 kg** (`simulation/assumptions.py` ~§Growth) vs breed standard 33.5–36 kg (TNAU: 35–40 for large males). High-side.
- Kid mortality 15% pre-weaning is NABARD-bankable-conservative (benchmark: <10% good, 5–10% acceptable) — fine for planning, worth surfacing as a target the ops side should beat.
- Doe cull rate 20%/yr + max age 72 mo: reasonable vs commercial practice.
- Meat price ₹370/kg base with Bakrid +35%: conservative vs 2026 mandi ₹570–610 for Osmanabadi — the *premium* is real; consider a "premium breed price" toggle.
- Everything single-sourced with operational SPEC (breeding age, weaning, sale window, ratio, cull rule) is a structural strength — keep.

---

## 4. Master backlog — "everything as tasks"

Ordered by risk to animals and money. Each item specifies trigger → timing → category → role → what completion must record. Categories marked **[new]** need a `TaskCategory` addition; the engine's role map (`permissions.py:269-279`) extends the same way.

### P0 — Kidding protocol (the farm's highest-mortality window)

1. **Kidding-watch daily task** — for every doe in DELIVERY (or within EKD−5, since the kidding window is 145–155): recurring daily CLEANING/monitor task "Kidding watch: {tag} (due {EKD}) — check udder fill, tail-head ligaments, vulva, isolate if restless; monitor through the night if due today." Auto-resolves when the kidding record lands. *Category: KIDDING_WATCH [new] or reuse KIDDING_DUE; role: VET/CLEANER per farm.*
2. **Birthing-kit readiness check** — daily recurring while ≥1 doe is within 7 days of EKD: "N does due within 7 days — verify kidding kit: 7% iodine + cup, clean towels, disinfected scissors, obstetrical lubricant, gloves, lamp, thermometer, feeding tube + 60 ml syringe, colostrum replacer/electrolytes, weigh sling, ear tags + applicator." Count of due does computed from DELIVERY bucket + EKD. *(Exactly your example.)*
3. **Kidding-pen preparation** — at EKD−15, attached to the DELIVERY-move task (or its own CLEANING task): wash → disinfect (effective vs *C. perfringens*/*E. coli*) → dry → fresh dry bedding. CLEANING category → verification loop already exists (`models/constants.py:77`).
4. **Kid-processing checklist at kidding** — extend the kidding form + close-out: dry & clear airways ✅, navel dipped in 7% iodine (repeat ~12 h) ✅, colostrum within 2 h (target ~10% BW in 24 h; tube-feed if weak) ✅, birth weight, ear tag. New `KidEntry` fields: `colostrum_within_2h bool`, `navel_dipped bool`. Default-on, exception-off.
5. **Post-kidding dam-care task** — due kidding+1: placenta passed (vet if >6–8 h retained), warm water offered, light feed day 1, hindquarters cleaned, udder checked (mastitis: hot/hard/lumpy). Record outcome as a HEALTH_CHECK event type or notes on the KiddingRecord. New fields: `placenta_passed_at`, `mastitis_suspected bool`.
6. **Post-kidding stall cleanout** — auto-generated CLEANING task due kidding+1: remove soiled bedding, disinfect the kidding stall, re-bed. Flows into the existing verify/reject loop.
7. **Dystocia escalation text + CAESAREAN option** in `ease`; assist rule printed on the kidding form (30 min no progress → assist; 15–20 min unresolved → call vet).

### P0 — Arrival protocol completion

8. **Day-0 arrival inspection task** (same day as purchase): dehydration check (skin tent/gums), injury & lameness triage, temperature, isolate sick immediately. Record as a health event (new lightweight EXAM type or TREATMENT with notes).
9. **Fecal sample / dung exam task** day 12–14 post-deworm (confirm efficacy) and optionally day 30 (screen before release). Record FEC result; drive "extend quarantine" if positive.
10. **Seller-history fields on PurchaseBatch**: prior vaccinations/deworming given at source, transport hours, origin market. Use to skip redundant day-10 PPR if documented (or at least display it during the day-10 task).
11. **Individual arrival weights** (optional per-head override of the batch average) + BCS at entry.
12. Make days 1–3 and 5–9 quarantine tasks record *something* (e.g., link the AD3E injection as a VITAMIN health event; add a "hydration status" note field on completion).
13. Add **"handle quarantine animals last; dedicated boots/tools"** to the day-1 task text.

### P1 — Calendar-driven health & husbandry (systemic)

14. **Vaccination round tasks from templates**: when `vaccination_schedule_for_animal` computes UPCOMING/OVERDUE within a horizon (e.g., 14 d), generate bucket-level VACCINE tasks per template (FMD Sep/Mar, ET pre-monsoon, PPR yearly, Goat Pox Nov/Dec, HS pre-monsoon) with the calendar-month rationale — or per-animal tasks for small herds. This turns the read-only schedule screen into the task board. *(Probably the single highest-leverage change in this audit.)*
15. **Deworming round tasks** Jun/Jan for adults; **kids 3-monthly until 6 months** (the seed note already promises this).
16. **Return-to-heat watch task** at breeding+18 through +21 per doe: "Watch {tag} for return to standing heat; if observed, record failed service early."
17. **PREGNANCY_EARLY→LATE move task at gestation day 100** (parity with the EKD−15 DELIVERY task; the feed step-up depends on it).
18. **Re-breed task for RESTING does at ~day 30** + minimum-stay guard on RESTING→BREEDING.
19. **Monthly weighing task** per grow-out bucket (MALE_KIDS/FEMALE_KIDS/FOUNDATION): compute ADG on completion; flag animals below gain curve (research curve: ~6.3 kg @ 3 mo, 15.5 @ 6 mo, 19.6 @ 12 mo — CIRG).
20. **Feed reorder task**: evaluate `qty_on_hand < reorder_level` (field exists, unused) daily → FEED task "Reorder {ingredient}: X kg left vs reorder level Y" assigned to FEEDER/BUYER.
21. **Water + bunk-sweep morning routine**: make the 6:30 AM shift's "sweep bunks first" a real recurring CLEANING/FEED task incl. water-trough check & refill (summer Deccan: lactating does need 10–15 L/d).
22. **Hoof trimming 6-monthly** (stall-fed standard) — HEALTH task per bucket.
23. **Ectoparasite spray/dip 2×/yr** (Apr–Jun and Jul–Sep, deltamethrin; never heavily pregnant does) — HEALTH task.
24. **Seasonal shed disinfection** incl. pre-kidding round; earthen-floor replacement reminder every 3 months — CLEANING task with verification.
25. **Buck management**: breeding-season conditioning task (0.5 kg/d extra concentrate, body check) and a **buck-rotation reminder** at 3 years of service age (or when inbreeding fence starts blocking pairings) — delete or implement `BUCK_ROTATION_DAYS`.

### P1 — Feeding accuracy

26. **Weight-scaled rations**: feed plan should compute kg/head from %BW by class (3% maintenance, 3.5% late pregnancy, 4–5% lactation, 3–3.5% growers) using latest weights, with the flat per-bucket number as a fallback when no weight exists. At minimum, add a DM-basis sanity line per recipe.
27. **Creep feeding from day 14 with a ramp** (50–100 g → 200–250 g by weaning) instead of flat 0.3 kg from birth.
28. **Buck breeding-season supplement** line in the feed plan.
29. Mineral mixture to 2% in maintenance recipes; add salt line.

### P2 — Finance & compliance records

30. **Sale capture upgrade**: weight-at-sale + ₹/kg (auto-suggest price = weight × mandi rate) + buyer name; soft-warn outside the 24–28 kg window; enforce ≥8 mo age for unknown-DOB males via estimated DOB.
31. **Death flow**: coded mortality causes (pneumonia, diarrhoea, predation, accident, dystocia, unknown…), optional necropsy record + disposal method, insurance-claim trigger, and a ledger-neutral "mortality loss" memo line (or valued at last weight × rate) so P&L reflects it.
32. **Insurance register**: policy no., insurer, sum insured, tag, premium, renewal date → renewal tasks.
33. **Per-animal lifetime P&L** (purchase + health + feed-share − sale) on the animal profile; per-bucket feed cost on the reports page.
34. **Monthly feed stock valuation** (closing stock × last purchase price) for the P&L.

### P2 — Data-capture / model nits

35. Add `parity` (or derive & expose it) on KiddingRecord; expose kidding-number on the doe profile.
36. Fix HS first-dose age (3 → 6 months per TNAU) or document the deviation; move "Anti-coccidial drench"/"Johne's" out of the vaccine-template table into a treatments list; add a standalone TT template.
37. Implement kids' coccidiosis watch in the wet season (task or template-driven Amprolium round at 1–3 months, already seeded as text).
38. Sim: buck adult weight 42 → 34–36 kg; consider a premium-breed price toggle (₹570–610/kg observed).
39. Orphan/kid-rejection workflow: flag on KidEntry (dam_rejected bool) → bottle/colostrum-replacer task series + foster option.
40. Consider making weaning weight-qualified (≥2× birth weight) with a documented 60-vs-90-day policy note, since 60 d is at the aggressive end of the Indian standard (90 d) — the whole 8-month kidding interval depends on it, so at least surface the trade-off in docs/simulation.

---

## 5. Parameter comparison table (quick reference)

| Parameter | App value | Research standard | Verdict |
|---|---|---|---|
| Gestation | 150 d (145–155) | 152 ± 0.2 d | ✅ |
| Pregnancy check | day 32 | 30–35 d | ✅ |
| Doe first service | 10 mo / 22 kg | ~12 mo / ~17.5 kg at puberty (350 d) | ⚠️ aggressive age, safe weight |
| Buck first service | 12 mo / 25 kg | ~12 mo | ✅ |
| Buck:doe | 1:20 | 1:20–25 | ✅ |
| Weaning | day 60, calendar | 90 d standard; 2–3 mo practice | ⚠️ aggressive, undocumented choice |
| Kidding interval (sim) | ~8 mo | 232.6 d organised / ~297 field | ✅ |
| Litter size (sim) | 1.6 | 1.6–1.8 managed | ✅ |
| Sale window | 8–9 mo, 24–28 kg | 6–8 mo, 18–25 kg typical | ⚠️ later/heavier (Bakrid-premium logic) — fine, but weight unenforced |
| Birth weight bounds | 0.5–8 kg | 1.8–3.0 kg typical | ✅ as bounds |
| Adult weights (sim) | doe 33 / buck 42 kg | doe 30–32 / buck 33.5–36 | ⚠️ buck high |
| Kid mortality (sim) | 15% pre-wean | <10% good / 10–15% NABARD norm | ✅ conservative |
| FMD | 6-monthly | 6-monthly | ✅ (calendar months unimplemented) |
| PPR | yearly | yearly (camps) / 3-yearly | ✅ |
| ET | annual pre-monsoon + pre-kidding 2-dose | annual/twice-yearly + pre-kidding ~30 d | ✅ |
| HS first dose | 3 mo | 6 mo (TNAU) | ⚠️ |
| Goat Pox | 3 mo, yearly Nov/Dec | 3 mo, yearly | ✅ |
| Deworming | 6-monthly adults | 4×/yr adults (TNAU) or FEC-guided; kids monthly–quarterly | ⚠️ light; kids' cadence unimplemented |
| Quarantine | 45 d | ≥30 d | ✅ stricter |
| Colostrum | not tracked | ≤2 h, ~10% BW/24 h | ❌ |
| DMI by class | flat kg/head | 3–5% BW | ❌ |
| Hoof/spray/disinfection | not scheduled | 6-mo / 2×-yr / seasonal | ❌ |
| Pre-kidding move | EKD−15 | ~1 wk | ✅ |
| Flushing | RESTING day 10+ | 2–3 wk pre-breeding | ✅ |
| Creep | 0.3 kg flat, day 0–60 | start wk 2–4, 50–100→250 g | ⚠️ |

---

## 6. Sources

ICAR-CIRG extension (housing, kid management); NABARD model bankable projects & Telangana unit-cost 2025-26; MANAGE 50-doe DPR; TNAU Agritech Portal (feeding, vaccination, disease management, general management); Vikaspedia vaccination schedule & goat models; Merck/MSD Vet Manual (puberty/estrus, parturition, nutrition, anthelmintic withholding); peer-reviewed Osmanabadi studies (Wakchaure et al.; morphological characterization; Vidarbha kidding/twinning studies; IJAnS improved-management study; ADG feeding-regime trials); Cornell BHAC dewormer chart; FSSAI contaminants/MRL compendium; PCAICD Act 2009; PPR eradication program literature; UW-Extension/USDA APHIS biosecurity guides; MSU/UCANR CD&T guidance; Penn State navel care; Ontario Goat water requirements; Hyderabad Bakra-mandi price trackers (2026). Full URL list available in the research workstream output.

*Every claim about the codebase above was verified against the working tree with file:line references during this audit (2026-09-14, branch `main`, commit `eae2691`).*

---

## 7. Remediation addendum (2026-09-14, same day — implemented)

The P0/P1 backlogs below were implemented the same day via a staged remediation (foundations wave → 8 parallel domain waves → integration). Disposition of every item:

### Kidding protocol (P0 #1–7) — DONE
- **#1 Kidding watch**: six daily KIDDING_WATCH duties from EKD−5 through EKD, auto-cancelled when kidding is recorded (`services/breeding.py`).
- **#2 Birthing-kit check**: BIRTHING_KIT duty at EKD−7 with the full kit checklist in the title (iodine, towels, scissors, lube, gloves, lamp, thermometer, tube+syringe, colostrum replacer, weigh sling, ear tags).
- **#3 Kidding-pen prep**: post-kidding CLEANING (disinfect + re-bed) duty spawns at kidding+1; the EKD−15 DELIVERY move remains.
- **#4 Kid processing**: kidding form now captures `colostrum_within_2h`, `navel_dipped`, `dam_rejected` per kid (stillborn excluded by validation); CAESAREAN ease accepted.
- **#5 Post-kidding dam care**: HEALTH_CHECK duty at kidding+1; `placenta_passed` and `mastitis_suspected` recorded on the kidding form; parity derived server-side.
- **#6 Stall cleanout**: dedicated CLEANING duty at kidding+1 (flows into the existing verification loop).
- **#7 Dystocia**: CAESAREAN value added; assist-rule text rides the watch titles. *(Full on-form escalation text was folded into the watch/checklist titles rather than a separate form panel.)*

### Arrival protocol (P0 #8–13) — DONE
- Day-0/1 arrival inspection duty (dehydration, injuries, lameness, temperature, "handle quarantine animals LAST"); day-13 fecal exam + day-30 fecal recheck duties (recordable as new FECAL_EXAM health events); protocol now 11 steps and release still requires every step.
- `origin_market`, `transport_hours`, `seller_health_history` on PurchaseBatch; per-head `individual_weights_kg` override of the batch average.
- *(Fecal results are recordable health events; result-driven quarantine extension remains future work.)*

### Health calendar (P1 #14–15, #22–24, #37) — DONE
- `services/cadence.py` materializes rounds on task-board load: FMD Sep/Mar, ET+HS May, Goat Pox Nov, CCPP Jan, deworming Jun/Jan (kids' cadence noted), hoof trimming + spraying 6-monthly, disinfection quarterly, monthly weighing, daily morning water/bunk routine, per-ingredient feed-reorder alerts, buck-rotation reminders at 36 months. Herd-level rounds close via bucket/batch-scoped health events.
- Template fixes: HS first dose 3→6 months; standalone TT template; Johne's/anti-coccidial marked advisory.

### Reproduction (P1 #16–18, #25) — DONE
- HEAT_WATCH at breeding+18 (closed by either ultrasound outcome); day-100 PREGNANCY_LATE move duty (EKD−50) with completion guard; REBREED prompt 30 days after weaning/postpartum RESTING entry; RESTING 10-day flush window enforced on manual moves and services; buck-rotation reminder implemented (the dead `BUCK_ROTATION_DAYS` constant remains for sim compatibility).

### Feeding (P1 #26–29) — DONE
- Per-head amounts scale from bucket mean weight (3–4% by class, clamped 0.5–2× flat, flat fallback); creep ramp 0.1/0.2/0.3 kg by band from day 14; BREEDING bucks +0.5 kg supplement line; maintenance recipe mineral 2% + salt line added to seed.

### Finance & records (P2 #30–34) — DONE
- Sales capture weight/₹-per-kg/buyer with derived price and below-window advisories; unknown-DOB male gate closed via estimated DOB; deaths carry coded cause + disposal + necropsy; insurance register with renewal duties + dashboard expiry card; per-animal lifetime P&L; feed-stock memo value on the finance summary.

### Simulation (#38) — DONE
- Buck 42→35 kg (model version bumped 3.1.0→3.2.0; daily-ops 1.0.0→1.1.0 — protocol firing fix + creep ramp sync); `breed_price_premium_pct` toggle (default 0 = bit-identical baseline).

### Partially addressed / deferred
- **#12 quarantine husbandry steps recording data**: days 1–3/5–9 remain button-completed; AD3E can be logged as a VITAMIN event but is not form-linked.
- **#19 weighing-round ADG computation**: the monthly weighing duty exists; growth-curve flagging is future work.
- **#35 parity**: derived and exposed, not a client-writable column.
- **#39 orphan workflow**: `dam_rejected` flag + kid-support duty; foster/bottle protocols beyond that are future work.
- **#40 weight-qualified weaning**: deliberately not implemented — day-60 calendar weaning retained (the 8-month kidding interval depends on it); documented as a policy choice.
- **Milk**: still unmodeled operationally (correct for a meat breed).

**Verification**: backend 4,326 tests green on the final full run, `ruff format/check` clean, `mypy --strict` 0 errors across 100 files; five chained Alembic migrations (`f1e2d3c4b5a6` → `d0e1f2a3b4c5`, single head); OpenAPI re-exported (79 paths) and the Orval client regenerated; frontend forms updated for every new field.
