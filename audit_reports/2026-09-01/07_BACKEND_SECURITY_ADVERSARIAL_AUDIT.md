# 07 — Backend Security Adversarial Audit (Goat Farm / Herdly SaaS API)

- **Date:** 2026-09-01
- **Auditor:** Red-team unit A7 (senior application-security review)
- **Target:** `/Users/pavankumarreddyreddem/Desktop/goat_saas/backend` — FastAPI, async SQLAlchemy 2.0 + asyncpg + PostgreSQL, Alembic, Argon2id, RS256 JWT with server-side refresh sessions, multi-farm multi-species (goat + buffalo dairy) tenancy
- **Tree audited:** commit `69a814d` ("Fix all independent-audit findings, incl. buffalo calving blocker"), working tree clean except `audit_reports/`
- **Personas attacked as:** unauthenticated external attacker, malicious authenticated multi-tenant user (farm-A worker vs. farm B, delegated team manager, privileged role holders), insider worker account, and a DB/backup reader (offline attack on persisted material)

---

## 1. Method

1. Read every router in `backend/app/api/` (`auth`, `animals`, `breeding`, `buckets`, `dashboard`, `feeding`, `finance`, `health`, `kidding`, `milk`, `purchases`, `simulation`, `tasks`, `team`, `_shared`) end-to-end or via targeted full-route reads; every object-lookup helper (`_get_animal`, `_get_task`, `_get_breeding_record`, `_get_scenario`, `_get_membership`, `_get_role`, `_resolve_related_animal`, `_bulk_target_snapshot`) was traced to its SQL predicate.
2. Read the security core: `deps.py`, `security.py`, `permissions.py`, `ratelimit.py`, `core/config.py`, `main.py` (middleware order), `db.py`, `seed.py`, `models/*.py` constraints, `services/idempotency.py`, `schemas/common.py` plus the module schemas.
3. Grepped for raw SQL (`text(`, f-string SQL, `.format`), client-controlled `order_by`, LIKE patterns, file/paths in responses, PII in logs — none exploitable (see §5).
4. Verified git history for secret leakage (`git log --all`, `git rev-list --objects`) and inspected `docker-compose.yml`, `Dockerfile`, `.env.example`, nginx edge config, alembic migrations (46; latest `f8a2c4e6b1d9`).
5. Ran the security test corpus against a real PostgreSQL with a collision-free database name (`GOATFARM_TEST_DB=goatfarm_test_a7audit*`) — **617+ tests, all green** (details §6). An initial run against the shared default `goatfarm_test` produced mass failures that were traced to a *concurrent sibling audit process* dropping/recreating the same database mid-run (observed live in `ps`), not to application code; reruns with a unique DB name pass 100%.

---

## 2. Verdict

**CRITICAL: 0 · HIGH: 0 · MEDIUM: 1 · LOW: 7.**

This backend has visibly absorbed multiple prior adversarial audit rounds (code comments cite them; the suite contains `test_adversarial.py`, `test_security_hardening.py`, `test_rbac_exhaustive.py`, `test_auth_token_version_races.py`, `test_tenant_foreign_keys.py`, ...). The tenancy model is enforced at **three independent layers** — scoped SQL predicates, an RBAC dependency on every route, and composite tenant foreign keys in the database itself — and I could not construct a working IDOR, privilege-escalation, race, or injection chain. The findings below are real but bounded: one permission-matrix inconsistency (M-1) and hygiene/deployment-hygiene items (L-1..L-7).

---

## 3. Findings

### M-1 — MEDIUM — Herd-composition disclosure under `simulation.view` alone (`GET /api/simulation/herd-snapshot`)

