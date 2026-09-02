# Master Audit Synthesis — Goat & Buffalo Dairy Farms

**Date:** 2026-09-01 · **Codebase:** Herdly (`goat_saas`) @ commit `69a814d`
**Method:** 8 independent auditor teams run in parallel — 4 independent audits (goat full-stack, dairy full-stack, cross-stack contract, simulation/finance math) and 4 adversarial red-teams (goat flows, dairy flows, backend security, frontend). Auditors worked from scratch with no shared assumptions; exploits were executed live against throwaway databases, and real-world constants were checked against published sources (ICAR-CIRG, NDRI Karnal, NABARD, TNAU, 2025-26 Telangana market data). The synthesis owner independently re-verified the six most severe findings in source before accepting them.

**Overall verdict:** The platform's security architecture, multi-tenant isolation, API contract discipline, and core goat biology are in genuinely good shape (no IDOR, no injection, exact three-way contract sync, 27/27 tables ↔ 45 migrations consistent, 617+ security tests green). The dairy (buffalo) side is **not production-ready**: one blocker, five highs, and a pattern where dairy rules promised in the README/seed/UI exist only in the simulator or only as prose. The simulation layer — the lender-facing financial product — has one high-severity math bug and three economic-default errors large enough to invert loan decisions.

## Severity totals (deduplicated across all 8 audits)

| Severity | Count | Landmark findings |
|---|---|---|
| BLOCKER | 1 | Dairy fresh-pen deadlock (D-1) |
| HIGH | 8 | 2× dairy HTTP 500s (D-2), milk-context/backdating/income provenance (D-3/D-4), VWP + cull rule unimplemented (D-5/D-6), goat meat-sale window dead code (G-1), kid feed billing (G-3), sim mortality calibration 3.4× (S-1) |
| MEDIUM | 15 | Dashboard species bypass (G-2), optimizer infeasible (S-2), unenforced buck ratio (G-4), dairy calf-economics leaks (D-7..D-12), dairy sim economics (S-3/S-4), contract hygiene (C-1..C-4), security scope gap (X-1), frontend 422 loop (F-1) |
| LOW | ~20 | See individual reports |

Cross-corroboration (two audit teams independently finding the same defect by different methods): meat-sale window unenforced (A1 static + A5 executed), VWP unenforced (A2 + A6), 3-service cull rule species-blind (A2 + A6), calf-weaning duty broken (A2 + A6), buck ratio/rotation unenforced (A1 + A5). One prompt premise was **refuted** by evidence: the RS256 private key is *not* committed to git (no history, no dangling objects; production requires mounted keys).

---

## A. GOAT FARM (Osmanabadi meat) — verdict: **sound biology, weakened operations & finance layer**

### What was verified correct (independent + literature)
Gestation 150 d with 145–155 window (published 152.2 ± 0.24 d); PD at +32 d; 60-day weaning; 14-day postpartum recovery; cull-after-2-failed-cycles; litter 1.6 (published prolificacy ~1.5–1.8 with 70–80% twinning); mortality tiers 10/5/4/5%; ₹370/kg live weight with 35% Bakrid uplift (2025-26 Telangana bands); DMI 3–4.5% BW; adult weights 33/42 kg; FMD/ET/goat-pox/PPR vaccination ages; the full bucket state machine and money ledger trace end-to-end; frontend/backend vocabulary parity. 485 backend goat tests + 45 frontend tests pass.

