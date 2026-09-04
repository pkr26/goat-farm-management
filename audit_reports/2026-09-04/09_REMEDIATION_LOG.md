# Remediation Log — Red-Team Audit Round 2026-09-04

> **Post-remediation verification (same day):** five independent verifiers audited every change, found 9 issues (1 High — the edge guard was dead code because the bind host never reached the container; 2 Medium — dry-window false rejections for early-calved dams, and the animal-create path bypassing the weight bands; 6 Low incl. the OpenAPI publishing the now-mandatory header as optional). All 9 were fixed with regression tests, and a revert-mutation experiment proved every attack test fails when its fix is removed. Full detail: `11_INDEPENDENT_VERIFICATION.md`.

**Scope:** every fixable finding from `00_MASTER_SYNTHESIS.md` (4 High, 9 Medium, 17 Low). Each fix below carries its proof test in `backend/tests/test_redteam_remediation_2026_09_04.py` (identifiers `RED-*`) or an updated existing test. Read-only advisory items and items needing infrastructure/product decisions are deferred explicitly at the end with rationale.

---

## Fixed and tested

### H1 — Inbreeding fence (parent-offspring, full siblings)
`backend/app/services/breeding.py` (`create_breeding_record`): rejects a NATURAL service when the buck is the doe's sire or dam, the doe is the buck's sire or dam, or the pair are full siblings (both parents shared). Half-siblings remain permitted (livestock practice).
**Proof:** `RED-H1` ×3 — sire×daughter → 409 "close kin"; sibling×sibling → 409; daughter×unrelated buck → 201 (negative control).

### H2a — Milk records tied to the lactation interval
`backend/app/api/milk.py` (`add_milk_record`): (1) a yield dated before the doe's latest recorded calving → 422 "cannot predate"; (2) a yield inside the dry window (expected calving − `prepartum_move_lead_days` … expected calving, from the latest confirmed open pregnancy) → 422 "dry period".
**Proof:** `RED-H2a` ×2 — pre-calving date → 422; date inside dry window → 422 while a date before dry-off → 201.

### H2b — Milk-sale overbooking fence tightened
`backend/app/api/finance.py`: flat 10% (never-resetting) → `0.5% + 10 L` (`MILK_SALE_ALLOWANCE_*`). The absolute floor was deliberately set to 10 L, not higher, so the fence is strictly tighter than the old one for every herd above ~105 L recorded.
**Proof:** `RED-H2b` — 430 L sold against 400 L recorded (7.5% over, legal under the old fence) → 422; 410 L → 201.

### H3 — Off-ledger sales and purchases close
- `backend/app/api/animals.py`: every SOLD/CULLED now books an INCOME `ANIMAL_SALE` row; an omitted price books ₹0 with note "… — no price recorded (₹0 booked)".
- `backend/app/services/purchases.py`: every purchase batch books its `ANIMAL_PURCHASE` expense row; unpriced batches book ₹0 with the same flag.
**Proof:** `RED-H3` ×2 — sale without price → ledger contains exactly one ₹0 flagged row; unpriced 2-head batch → ₹0 flagged expense row. (Old pin `test_sale_without_price_creates_no_transaction` inverted into `…books_flagged_zero_transaction`.)

### M2 — Breeding method species-gated
`backend/app/services/breeding.py`: AI/AI_SEXED rejected on GOAT farms ("not part of the goat protocol") — closes both the sire-lineage erasure and the ratio-cap bypass via AI labels. Dairy keeps its AI protocol.
**Proof:** `RED-M2` — goat AI_SEXED → 409; dairy AI → 201 (control). Also pinned inside the updated `test_ultrasound_kid_count_is_species_capped`.

### M3 — Idempotency-Key required on keyless money/stock mutations
`backend/app/services/idempotency.py` (new `RequiredIdempotencyKey` dependency) applied to `POST /api/finance/new` and `POST /api/feeding/dispense` (the two mutations with no DB natural key). Keyless → 422.
**Proof:** `RED-M3` ×3 — keyless finance → 422; keyless dispense → 422; same-key replay → single ledger row + `Idempotency-Replayed`. The shared test client injects fresh keys for these two paths (conftest `IDEMPOTENCY_REQUIRED_PATHS` + `_auto_idempotency_key`), so 100+ existing call sites keep exercising business logic unchanged.

