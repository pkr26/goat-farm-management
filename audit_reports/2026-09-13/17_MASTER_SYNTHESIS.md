# Red Team Audit — Master Synthesis (2026-09-13)

Campaign: exhaustive adversarial audit of all 135 functional units defined in
`00_RED_TEAM_AUDIT_SCOPE.md`, executed as 16 deep-dive audits (reports
`01`–`16` in this directory). Every finding is code-evidenced with file:line
references in its per-domain report; the four High findings were independently
re-verified in code during synthesis. Prior audit campaigns
(2026-09-01 … 2026-09-08) were re-attacked, not trusted.

## Severity rollup — 135 findings: 0 Critical · 4 High · 19 Medium · 45 Low · 67 Info

| # | Report | Scope | C | H | M | L | I |
|---|---|---|---|---|---|---|---|
| 01 | AUTH_TOKENS_ACCOUNTS | A1–A13 | 0 | 0 | 1 | 2 | 6 |
| 02 | TENANCY_RBAC_TEAM | B1–B12 | 0 | 0 | 1 | 3 | 4 |
| 03 | MIDDLEWARE_RATELIMIT_IDEMPOTENCY | M1–M4 | 0 | 1 | 1 | 4 | 4 |
| 04 | ANIMALS_LIFECYCLE | C1–C7 | 0 | 1 | 1 | 4 | 4 |
| 05 | BREEDING_KIDDING | D1–E3 | 0 | 0 | 1 | 1 | 6 |
| 06 | HEALTH_TASKS | F1–G7 | 0 | 0 | 1 | 4 | 5 |
| 07 | FEEDING_FINANCE_PURCHASES | H1–J3 | 0 | 1 | 1 | 4 | 7 |
| 08 | AGGREGATES_SIMULATION_API | K1–L7 | 0 | 0 | 2 | 3 | 4 |
| 09 | SIMULATION_ENGINE | L8 | 0 | 1 | 2 | 1 | 2 |
| 10 | OPS_SEEDING_LOOPS_CONFIG | M5–M11 | 0 | 0 | 1 | 2 | 3 |
| 11 | DATA_LAYER_MIGRATIONS_SCRIPTS | N1–N7 | 0 | 0 | 1 | 4 | 5 |
| 12 | FRONTEND_SESSION_AUTH | O1–O8 | 0 | 0 | 0 | 3 | 5 |
| 13 | FRONTEND_PAGES_A | P1–P11 | 0 | 0 | 1 | 2 | 1 |
| 14 | FRONTEND_PAGES_B | P12–P22 | 0 | 0 | 0 | 2 | 4 |
| 15 | FRONTEND_LIBS | Q1–Q11 | 0 | 0 | 1 | 2 | 4 |
| 16 | DEPLOYMENT_CI | R1–R9 | 0 | 0 | 4 | 4 | 3 |

No Critical findings: no cross-tenant data access, no authentication bypass,
no XSS/open-redirect/token exfiltration anywhere. `safeAppPath` survived a
66-case crafted matrix plus 200k fuzz iterations; every expired-token path is
provably dead; farm scoping is uniform (byte-identical 404s) across all 15
routers.

## The four High findings (all verified in code during synthesis)

### RT-M-1 — Money/stock mutations lack the *required* Idempotency-Key (03, confirmed against OpenAPI contract by 16)
`POST /api/feeding/mix`, `POST /api/feeding/inventory/{id}/add`,
`POST /api/purchases/new` (and `POST /api/auth/farms`, `POST /api/animals`
money-booking paths) accept only an **optional** key. A keyless network retry
double-books FEED_PURCHASE expenses, double-debits stock, duplicates an entire
purchase graph (animals + tasks + ledger), or creates a second farm — violating
the project's own rule that no-natural-key ⇒ required key (only
`finance/new` + `feeding/dispense` enforce it). Fix: extend
`_REQUIRED_IDEMPOTENCY_HEADER_ROUTES` + `RequiredIdempotencyKey` to the five
routes; frontend `isIdempotencyProtectedMutation` already treats them as
protected.

