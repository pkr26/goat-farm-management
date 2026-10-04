# Track 06 — Independent Security & Privacy Audit

**Commit:** `1ca78768ed227182b1b84663bcc97c5ea9bee41b`  
**Date:** 2026-10-03  
**Scope:** current backend, frontend, production configuration, and directly relevant tests. This was a clean-room review: prior audit reports, improvement/status plans, and mutation-campaign reports were excluded.

## Result

| Severity | Count |
|---|---:|
| Critical | 0 |
| High | 1 |
| Medium | 5 |
| Low | 2 |

The most important issue is not a conventional missing rate-limit check: the code intentionally makes the IP-agnostic worker-PIN limit **soft**. Once the target bucket is full, every new guess from a fresh IP is still Argon-verified and the correct PIN still produces a session. That prevents an attacker from locking out a worker, but it also means the limit does not cap distributed guessing of the permitted six-digit PIN space.

## Method and validation

- Traced authentication, JWT/refresh-session handling, RBAC/tenant dependencies, credential mutation, idempotency, browser storage, offline queues, upload processing, third-party egress, retention, logging, cookies/CORS/CSP, and production configuration.
- Searched tracked source for common secret, injection, unsafe process, raw-SQL, redirect, browser-storage, and script-injection sinks.
- Ran backend security-focused tests: **117 passed** (`test_security_hardening.py`, `test_security_module_gaps.py`, `test_jwt_rotation.py`, `test_metrics.py`).
- Ran the two throttle-behavior tests independently: **2 passed** (`test_correct_pin_still_works_when_the_account_bucket_is_full`, `test_soft_email_lockout_still_admits_correct_password`).
- Ran focused frontend persistence/offline tests: **50 passed** across `idempotent-request.persistence`, `worker-offline-shift`, and the offline worker page.
- Ran the configured backend static/security lint rules: **all checks passed**.
- No application code was changed, and no live external service or production data was touched.

## Findings

### SEC-01 — A rotating-source attacker is not capped when guessing worker PINs

**Severity:** High (conditional on a weak/six-digit PIN and source-IP rotation)  
**Confidence:** High  
**Status:** Reproduced and source-confirmed

**What happens**

The worker login route hard-blocks only `(IP, farm, membership)` and `(IP, farm)` buckets. The IP-agnostic `(farm, membership)` bucket is checked only after an attempted PIN has already been Argon-verified, and only a failed PIN is converted to `429`. A correct PIN continues to token issuance and clears the account bucket.

Evidence:

- `backend/app/api/auth.py:1193-1225` defines the soft membership bucket but excludes it from `_hard_blocked`.
- `backend/app/api/auth.py:1291-1303` performs password verification for every request admitted by the fresh-IP buckets.
- `backend/app/api/auth.py:1314-1328` checks the IP-agnostic bucket only in the failed-PIN path.
- `backend/app/api/auth.py:1408-1418` issues a session for a correct PIN and then resets the membership bucket.
- `backend/tests/test_worker_pin_auth.py:377-395` explicitly proves that a correct PIN succeeds when the account bucket is already full; the focused test passed in this review.
- Production permits six-digit numeric PINs (`backend/app/core/config.py:958-969`, `backend/app/core/config.py:1443-1449`; input shape at `backend/app/schemas/auth.py:133-138`). The UI's generator does produce a substantially safer random 12-digit PIN (`frontend/src/app/(app)/team/page.tsx:86-96`), but users are not required to use it.

**Impact and preconditions**

An attacker needs the farm and membership IDs, a weak PIN, many source IPs, and enough time to consume bounded Argon work. The unauthenticated roster materially lowers target discovery by returning membership IDs and display names (`backend/app/api/auth.py:1068-1170`). A successful guess yields the worker's farm-scoped session and whatever permissions their role grants. The two-thread/non-queuing Argon pool limits concurrency and makes the attack expensive (`backend/app/security.py:74-127`), but it limits service capacity rather than the number of guesses against one membership.

**Recommendation**

After a small durable per-membership failure budget, require a device-bound/bootstrap proof, owner unlock, WebAuthn-style second factor, or a progressively delayed challenge before another PIN verification. Do not solve this with an indefinite hard account lock alone, and do not store a numeric PIN verifier offline. Make the 12-digit generator the enforced default where device-bound proof is unavailable.

### SEC-02 — The password account throttle also shapes responses without capping distributed guesses

**Severity:** Medium  
**Confidence:** High  
**Status:** Reproduced and source-confirmed

