# End-to-end verification — 500-doe Osmanabadi herd build-out, 2026-10 to 2031-09

**Reproduce this report (deterministic, fixed seeds):**

```bash
backend/.venv/bin/python backend/scripts/simulate_herd_buildout.py
```

**Regenerated 2026-09-15** after the three engine fixes the first run of this report surfaced (same commit): (1) herd events gained a per-event `age_months` input, so the 10 grower batches now enter at their real 6-month age (observation 1, RESOLVED); (2) the NLM subsidy's eligible-head cap now counts breeding stock bought through scheduled events, so the toggle is live for event-built herds (observation 9, RESOLVED, §7); (3) the daily-ops eligibility note quotes the enforced `GOAT_PROFILE.min_breeding_age_months` constant instead of a stale hardcoded age (observation 5, RESOLVED).

Everything below comes out of the app's own simulation engines — the monthly bio-economic engine (model 3.3.0) for the 5-year money and herd trajectory, and the daily-ops engine (model 1.1.0) for the day-by-day bucket and task detail. No database, no hand-computed figures. Numbers in the monthly engine are expected values, so fractions of an animal are normal.

## 1. The scenario

Every 2 months the farm buys 50 female Osmanabadi growers, each 6 months old, plus 3 breeding bucks (~12 months old, app default ratio 1 buck : 20 does). Ten batches, months 1-19, until 500 purchased females stand on the farm. After that the herd breeds on its own. Horizon 5 years (60 months), starting 2026-10-01. The monthly-engine purchase events carry `age_months=6`, so each batch enters the grower chain at its real age and waits the full ~6 months to the 12-month breeding gate.

| Batch | Arrives | Sim month | Females (6 mo) | Bucks (12 mo) |
|---|---|---|---:|---:|
| 1 | 2026-10-01 | 1 | 50 | 3 |
| 2 | 2026-12-01 | 3 | 50 | 3 |
| 3 | 2027-02-01 | 5 | 50 | 3 |
| 4 | 2027-04-01 | 7 | 50 | 3 |
| 5 | 2027-06-01 | 9 | 50 | 3 |
| 6 | 2027-08-01 | 11 | 50 | 3 |
| 7 | 2027-10-01 | 13 | 50 | 3 |
| 8 | 2027-12-01 | 15 | 50 | 3 |
| 9 | 2028-02-01 | 17 | 50 | 3 |
| 10 | 2028-04-01 | 19 | 50 | 3 |
| **Total** | | | **500** | **30** |

All biology, growth, price, feed and cost assumptions are the app's default Osmanabadi calibration, untouched. Two *management* toggles had to change to express a build-to-500 plan: the breeding-doe cap (default 50 — the NABARD 50+2 unit size — would sell every graduate above 50 as meat) is set to unlimited, and the female-retention fraction (default 60%) is set to 100%, because this farm keeps every doe it raises. Both toggles are documented policy switches in `SimulationAssumptions.herd`, not biology.

## 2. The five-year trajectory (monthly engine, quarterly view)

End-of-quarter head counts; flows (births, sales, feed, water, cash) are quarter totals. The full 60-month table is in [monthly_trajectory.md](monthly_trajectory.md) and [monthly_trajectory.csv](monthly_trajectory.csv).

