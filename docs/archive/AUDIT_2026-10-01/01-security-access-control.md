# Security & Access Control Audit (2026-10-01)

Auditor: 01 of 10 (independent). Angle: security & access control (offensive/defensive, OWASP).
Scope: backend core config, all 17 API routers, shared API helpers, run limits, auth infrastructure (deps/security/ratelimit read caller→callee as needed), frontend auth/transport/CSP/proxy/SW/manifest, screening worker, backend/pins.

## Executive summary

**Verdict: PASS with hardening notes.** No Critical or High findings. No exploitable authentication bypass, authorization bypass, IDOR, cross-tenant leakage, injection, XSS, or unauthenticated-DoS path was found. Every endpoint audited enforces authentication (via `CurrentUser`/`CurrentUser`-bearing dependencies) and tenant scoping (`farm_id`/ownership predicate on every query, or a shared 404 for unknown-vs-forbidden ids); mutation paths additionally pin Membership→User→Role locks and revalidate the signed token version. The codebase shows multiple generations of prior security remediation (in-code audit IDs from 2026-09-16 through 2026-09-29) and the hardening is real, not cosmetic — verified against the code, not the comments.

Counts by severity: **Critical 0 · High 0 · Medium 0 · Low 4 · Info 3** (plus positive observations below).

Key basis for the verdict, each verified line-by-line:
- JWT: RS256 only (`alg` pinned before any key lookup; `kid` is a dict key, never a path/URL — security.py:766-847), issuer+audience verified, `sub` canonicalization strict, mandatory server-side `ver` (token_version) revocation check (deps.py:174-188), bounded rotation keyring validated at boot (security.py:581-648), ≥2048-bit floor, production key-file mode check (main.py:528-546).
- Refresh: server-side session rows, rotation with family revocation on reuse outside a 3s same-tab grace, family/session caps, cookie is httpOnly+SameSite=Lax(+`__Host-`/Secure enforced in production), duplicate-cookie and duplicate-Authorization ambiguity rejected, Origin/Referer/Sec-Fetch-Site guard on cookie-authenticated routes, JSON content-type required on credential endpoints (login-CSRF).
- Passwords: Argon2id off-loop with bounded pool, PBKDF2-iteration ceiling, dummy-hash timing equalization on unknown email/PIN/membership, rehash-on-login, PBKDF2 padding so rejections cost identical work.
- Multi-scope, IP-agnostic-where-needed rate limiting (composite/IP/email/per-token/per-account buckets, admission reservations, Argon2 work budget); production refuses multi-worker launches, weak keys, HTTP cookies, loopback CORS, TLS-less DB, short passwords, dev HMAC secret.
- Tenant scoping: 100% of the ~120 endpoint query paths reviewed carry `farm_id` (or `Farm.owner_id`) predicates; all path-param lookups band-guarded to int4/int8 PK ceilings with shared 404s (anti-enumeration); `X-Farm-Id` required exactly once, ASCII-digit parsed, one 404 for unknown/forbidden.
- Injection: no raw SQL with user input (only `SELECT 1` and constant advisory-lock namespaces); all LIKE searches escape `%`/`_`/`\`; no `dangerouslySetInnerHTML`/`innerHTML` anywhere in frontend/src; nonce CSP with `strict-dynamic` (no `unsafe-inline` scripts).
- DoS: request body/target-size middleware, bounded pagination everywhere, simulation CPU admission control priced per request (per-user+per-farm sliding budget, one in-flight run per farm/user, 2 process-wide), non-finite result 422s, per-farm standing quotas.

## Findings

### [Low] Unauthenticated worker-roster endpoint enumerates worker names by sequential farm id
Location: backend/app/api/auth.py:1029-1098 (`GET /api/auth/worker-roster`)
Evidence:
```python
@router.get("/worker-roster")
async def worker_roster(request, db, farm_id: Annotated[int, Query(ge=1, le=MAX_INT32_ID)]) -> WorkerRosterOut:
    """Names tap-to-sign-in offers on this farm's shared tablet.
    Unauthenticated by design (the tablet's first screen has no session) and
    hard-throttled per IP: this trades limited first/last-name enumeration per
    farm id for a PIN pad a field worker can actually use …"""
    ...
    .where(FarmMembership.farm_id == farm_id, FarmMembership.is_active.is_(True),
           FarmMembership.role_id.is_not(None), FarmMembership.pin_hash.is_not(None),
           User.deleted_at.is_(None))
    .order_by(User.name, FarmMembership.id).limit(100)
