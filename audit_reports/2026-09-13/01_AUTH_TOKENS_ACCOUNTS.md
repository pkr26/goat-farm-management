# Red Team Audit — Part A: Authentication, JWT, Tokens, Accounts (units A1–A13)

Date: 2026-09-13
Scope: `backend/app/api/auth.py`, `backend/app/security.py`, `backend/app/deps.py`,
`backend/app/models/core.py`, `backend/app/ratelimit.py`, `backend/app/core/config.py`
(auth settings), `backend/app/main.py` (lifespan key validation, middleware wiring),
`backend/app/schemas/auth.py` (input/response models).
Intent reference tests: `backend/tests/test_auth_bugs.py`, `test_auth_extended.py`,
`test_auth_token_version_races.py`, `test_jwt_rotation.py`, `test_account_tombstones.py`,
`test_adversarial.py`.

## Methodology

Every unit was read end-to-end and attacked as an adversary would: authorization
bypass, tenant isolation, race/concurrency, replay, enumeration/timing oracles,
resource exhaustion, and configuration abuse. Enforcement was verified in code —
comments, docstrings, and test names were treated as claims, not evidence. Branch
coverage was completed manually for the login/refresh/logout state machines and the
JWT keyring lifecycle. Claim-shape hypotheses not covered by the existing suite
(`sub` = `"0"`/`"+1"`/`"01"`/Arabic digits, `ver` = bool/float/negative, `exp` =
float/bool, oversized/empty `jti`, leeway boundary) were verified dynamically
against the real decoder in an isolated temp key environment (fastapi 0.141.1,
PyJWT 2.13.0). A static AST sweep confirmed no API route handler lacks the auth
dependency chain (the two `team.py` hits use an auth-gated `prepared` dependency).
All file:line references below were checked against the working tree on 2026-09-13.

Severity scale: Critical = auth bypass / cross-tenant / unauth data access.
High = conditional authz bypass, session hijack, account takeover, auth-system DoS.
Medium = defense-in-depth gaps, info disclosure, resource abuse. Low = hardening.

## Findings table

| ID | Severity | Unit | Title |
|----|----------|------|-------|
| RT-A-1 | Medium | A2 | Unauthenticated sustained per-email login lockout — targeted account login DoS (documented tradeoff) |
| RT-A-2 | Low | A1 | Register pays the full Argon2 hash before consulting the existing-email probe limiter |
| RT-A-3 | Low | A13 | Production JWT key file permissions are never validated at boot |
| RT-A-4 | Info | A1 | Register's explicit 400 duplicate-email response is an account-enumeration oracle (accepted design) |
| RT-A-5 | Info | A7 | Bearer-only logout revokes every session — one-shot "logout everywhere" DoS from a stolen access token (documented) |
| RT-A-6 | Info | A5 | ±3 s grace-window reconstruction hands the successor cookie to an in-window replay (bounded design tradeoff) |
| RT-A-7 | Info | A6/M2 | Unique-cookie spray appends per-cookie buckets while already IP-throttled — pressures the shared limiter's key ceiling (documented tradeoff) |
| RT-A-8 | Info | A2/A1 | `login-ip` never reset on success; register counts every attempt per IP — shared-NAT collateral throttling (documented) |
| RT-A-9 | Info | A4 | 60 s leeway keeps access tokens valid past `exp`; refresh session lifetime is exact (bounded, documented) |

No Critical or High findings. Every candidate auth bypass traced in this audit was
rejected by the code paths detailed in the per-unit notes below.

---

## RT-A-1 — Medium — A2 — Unauthenticated sustained per-email login lockout (targeted login DoS)

**Evidence.** `backend/app/api/auth.py:224-259` — `_login_blocked` consults three
buckets; the IP-agnostic `login-email` bucket blocks at
`attempts * EMAIL_LIMIT_MULTIPLIER` = 30 failures / 300 s
(`auth.py:99-100`, `core/config.py:245-246`). `_record_login_failure`
(`auth.py:246-259`) charges `login-email` on every rejection and every
cancellation-before-acceptance (`auth.py:866-869`). `_reset_login_failures`
(`auth.py:262-267`) only runs after a *successful* login, which is unreachable
while blocked: `login()` (`auth.py:752-753`) returns 429 before any Argon2 work,
even with the correct password.