| Q | Months | Does | F growers | Bucks | Kids+weaners | Total herd | Kids born | Sales head | Sales ₹ | Feed kg | Water kL | Net cash | Cum. cash |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 2026-10 – 2026-12 | 0 | 99 | 6 | 0 | 105 | 0 | 0 | ₹0 | 12,710 | 40 | -₹12,23,157 | -₹35,88,639 |
| 2 | 2027-01 – 2027-03 | 49 | 99 | 9 | 0 | 157 | 0 | 0 | ₹0 | 28,766 | 82 | -₹8,99,817 | -₹44,88,457 |
| 3 | 2027-04 – 2027-06 | 92 | 148 | 15 | 0 | 256 | 0 | 0 | ₹34,558 | 51,246 | 140 | -₹13,90,383 | -₹58,78,839 |
| 4 | 2027-07 – 2027-09 | 186 | 99 | 17 | 55 | 358 | 61 | 0 | ₹17,449 | 72,462 | 207 | -₹11,67,062 | -₹70,45,901 |
| 5 | 2027-10 – 2027-12 | 215 | 148 | 23 | 150 | 537 | 111 | 0 | ₹1,32,503 | 95,613 | 284 | -₹21,83,756 | -₹92,29,657 |
| 6 | 2028-01 – 2028-03 | 291 | 123 | 26 | 155 | 620 | 71 | 0 | ₹1,43,712 | 121,619 | 353 | -₹19,32,885 | -₹1,11,62,543 |
| 7 | 2028-04 – 2028-06 | 313 | 167 | 29 | 238 | 792 | 200 | 24 | ₹4,81,976 | 145,996 | 439 | -₹17,91,264 | -₹1,29,53,807 |
| 8 | 2028-07 – 2028-09 | 405 | 72 | 28 | 287 | 821 | 136 | 44 | ₹6,05,133 | 159,411 | 473 | -₹13,89,922 | -₹1,43,43,729 |
| 9 | 2028-10 – 2028-12 | 414 | 108 | 28 | 315 | 945 | 224 | 28 | ₹5,29,587 | 172,611 | 517 | -₹15,66,009 | -₹1,59,09,738 |
| 10 | 2029-01 – 2029-03 | 409 | 134 | 27 | 406 | 1,076 | 246 | 35 | ₹5,76,821 | 186,257 | 576 | -₹16,38,402 | -₹1,75,48,140 |
| 11 | 2029-04 – 2029-06 | 451 | 144 | 27 | 380 | 1,093 | 198 | 100 | ₹14,54,050 | 198,365 | 597 | -₹8,93,378 | -₹1,84,41,518 |
| 12 | 2029-07 – 2029-09 | 468 | 188 | 24 | 376 | 1,154 | 235 | 90 | ₹13,53,198 | 213,557 | 642 | -₹15,06,967 | -₹1,99,48,485 |
| 13 | 2029-10 – 2029-12 | 516 | 177 | 27 | 447 | 1,247 | 285 | 98 | ₹13,37,894 | 224,477 | 690 | -₹12,97,431 | -₹2,12,45,916 |
| 14 | 2030-01 – 2030-03 | 566 | 173 | 30 | 445 | 1,358 | 236 | 30 | ₹6,54,102 | 248,901 | 741 | -₹21,26,498 | -₹2,33,72,414 |
| 15 | 2030-04 – 2030-06 | 597 | 207 | 31 | 493 | 1,443 | 331 | 143 | ₹20,77,232 | 263,123 | 798 | -₹8,64,935 | -₹2,42,37,348 |
| 16 | 2030-07 – 2030-09 | 639 | 208 | 33 | 573 | 1,548 | 337 | 114 | ₹14,70,963 | 277,012 | 849 | -₹16,11,462 | -₹2,58,48,811 |
| 17 | 2030-10 – 2030-12 | 694 | 227 | 36 | 528 | 1,617 | 280 | 94 | ₹14,73,767 | 303,415 | 904 | -₹18,28,826 | -₹2,76,77,637 |
| 18 | 2031-01 – 2031-03 | 729 | 266 | 38 | 633 | 1,900 | 447 | 34 | ₹8,02,471 | 334,043 | 1,017 | -₹27,17,343 | -₹3,03,94,980 |
| 19 | 2031-04 – 2031-06 | 796 | 246 | 41 | 694 | 1,889 | 364 | 233 | ₹36,05,186 | 343,463 | 1,043 | -₹1,53,459 | -₹3,05,48,439 |
| 20 | 2031-07 – 2031-09 | 857 | 291 | 45 | 641 | 2,013 | 378 | 112 | ₹16,66,380 | 375,254 | 1,121 | ₹2,25,73,977 | -₹79,74,462 |

Key waypoints:

- **First kids born:** simulation month 11 (2027-08) in the monthly engine — batch 1's 6-month-olds graduate at the 12-month breeding gate and kid after the 5-month gestation, in line with the daily engine's 2027-08/09 first kiddings (observation 1).
- **Month 19 (2028-04, last batch in):** the doe pool stands at 282 — all purchased stock (the first homebred doe, born in month 11, cannot graduate before month 23); 193 females are still in the grower chain (the two youngest purchased batches plus the first homebred growers); 740 head total. Survivors of the 500 purchased females run a few percent below 500 — 5%/yr adult and 4%/yr grower mortality act from the day each batch lands.
- **Peak herd:** 2,013 head at end of month 60 (2031-09).
- **Over 60 months:** 4,142 kids born, 1,179 head sold for ₹1,84,16,982 (meat + culls).