The IP-agnostic `login-email` limit is likewise deliberately soft. Once full, wrong passwords receive `429`, but each fresh-IP request still performs verification and a correct password receives `200` (`backend/app/api/auth.py:287-325`, `backend/app/api/auth.py:916-979`). The focused behavior test at `backend/tests/test_redteam_spine_fixes.py:452-486` passed.

This avoids trivial targeted lockout, and the production 12-character minimum plus Argon2 floor materially reduce random online guessing (`backend/app/core/config.py:1608-1615`). It still leaves distributed dictionary/credential-stuffing attempts uncapped per account; a success remains distinguishable and accepted after the advertised account ceiling.

**Recommendation:** apply the same step-up/device-risk control proposed for SEC-01, with breached-password screening and alerting. Preserve an anti-lockout recovery path rather than treating a permanent hard lock as the only alternative.

### SEC-03 — Reset-password requests persist an offline-verifiable password digest in `sessionStorage`

**Severity:** Medium  
**Confidence:** High  
**Status:** Source-confirmed; no exploit harness was added

The protected-mutation allowlist includes `/api/team/workers/{id}/reset-password` (`frontend/src/lib/idempotent-request.ts:221-248`). The persistence guard correctly excludes worker creation and PIN reset, and its own comment explains that a digest of a credential-bearing body is an unacceptable unsalted offline verifier, but it omits reset-password (`frontend/src/lib/idempotent-request.ts:283-303`).

The persisted signature contains the raw JSON body (`frontend/src/lib/idempotent-request.ts:389-410`), is SHA-256 hashed (`frontend/src/lib/idempotent-request.ts:513-519`), and the digest plus idempotency key is written before fetch (`frontend/src/lib/idempotent-request.ts:549-575`) for a two-minute window (`frontend/src/lib/idempotent-request.ts:13-20`). The endpoint body is exactly the new password (`backend/app/schemas/team.py:41-42`; frontend call at `frontend/src/app/(app)/team/page.tsx:943-953`). Existing regression coverage enumerates worker creation and reset-PIN as memory-only but not reset-password (`frontend/src/lib/idempotent-request.persistence.test.ts:391-421`).

**Impact and preconditions**

A malicious extension, local browser-profile reader, or other actor able to copy same-tab session storage during an in-flight/ambiguous request can test password guesses offline when the actor/farm/URL/header context is known or inferable. The record is removed after a successful response, so this is a short and conditional exposure, not durable plaintext storage.

**Recommendation:** exclude `reset-password` from persisted recovery exactly like `reset-pin`, retain only in-memory retry/coalescing, and add a regression asserting that neither credential-reset route writes or recovers a storage record.

### SEC-04 — A cached offline shift is a 12-hour local bearer capability for viewing and recording work

**Severity:** Medium  
**Confidence:** High  
**Status:** Reproduced by the focused frontend tests and source-confirmed

An authenticated board writes worker/farm names and task titles into IndexedDB and a marker into `sessionStorage`; the marker lasts up to 12 hours (`frontend/src/lib/worker-offline-shift.ts:5-15`, `frontend/src/lib/worker-offline-shift.ts:28-66`, `frontend/src/app/worker/page.tsx:150-163`). With no authenticated user, the public offline page loads that snapshot, displays it, and permits Complete/Skip operations without re-entering the worker PIN (`frontend/src/app/worker/offline/page.tsx:25-63`, `frontend/src/app/worker/offline/page.tsx:71-93`). The focused test explicitly clears the access token and still persists a completion (`frontend/src/app/worker/offline/page.test.tsx:46-56`).

The queued action has no immediate API authority, which is a useful boundary. However, when the original actor signs in to the same farm, the worker shell automatically drains pending operations (`frontend/src/lib/worker-outbox.ts:300-372`, `frontend/src/app/worker/layout.tsx:145-161`). The resulting server mutation is therefore attributed to the original worker even if another person used the unattended shared tablet during the offline interval.

**Impact and preconditions**

An attacker needs physical/same-profile access to the still-open tab after a worker has established a shift and failed to use **End shift**. They can learn the worker/farm/task names and cause false completion/skip records to be sent later. Explicit logout/end-shift deletes the snapshot (`frontend/src/lib/auth-context.tsx:265-295`, `frontend/src/lib/worker-offline-shift.ts:69-76`), so disciplined handoff reduces the risk.

