# Red Team Master Synthesis — 8 Independent Adversarial Audits

**Date:** 2026-09-04 · **Target:** Herdly (`goat_saas`) — multi-tenant goat/buffalo dairy farm management SaaS (FastAPI + PostgreSQL backend, Next.js SPA frontend)
**Method:** 8 independent read-only adversarial audits, each with a distinct attacker persona and mandate, run in parallel. No file was modified; no test suite, server, or network call was executed. Every finding is code-verified with file:line evidence.

> **Remediation:** every fixable finding above was fixed and attack-tested the same day — see `09_REMEDIATION_LOG.md` (fix-by-fix evidence, proof tests in `backend/tests/test_redteam_remediation_2026_09_04.py`, explicit deferrals with rationale) and `10_VERIFICATION_SUMMARY.md` (full-suite verification numbers).

| # | Auditor | Mandate | Report |
|---|---------|---------|--------|
| 1 | The Neighbor | AuthN/AuthZ, tenant isolation, IDOR, JWT/sessions | `01_AUTH_TENANT_ISOLATION.md` |
| 2 | The Injector | SQL/operator injection, Pydantic abuse, migrations, JSONB, error leakage | `02_INJECTION_DATA_LAYER.md` |
| 3 | The Rogue Farmhand | Domain/biological invariants, state machines, species fences, lineage | `03_DOMAIN_INVARIANTS.md` |
| 4 | The Embezzler | Money integrity, pricing math, ledger, simulation financials | `04_FINANCE_MONEY_INTEGRITY.md` |
| 5 | The Race Runner | Concurrency, double-submit, idempotency, transactions | `05_CONCURRENCY_RACES.md` |
| 6 | The XSS Ghost | Frontend XSS, token handling, client-side trust, open redirects | `06_FRONTEND_CLIENT_SECURITY.md` |
| 7 | The Infra Wrecker | Docker/compose, secrets, CI/CD, supply chain, CORS, DoS | `07_INFRA_CONFIG_SUPPLY_CHAIN.md` |
| 8 | The Verification Skeptic | Test-suite integrity, mutation-claim verification, simulation math | `08_TESTS_SIMULATION_MATH.md` |

---

## Executive verdict

**No Critical vulnerability found by any auditor.** This codebase survived a full-spectrum adversarial attempt unusually well — it visibly carries the scars of prior audit/remediation rounds (2026-09-01..03), and the classic SaaS kill zones (SQL injection, XSS, IDOR/cross-tenant access, mass assignment, JWT forgery, lost-update races, float money math, committed secrets) are all genuinely closed, not cosmetically.

**What remains is 4 High, ~9 Medium, and ~17 Low findings**, concentrated in three themes:

1. **Semantic domain holes** — the API enforces *chronological* and *state-machine* integrity but not *biological* or *lineage* integrity (buck-to-daughter breeding), and not *lactation-linked* milk plausibility.
2. **Policy gaps a fraudster needs no technical bug for** — assets can leave the farm with no price booked; a hard-coded 10% fence authorizes permanently renewable fictitious milk income.
3. **Quality-claim distortion** — the mutation-testing headlines overstate their actual scope; the strongest remaining attack surface may be the project's confidence in itself.

---

## HIGH findings (4)

### H1. No inbreeding/lineage check — a buck can be bred to his own daughter (Audit #3-1)
`backend/app/services/breeding.py:420-487`, `services/breeding.py:217-262`
`POST /api/breeding {"doe_id": X, "buck_id": <her sire>}` → 201. Eligibility checks sex/age/weight/bucket/VWP/ratio — never `sire_id`/`dam_id`, even though both columns exist and are written at kidding. The README-promised "buck rotation (inbreeding avoidance)" has zero server-side enforcement; the pedigree graph is silently and permanently corrupted, and daughter-retention finance logic inherits the damage. Only `doe_id <> buck_id` is constrained.
**Fix:** reject in `create_breeding_record` when `buck.id in (doe.sire_id, doe.dam_id)` or `doe.id in (buck.sire_id, buck.dam_id)`.

### H2. Milk ledger decoupled from lactation biology — fabricated yields + 10% overbooking fence (Audit #3-2 + Audit #4-2)
`backend/app/api/milk.py:227-258` + `backend/app/api/finance.py:623-646`
Two compounding holes: (a) `has_calved` is existence-only and chronology checks only birth/purchase date — a buffalo that calved 2026-08-01 accepts a 15 L yield dated 2026-01-15 (mid-gestation/dry), permanently, repeated across months × herd; (b) the sold-vs-produced fence permits `sold ≤ produced × 1.10` with no reset — on a 200-Murrah dairy (~4,00,000 L/yr at ~₹55/L effective) that authorizes **₹22,00,000/yr of fictitious income**, internally consistent with the provenance validator. Daily caps (20–40 L) bound one day, not cumulative fabrication.
**Fix:** require `date ≥ max(KiddingRecord.date)` (imported-in-milk: `≥ purchase_date`), reject dry-window dates, and shrink/window the 10% allowance.