### Findings
- **G-1 (HIGH) — the terminal revenue event has no system support.** `MEAT_SALE_AGE_MONTHS=(8,9)`/`MEAT_SALE_WEIGHT_KG=(24,28)` (`models/constants.py:36-37`) are dead code: no task, guard, or metric uses them; only dashboard magic numbers (`-8`, `24.0`). Red-team executed a **3-month/12 kg kid sold for ₹5,000 → 200 + income booked** (`api/animals.py` change_status has no gate). The simulator sells males at 10 months/~22 kg and its own growth curve doesn't reach 24 kg until ~12–14 months — so the operational plan and the lender-facing projection describe different businesses. *(A1-H1, A1-M4, A5-M2; corroborated twice.)*
- **G-2 (HIGH→verified) — dashboard suggestions bypass `species_profile`.** `services/dashboard.py:138-183` hardcodes goat literals (10 mo/22 kg breeding floors, 8-month sale age, 24 kg sale weight, gestation day 100/135 windows) with no farm-type check — on a buffalo dairy farm these suggestions are nonsense. The adjacent comment claims suggestions "must never contradict the authoritative write paths," yet the thresholds themselves are goat-only.
- **G-3 (HIGH→verified) — kids are billed adult rations.** Kids in RECOVERY inherit the RECOVERY bucket's per-head rate (seeded ~1.5 kg/day adult ration; doe + twins ≈ 4.5 kg/day, ~4–5× a kid's intake) because the recipe CASE (`services/feeding.py`) has no kid branch, and the seeded CREEP recipe is unreachable from any bucket/age rule. Corrupts feed-cost accounting on every goat farm.
- **G-4 (MED) — buck management is prose.** The 1:20 buck:doe ratio and 7-day rotation (`constants.py:34-35`, bucket text in `seed.py:76`) are never counted on the breeding write path; red-team recorded **1 buck × 21 same-day services, all 201**.
- **G-5 (MED) — quarantine leaks.** Day-0 purchase→sale from the 45-day QUARANTINE succeeds (executed); the quarantine ration (~2.0% BW DM) is below the ~3% maintenance DMI the simulator itself assumes.
- **G-6/G-7 (MED) — seed prose ≠ behavior.** The DELIVERY bucket's promised 5–10-day post-kidding stay never happens (doe goes to RECOVERY same day); the pre-kidding ET+TT template promises two doses 15 days apart but generates one task at EKD−40.
- **G-8 (MED) — two incompatible feed models.** Ops lactating ration ≈3.0% BW DMI at ~54% concentrate vs simulation 4.5% BW at 20%; buck:doe 20 (ops) vs 25 (sim).
- **G-9 (LOW)** — manual ledger rows can mint `ANIMAL_SALE` income with no animal link; fabricated revenue mixes into category P&L. Zero behavioral tests exist for the ratio/rotation/sale-window rules.

---

## B. DAIRY FARM (Murrah buffalo) — verdict: **one blocker + systemic "rules that exist only in the simulator"**

### What was verified correct
Gestation 310 d (window 300–325, band 270–350); PD at +60 d; 90-day calf milk-weaning; 10-day fresh pen; 24-h calf separation into sexed calf buckets; dry-off task at EDD−60; Wood lactation curve parameters within published Murrah fits (peak day 65 within 57–73; b=0.6 within 0.465–0.677; persistency ~0.89–0.93); 2,100 L/305-d yield (defensible for proven purchases; breed avg 1,752); 45% AI conception; sexed-semen ×0.85 conception/0.90 female (matches published 38.6–40%); ₹840–850/kg-fat procurement (Sangam/Vijaya 2025-26); TMR recipes all sum to 100 kg; paise-exact ₹/kg-fat income; unique (animal, date, shift) + row-lock kills duplicate/race milk entries; fat testing has separation of duties; breeding records are immutable (cull counter tamper-proof); calving requires positive PD ≥60 d with one kidding per breeding.