Bakrid price-uplift months the engine auto-filled from its embedded calendar:

| Sim month | Calendar | Sales head that month |
|---|---|---:|
| 8 | 2027-05 | 0 |
| 20 | 2028-05 | 20 |
| 31 | 2029-04 | 55 |
| 43 | 2030-04 | 69 |
| 55 | 2031-04 | 153 |

## 3. Batch 1, day by day (daily-ops engine)

The daily engine follows individual animals building to building. Batch 1 — 50 six-month-old growers (G01-G50) and 3 yearling bucks (S1-S3) — was run from the day the truck arrived, 2026-10-01, for 365 days (seed 20261002). The complete day-by-day ledger — every feed delivery, cleaning, duty, move, birth and exit — is in [batch1_journey.md](batch1_journey.md); the highlights:

**The 45-day quarantine protocol** (dates the engine fired each step):

| Protocol day | Date | Step |
|---|---|---|
| 1 | 2026-10-01 | Arrival inspection (dehydration, injuries, temperature); rest + electrolytes, dry roughage only |
| 4 | 2026-10-04 | Deworm — Albendazole/Closantel oral + Ivermectin SC |
| 10 | 2026-10-10 | PPR vaccine |
| 13 | 2026-10-13 | Fecal exam — confirm deworming worked |
| 20 | 2026-10-20 | ET + Tetanus vaccine |
| 30 | 2026-10-30 | Goat Pox vaccine + fecal recheck / pre-release review |
| 40 | 2026-11-09 | FMD vaccine |
| 45 | 2026-11-14 | Zinc-sulfate footbath → release to FOUNDATION |

**Breeding journey milestones** (first occurrence in the run):

| Milestone | Date |
|---|---|
| Released from quarantine (day-45 protocol complete) | 2026-11-14 |
| First natural service recorded | 2027-04-02 |
| First positive pregnancy scan (+32d) | 2027-05-04 |
| First move to PREGNANCY_LATE (gestation day 100) | 2027-07-11 |
| First move to DELIVERY (15 days before due) | 2027-08-15 |
| First kidding | 2027-08-30 |

Every doe's first service happened at age 12.0 months — the 12-month first-service gate (GOAT_PROFILE.min_breeding_age_months) holding exactly. 59 services were recorded in all: 50 first services plus re-services of does whose first scan came back negative (next heat, +21 days). The day-60 weaning and the doe's re-breeding fall just past the engine's 365-day horizon cap, so that half of the journey is shown by the continuation run below.

**One sample doe — G01's journey, hop by hop:**

| Date | From | To | Why |
|---|---|---|---|
| 2026-11-14 | QUARANTINE | FOUNDATION | quarantine_release |
| 2027-04-02 | FOUNDATION | BREEDING | breeding |
| 2027-05-04 | BREEDING | PREGNANCY_EARLY | ultrasound |
| 2027-07-11 | PREGNANCY_EARLY | PREGNANCY_LATE | manual |
| 2027-08-15 | PREGNANCY_LATE | DELIVERY | delivery |
| 2027-08-30 | DELIVERY | RECOVERY | kidding |

### Batches 2-10, same journey

The daily engine cannot take mid-run arrivals (its herd is fixed on day 1 — see observations), so each batch was run on its own arrival date with its own seed. Every batch walks the identical path, two months apart:

| Batch | Arrived | Quarantine release | First service | First +32d scan | First kidding |
|---|---|---|---|---|---|
| 1 | 2026-10-01 | 2026-11-14 | 2027-04-02 | 2027-05-04 | 2027-08-30 |
| 2 | 2026-12-01 | 2027-01-14 | 2027-06-02 | 2027-07-04 | 2027-10-30 |
| 3 | 2027-02-01 | 2027-03-17 | 2027-08-03 | 2027-09-04 | 2027-12-31 |
| 4 | 2027-04-01 | 2027-05-15 | 2027-10-01 | 2027-11-02 | 2028-02-28 |
| 5 | 2027-06-01 | 2027-07-15 | 2027-12-01 | 2028-01-02 | 2028-04-29 |
| 6 | 2027-08-01 | 2027-09-14 | 2028-01-31 | 2028-03-03 | 2028-06-29 |
| 7 | 2027-10-01 | 2027-11-14 | 2028-04-01 | 2028-05-03 | 2028-08-29 |
| 8 | 2027-12-01 | 2028-01-14 | 2028-06-01 | 2028-07-03 | 2028-10-29 |
| 9 | 2028-02-01 | 2028-03-16 | 2028-08-02 | 2028-09-03 | 2028-12-30 |
| 10 | 2028-04-01 | 2028-05-15 | 2028-10-01 | 2028-11-02 | 2029-02-28 |

### After kidding — the continuation run

To keep the post-kidding half honest (and inside the 365-day cap), a second daily run starts 2027-08-01 with ten batch-1-age does already in PREGNANCY_LATE at gestation day 120 (bred ~2027-04-02, exactly when batch 1 was first served) plus two breeding bucks. It shows:

| Milestone | Date |
|---|---|
| First move to DELIVERY (15 days before due) | 2027-08-16 |
| First kidding | 2027-08-31 |
| First re-breeding (any path — here a doe whose litter died) | 2027-10-14 |
| First day-60 weaning | 2027-10-30 |
| First positive pregnancy scan (+32d) | 2027-11-15 |
| First re-breeding after weaning + ~30-day resting flush | 2027-11-29 |

In that run 19 re-services were recorded, 10 confirmed pregnant, and does that kidded twice show the full kidding-to-kidding cycle below (section 6).

### The whole herd at build-out — 2028-04-01 snapshot

A 90-day operational snapshot with 477 head (batches 1-9 and their 27 bucks — the engine caps a run at 500 starting animals, so batch 10, arriving that very morning, is covered by the identical batch-1 quarantine ledger). Over the window: 462 services, 341 confirmed pregnancies, 398 bucket moves, 3,609 duties generated (~40/day), and 51,197 kg of feed delivered (~569 kg/day). State convention: every breeding-age doe starts open in BREEDING (the monthly engine tracks pools, not individuals), so the window shows a fresh breeding wave at full scale rather than the true mixed-state herd.

## 4. Bucket movements

The daily engine models the ten lifecycle buckets as ten buildings, each with its own vet area; every move is validated against the same legal transition graph the live app enforces (`models.lifecycle.LEGAL_BUCKET_TRANSITIONS`). What each bucket means:

| Bucket | Meaning |
|---|---|
| QUARANTINE | New arrivals isolated for the 45-day protocol (inspection, deworming, PPR/ET+T/Goat Pox/FMD vaccines, fecal checks, footbath). |
| FOUNDATION | Cleared arrivals and growing stock — the grow-out pen where young does wait to reach the 12-month breeding gate. |
| BREEDING | Open does cycling and being served; the bucks' pen. |
| PREGNANCY_EARLY | Scan-confirmed does, gestation day 32-99. |
| PREGNANCY_LATE | Gestating does from day 100; pre-kidding ET+TT vaccines fire here (due-date −40 and −25). |
| DELIVERY | Kidding pen; does move in 15 days before the due date. |
| RECOVERY | Fresh dams with their unweaned kids (kids on creep feed); weaning at day 60 moves kids out. |
| RESTING | Post-weaning dry-off and flush (~30 days) before the doe returns to BREEDING. |
| MALE_KIDS | Weaned males growing to the 8-9 month meat window. |
| FEMALE_KIDS | Weaned females growing toward the breeding gate. |

Batch 1's arrival year produced 277 moves; the matrix (from → to, with the workflow context that caused it):

| From | To | Context | Moves |
|---|---|---|---:|
| BREEDING | PREGNANCY_EARLY | ultrasound | 48 |
| DELIVERY | RECOVERY | kidding | 37 |
| FOUNDATION | BREEDING | breeding | 51 |
| PREGNANCY_EARLY | PREGNANCY_LATE | manual | 47 |
| PREGNANCY_LATE | DELIVERY | delivery | 37 |
| PREGNANCY_LATE | RESTING | abortion | 2 |
| QUARANTINE | FOUNDATION | quarantine_release | 52 |
| RECOVERY | RESTING | postpartum | 1 |
| RESTING | BREEDING | breeding | 2 |