**Recommendation:** treat offline operations as untrusted drafts requiring explicit same-worker review after reauthentication, or bind them to a hardware/device key. Add a short inactivity lock and kiosk/device-lock guidance. Avoid caching a numeric PIN verifier as the reauthentication mechanism.

### SEC-05 — Security-event logging is neither transactional nor durably retained by the supplied stack

**Severity:** Medium (forensic/detection integrity)  
**Confidence:** High  
**Status:** Source-inferred; no crash injection or external log collector was exercised

`security_event` is a single `INFO` logger call (`backend/app/audit.py:23-28`). Logging honors any pre-existing handler level and creates only a standard handler when none exists (`backend/app/main.py:173-190`). The production Compose default is the local `json-file` driver with three 10 MB files (`docker-compose.production.yml:7-16`). No append-only audit store or guaranteed remote sink is represented in the repository.

The records also do not share transaction semantics with the events they describe. Worker creation/password-reset events are emitted inside `mutate` before the idempotency transaction commits (`backend/app/api/team.py:1093-1110`, `backend/app/api/team.py:1311-1323`; commit at `backend/app/services/idempotency.py:462-480`), so a later rollback can leave a false-positive event. Other events are emitted after commit, such as account deletion (`backend/app/api/auth.py:2144-2162`), so a crash after commit and before logging can omit a real event.

**Impact and preconditions:** an application/host crash, rollback, handler-level override, or log rotation can make incident history incomplete or contradictory. This does not bypass authorization, but it weakens investigation and alerting for credential resets, role changes, and account lifecycle operations. An external immutable collector may mitigate retention, but none was validated.

**Recommendation:** write security events transactionally to an append-only database/outbox table, then ship them to an immutable remote sink with monitored delivery and explicit retention. Keep current structured logs as a projection, not the source of truth.

### PRIV-01 — Screening-photo retention is opt-in, while object deletion is entirely external

**Severity:** Medium  
**Confidence:** High  
**Status:** Source-confirmed; live bucket lifecycle was not validated

The database retention sweep defaults off in both settings and production Compose (`backend/app/core/config.py:727-743`, `docker-compose.production.yml:239-247`). Even when enabled, the retention service explicitly never deletes S3 objects and relies on an independently configured bucket lifecycle (`backend/app/services/retention.py:9-24`). The workflow retains the uploaded original and writes a normalized derivative (`backend/app/services/screening/pipeline.py:1448-1488`, `backend/app/services/screening/pipeline.py:1553-1570`). EXIF/GPS is removed from the normalized bytes sent to vision providers (`backend/app/services/screening/images.py:1-6`, `backend/app/services/screening/images.py:51-123`), but that does not erase metadata from the retained raw object.

**Impact and preconditions:** screening must be enabled and the operator must omit or misconfigure either cleanup control. Farm photos—including incidental people, premises, and raw EXIF/location metadata—can then remain indefinitely, increasing breach and subject-access/deletion scope. A DB sweep alone can also remove the application's index while leaving the underlying media behind.

**Recommendation:** make a finite retention policy part of screening enablement, provision and verify the matching version-aware bucket lifecycle in deployment/IaC, expose lifecycle health/age metrics, and document raw-versus-derived/provider retention separately. Prefer an application-controlled deletion queue if the object store supports reliable versioned deletion.

### SEC-06 — Production validation accepts application auth limiting being disabled

**Severity:** Low  
**Confidence:** High  
**Status:** Configuration acceptance reproduced

`auth_rate_limit_enabled` is an unrestricted boolean (`backend/app/core/config.py:849-862`), and the production safety validator does not require it to remain true (`backend/app/core/config.py:1489-1644`). A direct construction with otherwise valid production settings accepted `auth_rate_limit_enabled=False`.

The supplied production Compose does not forward this override and therefore safely inherits `true`; it also applies a coarse nginx `/api/auth` flood zone. Exploitation thus requires a custom deployment/override or deliberate unsafe setting. If it occurs, login, PIN, refresh, MFA, roster, and invalid-token application ledgers lose their intended target-specific controls while only generic edge shaping remains.

**Recommendation:** reject `false` in production, or require an unmistakable, separately named emergency override plus startup warning/metric. Keep the edge/WAF control independent.

### PRIV-02 — The worker roster exposes names and membership IDs without authentication by default

**Severity:** Low (documented product tradeoff)  
**Confidence:** High  
**Status:** Source-confirmed

