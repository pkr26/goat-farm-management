# V2 — Independent Security/Contract Remediation Verification

- **Date:** 2026-09-01
- **Verifier:** V2 (independent; did not author any remediation under review)
- **Repo:** `/Users/pavankumarreddyreddem/Desktop/goat_saas/backend` (source read-only; scratch in `/tmp/v2_verify`; throwaway DBs `v2_verify_*_test`, all dropped afterwards; no servers — all probes in-process via `httpx.ASGITransport`)
- **Inputs:** `00B_REMEDIATION_LOG.md` claims verified against `07_BACKEND_SECURITY_ADVERSARIAL_AUDIT.md` (M-1/X-1, L-1, L-2, L-5) and `03_CONTRACT_CONSISTENCY_AUDIT.md` (M-1..M-4 = C-1..C-4)

## Verdict summary

| # | Item | Verdict |
|---|---|---|
| 1 | X-1 herd-snapshot RBAC | **VERIFIED** |
| 2 | L-5 forced worker rotation | **VERIFIED** |
| 3 | C-1 declared error responses (+500 on calibration) | **VERIFIED** |
| 4 | C-2 Out-schema enums ↔ TS unions | **VERIFIED** |
| 5 | C-3/C-4 tag bound parity + parity test ≥14 | **VERIFIED** |
| 6 | Migrations (fresh DB, single head, +350 CHECKs) | **VERIFIED** |
| 7 | L-1 register per-email limiter isolation | **VERIFIED** |
| 8 | Compose `POSTGRES_PASSWORD :?` | **VERIFIED** |
| 9 | Requested test suites | **VERIFIED — 678 passed / 0 failed** |
| 10 | Regression hunt (re-exploitation) | **VERIFIED — all closed** |

**FAILED: 0 · PARTIAL: 0 · VERIFIED: 10/10.**

---

## 1. X-1 — `GET /api/simulation/herd-snapshot` cross-permission disclosure — VERIFIED

**Code.** `app/api/simulation.py:646-657`: the route signature now takes `perms: SimView` **and** `animal_perms: AnimalsView`, with an on-site comment explaining the animal-register read. This mirrors the sibling `/calibration` design the original audit contrasted against.

**Live probe** (fresh throwaway DB; owner → farm → custom role `Sim Consultant` with `permissions: ["simulation.view"]` → worker created under it → worker logged in and rotation completed so the L-5 gate could not mask the result):

| Request | Result |
|---|---|
| Worker with only `simulation.view` → `GET /api/simulation/herd-snapshot` | **403** `Missing permission: animals.view` |
| Same worker → `GET /api/simulation/defaults/breeds` (SimView-only route) | 200 (control: the token/role works) |
| Owner → `GET /api/simulation/herd-snapshot` | 200 with the nine cohort counts |

The original M-1 exploit (consultant role reconstructing herd structure) is closed; the 403 is attributable specifically to `animals.view`, not to auth state or the rotation gate.

## 2. L-5 — forced first-login rotation for provisioned workers — VERIFIED

**Code.**
- `app/api/team.py:868` — `create_worker` persists `must_change_password=True` on the provisioned User.
- `app/api/team.py:1096` — owner `reset-password` re-flags the account.
- `app/deps.py:181-188` — any authenticated request to a non-`/api/auth/` path raises 403 with guidance ("This password was set by the farm owner — change it before using the farm (Account → Change password).").
- `app/api/auth.py:1239-1241` — the flag is cleared **only** by a completed self-service `change-password` (current-password verified, token_version bumped, sessions revoked).
- `app/models/core.py:55` — `users.must_change_password` column; migration `e3a5b7c9d1f2` (see item 6).

**Live probe** (in-process): owner created two workers (custom roles `dashboard.view`-only and `simulation.view`-only).

| Step | Result |
|---|---|
| Login response `user.must_change_password` | `true` |
| `GET /api/auth/me` while flagged | 200, `must_change_password: true` (auth routes exempt, as claimed) |
| `GET /api/dashboard` while flagged | **403** with the rotation-guidance detail |
| `POST /api/auth/change-password` | 200 (new token pair) |
| `GET /api/dashboard` after rotation | **200** |

Blocking is applied at the `current_user` dependency, so *every* domain route (reads included) is fenced, not just mutations — stronger than the audit's minimum ask.

## 3. C-1 — declared error responses on the API contract — VERIFIED

