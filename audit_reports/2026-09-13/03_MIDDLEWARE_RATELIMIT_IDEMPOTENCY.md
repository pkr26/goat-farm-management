# Red Team Audit — Part M1–M4: Outer Shell (Middleware, Rate Limiting, Argon2 Pool, Idempotency)

Date: 2026-09-13 · Auditor: red-team pass 3 · Scope units: **M1** middleware stack, **M2** rate-limiter core, **M3** Argon2 worker pool, **M4** idempotency service (per `00_RED_TEAM_AUDIT_SCOPE.md`).

## Methodology

Every implementation was read end-to-end and attacked as an adversary would; runtime semantics of
third-party layers were verified against the **actually installed** versions (starlette 0.48.0,
uvicorn 0.37.0, h11 as bundled), not assumed from memory. Middleware ordering was re-derived from
Starlette's `build_middleware_stack` rules against the `add_middleware` call sequence in
`create_app()`. Argon2/PBKDF2 costs were **measured on the audit machine** with the project's own
parameters (`t=3, m=64MiB, p=4`, PBKDF2 50k) to ground the DoS-budget analysis. Replay safety of
every non-idempotent mutation was traced into the service layer to its state preconditions and row
locks. Findings are code-evidenced with file:line; unconfirmed items are explicitly marked.

Primary files: `backend/app/main.py`, `backend/app/ratelimit.py`, `backend/app/security.py`,
`backend/app/services/idempotency.py`, `backend/app/models/idempotency.py`,
`backend/app/core/config.py`, `backend/app/api/_shared.py`, call sites in
`backend/app/api/auth.py`, `team.py`, `feeding.py`, `finance.py`, `deps.py`.

---

## Findings summary

| ID | Severity | Unit | Title |
|----|----------|------|-------|
| RT-M-1 | **High** | M4 | Money/stock mutations with only an *optional* Idempotency-Key double-apply on keyless retry (feed stock add, feed mix, purchase batch, farm create) |
| RT-M-2 | **Medium** | M3 | Distributed unauthenticated Argon2-pool saturation 429s legitimate logins/registers (budget quantified) |
| RT-M-3 | Low | M1/M8 | Unbounded Prometheus `method` label cardinality from arbitrary HTTP method tokens |
| RT-M-4 | Low | M1 | Log forging via percent-decoded request path (newline injection into request log lines) |
| RT-M-5 | Low | M2 | Below-threshold limiter buckets are evictable at the 50k-key cap by a unique-key spray |
| RT-M-6 | Low | M2 | Multi-worker misdeployment detection is warn-only (limits silently multiply per process) |
| RT-M-7 | Info | M2 | Per-IP budgets are spendable as one concurrent burst before any accounting lands |
| RT-M-8 | Info | M1 | Middleware-order and parser notes: RBL outside TrustedHost; dup-CL last-wins vs h11 first; Starlette `www_redirect` default |
| RT-M-9 | Info | M4 | Replay fidelity stores only status/body/`Idempotency-Replayed` (no other headers; no live impact) |
| RT-M-10 | Info | M1 | Well-formed inbound `X-Request-ID` is honored (log-correlation poisoning is a accepted tradeoff) |

Counts: **1 High, 1 Medium, 4 Low, 4 Info.** No Criticals; no cross-tenant or auth-bypass path was
found in these units.

---

## Detailed findings

### RT-M-1 (High, M4) — Money/stock mutations with an optional Idempotency-Key double-apply on keyless retry

**Evidence.** Only two mutations make the key mandatory
(`backend/app/main.py:545-548` `_REQUIRED_IDEMPOTENCY_HEADER_ROUTES` = `/api/finance/new`,
`/api/feeding/dispense`; enforced via `RequiredIdempotencyKey`,
`backend/app/services/idempotency.py:67-88`). The following *money/stock-creating* mutations take
`IdempotencyKey` (optional) and have **no database natural key** guarding a replay:

- `POST /api/feeding/inventory/{item_id}/add` — `backend/app/api/feeding.py:315-356`,
  `backend/app/services/feeding.py:457+` (`add_feed_stock`): unconditionally increases
  `qty_on_hand` and books a `FEED_PURCHASE` expense transaction. A keyless retry double-books the
  expense and double-adds stock.
- `POST /api/feeding/mix` — `backend/app/api/feeding.py:266-293`,
  `backend/app/services/feeding.py:364+` (`mix_feed_batch`): unconditional per-ingredient debit and
  finished-stock increment; keyless retry double-debits ingredients.
