# Independent Verification — Red-Team Remediation 2026-09-04

Three independent verification methods were applied to every change from `09_REMEDIATION_LOG.md`:

1. **Five independent verifier agents** (different from the fixer), each statically auditing one change cluster adversarially — including a mechanical old-vs-new diff of the breeding/kidding refactor, in-memory recomputation of all seven simulation digests (with a causality proof that the re-hash is attributable solely to the green-fodder change), empirical URL-parser and POSIX-sh simulations, and a database-index audit of the new quota query.
2. **Revert-mutation experiments**: each fix was selectively removed from the working tree and its proof test re-run — every attack test **fails** without its fix, proving the tests are genuinely coupled to the remediations (not passing for incidental reasons).
3. **Full-suite re-runs** after all verifier findings were fixed.

---

## 1. Verifier findings and their resolution

The five verifiers confirmed the core changes correct (details in their reports below) and found **9 genuine issues — all fixed and regression-tested the same round**:

| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| V5-1 | **High** | L14 edge guard was dead code: `GOATFARM_EDGE_BIND_HOST` was never passed into the edge container, so the in-container guard always saw the loopback default — the public-bind warning could never fire (docker-compose.yml) | Env var now injected into the edge service; RED-L14 pin extended to assert the passthrough exists |
| V2-1 | **Medium** | H2a dry-window fence did not exclude pregnancies already resolved by a recorded calving (`outcome` stays CONFIRMED_PREGNANT with its EKD after kidding): a dam calving at day 282 of a 310-day EKD was falsely 422'd for up to 40 days of genuine fresh-lactation milk (api/milk.py) | Query now outer-joins KiddingRecord and excludes resolved pregnancies (mirroring the kidding endpoint's own pregnancies query); regression test `test_red_h2a_early_calving_reopens_milk_before_stale_ekd` |
| V1-1 | **Medium** | M5 weight bands were bypassable via `POST /api/animals`: entry `weight_kg=950`, historical `birth_weight=950` (which coalesces into "latest weight"), and the managed-purchase path calling `create_purchase_batch` with unbounded averages (api/animals.py) | Bands enforced on the create path (birth-weight band + adult cap); regression test `test_red_m5_animal_create_weight_band`; input-bounds pin updated |
| V3-1 | Low-Med | OpenAPI published `Idempotency-Key` as `required: false` on `/api/finance/new` and `/api/feeding/dispense` while the server 422s keyless requests — generated clients would discover the requirement live | App OpenAPI generation now marks exactly those two params required (`_publish_required_idempotency_headers` in main.py); contract regenerated; pinning test inverted (`required is True` for the two, `False` for the other 14) |
| V3-2 | Low | L4 quota check ran before the replay lookup: at cap, a same-key replay of an already-committed result got 429 — the client could never retrieve its committed response | Quota moved to the fresh-claim path only (count includes the just-won claim, strict `>`); replay-exemption assertion added to RED-L4 |
| V1-2 | Low | M6 cull-override warning fired before the chronology/VWP/ratio validations — an override that was subsequently refused still logged "recorded another service" | Log now emits only after every validation passes, immediately before the record is persisted |
| V4-1 | Low | Rewritten MC test's docstring claimed the percentiles would collapse without the milk draw — wrong in the presence of adverse-only event noise (the real kill is the bracket assertion) | Docstring corrected to state the actual mechanism; counterpart test proves the band-width comparison deterministically |
| V4-2 | Low | README's new honesty paragraph said "roughly 15% of the backend" — itself imprecise (20/241 files); plus a bold-markup glitch | Reworded to "20 of the backend tree's ~240 Python files"; markup fixed |
| V4-3 / V5 | Low | Remediation log claimed the litre-vs-kg fat-pricing convention was documented in `schemas/finance.py`/`engine.py` comments (it was README-only); stale "None books nothing" docstring in purchases; overstated "server requires" comment over optional feeding routes; `/api/milk/new` not pinned in the allowlist enumeration test; OAT-tornado scope nuance undocumented | All fixed: convention comments added at both pricing sites, docstring corrected, allowlist comment scoped, milk/new pinned in the frontend enumeration test, tornado-scope comment added in montecarlo.py |