### H3. Off-ledger asset movement — SOLD/CULLED without `sale_price` books zero revenue (Audit #4-1)
`backend/app/api/animals.py:1205-1223`, `schemas/animals.py:233`; symmetric gap `services/purchases.py:202` + `schemas/purchases.py:32`
The schema enforces only "price requires SOLD" — never "SOLD produces a ledger row". `{"new_status":"SOLD"}` with no price: herd count drops, profile says SOLD, and `monthly_pnl`/`total_income` show nothing. 20 bucks × ₹30,000 cash = ₹6,00,000 invisible. Symmetrically, a purchase batch with `total_price=None` still creates the animals but books no expense — inventory acquired cost-free on the books.
**Fix:** require `sale_price` (₹0 allowed) for SOLD/CULLED — or emit a flagged ₹0 provenance row; require totals on animal-creating purchases.

### H4. Quality-claim distortion — mutation-testing headlines overstate their scope (Audit #8-1, #8-2)
`backend/pyproject.toml:70-75`; `frontend/reports/mutation/full-force-baseline.json`
"Backend kill rate 90.5% over 10,345 mutants" covers only `app/simulation/*.py` + one schema (~15% of the backend) and runs only 10 hand-picked DB-free test files against mutants. Frontend's only full-repo snapshot is **64.1% with 4,459 survivors**; the flagship simulation UI sits at **62% with 568 live survivors even after remediation**. The deltas in commit messages are real, but no artifact supports a repo-wide number. This is a High because the residual risk of the whole project is calibrated against an overstated safety signal.
**Fix:** re-state the claims with scope, and run one authoritative full-repo Stryker pass.

---

## MEDIUM findings (9)

