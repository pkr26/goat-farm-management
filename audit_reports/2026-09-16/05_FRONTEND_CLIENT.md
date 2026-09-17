# 05 — Frontend & Client-Side (static)

Catalog items 5.1–5.6. Static falsification audit of `frontend/` (no Node on the
audit host — live browser testing deferred; the repo's Playwright suites
`e2e/cross-tenant.spec.ts`, `navigation-rbac.spec.ts`, `tasks-guards.spec.ts`
back the RBAC-parity conclusions).

## Verdicts

| # | Area | Verdict |
|---|---|---|
| 5.1 | XSS surface / CSP escape | DEFENDED — zero `dangerouslySetInnerHTML`/`srcDoc`/`eval`; every dynamic href validated; Blob downloads use fixed filenames and non-HTML content types; no style injection from API data |
| 5.2 | Open redirect / navigation | DEFENDED — `safeAppPath` fuzz (`%2F`, double-encode, `\`, `/\`, `//`, late-decode `javascript:`, U+FF0F) all rejected; every `router.push(searchParams…)` call site goes through `permittedAppPath*` |
| 5.3 | Session/refresh races | DEFENDED (modern path); 2 Low residuals below |
| 5.4 | Logout & CSRF | DEFENDED — POST-only mutations, SameSite=Lax, browser-generated Origin/Sec-Fetch, no state-changing GET exists |
| 5.5 | Client RBAC parity | DEFENDED — every data page gated; stale farmId dropped safely |
| 5.6 | Storage/exports/error boundaries | DEFENDED — idempotency cache shape-validated, export contains no tokens, no token reaches console |
| 5.7 | Mobile/spoofing | 1 Low (bidi) |

## Findings

| ID | Sev | Finding | Evidence |
|---|---|---|---|
| FE-1 | Low | Browsers without Web Locks (pre-Safari-15.4) can run concurrent cross-tab refreshes over the per-realm fallback mutex → cookie replay → backend family revocation kills both tabs (session DoS). Depends on the 3 s backend grace; modern path is serialized. | `src/lib/api-client.ts:151,196-197` |
| FE-2 | Low | Non-transient spoofed refresh status (injected 403 / garbage 200) is classified `rejected` → local teardown, lost unsaved work (cookie survives; reload recovers). No server-side session death. | `src/lib/api-client.ts:270-276` |
| FE-3 | Low | A 5xx exactly during login's `/api/auth/farms` fetch revokes the just-created session (`revokeOnFailure=true`) — user must re-authenticate; attacker needs network-degradation position. | `src/lib/auth-context.tsx:352-365` |
| FE-4 | Low/Info | Bidi controls (U+202E etc.) from tenant-entered names visually reorder adjacent Latin text (no `dir="auto"` isolation anywhere) — a worker can be misled into acting on the wrong animal. Pairs with INJ-3/INJ-4 backend acceptance. | `src/app/(app)/app-layout-client.tsx:286` et al. |
| FE-5 | Info | Orval generated path params lack `encodeURIComponent` — currently fully backstopped by `assertSafeApiPath` (canonicalization mismatch throws). Latent generator weakness. | `src/api/generated/endpoints.ts:2174` |
| FE-6 | Info | `Donut`/`Histogram` accept raw `color`/`style` props — latent CSS-injection sink under `style-src 'unsafe-inline'` (no caller passes API data today). | `src/components/charts.tsx:95,121` |
| FE-7 | Info | Dev/non-prod responses carry no CSP/HSTS (prod unaffected — `isProd` gate). | `frontend/next.config.ts:46,66` |

## Note on CSP `'unsafe-inline'` (script-src)

The production CSP retains `'unsafe-inline'` for scripts (documented RSC
constraint). This campaign found **no sink** that converts inline-script
execution into XSS: React escaping everywhere, all navigation/url sinks
validated, no HTML-rendering Blob. The residual exposure is the httpOnly
refresh cookie itself (injected script can POST `/api/auth/refresh` same-origin
and mint a token) — mitigated by the origin guard + rotation theft detection,
both live-verified in report 01. A nonce-based CSP remains the recommended
long-term fix (prior attempt failed hydration; see next.config.ts comment).
