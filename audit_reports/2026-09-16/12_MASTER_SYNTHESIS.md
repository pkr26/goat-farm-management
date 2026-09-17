# 12 — Master Synthesis (2026-09-16 campaign)

Full-catalog execution: 6 deep static falsification audits + 8 live attack
phases (≈140 executed attacks/probes) + backup/restore live drill + detection,
human-layer, and governance assessments. Prior campaigns' remediations were
specifically re-falsified; every one held.

## Verdict

**0 Critical / 0 High (technical) / 3 Medium / 14 Low / 20 Info findings.**
The platform's core barriers — tenant isolation, authentication, request
framing, money integrity — held under live attack and code falsification. The
highest-risk item in the overall risk picture is human-layer (HUM-1: password-
only owner god-mode, no MFA), not a code defect.

## All findings by severity

### Medium

| ID | Finding | Report |
|---|---|---|
| INJ-1 | DPR loan-document content injection via plan name (live-confirmed; fake finance tables, formula cells, CRLF lines; headers safe) | 03 |
| INJ-2 | Unhandled-500 log line uses raw path → log forging via %0A (RT-M-4 incomplete; test-enshrined) | 03 |
| INFRA-1 | Outer-terminator XFF misconfig collapses per-IP rate limiting deployment-wide (documented, unenforced) | 08 |
| DET-1 | Refresh-family revocation (theft detection) emits no security event — silent | 09 |
| DET-2 | No aggregate security-event stream (JWT tamper, tenant probes, 403s are status codes only) | 09 |
| HUM-2/HUM-3 → see 10 | (contextual Mediums in the human layer) | 10 |

### Low

AUTH-1 legacy-hash timing oracle · FE-1 pre-Web-Locks refresh race · FE-2
spoofed-status teardown · FE-3 login-path 5xx revocation · FE-4/INJ-4 bidi
spoofing (UI + tags) · INJ-3 C1/DEL/U+2028 in prose fields · BIZ-1 idempotent
replay × schema drift 500s · BIZ-2 cadence head-of-line blocking · INFRA-2
RT-R-7 `https:` bypass of backend-URL guard · INFRA-3 pre-F4 restore revision
floor · INFRA-4 body-size comment/code drift · INFRA-5 S3 credential scope ·
INFRA-6 migration identity not least-privilege in example · INFRA-9 RT-R-6
rebuild-vs-scanned-bits · DET-3/4 limiter observability.

### Info

AUTH-2..6 · TEN-1..3 · BIZ-3 · FE-5..7 · RT-1 absolute-form targets · INFRA-7
nginx version disclosure · INFRA-8 public /readyz · GOV-1/2 · HUM-4.

## Defense scorecard (live)

| Barrier | Attacks | Held |
|---|---|---|
| Cross-tenant isolation | 50 + 9 + 12 header cases | 100% (uniform 404, zero leakage) |
| JWT verification | 7 variants | 100% (401) |
| Refresh rotation/theft | 6 scenarios | 100% (family revocation exact) |
| Worker fencing / token_version | 15 probes | 100% |
| Request framing (smuggling) | 10 primitives | 9 rejected + 1 Info (absolute-form) |
| Body/target size caps | 4 | 100% (incl. chunked-stream counting) |
| Rate limiting | 30+ attempts | Held; normalized keys; documented ceilings |
| State machine / money / idempotency | 30+ probes | 100% |
| Backup/restore integrity | 6 scenarios | 100% (all refusals correct) |

## What actually needs fixing (priority order)

1. **MFA for owners** (or any second factor on god-mode accounts) — HUM-1.
2. INJ-1 DPR sanitization (strip/escape newlines+formulas in plan names/notes
   for the DPR; or render title from a sanitized slug).
3. INJ-2 + DET-1/2: sanitize the 500-path log line; emit security events for
   family revocation, JWT failures, permission denials.
4. INFRA-1: convert the trusted-proxy/XFF guidance into a boot-time or
   health-check assertion.
5. Unicode hardening (INJ-3/4/FE-4): extend `PostgresText`/identifier checks
   to reject DEL/C1/U+2028/U+202E; add `dir="auto"` isolation in the UI.
6. The Low tail (see remediation log 13 for the full list).

## Comparison to prior campaigns

2026-09-04 (11 reports) and 2026-09-13 (18 reports, 135 units) were code-level.
This campaign adds: first live-fire verification (their conclusions replicate
under real HTTP attack), first backup/restore execution drill, first detection-
coverage and human-layer analyses, and 3 new Medium + ~15 new Low/Info findings
concentrated exactly where prior campaigns couldn't look (document rendering,
log integrity edge, observability, deployment drift).