### Findings
- **D-1 (BLOCKER → verified) — a dairy dam with a live calf can never leave the fresh pen.** `services/tasks.py:396-400` rejects completing the +10-day RESTING move while "a kid survives" — the surviving-calf check is goat biology (kids stay with the dam); dairy calves are separated at 24 h but remain ACTIVE offspring, so the duty 409s on complete *and* the skip path refuses ("only way out"). Every normal calving strands the dam in RECOVERY for 80 extra days on the fresh ration with a permanent zombie overdue duty. The branch is even marked `# pragma: no cover`.
- **D-2 (HIGH → verified) — two goat-era CHECK constraints crash legitimate buffalo records.** `models/breeding.py:59-63` caps `loss_date` at breeding+200 d and `:109-112` caps `ultrasound_result_date` at +200 d, while the buffalo service layer allows to day 350 (`species.py` max_gestation_days). Reproduced as HTTP 500 on `POST /api/breeding/{id}/abort` at day 250 and on a day-210 PD entry.
- **D-3 (HIGH) — milk is accepted for animals that cannot be milking.** Only ACTIVE+female is checked; red-team recorded milk for a dry DELIVERY buffalo, a pregnant heifer, and a 105-day-old calf. Herd totals and cull league tables are freely inflatable by any `milk.manage` worker.
- **D-4 (HIGH) — milk history is financially untethered.** No chronology check (19-months-before-birth entries accepted); no sold-vs-produced reconciliation (1,000,000 L "sold" against 319.5 L produced books fine; ₹1e9 manual MILK income has zero provenance).
- **D-5 (HIGH) — the 60-day voluntary waiting period is not enforced** anywhere in the service layer (AI recordable the day after calving); VWP exists only in the simulator.
- **D-6 (HIGH) — the advertised 3-service cull rule does not exist operationally.** The service layer uses species-blind `MAX_FAILED_CYCLES_BEFORE_CULL=2`, flagging ~30% of healthy buffalo as cull candidates one service early; four consecutive failed services accepted with the flag gating nothing.
- **D-7..D-12 (MED) — calf economics leak.** The auto "wean calves → FOUNDATION" duty is a silent no-op for dairy (calves are never in RECOVERY) while manual `FEMALE_KIDS→FOUNDATION` completes the 90-day program on day 1; pre-weaning calf deaths never realign `KidEntry` or cancel the dam's weaning duty (dashboards overstate); the kidding kids-cap (10) is species-blind for buffalo (10 "calves" accepted → fabricated inventory, day-0 ₹60k male-calf sales); the simulator never deducts the ~5–7% of milk fed to calves despite the farm's own 90-day whole-milk protocol; three inconsistent dry-off models (task at EDD−60, TMR switch at EDD−21, sim ~128-day dry) vs the published 101–150-day Murrah dry period; per-animal daily yield is bounded only by shift caps (300 L/day) and `milk.manage` can re-key 7 L → 100 L.
- **D-13 (LOW) — heifer breeding floor 22 mo (ops) vs 24 mo (sim)**; 22 mo ⇒ AFC ≈ 33 mo vs the published well-managed 36–40 mo (NDRI herd avg ~43.7). Aggressive but defensible only with proven weights (≥340 kg enforced).

---

## C. SIMULATION & FINANCE (both farms) — verdict: **exact machinery, corrupted inputs**

All core math verified exact (EMI error 1.8e-10 vs textbook; amortization lands on ₹0.00; mass balance 5.7e-14 over 120 months; Wood curve sums to exactly 2,100 L; Monte-Carlo deterministic with stable P5/P95 <2% from 100→400 runs; no look-ahead bias in calibration). The defects are in calibration and defaults:

- **S-1 (HIGH → verified) — mortality calibration inflates kid death 3.4×.** `services/simulation_calibration.py:669-671` annualizes a 3-month pre-wean exposure via 1−(1−obs)⁴ while the engine consumes `kid_pre_weaning` as a whole-phase rate: an observed 10% loss is modelled as 34.4% dead. Every auto-calibrated goat projection overstates mortality dramatically.
- **S-2 (MED → mechanism verified) — the optimizer is dead on both flagship presets.** Year-1 DSCR is negative by construction (interest-only moratorium, zero Y1 sales) so `min_dscr < floor` fails every candidate: `run_optimization` returns `recommended=None` on 37/37 goat and 135/135 dairy candidates; MC `prob_dscr_below_one` is degenerate at 1.00.
- **S-3 (MED) — labour default makes the flagship goat farm unviable.** `labour_per_head_threshold=60` counts standing herd (kids+growers) vs the cited TNAU/NABARD norm per 50 does; the model hires 3 labourers (₹5.6–8 L/yr) for a 50+2 unit → default Osmanabadi NPV **−₹37.5 L**, BCR 0.58, break-even ₹781/kg meat.
- **S-4 (MED) — dairy preset compounds feed 6%/yr vs milk 5%/yr for a decade** with no procurement pass-through (Vijaya revised ₹82→₹85/L within 2025): EBITDA +₹13.6 L (Y1) → negative from Y3, NPV **−₹92 L**; the ₹900/kg-fat default is +5.9% over the verified ₹850 procurement rate.
- **S-5 (LOW)** — dairy-mode sire sizing double-counts the lactation overlay (3 sires instead of 2); milk-planner convergence is flattered by the placement transient (flags its own converged plan `achievable=False`); kid-mortality default 10% sits at the best decile of field data (NABARD convention 15%; field 10.9–20.4%) — optimistic for a lender-facing default.

---

## D. CROSS-CUTTING