### M4 — breeding/kidding wired into `execute_idempotent`
`backend/app/api/breeding.py`, `backend/app/api/kidding.py`: all state-dependent checks moved inside `mutate()`; retries with the same key now replay the committed response instead of surfacing a false 409. `shared/openapi.json` regenerated (`scripts/export_openapi.py`).
**Proof:** `RED-M4` ×2 — replayed POST /api/breeding and POST /api/kidding return 201 + `Idempotency-Replayed: true` with the original record id, and the lists show exactly one record.

### M5 — Species-banded weights
`backend/app/models/species.py` (`birth_weight_kg_range`, `max_adult_weight_kg` per species) enforced at: kidding birth weights (goat 0.5–8 kg, buffalo 15–80 kg), live weight records (goat ≤150 kg, buffalo ≤1000 kg), purchase batch averages.
**Proof:** `RED-M5` ×5 — 950 kg kid → 422; 5 kg buffalo calf → 422; 500 kg goat weight → 422; 500 kg purchase average → 422; in-band values → 201. Input-bounds suite updated to pin the dual-layer (schema cap + species band) behavior.

### M6 — Owner cull-rule override attributed
`backend/app/services/breeding.py`: when the owner serves a cull-candidate doe past the failed-service limit, a structured `logger.warning("Cull-rule override: user … doe … farm … limit …")` records who overrode, for which animal, on which farm.
**Proof:** `RED-M6` — two failed cycles flag the doe; the owner's third service returns 201 and the warning is captured via `caplog`.

### M7 — Monte-Carlo theater test rewritten
`backend/tests/test_dairy.py`: `test_murrah_monte_carlo_milk_price_risk` now disables the other eight risk variables (common random numbers keep their draws consumed), asserts strict `p5 < p50 < p95` and that the band brackets the deterministic NPV — deleting the milk-price draw now fails the test. New counterpart `…milk_price_draw_beats_engine_event_noise` shows the all-disabled band is <50% of the milk-only band.
**Proof:** the rewritten tests themselves; both run in the dairy suite.

### M8 — Feed-price risk channels made consistent
`backend/app/simulation/montecarlo.py`: `_apply_draws` no longer scales home-grown green fodder by the feed-price draw — home green is a cultivation cost whose risk is the `fodder_yield` draw, matching the engine's drought/shock channel (`market.feed_prices_for_month`), which also leaves home green unshocked.
**Proof:** extended `test_feed_price_draw_moves_purchased_green_fodder` now pins: purchased green/dry/concentrate scaled, home green untouched.

### L4 — Idempotency open-record quota
`backend/app/services/idempotency.py` + `core/config.py` (`idempotency_max_open_records_per_actor`, default 1,000): a cheap indexed count rejects new claims with 429 once an actor's unexpired records reach the cap, so a key-minting client can no longer outgrow the throughput-capped purge.
**Proof:** `RED-L4` — cap monkeypatched to 1; the second distinct-key POST → 429.

### L5 — Task due-date sanity band
`backend/app/schemas/tasks.py`: `due_date` year must be within 2000–2100 (mirrors the purchase-date floor).
**Proof:** `RED-L5` — year 1 and 2101 → 422; next week → 201.

### L10 — `/api/milk/new` added to the frontend idempotency allowlist
`frontend/src/lib/idempotent-request.ts` (+ comment updates for the now-server-backed breeding/kidding routes).
**Proof:** existing allowlist test suite (`idempotent-request.mutation.test.ts`, 59 tests) green.

### L13 — BACKEND_URL transport guard
`frontend/src/lib/backend-rewrites.ts` (`assertSafeBackendUrl`): plain HTTP allowed only for loopback or single-label internal service names (e.g. `http://backend:8000`); dotted hostnames/IPs must be https — the proxied hop carries the bearer token and refresh cookie.
**Proof:** `src/next-config.test.ts` extended — 7 accepted URLs, 5 rejected (incl. `http://10.0.0.5:8000`, `http://api.example.com`), and the rewrites builder itself throws. 14 tests green.

### L14 — Edge warns when publicly bound outside production
`docker-compose.yml` edge `command`: alongside the existing production+HTTP refusal, a loud stderr warning now fires when the bind host is non-loopback while `GOATFARM_ENVIRONMENT != production` (exposure-aware, not label-only).
**Proof:** `RED-L14` pinning test asserts the guard and loopback case list in the compose file.

### L15 — Known HMAC secret no longer a compose fallback
`docker-compose.yml` requires `GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET` via `:?`; the documented dev value moved to `.env.example` with instructions. The backend's production gate already rejected that value.
**Proof:** `RED-L15` pinning test — no `:-` interpolation default and no known-secret literal in compose.