```
Impact: pre-auth disclosure of worker display names + membership ids for any farm that uses tablet PINs. Farm ids are sequential, so an unauthenticated caller can crawl the deployment at the throttle's pace (30 requests / 5 min / IP ⇒ ~8.6k farms/day/IP) harvesting names. Emails are deliberately excluded (`Worker {id}` fallback, not `display_name`). The tradeoff is documented and `GOATFARM_WORKER_ROSTER_ENABLED=false` closes it entirely — but it defaults to **true**.
Fix: ship `worker_roster_enabled=false` as the default for deployments without shared tablets, or bind the roster to a short-lived farm-scoped roster token the owner mints on the tablet.

### [Low] Account-existence oracle on /api/auth/register (explicit duplicate-email 400)
Location: backend/app/api/auth.py:852-867
Evidence:
```python
existing = await db.execute(select(User).where(User.email == payload.email))
if existing.scalar_one_or_none() is not None:
    _charge_email_probe()
    raise HTTPException(status_code=400, detail=ALREADY_REGISTERED)  # "That email is already registered."
```
Impact: confirmed in code — email enumeration against the user base at ~10 probes / 5 min / IP, plus a per-email probe bucket (an IP-rotating attacker can lock a *legitimate registrant* out of their own address's probe budget, though fresh-address registration itself stays unthrottled by that bucket). The code comments correctly note there is no accept-and-notify path without email verification; timing is equalized (hash-before-check).
Fix: once an email channel exists, switch to accept-and-notify; until then this is a documented, rate-limited tradeoff. Consider a per-email *soft* ceiling for the 400 path only (mirroring the login-email soft scope) so enumeration cannot also DoS registration.

### [Low] Per-process auth state: restart wipes brute-force ledgers and permits one replay of a consumed MFA challenge
Location: backend/app/api/auth.py:2140-2166 (`_mfa_jti_replay_cache`), backend/app/ratelimit.py:92-99 (MemoryLimiterBackend), backend/app/core/config.py:1403-1420 (multi-worker refusal)
Evidence:
```python
# Accepted tradeoff (2026-09-28 audit, S5): being in-memory, the cache is lost on a process
# restart, so a restart inside TOTP_CHALLENGE_TTL_SECONDS lets one already-
# consumed challenge token replay exactly once.
_mfa_jti_replay_cache: OrderedDict[str, datetime] = OrderedDict()
```
Impact: "depends on runtime config" class. A restart (crash loop, deploy) inside the 300 s challenge TTL allows exactly one replay of an already-consumed MFA challenge token — which still demands a valid TOTP/recovery code, so the residual risk is small. Restarts also clear login/PIN/refresh invalid-token ledgers (bounded by the fact that every guess still pays Argon2 and still fails). Multi-replica deployment is refused at boot in production (UVICORN_WORKERS/WEB_CONCURRENCY > 1 ⇒ refuse), so the per-process assumption is enforced.
Fix: as the code itself mandates — move the replay cache and (optionally) the failure ledgers to shared storage before any multi-replica scale-out; no action needed for the current single-worker topology.

### [Low] Notification channel: owner-asserted `verified` flag and free-text farm data interpolated into SMS bodies
Location: backend/app/api/team.py:1449 (`recipient.verified = payload.verified`), backend/app/api/animals.py:1279-1283, backend/app/api/health.py:1050-1057 (`emit_alert` messages embedding `animal.tag_number`, `finding.label`)
Evidence:
```python
recipient.verified = payload.verified            # team.py — client-asserted
...
f"Herdly: {animal.tag_number} placed under movement restriction "   # animals.py
f"Herdly: screening finding #{finding.id} ({finding.label}) was CONFIRMED by the vet.",  # screening.py
```
Impact: (a) `verified` is the owner's assertion — there is no OTP loop, so "verified" gates nothing cryptographic; it is a labeling field for the owner's own MSG91 account/billing. (b) SMS body text is partly composed from worker-enterable free text (tag numbers, finding labels). `IdentifierText` strips control characters and bounds length, phone numbers are pattern-locked (`^\+?[0-9]{10,19}$`), recipients are owner-registered, sender id fixed — so injection is bounded to plain words inside the farm's own alert channel (e.g. a misleading sentence in a tag). No cross-tenant reach.
Fix: template-only alert bodies (fixed vocabulary + ids) or strip URL-ish tokens from interpolated fields; consider a real OTP verification flow for phone numbers.

### [Info] /metrics is unauthenticated when enabled; force-disabled in production
Location: backend/app/main.py:1053-1062, backend/app/core/config.py:1423-1431. Route sits outside `/api` (compose edge only routes `/api/` to the backend) and `metrics_enabled` is force-set false by the production validator. No exposure in the shipped topology; any port-forward misdeployment is covered by the force-off.

### [Info] worker-login returns distinguishable 403s after a proven-correct PIN
Location: backend/app/api/auth.py:1296-1312 (ACTIVE TOTP → 403 "requires two-factor"; must_change_password → 403). Deliberate, PIN-gated oracle pinned by tests; discloses only flow requirements for a membership whose PIN the caller already knows. All wrong-PIN paths answer a generic 401 after identical Argon work.

### [Info] Bearer-only logout performs logout-everywhere
Location: backend/app/api/auth.py:1626-1634. With no valid cookie there is no provable family, so a valid current bearer bumps `token_version` and revokes all sessions. Documented; the alternative (revocable single-version bump) is correctly explained as insecure. Cookie+bearer identity mismatch is rejected (401) before any mutation.

## Coverage manifest

Line-by-line unless noted. 36 in-scope files: 34 full, 2 partial (generated hash manifests) ⇒ 94% fully reviewed, 100% accounted for.

| File | Status |
|---|---|
| backend/app/core/config.py (1583 ln) | full |
| backend/app/core/__init__.py (empty) | full |
| backend/app/api/_shared.py (435 ln) | full |
| backend/app/api/_run_limits.py (405 ln) | full |
| backend/app/api/auth.py (2944 ln) | full |
| backend/app/api/team.py (1649 ln) | full |
| backend/app/api/owner.py (373 ln) | full |
| backend/app/api/animals.py (1552 ln) | full |
| backend/app/api/breeding.py (456 ln) | full |
| backend/app/api/buckets.py (148 ln) | full |
| backend/app/api/dashboard.py (775 ln) | full |
| backend/app/api/feeding.py (374 ln) | full |
| backend/app/api/finance.py (1109 ln) | full |
| backend/app/api/health.py (1085 ln) | full |
| backend/app/api/kidding.py (435 ln) | full |
| backend/app/api/ops_simulation.py (142 ln) | full |
| backend/app/api/planner.py (545 ln) | full |
| backend/app/api/purchases.py (318 ln) | full |
| backend/app/api/screening.py (997 ln) | full |
| backend/app/api/simulation.py (724 ln) | full |
| backend/app/api/tasks.py (930 ln) | full |
| frontend/src/lib/api-client.ts (925 ln) | full |
| frontend/src/lib/auth-context.tsx (660 ln) | full |
| frontend/src/lib/csp.ts (129 ln) | full |
| frontend/src/lib/farm-scope-guard.ts (18 ln) | full |
| frontend/src/lib/backend-rewrites.ts (57 ln) | full |
| frontend/src/proxy.ts (73 ln) | full |
| frontend/next.config.ts (79 ln) | full |
| frontend/public/sw.js (92 ln) | full |
| frontend/src/app/manifest.ts (38 ln) | full |
| backend/app/worker/__init__.py (275 ln) | full |
| backend/app/worker/__main__.py (7 ln) | full |
| backend/app/worker/heartbeat.py (51 ln) | full |
| backend/pins/pip-audit.in (1 ln) | full |
| backend/pins/pip-audit.txt | partial — generated uv hash-manifest for the pip-audit *toolchain only* (not application deps); header + package list inspected, no secrets, no application dependencies, hash-pinned. Per-hash line reading has no security semantics. |
| backend/pins/uv.txt | partial — same rationale (toolchain lockfile manifest). |

Supporting files read end-to-end beyond scope for caller→callee verification: backend/app/security.py (1128), backend/app/deps.py (648), backend/app/ratelimit.py (490), backend/app/main.py (1083); excerpts: schemas/auth.py, schemas/team.py, schemas/screening.py, schemas/animals.py (IdentifierText), schemas/common.py, services/tasks.py (task_scope), services/idempotency.py (HMAC fingerprint). Repo-wide sweeps: no `dangerouslySetInnerHTML`/`innerHTML` in frontend/src; no `text()` raw SQL with user input in backend/app (only `SELECT 1` and constant advisory locks).

## Positive observations

1. **Tenant isolation is systematic, not incidental.** Every router repeats the pattern `where(Model.farm_id == farm.id)` on reads and `id == X, farm_id == farm.id` + `for_update` on mutations; cross-farm ids always resolve to the same 404 as unknown ids (no existence oracle). `X-Farm-Id` handling (exactly-one header, ASCII-digit spelling, range band) closes proxy/app disagreement. Owner cross-farm views (`owner.py`) are ownership-scoped, not permission-scoped, and bounded.
2. **Authorization depth beyond simple RBAC:** peer-manager protection (`team.py:643`), role-scope ceiling for delegated managers (`_guard_role_scope`), owner-only provisioning/reset guards with token-version revalidation after every off-transaction Argon pause, two-person rule for scheduled-disease hold clearance and duty verification, permission-derived field redaction in serializers (`animal_out`, dashboard/reports/finance withholding), and per-section permission gating so aggregate views cannot become side doors (explicitly documented and enforced in dashboard.py/reports/finance mortality).
3. **Refresh-token design is textbook-plus:** rotation + server-side sessions + family revocation on reuse with a bounded same-tab grace, session/family ceilings with deterministic eviction, family claim shape validation, expiry-boundary re-checks under lock, and lock ordering (User → RefreshSession) documented and consistent with reset/logout paths.
4. **Abuse-cost engineering:** login/register/PIN/TOTP/disable/confirm paths each carry multi-dimensional budgets (composite IP+identity, IP-agnostic per-account, spray scopes, per-token pre-verification budgets keyed on sha256(token) so NAT neighbors can't 429 a valid credential), admission reservations closing check-then-work races, and cancellation accounting so disconnects cannot get free Argon work. Simulation endpoints carry real CPU admission control with honest per-request pricing (including birth amplification for ops-sim) and cancellation-safe lease transfer.
5. **Frontend transport discipline:** access token in memory only (never localStorage), same-origin path allowlist (`assertSafeApiPath`), Web-Locks-coordinated refresh across tabs, actor-scope checks preventing cross-account replay, farm-scope epoch fencing continuations across farm switches, `BACKEND_URL` constrained to loopback/internal service names (anti credential-exfil), nonce CSP with `strict-dynamic`, SW network-only for `/api` (no tenant data ever cached; shell-only fallback), `no-store` on all `/api` responses at the backend.
6. **Fail-closed production posture:** boot refuses weak/missing/mismatched JWT keys, group-readable private keys, dev HMAC secret, non-HTTPS CORS, `__Host-` without Secure, TLS-less DB, sub-floor Argon2/password lengths, multi-worker launches, unknown GOATFARM_* env vars, incomplete screening/notification config; docs/OpenAPI/metrics disabled in production.
7. **Idempotency as a security control:** required keys on all money-booking mutations, keyed-HMAC fingerprints (never raw secrets) with constant-time compare, replay suppression for alert fan-outs.
8. **Logging hygiene:** secrets never logged (limiter keys are sha256 hashes of emails/tokens; phone numbers excluded from audit events), control-character-escaped paths (anti log-forging), opaque 500s, sanitized 422s that do not reflect rejected input.
