# Verification Summary — Red-Team Remediation 2026-09-04

Final proof run for every fix in `09_REMEDIATION_LOG.md`. **After this summary was first written, an independent verification round (see `11_INDEPENDENT_VERIFICATION.md`) found and fixed 9 additional issues; the numbers below are the authoritative post-verification results.**

## Full-suite results (after all fixes, including verification-round fixes)

| Gate | Result |
|------|--------|
| Backend `pytest` (real PostgreSQL, throwaway DB) | **4,074 passed, 4 skipped (justified), 0 failed** — 23m 39s |
| — of which new red-team proof suite (`test_redteam_remediation_2026_09_04.py`) | **27/27 passed** (every attack now fails with the intended status + message, each with a negative control; includes the 3 verifier-driven regressions) |
| Frontend `vitest` (full, MSW) | **4,208 passed / 203 files, 0 failed** |
| Backend `ruff check` + `ruff format --check` | **clean tree-wide** (main's pre-existing breakage restored) |
| Backend `mypy --strict app` | 26 pre-existing errors from commit `80b8a82` remain (identical count with these changes stashed → **zero new errors introduced**); none touch code added here — follow-up item |
| Contract | `shared/openapi.json` regenerated; `Idempotency-Key` now correctly published **required** on `/api/finance/new` + `/api/feeding/dispense` and optional on the other 14 routes; contract-drift test green |
| Revert-mutation coupling | **every attack test fails when its fix is surgically removed** (12/12 experiments; see `11_INDEPENDENT_VERIFICATION.md` §2) |

## What the proof tests demonstrate (attack → outcome)

| ID | Attack (from the audit) | Now |
|----|--------------------------|-----|
| RED-H1 ×3 | Breed a buck to his daughter / full siblings | 409 "close kin"; unrelated sire still 201 |
| RED-H2a ×2 | Milk dated before calving / inside the dry window | 422 each; lactation-window and pre-dry dates still 201 |
| RED-H2b | Sell 430 L against 400 L recorded (7.5% overbook — legal under the old fence) | 422; 410 L (within 0.5%+10 L) still 201 |
| RED-H3 ×2 | Sell animals / buy a batch with no price | flagged ₹0 ledger rows, visible in finance; priced paths unchanged |
| RED-M2 | AI/AI_SEXED on a goat farm (ratio-cap + lineage bypass) | 409 "goat protocol"; dairy AI untouched |
| RED-M3 ×3 | Keyless money/stock POSTs; same-key replay | keyless 422 (finance + dispense); replay books exactly one row |
| RED-M4 ×2 | Retry POST /api/breeding or /api/kidding after a lost response | 201 + `Idempotency-Replayed` with the original record; no false 409 |
| RED-M5 ×5 | 950-kg kid, 5-kg buffalo calf, 500-kg goat weight/purchase avg | 422 "not credible"; in-band values 201 |
| RED-M6 | Owner silently serves past the cull rule | 201 + attributed `Cull-rule override` warning logged |
| RED-L4 | Mint fresh idempotency keys faster than the purge | second claim over quota → 429 |
| RED-L5 | Task due dates in year 1 / 2200 | 422 "between year 2000 and 2100"; normal duty 201 |
| RED-L14/L15 | Publicly-bound dev edge; known-secret compose fallback | guard + absence pinned in compose |

## Old-behavior pins updated to the new contracts (13 tests, 9 files)

`test_breeding_extended` (zero birth weight now 422), `test_finance_extended` (unpriced sale → flagged ₹0 row), `test_animals_extended` ×2, `test_health_extended` ×2, `test_logic` (unpriced batch books ₹0 flagged), `test_tasks_extended` ×2 (year band), `test_input_bounds` ×3 (species-scaled caps), `test_dairy` (goat-AI fixture → NATURAL + explicit goat-AI 409; MC theater test rewritten with draw-isolation + event-noise counterpart), `test_simulation_advanced` ×2 (home-green unshocked by the feed-price draw), `test_simulation_explain` (risk-scenario narrative digest re-hashed — deterministic digests unchanged), `test_animals_bugs` (fixture bred father×daughter — now an unrelated sire), `test_concurrency` (racing dispenses carry explicit distinct keys), `test_idempotency` ×2 (keyed-claim counts; header-presence semantics).

## Residual risk

- **Deferred items** (need infrastructure/product decisions — full rationale in `09_REMEDIATION_LOG.md`): email verification + password recovery (M1, also gates L1), bearer-only logout scope (L2), multi-process limiter enforcement (L3), MC seed policy (L7), species-aware gestation CHECKs (L9), CSP `'unsafe-inline'` (L11), response runtime validation (L12), simulation recalibration (L17 model form).
- **Pre-existing `mypy --strict` errors (26)** from the previous commit remain open; this round introduced none and restored ruff/format to green.