### RT-C-1 / RT-HIJ-1 — Bidirectional RBAC asymmetry around the purchase cascade (04, 07)
- RT-C-1: a worker holding **only `animals.create`** triggers the full managed
  purchase cascade via `POST /api/animals` (PurchaseBatch + ₹1e9
  ANIMAL_PURCHASE expense + 8 quarantine tasks) — writes that otherwise need
  `purchases.manage` and `finance.manage`.
- RT-HIJ-1: a worker holding **only `purchases.manage`** creates up to 1,000
  full ACTIVE Animal rows + weight records + moves via `POST /api/purchases/new`
  — writes that otherwise need `animals.create`.
Custom roles can forge procurement records and pollute the ledger/P&L in both
directions. Fix: require `purchases.manage` on the PURCHASED source (and
`animals.create` when `create_animals=true`), or collapse both cascades behind
one coupled permission.

### RT-L8-1 — Planner `close_gaps` materializes 608M pydantic events before validation → OOM (09, amplified by 16/RT-R-3)
Schema-valid `conception_rate=1e-9` with a shortfall drives
`needed = ceil(shortfall/marginal)` to 6.08e13 head;
`_purchases_from` (`planner.py:491-508`) then chunks this into a list of ~608
Million events **before** the 500-event cap ever runs. Measured: 1M events =
1.8 s + 835 MB RSS; projected ~500 GB. Priced at only 2,640/650,000 budget
units — pure budget evasion. With compose `mem_limit: 2g` + `--workers 1` +
single replica this is an OOM-kill restart loop = whole-stack cross-tenant
outage from one authenticated request. Fix: bound `needed` against the event
cap *before* chunking (cheap arithmetic pre-check), and cap the chunk loop.

## The nineteen Medium findings

**Auth/tenancy:** RT-A-1 distributed per-email login lockout (3+ rotating IPs
keep a victim's correct-password logins 429'd indefinitely, no self-service
unlock); RT-B-1 worker-create is a platform-wide account-existence oracle
(201 vs CANT_ADD_TO_TEAM).

**Money integrity:** RT-HIJ-2 finance correction can recategorize a manual row
into system categories (ANIMAL_SALE/PURCHASE), defeating the
"indistinguishable from system revenue" invariant; RT-C-2 meat-sale minimum-age
gate bypassed by selling an unweaned male kid from RECOVERY (income booked).

**Lifecycle/task integrity:** RT-DE-1 owner `history_override` can strand an
open pregnancy in buckets with no kidding/abort exit (permanent overdue-list
zombie); RT-FG-1 form-link completion fence derives from linkage columns, not
category — legacy/unlinked rows are bare-completable with zero data recorded.

**Compute/availability (budget-evasion family):** RT-L8-2
`ZeroDivisionError` → opaque 500 on `conception_rate=0` / `sex_ratio_female`
∈{0,1}; RT-L8-3 daily-ops births amplify compute 8.5× beyond initial-head
pricing; RT-KL-2 ops-sim JSON result uncapped (multi-MB event-loop
serialization stall for all tenants); RT-M-2 distributed Argon2 pool
saturation (~70–140 rotating IPs hold login at 429; no global pre-auth cap);
RT-M2-1 misspelled `GOATFARM_*` **environment variables** are silently ignored
(`extra=forbid` only covers `.env`) — Argon2 cost, rate limits, TTLs, trusted
proxies silently revert to defaults.

**Info disclosure:** RT-KL-1 dashboard/reports expose per-bucket pregnancy
occupancy + avg weights to roles with only `dashboard.view`/`reports.view`
(bypassing `buckets.view`/`animals.view`); RT-P7-1 dashboard renders withheld
task sections as factual "0 / Nothing due" instead of the None sentinel.

**Ops:** RT-N-1 `libpq_url.py` denylist misses libpq TLS-trust params
(`sslrootcert` etc.) — a URL query param overrides the enforced verify-full
gate; RT-Q-1 frontend `taskSkipUnavailable` mirrors only 2 of the backend's 3
skip gates (always-409 Skip button).