- **Where:** `backend/app/api/simulation.py:646-707` vs. the contrasting design at `simulation.py:710-731`.
- **Vulnerability:** `herd-snapshot` queries the farm's `Animal` table and returns nine per-sex, per-age-cohort counts plus `total_head`, gated only by `require_perm("simulation.view")`. The sibling endpoint `GET /api/simulation/calibration` reads the same animal data and — per its own docstring/dependency list — requires `animals.view`, `breeding.view`, `kidding.view`, `feeding.view` and `finance.view` **in addition to** `simulation.view`. `herd-snapshot` requires none of those module permissions.
- **Attack scenario:** A farm owner grants a custom consultant role `simulation.view` (permissible: `_clean_permissions` accepts any subset and `PERMISSION_DEPENDENCIES` places no dependency on `simulation.view`, `permissions.py:62-81`). The consultant — explicitly *not* entitled to the animal register — polls `herd-snapshot` daily and reconstructs herd size, age structure, sex split, and turnover (buys/sales/deaths) from the cohort deltas. On a buffalo dairy this also reveals lactation-herd scale, commercially sensitive information.
- **Evidence:** `simulation.py:677-706` builds `cohort_count(...)` predicates over `Animal` with only `Animal.farm_id == farm.id, Animal.status == ACTIVE`; route signature `perms: SimView` (line 648) is the sole authorization. Compare `farm_calibration` (lines 711-719) demanding `AnimalsView, BreedingView, KiddingView, FeedingView, FinanceView` for the same-table reads.
- **Impact:** Cross-permission-boundary information disclosure (aggregates only — no tags, prices, or health data leave the farm, and the caller is already an authenticated member of the tenant).
- **Fix:** Add `AnimalsView` (and arguably `FeedingView` if cohort feeding weights ever appear) to `herd-snapshot`, mirroring `calibration`. Add an RBAC-matrix test asserting every simulation endpoint that reads tenant tables requires the corresponding module view permission (`test_dashboard_permissions.py` already models this pattern).

### L-1 — LOW — Account-enumeration oracle on `POST /api/auth/register`

- **Where:** `backend/app/api/auth.py:83, 688-691` (constant `ALREADY_REGISTERED`).
- **Vulnerability:** Register answers `400 "That email is already registered."` for an existing email versus `201` for a fresh one. The login path, by contrast, is fully equalized (dummy Argon2 hash + PBKDF2 padding, `security.py:153-240`). The register path hashes *before* the existence check to kill the timing half, and the per-IP register throttle (10/5 min, `auth.py:676-679`) blunts scripting — but the *response* remains a perfect existence oracle for anyone rotating source IPs.
- **Attack scenario:** An attacker with a modest botnet enumerates which emails hold accounts (corporate reconnaissance, targeted phishing on known-active identities) at a rate limited only by distinct IPs.
- **Evidence:** `auth.py:689-691`; the code comment explicitly acknowledges the trade-off ("without email verification there is no accept-and-notify path").
- **Fix:** Once an email-sending capability exists, switch to accept-and-notify. Near-term options: raise the register-only per-IP ceiling cost (e.g., also charge the per-email bucket), or return 201 with a no-op/no-login response for duplicates — accepting the UX cost knowingly. Severity stays LOW because login itself gives nothing away and the direct impact is enumeration only.

### L-2 — LOW — Docker Compose ships default credentials / dev secrets in environment defaults

- **Where:** `docker-compose.yml` (`POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-goatfarm}`, `GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET: ${...:-development-only-idempotency-hmac-secret-change-me}`), `.env.example` (`goatfarm/goatfarm`).
- **Vulnerability:** A copy-paste Compose deployment runs Postgres with password `goatfarm` and the known development idempotency HMAC secret. Mitigations already present: `db` is not published (only the loopback-bound edge is), the API/migration URLs are `:?`-required (no default), and production boot refuses the known HMAC secret, HTTP cookies, loopback CORS/hosts, and non-`verify-full` DB TLS (`config.py:427-572`). The residual risk is an operator who stays on `GOATFARM_ENVIRONMENT=development` in a reachable network.
- **Fix:** Drop the `:-goatfarm` fallback (use `:?`) for `POSTGRES_PASSWORD` so Compose refuses to start without a real secret; consider the same for the HMAC secret outside `environment=development`.

### L-3 — LOW — Security controls that are valid only under `--workers 1` have no runtime guard

- **Where:** `backend/app/ratelimit.py:1-9` (documented), `backend/app/api/simulation.py:490-529` (`_run_budget`, `_farm_run_locks`), `backend/app/api/team.py:96-133` (`_worker_idempotency_gates`), `Dockerfile` CMD (`--workers 1`).
- **Vulnerability:** Auth throttles, the simulation CPU budget, and the in-process worker-idempotency gate are per-process. Anyone launching the app outside the provided Dockerfile (bare `uvicorn --workers 4`, k8s replicas) silently multiplies every per-IP/per-owner budget by N and drops the farm serialization for same-key retries. PostgreSQL idempotency (unique index + `ON CONFLICT`) and all DB-level ceilings remain correct, so this is an availability/brute-force-budget issue, not integrity.
- **Fix:** Assert the deployment shape at boot (e.g., refuse `environment=production` when `len(os.sched_getaffinity)` indicates a multi-process setup is impractical to detect — at minimum, a startup log/health flag `workers=1` plus documentation, or move the limiter to Redis when scaling ever becomes a requirement).

