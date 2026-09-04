# Red Team Audit #1 — "The Neighbor" — Authentication, Authorization & Multi-Tenant Isolation

**Date:** 2026-09-04 · **Method:** read-only adversarial code audit (all 17 routers in `backend/app/api/`, `deps.py`, `security.py`, `core/config.py`, `main.py`, `permissions.py`, `models/core.py`, `services/{idempotency,tasks,feeding,_common}.py`, schemas, frontend token storage) · every endpoint read; every accusation backed by code.

## Findings (ranked by impact)

**No Critical or High tenant-isolation vulnerabilities found.** The genuine, code-verified residual risks are all in the identity layer, not in tenant scoping.

### 1. Unverified self-registration permits email squatting / pre-hijack of arbitrary identities — MEDIUM
`backend/app/api/auth.py:678-740`, `backend/app/api/team.py:855-860`

Attack: the attacker (a rival tenant) `POST /api/auth/register`s `victim@realdairy.com`. The server creates a fully functional account with zero email verification. Impact:
- (a) the real victim can never register that email — `register` returns 400 `"That email is already registered."` (`auth.py:719-721`);
- (b) any farm owner trying to provision a worker with that email is permanently refused — `team.py:855-860` `worker = ... select(User).where(User.email == email); if worker is not None: raise HTTPException(400, CANT_ADD_TO_TEAM)` — a targeted worker-onboarding DoS on arbitrary victim emails;
- (c) if the squatter is later legitimately added to a farm under a different route, the victim's email becomes the attribution identity.

Not High because existing accounts cannot be taken over (Argon2 hash required) and farm membership still requires owner action. Evidence: `register` performs only `select(User).where(User.email == payload.email)` then `db.add(user)` — no verification token is ever issued; the design gap is acknowledged at `team.py:59-62` ("Until an email-verified invitation/acceptance flow exists...").

**Fix:** verify email ownership before activation (or before the account can provision-blocklist / be attributed).

### 2. Cross-tenant account-enumeration oracle on POST /api/auth/register — LOW
`backend/app/api/auth.py:718-733`

Attack: an authenticated-or-anonymous neighbor probes `{"email": "owner@victim.com", "password": "..."}` — a 400 `ALREADY_REGISTERED` vs 201 cleanly classifies any email as a live account across the whole SaaS (emails are global; farm owners are users). Login is timing-equalized and generic; register is not. The per-email probe throttle (`auth.py:698-717`) charges only *existing* emails, so IP rotation preserves enumeration while the code deliberately avoids locking fresh registrants.

Evidence: `if existing.scalar_one_or_none() is not None: _charge_email_probe(); raise HTTPException(status_code=400, detail=ALREADY_REGISTERED)`.

**Fix:** once verification exists, return a uniform accept-and-notify 202 for both branches.

### 3. No self-service password recovery exists at all — LOW (lockout/availability)
`backend/app/api/team.py:64` vs the `auth.py` router (routes at `auth.py:678-1559`)

A farm owner forgets their password. `team.py:64` defines `RESET_PASSWORD_SELF_SERVICE_REASON = "This account must use self-service password recovery."`, but `auth.py` exposes only register/login/refresh/logout/change-password/me/account — there is no forgot/reset endpoint, and ownership cannot be transferred (account deletion is refused while owning a farm, `auth.py:1425-1437`). The tenant becomes permanently unreachable. Additionally, the IP-agnostic per-email login bucket (`auth.py:231-236`, `attempts * EMAIL_LIMIT_MULTIPLIER` = 30/5 min) lets a distributed attacker keep a *known* email locked out of login indefinitely — a documented anti-bruteforce tradeoff, but with no recovery channel it becomes a sustained DoS on a known tenant owner.

**Fix:** add an email-verified password-reset flow (which also requires solving finding 1).

### 4. Any valid access token can force logout-everywhere (targeted session DoS) — LOW
`backend/app/api/auth.py:1140-1153`