| # | Finding | Where | Auditor |
|---|---------|-------|---------|
| M1 | Unverified self-registration → email squatting; permanent worker-provisioning DoS on any victim email; no password-recovery path exists at all | `api/auth.py:678-740`, `api/team.py:855-860, 64` | #1 |
| M2 | Breeding method not species-gated: AI/AI_SEXED accepted on goat farms (erases sire lineage, kids get `sire_id=None`); "AI" label bypasses the 1:20 buck:doe ratio cap entirely | `schemas/breeding.py:16-37`, `api/breeding.py:272-281` | #3 |
| M3 | Keyless replay double-commits: `POST /api/feeding/dispense` double-debits stock; `POST /api/finance/new` double-books manual money — no DB natural key, idempotency opt-in per client | `services/feeding.py:589-621`, `api/finance.py:649-704` | #5 |
| M4 | `POST /api/breeding` & `/api/kidding` don't declare `Idempotency-Key` — frontend retry after a lost response yields a misleading 409 for a committed operation | `api/breeding.py:214-221`, `api/kidding.py:194-201` | #5 |
| M5 | Cross-species weight bands: birth weight and live weights accepted 0–1000 kg for any species — one fabricated 950 kg "kid" permanently satisfies breeding weight gates and poisons dashboards | `schemas/kidding.py:29`, `schemas/animals.py:158`, `models/animals.py:118-123` | #3 |
| M6 | 3-service cull rule is advisory for owners (unlimited services past it; one conception resets the streak) | `services/breeding.py:451-458, 646, 786-805` | #3 |
| M7 | `test_murrah_monte_carlo_milk_price_risk` is test theater — asserts percentile monotonicity, passes even if the milk-price draw is deleted | `backend/tests/test_dairy.py:883-892` | #8 |
| M8 | Drought vs Monte-Carlo feed-price inconsistency: home-grown green fodder is shocked in MC but not in drought months (~₹200k/yr of the flagship preset's dominant feed cost) | `simulation/montecarlo.py:88-90` vs `simulation/market.py:63-72` | #8 |
| M9 | Milk sold-vs-produced 10% fence ( ₹22L/yr fictitious income) — dual-listed under H2(b) as it compounds the lactation-window hole | `api/finance.py:623-646` | #4 |

## LOW findings (17, compact)

1. Register endpoint is a cross-tenant account-enumeration oracle (400-vs-201) — `api/auth.py:718-733` (#1)
2. Bearer-only `POST /api/auth/logout` kills every session (targeted device-lockout DoS) — `api/auth.py:1140-1153` (#1)
3. Multi-process deployment multiplies every in-memory auth/CPU budget; warning fires only if `UVICORN_WORKERS`/`WEB_CONCURRENCY` env is set (bare `--workers 4` is silent) — `main.py:322-336` (#1, #5 dual-confirmed)
4. No unbounded-growth guard on `idempotency_records` (purge capped 5k/hr vs unthrottled inserts) — `services/idempotency.py:299-313` (#2)
5. `TaskCreateIn.due_date` accepts year 0001/9999 → permanently-overdue tasks — `schemas/tasks.py:34` (#2)
6. Fat-based pricing multiplies litres directly by fat% (litre≠kg; ~3% systematic gap vs plant slips) — `schemas/finance.py:77-82`, `engine.py:1453-1456` (#4)
7. MC seed is client-chosen, persisted, and harvestable for lender-facing scenarios (runs can be 1) — `montecarlo.py:268`, `assumptions.py:684-689` (#4, #8 dual-confirmed)
8. Dry-off-60-days & 90-day weaning are reminder tasks, not enforced gates — no milk-vs-calving-window check — `services/breeding.py:663-688` (#3; feeds H2)
9. DB gestation CHECKs use a cross-species 350-day band (tight species window only at service layer) — `models/breeding.py:60-64` (#3)
10. `/api/milk/new` missing from frontend idempotency allowlist though backend supports it (double-submit → false 422) — `idempotent-request.ts:170-195` (#5)
11. CSP ships `'unsafe-inline'` (documented Next-16 framework gap; no injection sink exists today) — `next.config.ts:27` (#6)
12. Orval client trusts API responses with zero runtime validation (`as T` cast only) — `custom-instance.ts:36-41` (#6)
13. Default BACKEND_URL proxy hop is plaintext HTTP (bearer token + refresh cookie on the wire for multi-host deploys) — `next.config.ts:60` (#6)
14. All production-safety gates keyed to operator-declared `GOATFARM_ENVIRONMENT`; "development" + non-loopback bind = public HTTP stack with insecure cookies — `docker-compose.yml:71,135-138` (#7)
15. Known dev HMAC secret shipped as compose fallback default — `docker-compose.yml:79` (#7)
16. `frontend/.dockerignore` misses `reports/`, `.stryker-tmp*`, `*.tsbuildinfo` → leak into builder layer — `frontend/Dockerfile:13` (#7)
17. Simulation modeling nits: calf-milk charge ignores retained male calves (~50-60 L/mo overstated saleable milk); "exactly lactation_milk_litres" identity actually ~1.1% low via un-normalized seasonality; 30 zero-assertion smoke tests — `engine.py:1471-1479`, `lactation.py:23-25`, `test_unit_extended.py` (#8)

---

## Per-audit verdicts

| Audit | Verdict |
|-------|---------|
| #1 Auth & tenant isolation | **Strong.** Zero cross-farm accesses constructible; residual risk confined to the global identity layer (no email verification, no password recovery). |
| #2 Injection & data layer | **Clean.** Fully ORM-bound, parameterized, strict-typed; no injection/deserialization/constraint-bypass exploit exists. |
| #3 Domain invariants | **Unusually strong, two real wounds** (H1 lineage, H2a lactation window); chronology and state machines defended in depth. |
| #4 Finance | **Strong arithmetic, semantic gaps**: exact-Decimal drift-free ledger; the exploits are policy (H3 off-ledger sales, H2b 10% fence). |
| #5 Concurrency | **High.** Canonical lock ordering + DB uniques everywhere; three opt-in idempotency gaps are the only double-commit surfaces. |
| #6 Frontend | **Best-in-class for the stack.** No HTML sink, memory-only tokens, fail-closed navigation; only documented CSP gap remains. |
| #7 Infra & supply chain | **Top-tier.** Digest-pinned, least-privilege CI, fail-closed prod gates; residual risk is operator-optional environment labeling. |
| #8 Tests & simulation | **Tests are real (3,983 backend integration tests over live PostgreSQL; deep-mutation mirrors at 1e-9) but the headline claims overstate scope.** Simulation math survived line-by-line; defects are modeling-level, not arithmetic. |

## Recommended remediation order

1. **H1 + M2** — one choke point each in `create_breeding_record` (lineage reject + species-gate method). Cheapest, highest permanent-corruption value.
2. **H2** — lactation-window check in `add_milk_record` + shrink/window the 10% fence in `api/finance.py`.
3. **H3** — require (or default-₹0) `sale_price` on SOLD/CULLED and totals on animal-creating purchases.
4. **M3 + M4** — server-side required `Idempotency-Key` on money/stock POSTs; wire breeding/kidding into `execute_idempotent`.
5. **M1** — email verification + self-service password recovery (unlocks the enumeration fix too).
6. **M5/M6** — species-banded weights; audited owner-override for the cull rule.
7. **H4** — one authoritative full-repo Stryker run + rescoped mutmut claim; fix the theater test (M7) and the feed-shock inconsistency (M8).
8. Lows opportunistically; #14/#15 (environment gating) before any real customer deployment.
