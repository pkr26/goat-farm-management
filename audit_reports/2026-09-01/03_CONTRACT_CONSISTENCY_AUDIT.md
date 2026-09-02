# Contract Consistency Audit (AUDITOR A3) — 2026-09-01

Repo: `/Users/pavankumarreddyreddem/Desktop/goat_saas`
Scope: full API surface three-way consistency (FastAPI routes ↔ `shared/openapi.json` ↔ Orval client), Pydantic schemas ↔ TS models, enum drift, duplicated constants, DB models ↔ Alembic migrations, relationships/cascades/Decimal precision, contract tooling claims.

Method: read-only source analysis plus programmatic cross-checks from `/tmp/audit_a3/` — live OpenAPI exported from `create_app()` (no source writes), route/param/response deep-diff vs `shared/openapi.json`, URL-builder extraction from `endpoints.ts`, per-class Pydantic JSON-schema dump vs components, AST reconstruction of the 45-migration Alembic chain vs SQLAlchemy metadata, and `pytest tests/test_schema_parity.py` (7/7 passed, 2.57s).

---

## VERIFIED-CORRECT inventory

| Check | Result |
|---|---|
| Routes: backend app vs `shared/openapi.json` | **87/87 identical** — zero diff on paths, methods, operationIds, parameters, request bodies, response schemas, declared status codes (77 paths → 87 operations; 85 in `app/api/*.py` + `/healthz` `/readyz` in `main.py:593-607`; 1 intentional hidden route) |
| Routes: openapi.json vs generated `endpoints.ts` | **87/87 matched** URL builders + HTTP methods; no client-only calls, no spec-only routes |
| Component schemas: Pydantic vs openapi.json | 168 common components compared property-level (names, required/optional, types, bounds, enums): **no drift** (only FastAPI's expected omission of `default: null`) |
| Bounds | 455 numeric/length constraints present in spec; every input cap traced to `models/constants.py` via imports in schemas |
| Frontend enforcement of major caps | Verified matching: tag 50, task title 200, purchase count 1–1000, age ≤240, recur 1–3650, withdrawal ≤730, notes 4000, money ≥ ₹0.005, kg ≥ 0.0005 |
| Enums | All 17 backend enums surfaced as inputs match `models/enums.py`; frontend labels complete for every value the backend can return (no runtime `UNKNOWN` fallback anywhere); frontend derives bucket lists from generated enums (`Object.values(AnimalCreateInCurrentBucket)` etc.) |
| Species constants | `frontend/src/lib/farm-vocabulary.ts` mirrors `backend/app/models/species.py` exactly (goat 10mo/22kg, buck 12mo/25kg, gestation 100–200, PD 32d, wean 60d; buffalo 22mo/340kg, 24mo/350kg, 270–350, 60d, 90d) |
| DB vs migrations | **27/27 tables, 45 migrations, single head `f8a2c4e6b1d9`**; every model column/index/unique found in migrations (partial-index and CONCURRENTLY variants included); feed quantities migrated to `NUMERIC(15,3)` (`f2c3d4e5f6a7`); money → `NUMERIC(14,2)` (`c8f1d3a5e709`); every farm-scoped table has a farm_id-leading index; `unique(farm_id, tag_number)` on animals and kid tags, idempotency `uq_idempotency_actor_scope_key(actor_id, operation, key_digest)` all present |
| Money precision | All money columns `Numeric(14,2)`; no float money. `asdecimal=False` (Python float) only on kg/litre measures stored as SQL NUMERIC — storage exact |
| Cascades | Coherent: animals never hard-deleted (SOLD/DEAD/CULLED lifecycle; no `delete(Animal)` call sites), history FKs NO ACTION preserve audit, `weights`/`bucket_moves` ORM `delete-orphan`, `users` RESTRICT where audit attribution matters, memberships/idempotency CASCADE |
| Tooling | `orval` v8.23.0 header == `package.json` `^8.23.0`; CI enforces openapi snapshot (`ci.yml:77-81`), `alembic upgrade head` + `alembic check` (`ci.yml:84-90`), and orval regen diff (`ci.yml:143-146`) |
| Pagination | Uniform `limit/offset` (offset `0..1_000_000` everywhere; per-endpoint limit caps 200/100/50); parameters byte-identical across all three layers |

**Findings: 0 BLOCKER, 0 HIGH, 4 MEDIUM, 5 LOW, 5 INFO.**

---

## MEDIUM

### M-1. 53 of 86 audited routes raise HTTP status codes that are absent from the OpenAPI responses
- **File:line (representative):** `backend/app/api/simulation.py:710` (500), `backend/app/api/auth.py:832` (401 on /refresh), `backend/app/api/auth.py:1230` (409 on export), `backend/app/api/animals.py:753` (403/409 on move), `backend/app/api/breeding.py:213` (400/404/409), `backend/app/api/health.py:515` (400/403/409), `backend/app/services/idempotency.py:228,237,350-366` (409 on Idempotency-Key replay)
- **Issue:** Handlers systematically `raise HTTPException(400|401|403|404|409|429|500)`, but declared responses are only the success code + FastAPI's auto-422.
- **Evidence:** Static scan of every route body vs declared responses (`/tmp/audit_a3/error_codes2_out.txt`): 53/86 routes raise ≥1 undeclared code. The one literal 500 (`simulation.py:710`, calibration data corruption path) is entirely invisible in the contract. The generated client types every mutation error as `HTTPValidationError` by default, so consumers cannot know a route can 409/404.
- **Fix:** Annotate routes with `responses={400: {"model": ErrorOut}, 404: ..., 409: ...}` (or a router-level default), and regenerate the client. At minimum declare the 409 (idempotency/conflict) and 401/403 (auth) paths that the frontend must already handle.

### M-2. ~50 response fields that are semantically enums are typed as plain `string` end-to-end
- **File:line:** `backend/app/schemas/tasks.py:55-56` (`status: str  # PENDING | DONE | SKIPPED | VERIFIED`), `backend/app/schemas/animals.py` (AnimalOut.status/current_bucket/sex/source/birth_type), `backend/app/schemas/breeding.py` (BreedingRecordOut.outcome/method/loss_cause), `backend/app/schemas/finance.py` (TransactionOut.type/category), `backend/app/schemas/milk.py` (MilkRecordOut.shift), `backend/app/schemas/health.py` (HealthEventOut.type), `backend/app/schemas/auth.py` (FarmOut.farm_type)
- **Issue:** Input schemas use `Literal[...]` (so request enums are constrained in spec and TS), but the corresponding Out fields are bare `str`.
- **Evidence:** Component scan: `AnimalOut.status`, `TaskOut.status`, `TaskOut.category`, `BreedingRecordOut.outcome`, `TransactionOut.type`, `MilkRecordOut.shift`, `FarmOut.farm_type` (+44 more) have `{"type": "string"}` with no `enum`. Orval therefore emits `status: string` in TS — the frontend compares against literals (`tasks/page.tsx:549,600,607`) with no compile-time protection, relying on `enum-labels.ts` runtime mapping (titleCase fallback, no UNKNOWN).
- **Fix:** Reuse the existing `*Str` Literal aliases on Out fields (the parity-test docstring already explains the wire-name constraint). Zero backend logic change; spec, TS unions, and parity test all tighten at once.

### M-3. `AnimalCreateIn.tag_number` hardcodes `max_length=50` instead of `MAX_ANIMAL_TAG_LENGTH`
- **File:line:** `backend/app/schemas/animals.py:40` (`tag_number: PostgresText | None = Field(default=None, min_length=1, max_length=50)`) vs `backend/app/models/constants.py:51` (`MAX_ANIMAL_TAG_LENGTH = 50`)
- **Issue:** The documented "single source of truth" chain (constants → schemas → services) is broken for this field; services (`services/kidding.py:183`, `services/purchases.py:36`) enforce the constant while the API bound is a literal.
- **Evidence:** Values coincide today (50), but `tests/test_schema_parity.py` does not assert tag-length identity (only MAX_BATCH_COUNT / MAX_AGE_MONTHS / MAX_RECUR_DAYS), so a constant change would silently diverge schema vs services.
- **Fix:** `max_length=MAX_ANIMAL_TAG_LENGTH` (import from `..models`) and add it to the parity test's identity assertions.

### M-4. `tests/test_schema_parity.py` covers only 6 of 17 enums and 3 of 8 shared caps
- **File:line:** `backend/tests/test_schema_parity.py:21-57`
- **Issue:** Uncovered enums: `FarmType`, `AnimalStatus`, `Sex`, `BirthType`, `BreedingMethod`, `BreedingOutcome`, `FeedingShift`, `TransactionType`, `TaskStatus`, `IngredientCategory`, `PREGNANCY_LOSS_CAUSES`. Uncovered caps: `MAX_WITHDRAWAL_DAYS` (730), `MAX_ANIMAL_TAG_LENGTH` (50), `MAX_TASK_TITLE_LENGTH` (200), `MAX_FREE_TEXT_LENGTH` (4000).
- **Evidence:** Test asserts identity for `Bucket`, `TaskCategory`, `HealthEventType`, `TransactionCategory`, `KidStatus`, `KiddingEase` and 3 constants only. Wire-visible input Literals are still guarded indirectly by the CI openapi-snapshot diff, which is why this is MEDIUM not HIGH; `FarmCreateIn.farm_type` (`schemas/auth.py:134`) and OUT-side plain-string enums (M-2) have no guard at all.
- **Fix:** Enumerate `app.models.enums` members and assert each has a matching Literal set in schemas; extend the identity assertions to all caps consumed by `schemas/` (withdrawal/tag/task-title/free-text).

---

## LOW

### L-1. Orval drops 104/455 schema bounds (those inside `anyOf` optional variants) from generated TS
- **File:line:** `frontend/src/api/generated/models/animalCreateIn.ts:13` (`tag_number?: string | null` — no annotation, while spec has `maxLength: 50, minLength: 1` inside anyOf); required-field bounds survive only as JSDoc (`taskCreateIn.ts:10-14`, `purchaseBatchIn.ts:13-17`)
- **Issue:** The generated client carries no runtime validation and, for optional fields, not even documentary bounds.
- **Evidence:** Automated spec→TS walk: 349 bounds mirrored as comments, 104 absent. Frontend compensates in forms for the user-facing majors (verified: animals tag zod `.max(50)`, purchases count/age/weight, tasks recur 1–3650, health notes 4000, withdrawal 730) — the remainder rely on server 422.
- **Fix:** Acceptable as-is (server 422 is the enforcement point); optionally enable a zod/valibot generator in Orval for input models.

### L-2. Frontend mirrors backend caps as scattered literals with no cross-layer parity test
- **File:line:** `frontend/src/app/(app)/purchases/page.tsx:91-105` (1000/240/1000 + comment "Bounds mirror backend/app/schemas/purchases.py"), `frontend/src/app/(app)/animals/page.tsx:168` (`.max(50)`), `frontend/src/app/(app)/tasks/page.tsx:642-646` (1–3650), `frontend/src/app/(app)/health/page.tsx:106,216,1910` (730/4000), `frontend/src/lib/persisted-numbers.ts:2-3` (0.0005/0.005 ↔ `Numeric(15,3)`/`Numeric(14,2)` + `_money_precision` `schemas/common.py:83-88`)
- **Issue:** Duplicated constants, all currently matching; each is a silent-drift hazard because no test ties the pair together (backend parity test covers backend-internal identity only).
- **Fix:** Single frontend constants module referencing the spec values (or generate from OpenAPI JSDoc), plus one vitest asserting against the generated models' annotations where present.

### L-3. `loss_cause` rendered raw; server-owned `ANIMAL_STATUS_CHANGE` has no label mapping
- **File:line:** `frontend/src/app/(app)/breeding/page.tsx:1050` (`(r.loss_cause ?? "UNKNOWN").replace(/_/g, " ")`) and `:110` (option labels via same replace)
- **Issue:** Backend `PREGNANCY_LOSS_CAUSES` (`models/constants.py:56-64`) includes 7 values; `BreedingRecordOut.loss_cause` (plain string, see M-2) can return `ANIMAL_STATUS_CHANGE` (written by `api/animals.py:1074`), which renders as the all-caps "ANIMAL STATUS CHANGE" instead of a human label, bypassing `enum-labels.ts`.
- **Fix:** Add a `lossCause` kind to `enum-labels.ts` (6 user causes + the server-owned one) and route the page through it.

### L-4. `FarmCreateIn.farm_type` Literal is hardcoded, duplicating `FarmType`
- **File:line:** `backend/app/schemas/auth.py:134` (`farm_type: Literal["GOAT", "BUFFALO_DAIRY"] = "GOAT"`) vs `backend/app/models/enums.py:10-12`
- **Issue:** Same class as M-3; not covered by the parity test.
- **Fix:** Define from `FARM_TYPES`/enum values and assert in the parity test.

### L-5. `STATUS_TONES` misses `DEAD` and `SKIPPED`
- **File:line:** `frontend/src/components/status-badge.tsx:22-57`
- **Issue:** `CULLED`/`SOLD`/`STILLBORN` are tinted; `DEAD` and `SKIPPED` fall to the neutral chip — inconsistent emphasis for equally final/dismissive states (behavior is graceful, never breaks).
- **Fix:** Add `DEAD: "destructive"`, `SKIPPED: "info"` (or a deliberate comment why not).

---

## INFO

### I-1. Retired `POST /api/team/workers/{membership_id}/toggle` is hidden from the contract by design
- `backend/app/api/team.py:978` (`include_in_schema=False`) returns 405 with `Allow: PUT`; frontend uses `PUT .../status`. Correct retirement pattern; no client binding expected.

### I-2. Orval serializes `null` query params as literal `"null"`; mitigated in the mutator
- `frontend/src/api/custom-instance.ts:14-27` strips them (except free-text `q`). Documented workaround; no drift.

### I-3. `PregnancyLossIn.cause` intentionally omits `ANIMAL_STATUS_CHANGE`
- `backend/app/schemas/breeding.py:63-80` documents it as server-owned; frontend derives options from generated `PregnancyLossInCause` — correct.

### I-4. Contract sync is CI-enforced
- `.github/workflows/ci.yml:77-81` (openapi snapshot), `:84-90` (alembic upgrade + `alembic check` round-trip), `:143-146` (orval regen diff). This is why zero three-way route drift exists today; M-1/M-2 are spec-fidelity gaps the snapshot check cannot catch (it only compares the app to its own export).

### I-5. Duplicate `GET /api/animals` params note
- All 87 operations' parameters (incl. `x-farm-id`, `Idempotency-Key` headers — documented at openapi paths for the relevant POSTs) are identical across layers; `limit` defaults vary by endpoint (100/50/30/20/25) with per-endpoint caps — intentional, consistently documented.

---

## Cross-check artifacts (scratch, read-only on source)
- `/tmp/audit_a3/openapi_live.json` — live export from `create_app()`
- `/tmp/audit_a3/error_codes2_out.txt` — per-route raised vs declared status codes
- `/tmp/audit_a3/pydantic_json_schemas.json`, `/tmp/audit_a3/prop_compare.py` — schema↔component property diff
- `/tmp/audit_a3/ts_bounds_out.txt` — spec bounds vs TS JSDoc mirror (349 present / 104 dropped)
- `/tmp/audit_a3/mig_ast2.py`, `/tmp/audit_a3/migration_state.json`, `/tmp/audit_a3/db_diff_out.txt` — migration replay + models diff (all flagged deltas manually resolved to migrations: `c8f1d3a5e709`, `f2c3d4e5f6a7`, `e5f6a7b8c9d0`, `c2a4e6b8d013`, `b3d7f1a5c9e2`, `d5e7f9a1b3c4`, `f7d8c9b0a1e2`)

**Bottom line:** the three-way route/schema/client contract is exactly in sync and machine-enforced (87/87/87 routes, 168 components, 455 bounds, 0 drift). The real gaps are contract *richness*, not consistency: undocumented error codes (53 routes, incl. one 500), enum-less response fields (~50), and an under-powered parity test that would not catch a constants change in tag length, farm type, withdrawal days, or free-text caps.