### L-4 — LOW — RS256 private key lives inside the repository working tree

- **Where:** `backend/keys/jwt_private.pem` (0600, dir 0700).
- **Vulnerability:** **Not** committed to git — verified `git check-ignore` matches, `git log --all -- backend/keys/` is empty, and `git rev-list --all --objects` contains no `keys/` path; `security.py:427-502` generates dev keys atomically under an flock and production *refuses to boot* without externally mounted key files (`security.py:434-439`, `config.py` key path settings). The residual exposure is non-git leakage: directory zips, cloud-synced folders, or ad-hoc `cp -r` backups of the working tree would carry the live signing key for this dev instance.
- **Attack scenario:** An insider or a misconfigured backup job exfiltrates `backend/`; the attacker forges `access` and `refresh` JWTs (refresh jti rows would still gate refresh rotation — but forged *access* tokens work for their 30-minute TTL with a synthetic `ver` matching the victim's `token_version`, readable from any leaked DB copy).
- **Fix:** Keep dev keys out of the repo directory entirely (e.g., `~/.cache/goatfarm/keys` or `$XDG_DATA_HOME`), leaving `backend/keys/` for explicitly configured production mounts.

### L-5 — LOW — No first-login forced rotation for owner-provisioned worker passwords

- **Where:** `backend/app/api/team.py:748-913` (`create_worker`), `team.py:1041-1094` (`reset_password`).
- **Vulnerability:** The owner chooses the worker's initial password (and can reset it later — deliberately, and only for accounts this farm provisioned with no other affiliations, `team.py:354-394`). Nothing forces the worker to change it, so the owner permanently knows (or can re-acquire) the credential. This is a documented product decision (the generic refusal message hides exactly the wrong things otherwise), and the blast radius is limited to farm-provisioned single-tenant worker accounts, but it weakens non-repudiation of worker-attributed actions (task completions, health records, finance entries).
- **Fix:** Add a `must_change_password` flag on the provisioned User; block domain mutations (or just warn in the UI) until the worker rotates. Combine with the existing attribution audit trail.

### L-6 — LOW — Persistent online-guessing budget against a single account (no escalation beyond throttle)

- **Where:** `backend/app/api/auth.py:87-93, 217-260`.
- **Vulnerability:** Login ceilings: composite (IP,email) 10/5 min, per-email (IP-agnostic) 30/5 min, per-IP 100/5 min. An attacker with many source IPs sustains ~30 password guesses per account per 5 minutes indefinitely — no account lockout, progressive delay, or CAPTCHA escalation exists. With `min_password_length=8` in dev (production gate forces ≥12, `config.py:557-558`), this is a slow-but-unbounded guessing channel.
- **Fix:** Consider alerting owners on sustained per-email throttling, or an exponential per-email backoff after N consecutive windows. Severity LOW: 30/5 min against Argon2id-hashed, ≥12-char (production) passwords is not practically convertible.

### L-7 — LOW — Shared-NAT availability: per-IP invalid-token ceiling can 429 co-located colleagues

- **Where:** `backend/app/deps.py:30, 101-131` (`INVALID_TOKEN_IP_LIMIT_MULTIPLIER = 10`), `auth.py:450-476` (refresh pre-verify).
- **Vulnerability:** The per-IP invalid-token ledger (100 invalid JWTs/5 min) turns *this already-invalid request* into a 429 for its source address. The design carefully avoids pre-classifying valid tokens from a shared IP (the per-token bucket is keyed on the token hash), but a single noisy actor behind one egress (or one compromised browser tab replaying a stale token in a loop) can still saturate the IP bucket such that *their own subsequent* requests 429 — not others' valid ones (valid tokens are never charged). Actual cross-user impact is limited to log noise and the actor's own lockout; the code comments document exactly this trade-off.
- **Fix:** None required; optionally split the IP ceiling per (IP, route) to bound a single tab's retry loop from consuming the shared budget.

---

## 4. Attacks attempted and refuted (with the code that stopped them)

### 4.1 Tenancy / IDOR — every path checked

| Probe | Code that blocks it |
|---|---|
| Farm-A worker `PUT/POST` against farm-B animal/task/breeding/milk/health/finance/scenario ids | `X-Farm-Id` resolves a `Farm` the caller owns **or** holds an active membership for (`deps.py:475-537`); then every lookup helper ANDs `farm_id`: `animals.py:119-144`, `tasks.py:106-124`, `breeding.py:54-82`, `simulation.py:149-166`, `finance.py:666-677`, `team.py:438-497`, `health.py:277-351`, `feeding.py:335-339`, `purchases.py:189-191` |
| Child-resource filter with a cross-farm `animal_id` (e.g., `/api/milk?animal_id=<farm-B-id>`) | Filter id is first resolved *within the farm* (`milk.py:65-72,104-111`; `health.py:_bulk_target_snapshot` lines 443-470; `tasks.py:416-433` assignment locks) — cross-farm id ⇒ 404/400, never a cross-join |
| Sequential farm-id enumeration | Unknown and forbidden farm ids share one 404 (`deps.py:499-503`); tasks likewise (`tasks.py:377-380`) |
| DB-level bypass (direct FK to another tenant's row) | Composite tenant FKs from migration `f7d8c9b0a1e2` (animals→dam/sire/purchase_batch, breeding→doe/buck, health→animal/batch, memberships→role, …) make cross-tenant parent references physically impossible |
| Worker creating tasks that assign another farm's role/user/animal | Assignment re-validated under locks against `farm.id` + active membership + non-deleted user + live role, with role-membership consistency check (`tasks.py:435-513`) |
| Replay of an `Idempotency-Key` across farms/actors/paths | Uniqueness domain is `(farm_id, actor_id, operation, key_digest)` with a NULL-farm partial index for `create-farm` (`models/idempotency.py:56-66`, `services/idempotency.py:299-332`); replay additionally requires a matching request fingerprint over body **and** `path_identity` (e.g., `transaction_id` in `finance.py:745`) — else 409 |

### 4.2 Authentication / session

- **Committed private key (audit premise):** refuted — `backend/keys/` is gitignored (line 25 of `.gitignore`), never appears in any commit or dangling object (see L-4 for the residual on-disk note).
- **Alg confusion / `kid` abuse:** decode requires `header.alg == "RS256"` (a `Literal` setting), `algorithms=["RS256"]` is pinned in `jwt.decode`, `kid` is a dict lookup against a validated keyring only — never a path/URL (`security.py:717-741`).
- **Token-kind confusion:** `kind` claim checked against `access`/`refresh` per use (`security.py:767`), `sub` must be canonical decimal ≤ 20 digits, `ver` mandatory int (`security.py:812-833`).
- **Refresh theft/replay:** server-side `RefreshSession` per jti, `FOR UPDATE` consumption lock, family revocation on reuse, 3-second symmetric grace for concurrent tabs, successor `replacement_jti` idempotency, family/session caps (10/1024) with deterministic eviction (`auth.py:479-563, 832-963`). Replay of a compacted consumed token still revokes the signed family (`auth.py:882-896`).
- **Revocation on logout/password change/worker reset:** `token_version` bump + family/user-wide session revocation under a consistent User→RefreshSession lock order (`auth.py:966-1122, 1125-1222`; `team.py:1070-1093`); logout with mismatched cookie/bearer identities is rejected before any mutation (`auth.py:988-997`).
- **Cookie flags:** HttpOnly + SameSite=Lax always; `Secure` + `__Host-` prefix enforced by production boot gates (`config.py:444-496`); duplicate refresh cookies rejected (`auth.py:421-442`); refresh/logout additionally origin-guarded against cross-site browsers (`auth.py:179-205`).
- **Worker password auth path:** no separate worker login — workers use the same hardened `/login`; owner-side hashing is admission-controlled per owner (`team.py:171-225`) and owner-only (`team.py:254-316`).
- **Enumeration:** login responses generic and timing-equalized (dummy Argon2 + PBKDF2 padding, fixed two-submission rejection profile, `security.py:200-240`); `create_worker` returns one generic message for owner/worker/unaffiliated emails (`team.py:855-860`); password-reset policy reasons deliberately non-revealing (`team.py:354-364`). Register remains the one oracle (L-1).

### 4.3 Authorization matrix

- Every mutation route carries a `require_perm(...)` dependency (route→permission map verified across all 15 routers; RBAC pinned by `test_rbac_exhaustive.py`, 500+ assertions, green).
- **Privilege escalation blocked:** self role-change/self-deactivate 400s (`team.py:953-954, 1019-1020`); peer-manager protection (`team.py:545-557`); only the owner may grant/edit/assign `team.manage` (`team.py:560-591`); delegated managers may only operate within `role.permission_set().issubset(caller perms)` for both target and source roles (`team.py:573-585`); permission edits intersect with the editor's own set and preserve the rest (`team.py:664-677`). `Role.code` vocabulary is DB-constrained (`models/core.py:113-135`) and never accepted from payloads.
- **Verification bypass (`VERIFICATION_REQUIRED_CATEGORIES`):** only CLEANING (`models/constants.py:68`); two-person rule enforced server-side — completer cannot verify own work unless owner (`tasks.py:698-701`); form-linked duties refuse bare completion (`tasks.py:585-586`); `needs_verification` is a SQL clause, not client-controlled.

### 4.4 Injection

- No raw SQL beyond constant `SELECT 1` / fixed advisory-lock literals; all `text()` hits are static index/constraint DDL (`grep` across `app/`, §1).
- LIKE wildcards escaped with explicit `escape="\\"` in every search (`animals.py:283-293`, `purchases.py:83-91`, breeding candidates); no client-controlled `order_by` column names anywhere (orderings are literal columns).
- No file endpoints, no CSV/Excel export (account export is JSON with an integer-derived filename, `auth.py:1278-1280` — no formula-injection surface, no header injection).
- Timezone strings validated through `ZoneInfo` + placeholder-zone denylist (`schemas/auth.py:136-154`); `today()` additionally falls back safely (`utils.py:43-55`).
- Strict inputs: `extra="forbid"` bodies, strict int/float/bool (no `"12"`/`true` coercion), finite-float guards, control-character + surrogate rejection, and domain caps (₹1e9, 1e6 kg, 1000 kg weight, litre ≤ 100, fat 3-12) — `schemas/common.py:29-154`.

### 4.5 Races and capacity invariants

- `MAX_FARMS_PER_USER`: User row `FOR UPDATE` before count+insert inside the idempotent mutation (`auth.py:1462-1499`).
- `MAX_TEAM_MEMBERS_PER_FARM` / `MAX_ROLES_PER_FARM`: per-farm `pg_advisory_xact_lock` + Farm `FOR KEY SHARE` then COUNT inside the critical section (`team.py:605-661`), with the lock-order rationale documented against FK key-share deadlocks.
- `MAX_PENDING_MANUAL_TASKS_PER_FARM`: Farm-lock-guarded count/create for create, verify-spawn and reject (`tasks.py:68-84, 536-552, 715-723, 745-767`).
- Concurrent idempotent POSTs: PostgreSQL unique index arbitrates via `ON CONFLICT DO NOTHING` + re-read with expiry re-check (`services/idempotency.py:298-374`); concurrent refresh rotation: jti row `FOR UPDATE` (`auth.py:866-874`); concurrent sales/status flips: animal row `FOR UPDATE` + state re-check (`animals.py:130-137, 950-958`); role edits: optimistic `revision` + row locks (`team.py:1164-1194`).
- Lock-order discipline is explicit and tested (`test_lock_order_hardening.py`): Farm → Animal → Membership → User → Role with `populate_existing` reloads after every wait.

### 4.6 Information security / ops

- Opaque 500s with no internals (`main.py:458-489`); 422s strip Pydantic's `input` echo (`main.py:421-437`); production disables `/docs`, `/redoc`, `/openapi.json` (`main.py:527-534`); `/healthz` dependency-free, `/readyz` returns only ok/unavailable.
- CORS: exact origins only, canonicalized and production-gated to non-loopback HTTPS (`config.py:338-388, 497-534`); TrustedHost with grammar-validated patterns (`config.py:259-328`); `Vary: Origin` on 500s reproduces CORS headers safely.
- Proxy trust: `trusted_proxy_hosts` validated to fail closed (`config.py:390-425`), uvicorn's `ProxyHeadersMiddleware` walks the XFF chain **right-to-left skipping trusted hosts** (verified in vendored source) — client-supplied spoofed entries cannot steer rate limiting through the compose edge, and nginx appends its observed `$remote_addr`.
- Request-body and request-target limits enforced pre-parse including chunked bodies (`main.py:69-135`), mirrored by `client_max_body_size 1m` at the edge; 414 handling present.
- Logs: request-id correlation with forged-input validation (`[A-Za-z0-9_-]{1,64}`), no emails/passwords/tokens logged (grep §1); task assignee labels deliberately de-identify emails (`_shared.py:264-277`).
- Argon2id: t=3, m=64 MiB, p=4 defaults; production floor t≥2, m≥19456 KiB (OWASP+), key size ≥2048 enforced, bounded off-loop worker pool with non-queuing admission (`security.py:69-132`).
- Rate-limiter memory: bounded key cardinality (50k) with LRU eviction tiers protecting live brute-force buckets; pure probes allocate nothing (`ratelimit.py:55-113`).
- Account deletion: password-confirmed, farm-ownership-guarded, scrubbed email/name/hash + DB CHECK `ck_users_deleted_profile_scrubbed`, `token_version` bump, synchronous session deletion, async membership deactivation via SKIP LOCKED batches (`auth.py:1314-1418`, `models/core.py:36-39`, `deps.py:245-298`).
- Seed/startup: fixed global reference data only, advisory-locked idempotent upserts; production key validation and dummy-hash priming precede serving (`main.py:313-340`); migrations run as a separately configured one-shot job with its own least-privilege settings projection and `verify-full` TLS gate (`config.py:30-61`).

---

## 5. DEFENSES THAT HELD (summary)

1. **Three-layer tenancy** — farm-scoped SQL on every lookup, `require_perm` on every route, composite tenant FKs in the database.
2. **RS256 done right** — pinned algorithm, keyring `kid` selection, issuer/audience binding, mandatory `ver`, canonical `sub`, no committed keys, fail-closed production key validation.
3. **Refresh-token rotation with reuse detection** — jti sessions, family revocation, concurrency-safe 3s grace, bounded families/sessions, lock-ordered revocation on logout/change/reset.
4. **Timing-equalized login** — dummy Argon2 hash, PBKDF2 rejection padding, fixed submission counts, cancellation accounted.
5. **Owner-only, scope-bounded team administration** — no self-promotion, no peer-manager attacks, no `team.manage` delegation by non-owners, no cross-permission role edits, no silent enrollment of foreign identities, no password takeover of multi-farm/owner accounts.
6. **DB-arbitrated idempotency** — tenant/actor/operation/path-scoped claims, HMAC-protected password fingerprints (not offline-attackable by a backup reader), atomic claim+response commits, bounded retention purges.
7. **Race-hardened capacity ceilings** — advisory-lock + row-lock serialization for farms-per-user, team size, roles, manual-task queue, scenario quota.
8. **Fail-closed production boot validation** — cookie/CORS/hosts/DB-TLS/password-policy/Argon2/HMAC secrets all refuse unsafe production config.
9. **Injection-proof query layer** — ORM-only with escaped LIKE, no dynamic ordering, no raw interpolation anywhere.
10. **Ops hygiene** — opaque errors, no PII in logs, body/target limits, nosniff/frame/referrer headers, `Cache-Control: no-store` on `/api/*`, non-root container, `--workers 1` + `--no-proxy-headers` pinned in the image.

---

## 6. Test evidence (executed during this audit)

| Suite (real PostgreSQL, unique DB name) | Result |
|---|---|
| `test_security_hardening.py` | **98 passed** |
| `test_adversarial.py` + `test_rbac_exhaustive.py` + `test_idempotency.py` | **104 passed** |
| `test_smoke.py` + `test_auth_bugs.py` + `test_team_bugs.py` + `test_rbac.py` | **57 passed** |
| `test_auth_extended.py` + `test_rbac_exhaustive.py` + `test_security_workflow.py` + `test_auth_token_version_races.py` + `test_team_extended.py` | **560 passed** |
| Direct exploit reproduction scripts (register→farm chain on a fresh DB, XFF direction inspection, idempotency-key cross-farm replay reasoning) | No vulnerability reproduced |

Note on methodology: an initial combined run against the default `goatfarm_test` database produced 168 failures/328 errors that were **caused by a concurrent sibling audit process** executing `DROP DATABASE goatfarm_test WITH (FORCE)` mid-run (process observed via `ps`; failures were phantom "Account no longer exists" 401s from rows vanishing under the app). With a collision-free `GOATFARM_TEST_DB`, every suite is green. No application defect was found in that anomaly.

---

## 7. Prioritized remediation list

1. **M-1:** add `animals.view` (at minimum) to `GET /api/simulation/herd-snapshot`; add a matrix test.
2. **L-2:** remove the `goatfarm` default for `POSTGRES_PASSWORD` in Compose (`:?` instead of `:-`).
3. **L-1:** plan the accept-and-notify register flow once email infra exists; meanwhile consider charging the per-email bucket on register probes.
4. **L-4:** relocate generated dev JWT keys outside the repo tree.
5. **L-5:** `must_change_password` flag for provisioned workers.
6. **L-3 / L-6 / L-7:** document/guard single-process deployment; optional per-email backoff escalation; optional per-route IP budget split.