The continuation run added 107 moves (delivery, kidding, weaning, resting, re-breeding) and the 477-head snapshot 398 moves in 90 days. Matrices for all three runs: [bucket_transitions.md](bucket_transitions.md).

## 5. Tasks — what the crews actually do

Every duty the engine generated for batch 1's arrival year, by category (FEED and CLEANING dominate by construction: three feed deliveries and two verified cleanings per occupied building per day):

| Category | Duties | Example headline |
|---|---:|---|
| BUCKET_MOVE | 93 | Release G01 to FOUNDATION — Day 45: 10% zinc sulfate footbath → release to FOUNDATION |
| CLEANING | 3,132 | Clean Quarantine Ward — morning (after feeding) |
| DEWORMING | 53 | G01: Day 4: deworm — Albendazole/Closantel oral + Ivermectin SC |
| FEED | 3,049 | Mix 58.300 kg — Dry roughage only (days 1–3, zero grain) |
| KIDDING_DUE | 37 | Kidding due: G01 |
| OTHER | 108 | Attend casualty: G35 died |
| QUARANTINE | 263 | G01: Day 0–1: arrival inspection — dehydration (skin tent/gums), injuries, lameness, temperature; isolate sick immediately; handle quarantine animals LAST (dedicated boots/tools) |
| ULTRASOUND | 115 | Pregnancy check: G01 |
| VACCINE | 300 | G01: Day 10: vaccinate PPR (live viral, SC) |

By crew role: CLEANER 3,132, FEEDER 3,049, MANAGER 108, MOVER 93, VET 768. At build-out scale (477-head snapshot): 3,609 duties in 90 days (~40/day), with the vet round heaviest in BREEDING. Full breakdowns for all three runs: [task_log_summary.md](task_log_summary.md).

## 6. Breeding & reproduction — app defaults vs published Osmanabadi benchmarks

The app documents every biological default inline (simulation/assumptions.py, models/species.py). Both sides quoted:

