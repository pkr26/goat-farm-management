# 09 — Detection Coverage & Purple Team (catalog 10.1–10.3)

Method: every attack from this campaign was executed against the live stack;
afterwards we mapped which attacks left **operator-visible evidence**. Sources:
`evidence/uvicorn_runtime.log`, `evidence/security_status_log_excerpt.txt`,
DB audit tables.

## 1. What the platform logged during the campaign (verified)

| Attack executed | Detection signal | Quality |
|---|---|---|
| Team worker creation | `goatfarm.audit security_event event='team.worker.create' …` (ids only) | Explicit structured event |
| Login/register throttling | `goatfarm.auth: login throttled (ip=…, scope=composite)` / `register throttled (key=…)` per trip | Explicit, scoped, greppable |
| Every request | access line with method/path/status/duration/request-id, control-char-escaped path | Good (66×401, 13×403, 22×429, 54×404 during the campaign, all present) |
| Simulation run floods | 429 access lines + instant rejection | Present as status codes only |
| Smuggling primitives | 400 access lines | Present as status codes only |

## 2. Detection gaps (findings)

| ID | Sev | Gap |
|---|---|---|
| DET-1 | Medium | **Refresh-token family revocation (theft detection) is silent.** The strongest security event the system can produce — proof someone replayed a stolen cookie — produces a 401 access line indistinguishable from an expired token. No `goatfarm.audit security_event` is emitted. During the live theft simulation, nothing in the log identified it. |
| DET-2 | Medium | **No aggregate security-event stream.** JWT-tamper rejects (7/7), cross-tenant probes (50/50), 403 permission denials ("logged denials" per `require_perm` docs — but only at INFO, same channel as everything else), DPR anomalies — all reconstructable only by post-hoc pattern analysis of access logs. A SIEM cannot alert on "401 burst with malformed Authorization" without custom parsing. |
| DET-3 | Low | Rate-limit trips are logged per-request but there is no periodic summary (attempts blocked per scope/IP/email) — a slow distributed stuffing campaign below per-request alert thresholds is invisible until a human aggregates. |
| DET-4 | Low | In-memory limiter state is invisible to central observability (restart wipes history; `/metrics` is disabled in production by policy). |
| DET-5 | Info | INJ-2 (report 03) directly undermines log integrity for any 500-path attack — forged lines can inject fake `security_event` records into the log stream. |

## 3. Incident-response tabletop (10.3) — key results

**Scenario A: stolen JWT signing keypair (jwtkeys volume leak).**
Response path exists technically (`GOATFARM_JWT_PREVIOUS_PUBLIC_KEY_PATHS`
rotation, max 3 previous keys; `token_version` global burn via forced password
resets; refresh-family revocation). **Gap: no runbook documents the sequence**
(key generation, kid change → all tokens invalid → users re-login; ordering
vs. deployment). Rotation has never been drilled.

**Scenario B: leaked DB dump.**
Password hashes Argon2id (resistant); refresh jtis useless without signing key;
idempotency HMAC records are keyed (AUTH-3: fast verifier if HMAC secret also
leaked — 7-day retention bounds it). Tombstone/delete scrubbing verified in
prior campaigns. Residual: INJ-1 shows attacker-controlled strings in dumps are
returned verbatim to future UIs (bidi/controls) — restore-then-serve re-exposes.

**Scenario C: trojaned image in GHCR.**
Digest-pinned pulls documented; SLSA provenance exists. Gap: no documented
verification step in the deploy runbook (cosign/provenance check is not in the
README's production section).

**Scenario D: ransomware on the backup host.**
GPG sign+encrypt means offsite objects are unreadable/unforgeable without the
signer key — but INFRA-5 (unscoped S3 credential) means the attacker can
**delete** the offsite tier. Object-lock/versioning on the bucket is the
missing control (policy, not code).

## 4. Recommendations (see remediation log)

1. Emit `security_event` records for: refresh family revocation (with family
   id, no PII), JWT verification failures (reason code), permission denials on
   mutating routes, and DPR downloads.
2. Periodic limiter-state summary log line (per scope: blocked counts, top IPs).
3. Write and drill the key-compromise runbook; document provenance verification
   in the deploy runbook; add S3 bucket object-lock/versioning requirement.
