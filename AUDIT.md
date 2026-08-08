# Consolidated Security & Quality Audit — Goat Farm Management

**Date:** 2026-08-07
**Remediation status (2026-08-07, same day):** all findings in this report have been **fixed and verified** — 2300+ backend tests, 682 frontend tests, strict typecheck/lint all green. A handful of items were deliberately resolved by documentation or judgment calls instead of code (each marked in the remediation notes): 3-8/3-11 (product decisions), 0-10 (no global-disable admin surface exists to toggle), 7-10 (needs a new API field), 10-L7 (accepted residual flake window), 11-M8 (documented rotation procedure instead of key-set code). Intentional behavior changes: replayed pre-rotation refresh tokens now 401 and revoke the family; missing `X-Farm-Id` is a 422 (required contract header); a forbidden farm id is a 404 like an unknown one (0-7); malformed worker emails and future kidding dates are 422 (schema-level validation); `max_doe_age_months` floor is now 36; conception requires at least one buck in simulation; selling a confirmed-pregnant doe auto-resolves the pregnancy as ABORTED. `shared/openapi.json` and the Orval client were regenerated, and a contract-drift guard test (`backend/tests/test_contract_drift.py`) now fails the build if the export goes stale.

**Scope:** 12 parallel audit lenses over the full monorepo (`backend/`, `frontend/`, `shared/`, tests, config/ops). Every finding below was verified by the auditing lens against the code; this document is an editorial consolidation only — no findings were invented, dropped, or re-severitized. Where two lenses report the same issue, the entries are cross-referenced and the severity disagreement (if any) is preserved.

## Executive Summary

The codebase is in unusually good shape for its stage: authentication primitives are strong (Argon2id above OWASP minimums, RS256 immune to algorithm confusion, disciplined key handling, timing-equalized login), multi-tenancy is watertight (no cross-farm read/write path exists; every PK fetch is farm-verified), core reproductive arithmetic and the auto-task engine are exact, the simulation engine is deterministic and well-isolated, and the backend test suite is genuinely strong. The systemic weaknesses cluster in four areas: (1) **session lifecycle** — refresh tokens are stateless JWTs with no server-side revocation, so logout, password reset, and rotation terminate nothing (reported by 4 lenses); (2) **the pregnancy-lifecycle state machine** — the ultrasound and abort flows were never given the row-locking their sibling flows got, leaving three HIGH-severity races; (3) **operational readiness** — no health endpoint, no logging/observability, no Docker/CI, no production-config validation; and (4) **read-side amplification** — mapper-level `selectin` loading plus unbounded aggregate endpoints will degrade linearly with farm age. Two schema-valid simulation input combinations crash the engine with raw 500s. There are **no CRITICAL findings** and no exploitable cross-tenant or injection vulnerabilities anywhere.

## Findings Count by Severity and Lens

Counts are as reported per lens (cross-lens duplicates counted in each lens that reported them). **127 findings total; ~20 are cross-lens duplicates → ~107 unique issues** (15 HIGH, 32 MEDIUM, 60 LOW, 0 CRITICAL).

| # | Lens | HIGH | MEDIUM | LOW | Total |
|---|------|------|--------|-----|-------|
| 0 | Backend auth & session security | 1 | 2 | 7 | 10 |
| 1 | Multi-tenancy & RBAC/IDOR | 0 | 1 | 4 | 5 |
| 2 | Transactions & concurrency | 3 | 2 | 7 | 12 |
| 3 | Domain logic correctness | 1 | 3 | 9 | 13 |
| 4 | Backend architecture & code quality | 2 | 6 | 9 | 17 |
| 5 | DB schema, migrations & query performance | 2 | 5 | 6 | 13 |
| 6 | Frontend security & API client | 1 | 1 | 2 | 4 |
| 7 | Frontend code quality & UX | 0 | 4 | 7 | 11 |
| 8 | API contract drift | 0 | 0 | 4 | 4 |
| 9 | Simulation module | 2 | 3 | 4 | 9 |
| 10 | Testing adequacy | 2 | 4 | 5 | 11 |
| 11 | Config, ops & deployment readiness | 3 | 9 | 6 | 18 |
| | **Total (raw)** | **17** | **40** | **70** | **127** |

---

## Top Findings

The ~10 most important issues across all lenses. Duplicate lens reports are noted.

**T1. HIGH — No refresh-token revocation: logout, password reset, and rotation terminate nothing** (lenses 0, 1, 6, 11)
`backend/app/api/auth.py:157-171`, `backend/app/api/team.py:319`, `backend/app/security.py:164-182`. Refresh tokens are stateless JWTs; the `jti` is issued but never tracked. A stolen refresh token mints access tokens for its full 14-day TTL even after logout, owner-initiated password reset (the *only* credential control over workers), or rotation. A double-click-tested design decision (`tests/test_auth_extended.py:616-626`) enshrines that pre-rotation tokens stay valid. **Fix:** server-side refresh-session table (user_id, jti, expiry, revoked, family id); consume the presented jti on refresh, revoke the family on reuse detection, revoke all rows on password reset/logout; optionally a per-user `token_version` so access tokens die on password change.

**T2. HIGH — Concurrent ultrasound submissions duplicate all follow-up tasks; mixed submissions leave divergent state** (lenses 2, 4)
`backend/app/api/breeding.py:185-203`, `backend/app/services.py:288-357`. No row lock on the breeding record; two in-flight posts both pass the PENDING check and both commit → duplicated ET+TT/DELIVERY/KIDDING_DUE tasks and BucketMove rows; a pregnant + not-pregnant race yields `outcome=FAILED` with three live pregnancy tasks. Every sibling flow was hardened except this one. **Fix:** `.with_for_update()` in `_get_breeding_record` with outcome re-check under the lock.

**T3. HIGH — Schema-valid simulation inputs crash the engine with raw 500s (two cross-field holes)** (lens 9)
`backend/app/simulation/engine.py:245-247` — `max_doe_age_months` in [24, 35] (schema floor `ge=24`, `assumptions.py:130`) makes the foundation-doe age `slots` list empty → ZeroDivisionError. `engine.py:292,315,406` — `age_at_first_breeding_months` (≤30) > `max_doe_age_months` (≥24) → IndexError. Both reproduced; both bypass the `_finite_payload` 422 defense. **Fix:** raise the floor to `ge=36` (or guard `slots`), and add a `model_validator` requiring `afb <= max_doe_age_months`.