- `POST /api/purchases/new` — `backend/app/api/purchases.py:107-170`: creates a new batch, stub
  animals in QUARANTINE, an 8-step schedule and an expense booking; every retry creates a fresh
  graph (new ids, fresh auto tags), so nothing collides — the whole purchase is duplicated.
- `POST /api/auth/farms` — `backend/app/api/auth.py:1487-1556`: a keyless retry creates a *second*
  farm (subject only to `max_farms_per_user=10`), including a full seed graph.

Lower-tier consequences of the same optionality (data duplication, not money):
`POST /api/animals` with an auto-generated tag (tag optional,
`backend/app/schemas/animals.py:40`), `POST /api/animals/{id}/weight`,
`POST /api/health/events`, `POST /api/tasks`.

**Exploit sketch.** Any contract-following client that omits the optional header and applies a
transport-level retry policy (timeout after server commit, connection drop, user double-submit in a
custom integration) re-executes the mutation. The OpenAPI contract itself tells clients the key is
optional (`main.py:540-544` documents that FastAPI publishes optional even where required; only the
two routes above were patched). The shipped frontend mitigates with its own key registry
(`frontend` Q4) and single-flight, so exposure is via API consumers, mobile/custom integrations, or
proxy-level retries of non-registry clients.

**Impact.** Double-booked feed-purchase expenses, double-debited feed stock (can spuriously trigger
"shortage 400" later), duplicated purchase batches with duplicated expense ledger rows, duplicate
farms. Financial-report integrity damage; no cross-tenant effect.

**Fix.** Promote `feeding/inventory/{id}/add`, `feeding/mix`, `purchases/new` (money-bearing) and
`auth/farms` (irreversible graph creation) to `RequiredIdempotencyKey`, add them to
`_REQUIRED_IDEMPOTENCY_HEADER_ROUTES`, and republish the contract. If some must stay optional for
compatibility, document the retry contract per route in the OpenAPI description and add
natural-key defenses where feasible (e.g., dedupe feed purchases on an idempotent client reference).

### RT-M-2 (Medium, M3) — Distributed Argon2-pool saturation degrades legitimate logins to 429