| Metric | App default (source note in code) | Published Osmanabadi reference | This run realized |
|---|---|---|---|
| Conception per service | 0.85 (ICAR herd models) | ~80-85% under managed natural service | Batch 1: 48/58 scans positive = 83% |
| Gestation | 150 days (GOAT_PROFILE; monthly engine: 5 months) | 145-155 days (the app's own kidding window) | Exactly 150 days by construction — every kidding lands on service + 150 |
| Litter size | 1.6 kids/kidding mean; daily engine draws 40% singles / 60% twins; maiden does scaled to ~0.85 of mature (~1.4) per AICRP/NARI parity table | Osmanabadi twinning ~35-40% in field records; AICRP/NARI managed herds ~1.65-1.7 for mature does | Batch 1 mean litter 1.62 over 37 kiddings (small sample — one batch, one year) |
| Kidding interval | 5 mo gestation + 2 mo lactation/weaning + 1 mo open ≈ 8 months (~243 d), chosen to sit inside the published band | 232-297 days (7.7-9.8 months) published; ~195 d in improved-management herds | Continuation run: 8 does kidded twice, intervals 194 d, 240 d, 240 d, 240 d, 240 d, 240 d, 240 d, 240 d; an interval near 194 d is a doe whose litter died — the postpartum path (14 d recovery + 30 d flush + 150 d gestation) instead of the full 60 d-to-weaning cycle |
| Kid mortality (pre-weaning) | 15% of each crop (NABARD bankable convention; field studies 10.9-20.4%) | same | Batch 1: 7 kid deaths of 60 born alive (kiddings begin only on day ~333 of the 365-day run, so most of the crop is still pre-weaning at horizon — the rate will keep acting past it); continuation: 6 kid deaths |
| Adult mortality | 5%/yr, compounded monthly/daily | 5-10% field range | Batch 1 (53 adults, 365 d): 2 death(s); continuation (12 adults, 365 d): 1 death(s) |
| First breeding | 12 months + 22 kg (field puberty ~11.5 mo; age at first kidding norm 19-20 mo) | same | First services at 12.0-12.1 months of age (section 3) |
| Repeat breeders | culled after 2 consecutive failed services (the app's own operational rule) | farm policy, not literature | See cull counts in the ledgers |

## 7. Money over the five years (monthly engine)

### Buying the herd

| What | Head | Spend |
|---|---:|---:|
| 500 female growers (10 batches × 50, at the engine's live-weight valuation, which escalates ~4%/yr) | 500 | ₹34,26,017 |
| 30 scheduled bucks (10 × 3) | 30 | ₹4,63,515 |
| Automatic replacement bucks (engine restocks sires after the 3-year rotation cull, `herd.auto_purchase_bucks`) | — | ₹8,30,959 |
| **Total stock purchases** | | **₹47,20,491** |

Accounting note: the engine books grower purchases as that month's operating cost (young stock is trading inventory) while buck purchases are capitalized as breeding livestock and depreciated over 60 months — the cash still leaves in the purchase month either way.

### Profit & loss by year

| Year | Meat sales | Culls | Manure | Total revenue | Feed | Vet | Labour | Insurance | Misc+selling | Grower purchases | EBITDA | Net cash flow |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 (2026-10 – 2027-09) | ₹0 | ₹52,007 | ₹67,266 | ₹1,19,272 | ₹6,58,781 | ₹87,396 | ₹2,10,365 | ₹66,631 | ₹26,826 | ₹20,00,741 | -₹29,31,469 | -₹46,80,419 |
| 2 (2027-10 – 2028-09) | ₹7,04,188 | ₹6,59,136 | ₹2,99,912 | ₹16,63,236 | ₹23,19,573 | ₹3,19,514 | ₹9,12,257 | ₹2,14,691 | ₹83,374 | ₹14,25,276 | -₹36,11,448 | -₹72,97,828 |
| 3 (2028-10 – 2029-09) | ₹27,04,097 | ₹12,09,560 | ₹4,53,379 | ₹43,67,036 | ₹36,29,976 | ₹5,22,150 | ₹13,98,006 | ₹3,31,067 | ₹1,89,632 | ₹0 | -₹17,03,796 | -₹56,04,756 |
| 4 (2029-10 – 2030-09) | ₹42,38,912 | ₹13,01,278 | ₹6,11,702 | ₹61,51,892 | ₹50,12,724 | ₹7,27,246 | ₹19,24,809 | ₹4,53,578 | ₹2,58,800 | ₹0 | -₹22,25,264 | -₹59,00,326 |
| 5 (2030-10 – 2031-09) | ₹57,08,877 | ₹18,38,926 | ₹8,49,230 | ₹83,97,034 | ₹70,03,637 | ₹10,08,683 | ₹26,74,450 | ₹6,29,323 | ₹3,41,604 | ₹0 | -₹32,60,663 | ₹1,78,74,349 |

Year 5's net cash flow includes the terminal value — the closing herd, shed and working capital valued at the horizon (see the table below); the operating years 1-4 are cash-negative because this plan keeps every female it raises instead of selling her: the money is going into a growing breeding herd, which is exactly what the terminal value recovers. EBITDA stays negative through year 5 — at default prices an ever-expanding, never-sell-a-female herd does not cover its feed and labour bill from male and cull sales alone (break-even meat price ₹837/kg vs the ₹370 base). That is the honest economics of *this* build policy, not an engine fault: a farm that sold surplus females (the app's 60% retention default) would show a very different P&L.

### Project cost and viability

| Item | Without subsidy | With NLM 50% subsidy |
|---|---:|---:|
| Project cost (shed ₹1.36 crore for 2,265 places + equipment ₹11.32 lakh + working capital ₹10.50 lakh) | ₹1,57,69,884 | ₹1,57,69,884 |
| Bank loan (85%, 11% p.a., 12-month moratorium) | ₹1,34,04,401 | ₹1,34,04,401 |
| Capital subsidy | ₹0 | ₹23,65,483 |
| Promoter equity (month-0 outflow) | ₹23,65,483 | ₹0 |
| NPV @ 12% | -₹1,11,50,251 | -₹87,84,768 |
| IRR (annual blocks) / MIRR | -10.9% / -0.5% | -8.6% / 1.4% |
| Payback (first month cumulative cash ≥ 0) | beyond horizon | beyond horizon |
| Deepest cash hole | -₹2,86,58,996 in month 59 (2031-08) | -₹2,86,58,996 in month 59 |
| Terminal value recovered at horizon (herd + shed + WC) | ₹2,81,79,503 | ₹2,81,79,503 |
| Break-even meat price | ₹845/kg vs ₹370 base | ₹744/kg |

**NLM comparison result: a real ₹23,65,483 subsidy.** The eligible-head cap now sizes the unit being established — the 500 event-purchased female growers plus the 30 scheduled bucks, 530 head × ₹10,000 = ₹53.00 lakh of eligible capital (squarely in the scheme's 500F+25M ~₹50 lakh band). What binds instead is the funding stack — loan + subsidy may not exceed the project cost, so with an 85% loan the subsidy lands at 15% of ₹1.58 crore = ₹23,65,483, below the ₹26,50,000 half-ceiling. Against the no-subsidy run, promoter equity drops ₹23,65,483 → ₹0 and payback is unchanged (beyond horizon). Operating economics — revenue, EBITDA, break-even price — are unchanged: the subsidy is a month-0 capital event, not an operating one.

## 8. Engine observations and limitations (read before trusting any single number)

1. **RESOLVED — purchased growers now carry their real arrival age.** The first run of this report found scheduled `female_grower` purchases placed mid-class (~9 months in the 6-11-month chain) with no per-event age input, so each batch reached the doe pool ~3 months after arrival instead of ~6 and year-1-2 revenue ran ahead of reality. Herd events now accept `age_months` (validated against the class's age chain), and this scenario's events carry `age_months=6`: the first batch-1 does graduate in month 6 (2027-03) and the first monthly-engine kids arrive in month 11 (2027-08) — matching the daily engine's 6-month-old timeline (first service 2027-04, first kidding 2027-08/09).
2. **Grower purchases are expensed, buck purchases capitalized.** The engine treats young-stock event purchases (our 500 growers) as trading inventory in operating cost, while does/bucks bought as adults capitalize. Buying the same animals as `female_grower` vs `doe` therefore changes EBITDA timing, not cash.
3. **Automatic sire restocking spent ₹8,30,959 beyond the 30 scheduled bucks.** `herd.auto_purchase_bucks` (default on) buys a buck whenever the 1:20 ratio or the 3-year rotation cull requires it. This is intended engine policy, surfaced here so the purchase bill reconciles.
4. **The daily engine takes its herd on day 1 only — no staggered arrivals — and caps a run at 500 head / 365 days.** The 10-batch build-out was therefore run as ten independent per-batch daily runs (identical journeys two months apart), one late-pregnancy continuation run to show weaning/re-breeding past the day-365 cap, and one 477-head snapshot at month 19. No whole-herd 5-year daily run exists; the monthly engine is the whole-herd source of truth.
5. **RESOLVED — stale note text in the daily engine.** The first run flagged that the `notes` output claimed breeding eligibility is "age-gated (≥10 months)" while the enforced gate is 12 months. The note now quotes `GOAT_PROFILE.min_breeding_age_months` directly, so it cannot drift from the enforced gate again; the run itself still proves the gate — no doe was served before 12.0 months.
6. **Weight gates are age proxies in the daily engine** (its own notes say so): the 22 kg breeding floor and the 24-28 kg sale window are enforced as 12-month and 8-9-month age gates. The monthly engine carries the full weight curve.
7. **Monthly resolution synchronizes each batch.** All does in a monthly slot are served and kid together; a real batch spreads services over a few weeks. The daily per-batch ledgers show the realistic spread within the arrival year.
8. **Weaning differs by design between engines**: monthly resolution kids span ages 0-2 months (weaning effectively month 3) while daily ops weans at day 60 — a documented approximation in the engine docstring.
9. **RESOLVED — the NLM subsidy toggle is live for event-built herds.** The eligible-head cap used to multiply ₹10,000 by the *starting* doe+buck counts (zero here — all 530 animals arrive by scheduled events), so base and NLM runs came out identical. The cap now also counts breeding stock bought through scheduled events — adult does/bucks and female young stock raised into the doe pipeline — so this 500F+30M build-out earns ₹23,65,483 of capital subsidy against the scheme's 500F+25M band (§7).
10. **The negative P&L is the policy, not the model.** Keeping 100% of female graduates (uncapped) means year-on-year negative EBITDA — the farm forgoes ~₹10,000 of sale value per retained graduate and feeds her instead. The wealth accumulates as breeding stock and shows up in the ₹3+ crore terminal value. Run the same build-out with the app's 60% retention default and the P&L turns.

## 9. What to verify manually — tick-off checklist

Each statement below carries the value this run produced, so it can be checked against the UI or a re-run of the same engines:

- [ ] 1. Batch 1 arrives 2026-10-01: 50 female growers + 3 bucks appear in QUARANTINE; arrival-inspection and rest duties are dated 2026-10-01.
- [ ] 2. Batch 5 arrives 2027-06-01 (simulation month 9); batch 10 arrives 2028-04-01 (simulation month 19) — the last purchase.
- [ ] 3. Batch-1 deworming tasks are dated 2026-10-04; PPR vaccine 2026-10-10; fecal exam 2026-10-13; ET+Tetanus 2026-10-20; Goat Pox + pre-release review 2026-10-30; FMD 2026-11-09.
- [ ] 4. All 53 batch-1 animals are released to FOUNDATION on 2026-11-14 (protocol day 45); batch 1's quarantine pen is empty from that day on.
- [ ] 5. No batch-1 doe is served before 12.0 months of age (first-service ages: 12.0 months).
- [ ] 6. First batch-1 service: 2027-04-02 (≈2027-04-01, the day the oldest does cross 12 months).
- [ ] 7. First positive pregnancy scan: 2027-05-04 (service + 32 days).
- [ ] 8. First move to PREGNANCY_LATE: 2027-07-11 (gestation day 100).
- [ ] 9. First move to DELIVERY: 2027-08-15 (15 days before the due date).
- [ ] 10. First kidding: 2027-08-30 — exactly 150 days after the first service; kids stay with the dam in RECOVERY.
- [ ] 11. Batch 5 follows the same path shifted 8 months: release 2027-07-15, first service 2027-12-01, first kidding 2028-04-29.
- [ ] 12. Batch 10: release 2028-05-15, first service 2028-10-01, first kidding 2029-02-28.
- [ ] 13. Continuation run: first day-60 weaning 2027-10-30; dams move to RESTING the same day and are re-bred from 2027-11-29 after the ~30-day flush.
- [ ] 14. First male-kid meat sale (8-9 month window): 2028-05-01 — Meat sale at ~8.0 months.
- [ ] 15. Monthly engine, first kids born: month 11 (2027-08) — batch-1 6-month-olds graduate at the 12-month gate in month 6 and kid after the 5-month gestation, matching the daily engine (observation 1).
- [ ] 16. Month 19 (2028-04): the doe pool stands at 282 (all purchased — the first homebred doe graduates no earlier than month 23), plus 193 females still growing; total herd 740.
- [ ] 17. Peak herd 2,013 head at end of month 60 (2031-09).
- [ ] 18. Bakrid uplift months: 2027-05 (sim month 8), 2028-05 (sim month 20), 2029-04 (sim month 31), 2030-04 (sim month 43), 2031-04 (sim month 55).
- [ ] 19. Year-1 meat sales are zero head (the first homebred males, born from month 11, finish at the 9-month sale age in month 20 (2028-05); the small year-1 Sales ₹ are cull revenue) — revenue ramps from year 2 as all 10 batches cycle.
- [ ] 20. Payback (cumulative cash ≥ 0): not reached inside the 60-month horizon without subsidy — the build-out is still cash-negative at 2031-09 (see §7).
- [ ] 21. NLM 50% subsidy: subsidy amount ₹0 without vs ₹23,65,483 with NLM; equity ₹23,65,483 vs ₹0.
- [ ] 22. Batch-1 daily run generated 300 vaccine duties and 53 deworming duties for 53 animals over 365 days.
- [ ] 23. Batch-1 run: 59 services, 48 confirmed conceptions, 60 kids born alive (0 stillborn).

