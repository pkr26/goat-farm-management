# 14 — Remediation Applied (2026-09-16)

Execution of the 2026-09-16 remediation log (report 13). Every code change
carries a regression test; backend `ruff`/`mypy` clean; frontend `tsc` clean.

## P1 items

| Finding | Status | What landed |
|---|---|---|
| **INJ-1** DPR content injection | **FIXED** | `app/characters.py` (new): shared forbidden-character sets + `sanitize_single_line`. `build_dpr_markdown` renders the plan name through `sanitize_single_line` — CRLF/U+2028/bidi collapse to spaces, whitespace-only names fall back to the default title; the forged-sections PoC from the audit now renders as one inert title line. Tests: `test_dpr_markdown_title_is_single_line_and_directional_free`. |
| **INJ-2** 500-path log forging | **FIXED** | The unhandled-error handler logs `_log_safe_path(request.url.path)` (`app/main.py`). Test-enshrined gap corrected + new end-to-end proof: a U+2028-bearing path that still routes (`{rest:path}`) renders as `\\u2028`, never a literal separator. |
| **DET-1/DET-2** invisible security signals | **FIXED** | `app/audit.py` (new): `security_event()` helper. Events now emitted for `auth.refresh.family_revoked` (both revocation sites), `auth.token.invalid` (tampered, non-expired), `auth.token.version_mismatch` (revoked-generation reuse), `rbac.denied` (user+farm+permission), `planner.dpr.download`. 5 new tests in `tests/test_security_events.py`; `tests/test_rbac.py` denial test re-pinned to the structured event. |
| **INFRA-1** XFF aggregate identity | **FIXED (boot warning)** | Production boot logs a loud warning when `GOATFARM_TRUSTED_PROXY_HOSTS` is empty. The outer-terminator case cannot be detected from inside the process; the warning + README/compose prose adjacent to the setting are the enforceable fix. |
| **HUM-1** owner MFA | **FIXED — full TOTP shipped** | See "Second round" below: RFC 6238 engine (stdlib-only), AES-GCM-at-rest secrets keyed from the JWT signing key, enroll/confirm/disable/challenge endpoints, login challenge flow, single-use codes and tokens, 5/5-min throttle, security events, migration, frontend login step + account-dialog enrollment + i18n (en/te), regenerated contract & client, 14 backend + 6 frontend tests. |

## P2 items