**Backend security (A7): 0 critical / 0 high / 1 medium.** No IDOR (every lookup ANDs `farm_id`); tenancy triple-enforced incl. composite tenant FKs making cross-tenant rows physically impossible; zero raw SQL/injection surface; timing-equalized login; refresh rotation with reuse-detection family revocation; idempotency scoped to (farm, actor, operation, key) with HMAC-keyed fingerprints; capacity races closed by advisory+row locks. Medium: `GET /api/simulation/herd-snapshot` requires only `simulation.view` while returning herd structure that `/simulation/calibration` gates behind all module view permissions. Lows: register endpoint is an email-existence oracle; docker-compose default DB password; per-process rate limits correct only under the hard-coded `--workers 1`; no forced worker-password rotation.

**Frontend (A8): 0 critical / 0 high / 1 medium.** Tokens never touch storage accessible to XSS; cross-tab refresh single-flighted via Web Locks; farm switch cancels in-flight queries and clears cache (procedural fence — held, but farmId is not in query keys); idempotency keys survive reloads; 47-spelling path-injection corpus defeated. Medium: the animals list blind-casts `?bucket/sex/status` URL values into generated enums → hostile link = permanent 422 loop with a self-defeating Retry button (probe-verified).

**Contract (A3): 0 high / 4 medium.** Route contract exactly in sync 87/87/87 (live app = openapi.json = generated client), CI-enforced; models ↔ 45 migrations fully consistent; money is `Numeric(14,2)` everywhere. Mediums: 53/86 routes raise codes absent from the contract incl. one genuine undocumented 500 (`/api/simulation/calibration` corrupt path); ~50 enum-ish response fields typed as plain `string`; `tag_number` hardcodes `max_length=50` breaking the constants single-source chain; the schema-parity test covers only 6/17 enums and 3/8 caps.

---

## Recommended fix order

**P0 — ship-stoppers (this week)**
1. D-1: species-aware `_litter_has_surviving_kid` / skip semantics in `complete_task` (fresh-pen exit for dairy).
2. D-2: Alembic migration widening `ck_breeding_loss_within_max_gestation` / `ck_breeding_records_result_within_max_gestation` to 350 d (or species-aware), eliminating the two 500s.
3. S-1: fix `_annualized_fraction` usage for whole-phase mortality classes.

**P1 — financial integrity (next sprint)**
4. D-3/D-4: lactation-context check + chronology fence + sold-vs-produced reconciliation on milk and MILK income.
5. D-5/D-6: enforce VWP and the species-aware 3-service cull rule in the service layer.
6. G-1: wire the meat-sale window into the write path (or a hard warning) and align the simulator's sale age/weight to it.
7. G-3: kid feeding branch (CREEP) so kids stop being billed doe rations.

**P2 — correctness & trust**
8. G-2: `species_profile`-driven dashboard suggestion thresholds.
9. S-2/S-3/S-4: optimizer DSCR moratorium-aware; labour threshold per-does; dairy price escalators with pass-through; ₹850/kg-fat.
10. D-7..D-12: calf-death realignment, species-aware litter caps, dry-off model unification, milk re-key bounds.
11. X-1 herd-snapshot permission scope; F-1 animals URL-enum whitelist; contract 500/response-code hygiene.

**P3 — hardening** — behavioral tests for every rule currently asserted only as constants (A5-L3), parity-test coverage completion, register enumeration, worker password rotation, quarantine-exit policy.

## Report index
| File | Audit |
|---|---|
| `01_GOAT_INDEPENDENT_AUDIT.md` | A1 — goat full-stack independent (0B/3H/6M/9L/5I) |
| `02_DAIRY_INDEPENDENT_AUDIT.md` | A2 — dairy full-stack independent (0B/4H/4M/5L/5I) |
| `03_CONTRACT_CONSISTENCY_AUDIT.md` | A3 — cross-stack contract (0B/0H/4M/5L/5I) |
| `04_SIMULATION_FINANCE_AUDIT.md` | A4 — simulation/finance math (0B/1H/3M/4L/6I) |
| `05_GOAT_ADVERSARIAL_AUDIT.md` | A5 — goat red-team (0B/0H/2M/3L) |
| `06_DAIRY_ADVERSARIAL_AUDIT.md` | A6 — dairy red-team (1B/3H/4M/3L) |
| `07_BACKEND_SECURITY_ADVERSARIAL_AUDIT.md` | A7 — backend security red-team (0C/0H/1M/7L) |
| `08_FRONTEND_ADVERSARIAL_AUDIT.md` | A8 — frontend red-team (0C/0H/1M/5L/2I) |