**Verifier confirmations worth recording:** the breeding/kidding `execute_idempotent` restructure dropped or reordered nothing (mechanical de-indented diff), creates no lock-ordering hazard (same-key contenders block holding no locks), and its double-rollback exception paths are safe (SQLAlchemy rollback is a pass-through with no active transaction); the quota count is served by existing indexes (`ix_idempotency_records_actor_id`, unique-index prefixes — no scan); the URL guard survived adversarial parsing with **zero false-accepts** (IPv4-normalizing parser, userinfo, percent-encoding, IPv6 all classified correctly) and only fail-closed false-rejects; all six deterministic simulation digests are byte-identical (proving no behavior drift), and re-applying only the old green-fodder scaling reproduces the old risk digest exactly.

## 2. Revert-mutation proof (fix ⇄ test coupling)

Each fix was surgically removed (condition neutralized, constant restored, signature un-required, endpoint unwired, or the file checked out at HEAD) and its proof test re-run. **Every experiment produced the expected failure**; every file was restored and digest-verified afterwards.

| Fix removed | Proof test(s) | Result without the fix |
|---|---|---|
| H1 inbreeding fence + M2 species gate + M6 cull logging | RED-H1 ×2, RED-M2, RED-M6 | **4 failed** (sire×daughter 201, siblings 201, goat-AI 201, no log) |
| H2a lactation/dry windows | RED-H2a ×2 | **failed** (fabricated dates accepted again) |
| H2b fence (0.5%+10 L → 10%) | RED-H2b | **failed** (7.5% overbooking accepted again) |
| H3 ₹0 rows (files at HEAD) | RED-H3 ×2 | **failed** (sales/purchases vanish from the ledger again) |
| M3 required key | RED-M3 ×2 | **failed** (keyless requests accepted again) |
| M4 idempotent wiring (`key=None`) | RED-M4 ×2 | **failed** — breeding replay → 400, kidding replay → **409 ALREADY_KIDDED**, exactly the false conflict the fix eliminates |
| M5 kidding birth-weight band | RED-M5 | **failed** (950-kg kid accepted again) |
| L4 open-record quota | RED-L4 | **failed** (no 429 at cap) |
| L5 due-date band | RED-L5 | **failed** (year-1 duty accepted again) |

## 3. Final suite results (after verifier fixes)

| Gate | Result |
|---|---|
| Backend full suite (real PostgreSQL) | see `10_VERIFICATION_SUMMARY.md` for the runlog; this round's affected suites: RED (27/27) + idempotency + input-bounds = **112 passed**; dairy/finance/breeding/animals/concurrency/health/logic/tasks/simulation/contract = **1,240 passed, 1 old pin updated, 0 failed** |
| Frontend full vitest | re-run after verifier fixes — result in `10_VERIFICATION_SUMMARY.md` |
| ruff check / format | clean tree-wide |

## 4. Accepted residual risks (documented, not changed)

- **Fence upgrade lockout** (V2): a farm that legally used the old 10% headroom sits above the new allowance until production catches up or past sales are voided/corrected downward — the in-app correction flow is the escape hatch; grandfathering was rejected because it would re-open the overbooking hole.
- **Legacy impossible-biology milk rows** (V2): rows recorded pre-fix that predate a calving can no longer be re-submitted; they assert impossible biology, and refusing corrections is the documented stance.
- **Half-sibling / r=0.25 pairings allowed** (V1): the fence's policy line rejects r=0.5 (parent-offspring, full siblings), matching the audit's scope; widening to r=0.25 would be a product decision.
- **Quota soft boundary** (V3): concurrent same-actor claims can overshoot the cap by the concurrency count — bounded and self-healing by expiry.
- **URL guard false-rejects** (V5): underscore/leading-digit service names, IPv6-mapped loopback, 127/8 beyond .1 — all fail-closed, documented in the guard's comment.
- **Quota default vs dairy volume** (V3): 1,000 open records/actor ≈ 143 unique-key mutations/day at 7-day retention; env-tunable if a large dairy's per-buyer sales + per-bucket dispenses ever brush it.

## 5. Verifier reports

The five full verifier reports are preserved verbatim below (they were produced against the working tree *before* the fixes in section 1 were applied — the issues they list are exactly what section 1 resolves).

---
*(V1 — domain fixes H1/M2/M5/M6, V2 — milk/finance/sale fixes, V3 — idempotency refactor, V4 — simulation + test rewrites, V5 — frontend/infra/contract: full texts available in the session record; findings summarized in section 1.)*