**T4. HIGH — Deworming records never match the "Deworming" template: schedule shows false OVERDUE forever** (lens 3)
`backend/app/services.py:916-927` matches templates by substring over `product_name + disease_target`, ignoring `HealthEvent.type`. The natural entry (type=DEWORMING, product "Albendazole", target blank — the form leaves both blank and doesn't prefill from the linked task, `frontend/src/app/(app)/health/page.tsx:602-609`) never matches; even the quarantine task's "deworm" text fails ("deworm" ≠ "deworming"). Every animal's June/January deworming reads OVERDUE despite compliant recording; owners lose trust or double-dose. Same fragility for vaccines recorded by trade name. **Fix:** match by `event.type == DEWORMING` (accept "deworm" key); alias/drug-name lists for vaccines.

**T5. HIGH — Not deployment-ready: no health endpoint, no Docker/CI, no production-config validation** (lens 11)
`backend/app/main.py:86-98` — no `/healthz`/`/readyz` (Playwright polls `/openapi.json` as a makeshift probe). No Dockerfile, compose, or CI anywhere (`git ls-files`-verified); the 2315 backend + 649 frontend tests run only when someone remembers. `backend/app/core/config.py:47` — `cookie_secure` defaults `False` with no `environment` setting or startup validator; forgetting `GOATFARM_COOKIE_SECURE=true` ships the refresh JWT over plain HTTP with no warning, and `/docs` + `/openapi.json` are served unconditionally. **Fix:** unauthenticated `/healthz` + `/readyz` (`SELECT 1`); minimal Dockerfile + CI (pytest/ruff/mypy/vitest + audits); an `environment` field with a startup validator that refuses prod boot with `cookie_secure=False`/localhost CORS, and docs gated off in prod.

**T6. HIGH — `mark_aborted` skips pregnancy tasks without row locks — can overwrite a committed completion** (lens 2)
`backend/app/services.py:393`. The abort path uses the unlocked `_pending_tasks_for` while the two sibling paths were deliberately fixed (`services.py:108-112, 543-545`). Interleave: a worker completes the pre-kidding vaccine task (committed DONE with attribution); a concurrent abort's stale UPDATE flips DONE → SKIPPED, erasing the attributed work. Double-abort also writes duplicate BucketMove rows (unlocked CONFIRMED check at `breeding.py:214`). **Fix:** `for_update=True` + status re-check in the loop; lock the breeding row per T2.

**T7. HIGH — Kidding vs abort TOCTOU: a pregnancy can end ABORTED with live born kids** (lens 2)
`backend/app/api/kidding.py:107-120`, `backend/app/api/breeding.py:213-221`, `backend/app/services.py:436-442`. Both endpoints gate on an unlocked `outcome == CONFIRMED_PREGNANT` read; a concurrent abort + kidding both pass → outcome=ABORTED with live stock on the farm and last-writer-wins bucket. **Fix:** `SELECT … FOR UPDATE` the breeding record in both flows; loser re-reads and fails its state guard.

**T8. HIGH — Zero logging/observability anywhere in the backend** (lenses 4, 11 — severity disagreement: HIGH vs MEDIUM)
No `import logging` in `backend/app/` (grep-verified); no request logging, no health endpoint, no request IDs, no error tracker. Unhandled exceptions become bare 500s with no record; `seed_startup` failure kills startup with only a uvicorn traceback; `Role.permission_set` silently swallows corrupt JSON (empty permissions = silent lockout, `models.py:239-245`); rate-limit blocks, legacy-hash upgrades, and RBAC denials leave no audit trail. **Fix:** configure structured logging in `create_app()`, a generic exception handler, request-ID middleware, log security events, `/healthz`.

**T9. HIGH — Read-side amplification: every `Animal` query eagerly loads full weight/move/breeding history; reports endpoint is an unbounded full-history scan** (lens 5)
`backend/app/models.py:310-324` — mapper-level `lazy="selectin"` on `weight_records`, `bucket_moves`, `breedings_as_doe` (and `BreedingRecord.kidding_record`, `models.py:487-489`) fires 4 extra `WHERE IN (...)` selects pulling entire histories on every Animal load. `backend/app/api/dashboard.py:194,221-244` — `GET /api/dashboard/reports` loads ALL animals (incl. sold/dead), all breedings, all kiddings + kids ever, then aggregates in Python. A 3-year, 500-head farm ships tens of thousands of rows per dashboard load. **Fix:** drop mapper-level eager loading, add explicit `selectinload` only where computed fields serialize; push aggregates into SQL (`GROUP BY`, `DISTINCT ON`/lateral for latest weight).

**T10. HIGH — E2E suite is not reproducible and is parallel-unsafe against shared state** (lens 10)
`frontend/e2e/helpers.ts:3-5` — every spec signs in as a pre-existing dev account (`demo@goatfarm.in`) owning a pre-existing farm; no `globalSetup` creates them, so the suite can't run on a fresh clone or CI. `frontend/playwright.config.ts` sets no `workers` → parallel spec files mutate the same farm with count/delta assertions (`e2e/feeding-finance.spec.ts:43,74-76`) while `e2e/purchases.spec.ts:23` concurrently books expenses into the same totals; `retries: 0`. Flaky, non-portable, gates nothing. **Fix:** global setup registering a fresh user+farm per run against a throwaway DB, or `workers: 1` + per-spec isolation; row-identity assertions instead of deltas.

---

## Lens 0 — Backend Auth & Session Security

### Findings

**HIGH 0-1. Password resets and logout do not invalidate sessions — no token revocation exists anywhere.** `backend/app/api/team.py:319` (reset rewrites the hash only), `backend/app/api/auth.py:167-171` (logout only deletes the cookie), `auth.py:157-164` (refresh re-issues purely from JWT validity); repo-wide grep for `revok|blacklist|invalidat|token_version|password_changed` finds nothing. Impact: if an owner resets a worker's password *because the account is suspected compromised*, every outstanding refresh token (14-day TTL, `config.py:34`) and access token (30-min) keeps working; "logout" on a shared device does nothing if the cookie was exfiltrated; no way to forcibly end a session short of deleting the user row. Fix: server-side refresh-session table (user_id, jti, expiry, revoked, family); consume jti on refresh; revoke on reset/logout; optional per-user `token_version` checked in `current_user` (`deps.py:25-36`). *(Same root issue as 0-2, 1-1, 6-1, 11-L5 — see T1.)*

**MEDIUM 0-2. Refresh-token rotation has no reuse detection — pre-rotation tokens remain fully valid.** `backend/app/api/auth.py:164` rotates the cookie but the old token still verifies; an explicit tested decision (`tests/test_auth_extended.py:616-626` asserts replay returns 200). Impact: a stolen refresh token works in parallel with the legitimate client for up to 14 days with no signal — rotation gives the appearance of theft protection without the substance (RFC 6819 §5.2.2.3). Fix: the session table from 0-1 enables "old jti presented after rotation ⇒ revoke the whole family."

**MEDIUM 0-3. Login throttling keying permits distributed brute force and password spraying.** `backend/app/api/auth.py:63-69` keys the limiter per `(client IP, email)`; 10 failures / 300 s (`config.py:55-56`); only failures count, success resets (`auth.py:146-150`). Impact: (a) rotating source IPs gets a fresh 10-attempt budget per IP against a single account — no per-account ceiling; (b) from one IP, 10 tries per account per 5 min across unlimited distinct accounts (spraying) — no per-IP global cap. Fix: add per-email (IP-agnostic) and per-IP (email-agnostic) counters alongside the composite key.

**LOW 0-4. User enumeration via `/register`.** `auth.py:115-117` returns the explicit `"That email is already registered."`, checked before the expensive Argon2 hash (`auth.py:112-121`), so timing also distinguishes existing emails. Rate-limited to 10/5 min per IP (`auth.py:108-111`) but trivially rotated. Fix: accept-and-notify pattern, or hash before the existence check to remove the timing half. *(Same as 1-5.)*

**LOW 0-5. Timing oracle for legacy-migrated accounts.** Unknown emails verify against a dummy Argon2id hash (`auth.py:140-143`), but a known legacy pbkdf2 account with a wrong password returns after a fast `hashlib.pbkdf2_hmac` (`security.py:66-75`) — distinguishes "email unknown" from "email exists, pre-migration." Fix: run a dummy Argon2 verify after a failed legacy verify, or batch-migrate remaining hashes.

**LOW 0-6. No self-service password change; weak password policy.** No change-password endpoint anywhere; workers' only credential is the owner-set password (acknowledged at `team.py:274-279`). Policy is min-length 8 + non-whitespace only (`security.py:52-59`) — no breach-list/dictionary check. Fix: add `POST /api/auth/change-password` (requires current password, revokes sessions per 0-1); consider k-anonymity HIBP check at register/reset.

**LOW 0-7. Farm-ID existence oracle.** `deps.py:86-89`: nonexistent farm → 404, existing-but-forbidden farm → 403; any authenticated user can enumerate sequential farm IDs. Fix: return 404 for both.

**LOW 0-8. Per-process in-memory rate limiter.** `ratelimit.py:1-7` (documented): limits reset on restart and multiply per worker under `--workers N`/horizontal scaling. Fix: Redis-backed limiter beyond single-process, or document the constraint. *(Same as 11-M6, which rates it MEDIUM.)*

**LOW 0-9. `cookie_secure` defaults to `False`.** `config.py:47` — correctly documented (`README.md:196-197`), but a production deploy that forgets `GOATFARM_COOKIE_SECURE=true` sends the refresh cookie over plaintext HTTP. Fix: default `True` with explicit dev opt-out, or fail startup when `cookie_secure=False` with a non-localhost CORS origin. *(Subsumed by 11-H3, which rates the broader config-validation gap HIGH.)*

**LOW 0-10. No account-level disable flag.** `User` has no `is_active` (`models.py:184-194`); deactivating a membership (`team.py:254-263`) blocks farm data but the account can still log in, refresh, and create farms (`auth.py:192-220` requires only `CurrentUser`). Combined with 0-1, no way to suspend a person globally. Fix: `users.is_active`/`disabled_at` checked in `current_user` and `refresh`.

### Checked and clean

Argon2id params (t=3, m=64 MiB, p=4, len=32) above OWASP minimums with rehash-on-upgrade (`config.py:38-41`, `security.py:91`); legacy pbkdf2 path constant-time and error-safe (`security.py:85-90`); JWT algorithm pinned to RS256 from settings, never the token header — no alg-confusion path; `kind` claim separates access/refresh (`security.py:189`); RSA-2048 keygen with 0600 perms before rename, atomic `os.replace`, cross-process `flock` (`security.py:102-148`); `backend/keys/` gitignored and `git grep` across all revisions confirms no secret was ever committed; generic login error + dummy-hash equalization (`auth.py:139-145`); cookies HttpOnly + SameSite=Lax + `path=/api/auth` scoped (`auth.py:86-96`); X-Forwarded-For trust defaults to nothing and uvicorn's ProxyHeadersMiddleware walks the chain right-to-left against trusted CIDRs (`config.py:62`, adversarial test coverage); CORS pinned origins, no wildcard (`main.py:73-79`); rate-limiter emptied deques deleted (no unbounded map growth, `ratelimit.py:44-48`); cross-tenant takeover guards in worker creation/reset (`team.py:146-191, 285-315`); farm-cap race guarded by `SELECT … FOR UPDATE` (`auth.py:203`); no unauthenticated routers; frontend access token in memory only with single-flight refresh dedupe.

### Lens health summary

Authentication *primitives* are in very good shape; the weak layer is **session lifecycle** — stateless refresh JWTs with no server-side record mean rotation has no reuse detection and neither logout, password reset, nor membership deactivation can terminate a session. The rate limiter is sound in construction but its `(IP, email)` keying leaves distributed brute force and spraying open. Fixing the revocation model (0-1) resolves the top three concerns in one architectural change.

---

## Lens 1 — Multi-Tenancy & RBAC/IDOR

### Findings

**MEDIUM 1-1. Refresh tokens are stateless and never revoked — rotation, logout, and worker password resets leave old sessions alive.** `backend/app/security.py:164-182` (jti issued, never tracked), `backend/app/api/auth.py:157-171` (rotation cosmetic; logout deletes cookie only), compounded by `backend/app/api/team.py:266-321`: the owner's reset-password is the *only* credential control over workers, yet it doesn't invalidate the worker's tokens — a fired/malicious worker keeps minting access tokens for up to 14 days after reset. Only `toggle_worker` deactivation cuts access (membership re-checked per request). Fix: persist refresh jtis (DB table or denylist), consume on rotation with family-wide reuse revocation, revoke on reset/logout; also rate-limit `/refresh` (currently unthrottled, `auth.py:157`). *(Same as 0-1/0-2, 6-1, 11-L5 — lenses 0 and 6 rate this HIGH.)*

**LOW 1-2. Task side-effect animal fetches skip the farm check (defense-in-depth).** `backend/app/services.py:704` (BUCKET_MOVE `db.get(Animal, task.animal_id)`) and `services.py:709` (WEANING doe fetch). The Task row is farm-verified in `_get_task` (`api/tasks.py:86-104`) and `animal_id` is only ever server-assigned today, so **not currently exploitable** — but any future client-influenced `task.animal_id` would move a cross-farm animal. Fix: one-line guard `if linked_animal and linked_animal.farm_id == task.farm_id`.

**LOW 1-3. Horizontal control among `team.manage` holders — a manager worker can demote, deactivate, or password-reset a peer manager.** `backend/app/api/team.py:232-263` — `change_role`/`toggle_worker` only block acting on yourself; `:266-321` lets them reset a peer's global password. No privilege *gain* (permissions equal), but enables insider lockout/account-hijack of peers; only the owner can undo. Fix: non-owner actors may not act on memberships whose role grants permissions the actor doesn't hold (or restrict acting on other `team.manage` holders to the owner).

**LOW 1-4. `create_worker` error messages disclose other farms' roster state; enrollment has no consent.** `backend/app/api/team.py:155-159` ("owns a farm"), `:189-191` ("belongs to another farm's team"), `:173` ("already on this farm's team") let any farm owner probe arbitrary emails and learn whether a person owns a farm or works elsewhere; the docstring at `:131-137` acknowledges the missing invitation/consent flow (unaffiliated accounts enrolled without their say-so). Fix: collapse to one generic refusal; move to invite-accept flow when built.

**LOW 1-5. Register endpoint is a user-enumeration oracle.** `backend/app/api/auth.py:115-117` returns "That email is already registered."; login is timing-equalized (`:140-145`) but register is not. Mitigated by per-IP register rate limiting (`:108-111`). Fix (optional): accept registration and require email verification before activation. *(Same as 0-4.)*

### Verified clean

`deps.py` farm resolution (`:70-90`): X-Farm-Id parsed, range-checked, 404 unknown, owner-or-active-membership enforced, int32 overflow guarded; owner implicitly holds all permissions (`perms_for`, `:108-114`). **`require_perm` coverage: every endpoint in all 14 routers** carries a permission dependency (or is intentionally public auth). **IDOR trace — all clean:** every PK fetch re-checks/filters `farm_id` (animals `api/animals.py:45-63`, breeding `api/breeding.py:54-72`, kidding `api/kidding.py:99-106`, tasks `api/tasks.py:86-104`, memberships `api/team.py:61-81`, purchase batches `api/purchases.py:116-118`, feed inventory `api/feeding.py:151-161`, scenarios `api/simulation.py:63-70`, vaccine schedule `api/health.py:144-146`); child-by-parent-FK queries safe because the parent is farm-verified first. **Body-supplied IDs:** no `In` schema accepts `farm_id`/actor `user_id`; `doe_id`/`buck_id`, `breeding_record_id`, health scope IDs (incl. smuggled `task_id` with farm + assignment re-check), `related_animal_id` stripping, and task-assignee validation all verified. Team consent guards block absorbing owners/other-team accounts; password reset refused for owners and cross-affiliated accounts. Ownership cap race-safe via user-row `FOR UPDATE` (`auth.py:203`). Two-person verification rule with correct owner exemption (`tasks.py:270-275`); row locks serialize complete/verify/reject; worker task scoping applied consistently. RS256 kind-confusion prevented; rate limiter honors X-Forwarded-For only from trusted proxies; DB unique constraints backstop per-farm uniqueness; extensive adversarial test suite. Frontend farm id comes only from the server-validated `/api/auth/farms` list; farm switching clears the query cache.

### Lens health summary

Multi-tenancy and RBAC are in strong shape — no CRITICAL or HIGH issues; no cross-farm read or write path exists today. The one substantive gap is session lifecycle (1-1); the rest is defense-in-depth hardening (1-2), insider controls (1-3), and minor information disclosure (1-4, 1-5).

---

## Lens 2 — Transactions & Concurrency

### Findings

**HIGH 2-1. Concurrent ultrasound submissions duplicate all follow-up tasks; mixed submissions leave divergent state.** `backend/app/api/breeding.py:185-203`, `backend/app/services.py:288-357`. Neither router nor service locks the `breeding_records` row; two in-flight posts both pass the idempotency check (`services.py:301`) and both commit: 6 follow-up tasks instead of 3 (no Task dedup constraint), duplicate BucketMove rows; a pregnant + not-pregnant race yields last-writer-wins outcome with the other request's tasks/cull-flag/bucket move already committed (`outcome=FAILED` + doe in PREGNANCY_EARLY + three live pregnancy tasks). Double-clicking the ultrasound form is a realistic trigger. Fix: `.with_for_update()` in `_get_breeding_record` + outcome re-check under lock (same pattern as `_get_task`). *(Same as 4-H1.)*

**HIGH 2-2. `mark_aborted` skips pregnancy tasks without row locks — can overwrite a committed completion.** `backend/app/services.py:393`. The exact bug class deliberately fixed in `skip_pending_tasks_for_animal` (`services.py:108-112`, `for_update=True` + re-check) and `record_kidding`'s leftover skip (`services.py:543-545`) — but the abort path still calls unlocked `_pending_tasks_for`. A worker's committed DONE completion (with attribution) can be flipped to SKIPPED by a concurrent abort's stale UPDATE; a double-abort race (unlocked CONFIRMED check, `breeding.py:214`) also writes duplicate "Pregnancy aborted" BucketMove rows. Fix: `for_update=True` + status re-check in the loop; lock the breeding row per 2-1.

**HIGH 2-3. Kidding vs abort TOCTOU: a pregnancy can end ABORTED with live born kids.** `backend/app/api/kidding.py:107-120`, `backend/app/api/breeding.py:213-221`, `backend/app/services.py:436-442`. Both gate on `br.outcome == CONFIRMED_PREGNANT` from an unlocked row; a concurrent abort + kidding both pass → outcome=ABORTED, doe in RESTING, yet `KiddingRecord` + kid Animals + weaning task inserted; bucket is whichever move committed last. `uq_kidding_breeding_record` doesn't help. Low-frequency, high-confusion corruption. Fix: `SELECT … FOR UPDATE` the breeding record in both flows; loser re-reads and fails its guard.

**MEDIUM 2-4. Lock-order inversion between task completion and status change → deadlock 500.** `backend/app/api/tasks.py:223` + `services.py:692-723` (task lock → animal lock at flush for WEANING/BUCKET_MOVE) vs `backend/app/api/animals.py:304` + `services.py:108` (animal lock → task locks). Completing a weaning/delivery-move duty while the doe is concurrently sold/marked dead → `DeadlockDetected` → 500. No corruption (Postgres kills one side) but an avoidable 500. Fix: canonical lock order — animal lock before mutating it inside `complete_task`, or `change_status` locks tasks before the animal.

**MEDIUM 2-5. `set_daily_kg_per_head` upsert race → unhandled IntegrityError 500.** `backend/app/api/feeding.py:58-64`, `backend/app/services.py:1157-1170`. Check-then-insert against `uq_feed_setting_per_bucket` with no lock and no IntegrityError handler — the only constrained write path that doesn't catch it; two concurrent first-time saves of the same bucket's ration → one gets a 500. Fix: catch IntegrityError, rollback, retry as UPDATE (or `INSERT … ON CONFLICT DO UPDATE`).

**LOW 2-6. Health-form task completion is unlocked.** `backend/app/api/health.py:114-129` uses `db.get(Task)` then `complete_task`; concurrent posts both pass PENDING; for a manual *recurring* duty this also defeats `spawn_next_occurrence`'s check-then-insert dedupe (`services.py:741-754`, no backing constraint) → duplicate next occurrences. Fix: fetch with `with_for_update()` like `_get_task` (`tasks.py:86-104`). *(Cross-referenced by 4-H1 as the same class, lower likelihood.)*

**LOW 2-7. Kidding IntegrityError handler mislabels tag-race collisions.** `backend/app/api/kidding.py:163-166` maps *any* IntegrityError to 409 "already has a kidding record"; a concurrent insert winning `uq_animal_tag_per_farm` (explicit kid tag raced, or auto `<doe>-K<n>` tag raced — `_unique_tag` at `services.py:478-484` uniquifies against a pre-insert snapshot) rolls back safely but returns a misleading message. Fix: branch on `exc.orig.diag.constraint_name`.

**LOW 2-8. Broken auto-tag retry branch in `create_animal`.** `backend/app/api/animals.py:178-187`: after `db.rollback()` all ORM objects expire; the retry accesses `farm.id` (line 135) → forbidden sync refresh on AsyncSession → `MissingGreenlet` 500 instead of the intended retry. Practically unreachable (random-tag collision) but dead code as written. Fix: capture `farm_id = farm.id` before the loop (`breeding.py:146` already does this for `doe_tag`).

**LOW 2-9. Breeding vs concurrent sale of the doe.** `backend/app/api/breeding.py:130-156`: eligibility is a snapshot; if the doe is sold between the candidate check and commit, the BreedingRecord + ultrasound task still persist (`move_animal` no-ops on non-ACTIVE, `services.py:89-90`) → a SOLD doe carries an open PENDING breeding whose ultrasound task was never skipped. Fix: lock the doe row FOR UPDATE before inserting.

**LOW 2-10. Startup seeding races under multi-worker uvicorn.** `backend/app/main.py:36-42`, `backend/app/seed.py:239-275`: count-then-insert idempotency without `ON CONFLICT`; two booting workers both see empty reference tables and one crashes on `BucketDefinition.code`/`FeedRecipe.code`/`VaccineTemplate.name` uniqueness. Fix: catch IntegrityError per row, or `ON CONFLICT DO NOTHING`. *(Same as part of 4-L7.)*

**LOW 2-11. `delete_role` member-count TOCTOU.** `backend/app/api/team.py:397-406`: a concurrent role assignment between the count check and the DELETE → FK-violation 500 instead of the friendly 400. Fix: catch IntegrityError → 400.

**LOW 2-12. Unlocked DONE-stamp on KIDDING_DUE.** `backend/app/services.py:529-534` uses unlocked `_pending_tasks_for`; a concurrent user-skip of that task (skip endpoint allows form-linked duties, `tasks.py:242-262`) can be overwritten back to DONE. Benign direction, but inconsistent with the locking discipline elsewhere.

### Checked and clean

Transaction boundaries: every multi-step flow (purchase batch → stub animals → quarantine tasks → expense; kidding → kid animals → task closeout → weaning task; breeding → ultrasound task → move) runs in one request-scoped transaction with exactly one commit; all error paths rollback before raising; no partial-commit flows. `autoflush=False`: the two load-bearing explicit flushes check out (`services.py:350-353, 535-538`). Constraints vs model drift: all uniques incl. the partial `uq_breeding_open_pregnancy` exist identically in models and migrations; the CONFIRMED exclusion is correctly reasoned (`models.py:452-458`). Correctly locked paths: double-sale, task complete/skip/verify/reject, feed mix (canonical ingredient order), restock, farm-cap, skip-vs-complete. IntegrityError → clean 4xx nearly universal. READ COMMITTED + explicit row locking is appropriate; no SERIALIZABLE-only anomalies beyond those listed. `test_concurrency.py` genuinely races (two ASGI clients + `wait_until_blocked` against `pg_stat_activity`) and covers the fixed races — **gaps: no concurrent ultrasound, abort, kidding-vs-abort, or weaning-vs-status tests — exactly findings 2-1..2-4.**

### Lens health summary

Concurrency posture is mature and self-aware: highest-traffic check-then-act flows are properly row-locked and constraint-backed. Remaining risk clusters in the pregnancy-lifecycle state machine (three HIGH races + one deadlock window), all fixable with the established `FOR UPDATE` + re-check idiom; no schema changes required except optional ON CONFLICT for the feed-setting upsert.

---

## Lens 3 — Domain Logic Correctness

### Findings

**HIGH 3-1. Deworming events recorded by drug name never match the "Deworming" vaccine template — per-animal schedule shows false OVERDUE forever.** `backend/app/services.py:916-927` (`_matches`) matches templates purely by substring over `product_name + disease_target`, ignoring `HealthEvent.type`. The "Deworming" template (`seed.py:204-210`, 6-month repeat) only matches if literal "deworming" appears in the free-text fields; the health form leaves both optional and blank and doesn't prefill from the linked task (`frontend/src/app/(app)/health/page.tsx:602-609, 261-276`), so the natural entry (type=DEWORMING, product "Albendazole", target blank) never matches; even "deworm" ≠ "deworming". Impact: the health schedule page (`vaccination_schedule_for_animal`, `services.py:899-977`) permanently reports every animal's June/January deworming as OVERDUE despite compliant recording — owners lose trust or double-dose. Same fragility for trade-name vaccines (e.g. "Raksha-Triovac" with blank target won't match "FMD"). Fix: match the Deworming template by `event.type == DEWORMING` (and/or accept "deworm"); alias/drug-name lists for vaccines.

**MEDIUM 3-2. Selling/culling a confirmed-pregnant doe leaves a phantom pregnancy that pollutes the kidding due list forever.** `api/kidding.py:52-72` and `api/dashboard.py:121-133` select CONFIRMED_PREGNANT breeding records with no doe-status filter; `change_status` (`api/animals.py:295-339`) skips pending tasks but leaves the BreedingRecord CONFIRMED_PREGNANT; `record_kidding` (`services.py:441-442`) rejects resolution because the doe isn't ACTIVE; `mark_aborted` is the only out and nothing prompts it. Impact: sold/dead pregnant does sit in "Overdue kiddings" indefinitely; conception-rate stats count a pregnancy that can never resolve. Fix: auto-resolve open pregnancies on status change (ABORTED or new terminal state), or filter due lists by doe ACTIVE.

**MEDIUM 3-3. "Move to DELIVERY" task silently no-ops if the doe was never moved to PREGNANCY_LATE.** `services.py:703-706` — completing the auto DELIVERY-move duty only relocates the doe when `current_bucket == PREGNANCY_LATE`; the EARLY→LATE transition is only a dashboard *suggestion* (`services.py:1045-1054`, gestation ≥100), never a task. An owner who ignores the suggestion and completes the DELIVERY duty gets a green DONE while the doe stays in PREGNANCY_EARLY — wrong bucket counts, and she stays on MAINTENANCE_75_25 instead of LACTATING_60_40 (`services.py:1122-1131`) through her highest-demand weeks. Fix: accept PREGNANCY_EARLY in that condition too.

**MEDIUM 3-4. Ultrasound results accepted for a sold/dead doe's lingering PENDING breeding.** Selling skips pending *tasks* but leaves the BreedingRecord PENDING; `submit_ultrasound` (`api/breeding.py:185-203`) / `record_ultrasound_result` (`services.py:288-357`) never check doe status. Recording "pregnant" spawns the full pre-kidding task set for a non-existent animal; `move_animal` silently no-ops (`services.py:89-90`), compounding 3-2. Fix: reject the submit when the doe is not ACTIVE (mirror the `record_kidding` guard, `services.py:441-442`).

**LOW 3-5. Missed vaccine boosters are invisible in schedule status.** `services.py:939-966`: `booster_due` (e.g. FMD +3.5 weeks, `seed.py:181`) is computed/displayed but `status` only looks at first/last dose + repeat interval; first-dose-only animals show DONE. Fix: add BOOSTER_DUE/OVERDUE state when `booster_due < today()` and no second matching event.

**LOW 3-6. `add_feed_stock` treats explicit ₹0/kg as "no price".** `services.py:1287` `if price_per_kg:` drops an explicit 0 (no ₹0 expense, `last_purchase_price_per_kg` not zeroed), inconsistent with the careful explicit-₹0 handling in `create_purchase_batch` (`services.py:648`). Fix: `if price_per_kg is not None:`.

**LOW 3-7. Re-breeding branch bypasses the ≥22 kg rule.** `services.py:227-234`: does already in BREEDING need only age ≥10 to be re-bred, no weight check; cull-candidate does also remain breedable. Deliberate-looking but diverges from the README's breeding-ready criteria (≥10 mo **and** ≥22 kg). Fix: document it, or enforce the weight floor.

**LOW 3-8. Heat cycle 21d is data-only.** After a FAILED ultrasound the doe sits in BREEDING with no "next heat ~+21d" task; `heat_cycle_number` is free-text defaulting to 1 (`schemas/breeding.py:14`), so reports' `first_cycle_rate` (`api/dashboard.py:226`) depends on manual increments. Spec gap, not a code bug.

**LOW 3-9. `avg_age_months=0` becomes "unknown age".** `services.py:623-625` uses `if avg_age_months` (0.0 falsy), so purchased newborn kids get `estimated_dob=None` instead of `= batch_date`, breaking age-based vaccine scheduling. Fix: `if avg_age_months is not None`.

**LOW 3-10. Per-head purchase price drift.** `services.py:610` rounds `total_price / count` to 2dp per animal while the expense books the full total (`services.py:648-659`); Σ animal `purchase_price` can drift from the ledger by up to `count × ₹0.005` (₹5 at the 1000-head cap). Cosmetic; could absorb the remainder on the first animal like `record_health_event` does (`services.py:868-874`).

**LOW 3-11. ABORTED resets the cull streak.** `_update_cull_candidate` (`services.py:360-377`) counts consecutive FAILED only; an abortion breaks the streak and the flag isn't recomputed until the next failed ultrasound. Defensible reading of "two consecutive failed cycles," but an abort is arguably also a failed cycle — product decision needed.

**LOW 3-12. Simulation weans one month later than the ops spec.** Monthly engine: kids ages 0–2, weaners 3+ (`engine.py:213-214`, `assumptions.py:169` comment "weaning at 3") vs the operational day-60 (~month 2) rule. Monthly-step approximation; note it in sim docs or shift the kid window to 0–1. *(Same as part of 9-7.)*

**LOW 3-13. `format_money` float-repr rounding.** `utils.py:116` uses `f"{amount:.2f}"`, so 2.675 renders "2.67" (binary repr, half-even). Display-only, negligible.

### Verified clean

Gestation/kidding math exact (GESTATION_DAYS=150, window 145–155, acceptance band 100–200, `models.py:24-31`; validated in `record_kidding` + router; EKD = breeding+150, ultrasound = +32, `models.py:753-758`). Auto-task sequencing exact (ET+TT at EKD−40 inside the 4–6-week spec window; DELIVERY move at EKD−15 = day 135; kidding due at EKD; weaning at kidding+60). Quarantine protocol offsets 1/4/5/10/20/30/40/45 with `due_date = batch.date + offset − 1` and the exact spec order; day-45 release moves only QUARANTINE/ACTIVE batch animals; auto-task due-date lock prevents early release/weaning. Vaccine/deworming intervals all match the README (FMD 6-monthly, PPR 36-monthly, ET/HS/Goat Pox 12-monthly, deworming 6-monthly; pre-kidding ET+TT correctly pregnancy-linked). Feeding: SHIFT_SPLIT exactly 40/20/40 with spec'd times; all five recipes sum to exactly 100 kg with correct roughage:concentrate ratios; recipe switches at RESTING day 10 and MALE_KIDS day 91 boundary-correct. `is_breeding_ready` enforces the spec exactly; cull flag at 2 consecutive FAILED with load-bearing flush documented, cleared on new conception. Task idempotency/concurrency: PENDING-only ultrasound guard, real UNIQUE on kidding, recurring-spawn dedupe, re-check-under-lock skips. Dates/timezones: naive-UTC consistent; +1-day headroom in both schema and router; `add_months` clamps month-end. Money: Indian grouping correct both ends incl. negatives; non-finite guards; health-cost split sums exactly; `monthly_pnl` filters NaN/±inf.

### Lens health summary

Core reproductive arithmetic and the auto-task engine are exact and well-guarded. Real exposure: health-schedule matching (3-1) breaks deworming in the most common recording path, and lifecycle edge cases around selling pregnant does (3-2..3-4) degrade data quality over time. Nothing corrupts the financial ledger; the worst outcomes are misleading schedules and due lists.

---

## Lens 4 — Backend Architecture & Code Quality

### Findings

**HIGH 4-H1. Concurrent ultrasound submissions are not serialized; duplicate side effects possible.** `backend/app/api/breeding.py:185-203` fetches without `with_for_update()`; `record_ultrasound_result` (`backend/app/services.py:288-357`) guards only on the in-memory PENDING check; no DB constraint backs it (`uq_breeding_open_pregnancy` covers creation only, `models.py:459-466`). The service docstring's idempotency claim holds for sequential replays but not races. Every other double-apply path was hardened; ultrasound was missed. Same class, lower likelihood: `api/health.py:115-129` completes a linked task without a row lock. Fix: lock in `_get_breeding_record` for ultrasound/abort endpoints, or re-read `outcome` under a lock in the service. *(Same as 2-1; the health-task variant is 2-6.)*

**HIGH 4-H2. Zero logging/observability in the application.** No `import logging`, logger, request logging, or health endpoint anywhere in `backend/app/` (grep-verified). Consequences: unhandled exceptions become bare 500s with no app-level record; `seed_startup` failure in the lifespan (`main.py:40-41`) kills startup with only a uvicorn traceback; `Role.permission_set` silently swallows corrupt JSON (`models.py:239-245`, empty permission set = silent lockout) with no trace; rate-limit blocks, legacy-hash upgrades, and pbkdf2 failures leave no audit trail. Fix: configure `logging` in `create_app()`, a generic exception handler that logs and returns a clean 500, log the intentional swallow, add `/healthz`. *(Same as 11-M4, which rates it MEDIUM.)*

**MEDIUM 4-M1. God files: `services.py` and `models.py`.** `services.py` is 1,369 lines spanning eight unrelated domains and still carries its "Phase 2" docstring; `models.py` is 796 lines / 21 tables. Both are the import hub for every router — maximal blast radius for any change. Fix: split per domain mirroring the `api/`/`schemas/` layout; mechanical, no behavior change.

**MEDIUM 4-M2. Dead v1 leftovers in production modules, kept alive only by tests.** `backend/app/utils.py:26-78` (`parse_date`, `parse_float`, `parse_int`, `finite`) and `utils.py:81-118` (`format_date`, `format_money`) — zero app-code callers; `permissions.py:71-72` `permission_label` — test-only; `schemas/animals.py:148-149` `AnimalIdsIn` — unused; `models.py:325` `Animal.kidding_records` — never dereferenced and uses default `lazy="select"`, which would raise `MissingGreenlet` if ever used from async code. Commit `29344f6` ("Remove dead code and stale v1 leftovers") missed them. Fix: delete (and their tests), or move formatters to the frontend. *(The `kidding_records` item is also 5-L4.)*

**MEDIUM 4-M3. Verbatim duplication across routers, and cross-router private imports.** `task_action_url` duplicated character-for-character (`api/tasks.py:57-71`, `api/dashboard.py:51-64` — the dashboard's `_task_out` is a weaker copy missing `assigned_role_name`/`animal_tag` enrichment); `_breeding_out` twice (`api/breeding.py:32-51`, `api/dashboard.py:73-77`); routers import each other's private helpers (`api/kidding.py:23`, `api/health.py:26`); the `MAX_INT32_ID` forged-id guard re-implemented in ~8 routers. Fix: extract `api/_shared.py` (out-builders, visible_to, bounded farm-scoped getter).

**MEDIUM 4-M4. Enum/constant duplication between models and schemas (drift risk, no parity guard).** `BucketStr` (`schemas/animals.py:20-31`), `TaskCategoryStr`, `HealthEventTypeStr`, `TransactionCategoryStr`, `KidStatusStr`/`KiddingEaseStr` each re-declare a models enum as a hand-maintained `Literal`; constants duplicated too (`MAX_RECUR_DAYS` `schemas/tasks.py:22` vs `services.py:668`; `MAX_BATCH_COUNT`/`MAX_AGE_MONTHS` `schemas/purchases.py:12-13` vs `services.py:562-563`; `_SYSTEMS` `api/simulation.py:44` vs `simulation/defaults.py:21`). A new bucket/category added to models silently won't be accepted by the API. Fix: derive Literals from the enums, or add a parity test.

**MEDIUM 4-M5. Business logic living in routers.** Reports aggregation (`api/dashboard.py:192-286`), cohort bucketing (`api/simulation.py:144-174`), all-time totals loop (`api/finance.py:65-76`), task status mutation in the skip endpoint (`api/tasks.py:256-257` — the only task transition not in a service). Untestable-without-HTTP and inconsistent with the otherwise-clean layering. Fix: move to `services.py` (or its M1 successors).

**MEDIUM 4-M6. DB engine lifecycle gaps (`backend/app/db.py`).** No `pool_pre_ping`/`pool_recycle` (`db.py:28-36`) — after a Postgres restart or idle-timeout, pooled connections are stale and the first request per connection 500s; the lifespan never disposes the engine (`main.py:36-42` — nothing after `yield`) — connections leak per app restart (tests, `--reload`); `reset_engine()` (`db.py:49-53`) drops references without `await engine.dispose()`. Fix: `pool_pre_ping=True`, dispose in lifespan teardown and `reset_engine`. *(pool_pre_ping also 5-M1 and 11-M2; engine-dispose/graceful shutdown also 11-M5.)*

**LOW 4-L1. Silent failure modes without errors.** `Role.permission_set` returns `set()` on corrupt JSON (`models.py:243-245`); `_scenario_out` does unguarded `json.loads(scenario.assumptions)`, so one bad row 500s every scenario list (`api/simulation.py:50-60`). Fail-closed is fine, but log it / don't 500-without-message. *(Scenario-list variant also 9-6.)*

**LOW 4-L2. `Any` leaks / casts / type-ignore.** `kids: list[dict[str, Any]]` (`services.py:425`, `api/kidding.py:122`); three `cast()` calls on the untyped `quarantine_schedule` dict (`services.py:642-644` — a TypedDict fixes all three); `# type: ignore[arg-type]` on `get_preset` (`api/simulation.py:139` — runtime-safe, but a `Literal` query type removes the ignore).

**LOW 4-L3. `ilike` wildcard injection + unbounded `q`.** `api/animals.py:98-99` interpolates `q` into `ilike("%...%")` without escaping `%`/`_` and without a length cap. Cosmetic result corruption, not a breach.

**LOW 4-L4. O(herd) eligibility check per breeding create.** `create_breeding` builds the full candidate-doe set (all active does + selectin-loaded collections, `services.py:188-235`) just to test membership of one doe (`api/breeding.py:136`). Correct but quadratic-ish under load; a targeted existence query suffices.

**LOW 4-L5. `lazy="selectin"` on Animal collections drags everything.** `weight_records`, `bucket_moves`, `breedings_as_doe` (`models.py:310-324`) load on *every* `select(Animal)`, including reports over all historical animals (`api/dashboard.py:194`). Works today; will hurt at scale. Explicit `selectinload` at call sites beats ambient loading. *(Same root issue as 5-H1, which rates it HIGH.)*

**LOW 4-L6. Inconsistent error/validation styles.** Typed `InsufficientFeedError` (`services.py:1221`) vs bare `ValueError` elsewhere; email validation via `_EmailMixin` for auth (`schemas/auth.py:6-13`) but inline in the router for workers (`api/team.py:139-140`); `KiddingCreateIn.date` validated in the router (`api/kidding.py:115-120`) instead of `PastOrTodayDate`.

**LOW 4-L7. Startup seeding edge cases.** Count-based seeding (`seed.py:241,254,263`) can race two first-boot processes into a unique-violation crash (see 2-10); `backfill_task_assignments` runs for every farm on every boot (`seed.py:345-352`) — harmless today since unassignment isn't API-reachable, but it silently re-assigns any NULL-role auto task forever.

**LOW 4-L8. Stale docs/comments.** README:162 says "Migrations (single head: initial schema)" but `alembic/versions/` has 4 migrations; `models.py:3-5` still claims v1 "byte-identical" porting; `services.py:1` says "Phase 2"; `alembic/versions/__init__.py` is an unusual artifact in a versions dir.

**LOW 4-L9. Portability/infra nits.** `fcntl` (`security.py:17`) makes the backend Unix-only (undocumented for Windows contributors); `config.py:13` `env_file=".env"` resolves against CWD, so running uvicorn from anywhere but `backend/` silently drops env files (also 11-L1); `api/health.py:76-89` silently ignores a stray `animal_id` sent with batch scope (deliberate v1 behavior, worth a validator rejection).

### Checked and clean

Layering in services: zero HTTP imports; routers own all HTTPExceptions (the one real inversion is M5). Error handling: uniform pattern (router HTTPException, service ValueError → 400/409, IntegrityError → raced-write 4xx) applied consistently; **no bare/`except Exception` swallow anywhere**. `_json_safe` 422 handler correctly neutralizes non-finite floats/bytes (`main.py:45-67`). Pydantic mutable defaults safe (v2 deep-copies per instance). Schema bounds thorough (money/weight/quantity caps with rationale, PastOrTodayDate headroom, BoundedId + int32 guards against asyncpg DataError). Concurrency hardening present (FOR UPDATE, canonical lock ordering, partial unique index, re-check-after-lock). `seed.py` idempotency correct; `seed_new_farm` flush/commit split honored. Auth plumbing (timing-equalized login, pbkdf2→Argon2id upgrade, leak-guarded limiter, flock keygen). Tenancy: every domain query farm-scoped; cross-farm ids stripped or 404'd; `_visible_to` assignment checks.

### Lens health summary

Production-quality core: disciplined layering, remarkably consistent error handling, thorough validation, deliberately closed races. The two gaps that matter are the missed ultrasound race (4-H1) and the total absence of logging (4-H2). Below that: structural debt (god files, duplicated helpers/enums, v1 dead code) — maintenance friction that compounds, not danger.

---

## Lens 5 — DB Schema, Migrations & Query Performance

### Findings

**HIGH 5-H1. Every `Animal` query eagerly loads its full weight/bucket-move/breeding history via mapper-level `lazy="selectin"`.** `backend/app/models.py:310-324` (+ `BreedingRecord.kidding_record`, `models.py:487-489`). Because `AnimalOut` exposes computed fields (`latest_weight_kg`, `is_breeding_ready`, `days_in_current_bucket`, `is_currently_pregnant` — `schemas/animals.py:78-82`), the mapper selectin-loads these collections on *every* Animal load — including `db.get(Animal, …)` for simple writes. Hot read paths compound it: `dashboard` (`api/dashboard.py:89-95`), `list_animals` (`api/animals.py:89-110`), `buckets_board` (`api/buckets.py:24-28`), `herd_snapshot` (`api/simulation.py:156-158`), `breeding_candidate_does` (`services.py:193-207`) each load the full ACTIVE herd *plus* 4 extra `WHERE IN (...)` queries pulling every weight record, bucket move, breeding, and kidding those animals ever had. Batched (not N+1), but payload grows linearly with farm history — a 3-year, 500-head farm ships tens of thousands of rows per dashboard load. Fix: lazy at mapper level + explicit `selectinload` where computed fields serialize; count/board endpoints select only needed columns (`func.count()` GROUP BY; lateral/`DISTINCT ON` for latest weight). *(Same root as 4-L5.)*

**HIGH 5-H2. `GET /api/dashboard/reports` loads the farm's entire history into Python, unbounded.** `backend/app/api/dashboard.py:194` (ALL animals incl. sold/dead, with the H1 cascade), `:221-224` (all breeding records ever), `:227-235` (all kiddings + kids), `:236-244` (all kid entries); aggregates (`status_counts`, `deaths_by_month`, `conception_rate`, `stillborn`) computed row-by-row in Python. Grows without bound; the single heaviest endpoint. Fix: push aggregation into SQL (`GROUP BY status`, `GROUP BY to_char(status_date,'YYYY-MM')`, AVG/COUNT over kid entries); hydrate only the cull-candidate list.

**MEDIUM 5-M1. No `pool_pre_ping` / `pool_recycle` on the async engine.** `backend/app/db.py:28-36` sets pool size/timeout and statement_timeout (good) but never validates pooled connections; after a Postgres restart/failover/idle-kill (managed PG, pgBouncer, NAT), the pool hands out dead connections and the first request after idle 500s with `InterfaceError`. Fix: `pool_pre_ping=True`; consider `pool_recycle` below any infra idle timeout. *(Same as 11-M2 and part of 4-M6.)*

**MEDIUM 5-M2. Finance all-time totals load every transaction row into Python.** `backend/app/api/finance.py:65-76` — `select(type, amount).where(farm_id)` with Python-side sum + per-row isfinite filter, on every finance page load, unbounded. Fix: `SELECT type, sum(amount) … GROUP BY type` (finite filter expressible in SQL).

**MEDIUM 5-M3. Tasks "awaiting verification" query is unbounded; "completed" tab filters *after* `LIMIT 100`.** `backend/app/api/tasks.py:142-146`: for `tasks.verify` holders, ALL farm-wide DONE tasks are loaded and filtered to CLEANING in Python — the DONE pile grows forever (recurring cleaning duties complete daily). `:147-160`: `finished.order_by(…).limit(100)` is filtered in Python afterwards, so if the newest 100 rows are dominated by unverified CLEANING rows, the completed tab shows far fewer than 100 entries even when more eligible rows exist (mild correctness quirk + wasted I/O). Fix: filter category in SQL for the awaiting tab; express the completed-tab predicate in SQL before the limit.

**MEDIUM 5-M4. Missing index: `tasks.purchase_batch_id`.** `backend/app/models.py:659` (no `index=True`); not in any migration. Queried by `Task.purchase_batch_id.in_(ids)` on every purchases list (`api/purchases.py:39-43`) and batch detail (`:119-123`); `tasks` is ever-growing → seq scan that worsens with age. Fix: `op.create_index("ix_tasks_purchase_batch_id", …)` + `index=True` in the model.

**MEDIUM 5-M5. `GET /api/breeding` returns all breeding records of all time.** `backend/app/api/breeding.py:81-90` — no limit, ordered desc, plus `breeding_candidate_does` (`services.py:193-235`) loading every active female with full weight+breeding history (H1 cascade) and a second open-breedings query. ~2 cycles/doe/year → ~1000 rows/year for a 500-doe farm. Fix: cap history (e.g. latest 100) or paginate; candidates could be a SQL-side filter on the partial-index columns.

**LOW 5-L1. `await`-in-loop for per-bucket feed settings (bounded N+1).** `backend/app/api/buckets.py:42` (10 sequential `get_daily_kg_per_head` calls) and `services.py:1197` (same per bucket group in `feeding_plan`). Bounded ~10 buckets × 2 queries, but 10–20 extra round-trips per request. Fix: load all `BucketFeedSetting` rows + all `BucketDefinition`s in two queries, join in Python.

**LOW 5-L2. Full per-farm tag scan on every animal create / kidding.** `backend/app/services.py:406` (`generate_unique_tag`) and `:475` (`record_kidding`) select every tag for the farm and build a Python set per insert — O(herd size) rows per write. Fix: probe existence of the generated candidate (`WHERE farm_id=? AND tag_number=?`, backed by `uq_animal_tag_per_farm`) and rely on the IntegrityError retry already in place.

**LOW 5-L3. Unindexed FK columns on hot lookup paths.** `farms.owner_id` (`models.py:207`) — queried by `accessible_farms` (`deps.py:58`), `create_farm` (`api/auth.py:205`), team guards (`api/team.py:151,286`); small table today, seq scan on every farm-list/login-adjacent request as the SaaS grows. `tasks.breeding_record_id` (`models.py:660`) — filtered in `_pending_tasks_for` (`services.py:115-127`); usually rescued by farm_id+status indexes, low priority.

**LOW 5-L4. Dead relationship `Animal.kidding_records` would 500 if ever touched.** `backend/app/models.py:325` — default `lazy="select"` (unlike its selectin siblings), nothing reads it (grep-verified); any future access raises `MissingGreenlet`. Fix: `lazy="selectin"` for consistency or remove. *(Same as part of 4-M2.)*

**LOW 5-L5. Startup seeding is O(number of farms).** `backend/app/seed.py:345-352` — `seed_startup` iterates every farm running `seed_default_roles` + `backfill_task_assignments` (2+ queries each) at every process start; will slow cold starts as farms accumulate (and multi-worker deployments all run it).

**LOW 5-L6. `list_animals` pagination exists but is unused.** `backend/app/api/animals.py:86-110` — optional `limit`/`offset` (max 1000), but the default path returns the full filtered herd and the frontend never passes a limit (`frontend/src/app/(app)/animals/page.tsx:361-367`). By design per the "v1 default" comment, but combined with H1 the herd list is the second-heaviest unbounded response.

### Checked and clean

Migration state: single linear chain `ed5efe13a516 → c4b72167db86 → d8f2b6a41e90 → 91a712b0367b`, single head; every index/UniqueConstraint/partial unique index in models has a matching migration op; `alembic/env.py` targets `Base.metadata` so autogenerate catches drift. Concurrency/locking correct (FOR UPDATE with post-lock re-checks; canonical ingredient-order locking in `mix_feed_batch`; unique-race → 4xx everywhere). N+1: none outside L1; selectinload applied correctly elsewhere; `_batch_out` uses batched GROUP BY aggregates. Bounded history endpoints: health events (100), kidding history (30), transactions (200), completed tab (100). Pool guardrails: `statement_timeout=30s`, `pool_timeout=30`, sane sizing, all env-configurable. `monthly_pnl` properly SQL-aggregated (`services.py:1330-1368`).

### Lens health summary

Schema/migration discipline is genuinely good (single head, lockstep models/migrations, thoughtful partial indexes, correct row-locking). The systemic weakness is read-side amplification: mapper-level selectin on Animal history collections plus unbounded aggregate endpoints computed in Python. None of it bites at demo scale; dashboard and reports degrade linearly with farm age and herd size. Fixes are mostly mechanical: SQL aggregates, per-endpoint eager-load scoping, two missing indexes, `pool_pre_ping`.

---

## Lens 6 — Frontend Security & API Client

### Findings

**HIGH 6-1. Refresh tokens are stateless: no revocation, no rotation invalidation, no reuse detection.** `backend/app/security.py:164-183` (jti issued, never stored/checked), `backend/app/api/auth.py:157-171` (rotation leaves the old JWT valid for its 14-day TTL; logout only deletes the browser cookie). Impact: anyone capturing a refresh token keeps minting access tokens up to 14 days after logout/rotation/password change; rotation provides zero theft detection — attacker and victim refresh in parallel indefinitely. Undermines the otherwise good design (short-lived in-memory access token, httpOnly cookie). Fix: server-side refresh state (`refresh_tokens` table keyed by `(user_id, jti_hash)` or token family); consume on refresh, reject reuse (reuse = theft signal → revoke family); revoke on logout/password change. *(Same as 0-1/0-2, 1-1, 11-L5.)*

**MEDIUM 6-2. No Content-Security-Policy (and no HSTS) on the frontend.** `frontend/next.config.ts:1-9` — headers are X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy; the comment defers CSP to "a production-only tightening pass" that never happened. Impact: any single future XSS sink is immediately fatal — injected JS can call `/api/auth/refresh` itself and read the access token from the JSON response, exfiltrating a live session (httpOnly doesn't protect against this); no `frame-ancestors` modern equivalent; HSTS absent (nothing at the TLS terminator sets it). Fix: production CSP (report-only first): `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'`, plus `Strict-Transport-Security` in prod; per-request nonces via middleware if stricter script-src is needed later. *(Same as 11-L4, which rates it LOW.)*

**LOW 6-3. Forced-logout path skips the cleanup that manual logout performs.** `frontend/src/lib/api-client.ts:116-117` + `frontend/src/lib/auth-context.tsx:114-118` — on refresh failure the client nulls the token and fires `onAuthFailure`, which only does `setUser(null); router.push("/login")`; compare `signOut` (`auth-context.tsx:77-91`), which also clears the TanStack cache, farm id, and localStorage farm key. No cross-user leak (cache cleared before any new user's queries), but the two paths should behave identically; N concurrent 401s with failed refresh also fire N redundant `router.push("/login")`. Fix: `onAuthFailure` runs the same cleanup as `signOut`, guarded to fire once per logout transition.

**LOW 6-4. Login page mislabels 429 rate-limit responses.** `frontend/src/app/login/page.tsx:68-74` — any non-401 `ApiError` (including the backend's 429, `backend/app/api/auth.py:135-136`) renders "Could not sign in — is the backend running?"; a throttled user is told the server is down and retries, extending the lockout. Fix: special-case `err.status === 429`. *(Same as 7-2 and 8-3, which cover register too.)*

### Verified clean

XSS sinks: zero occurrences of `dangerouslySetInnerHTML`, `innerHTML`, `document.write`, `eval`, `new Function` across `src/**`; all content renders as React text nodes; dynamic hrefs use integer IDs or `encodeURIComponent`; `task.action_url` is server-built from fixed templates + int IDs — no `javascript:` injection. Token storage: access token in a module-level variable only; never localStorage/sessionStorage/URL/logs; only the farm id (non-secret int) persisted. Refresh-retry races: `refreshPromise ??=` correctly dedupes concurrent 401s into one refresh; retry strictly single-shot — no infinite loop; `/refresh` and `/logout` excluded from retry (`NO_REFRESH_PATHS`) preventing recursion. Manual logout cleanup complete and failure-tolerant; cookie set/delete paths match. CSRF on the refresh flow clean (httpOnly + SameSite=Lax + Path-scoped cookie, POST-only, farm context in a header, credentialed CORS pinned to exact origins). Error info leakage: pages render only server-authored `err.detail`; 422s sanitized; error boundary shows a generic message; login timing-equalized. All traffic funnels through `apiFetch`/`custom-instance` (only 3 raw fetch calls) — no auth-bypassing request paths.

### Lens health summary

Genuinely solid client-side engineering: memory-only tokens, correct dedup'd single-shot refresh, no XSS sinks, no token leakage. Blocked from "excellent" mainly by the missing refresh-token revocation model (server side) and the absent CSP as defense-in-depth.

---

## Lens 7 — Frontend Code Quality & UX

### Findings

**MEDIUM 7-1. Tasks page ignores the `?tab=` deep-link the dashboard generates.** `frontend/src/app/(app)/tasks/page.tsx:368` — `useState("today")` is the only tab source; the page never reads search params, while `dashboard/page.tsx:167,181,201,270` links to `/tasks?tab=overdue` and `/tasks?tab=today`. Impact: clicking "View" on an overdue task lands on the **Today** tab where the task isn't visible — a broken navigation promise. Fix: wrap in `Suspense`, initialize `tab` from `useSearchParams().get("tab")`, validate against the visible-tab set (fall back to "today").

**MEDIUM 7-2. Login/register hide rate-limit and server errors behind "is the backend running?"** `frontend/src/app/login/page.tsx:68-74` — only 401 gets a real message; everything else → "Could not sign in — is the backend running?". `frontend/src/app/register/page.tsx:72-78` — only 400 surfaces `err.detail`. The backend returns 429 with detail "Too many attempts — please try again later." (`backend/app/api/auth.py:41,110,136`) and 422 for password-policy failures. Impact: a throttled user is told the backend is down and may retry/restart instead of waiting. Fix: `err instanceof ApiError ? (err.status === 401 ? "Invalid email or password." : err.detail) : fallback` on both pages. *(Same as 6-4 and 8-3.)*

**MEDIUM 7-3. Feeding plan "done" badge mis-fires for buckets split across recipe lines.** `frontend/src/app/(app)/feeding/page.tsx:281-284` aggregates dispensed kg **per bucket**; `:362-372` compares that bucket-wide total to **each line's** `daily_kg`, while the backend splits one bucket into multiple plan lines keyed by `(bucket, recipe)` (`backend/app/services.py:1186-1198`), each with its own `daily_kg`. Impact: BREEDING with lines 30 kg (recipe A) + 15 kg (recipe B) shows "done" on **both** lines after dispensing only 30 kg — feed staff can under-feed a subgroup believing it's finished. Fix: group lines per bucket and compare summed line `daily_kg` against summed dispensed total, or match dispense records by `recipe_code` per line.

**MEDIUM 7-4. Verify button shown to the one user the API forbids from using it.** `frontend/src/app/(app)/tasks/page.tsx:164-181` — any `tasks.verify` holder gets Verify/Reject on the awaiting tab; the backend 409s when `completed_by_id == user.id` and the user isn't the owner (`backend/app/api/tasks.py:272-275`); `TaskOut.completed_by_id` is available client-side. Impact: a worker-manager who completed a cleaning duty clicks Verify and gets a 409 toast — the UI offers an action the API must refuse. Fix: hide/disable Verify+Reject when `task.completed_by_id === currentUser.id`.

**LOW 7-5. `localToday()` vs backend UTC-today: off-by-one in the IST 00:00–05:30 window.** `frontend/src/app/(app)/tasks/page.tsx:118` mirrors the "auto duties unlock on due date" guard using **local** today; the backend compares against **UTC** today (`backend/app/api/tasks.py:235`, `utils.py`) — in early-morning IST the button unlocks up to a day early → 409 on click. Same class, cosmetic: "Xd late" labels (`dashboard/page.tsx:162`, `tasks/page.tsx:269-273`, `kidding/page.tsx:438-440`) and overdue/due-soon tinting (`tasks/page.tsx:253-255`, `health/page.tsx:99-115`) can be off by one in that window. Fix: use a UTC-date helper for *comparisons* (writes are fine — the backend's +1-day headroom covers local-today posts).

**LOW 7-6. `usePermissions` turns fetch errors into silent "no access".** `frontend/src/lib/use-permissions.ts:16-21` — on query error, `perms` is an empty set: every page renders "You don't have access to this page" and the sidebar empties (`layout.tsx:131-136`) instead of surfacing that the permissions call failed. Fix: expose `isError`/`error` and render an error state; consider exposing `is_owner` from `PermissionsOut` (would enable fix 7-4).

**LOW 7-7. Dashboard "Record" kidding link ignores kidding permissions.** `frontend/src/app/(app)/dashboard/page.tsx:235-241` — shown to any `dashboard.view` holder; a user without `kidding.view` lands on access-denied; one with view-but-not-manage lands on `/kidding` where the auto-open silently no-ops (`kidding/page.tsx:358` gates on `canManage`). Fix: render the link only when `can("kidding.manage")`.

**LOW 7-8. `/animals` consumes search params once, via `useState` initializers.** `frontend/src/app/(app)/animals/page.tsx:349-352,390` — after creating an animal from `?new=1` the param stays in the URL (dialog reopens on reload); same-route client navigations with changed params won't update the filters. Fix: react to `useSearchParams()` changes, or strip the consumed param with `router.replace` after opening the dialog.

**LOW 7-9. Accessibility: unassociated labels on Base UI selects (pattern-wide).** E.g. `animals/page.tsx:196`, `health/page.tsx:400,507,530`, `tasks/page.tsx:545,579,602`, `team/page.tsx:284` — `<Label>` without `htmlFor` sits next to a `SelectTrigger` (a `<button>`), so the control has no accessible name; the reject-reason input has only a placeholder (`tasks/page.tsx:182-188`). The simulation page does it right (`simulation/page.tsx:1153-1156` — `htmlFor` + trigger `id`). Fix: wire `htmlFor`→trigger `id` (or `aria-label`) everywhere.

**LOW 7-10. Dam/Sire shown as raw numeric ids.** `frontend/src/app/(app)/animals/[id]/page.tsx:478-497` — links render `#{dam_id}`/`#{sire_id}` instead of tag numbers (tags aren't in `AnimalOut`; needs a backend field or accepted as-is). Cosmetic.

**LOW 7-11. Minor form-label/consistency nits.** `health/page.tsx:577` — "Date *" label but the schema field is optional (`null` = today); `health/page.tsx:262-276,653-669` — selecting "— none —" in the linked-duty select doesn't revert the scope/type prefills from a previously chosen duty; `animals/[id]/page.tsx:95-100` — weight dialog skips the client-side future-date check other date forms have (backend still rejects).

### Checked and clean

React hooks rules: no conditional calls; every early return after all hooks; `set-state-in-effect` suppressions are genuine one-shot initializations. TanStack Query: base-key invalidations prefix-match all param variants; farm switch/sign-out clear the cache; debounced search, `enabled` gating, and `enabled:false` + refetch all correct. api-client: refresh dedup, no-recurse paths, in-memory token, `extractDetail` handles both error shapes. Form bounds vs backend schemas spot-verified exact (purchases batch 1–1000/age ≤240/year ≥2000, recur_days 1–3650, kid_count 1–3, BCS 1–5, kidding kids 1–10, weight >0); 422/409 details surfaced via toasts. Permission mirroring: nav codes match the catalog; animal-profile action buttons map 1:1. Date/datetime contract: `fmtDateTime` correctly appends `Z`; date inputs use `max={localToday()}` consistent with the backend headroom rule. Loading/error/empty states on every data page; route fallbacks exist. Animals list: backend unpaginated by default so no silent truncation. Theme toggle SSR-safety, `formatMoney` Indian grouping, `StatusBadge` fallback all fine.

### Lens health summary

Good shape: disciplined hooks, consistent invalidation, faithful validation mirroring, uniform states. Nothing critical/high — the worst are UX correctness bugs (ignored deep-link, false "done" badge, swallowed 429s, forbidden Verify button), plus an IST-midnight off-by-one class and a systematic label-association a11y gap. All fixes small and localized.

---

## Lens 8 — API Contract Drift

Verification method: regenerated the OpenAPI schema in-memory from `create_app().openapi()` and deep-compared against `shared/openapi.json` — **byte-for-byte identical**; regenerated the Orval client (pinned orval 8.23.0) — `models/` identical, `endpoints.ts` identical except an out-of-tree mutator-import artifact; `pnpm tsc --noEmit` passes; `git status` clean.

### Findings (all LOW)

**LOW 8-1. Client validation looser than the backend in two places; three more with no client cap.** `frontend/src/app/farm-select/page.tsx:29` allows farm `location` 200 chars vs backend 120 (`backend/app/schemas/auth.py:51`); `frontend/src/app/(app)/finance/page.tsx:96` allows transaction `notes` 500 vs backend 255 (`schemas/finance.py:31`); no client max where the backend caps: weight `notes` 255 (`animals/[id]/page.tsx:99` vs `schemas/animals.py:94`), move `reason` 255 (`:177` vs `animals.py:109`), status-change `notes` 255 (`:273` vs `animals.py:127`), health-event `route` 20 (`health/page.tsx:128` vs `schemas/health.py:65`). Impact: users pass client validation then get a 422 with a raw Pydantic message instead of inline field feedback. Fix: align zod schemas with the backend `Field(max_length=…)` values.

**LOW 8-2. Auth flows bypass the generated client with hand-maintained response types.** `frontend/src/app/login/page.tsx:62`, `register/page.tsx:63`, `farm-select/page.tsx:52`, `auth-context.tsx:79,94,121` call `apiFetch`/`fetch` directly with hand-written generics (`{ access_token; user: SessionUser }`, `FarmEntry`) even though generated hooks exist. Verified every payload/field read against `TokenOut`/`UserOut`/`FarmOut` — all currently correct — but these types are invisible to `tsc` drift detection if the backend schemas change. Fix: switch to the generated hooks, or derive `SessionUser`/`FarmEntry` from the generated models.

**LOW 8-3. Rate-limit (429) responses render as "backend down" on auth pages.** Login only special-cases 401 (`login/page.tsx:68-74`), register only 400 (`register/page.tsx:72-78`); the backend's 429 (`backend/app/api/auth.py:110,136`) falls through to "Could not sign in — is the backend running?" (same for a 422 from a >128-char password). Fix: display `err.detail` for any 4xx `ApiError`, as farm-select already does (`farm-select/page.tsx:61`). *(Same as 6-4 and 7-2.)*

**LOW 8-4. Spec says `x-farm-id` header is optional; runtime requires it.** 57 operations in `shared/openapi.json` declare the header `required: false` (FastAPI default for `Header() = None`), but `backend/app/deps.py:75-76` returns 400 when absent. The SPA is unaffected (custom instance always injects it, `frontend/src/lib/api-client.ts:83`), but any third-party consumer gets a false picture. Fix: make the header a required `Header(...)` dependency or document the behavior.

### Checked and clean

Contract pipeline end-to-end (backend → `shared/openapi.json` → Orval client) verifiably synchronized at this commit — both regeneration diffs empty; the recent auto-tag change (`tag_number` optional, commit `eded54c`) propagated correctly through schema → openapi → generated model → form. Hand-written fetch inventory: only the auth flows; no `as any`/`as unknown`/`@ts-ignore` outside generated/tests. Error shapes: every backend error is `{detail: string}` or the sanitized 422 list — exactly the shapes `extractDetail` handles. Status-code assumptions: all `status === 200` guards correct; the envelope preserves real statuses. Refresh dedup/`NO_REFRESH_PATHS` match the actual auth routes. Zod/backend parity verified for ~15 additional field groups (passwords, role name/description, task title, kid tag, supplier, avg_age_months, animal tag/name/breed/seller, health product/disease/dose/vet, BCS).

### Lens health summary

Contract health is excellent: the pipeline is verifiably synchronized, the strict TS build passes, no stale hooks/phantom fields/mismatched error parsing. The only drift is peripheral (looser client zod limits, hand-rolled auth fetches outside the type-checked contract). Nothing found would produce a runtime break today.

---

## Lens 9 — Simulation Module

### Findings

**HIGH 9-1. Schema-valid `max_doe_age_months` in [24, 35] crashes every run with ZeroDivisionError → HTTP 500.** `backend/app/simulation/engine.py:245-247` computes the foundation-doe age spread as `span_lo, span_hi = 24, min(60, cull.max_doe_age_months - 12)`; with `max_doe_age_months < 36`, `span_hi < span_lo`, `slots` is empty, and `float(a.herd.does) / len(slots)` divides by zero. The schema floor is `ge=24` (`assumptions.py:130`), so `POST /api/simulation/run`, scenario run, and compare all 500 on a perfectly valid payload — bypassing the `_finite_payload` 422 defense (which catches non-finite *outputs*, not exceptions). Reproduced for ages 24, 30, 35. Fix: raise the floor to `ge=36`, or guard `slots` with a fallback (`slots = slots or [span_lo]`).

**HIGH 9-2. `age_at_first_breeding_months` (≤30) can exceed `max_doe_age_months` (≥24): IndexError → HTTP 500.** `doe_ages` has `max_doe_age_months + 1` slots (`engine.py:243`), but `doe_ages[afb] += n` is written at `engine.py:292` (event doe purchase), `:315` (female-grower purchase when afb==6), and `:406` (retained growers); with `afb=30, max_doe_age=24`, any doe entering the pool indexes out of range. Both fields validate individually; the cross-field invariant is never checked. Reproduced via event purchase and via home-grown retained growers. Fix: `model_validator` on `SimulationAssumptions` requiring `reproduction.age_at_first_breeding_months <= culling.max_doe_age_months`.

**MEDIUM 9-3. Worst-case run costs ~12.3 s of CPU with no rate limit, no timeout, and an understated docstring.** Measured: 240-month horizon + `monte_carlo_runs=2000` + sensitivity + break-even ≈ **12.3 s** single-threaded (default ~0.5 s). Any worker holding only `simulation.view` can fire these repeatedly (`api/simulation.py:177-180`, `:303-315`). Runs are threadpool-offloaded (good — the event loop and DB stay responsive), but nothing throttles per-user/per-farm concurrency, so sustained requests hold a CPU core at 100%. The router docstring (`api/simulation.py:8-9`) claims worst case "in the low seconds" — off by ~an order of magnitude at combined maxima. Fix: cap combined work (scale down `monte_carlo_runs` at large horizons), add a per-farm concurrency limiter (one in-flight run per farm), or correct the docstring and accept the bound.

**MEDIUM 9-4. Conception completely ignores buck presence: zero-buck herds reproduce at full rate, undocumented.** `engine.py:437-440`: `conceived = open_ready * r.conception_rate` — no check that any buck exists; bucks are modeled only as a cost/rotation line (`:495-506`). With `bucks=0, auto_purchase_bucks=False` the herd still kids normally; the golden toy tests (`test_simulation_engine.py:52-68`) actually bake this in (10 does, 0 bucks, births in month 6), and the docstring's "Documented v1 approximations" never mentions it. A user zeroing the buck line silently gets full-fertility results. Fix: gate conception on `bucks > 0` (possibly scaled by `min(1, bucks * buck_doe_ratio / does)`), or document it in the engine docstring and narrative report.

**MEDIUM 9-5. Terminal balloon payment is invisible to DSCR and the annual P&L's debt-service columns.** When the loan outlives the horizon, the closing balance is charged against the final month's `net_cash_flow` (`engine.py:688-693`) but *not* added to `debt_service` (`:690`), so `annual_pl` debt_service/interest/principal and `dscr_per_year` (`:784-791`) exclude it. Verified numerically (24-month horizon, 120-month loan): reported final-year DSCR **1.20**; including the ₹642k balloon it is **0.19** — the exact bank-facing metric the module advertises materially overstates terminal-year coverage. Fix: include the balloon in the final year's `debt_service` (and document), or emit a separate `terminal_debt` column on `AnnualPLRow`/`MonthlyRow`.

**LOW 9-6. Stored scenario JSON is revalidated on every read; one stale/invalid row 500s the entire list endpoint.** `_scenario_out` (`api/simulation.py:50-60`) does `SimulationAssumptions.model_validate(json.loads(...))` with no error handling, and `list_scenarios` (`:213-220`) validates *all* rows eagerly. If the assumptions schema tightens after scenarios are stored (it already has: the magnitude caps in `assumptions.py:19-35` were added in a later audit wave), any previously stored now-invalid scenario turns list/get/run/compare into a ValidationError 500 for that farm. Fix: wrap the revalidation and skip/flag bad rows (or store a schema version and migrate). *(Also noted as 4-L1.)*

**LOW 9-7. Simulation biology diverges from the operational domain rules in ways not all documented.** The docstring's v1-approximations list omits: weaning at month 3 (kid class 0–2 m, `engine.py:378-381`) vs the real system's day 60 (README:133); default meat sale age 12 months (`assumptions.py:172`) vs the farm's 8–9 months (README:100). Defensible planning-model choices, but a user comparing the projection against live records sees systematically later weaning/sales than the app itself enforces. Fix: one line each in the engine docstring's approximation list. *(Weaning half also 3-12.)*

**LOW 9-8. `compare` docstring says "2+ scenarios" but a single id is accepted.** `api/simulation.py:227-238` allows one distinct id; `test_simulation_fuzz.py:336-344` enshrines the 1-id compare. Fix: enforce `len(id_list) >= 2` or fix the docstring.

**LOW 9-9. Global reference endpoints require farm context.** `GET /api/simulation/defaults` and `/defaults/breeds` (`api/simulation.py:127-141`) gate on `require_perm`, which resolves `CurrentFarm` (`deps.py:117-134`), so pure breed reference data needs an `X-Farm-Id` header and farm membership. Harmless but surprising; consider an auth-only dependency for these two.

### Checked and clean

Safety isolation: the engine is pure Python (no FastAPI/SQLAlchemy imports); run/compare/defaults perform zero DB writes; `herd-snapshot` is read-only; scenario CRUD touches only `simulation_scenarios`, farm-scoped with tested cross-farm 404s. Determinism: no unseeded randomness; MC draws from a single seeded `random.Random` in fixed order; reproducibility tested. Input validation: NaN/inf, magnitude caps (₹1e9, 1000 kg, 100k head), list caps (500 events, 1200 weights), `start_year_month` format, risk-spread bracketing, negative-equity and full-moratorium financing all rejected at 422 with a thorough fuzz suite; `_finite_payload` overflow defense test-verified. Financial math: EMI/amortization chaining, NPV/IRR/BCR/payback cross-checked against hand derivations; NPV at returned IRR ≈ 0 and break-even price zeroing NPV proven by tests. Herd mass balance verified across a wide scenario grid. Resource bounds other than 9-3: worst-case payload ~0.3 MB; MC memory ~15 MB; compare capped at 5 ids with dedup.

### Lens health summary

Unusually well-engineered: pure, deterministic, extensively cross-validated engine with genuinely independent audit tests and a mature adversarial-input posture. The one systemic blind spot is **cross-field validation** — two schema-valid combinations crash with raw 500s, slipping past every guard the fuzz suite celebrates, and no test exercises either. Beyond that: an unthrottled 12-second worst-case endpoint, buck-independent conception that silently flatters no-buck scenarios, and a terminal balloon that inflates final-year DSCR from 0.19 to a reported 1.20. Isolation from production data is clean. Fix 9-1/9-2 first (trivial schema validators), then 9-3..9-5.

---

## Lens 10 — Testing Adequacy

### Findings

**HIGH 10-H1. E2E suite is not reproducible and is parallel-unsafe against shared state.** `frontend/e2e/helpers.ts:3-5` — every spec signs in as a pre-existing dev account `demo@goatfarm.in`/`demo1234` owning "Demo Osmanabadi Farm"; no `globalSetup` in `frontend/playwright.config.ts` creates this account/farm, so the suite can't run on a fresh clone or CI — only on the author's migrated dev DB. The config sets no `workers`, so spec files run in parallel, all mutating the same farm, with `retries: 0`. Assertions are count/delta-based: `e2e/feeding-finance.spec.ts:43` (`toHaveCount(logCountBefore + 1)`) and `:74-76` (`toBeCloseTo(before + 321.5)`), while `e2e/purchases.spec.ts:23` concurrently books a ₹24,000 expense into the same farm's totals; `feeding-finance.spec.ts:27-28` even comments that leftover rows from earlier runs forced the weaker count-delta assertion. Impact: flaky, non-portable e2e that can't gate anything. Fix: global setup registering a fresh user+farm per run against a throwaway DB (`GOATFARM_DATABASE_URL` + `alembic upgrade head` in setup), or `workers: 1` + per-spec isolation; replace delta assertions with row-identity assertions.

**HIGH 10-H2. Production startup seeding/backfill paths have zero test coverage.** `backend/app/main.py:36-41` runs `seed_startup` only in the ASGI lifespan; httpx's `ASGITransport` (used by the `client` fixture, `backend/tests/conftest.py:81`) never triggers lifespan. Grep confirms no test references `seed_startup` or `backfill_task_assignments` (`backend/app/seed.py:325-352`). Impact: `backfill_task_assignments` is a data migration applied to every existing farm at startup (assigns preset roles to orphan auto-generated tasks); a regression ships silently to production data. Fix: a test that builds a farm with `auto_generated=True, assigned_role_id=None` tasks, calls `seed_startup(db)` directly, and asserts role assignment + idempotency (or use `asgi-lifespan` to cover the real lifespan).

**MEDIUM 10-M3. No contract-drift guard (no CI at all).** Nothing fails when `shared/openapi.json` or the Orval client goes stale: no test, no `.github/` workflows (directory absent); drift protocol is README-only (`README.md:87-90`). In-memory regeneration diffed byte-identical today — a process gap, not current drift. Fix: a pytest asserting `create_app().openapi() == shared/openapi.json`, and/or a minimal CI job running export + `pnpm orval` + `git diff --exit-code`. *(CI absence also 11-H2, rated HIGH.)*

**MEDIUM 10-M4. Raced-tag test can pass without exercising the race it guards.** `backend/tests/test_concurrency.py:307-309` uses a fixed `asyncio.sleep(0.2)` before committing the holder, while sibling tests use the deterministic `wait_until_blocked()` poll (`test_concurrency.py:59-77`, whose own docstring says a wall-clock sleep can fire before the request reaches its INSERT). Under load, the holder commits first, the request fails at the pre-check with the same 400/message, and the test passes — the raced-INSERT → IntegrityError → 400 translation (B5.1) is never exercised. Fix: `await wait_until_blocked()` before `holder.commit()`.

**MEDIUM 10-M5. Leaked lock-holding sessions can hang the whole suite on failure.** `test_concurrency.py:194-196`, `:295-305`, `:450-456` hold raw sessions with uncommitted row locks outside any try/finally. If the test fails before commit, the session leaks; the autouse teardown's `TRUNCATE … RESTART IDENTITY CASCADE` (`conftest.py:72-76`) needs ACCESS EXCLUSIVE and blocks behind the leaked lock indefinitely — the suite hangs with no timeout. Fix: wrap holder/completer sessions in try/finally with `rollback()`/`close()`, or a disposing fixture.

**MEDIUM 10-M6. `GOATFARM_TEST_DB` drop footgun.** `conftest.py:47` runs `DROP DATABASE IF EXISTS "<GOATFARM_TEST_DB>" WITH (FORCE)` on whatever the env var says; `GOATFARM_TEST_DB=goatfarm` (the dev DB) would be destroyed without any guard. Fix: refuse names not ending in `_test`, or assert the name differs from `GOATFARM_DATABASE_URL`'s database. *(Same as 11-M9.)*

**LOW 10-L7. UTC-midnight flakiness.** No time freezing anywhere (acknowledged at `test_breeding_extended.py:18`); many tests compare server-side `today()` to test-side `today()` (`test_rbac.py:443`, `test_concurrency.py:283`) — a run crossing UTC midnight flakes. Fix: monkeypatch `app.utils.today`/`utcnow` per test, or thread explicit dates end-to-end.

**LOW 10-L8. Fixed-sleep negative assertions (frontend).** `src/app/page.test.tsx:45` and `src/lib/use-permissions.test.tsx:144` sleep 50 ms then assert "nothing happened"; on a loaded machine the forbidden action can fire after the sleep and the test still passes. Prefer waiting on a concrete settled signal.

**LOW 10-L9. Misleading names in the bug-regression suites.** Names describe the old bug while assertions check the fix — `test_auth_bugs.py:91` (`..._returns_500` asserts 401), `test_auth_bugs.py:63` (`..._accepted` asserts rejection, loosely as `in (400, 422)` at `:70`). Rename to the guarded behavior so nobody "fixes" the assertions to match the names.

**LOW 10-L10. Refresh-token test locks in non-revocation.** `test_auth_extended.py:616-626` asserts pre-rotation refresh tokens stay valid (stateless design), while `README.md:50` advertises a "rotating refresh JWT". The test is intentionally documentary, but it enshrines a token-theft window — make sure it's a conscious decision, not an accident. *(Documents the design tension behind T1 / 0-2.)*

**LOW 10-L11. Frontend minor gaps.** No direct tests for `src/components/*` (stat-card, status-badge, data-table-card, empty-state, page-header, theme-toggle, providers) or root `src/app/layout.tsx` (only indirect via page tests); `ALL_PERMISSIONS` in `src/test/msw-server.ts:28` is a hand-maintained mirror of `backend/app/permissions.py:16-44` with no drift check.

### Checked and clean

Assertion-free tests: none (apparent ones delegate to asserting helpers or are must-not-raise Pydantic accepts). Broad excepts: none; the single `except` re-raises. Endpoint coverage: all ~60 routes across all 14 routers exercised; RBAC deny coverage includes a parametrized per-module GET matrix plus per-module write-permission denies. Rate-limit tests correctly re-enabled per test with monkeypatch + limiter clear; X-Forwarded-For spoofing and trusted-proxy cases covered. Conftest isolation: per-test TRUNCATE + RESTART IDENTITY + reseed sound for a serial suite; session-scoped event loop matches the loop-bound asyncpg engine; the Alembic migration itself is under test via subprocess; `second_client()` gives genuinely separate sessions. Determinism: seeded Monte Carlo, deterministic fuzz, no `page.waitForTimeout` in e2e (all expect-polling), MSW `onUnhandledRequest: "error"` so Vitest never touches the network. Bug suites assert fixed behavior with strong status codes (modulo L9 naming). Concurrency suite design: final-state-invariant assertions that hold regardless of race winner; `wait_until_blocked()` is a genuinely deterministic interleave primitive.

### Lens health summary

Backend suite unusually strong (exhaustive endpoint/RBAC/adversarial coverage, real-Postgres concurrency tests with deterministic lock choreography, seeded simulation fuzzing, well-isolated fixtures). Weaknesses at the edges: the startup lifespan/backfill path runs in production but never in tests (10-H2), one concurrency test can silently pass via the wrong path (10-M4), the harness has hang-on-failure and drop-database risks (10-M5/M6), the Playwright suite is the weakest link (10-H1), and with no CI/drift check the contract has no automated enforcement (10-M3). Overall: backend testing A-, e2e/process C+.

---

## Lens 11 — Config, Ops & Deployment Readiness

### Findings

**HIGH 11-H1. No health/liveness/readiness endpoint exists.** `backend/app/main.py:86-98` (router list), `frontend/playwright.config.ts:27`. The only "health" router is the domain health-events module (`/api/health`, auth-protected); Playwright polls `/openapi.json` as a makeshift readiness probe. Impact: no load balancer can health-check, no orchestrator can do rolling deploys or distinguish "process up" from "DB reachable"; deploys are blind. Fix: unauthenticated `/healthz` (process) and `/readyz` (`SELECT 1` against the pool).

**HIGH 11-H2. Zero deployment and CI artifacts.** Verified via `git ls-files`: no Dockerfile, no compose file, no `.github/`/CI config, no deployment docs beyond the dev quickstart (`README.md:14-32`). No documented production run command (workers, proxy, migration step), no automated test/lint gate, no dependency-vulnerability scanning (no pip-audit/Dependabot/npm audit). Impact: every deploy is a manual snowflake; the 2315 backend + 649 frontend tests run only when someone remembers. Fix: minimal Dockerfile per service + CI workflow running pytest/ruff/mypy/vitest + `pip-audit`/`pnpm audit`. *(Contract-drift guard aspect also 10-M3.)*

**HIGH 11-H3. Production-safety depends entirely on the operator remembering env vars; nothing validates them.** `backend/app/core/config.py:47` (`cookie_secure: bool = False`), `config.py:44` (CORS defaults), `backend/app/main.py:71`. No `environment`/`debug` setting and no startup check: forgetting `GOATFARM_COOKIE_SECURE=true` sends the refresh JWT over plain HTTP with no warning; FastAPI's interactive docs and full schema are served unconditionally (`/docs`, `/openapi.json` — no override exists). Impact: silent insecure-prod misconfiguration; schema disclosure. Fix: an `environment` field + startup validator refusing to boot (or loudly warning) when production with `cookie_secure=False` or localhost CORS origins; gate docs URLs on non-prod. *(Subsumes 0-9.)*

**MEDIUM 11-M1. Backend dependencies are not reproducibly locked.** `backend/pyproject.toml:10-21`, `README.md:23`. Direct deps are exact-pinned (good), but there is no lockfile (no `uv.lock`, no hashed requirements); install is `pip install -e '.[dev]'`, so all transitive deps (starlette, anyio, argon2-cffi-bindings, cffi, greenlet, Mako…) float between builds. Fix: adopt `uv lock` or `pip-compile` and commit the lockfile. (Frontend is clean: `packageManager: pnpm@9.15.9` pinned, `pnpm-lock.yaml` committed, `--frozen-lockfile` documented.)

**MEDIUM 11-M2. No DB connection liveness: missing `pool_pre_ping`/`pool_recycle`.** `backend/app/db.py:28-36`. After a Postgres restart/failover or idle-connection reaping, every request hits a dead connection and 500s until the pool drains. Fix: `pool_pre_ping=True` (and consider `pool_recycle`). *(Same as 5-M1 and part of 4-M6.)*

**MEDIUM 11-M3. No TLS on the database connection.** `backend/app/db.py:33-35` — `connect_args` sets only `statement_timeout`; asyncpg's `ssl` parameter is never configured, so a remote DB's wire (including credentials) is plaintext. Fix: a `db_sslmode` setting mapped to asyncpg's `ssl` arg.

**MEDIUM 11-M4. No application logging, no request IDs, no error tracking.** Zero `import logging`/`getLogger` in `backend/app/`; no Sentry/similar in `pyproject.toml:10-30`; only uvicorn's default access log. Failed logins, rate-limit trips, 500 tracebacks, and RBAC denials leave no audit trail; no way to correlate a user report with a request. Fix: structured (JSON) logging at startup, request-ID middleware, auth/security event logging; optional error tracker. *(Same as 4-H2, which rates it HIGH.)*

**MEDIUM 11-M5. No graceful-shutdown handling.** `backend/app/main.py:36-42` — the lifespan never disposes the engine after `yield`; pooled connections are dropped on SIGTERM rather than drained, and `seed_startup` runs on every boot (every replica writes reference data). Fix: `await get_engine().dispose()` on shutdown. *(Engine-dispose half also in 4-M6.)*

**MEDIUM 11-M6. Rate limiting is per-process in-memory and covers only login/register.** `backend/app/ratelimit.py:1-7`, `backend/app/core/config.py:51-56`. The docstring states "single-process deployment" by design, but nothing enforces it (no deploy docs, 11-H2); with `--workers N` or replicas the effective limit multiplies by N. No other endpoint (register-adjacent, refresh, expensive reports) is throttled. Fix: document/enforce single-worker, or move to a shared backend (Redis). *(Same as 0-8, which rates it LOW.)*

**MEDIUM 11-M7. Migrations are not zero-downtime-safe.** `backend/alembic/versions/d8f2b6a41e90_concurrency_indexes.py:80-121`, `backend/alembic/env.py:31-34`. All indexes use plain `op.create_index` (no `postgresql_concurrently=True`), blocking writes for the build duration, and `env.py` runs everything in one transaction — which also *prevents* CONCURRENTLY without per-revision `transactional_ddl = False`. No `lock_timeout` is set, so a migration can queue behind (or block) live traffic. Fine at current scale; unsafe as tables grow. Fix: mark index revisions non-transactional + concurrent; set `lock_timeout` in `env.py`.

**MEDIUM 11-M8. JWT key story is dev-grade: no rotation, no `kid`, cache never invalidates.** `backend/app/security.py:98-161`. Keys are read once into `_key_cache` and never re-read; tokens carry no `kid` header; no overlapping-key verification — rotating keys requires a full restart and instantly invalidates every session. Generation itself is solid (atomic writes, flock, 0600 — `security.py:102-148`). Fix: support a key set (current + previous) keyed by `kid`, reload on restart, document a rotation procedure.

**MEDIUM 11-M9. Test suite can be pointed at the production database.** `backend/tests/conftest.py:18-22` — `GOATFARM_TEST_DB` honored unchecked; the suite truncates all tables per test, so `GOATFARM_TEST_DB=goatfarm pytest` would wipe production data. Fix: hard-fail unless the DB name ends in `_test`. *(Same as 10-M6.)*

**LOW 11-L1. No `.env.example` exists anywhere; `env_file=".env"` is CWD-relative.** `backend/app/core/config.py:13`; verified absent via `git ls-files`. Launching uvicorn/alembic from the repo root instead of `backend/` silently ignores the `.env` file (pydantic-settings doesn't error on missing env files); the ~20 `GOATFARM_*` vars are documented only in README prose. Fix: commit a `.env.example`; resolve the env file relative to `BACKEND_DIR`. *(CWD-relative half also 4-L9.)*

**LOW 11-L2. Settings have no validators.** `backend/app/core/config.py:12-65` — no bounds on TTLs, pool sizes, or Argon2 params; `jwt_algorithm` is env-overridable (a typo'd algorithm fails only at first token issue). Fix: `Field(ge=…)` constraints and a `Literal["RS256"]`-style restriction.

**LOW 11-L3. CORS methods/headers are wildcarded with credentials.** `backend/app/main.py:73-79` — `allow_methods=["*"]`, `allow_headers=["*"]` with `allow_credentials=True`. Origins are pinned so impact is limited, but the preflight surface is broader than the API needs. Fix: enumerate the actual methods/headers (`Authorization`, `X-Farm-Id`, `Content-Type`).

**LOW 11-L4. CSP explicitly deferred, and the deferral is permanent.** `frontend/next.config.ts:1-9` — the comment says CSP is "deferred to a production-only tightening pass", but the headers block is static — no prod-conditional path, so production ships without CSP. Other baseline headers (X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy) present and good. *(Same as 6-2, which rates it MEDIUM.)*

**LOW 11-L5. Stateless refresh tokens: no revocation or reuse detection.** `backend/app/api/auth.py:164-171` — logout only deletes the cookie; a stolen refresh JWT stays valid up to 14 days (`config.py:34`); rotation has no reuse detection. Overlaps the auth lens but is an ops-relevant incident-response gap: no way to forcibly sign a user out. *(Same as 0-1/0-2, 1-1, 6-1 — rated HIGH by lenses 0 and 6, MEDIUM by lens 1.)*

**LOW 11-L6. Hardcoded e2e credentials.** `frontend/e2e/helpers.ts:5` (`DEV_PASSWORD = "demo1234"`). Test-only against the local stack; acceptable, but this account must never exist in a deployed environment.

### Checked and clean

Secrets hygiene: no committed secrets, `.env`, PEMs, or credentials in the tree *or in git history* (`git log --diff-filter=A`); `.gitignore` coverage complete at root and frontend; on-disk private key mode 0600. Key generation: atomic temp-file + `os.replace`, perms before rename, cross-process flock. Proxy trust defaults to empty — spoofed X-Forwarded-For cannot steer the rate limiter. Rate limiter leak-proof (pruned empty deques). DB guardrails: per-statement timeout via asyncpg server_settings; pool bounds configurable. Error surface: 422 handler sanitizes non-finite floats/bytes; unhandled exceptions use FastAPI's opaque default 500 (no stack-trace leak). Argon2id parameters strong and env-protected; legacy pbkdf2 upgrade path verified. Refresh cookie httpOnly, SameSite=Lax, path-scoped. Frontend deps: lockfile committed, package manager pinned. Alembic: URL from settings (not hardcoded), linear single-head chain, documented dedupe SQL in the risky unique-index migration. Seed data: no default admin user or credentials. Dependency pins are recent-looking but CVE status could not be verified offline — the absence of automated scanning (11-H2) is the actionable gap.

### Lens health summary

For dev/local purposes the configuration posture is unusually thoughtful (clean secrets, race-safe key handling, empty-default proxy trust, leak-proof limiter). But the project is **not deployment-ready**: no health endpoint, no Docker/CI, no logging/request-ID/error-tracking, no graceful shutdown, and no guardrail against booting production insecurely or running the truncating test suite against the production DB. Backend transitive deps unpinned; migrations use blocking index builds. Fixes are mostly additive — a focused hardening pass, not a redesign.

---

## Prioritized Remediation Roadmap

### Phase 1 — Immediate / Critical (correctness & security holes with live impact)

1. **Refresh-token revocation model** (T1; 0-1, 0-2, 1-1, 6-1, 11-L5): server-side refresh-session table with jti consumption, family-wide reuse revocation, revoke-on-reset/logout; rate-limit `/refresh`. One architectural change closes the top finding of four lenses.
2. **Lock the pregnancy-lifecycle state machine** (T2, T6, T7; 2-1..2-3, 4-H1): `with_for_update()` in `_get_breeding_record` for ultrasound + abort, `for_update=True` + status re-check in `mark_aborted`'s task skip, row-lock the breeding record in kidding. Extend `test_concurrency.py` to cover ultrasound/abort/kidding-vs-abort races (the existing suite has exactly these gaps).
3. **Simulation cross-field validators** (T3; 9-1, 9-2): `ge=36` floor (or `slots` fallback) + `afb <= max_doe_age_months` model validator; add regression tests for both corners.
4. **Deworming template matching** (T4; 3-1): match by `event.type == DEWORMING` / accept "deworm"; prefill the health form from the linked task.
5. **Minimal ops floor** (T5, T8; 11-H1, 11-H3, 4-H2/11-M4): `/healthz` + `/readyz`; `environment` startup validator (refuse prod with `cookie_secure=False`/localhost CORS; gate `/docs`); basic structured logging + exception handler. 
6. **Test-harness safety guards** (10-M6/11-M9, 10-M5): hard-fail `GOATFARM_TEST_DB` unless it ends in `_test`; try/finally around lock-holding test sessions.

### Phase 2 — Short-term (next few weeks)

1. **CI + reproducibility** (11-H2, 10-M3, 10-H1): minimal CI running pytest/ruff/mypy/vitest + openapi-drift check (`create_app().openapi() == shared/openapi.json`) + `pip-audit`/`pnpm audit`; Dockerfiles; e2e global setup with fresh user+farm per run (or `workers: 1`) and row-identity assertions.
2. **Read-side performance** (T9; 5-H1, 5-H2, 5-M2, 5-M3, 5-M5): remove mapper-level `lazy="selectin"` on Animal history, per-endpoint `selectinload`; push reports/finance/verification aggregates into SQL; cap/paginate breeding history; add `ix_tasks_purchase_batch_id` (+ `farms.owner_id` index).
3. **Connection resilience** (5-M1/11-M2/4-M6, 11-M5, 11-M3): `pool_pre_ping=True` + `pool_recycle`; dispose engine in lifespan teardown; `db_sslmode` setting.
4. **Remaining concurrency MEDIUMs** (2-4, 2-5): canonical lock order for task-completion vs status-change; ON CONFLICT for the feed-setting upsert.
5. **Pregnancy lifecycle edge cases** (3-2, 3-3, 3-4): auto-resolve pregnancies on doe status change; accept PREGNANCY_EARLY in the DELIVERY-move task; reject ultrasound for non-ACTIVE does.
6. **Simulation economics** (9-3, 9-4, 9-5): per-farm run concurrency limit or combined-work cap; gate conception on buck presence (or document); include the terminal balloon in debt_service/DSCR.
7. **Login brute-force keying** (0-3): add per-email and per-IP counters.
8. **Frontend correctness quick wins** (7-1, 7-2/6-4/8-3, 7-3, 7-4): honor `?tab=` deep-links; surface 429/4xx details on auth pages; fix the feeding "done" badge grouping; hide Verify for self-completions.

### Phase 3 — Medium-term (hardening & structural debt)

1. **Account/session policy** (0-6, 0-10, 1-3, 1-4): self-service change-password with session revocation; `users.is_active`; restrict peer-manager actions to the owner; invite-accept flow with generic enrollment refusals.
2. **CSP + header tightening** (6-2/11-L4, 11-L3): production CSP (report-only first) + HSTS; enumerate CORS methods/headers.
3. **Zero-downtime migrations + key rotation** (11-M7, 11-M8): concurrent index builds, `lock_timeout`, non-transactional index revisions; `kid`-keyed JWT key set with documented rotation.
4. **Backend dependency lockfile** (11-M1): `uv lock`/`pip-compile`, committed.
5. **Structural decomposition** (4-M1, 4-M3, 4-M4, 4-M5): split `services.py`/`models.py` per domain; extract `api/_shared.py`; derive schema Literals from enums (or parity test); move router-embedded business logic to services.
6. **Dead code & docs cleanup** (4-M2/5-L4, 4-L8, 11-L1): delete v1 leftovers (or move formatters frontend-side); fix stale README/models/services comments; commit `.env.example`, resolve env file relative to `BACKEND_DIR`.
7. **Enumeration oracles & misc lows** (0-4/1-5, 0-5, 0-7, 8-1, 8-4, 7-5, 7-9): uniform 404 for unknown/forbidden farms; register timing equalization; legacy-hash timing oracle; align client zod caps with backend `max_length`; make `x-farm-id` required in the spec; UTC-date comparisons in the frontend; label-association a11y pass.
8. **Test quality polish** (10-M4, 10-H2, 10-L7..L11): `wait_until_blocked()` in the raced-tag test; direct `seed_startup` coverage; time freezing; rename misleading bug-suite tests; component-level frontend tests.

---

*Consolidated from 12 lens reports. Severity disagreements between lenses on the same issue are preserved inline (see T1, T8, 6-2/11-L4, 0-8/11-M6, 10-M3/11-H2) — the roadmap phases take the higher severity in each case.*