Programmatic inspection of `shared/openapi.json` (77 paths / 87 operations; 85 operations on `/api/*` routers):

- **Routes missing any of 400/401/403/404/409/429: 0 of 85.** No route is missing even a subset of the six codes.
- `GET /api/simulation/calibration` declares **`500`** ("Calibration data is internally inconsistent") in addition to the six common codes — the audit's invisible-500 path is now in the contract.
- Mechanism: `COMMON_ERROR_RESPONSES` (`app/schemas/common.py:167-174`, each `{"model": ErrorOut, ...}`) is passed as `APIRouter(responses=...)` on all 15 API routers (verified in every `app/api/*.py`).
- **Live vs snapshot:** OpenAPI exported from `create_app()` (in-process) is byte-equivalent on all 77 paths against `shared/openapi.json` — zero drift.

## 4. C-2 — enum-constrained Out schemas and TS unions — VERIFIED

**Spec (`shared/openapi.json` components):** all six audited Out schemas carry real `enum` arrays on the fields the audit flagged as plain strings:

| Schema | Enum-constrained fields (values verified non-empty) |
|---|---|
| `AnimalOut` | `status` (ACTIVE/SOLD/DEAD/CULLED), `current_bucket` (10 buckets), `sex`, `source`, `birth_type` |
| `TaskOut` | `status` (PENDING/DONE/SKIPPED/VERIFIED), `category` (10) |
| `BreedingRecordOut` | `outcome`, `method`, `loss_cause` (incl. server-owned `ANIMAL_STATUS_CHANGE`) |
| `TransactionOut` | `type`, `category` (11 incl. ANIMAL_SALE/ANIMAL_PURCHASE) |
| `MilkRecordOut` | `shift` |
| `FarmOut` | `farm_type` (GOAT/BUFFALO_DAIRY) |

Remaining plain-string fields are free text or datetimes (`breed`, `tag_number`, `title`, `created_at`, …) — correctly not enums.

**TS spot-checks (5 checked, 3 required):** `frontend/src/api/generated/models/animalOut.ts` imports `AnimalOutStatus`/`AnimalOutSex`/`AnimalOutCurrentBucket`/…; `taskOut.ts` uses `TaskOutStatus`/`TaskOutCategory`; `breedingRecordOut.ts` uses `BreedingRecordOutMethod/Outcome/LossCause`; `transactionOut.ts` and `milkRecordOut.ts` likewise. Each companion file (e.g. `animalOutStatus.ts`) declares `export type X = typeof X[keyof typeof X]` over an `as const` object — compile-time unions, orval 8.23.0.

## 5. C-3/C-4 — tag-length single source of truth and parity test coverage — VERIFIED

- `app/schemas/animals.py:40`: `tag_number: ... Field(default=None, min_length=1, max_length=MAX_ANIMAL_TAG_LENGTH)` — imported from `app.models.constants` (no literal).
- `app/models/constants.py:60`: `MAX_ANIMAL_TAG_LENGTH = 50`; `shared/openapi.json` `AnimalCreateIn.tag_number` carries `maxLength: 50, minLength: 1`. Symbolic and numeric identity both hold.
- `tests/test_schema_parity.py`: **14 test functions, 14 passed** (8.6 s) — meets the ≥14 bar. `test_wire_caps_are_shared_from_models` asserts `animals_schemas.MAX_ANIMAL_TAG_LENGTH is models.MAX_ANIMAL_TAG_LENGTH` (object identity — re-definition impossible) plus `MAX_WITHDRAWAL_DAYS == 730`, `MAX_TASK_TITLE_LENGTH == 200`, `MAX_FREE_TEXT_LENGTH == 4000`; vocabulary tests cover the previously-uncovered enums (farm type, animal vocabularies, breeding, milk/feeding, kid status/ease, goat-profile aliases, species policy knobs).

## 6. Migrations on a fresh throwaway DB — VERIFIED

Fresh `v2_verify_mig_test` (created, migrated, inspected, dropped):

- `alembic upgrade head` applies **cleanly** through all 48 revisions; final log lines: `f8a2c4e6b1d9 → d1e2f3a4b5c6 → e3a5b7c9d1f2`.
- `alembic heads`: **`e3a5b7c9d1f2 (head)`** — single-headed.
- `users.must_change_password`: `boolean NOT NULL DEFAULT false` — present.
- Breeding gestation CHECKs both widened to **+350**:
  - `ck_breeding_loss_within_max_gestation`: `loss_date IS NULL OR loss_cause = 'ANIMAL_STATUS_CHANGE' OR loss_date <= breeding_date + 350`
  - `ck_breeding_records_result_within_max_gestation`: `ultrasound_result_date IS NULL OR ultrasound_result_date <= breeding_date + 350`