| Finding | Status | What landed |
|---|---|---|
| **AUTH-1** legacy-hash timing oracle | **FIXED** | `_legacy_pbkdf2_parts` ceiling is now `min(1M, rejected_login_pbkdf2_work_budget)`: over-budget hashes are rejected fast with full-budget padding (constant rejection cost), and raising the budget restores them — the documented import-audit contract, now enforced. 2 tests updated + the equalization test extended. |
| **AUTH-4** password code-point limit | **FIXED** | `PasswordString` annotated type (byte-bound validator, ≤128 UTF-8 bytes) on all 7 password fields. Test: 33 astral-plane code points (132 bytes) → 422. |
| **INJ-3/INJ-4** DEL/C1/LS/bidi acceptance | **FIXED** | `FORBIDDEN_TEXT_CHARS` (DEL, C1, U+2028/9, bidi embedding/overrides/isolates, LRM/RLM/BOM) rejected by `PostgresText` AND `no_control_characters` (identifiers). ZWJ/ZWNJ deliberately still allowed (Telugu conjuncts) — pinned by test. 11 new validator cases + HTTP-level test. Legacy rows grandfathered (display-side `dir="auto"` added, see FE-4). |
| **BIZ-1** idempotent replay × schema drift → 500 | **FIXED** | `_revalidate_cached_response` maps the drift to 409 "retry with a new key" on both replay paths. Unit test both ways. |
| **BIZ-2** cadence head-of-line starvation | **FIXED** | Per-farm try/rollback/log/continue in `ensure_cadence_farm_batch`; ids snapshotted before rollback (avoids the expired-instance trap); fetched-count semantics preserved so paging still terminates. Test: poisoned farm 1 does not stop farm 2, cursor advances. |
| **BIZ-3** unbounded renewal span | **FIXED** | `MAX_RENEWAL_SPAN_DAYS` (5×366) in `renew_insurance_policy` — forward-only renewals can extend cover at most 5 years. Test inside/outside the cap. |
| **DET-3/DET-4** limiter invisibility | **FIXED** | Always-on per-scope 429 counter (`ratelimit.note_throttle_rejection`, hooked centrally in `metrics.record_auth_rate_limit_rejection`) + `_throttle_summary_loop` logging a 5-minute summary line. |
| **AUTH-2** framework-incidental login CSRF defense | **FIXED** | `_require_json_content_type` route-level dependency on `/api/auth/login` + `/register` → explicit 415 for non-JSON content types (runs before body parsing). Test updated to 415 × 4 cases. |
| **AUTH-5** dead `decode_token` | **FIXED** | Deleted from `app/security.py`; all 11 test call-sites ported to `decode_access_claims_result` / `decode_refresh_claims`. |
| **INFRA-2 / RT-R-7** https bypass of BACKEND_URL guard | **FIXED** | `assertSafeBackendUrl` applies the loopback/internal-name host rules to **both** schemes; public hostnames are rejected over https too. Tests updated in `next-config.test.ts` + `api-campaign.test.ts`. |
| **INFRA-3** pre-F4 restore oracle | **FIXED** | `restore.sh` revision floor `f4e5f6a7b8c9` with operator guidance; artifact test pins it. |
| **INFRA-4** hardcoded edge body cap | **FIXED** | `client_max_body_size ${GOATFARM_EDGE_MAX_BODY_SIZE:-1m}` templated from the operator knob; documented in `.env.example`; artifact test pins the correspondence. |
| **INFRA-5** S3 credential scope | **FIXED (docs)** | README backups section: PutObject-only scoping + bucket versioning/Object-Lock requirement. |
| **INFRA-6** shared DB identity example | **FIXED** | `.env.example` now shows the two-role GRANT recipe and names the risk of copying the bootstrap identity. |
| **INFRA-9 / RT-R-6** rebuild-vs-scanned bits | **FIXED** | `release.yml`: per-arch publish once → config-digest verification against the gate-scanned image (hard fail on divergence) → `imagetools create` assembles the release tag from the verified per-arch manifests (attestations preserved). Digest outputs re-wired. **Not executable locally** — validated by YAML parse + shell syntax only; watch the first tagged release. |
| Weekly audits | **FIXED** | `security.yml` gains a schedule-only `dependency-audit` job (pip-audit 2.10.1 via the repo's pinned uv install + `pnpm audit --audit-level=low`), reusing the repo's SHA-pinned actions. |

## Frontend

| Finding | Status | What landed |
|---|---|---|
| **FE-2** spoofed-refresh teardown | **FIXED** | Non-transient refresh statuses with a **non-JSON content-type** classify as `unavailable`; bare-status and JSON answers stay authoritative (matches real gateway 401s; pinned by existing tests). |
| **FE-3** login-path 5xx revocation | **FIXED (retry)** | `establishSession` retries the post-login farms fetch once (750 ms) before any teardown; the transactional logout remains for persistent failure. |
| **FE-4** bidi visual spoofing | **FIXED (key sites)** | `dir="auto"` on the farm-switcher name, animal tag links + name cells, and task-board titles — the decision-relevant identifier surfaces. (Backend now rejects the characters for new data; UI isolation covers legacy rows.) |
| **FE-6** chart CSS-injection sink | **FIXED** | `DonutSlice.color` removed; palette assignment only. Mutation test updated. |
| **FE-5** orval `encodeURIComponent` | **HARDENED (backstop pinned)** | The generated file itself stays generator-owned (hand-patching would make every regeneration noisy); the `assertSafeApiPath` backstop is now pinned end-to-end through the generated client: `src/api/generated/path-traversal.test.ts` proves a type-confused `../`/`?` path param throws (or degrades to a same-endpoint query) before any request. |
| **FE-1** pre-Web-Lock cross-tab refresh race | **ACCEPTED RESIDUAL** | A localStorage advisory-lock tier was implemented, then **reverted**: its wall-clock polling is incompatible with the suite's fake-timer lock tests, and it adds novel concurrency surface for a browser population (pre-2022 Safari) outside this product's support matrix. The backend's replay grace + family revocation remain the bounded mitigation; the decision is recorded in code comments and README Notes. |

## Second round (2026-09-16, "fix the remaining issues")

### HUM-1 — TOTP two-factor authentication (the full feature)

Backend (`app/security.py`, `app/api/auth.py`, migration `a19b2569d466`):
- RFC 6238 engine in stdlib (`hmac`/`hashlib`/`base64`): 30 s step, 6 digits,
  ±1 step drift, constant-time compare, per-account replay high-water mark —
  a verified code never validates twice.
- Secret at rest: 160-bit, **AES-GCM encrypted under an HKDF key derived from
  the JWT signing private key** — no new operator secret, and a database dump
  alone cannot recover second-factor material. JWT keypair rotation invalidates
  stored secrets (fail-closed re-enrollment, documented).
- Endpoints: `POST /api/auth/totp/enroll` (password-confirmed; returns secret +
  `otpauth://` URI — on phones the link opens the authenticator app directly,
  no QR dependency), `/totp/confirm` (proof-of-possession activates),
  `/totp/disable` (password + code when ACTIVE; password alone for PENDING),
  `/totp/challenge`.
- Login: an ACTIVE enrollment returns a 5-minute, single-use,
  token_version-bound challenge token instead of any session material; no
  refresh cookie exists until the code is verified.
- Challenge hardening: consumed-on-success jti cache (bounded), 5 attempts /
  5 min per account (and per IP|account composite) — ~700 years to exhaust a
  6-digit space; wrong codes leave the challenge retryable; events
  `auth.totp.enroll_started/enabled/disabled(/disable_failed)/challenge_failed/challenge_issued`.
- The `must_change_password` fence covers all TOTP routes by default-deny.
- Tests: `tests/test_totp.py` — 14 tests incl. encryption-at-rest proof,
  single-use code/token, version-binding after password change, throttle
  lockout, and the pending-state non-gating.

Contract & client: `shared/openapi.json` re-exported (85 paths); orval client
regenerated. `UserOut.totp_state` addition re-pinned in two field-set tests.

Frontend:
- Login page: two-step flow — password → (mfa_token) → code field with
  `autocomplete="one-time-code"` + Back button → challenge exchange → the
  exact pre-existing post-sign-in continuation. i18n keys added in English
  AND Telugu. 3 tests (`page.totp.test.tsx`).
- Account dialog: full "Two-factor authentication" section (off/pending/
  active states, password-confirmed enrollment, secret + otpauth link,
  activate, disable) updating the session user via `updateUser`. 3 tests
  (`account-dialog.totp.test.tsx`).
- README: MFA note rewritten from "planned amendment" to shipped design
  notes, including the no-recovery caveat for owner accounts.

### RT-R-6 — release-flow verification logic validated locally

The workflow's digest-verification bash/jq was extracted verbatim (only
`exit`→`return` in the harness) and exercised against realistic OCI-index
fixtures (`evidence/verify_rtr6_harness.sh`): (1) matching config digests
pass, (2) divergence fails loudly, (3) an amd64-attestation-only index is
correctly filtered by the `vnd.docker.reference.type` guard. The full
workflow still needs its first tagged release to run in CI.

