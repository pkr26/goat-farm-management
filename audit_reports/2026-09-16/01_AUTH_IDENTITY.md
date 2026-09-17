# 01 — Authentication & Identity (static + live)

Catalog items 1.1–1.7. Static falsification audit of `backend/app/{api/auth.py,security.py,deps.py,ratelimit.py,api/team.py,core/config.py}` plus live attacks (evidence: `evidence/phase4_auth.json`, `evidence/phase8_ratelimit.json`).

## Verdicts

| # | Attack class | Verdict |
|---|---|---|
| 1.1 | Rate-limit bypass / stuffing | DEFENDED (documented ceilings; 2 Info) |
| 1.2 | Refresh rotation races / theft detection | DEFENDED (live-confirmed) |
| 1.3 | JWT attack suite (alg/kid/leeway/rotation) | DEFENDED (live 7/7 rejects) |
| 1.4 | Worker provisioning & reset takeover | DEFENDED (live-confirmed fencing) |
| 1.5 | Enumeration & timing | DEFENDED (1 Low oracle) |
| 1.6 | Legacy pbkdf2 | DEFENDED (no insertion path) |
| 1.7 | Password policy & storage | DEFENDED (2 Info) |

## Live results (2026-09-16)

- **JWT tampering — 7/7 rejected 401**: flipped signature, `alg:none`, HS256-confusion, unknown `kid`, missing `kid`, unsigned forged claims (`ver=999999`), garbage token. (`phase4_auth.json`)
- **Refresh theft detection — exact design behavior**: first refresh 200; replay of the consumed cookie 401 (family revocation); the *newest* cookie is also dead after replay (whole family revoked); evil-`Origin` refresh → 403 "Untrusted origin for cookie-authenticated request"; logout with revoked cookie → 204 idempotent.
- **Worker fencing — total**: pre-rotation worker reaches only `/api/auth/me|permissions|farms` (GET) + `change-password`; farm creation, account deletion, and all domain routes → 403 with the rotate-your-password message. VIEWER role confirmed read-only permission set.
- **token_version — instant revocation**: after password change, prior access token 401 on next request; prior refresh cookie dead.
- **Rate limiting**: same-email spray → exactly 10×401 then 429 (composite ledger). **Case/whitespace variants (`SPRAY1…`, ` spray1… `, mixed case) all hit the same normalized ledger key — no fragmentation bypass.** Distributed 1-attempt-per-email stuffing across fresh emails stays under limits (documented tradeoff: ~30 wrong guesses/5 min/email across IPs, each paying full Argon2). Unknown-email rejections equalized at ~26 ms (Argon2 dummy path).

## Findings

| ID | Sev | Finding | Evidence |
|---|---|---|---|
| AUTH-1 | Low | **Legacy pbkdf2 timing oracle**: for imported hashes with iterations > the 50k rejection budget, 401 latency grows linearly with the stored count → remote account-class oracle (identifies high-cost legacy accounts). Documented tradeoff at `config.py:269-274`; no test pins behavior above budget. | `app/security.py:200-217` |
| AUTH-2 | Info | Login/register have no Origin/Referer requirement; login-CSRF defense is framework-incidental (FastAPI strict JSON content-type + CORS preflight). Verified in fastapi 0.141.1 `routing.py:433-450`; no application-level assertion. | `app/api/auth.py:801` |
| AUTH-3 | Info | Worker-create idempotency HMAC fingerprints the cleartext password (never persisted raw); if the HMAC secret leaks, DB/backup readers get a fast 7-day offline verifier bypassing Argon2 cost. Prod secret validators (32+ chars, non-default) mitigate. | `app/services/idempotency.py:129-179`, `config.py:570-602` |
| AUTH-4 | Info | Password max-128 counted in code points (≤512 UTF-8 bytes reach Argon2). Bounded; intent holds. | `app/schemas/auth.py:49,55,81-82` |
| AUTH-5 | Info | `security.py:decode_token()` lacks `ver` enforcement — dead code, zero app callers; delete or align. | `app/security.py:801-809` |
| AUTH-6 | Info | In-memory ledgers reset on restart; sustained within-ceiling stuffing (1 guess/10 s/email) possible by design. Single-worker invariant enforced at boot + Dockerfile. | `app/ratelimit.py`, `config.py:687-700` |

## Held fixes re-verified

RT-A-1 soft per-email ceiling, RT-A-2 pre-hash probe gate, RT-A-3 private-key
mode check, RT-M-5 per-IP gate ordering, grace-window successor idempotency,
fence allowlist, shared account-password Argon budgets — each with a named
regression test; none could be re-broken. Rotation serialization (User FOR
UPDATE → RefreshSession FOR UPDATE, ±3 s grace returning the same successor),
family-wide logout, cross-family mixing rejection, and session caps (10
families/1024 sessions) all held.