**Exploit sketch.** Attacker picks a known victim email. Each failed login from one
source IP charges the composite `(ip,email)` bucket (limit 10) and `login-email`
(limit 30). From three or more source addresses (proxies/IPv6 rotation — each
address contributes up to 10 composite failures and 100 `login-ip` budget), the
attacker lands 30 in-window `login-email` failures. From that moment *every* login
for that email — any IP, correct password included — returns 429
(`TOO_MANY_ATTEMPTS`). Keeping ≥30 failures inside the sliding window (a trickle of
≥30 requests / 5 min across the same addresses) locks the victim out indefinitely;
there is no self-service unlock, no lockout notification, and no success-exempt
path. The window is sliding, so it never drains while the spray continues.

**Impact.** Targeted, credential-less, sustained denial of the victim's ability to
log in (account availability). Does not affect other accounts or the auth system as
a whole; not a compromise of confidentiality or integrity.

**Mitigation context / suggested fix.** This is a deliberate documented tradeoff
(`auth.py:94-98`: the per-email ceiling exists to stop "distributed guessing
against ONE account from rotating addresses"). Standard mitigations that preserve
the anti-brute-force property while removing the DoS: (a) treat the per-email
ceiling as soft — when it trips, require a successful Argon2 verify anyway and only
reject on failure (attacker burning Argon2 budget per probe keeps the brute-force
cost, correct-password victims log in); or (b) add exponential backoff with a
CAPTCHA/step-up after repeated blocks; or (c) alert the account holder. If the
tradeoff is reaffirmed, record it as accepted risk.

## RT-A-2 — Low — A1 — Argon2 hash runs before the existing-email probe limiter

**Evidence.** `backend/app/api/auth.py:693-728` — order of operations in
`register()`: per-IP `_rate_limited("register", …)` check (line 690-692) →
password policy (693-695) → `await hash_password_async(...)` (line 704, full 64 MiB
Argon2id hash) → `register_email_limiter.is_blocked(...)` probe check (lines
717-724) → existence SELECT (725).

**Exploit sketch.** An attacker probing one existing email with IP rotation: after
10 probes the email's probe bucket is full, but each subsequent request still pays
the full memory-hard hash before the 429. The per-IP register ceiling (10/300 s)
does not apply once addresses rotate. Each request therefore converts ~100 ms of
Argon2 (and one of only 2 global pool slots, `core/config.py:217`) into a 429 —
the attacker amplifies contention on the shared pool that logins depend on
(collateral 429 "Password service is busy" for other users).

**Impact.** Resource abuse / CPU amplification on the register endpoint; bounded
by the non-queuing worker pool (no queue growth, no memory growth). Defense
oracle note: moving the probe check ahead of the hash leaks nothing new — the
probe bucket is only populated for existing emails, but existence is *already*
directly disclosed by the 400 (see RT-A-4), and `is_blocked` is allocation-free
for unseen keys (`ratelimit.py:285-299`), so fresh-address registrations would
still never allocate.

**Suggested fix.** Hoist the `register-email` `is_blocked` check above
`hash_password_async` (order swap only; charging stays on the existence/
IntegrityError paths exactly as today).

## RT-A-3 — Low — A13 — Production JWT key file permissions never validated

**Evidence.** Dev generation enforces 0600 on the private PEM
(`security.py:378-395`, `_write_atomic(mode=0o600)`, `NamedTemporaryFile` inode
mode) and 0700 on the default key dir (`security.py:445-454`). The production
load path `_load_jwt_keyring_locked` / `_read_pinned_key_text`
(`security.py:300-335, 527-594`) validates type, size, key strength, pair match,
and kid uniqueness — but never `st_mode` / owner of the mounted files. Lifespan
calls `validate_jwt_keypair()` (`main.py:341`).

**Exploit sketch.** Not remotely exploitable — a deployment/misconfiguration gap:
an operator who mounts the private key 0644 (common default for ConfigMaps/bind
mounts) boots cleanly; any local user on the API host can then read the RS256
signing key and forge arbitrary access/refresh tokens (silent, total auth
compromise).

**Suggested fix.** In production mode only, refuse to boot (or warn loudly) when
the private key file mode allows group/other read or the file is not owned by the
service user. Symlink acceptance stays as-is — the K8s rationale in
`security.py:300-306` is sound, and the pinned-fd fstat comparison
(`security.py:320-331`) already detects in-read replacement.

## RT-A-4 — Info — A1 — Duplicate-email 400 enumeration oracle (accepted design)

`auth.py:85, 725-728`: a probe of an existing email returns 400
"That email is already registered." — a direct existence oracle. The timing half
is properly equalized (hash before the existence check, `auth.py:696-704`), and
repeated probing of one email is throttled by the separate
`register_email_limiter` (`auth.py:90, 708-724`) charged only when the email
exists (fresh-address attempts never charge, so an IP-rotating attacker cannot
lock a legitimate registrant out of their own address — verified
`auth.py:741-744` resets the probe bucket once the account exists). Without email
verification there is no accept-and-notify alternative; the code documents the
tradeoff explicitly. Observation only.

## RT-A-5 — Info — A7 — Bearer-only logout = logout everywhere (documented)

`auth.py:1143-1161`: with no valid cookie, a current bearer token triggers
`revoke_user_sessions` (all families) plus a `token_version` bump — a stolen
30-minute access token can force a global logout of the victim. Assessed and
accepted: the move is one-shot (the bump also kills the attacker's token),
requires a token that already grants strictly more harmful capability for its
TTL, and the code documents why a version-only revocation would be reversible via
surviving refresh families (`auth.py:1153-1158`). Duplicate logouts do not
re-advance the counter (stale bearer → no bump; verified branches at
`auth.py:1098-1161`, regression `test_logout_with_compacted_predecessor_revokes_live_family_once`).

## RT-A-6 — Info — A5 — Grace-window successor reconstruction is a bounded "shared knowledge" window

`auth.py:951-999`: replaying a consumed token within ±3 s returns the
reconstructed successor (same jti, deterministic RS256) without consuming it.
A thief who replays within 3 s of the victim's rotation silently receives the
successor cookie; theft detection fires only for replays outside the window.
Riding the chain requires hitting every rotation within 3 s: passive polling
*rotates* the token itself (consuming it and arming detection on the victim's
next use >3 s later), so only an adversary who can observe rotations in real time
(a MITM, who already has persistent access) benefits. The window is
config-bounded (`refresh_reuse_grace_seconds` ≤ 30, default 3,
`core/config.py:206`) and symmetric to tolerate small backward clock steps.
Design tradeoff, correctly bounded; no change recommended.

## RT-A-7 — Info — A6/M2 — Per-cookie buckets appended while already IP-throttled

`auth.py:651-682` (`_raise_invalid_refresh`): the per-cookie
`refresh-preverify` bucket is recorded *before* the per-IP `refresh-invalid`
check, so requests that will end as 429 still append a unique key to the shared
`auth_limiter` map. ~50 k unique garbage cookies fills the 50 k key ceiling and
starts evicting *cold* (below-threshold) buckets of unrelated scopes — e.g.
partially accumulated login-failure counts. Mitigations verified: tiered LRU
protects at-threshold buckets (`ratelimit.py:236-267`), each sprayed request
costs ≤4 RSA verifies (no-kid fallback) before failing, and the per-IP ledger
429s the address at 10× attempts. Degrades only sub-threshold counters, needs
tens of thousands of requests, and the module documents the tradeoff
(`ratelimit.py:92-99`). Cross-reference for the Part M2 report.

## RT-A-8 — Info — A2/A1 — Shared-NAT collateral throttling (documented)

`auth.py:262-267`: the per-IP spray bucket (`login-ip`, 100/window) is never
reset by anyone's success; `register` counts *every* attempt per IP including
policy-rejected bodies (`auth.py:689-692`). A busy NAT can therefore see
collateral 429s on login/register. Deliberate anti-spray design, documented at
`auth.py:262-265` and `core/config.py:240-242`; refresh-side collateral was
already fixed by keying pre-verification on the presented cookie, not the IP
(`auth.py:461-488`, regression `test_garbage_refreshes_do_not_lock_out_a_co_located_session`).

## RT-A-9 — Info — A4 — 60 s expiry leeway on access tokens

`security.py:745-793`: access tokens are accepted up to 60 s past `exp`
(`expired = expires_at <= now - 60`); verified dynamically (−30 s accepted,
−120 s rejected). Refresh-side session lifetime is checked exactly with no leeway
(`auth.py:888-893, 917-923, 943-948`). Standard multi-instance clock-skew
absorption, bounded and documented; the signed-value/persisted-value ordering
also prevents an in-flight boundary crossing from being misclassified as replay.

---

## Per-unit attacked & held notes

### A1 Registration — held
- **Concurrent duplicate-email race**: pre-check SELECT then `flush()` inside
  try/IntegrityError → rollback → identical 400 (`auth.py:725-740`). No 500 path;
  the loser also charges the probe bucket. Commit happens after token mint; a
  failed commit discards the response object, so no cookie/access token is ever
  delivered for an uncommitted user (`auth.py:745-747`; FastAPI discards the
  injected `Response` mutations on exception).
- **Email normalization**: single shared `EmailMixin` (strip + lower + RFC-ish
  ASCII regex, ≤254) used by `RegisterIn`, `LoginIn`, and team provisioning
  (`schemas/auth.py:28-55`, `schemas/team.py:9`) — register, login, lookup and
  worker provisioning all compare the identical normalized string. Unicode
  homoglyphs are impossible post-lower (regex is ASCII-only; exotic case-folds
  like U+212A normalize *into* canonical ASCII, so they alias rather than
  duplicate). `email_probe_key` re-lower is a no-op. No login mismatch or
  duplicate-account vector found.
- **Rate-limit key derivation**: `_client_key` uses `request.client.host`
  (`auth.py:156-159`); `ProxyHeadersMiddleware` is installed only when
  `trusted_proxy_hosts` is non-empty (`main.py:609-614`), and that setting
  rejects hostnames and `0.0.0.0/0`-style wildcards at boot
  (`core/config.py:442-477`). Default (empty) = XFF ignored, not spoofable.
- **Password policy edges**: whitespace-only rejected after min-length
  (`security.py:135-142`); exactly-min accepted; >128 rejected at the schema
  (`schemas/auth.py:49`) — far under any Argon2 input limit; strict
  `extra=forbid` + pydantic v2 non-coercing `str` (no type-confusion 422/500).

### A2 Login — held
- **Enumeration (response/timing)**: unknown email verifies against a real
  Argon2id dummy hash (boot-primed, `security.py:153-159`, `main.py:343-344`);
  every rejection makes exactly two bounded executor submissions and pays one
  Argon2 + one fixed PBKDF2 budget regardless of account existence or hash
  generation (`auth.py:779-805`, `security.py:200-240`) — legacy rows spend part
  of the PBKDF2 budget on their real hash and the remainder on padding, so the
  *totals* match. Identical 401 detail for unknown email / wrong password /
  garbage stored hash (`InvalidHashError` handled, `security.py:254-259`).
- **token_version after Argon2**: yes, by design; the post-verify locked reload
  compares `token_version` and exact-compares the hash snapshot, distinguishing a
  genuine mid-flight credential change (401 + ledger charge) from a concurrent
  benign legacy→Argon2 rehash (re-verify, success, no charge)
  (`auth.py:819-858`; regressions in `test_auth_bugs.py:297-404`).
- **Login + password-change race**: the User row is re-locked
  (`with_for_update` + `populate_existing`), so a reset/change that won mid-Argon2
  is rejected generically; a stale-hash false 401 is impossible via the re-verify
  path, and no wrong-success path exists (re-verify uses the reloaded hash).
- **Cancellation-before-acceptance** recorded as a failure
  (`auth.py:866-869`), and the reservation holds admission until native Argon2
  work is idle (`auth.py:342-401`) — cancel/retry cannot hold every global slot.
- **Worker-pool lock discipline**: the only path holding a User lock while
  requesting Argon2 capacity is the rare cross-worker re-verify; no path holds
  Argon2 capacity while requesting a User lock (all change/delete/reset paths
  finish password work before locking) — no circular wait.
- Victim-lockout residual risk filed as RT-A-1; NAT collateral as RT-A-8.

### A3 JWT issuance — held
- `extra_claims` reserved-collision raises (`security.py:647-652`); internal
  callers only add `ver`/`fid`; no route accepts caller-supplied claims.
- `kid` derived from SHA-256 of the SPKI DER (`security.py:512-515`) —
  deterministic, non-secret, set only by the issuer.

### A4 JWT verification & keyring — held
- **alg pinned twice**: untrusted-header `alg != RS256` → reject
  (`security.py:723-724`) *and* `algorithms=[RS256]` in the decode call (749);
  `none`/HS256 confusion covered by tests and re-verified.
- **kid trust**: dictionary lookup only; unknown kid never falls back across the
  keyring (`security.py:728-735`, `test_jwt_rotation.py:130-156`); non-string kid
  rejected.
- **no-kid fallback**: bounded to active + ≤3 previous keys (`config.py:34`).
- **`verify_exp=False` traced end-to-end**: every consumer of
  `_decode_payload_result` handles `expired` — `_decode_payload` returns None
  (→ `decode_token`, `decode_refresh_claims` reject);
  `decode_access_claims_result` returns `claims=None, expired=True` (→
  `current_user` 401 without ledger charge, `deps.py:153-163`; logout skips the
  charge, `auth.py:1054-1064`); `_refresh_token_expired_but_genuine` uses it
  purely for the returning-client exemption. No path accepts an expired token
  (leeway note RT-A-9).
- **`sub` canonicalism**: dynamically verified rejections for `"0"`, `"+1"`,
  `"01"`, `"1.0"`, `"-5"`, `"1e3"`, Arabic-Indic digits (`security.py:769-784`).
- **iss/aud** bound at issuance and verified on every decode (tested).
- **`ver` shape**: bool rejected, float rejected, negative rejected, missing →
  reject (`security.py:826-827`; dynamic probe; `ver="0"` string tested).

### A5 Refresh rotation — held
- **TOCTOU**: session row locked `FOR UPDATE` before the `consumed_at` check;
  expiry re-checked three times (post-decode, post-User-lock, post-session-lock)
  with the signed boundary re-validated so a boundary race never degrades into a
  false theft classification (`auth.py:888-948`).
- **Replay/theft**: consumed/revoked/absent-row presentation revokes the signed
  family and charges the attacker ledger; compaction-safe via the signed `fid`.
- **Grace window**: successor reconstruction requires a live, unconsumed,
  unrevoked successor and a symmetric ±3 s elapsed; anything else revokes
  (residual window noted RT-A-6).
- **Family eviction DoS**: new families only created on password-verified
  issuance (login/change-password); 10-family ceiling with id-ordered eviction —
  cannot be triggered without the credential.
- **Lock order User→RefreshSession**: verified in every mutation site — refresh
  (`auth.py:897-916`), logout (`auth.py:1075-1096`), change-password
  (`auth.py:1234-1260`), delete (`auth.py:1414-1449`), login (re-lock at
  `auth.py:819-830` before `_issue_tokens`), team owner-reset
  (`api/team.py:1105→1117`). No inversion found.
- **Session-history compaction**: presented row excluded from eviction
  (`id != preserve_session_id`), ordering by row id not wall clock, >4096
  overflow → explicit 409 instead of unbounded work (`auth.py:543-575`).

### A6 Refresh cookie handling — held
- **Duplicate cookies**: raw header recount rejects >1 exact-name pair
  (`auth.py:432-453`); malformed pairs fail closed to "absent"; Starlette's
  collapsed dict is only read after the count check. Cookie names are
  case-sensitive by RFC and matched exactly.
- **Origin guard**: exact set membership (not substring) against canonicalized
  CORS origins + the request's own (TrustedHost-validated) base origin
  (`auth.py:186-212`); `Origin: null` and malformed Referer ports fail closed;
  `Sec-Fetch-Site: cross-site` always rejects; missing-all-headers is the
  documented non-browser compatibility path (no ambient cookie authority there).
  Sec-Fetch-Site is a browser-forbidden header; cross-site POSTs carry Origin per
  the fetch spec. `__Host-` upgrade + `cookie_secure` enforced in production by
  the settings validator (`core/config.py:496-505, 542-548`).
- Pre-verify budget keyed `sha256(cookie)` — a fresh garbage cookie gets a fresh
  bucket (RT-A-7 notes the residual), but each request still pays ≤4 RSA
  verifies and the per-IP ledger 429s the address at 10×; valid cookies are
  never judged by shared-IP history.

### A7 Logout — held
- **Identity matrix**: cookie/bearer mismatch rejected 401 before any lock or
  mutation (`auth.py:1030-1039`); duplicate Authorization headers rejected via
  `single_bearer_token` (`deps.py:42-60` — exactly one header, exact `Bearer `
  prefix, no inner whitespace; HTTPBearer's raw scheme re-checked against
  canonical casing, `deps.py:145-151`); bearer-only and expired-cookie paths
  analyzed (RT-A-5); expired/invalid cookie → family untouched (cannot name one —
  decode failed); forged material charged to its own ledgers; the
  authentic-but-expired exemption verified against both refresh and access
  branches (regressions `test_auth_bugs.py:424-499`).
- **CSRF on logout**: same origin guard as refresh (`auth.py:1010`).
- **Idempotency**: revoked/compacted-predecessor branches avoid double
  token_version advancement; a stale bearer with a confirming cookie bumps once.

### A8 Change password — held
- **Budgets**: composite (10/window) + shared per-account ceiling
  `account-password-confirm` (30/window) genuinely shared with account-delete —
  both routes pass the same `ACCOUNT_PASSWORD_CONFIRM_ACCOUNT_SCOPE` literal
  (`auth.py:1187, 1377`), so endpoint-switching cannot double the guessing rate;
  the Argon2 *reservation* is likewise shared (`ACCOUNT_PASSWORD_RESERVATION_SCOPE`,
  `auth.py:1190, 1380`) preventing two slots per account.
- **Revocation completeness**: `revoke_user_sessions` kills every live family,
  `token_version += 1`, fresh pair in a new family with the new version, cookie
  re-set, `must_change_password` cleared (`auth.py:1254-1265`).
- **Revalidation**: post-Argon2 locked reload re-checks `token_version`
  (token-version-only gate; the benign-rehash byte race is forgiven by design —
  regression-tested); no stale-version write path exists.
- Note: the `new == current` 400 gives no oracle beyond what a successful change
  already proves (attacker must hold a valid access token to reach the route).

### A9 Account export — held
- Identity + owned-farm metadata + own memberships/role names only; no farm
  domain records, no password material, no `token_version` in `UserOut`
  (`schemas/auth.py:58-71, 85-114`). Affiliation overflow → 409 on both legs
  with correct arithmetic (`auth.py:1285-1322`); cap shared with the farm-list
  path. Fenced under `must_change_password` (not in the GET allowlist).

### A10 Account deletion — held
- Password confirmation under the shared two-layer budget (above); random
  tombstone hash + `deleted-…@deleted.invalid` email + name scrub satisfy the
  CHECK constraint (`models/core.py:35-38`); login with the old credential →
  generic 401 via the `deleted_at.is_(None)` filter (`auth.py:769-776`).
- **Owns-farm refusal race**: both `create_farm` and `delete_account` take the
  User row `FOR UPDATE` first (`auth.py:1509-1516, 1414-1421`), so the pair
  serializes: delete-then-create → creator sees the tombstone (401);
  create-then-delete → deletion's Farm probe (READ COMMITTED, post-lock) sees
  the committed farm (409). No bypass.
- **Session purge**: all refresh rows deleted, `token_version` bump kills live
  access tokens; memberships deactivated asynchronously by the bounded loop
  (`deps.py:290-343`) — `deleted_at` is the synchronous barrier everywhere
  (`deps.py:166`, refresh/login/logout all filter it).
- Email reuse after delete re-registers cleanly as a *new* identity (uuid-bearing
  tombstone guarantees no unique collision).

### A11 `must_change_password` fence — held
- Full allowlist enumerated (`deps.py:201-222`): always —
  `register/login/refresh/logout/me/change-password` (the first two never pass
  through `current_user` at all); GET-only — `permissions`, `farms`.
  `POST /api/auth/farms`, `DELETE /api/auth/account`, `GET /api/auth/account/export`
  are all fenced (verified by absence). No other router route is reachable: the
  fence sits inside `current_user`, which every non-auth route requires (AST
  sweep of all 15 routers found no unauthenticated mutating endpoint).
  Fenced workers can refresh indefinitely — required to keep the shell alive to
  present the banner; the only data reachable is their own identity and their
  farm-name/role selector (by design). Path matching is exact string equality on
  the routed path; FastAPI normalization offers no bypass spelling.

### A12 `token_version` revocation check — held
- Checked on every authenticated request (`deps.py:168-169`) with the signed
  value stashed outside the ORM identity map (`deps.py:178`); every *mutating*
  path re-validates under lock after acquiring dependent locks
  (`_pin_authenticated_user`, `deps.py:426-447`; auth routes' own locked
  reloads; refresh reads the version under the User lock). In-flight GETs during
  a revocation may complete — the documented, bounded residual. `ver` bool/float/
  negative/missing all rejected (A4).

### A13 JWT key management — held
- **Dev auto-keygen race**: per-process RLock + cross-process flock on a
  0600 `O_NOFOLLOW` regular lock file, with inode re-validation after acquisition
  and after generation; the loser re-checks pair validity under the lock and
  adopts the winner's pair (`security.py:427-502`). Torn-pair repair only for
  generator-managed pairs (marker file); pre-provisioned pairs without the marker
  fail closed in the keyring validator instead of being overwritten.
- **Atomic write**: mkstemp (O_EXCL, 0600 inode) + fsync + chmod-after-write +
  `os.replace` — no half-written key observable; `os.replace` replaces a symlink
  target rather than following it.
- **Reads**: pinned fd, regular-file + 1 MiB size bounds, before/after
  fstat(dev/ino/size/mtime/ctime) comparison, UTF-8 validation
  (`security.py:300-335`); O_NONBLOCK rejects a raced FIFO without hanging.
- **Production validation**: missing files refuse to boot; non-RSA, <2048-bit,
  mismatched pair, non-RSA/weak/duplicate-kid previous keys all rejected before
  the keyring is published (`security.py:527-594`, `main.py:338-341`);
  `load_pem_public_key` structurally rejects a private PEM supplied as a
  previous-key path. Previous keys are verification-only (never used for
  signing), bounded to 3 by settings validation, and must have unique kids.
- Keyring reload only on configured-path change (read-once snapshot);
  permissions gap filed as RT-A-3.

### Cross-cutting sweeps — held
- **Secret logging**: all log lines in `auth.py`/`security.py`/`deps.py` emit
  only IPs, emails (throttle keys), user ids, scopes and family ids — never
  tokens, cookies, passwords, hashes, or PEM material; the pbkdf2 anomaly log
  prints only iteration counts (`security.py:179-186`).
- **Response models**: `UserOut`/`TokenOut`/export schemas carry no
  `password_hash`, no `token_version`, no session internals
  (`schemas/auth.py:58-114`); 422 handler strips reflected inputs
  (`main.py:440-456`).
- **Error-class distinction**: unknown email vs wrong password share one detail
  string and equalized work; garbage stored hashes never 500; throttles share
  one generic message with `Retry-After`.

## Unconfirmed suspicions — explicitly not findings

- **`exp` accepted as a JSON float** (`int()` truncation in
  `security.py:789-792`, dynamically observed): only reachable with the RS256
  private key in hand — our issuer always writes integer `exp`. No impact
  without a signing-key compromise; noted for completeness.
- **Kelvin-sign and similar unicode case-folds normalizing into canonical ASCII
  emails**: produces aliasing to the same account on both register and login
  (single shared mixin), never a duplicate account or a login mismatch. Verified
  reasoning, not runtime-tested against Postgres collation.