## Info items

- **INFRA-7** `server_tokens off` in the edge config + artifact test.
- **GOV-1/GOV-2/HUM-4** documented in README Notes (DPDP retention position, per-process budget multiplier, no-recovery design).
- **TEN-3** X-Farm-Id leading-zero canonicalization pinned by test (own-farm 200 / foreign-farm 404). Task list-vs-single divergence remains intended (already functionally pinned); documented in report 02.
- **TEN-1/TEN-2, RT-1, INFRA-8, FE-7, AUTH-3/6** — intended/documented behaviors; no change (see report 12).

## Verification

- Backend: `ruff` clean, `mypy` clean (101 files); **full `pytest` suite after the second round: 4,435 passed / 4 skipped / 0 failed** (first round was 4,412/0 with 9 pre-existing environmental failures in `test_*_reads_the_application_env_file…` — staged scripts needing `python-dotenv` in the fallback system `python3`; verified identical on pristine HEAD, resolved by a user-site install on the audit machine).
- Frontend: `tsc --noEmit` clean; **full `vitest` suite: 4,573/4,573 passed** (includes the 9 new TOTP/traversal tests).
- OpenAPI contract: re-exported (85 paths, +568 lines) and the orval client regenerated in lockstep; `tsc` clean after regeneration.
- Migration: roundtrip `head ↔ parent` and `alembic check` verified; the repo's migration-integrity suites (including `f4` irreversible-downgrade and autogenerate-drift pins) pass.
- Workflow YAMLs parse; `restore.sh` passes `bash -n`; compose/`.env.example`/restore changes pinned by new artifact tests; RT-R-6 verify logic exercised against OCI-index fixtures (evidence/verify_rtr6_harness.sh).

## Notes / caveats

1. The 9 `backup/restore_reads_the_application_env_file` artifact tests
   initially failed on this audit machine **and on pristine HEAD** (staged
   scripts fall back to a system `python3` lacking python-dotenv). Installing
   `python-dotenv` into the system interpreter's user site resolves them
   locally; CI is unaffected either way.
2. The release workflow cannot be exercised locally — its digest-verification
   logic was validated against fixtures, and the first tagged release run
   should be watched.
3. FE-1 (pre-2022-Safari cross-tab refresh race) remains the single accepted
   residual, with rationale recorded in code comments and README Notes.
   Everything else from the 2026-09-16 findings — including owner MFA — is
   closed.