`GET /api/auth/worker-roster` intentionally requires only a farm ID and returns active PIN-enabled workers' membership IDs and display names (`backend/app/api/auth.py:1068-1170`, schema at `backend/app/schemas/auth.py:141-159`). It is enabled by default in settings and production Compose (`backend/app/core/config.py:958-969`, `docker-compose.production.yml:230-237`). The endpoint has per-IP and per-farm throttles, but each response may contain 100 names and a normal farm is capped at 200 team members, so only two admitted calls can expose a complete maximum-default roster (`backend/app/api/auth.py:1072-1075`, `backend/app/core/config.py:887-892`).

This does not disclose email or role, and nameless workers use a non-email fallback. The privacy cost is still real: a farm-ID guess can reveal worker identity and the exact membership identifier needed for targeted PIN attempts.

**Recommendation:** default the roster off and require explicit shared-tablet enablement. Prefer a provisioned device/bootstrap secret or opaque badge identifier over a globally unauthenticated name directory.

## Positive controls observed

- JWT verification fixes the algorithm, bounds the key ring, validates `kid` only as a lookup, and requires issuer/audience/core claims (`backend/app/security.py:831-912`). Access requests recheck token generation and a live, unconsumed, unrevoked refresh family (`backend/app/deps.py:140-205`, `backend/app/deps.py:269-305`).
- PIN sessions are bound to one farm/membership and denied from global identity authority; tenant selection rejects duplicate/noncanonical headers, hides unknown versus forbidden farms, and locks/revalidates membership-user-role authorization for writes (`backend/app/deps.py:202-249`, `backend/app/deps.py:681-761`).
- Production fails closed on Secure `__Host-` cookies, exact HTTPS CORS origins, allowed hosts, password/Argon floors, and `verify-full` database TLS (`backend/app/core/config.py:1541-1640`). Cookie requests receive an exact-origin/Fetch-Metadata check (`backend/app/api/auth.py:249-275`), and duplicate refresh cookies are rejected (`backend/app/api/auth.py:555-576`).
- Access tokens live in memory, not Web Storage (`frontend/src/lib/api-client.ts:1-20`); API paths are same-origin constrained (`frontend/src/lib/api-client.ts:578-609`); API responses are `no-store` (`backend/app/main.py:857-872`).
- Production CSP uses a per-request nonce and `strict-dynamic`, with `object-src 'none'` and `frame-ancestors 'none'` (`frontend/src/lib/csp.ts:96-128`). The service worker keeps `/api/` network-only (`frontend/public/sw.js:85-128`).
- Screening input validates MIME, magic bytes, decoded format, pixel/edge budgets, and full decode before re-encoding; the normalized provider payload strips EXIF (`backend/app/services/screening/images.py:51-123`).
- Account deletion immediately revokes access and scrubs identity/password/TOTP material; background cleanup removes recovery codes, PIN material, and notification phone data (`backend/app/api/auth.py:2053-2169`, `backend/app/deps.py:401-528`).
- The repository has pinned dependencies, security-oriented Ruff rules, hash-pinned audit tooling, CodeQL, and container scanning in CI. The local static security lint pass was clean.

## Unvalidated boundaries

- No live PostgreSQL, S3/bucket lifecycle, Anthropic/OpenAI-compatible provider, MSG91, TLS terminator, WAF, or remote SIEM behavior was tested.
- No destructive load test, live credential guessing, browser-extension attack, physical tablet exercise, or multi-replica deployment was performed.
- The in-memory limiter is explicitly single-process and restart-reset (`backend/app/ratelimit.py:1-16`, `backend/app/ratelimit.py:92-105`). Production rejects multi-worker environment settings but cannot detect horizontally replicated containers; single-replica operation remains a hard boundary until shared limiter storage exists.
- This track did not re-run online vulnerability databases or container registries; it inspected the repository's audit/scan gates instead.
- A full dynamic route-by-route IDOR matrix and external privacy/legal compliance assessment were outside this pass. The reviewed dependency architecture showed strong tenant revalidation, but that is not a substitute for an authenticated multi-tenant penetration test.

## Recommended order

1. Close the rotating-source worker-PIN guessing path (SEC-01) without introducing permanent account-lockout DoS.
2. Exclude reset-password from persisted idempotency recovery (SEC-03); this is the smallest high-confidence code fix.
3. Require post-login confirmation for unauthenticated offline drafts (SEC-04).
4. Make security events transactional/durable (SEC-05) and make screening retention verifiable (PRIV-01).
5. Tighten the password soft limit, production limiter invariant, and roster opt-in posture (SEC-02, SEC-06, PRIV-02).