**Deployment:** RT-R-1 edge nginx has no `limit_req` (amplifies RT-M-2);
RT-R-2 flat compose network (frontend/edge can reach `db:5432`);
RT-R-3 single-worker 2g OOM restart loop (amplifies RT-L8-1); RT-R-4 non-
loopback bind with default `ENVIRONMENT=development` is refused nowhere — all
production validators stay off on a public listener.

## Low/Info highlights (45 + 67 — see per-domain reports)

Noteworthy Lows: unilateral statutory-hold clearance without a second person
(RT-FG-2); depth-1 inbreeding fence (RT-DE-2); supplier names leak to task-board
workers via duty titles (RT-HIJ-3); typed worker password survives dialog
dismissal (RT-P2-1); prod JWT key file permissions never validated (RT-A-3);
log forging via `%0A` in paths (RT-M-4); metrics method-label cardinality
(RT-M-3); S3 backup cleanup best-effort (RT-N-2); `BACKEND_URL` baked into the
build manifest (RT-R-7); no cross-tenant negative e2e tests anywhere (RT-R-5).

## Cross-cutting themes

1. **Idempotency doctrine is enforced in only two places** — the money paths
   the rule was written for (RT-M-1, RT-C-4, RT-Q-2) all sit outside it.
2. **The purchase cascade is a permission seam** — two endpoints, two
   permission sets, same writes (RT-C-1 + RT-HIJ-1, plus RT-HIJ-3 leakage).
3. **Compute budget priced on inputs, not on amplification** — the engine is
   honest about what it charges but the amplifiers (event materialization,
   births, JSON serialization) grow past the price (RT-L8-1/2/3, RT-KL-2;
   deployed atop RT-R-3's single 2g worker).
4. **Mirror drift** — client rules that re-implement server gates drift one
   gate behind (RT-Q-1, RT-FG-1's UI assumptions, RT-P2-2, RT-P7-1).
5. **"Development defaults on a public port"** — nothing refuses a
   publicly-bound deployment running with development posture (RT-R-4,
   RT-M2-1, RT-M-6, RT-A-3, RT-M2-2).

## Verified strong (attacked and held — highlights)

JWT/refresh family (alg pinning, kid keyring, expiry ordering, reuse detection
with grace, lock orders User→RefreshSession at all six mutation sites);
tombstones; must-change fence; uniform 404 farm conflation; fail-closed
corrupt-JSONB permissions; Membership→User→Role lock graph with
`populate_existing` everywhere; peer-manager/ceiling/preset guards; body/target
limits incl. chunked streaming; exact-origin CORS on every path incl. 500s;
rightmost-untrusted XFF; sliding-window limiter reservation pairing;
idempotency claim/contender arbitration and HMAC coverage; terminal-status
replay cannot double-book ANIMAL_SALE; like-escape + CHECK parity (no
input-driven 500s in the domain APIs); mix/underflow impossible (FOR UPDATE +
non-negative CHECKs, exact gram conservation); void-and-replace atomicity and
anchor checks; admission-control lease/budget mechanics; calibration six-permission
gate; engine seeding/determinism; 54-migration linear chain + tenant-FK
completeness; restore/backup GPG + advisory-lock coordination; no
dangerouslySetInnerHTML / window.confirm / target=_blank / token-in-storage
anywhere in the frontend; all navigation validated; all 42 farm-scope captures
synchronous; safeAppPath fuzz-clean.

## Recommended remediation order

1. **RT-L8-1** (pre-check before chunking) + **RT-R-3** context — cheapest fix,
   worst outage.
2. **RT-M-1** (required keys on 5 routes) — mechanical, closes double-booking.
3. **RT-C-1 + RT-HIJ-1** (cascade permission coupling) — one design decision,
   two endpoints.
4. **RT-R-4 + RT-M2-1** (refuse public bind / unknown-env in dev posture;
   reject unknown `GOATFARM_*` env vars at boot).
5. **RT-M-2 + RT-R-1** (edge `limit_req` on /api/auth/* + global pre-auth
   Argon2 cap).
6. **RT-HIJ-2, RT-C-2, RT-DE-1, RT-FG-1** (integrity fences).
7. Remaining Mediums, then Lows by report order.
