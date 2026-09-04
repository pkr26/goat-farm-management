# Red Team Audit #6 — "The XSS Ghost" — Frontend & Client-Side Security

**Date:** 2026-09-04 · **Method:** read-only adversarial code audit of `frontend/` (Next.js App Router SPA, Orval client, /api proxy). Persona: attacking through the browser. **No Critical/High/Medium exploitable issue confirmed.** The findings below are the only verified weaknesses.

## FINDINGS

### 1. CSP allows inline scripts (documented framework gap) — LOW
`frontend/next.config.ts:27`

Evidence: `"script-src 'self' 'unsafe-inline'",`

Attack scenario: an attacker who stores `"><script>fetch('/api/team/workers…')</script>` in any coworker-rendered field (animal notes, task titles, farm names) would get script execution unrestrained by CSP. However, zero injection sinks were verified to exist (see defended vectors) — today this is purely a lost defense-in-depth layer, and the file itself documents the attempted-and-reverted nonce fix ("Next 16 does not propagate a request-header nonce… a nonce policy would block hydration").

**Fix:** none available short of a Next framework fix for nonce'd RSC bootstrap scripts; keep the gap pinned by the existing adv-G-config test and re-test on Next upgrades.

### 2. Orval generated client trusts API responses with zero runtime validation — LOW
`frontend/src/api/custom-instance.ts:36-41`

Evidence: `return (await apiFetchEnvelope(stripNullQueryValues(url), options)) as T;` — the cast is the only "validation".

Attack scenario: a compromised/malicious backend (or MITM on the plaintext BACKEND_URL hop, finding 3) returns `{__proto__: {...}}` or type-mismatched JSON hoping to hijack the UI. Blast radius traced: `resp.json()` (JSON.parse) cannot pollute `Object.prototype`, and there is no deep-merge/`Object.assign` sink fed by response data (only static literal rules in `simulation/page.tsx:439-482`). Worst case is wrong data rendered as escaped text or a render crash caught by `app/(app)/error.tsx`. Not UI-hijackable today.

**Fix:** optional zod parse on auth-critical responses (`/api/auth/permissions`, `/api/auth/farms`) to fail closed on schema drift.

### 3. Default proxy hop to backend is plaintext HTTP — LOW
`frontend/next.config.ts:60`

Evidence: `return backendRewrites(process.env.BACKEND_URL ?? "http://localhost:8000");`

Attack scenario: an operator who sets `BACKEND_URL=http://backend:8000` across a shared network exposes the browser→Next bearer token (forwarded verbatim by the rewrite) and the httpOnly refresh cookie to network-level attackers on that hop. Fine for the compose-internal default; a real risk only for remote/multi-host deploys. `connect-src 'self'` correctly confines the browser itself.

**Fix:** document/require an https or unix-socket BACKEND_URL for non-localhost deployments.

## VECTORS ATTEMPTED AND DEFENDED (top 5 of ~15 tried)

- **Stored XSS via user content:** grep of all of `frontend/src` found zero `dangerouslySetInnerHTML`, `innerHTML`, `document.write`, `eval`, `new Function`, `insertAdjacentHTML`, iframes, `srcDoc`, markdown renderers, or `<img>`/next-image usage. Every coworker-controlled string (farm names, task titles, notes, `ApiError.detail` echoed at `login/page.tsx:99`) renders as JSX text — React-escaped by construction. All inline `style={{...}}` values are numeric/computed (`charts.tsx:189,245`).
- **Open redirect / `javascript:` URL via returnTo and query state:** every navigation consumer funnels through `safeAppPath` (`utils.ts:13-37`, rejects `//`, `\`, control chars, `%`, non-canonical spellings) plus `permittedAppPath*` permission-root checks (`permission-navigation.ts:87-113`); `login/page.tsx:76-80`, `farm-select/page.tsx:99-103`, `health/page.tsx:422` all validated. Backend-generated `task.action_url` likewise gated (`task-action-access.ts:53-75`, rejects `%2e/%2f/%5c`, allowlists 3 module roots).
- **Token theft surface:** access token lives only in a module-level variable (`api-client.ts:15`) — never localStorage/sessionStorage/URL/`window.___`; localStorage holds exactly one key, the non-secret farm id (`auth-context.tsx:55,66-77`); sessionStorage holds only idempotency digests, with password-bearing routes explicitly excluded from persistence (`idempotent-request.ts:205-207`). Logout clears query cache, token, farm state and revokes server-side (`auth-context.tsx:182-233`). One console.error in `error.tsx:19` logs the error object only.
- **Client-side-only authz:** no middleware exists — gating is React-side, but cross-check against every backend domain router (`backend/app/api/*.py`) shows each carries `require_perm` dependencies (`team.py:57` `Depends(require_perm("team.manage"))` etc.), so hidden UI cannot read data the API would return.
- **Proxy abuse / SSRF-ish fetch escape:** rewrites are fixed env-configured destinations (`backend-rewrites.ts:2-8`); the client blocks arbitrary hosts via `assertSafeApiPath` (`api-client.ts:386-414`: same-origin `/api/`, `/healthz`, `/readyz` only, no `%`, no hash, canonical path), and path params are `Number()`-coerced before URL interpolation (`animals/[id]/page.tsx:1518`). No postMessage listeners, no `window.open`, COOP `same-origin` + `X-Frame-Options: DENY` set (`next.config.ts:9-14`). No `.env` committed (git ls-files: only `.env.example`), no `NEXT_PUBLIC_*` anywhere, no remote image domains, no source-map enabling config.

## VERDICT

Frontend security is **strong — best-in-class for this stack**. Token handling is memory-only with a disciplined cookie critical section; navigation state is fail-closed validated; rendering is escape-by-default with no HTML sink to attack. The only real exposure is the documented `'unsafe-inline'` CSP gap, which is inert until someone introduces an injection sink — the pinning test and this report are the right tripwires. No Critical/High/Medium issues to report.