An attacker who briefly obtains one 30-minute access token (logs, referrer leak, shared machine) calls `POST /api/auth/logout` with only the Bearer header; the server executes `revoke_user_sessions(db, logged_out_user.id)` and `token_version += 1`, killing every refresh family on every device. Comment shows this is deliberate ("bearer-only logout means logout everywhere"); a thief would usually prefer silence — impact is device-lockout DoS.

**Fix:** restrict family-wide revocation to requests that also present the (valid) refresh cookie, or make bearer-only logout revoke just the presented `jti`/version scope.

### 5. Multi-process deployment silently multiplies every auth ceiling — LOW (deployment hardening)
`backend/app/main.py:328-336`, `backend/app/ratelimit.py:3-9`

Operator launches `uvicorn --workers 4` (only a *warning* is logged at boot). All brute-force budgets (login, register, invalid-token, refresh) and the Argon2 admission pool are per-process, so effective limits multiply 4x, weakening every throttle the app relies on.

Evidence: `logger.warning("UVICORN_WORKERS/WEB_CONCURRENCY indicates a multi-process deployment: ... budgets ... will be multiplied.")`.

**Fix:** refuse to boot (raise) when multi-process is detected, or move limiter state to a shared store. *(Cross-referenced by Audit #5 finding 5: a bare `uvicorn --workers 4` sets neither env var, so the warning never fires at all.)*

## Attack vectors attempted and found PROPERLY DEFENDED

1. **IDOR / numeric-ID guessing on every router** — every object fetch filters `farm_id` in the same WHERE as the id (`animals.py:137/141`, `tasks.py:114`, `breeding.py:70`, `simulation.py:152-159`, `planner.py:156-159`, `purchases.py:189-191`, `health.py:277-287`, `finance.py:90-103`, `milk.py:91/199`), and tenant selection (`deps.py:518-580 current_farm`) validates ownership/membership from a duplicate-rejected `X-Farm-Id` with a unified 404 that defeats farm-id enumeration; int4 ceiling guards prevent 500-oracle probing.
2. **Privilege escalation via team.py** — self role-change blocked (`team.py:956-957`), peer-manager lockout/hijack blocked (545-557), non-owner `team.manage` grant blocked (588-592), role edits above one's own permission ceiling blocked (573-585), worker-create/reset owner-only (268-269, 294-295), owner password resets limited to farm-provisioned accounts (354-364).
3. **JWT/session attacks** — RS256 pinned by `Literal["RS256"]` and header-alg equality (`security.py:723-724`), iss/aud/required-claims enforced, `kid` is a dictionary lookup only, `token_version` revocation checked on every request (`deps.py:166-167`), refresh tokens are server-side jti sessions with family-wide reuse revocation and a bounded 3s tab-race grace (`auth.py:944-992`).
4. **Mass assignment** — every input schema extends `StrictInputModel(extra="forbid")` (`schemas/common.py:29-37`); no `*In` schema exposes `farm_id`/`role`/`owner_id`/`is_active`/`created_at`; `Role.code` is documented server-owned (`permissions.py:349-351`); strict typed IDs/bools prevent `true`→1 coercion.
5. **Cookie/CORS hygiene** — refresh cookie is httpOnly+SameSite=Lax, `__Host-` + Secure force-failed in production (`config.py:463-467, 509-515`), exact-Origin/Referer/Sec-Fetch-Site check before cookie consumption (`auth.py:185-211`), CORS is exact origins only with fail-closed production validation, and `TrustedHostMiddleware` + untrusted X-Forwarded-For defaults prevent host/IP spoofing of the rate limiter.

## Verdict on tenant isolation

**Strong.** Cross-farm read/write is enforced server-side in the SQL itself on every path traced, with defense-in-depth at the DB layer (composite tenant FKs), consistent lock-ordering against authorization races, and object-level task visibility. As a malicious tenant I could not construct a single cross-farm data access; the exploitable surface that remains is confined to the global identity layer (unverified emails → squatting, enumeration, and the absence of any password-recovery channel).