- `alembic check`: "No new upgrade operations detected" (models ↔ DB consistent at head).

## 7. L-1 — register enumeration: per-email budget, isolated bookkeeping — VERIFIED

**Code.** `app/api/auth.py:89` instantiates `register_email_limiter = SlidingWindowRateLimiter()` — a **separate object** from `auth_limiter` (`app/ratelimit.py:238`), each with its own `_hits`/LRU maps. `register` (auth.py:696-713) charges `("register-email", "register-email:<email>")` with the standard `max_attempts`/window **before** the existence check, so duplicate probes of one address exhaust that email's budget even across rotating source IPs.

**Live probe (rate limiting enabled in-process):**
- Duplicate-email register → 400 `That email is already registered.` (the explicit oracle remains, as documented — the accepted trade-off without email infra).
- After the charge, the key `("register-email", "register-email:<victim>")` exists **only** in `register_email_limiter._hits`; the shared `auth_limiter._hits` contains **zero** `register-email` keys — register probes cannot evict or consume the login/invalid-token bookkeeping that the shared limiter's bounded cardinality protects.
- Simulated IP rotation (9 additional direct charges to the email bucket, per-IP `register` bucket at only 4/10): next register for that email → **429** while the per-IP budget was still open — the block is attributable to the per-email budget.
- A normal owner login immediately after → 200 (auth path unharmed).

## 8. Compose — VERIFIED

`docker-compose.yml:20`: `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set a real POSTGRES_PASSWORD}` — the `:-goatfarm` fallback is gone; Compose refuses to start without a real secret (audit L-2 fix).

## 9. Test suites — VERIFIED

```
GOATFARM_TEST_DB=v2_verify_test ./.venv/bin/python -m pytest \
  tests/test_security_hardening.py tests/test_team_extended.py \
  tests/test_auth_extended.py tests/test_schema_parity.py tests/test_rbac.py -q
→ 678 passed in 526.95s (0 failed, 0 errors)
```
(`test_schema_parity.py` alone: 14/14.)

## 10. Regression hunt — re-exploitation attempts — VERIFIED (closed)

Attempted against the live in-process app on a fresh throwaway DB:

| Original finding | Re-exploit attempt | Result |
|---|---|---|
| Manual ledger rows with system categories | `POST /api/finance/new` `{type: INCOME, category: ANIMAL_SALE}` | **422** — "ANIMAL_SALE rows are generated by the animal purchase/sale workflow and cannot be created manually" (same for EXPENSE/ANIMAL_PURCHASE) |
| (control for the above) | legal manual `{EXPENSE, FEED}` row | 201 — the fence is category-scoped, not a blanket lock |
| Quarantine sale fence | `POST /api/animals/{id}/status` `{new_status: SOLD}` on a `PURCHASED` animal (server-forced into `QUARANTINE`) | **409** — "… is still in the 45-day quarantine protocol — complete or skip the protocol before selling" |
| X-1 herd-composition disclosure | sim-only role polls herd-snapshot | **403** (item 1) |

## Residual notes (non-blocking, consistent with the remediation log's own scoping)

1. **L-1 oracle remains by design:** duplicate-email register still answers a distinguishable 400 — the remediation claims only the per-email budget, which is exactly what the audit's near-term fix suggested. No overclaim found.
2. The per-email register budget is charged on *every* register attempt for that address, so a fresh email's own successful registration consumes one of its ten attempts — harmless (success on attempt one) and it is what makes duplicate probing chargeable.
3. `GET /api/auth/me` staying reachable while flagged is deliberate (it is how the UI learns to show the banner) and matches the documented "auth routes exempt" behavior.

## Artifacts

- Probe scripts (scratch): `/tmp/v2_verify/probe.py` (17 live checks, all PASS), `/tmp/v2_verify/inspect_openapi.py`, `/tmp/v2_verify/enums_detail.py`, `/tmp/v2_verify/live_vs_snapshot.py`, `/tmp/v2_verify/mig_check.sh`
- All `v2_verify_*` databases dropped after use; no source files modified.