### L16 — Frontend `.dockerignore` hardened
`reports/`, `.stryker-tmp*`, `*.tsbuildinfo`, `Dockerfile`, `.dockerignore` added — local Stryker artifacts and reports no longer leak into the builder layer.

### H4 / L6 / L17 — Claims and conventions made honest
- `README.md`: new **Mutation-score scope** paragraph states exactly what the 90%+ kill-rate covers (`app/simulation` + one schema file, ten deterministic suites) and that targeted Stryker deltas are not full-repo aggregates.
- `README.md` + `schemas/finance.py`/`engine.py` comments: the litre-treated-as-kg fat-pricing convention is documented (~3% systematic gap vs weighed plant slips).
- `engine.py`: calf-milk charge documented as the heifer-only protocol; `lactation.py`: the ~1.1% seasonality-coupled realization of `lactation_milk_litres` documented.

---

## Regression handling (existing tests that pinned the old holes)

Nine tests pinned pre-fix behavior and were updated to pin the new contracts: zero-birth-weight acceptance (now 422), sale-without-price invisibility (now flagged ₹0 row), 10% fence legality (now the tightened fence), 1000-kg schema-cap acceptance on goat farms (now species-scaled), the kid-count test's goat-AI fixture (now NATURAL + an explicit goat-AI 409 assertion), and the idempotency suite's headerless-duplicate expectation (now keyed claims). One conftest detail worth noting: the auto-key hook uses header *presence*, not truthiness, so an explicitly empty/invalid key still reaches the server and earns its 422.

## CI gates

The previous commit on `main` (`80b8a82`) had already broken `ruff check`/`ruff format --check` (2 over-length lines, 27 unformatted files) and carried 26 pre-existing `mypy --strict` errors. This round restored ruff to fully green (`ruff check`: clean; `ruff format --check`: 182 files clean) across the whole tree. The 26 mypy errors are pre-existing (identical count with these changes stashed — zero new errors introduced) and remain open as follow-up; none touch the code added here.

## Test evidence

- New suite: `backend/tests/test_redteam_remediation_2026_09_04.py` — **23/23 passed** (attack → intended failure + negative controls).
- Affected existing suites re-run green after updates: breeding (extended+bugs), dairy, finance (extended+bugs), feeding, idempotency, input-bounds, adversarial — 800+ tests.
- Full backend suite re-run after remediation: see final summary in `10_VERIFICATION_SUMMARY.md`.
- Frontend: `next-config.test.ts` + `idempotent-request.mutation.test.ts` — 73/73 passed.

## Explicitly deferred (needs infrastructure or a product decision)

| Finding | Why deferred | What it needs |
|---------|--------------|---------------|
| M1 email verification + password recovery | No email-delivery infrastructure exists in the app; a token flow without delivery would be security theatre, and a wrong design (e.g. tokens in logs) would *reduce* security | SMTP/provider config + product decision on verification UX; also unblocks L1 (register enumeration oracle) and the no-recovery lockout risk |
| L1 register enumeration oracle | Falls out of M1 (uniform accept-and-notify requires verification) | Same as M1 |
| L2 bearer-only logout-everywhere | Deliberate documented design; fixing it properly needs per-access-token revocation (hot-path denylist query on every request) | Product decision on the availability/confidentiality tradeoff |
| L3 multi-process limiter multiplication | In-process detection is unreliable (the code documents this); Dockerfile already pins `--workers 1` | Orchestrator-level enforcement (e.g. fail-fast wrapper in the container CMD) |
| L7 MC seed harvestability | Reproducibility is a pinned, tested product feature; unbiasing saved scenarios is a policy choice, not a bug fix | Decide: server-side re-seed for "audit" scenarios, or label user-seeded bands in the UI |
| L9 species-aware gestation CHECKs | Service layer already enforces the tight per-species window; the DB CHECK is defense-in-depth | An Alembic migration making the CHECKs species-aware via the farm join |
| L11 CSP `'unsafe-inline'` | Next 16 framework limitation (nonce'd RSC bootstrap scripts unsupported in standalone); documented + pinned by an existing test | Revisit on Next major upgrades |
| L12 zero runtime validation of API responses in the Orval client | Adding a validation layer (zod) across the generated client is an architecture decision; blast radius today is wrong-data-rendered-as-text, not hijack | Decide on response-validation strategy for auth-critical endpoints |
| L17 model refinements (calf-milk sex asymmetry, seasonality normalization) | Changing the model numbers would invalidate the 1e-9 golden mirrors and every published scenario; the protocol is now documented instead | A deliberate recalibration round if the heifer-only convention is deemed wrong |