**Evidence.** Pool: `argon2_worker_threads` default **2** (`backend/app/core/config.py:217`,
`ge=1, le=4`); `ThreadPoolExecutor` + `threading.BoundedSemaphore` (`backend/app/security.py:73-78`);
fail-fast `PasswordWorkCapacityError` → 429 `Retry-After: 1` (`main.py:511-518`).
Every *rejected* login costs two off-loop submissions: Argon2 verify + fixed PBKDF2 padding
(`security.py:229-240`, budget 50k iters `config.py:223`). **Measured** on this machine
(M-series, project's own parameters): Argon2 verify ≈ 33 ms, PBKDF2-50k ≈ 10 ms → ≈ 43 ms of
worker time per rejected login → the 2-thread pool sustains ≈ **47 rejected logins/sec** here;
expect ~2x worse (~20–25/s) on a small cloud VPS.

Throttle coverage is per-identity only (`backend/app/api/auth.py:100,215-259`): `login`
(IP,email)=10/300s, `login-email`=100/300s, `login-ip`=100/300s. There is **no global
pre-auth admission ceiling**, and the nginx edge adds no `limit_req`
(`docker-compose.yml` configs.edge_proxy). Therefore:

- One IP can burst **100 concurrent** unknown-email logins (pre-check passes while the ledger is
  empty; per-email reservations are distinct, `auth.py:754-756`) = ≈ 4.3 pool-seconds of work in a
  burst, then is locked out 300 s (~1.4% duty cycle from a single IP).
- ≈ **70–140 rotating source IPs** sustain 100% pool occupancy indefinitely → every legitimate
  login/register during that window fails fast with 429 "Password service is busy"
  (`Retry-After: 1`). `POST /api/auth/register` hashes *before* the existence check
  (`auth.py:706`) on the same pool and adds to the pressure (10/300s per IP).

**Honest assessment.** This is an availability tradeoff inherent to any fixed-size password pool
with per-IP-only throttling, and the design deliberately degrades safely: bounded queue (none), no
memory blowup (no unbounded pending work — the semaphore rejects), no event-loop impact (all work
off-loop; the "50k PBKDF2 inline on the loop" hypothesis is **refuted**, see M3 notes). Single-IP
attackers cannot reach even 5% of pool capacity. The residual risk is a modest botnet or an
IP-rotating script. Rated Medium (not High) because impact is bounded, fail-fast and
self-healing, and no data/integrity is at risk.

**Fix options.** An edge `limit_req` on `/api/auth/(login|register)` (conn-level, no app work), or
a small global pre-auth token bucket for unauthenticated password work (e.g., a few hundred
attempts/s process-wide) that sheds load before Argon2 admission; optionally raise
`argon2_worker_threads` (bounded ≤4 by settings) on multicore hosts.

### RT-M-3 (Low, M1/M8) — Unbounded Prometheus `method` label cardinality

**Evidence.** `backend/app/metrics.py:33-45` — `goatfarm_http_requests{method,route,status}`;
`route` is properly bounded to templates/`unmatched` (`main.py:616-620`), but `method` is the raw
request verb (`main.py:649-655`). h11/httptools accept any RFC-7230 *token* as a method, so an
unauthenticated client can send `GET`-shaped requests with distinct verbs (`M1`, `M2`, …, or long
random tokens, header block ≤ h11's `max_incomplete_event_size`) to `/api/...`; each distinct verb
creates a new label series in both the counter and the histogram. 100k requests → ~100k series →
tens of MB of registry memory plus scrape amplification for the internal scraper. `/metrics` is
not internet-reachable in the shipped topology (`main.py:681-690`), so the impact is in-process
memory, not exposure.

**Fix.** Normalize the label: map unknown verbs to a literal (`OTHER`) or clamp to the route
table's allowed methods; assert `len(method) <= 16`.

### RT-M-4 (Low, M1) — Log forging via percent-decoded request path

**Evidence.** uvicorn percent-decodes the path into `scope["path"]` (both protocol impls:
`unquote(raw_path)`, so `%0A` becomes a literal newline). The request-access log line then embeds
it raw: `backend/app/main.py:656-662` `logger.info("request method=%s path=%s status=%d ...",
request.method, request.url.path, ...)`. `GET /api/%0A2026-09-13ERROR goatfarm [x] fake line`
produces a forged follow-up log line. The `X-Request-ID` value itself is regex-bounded
(`[A-Za-z0-9_-]{1,64}`, `main.py:69`) — this is the one remaining un-validated field in the log
line. Metrics are unaffected (route template). Impact: misleading audit/incident-response trails;
severity Low.

**Fix.** Log `urllib.parse.quote(path, safe="/%:")` (or `repr(path)`) or strip C0 controls before
formatting.

### RT-M-5 (Low, M2) — Cold (below-threshold) buckets are evictable at the 50k cap

**Evidence.** `backend/app/ratelimit.py:172-177`: on inserting a new key at the ceiling, eviction
picks the oldest **cold** entry first; protection is granted only when
`len(hits) >= threshold` (`_is_protected`, `ratelimit.py:236-238`). A bucket with, say, 5 of 10
recorded login failures for a victim email is cold and therefore evictable once an attacker fills
the map to 50k live keys with one-shot spray entries — erasing the victim's partial history.
Fully-blocked (≥ threshold) buckets survive unless *every* slot is protected.

**Honest bounds.** Reaching the cap requires ~50k distinct-key *failures* (each an Argon2-class
login rejection, so RT-M-2's per-IP ceilings make this a distributed-only exercise of ≈500+
IP·windows); after erasure the attacker gains only the victim's *partial* count back — the
per-IP 100/300s ceiling still applies to a single-source attacker, and re-erasing a re-created
bucket requires evicting ~50k newer cold entries again. Marginal throughput gain; this is a
defense-in-depth gap in the documented spray-resistance design, not a practical bypass.

**Fix (optional).** Also protect buckets whose newest hit is recent (e.g., touched within
`window/2`), or age-weight eviction so partially-filled active buckets outrank stale one-shots.

### RT-M-6 (Low, M2) — Multi-worker misdeployment detection is warn-only

**Evidence.** `backend/app/main.py:329-337`: if `UVICORN_WORKERS`/`WEB_CONCURRENCY` indicate >1
processes, the app logs a warning and continues. Every in-memory control (auth limiter,
simulation CPU budget, worker-idempotency in-process gates) then multiplies silently for any
operator not watching logs. The provided Dockerfile pins `--workers 1`, and
`GOATFARM_RATE_LIMIT_BACKEND` fails closed on unknown values (`ratelimit.py:302-317`), but a
`docker run` override or a different launcher bypasses both. Fix: refuse to boot in `production`
when either env var exceeds 1 (the guard already exists — it just doesn't refuse).

### RT-M-7 (Info, M2) — Per-IP budgets are spendable as one concurrent burst

Pre-checks (`_login_blocked`) read the ledger before any failure is recorded
(`backend/app/api/auth.py:752-756`), so N concurrent requests from one IP all pass before the
first record lands. The per-identity reservation (`login-password-work`, 1 in-flight per email)
closes this per-account, but distinct-email sprays spend the whole `login-ip` budget
(100) concurrently. The aggregate work per IP per window stays bounded (100 × ~43 ms ≈ 4.3
pool-seconds), so this only feeds RT-M-2's burst math; no separate action needed beyond RT-M-2's
fix.

### RT-M-8 (Info, M1) — Middleware-order and parser notes (all held)

- **Order** (request direction, derived from `main.py:590-622` + Starlette stack rules):
  `ServerErrorMiddleware → request_id → ProxyHeaders(if trusted) → CORS → RequestBodyLimit →
  TrustedHost → ExceptionMiddleware → router`. Consequence: 413/414 are answered *before* Host
  validation (oversized requests need no valid virtual host — no security impact) and *after* CORS
  (CORS headers still applied); TrustedHost's 400 receives baseline headers + metrics via the
  request-id middleware. Verified intended.
- **Duplicate Content-Length**: the ASGI dict-collapse takes the **last** value
  (`main.py:100-104`) while h11 framing uses the **first**; under the deployed servers this is
  moot — h11 rejects conflicting duplicates outright (`h11/_headers.py:172-180`) and collapses
  identical ones; the streaming counter (`main.py:125-137`) backstops any server that passed both.
  Held.
- **Starlette TrustedHost** (0.48.0): Host compared after `split(":")[0]` — port suffixes are
  stripped, `Host: api.example.com:1234` matches `api.example.com`; empty host → 400; case is
  *not* normalized at runtime (settings canonicalizes to lowercase, browsers lowercase —
  fail-closed for odd casings). `www_redirect` defaults to True: with a `www.*` pattern
  configured, a non-www Host gets a 307 to the allowlisted www host (target host constrained by
  the allowlist; not an open redirect). Absolute-URI request lines: uvicorn extracts only the
  path (httptools) or leaves the full URI in `raw_path` (h11 — *longer*, so the 414 check is
  stricter); Host header still governs TrustedHost. No bypass.

### RT-M-9 (Info, M4) — Replay fidelity

Replays restore `response_status`, `response_body`, and stamp `Idempotency-Replayed: true`
(`services/idempotency.py:264-266, 394-399`); no other original response headers are persisted.
No current idempotent endpoint sets meaningful custom headers (no `Location`, no cookies on these
routes), so there is no live impact; note for future routes that rely on response headers.

### RT-M-10 (Info, M1) — Inbound `X-Request-ID` honored when well-formed

Any `[A-Za-z0-9_-]{1,64}` inbound id is adopted (`main.py:628-629`), so a client can poison log
correlation with random-but-valid ids (cannot inject newlines/controls — regex-bounded, and it is
interpolated as a value, not a format string). Documented proxy-correlation tradeoff; no action
required beyond awareness.

---

## Replay-safety table — every non-idempotent mutation (audit deliverable)

Legend: **Key** = Idempotency-Key handling (REQ required / OPT optional / — none).
**Replay outcome** = what a byte-identical re-send does after the first succeeded.

### Mutations using the idempotency service

| Endpoint | Key | Replay outcome after success | Money/stock? | Verdict |
|---|---|---|---|---|
| POST /api/finance/new | **REQ** | stored response replayed (`Idempotency-Replayed`) | yes | SAFE |
| POST /api/feeding/dispense | **REQ** | replayed | yes (debit) | SAFE |
| POST /api/feeding/inventory/{id}/add | OPT | keyless: **double qty + double FEED_PURCHASE expense** (`services/feeding.py:457+`, no natural key) | **yes** | **RT-M-1** |
| POST /api/feeding/mix | OPT | keyless: **double ingredient debit + double finished stock** (`services/feeding.py:364+`) | **yes** | **RT-M-1** |
| POST /api/purchases/new | OPT | keyless: **whole batch+animals+schedule+expense duplicated** (`api/purchases.py:107-170`) | **yes** | **RT-M-1** |
| POST /api/auth/farms | OPT | keyless: **second farm + seed graph** up to quota (`api/auth.py:1487-1556`) | no (quota-bound) | **RT-M-1** |
| POST /api/finance/transactions/{id}/correct | OPT | keyless: 409 "already been corrected" (`api/finance.py:657-658`, voided guard under FOR UPDATE) | yes | SAFE (precondition) |
| POST /api/breeding | OPT | keyless: partial-unique on open breeding → 409/400 (`services/breeding.py` D3) | no | SAFE (precondition) |
| POST /api/kidding | OPT | keyless: 422/409 "pregnancy already has a kidding record" (`services/kidding.py:81-84`) | no | SAFE (precondition) |
| POST /api/team/workers | OPT | keyless: worker identity unique → anti-takeover 400 (B7) | no | SAFE (precondition) |
| POST /api/simulation/scenarios | OPT | keyless: name-unique → 400/409 (`api/simulation.py:164+`) | no | SAFE (precondition) |
| POST /api/planner/plans | OPT | keyless: name-unique → 400 (`api/planner.py:310-315`) | no | SAFE (precondition) |
| POST /api/animals | OPT | keyed: replayed. keyless: explicit tag → 400 (farm-unique); **auto-tag → duplicate animal** (`schemas/animals.py:40`, tag optional) | no | Minor (data dup) |
| POST /api/animals/{id}/weight | OPT | keyless: duplicate weight row (no natural key) | no | Minor (analytics dup) |
| POST /api/health/events | OPT | keyless: duplicate health event (withdrawal duplication) | no | Minor (data dup) |
| POST /api/tasks | OPT | keyless: duplicate manual task (bounded by pending-task capacity) | no | Minor (data dup) |

### Mutations guarded only by state preconditions (no idempotency service)

| Endpoint | Precondition + lock | Replay outcome | Verdict |
|---|---|---|---|
| POST /api/animals/{id}/status | `status == ACTIVE` under `FOR UPDATE` (`api/animals.py:961-968`, comment: "replaying a sale … must not book a second income transaction") | 400 "already sold/dead/culled"; ANIMAL_SALE booked once | SAFE |
| POST /api/animals/{id}/move | `LEGAL_BUCKET_TRANSITIONS` has no (X,X) pair (`models/lifecycle.py:34-62`) | 409 transition error | SAFE |
| POST /api/breeding/{id}/ultrasound | `outcome == PENDING` (`services/breeding.py:594` "double submission … must not spawn a second set of follow-up tasks") | no-op returning current record (silent idempotency) | SAFE |
| POST /api/breeding/{id}/abort | `CONFIRMED_PREGNANT` + no kidding record, re-checked under lock (`api/breeding.py:424-429`) | 409 | SAFE |
| POST /api/health/restrictions/{id}/clear | `restriction_version` optimistic check + "no active restriction" (`api/health.py:333-339`) | 409 | SAFE |
| POST /api/tasks/{id}/complete | `PENDING`-only under `FOR UPDATE` (`api/tasks.py:572-573`) | 400 "not pending" | SAFE |
| POST /api/tasks/{id}/skip | `PENDING`-only (`api/tasks.py:634-635`) | 400 | SAFE |
| POST /api/tasks/{id}/verify | `DONE` + `needs_verification` (`api/tasks.py:734-735`) | 400; successor spawned once | SAFE |
| POST /api/tasks/{id}/reject | `DONE` + `needs_verification` (`api/tasks.py:792-793`) | 400 | SAFE |
| POST /api/feeding/settings | upsert (`api/feeding.py:124-131`) | idempotent assignment | SAFE |
| POST /api/team/workers/{id}/role | assignment (`api/team.py:1010`) | idempotent (same role re-assigned) | SAFE |
| PUT /api/team/workers/{id}/status | assignment, comment: "retries a no-op instead of a second inversion" (`api/team.py:1060-1063`) | idempotent | SAFE |
| POST /api/team/workers/{id}/reset-password | policy-guarded; no idempotency record (no fingerprint persisted → no HMAC exposure) | re-hash + re-set same password; `token_version` bumped again (sessions revoked again) | SAFE-ish (benign) |
| POST /api/team/roles | name-unique | 400/409 | SAFE |
| PUT /api/team/roles/{id} | revision optimistic lock | 409 | SAFE |
| POST /api/auth/register | email-unique, hashed pre-check | 400 already registered | SAFE |
| POST /api/auth/login/refresh/logout/change-password | session-family semantics (A-units) | n/a (not state-creating money ops) | n/a |
| POST /api/simulation/run, /scenarios/{id}/run; PATCH scenarios | admission control + revision lock | no money effect | SAFE |
| POST /api/planner/plan; PATCH plans | revision/quota | no money effect | SAFE |
| POST /api/ops-simulation/run | compute-only | no state | SAFE |

---

## Per-unit attacked-&-held notes

### M1 — Middleware stack (`main.py`, `core/config.py`)

**Body/target limits — held.**
- 414 on `len(raw_path) + ('?' if query) + len(query) > 8192` measured on the **raw
  (percent-encoded)** target (`main.py:90-98`) — encoded ≥ decoded, so `%2F`-style encoding cannot
  shrink the measured size below the routed size; no bypass. `?`-separator byte accounted (tested:
  `test_ops.py` target tests; `test_input_bounds.py:126-160`).
- Declared `Content-Length`: parsed once, non-int or negative → 400, `> max` (1 MiB default,
  env-capped 20 MiB, `config.py:169`) → 413 **before the app runs** (verified by
  `test_ops.py:459-530`: `calls == 0`). Chunked/no-CL bodies counted across **all** `http.request`
  messages by `limited_receive` (`main.py:125-137`) with cumulative accounting; the raised
  `HTTPException(413)` is converted by the inner `ExceptionMiddleware` into the normal JSON 413
  that then flows out through CORS/request-id (client sees it; baseline headers + metrics apply).
  A lying-small CL is corrected by server framing (h11 reads exactly CL bytes; surplus becomes a
  parse error on the connection).
- Duplicate CL: see RT-M-8. TE+CL conflicts: h11 rejects multiple TE and non-`chunked` TE
  (`h11/_headers.py:181-199`).
- Max body env ceiling 20 MiB (`config.py:169` `le=20_971_520`); edge mirrors 1 MiB
  (`docker-compose.yml` `client_max_body_size 1m`).

**TrustedHost — held.** Settings canonicalization (`config.py:296-365`): lowercase, single
trailing dot stripped, `*`-grammar mirrored from Starlette's assertions, ports/IPv6/partial
wildcards/URL forms rejected, duplicates rejected; production gate additionally requires
non-loopback, non-`*` entries (`config.py:587-608`). Runtime port-stripping and case semantics:
see RT-M-8. Dev defaults (`localhost, 127.0.0.1, test, testserver`) plus compose-added `backend`
are dev-only; production gate refuses loopbacks.

**CORS — held.** Exact origins only (`allow_origins` list, no regex, no `*`; production requires
non-loopback HTTPS origins, `config.py:554-586`). `Origin: null` not in allowlist → no ACAO on
simple responses; disallowed preflights get 400 "Disallowed CORS origin" **without** any
allow-origin header (starlette 0.48 `preflight_response`); methods/headers are enumerated exact
lists (`main.py:56-64`) — a TRACE or disallowed-header preflight fails. The 500 path's manual CORS
reproduction (`main.py:498-508`) echoes the origin **only when it is in the allowlist**
(`"*" in allowed_origins` is exact list membership, and production forbids `*`). Exposed headers
are minimal (`Idempotency-Replayed, X-Request-ID, Retry-After`).

**ProxyHeaders — held.** Default trusts nothing → `request.client.host` (socket peer) keys all
limiter scopes (`auth.py:156-159`, `deps.py:116`). When configured, settings reject hostnames,
`*`, `/0` wildcards and malformed CIDRs (`config.py:442-477`). uvicorn 0.37 uses
**rightmost-untrusted** XFF semantics (`proxy_headers.py:180-187`): with the compose edge
appending `$proxy_add_x_forwarded_for`, a client-prepended fake XFF chain cannot rotate limiter
identity — the real peer address appended by nginx is the rightmost untrusted entry. Compose pins
trust to exactly the edge container IP, not the bridge range (`docker-compose.yml:118-124`).
`X-Forwarded-Proto` only affects `scope["scheme"]`, which no security decision reads.

**Request-id — held with notes.** Regex `[A-Za-z0-9_-]{1,64}` + `fullmatch` blocks newline/control
injection through the id itself (RT-M-4 covers the *path* field). Metrics recorded for every
request in `finally` — including auth failures (401/403/429) and middleware short-circuits; route
label uses the template or `unmatched` (never raw path/query). See RT-M-3/RT-M-10 for the two
residual gaps.

### M2 — Rate limiter core (`ratelimit.py`, call sites)

**Math — held.** Exactly `max_attempts` admissions per window per scope (the Nth attempt passes
the pre-check at N−1 hits and is recorded; N+1th sees N ≥ N and is blocked); per-hit sliding
expiry (`cutoff = now − window`, `hits[0] <= cutoff` pruned) with no fixed-window reset burst.
Blocked buckets do not grow: `record()` short-circuits to `mark_blocked()` (window/threshold
refresh, no hit appended — `ratelimit.py:375-380`), preventing a hot IP from growing an unbounded
deque.
**Keys/scopes.** Composite `("login", ip|email)`, `("login-email", email)`, `("login-ip", ip)`
plus separate register/refresh/team/token scopes; no cross-scope collision (tuples include scope).
**Reservations.** `try_reserve/release` are paired on every path: login
(`auth.py:866-871` finally + done-callback surviving cancellation), change-password/account-delete
(`auth.py:1266-1267`), team worker create/reset (`team.py:188-230`: synchronous release on
admission failure, callback-owned release once native work finishes, matching
`security._run_password_work`'s shield semantics). No leaked-reservation path found. Reservation
map is ceiling-bound (`ratelimit.py:205-206`).
**Eviction.** Protected-vs-cold tiers work as documented for *blocked* buckets; partial-history
erasure at cap is RT-M-5. Amortized sweep every 30 s keeps the request path O(1); the saturated-
insertion path deliberately avoids scanning all 50k buckets.
**429 shape.** `Retry-After: <window>` (300 s) on throttle responses (`auth.py:333-339`);
`Retry-After: 1` on pool-capacity 429 (`main.py:511-518`); details are generic constants — no
scope names or keys in client bodies (scope only in logs/metrics).
**Backend honesty.** Unknown backend name fails startup (`ratelimit.py:302-317`, mirrored in
settings `config.py:367-380`); `auth_rate_limit_enabled=false` is env-only, default true, used by
the test suite — an operator flipping it in production removes brute-force throttling entirely
(worth a runtime config audit note, not a code fix).
**Clock.** `time.monotonic`; process restart wipes all history — documented single-process design.

### M3 — Argon2 worker pool (`security.py`)

**Cancellation safety — held.** Semaphore released in the executor thread itself
(`security.py:102-111`), idempotent via `release_slot_once`; `asyncio.shield` keeps the future
alive across HTTP-request cancellation so the slot is never released early; submission failure
after admission releases synchronously (`security.py:113-121`). Rejected logins always make
exactly two bounded submissions (verify + timing completion) so executor contention cannot
distinguish hash generations (`security.py:229-240`); a saturated pool on the *padding* submission
downgrades to fidelity loss, not a wrong response (`auth.py:798-803`).
**Event-loop starvation — refuted.** PBKDF2 padding runs inside `_run_password_work`
(`security.py:238-240` → executor), never inline on the loop. Legacy PBKDF2 ceiling 1M iterations
bounds corrupt-hash CPU (`security.py:63`).
**Saturation economics — RT-M-2** (measured: 33 ms Argon2 + 10 ms PBKDF2; 2 threads; per-IP
100/300 s; no global ceiling; no edge limit_req).
Boot-time dummy-hash priming removes the cold-start timing oracle (`main.py:344`,
`security.py:153-159`).

### M4 — Idempotency service (`services/idempotency.py`, `models/idempotency.py`)

**Header contract — held.** 1–128 printable non-whitespace ASCII enforced twice (FastAPI `Header`
pattern `services/idempotency.py:34-42` + service-level re-check `:297-306`); duplicates rejected
422 via raw `getlist` count (`:45-61`) — verified against Starlette's first-wins `Headers`
behavior, so the edge and app cannot disagree on which member is the retry identity. Required-key
variant for the two natural-key-less money routes (`:67-88`).
**Claim/contender — held.** `INSERT … ON CONFLICT DO NOTHING RETURNING` arbitrates at the unique
index; a contender's INSERT blocks on the in-flight claimant (PG row/xid wait), then either
replays (commit) or takes the claim (rollback) — no polling, no infinite loop (2-attempt loop only
covers expiry-takeover, `services/idempotency.py:344-370`; `409 key changed at expiry boundary`
fails closed). Expiry is re-evaluated *after* every awaited query so a record expiring while a
request was blocked is not replayed past retention (`:248`, `:365-369`). Incomplete-record rows
(visible only via manual DB damage) fail closed 409 (`:383-393`). Tests:
`test_idempotency.py:1204, 1450` (contender re-check / rollback takeover).
**Fingerprint — held.** Canonical JSON with recursive `sort_keys`, `exclude_none=False`,
`mode="json"`, fixed `version`+`operation`+`path` envelope (`:125-142`); no idempotent payload
model contains set-typed fields (order instability risk audited away); inputs are strict-mode
models so equivalent logical requests canonicalize identically. SHA-256 collision or
distinct-requests-same-fingerprint: not constructible. Same-request-different-fingerprint
(spurious 409): only via non-deterministic serialization, none found. Mismatch → 409 with a
generic detail (no stored hash/payload echo — no info leak).
**Scoping — held.** Unique (farm, actor, operation, key_digest) + partial unique for the one
actor-scoped op; `farm_id IS NULL` allowed only for `auth.farms.create` — enforced at app level
(`:215-220, 291-296`) and by DB CHECK `ck_idempotency_scope_kind` (`models/idempotency.py:48-52`).
Keys are scoped per actor — no cross-tenant replay oracle (tested
`test_idempotency.py:1780, 1868, 1901`).
**Capacity — held.** Per-actor open-record cap checked only on the fresh-claim path; replays stay
answerable at cap (`:401-429`, tested `test_idempotency.py:408`).
**HMAC coverage — held.** Only `team.workers.create` persists password-bearing fingerprints
(`SENSITIVE_IDEMPOTENCY_OPERATIONS`, `:30`); verified by enumerating all 16 `execute_idempotent`
call sites — no other operation's payload carries credential material (login/register/change-
password/reset-password never enter the service). Rotation via previous-secrets with
constant-time multi-candidate compare (`:178-191`); production boot rejects the dev secret and
short/duplicated secrets (`config.py:509-541`).
**Cleanup — held.** Request transactions never scan/delete retention cohorts; purge is a
background, `SKIP LOCKED`, materialized-ID batch with explicit LIMIT (`:91-118`, tests
`test_idempotency.py:1255-1450`).

### M9-adjacent handlers verified along the way (context for M1)

`RequestValidationError` → 422 with only `type/loc/msg` — **no input echo** for body, query or
path errors, nested included (`main.py:440-456`); `_json_safe` neutralizes non-finite floats
before `JSONResponse(allow_nan=False)` (`main.py:422-437`). Unhandled → opaque 500, traceback
only in logs with the recovered request id, baseline headers (incl. `no-store` under `/api/`,
nosniff, XFO DENY, Referrer-Policy, Permissions-Policy) re-stamped above `ServerErrorMiddleware`
(`main.py:477-508`). Non-API responses (docs/OpenAPI in dev) carry baseline headers but are
cacheable — public data, acceptable.

---

## Test-coverage assessment

Strong prior coverage held under attack: `test_ops.py:440-540` (CL pre-check, case folding,
lying CL, malformed/negative CL, at-limit acceptance, chunked streaming, 414 raw-target,
query-separator accounting), `test_input_bounds.py:126-160`, `test_idempotency.py` (44 tests:
contender/expiry/capacity/HMAC-rotation/actor-farm isolation/OpenAPI header declaration),
`test_concurrency.py` (25: double-sale/complete/abort/ultrasound/mix dedup, deadlock orders),
`test_adversarial.py` (resell-sold, ultrasound resubmit, kidding duplication guards),
`test_metrics.py` (unmatched template, 429 scope counter, replay counter, backend gate).
Gaps matching findings: no test for custom-method metric cardinality (RT-M-3), none for decoded-
path log content (RT-M-4), none asserting keyless-retry behavior of mix/stock-add/purchase
replays (RT-M-1's regression tests should fix that).

## Regression tests to add

1. RT-M-1: keyless double-POST to `/api/feeding/inventory/{id}/add`, `/api/feeding/mix`,
   `/api/purchases/new` (once required: keyless → 422), `/api/auth/farms` (second farm refused
   or keyed).
2. RT-M-3: `X`-method request → metrics gains no new series beyond a fixed set.
3. RT-M-4: request to `/api/a%0Ab` → log line contains no raw newline (assert on captured log).
4. RT-M-6: production Settings + `WEB_CONCURRENCY=4` → boot refusal (after fix).
